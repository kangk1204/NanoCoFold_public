"""Minimal, opt-in MMseqs2 acquisition with explicit sequence-upload consent.

The public-preset entry point still rejects generated/private sequences. The
separate authorized-upload entry point requires explicit consent and records
that its sequences are not declared public. The default is offline cache reuse.
A persisted ticket is resumed; ambiguous POST outcomes are never reposted.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import http.client
import io
import json
import os
import re
import tarfile
import tempfile
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Mapping
from urllib.parse import urlencode

from .colabfold_msa import cached_monomer, promote_monomer, read_monomer, sequence_hash

PROVIDER = "colabfold_mmseqs2_public"
MODE = "mmseqs2_uniref_env"
HOST = "api.colabfold.com"
MAX_BYTES = 64 * 1024 * 1024
ARCHIVE_NAMES = ("uniref.a3m", "bfd.mgnify30.metaeuk30.smag30.a3m")
PROVIDER_LOCK_PATH = Path.home() / ".cache/nanocofold/public_msa_api.lock"


class MSAAcquisitionError(ValueError):
    pass


class UncertainSubmission(MSAAcquisitionError):
    """POST may have reached the provider; automatic resubmission is forbidden."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


class OfficialTransport:
    """Pinned HTTPS host, bounded body, separate connection/read timeouts."""
    def request(self, method, path, *, data=None, timeout=(6.02, 60.0)):
        if method not in ("GET", "POST") or not path.startswith("/"):
            raise MSAAcquisitionError("Unsupported MSA transport request")
        connection = http.client.HTTPSConnection(HOST, timeout=timeout[0])
        try:
            connection.connect()
            connection.sock.settimeout(timeout[1])
            body = urlencode(data).encode() if data is not None else None
            headers = {"User-Agent": "NanoCoFold-public-MSA/1", "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            chunks, size = [], 0
            while chunk := response.read(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise MSAAcquisitionError("MSA response exceeds bounded download size")
                chunks.append(chunk)
            return Response(response.status, {key.lower(): value for key, value in response.getheaders()}, b"".join(chunks))
        finally:
            connection.close()


def _atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            try:
                os.fsync(descriptor)
            except OSError as exc:
                import warnings
                warnings.warn(f"MSA 저장 후 디렉터리 동기화 미지원: {exc}", RuntimeWarning)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save(path, value):
    _atomic(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


@contextmanager
def _lock(path, *, sleep, timeout=120, check_budget=lambda: None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise MSAAcquisitionError("MSA lock must not be a symbolic link")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    start = time.monotonic()
    try:
        while True:
            check_budget()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - start >= timeout:
                    raise MSAAcquisitionError("MSA cache is being acquired by another process")
                sleep(.05)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _delay(response, attempt, sleep):
    raw = next((value for key, value in response.headers.items() if key.lower() == "retry-after"), None)
    delay = min(60.0, 2.0 * 2 ** min(attempt, 5))
    if raw is not None:
        try:
            delay = float(raw)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    sleep(max(.1, min(60.0, delay)))


def _document(response):
    if len(response.body) > 65536:
        raise MSAAcquisitionError("MSA ticket response exceeds bounded size")
    try:
        value = json.loads(response.body)
    except (ValueError, UnicodeError) as exc:
        raise MSAAcquisitionError("Malformed MMseqs2 ticket response") from exc
    if not isinstance(value, dict) or value.get("status") not in {"UNKNOWN", "RATELIMIT", "PENDING", "RUNNING", "COMPLETE", "ERROR", "MAINTENANCE"}:
        raise MSAAcquisitionError("Unsupported MMseqs2 ticket state")
    return value


def _safe_alignments(archive, queries):
    """Read only regular bounded A3M members; never extract a tar filesystem."""
    if not archive or len(archive) > MAX_BYTES:
        raise MSAAcquisitionError("Empty or oversized MMseqs2 archive")
    files, expanded = {}, 0
    try:
        # Bound the complete gzip expansion before tar parses PAX/long-name
        # headers: those metadata payloads are read before yielding a member.
        with gzip.GzipFile(fileobj=io.BytesIO(archive), mode="rb") as compressed:
            unpacked = compressed.read(MAX_BYTES + 1)
        if len(unpacked) > MAX_BYTES:
            raise MSAAcquisitionError("MMseqs2 archive exceeds expanded size limit")
        with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as package:
            for count, member in enumerate(package, 1):
                if count > 100:
                    raise MSAAcquisitionError("Too many MMseqs2 archive members")
                if (member.name.startswith("/") or ".." in Path(member.name).parts or "\\" in member.name
                        or not member.isfile() or member.size < 0):
                    raise MSAAcquisitionError("Unsafe MMseqs2 archive member")
                expanded += member.size
                if expanded > MAX_BYTES:
                    raise MSAAcquisitionError("MMseqs2 archive exceeds expanded size limit")
                if member.name in ARCHIVE_NAMES:
                    if member.name in files:
                        raise MSAAcquisitionError("Duplicate MMseqs2 archive member")
                    with package.extractfile(member) as stream:
                        content = stream.read(MAX_BYTES + 1)
                    if len(content) != member.size:
                        raise MSAAcquisitionError("Truncated MMseqs2 archive member")
                    files[member.name] = content.decode("utf-8")
    except (tarfile.TarError, UnicodeError, OSError, EOFError) as exc:
        raise MSAAcquisitionError("Damaged MMseqs2 archive") from exc
    if set(files) != set(ARCHIVE_NAMES):
        raise MSAAcquisitionError("MMseqs2 archive lacks required alignment databases")
    rows = {101 + i: [] for i in range(len(queries))}
    for text in files.values():
        seen = set()
        for block in text.split("\x00"):
            lines = [line for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            if not re.fullmatch(r">\d+", lines[0]):
                raise MSAAcquisitionError("MMseqs2 A3M query ID missing")
            identifier = int(lines[0][1:])
            if identifier not in rows or identifier in seen:
                raise MSAAcquisitionError("Unknown or duplicate MMseqs2 query ID")
            seen.add(identifier)
            # Each DB block contains the exact query; merge its homologs only.
            split = next((i for i in range(1, len(lines)) if lines[i].startswith(">")), len(lines))
            sequence = "".join(lines[1:split])
            if sequence != queries[identifier - 101]:
                raise MSAAcquisitionError("MMseqs2 returned a different query sequence")
            rows[identifier].extend(lines[split:])
        if seen != set(rows):
            raise MSAAcquisitionError("MMseqs2 alignment lacks a requested query")
    return {sequence: ">query\n" + sequence + "\n" + "\n".join(rows[101 + i]) + "\n" for i, sequence in enumerate(queries)}


def _cached(cache_dir, sequence, provider, mode, authorization_kind="public_source", source_id=None):
    directory = cache_dir / sequence_hash(sequence)
    if not directory.exists():
        return None
    if directory.is_symlink() or (directory / "query.a3m").is_symlink() or (directory / "receipt.json").is_symlink():
        raise MSAAcquisitionError("MSA cache must not contain symbolic links")
    value = cached_monomer(cache_dir, sequence)  # Existing corruption is never a miss.
    provenance = value["receipt"].get("source_provenance", {})
    expected_public = authorization_kind == "public_source"
    if (not isinstance(provenance, dict) or provenance.get("provider") != provider or provenance.get("mode") != mode
            or provenance.get("public_sequence") is not expected_public
            or (not expected_public and (provenance.get("authorization_kind") != authorization_kind
                                        or provenance.get("explicit_sequence_upload_authorized") is not True))
            or (source_id is not None and provenance.get("public_source_id" if expected_public else "source_id") != source_id)):
        raise MSAAcquisitionError("Cached MSA provider/mode/public-source identity differs; use a separate cache")
    return {**value, "receipt_path": str(directory / "receipt.json")}


def acquire_public_monomers(cache_dir, queries, *, approved_public_sequences, allow_network=False,
                            provider=PROVIDER, mode=MODE, transport=None, sleep=time.sleep,
                            max_polls=120, connect_timeout=6.02, read_timeout=60.0, check_budget=None):
    """Batch only missing unique approved public sequences, returning verified cache.

    ``approved_public_sequences`` is a trusted server-side exact sequence ->
    public source ID mapping, never accepted from browser input. Provider and
    mode are pinned. GET retries reuse a durable ticket. HTTP 429 without a
    ticket is the only bounded retryable POST rejection; ambiguous outcomes
    remain recorded as uncertain and require human/provider reconciliation.
    """
    return _acquire_monomers(cache_dir, queries, approved_sequences=approved_public_sequences,
        allow_network=allow_network, provider=provider, mode=mode, transport=transport, sleep=sleep,
        max_polls=max_polls, connect_timeout=connect_timeout, read_timeout=read_timeout,
        check_budget=check_budget, authorization_kind="public_source")


def acquire_authorized_monomers(cache_dir, queries, *, approved_sequences, allow_sequence_upload=False,
                               allow_network=False, **kwargs):
    """Acquire exact private/generated queries only with explicit upload consent.

    The caller constructs an exact sequence -> reviewed source ID mapping.
    Entries are never labeled public and cannot share public-preset cache
    entries. Consent is required even for a cache hit, so a configuration change
    cannot silently retain the public-upload execution mode.
    """
    if allow_sequence_upload is not True:
        raise MSAAcquisitionError("Explicit consent to upload target/generated sequences is required")
    return _acquire_monomers(cache_dir, queries, approved_sequences=approved_sequences,
        allow_network=allow_network, authorization_kind="explicit_sequence_upload", **kwargs)


def _acquire_monomers(cache_dir, queries, *, approved_sequences, authorization_kind,
                     allow_network=False, provider=PROVIDER, mode=MODE, transport=None, sleep=time.sleep,
                     max_polls=120, connect_timeout=6.02, read_timeout=60.0, check_budget=None):
    if provider != PROVIDER or mode != MODE:
        raise MSAAcquisitionError("Only the approved public MMseqs2 provider/mode is supported")
    if type(allow_network) is not bool or type(max_polls) is not int or not 1 <= max_polls <= 1000:
        raise MSAAcquisitionError("Invalid MSA acquisition policy")
    if (type(connect_timeout) not in (int, float) or type(read_timeout) not in (int, float)
            or not 1 <= connect_timeout <= 30 or not 1 <= read_timeout <= 120):
        raise MSAAcquisitionError("Invalid MSA connection/read timeout")
    if not isinstance(approved_sequences, Mapping):
        raise MSAAcquisitionError("Explicit public-sequence authorization mapping required")
    queries = list(queries)
    if not 1 <= len(queries) <= 25:
        raise MSAAcquisitionError("Public MSA acquisition accepts 1..25 base queries, never mutant batches")
    if any(not isinstance(seq, str) or not 1 <= len(seq) <= 2000 or not re.fullmatch("[ACDEFGHIKLMNPQRSTVWY]+", seq) for seq in queries):
        raise MSAAcquisitionError("Public MSA query must be an exact canonical sequence")
    unique = sorted(set(queries))
    if any(seq not in approved_sequences or not isinstance(approved_sequences[seq], str)
           or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", approved_sequences[seq]) for seq in unique):
        raise MSAAcquisitionError("A query is not an expressly approved public source sequence")
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    stats = {"cache_hits": 0, "unique_missing": 0, "post_requests": 0, "poll_requests": 0,
             "download_requests": 0, "source": provider, "mode": mode}
    entries = {}
    check_budget = check_budget or (lambda: None)
    public = authorization_kind == "public_source"
    sources_key = "public_sources" if public else "reviewed_sources"
    # Sorted per-sequence locks make overlapping batches single-flight too.
    with ExitStack() as locks:
        for seq in sorted(unique, key=sequence_hash):
            locks.enter_context(_lock(cache_dir / ".acquisition" / "locks" / (sequence_hash(seq) + ".lock"),
                                      sleep=sleep, check_budget=check_budget))
        missing = []
        for seq in unique:
            entry = _cached(cache_dir, seq, provider, mode, authorization_kind, approved_sequences[seq])
            if entry is None:
                missing.append(seq)
            else:
                entries[seq] = entry
                stats["cache_hits"] += 1
        stats["unique_missing"] = len(missing)
        if not missing:
            return {"cache_dir": str(cache_dir), "entries": entries, "stats": stats}
        if allow_network:
            # Disjoint runs/caches for this local account submit serially. This
            # is a provider lock, separate from per-query cache single-flight.
            locks.enter_context(_lock(PROVIDER_LOCK_PATH, sleep=sleep, check_budget=check_budget))
        identity = {"provider": provider, "mode": mode, "queries": missing,
                    sources_key: [approved_sequences[seq] for seq in missing]}
        if not public:
            identity["authorization_kind"] = authorization_kind
        batch_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        # A crash can publish one member of a batch before the next. Reuse the
        # original downloaded batch, rather than submit its remaining members.
        # Any overlapping uncertain/unfinished submission blocks a changed
        # batch too: changing its input list must not bypass the POST safeguard.
        previous = sorted((cache_dir / ".acquisition" / "tickets").glob("*/ticket.json"))
        if len(previous) > 10000:
            raise MSAAcquisitionError("Too many MSA ticket receipts; inspect the cache")
        for existing_path in previous:
            try:
                candidate = json.loads(existing_path.read_text())
                old = candidate["identity"]
                old_queries = old["queries"]
                if not isinstance(old_queries, list) or any(not isinstance(seq, str) for seq in old_queries):
                    raise ValueError("Invalid ticket query list")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise MSAAcquisitionError("Existing MSA ticket receipt damaged; preserve it") from exc
            if old.get("provider") != provider or old.get("mode") != mode or not set(missing).intersection(old_queries):
                continue
            if candidate.get("status") in ("submitting", "uncertain"):
                raise UncertainSubmission("Overlapping MSA submission outcome is uncertain; automatic POST retry is disabled")
            if set(missing).issubset(old_queries) and set(old_queries).issubset(unique):
                if (old.get(sources_key) != [approved_sequences[seq] for seq in old_queries]
                        or (not public and old.get("authorization_kind") != authorization_kind)):
                    raise MSAAcquisitionError("Persisted MSA public-source authorization differs")
                expected_key = hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()
                if existing_path.parent.name != expected_key:
                    raise MSAAcquisitionError("Persisted MSA ticket identity hash differs")
                identity, batch_key = old, expected_key
                break
            if candidate.get("status") != "published":
                raise MSAAcquisitionError("An overlapping MSA ticket is unfinished; resume its original public query set first")
        batch_queries = identity["queries"]
        directory = cache_dir / ".acquisition" / "tickets" / batch_key
        receipt_path, archive_path = directory / "ticket.json", directory / "out.tar.gz"
        if receipt_path.exists():
            try:
                state = json.loads(receipt_path.read_text())
            except (ValueError, OSError) as exc:
                raise MSAAcquisitionError("MSA ticket receipt is damaged; preserve it") from exc
            if (state.get("identity") != identity or state.get("schema_version") != 1
                    or not isinstance(state.get("requests"), dict)
                    or any(type(state["requests"].get(key)) is not int or state["requests"][key] < 0 for key in ("post", "poll", "download"))):
                raise MSAAcquisitionError("MSA ticket identity differs")
        else:
            state = {"schema_version": 1, "identity": identity, "status": "new", "requests": {"post": 0, "poll": 0, "download": 0}}
        if state["status"] in ("submitting", "uncertain"):
            raise UncertainSubmission("Previous MSA submission outcome is uncertain; automatic POST retry is disabled")
        if state["status"] in ("ERROR", "MAINTENANCE", "failed"):
            raise MSAAcquisitionError("Previous MSA ticket failed; preserve its receipt for investigation")
        if state.get("status") not in {"new", "rate_limited", "PENDING", "RUNNING", "COMPLETE", "UNKNOWN", "RATELIMIT", "published"}:
            raise MSAAcquisitionError("Persisted MSA ticket state is invalid; preserve its receipt")
        if "ticket_id" not in state and state["status"] not in {"new", "rate_limited"}:
            raise MSAAcquisitionError("Persisted MSA ticket ID missing; automatic POST retry is disabled")
        if not archive_path.exists() and not allow_network:
            raise MSAAcquisitionError("Verified public MSA cache missing; explicit public acquisition is disabled")
        transport = transport or OfficialTransport()
        if "ticket_id" not in state:
            if state["status"] == "rate_limited" and state["requests"]["post"] >= 4:
                raise MSAAcquisitionError("Public MSA service rate limited; bounded POST retries exhausted")
            # Rejections are bounded across process restarts, not per call.
            for attempt in range(state["requests"]["post"], 4):
                check_budget()
                state["status"] = "submitting"
                state["requests"]["post"] += 1
                stats["post_requests"] += 1
                _save(receipt_path, state)  # Crash after POST begins is uncertain.
                try:
                    response = transport.request("POST", "/ticket/msa", data={"q": "".join(f">{101+i}\n{seq}\n" for i, seq in enumerate(batch_queries)), "mode": "env"}, timeout=(connect_timeout, read_timeout))
                    if response.status == 429:
                        state["status"] = "rate_limited"
                        _save(receipt_path, state)
                        if state["requests"]["post"] >= 4:
                            raise MSAAcquisitionError("Public MSA service rate limited; bounded POST retries exhausted")
                        _delay(response, attempt, sleep)
                        continue
                    if response.status != 200:
                        raise UncertainSubmission("MSA POST outcome is uncertain; no automatic resubmission")
                    document = _document(response)
                    ticket = document.get("id")
                    if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", ticket):
                        raise UncertainSubmission("MSA POST returned no usable ticket; no automatic resubmission")
                    state.update(ticket_id=ticket, status=document["status"],
                                 retry_after=next((value for key, value in response.headers.items() if key.lower() == "retry-after"), None))
                    _save(receipt_path, state)
                    break
                except Exception as exc:
                    if state["status"] == "rate_limited":
                        raise
                    state["status"] = "uncertain"
                    _save(receipt_path, state)
                    raise UncertainSubmission("MSA submission may have reached the service; preserved uncertain receipt, no automatic POST retry") from exc
        ticket = state["ticket_id"]
        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", ticket):
            raise MSAAcquisitionError("Invalid persisted MSA ticket ID")

        def get(path, counter):
            for attempt in range(5):
                check_budget()
                state["requests"][counter] += 1
                stats[counter + "_requests"] += 1
                _save(receipt_path, state)
                try:
                    response = transport.request("GET", path, timeout=(connect_timeout, read_timeout))
                except (OSError, TimeoutError, http.client.HTTPException):
                    if attempt == 4:
                        raise MSAAcquisitionError("MSA GET timed out; ticket retained for resume")
                    _delay(Response(503, {}, b""), attempt, sleep)
                    continue
                if response.status in (429, 502, 503, 504):
                    if attempt == 4:
                        raise MSAAcquisitionError("MSA GET retries exhausted; ticket retained for resume")
                    _delay(response, attempt, sleep)
                    continue
                if response.status != 200:
                    raise MSAAcquisitionError(f"MSA GET failed HTTP {response.status}; ticket retained")
                return response

        if not archive_path.exists():
            for attempt in range(max_polls):
                check_budget()
                if state["status"] == "COMPLETE":
                    break
                if state["status"] in ("ERROR", "MAINTENANCE"):
                    raise MSAAcquisitionError("MMseqs2 ticket failed; no automatic resubmission")
                _delay(Response(200, {"retry-after": state["retry_after"]} if state.get("retry_after") is not None else {}, b""), attempt, sleep)
                response = get("/ticket/" + ticket, "poll")
                document = _document(response)
                if "id" in document and document["id"] != ticket:
                    raise MSAAcquisitionError("MSA poll returned a different ticket")
                state["status"] = document["status"]
                state["retry_after"] = next((value for key, value in response.headers.items() if key.lower() == "retry-after"), None)
                _save(receipt_path, state)
            if state["status"] != "COMPLETE":
                raise MSAAcquisitionError("MSA polling budget exhausted; same ticket retained for resume")
            archive = get("/result/download/" + ticket, "download").body
            state["archive_sha256"] = hashlib.sha256(archive).hexdigest()
            # Save the expected hash before publishing the archive; a crash
            # after file replacement must remain resumable without another POST.
            _save(receipt_path, state)
            _atomic(archive_path, archive)
        archive = archive_path.read_bytes()
        if len(archive) > MAX_BYTES or state.get("archive_sha256") != hashlib.sha256(archive).hexdigest():
            raise MSAAcquisitionError("Persisted MSA download hash mismatch")
        alignments = _safe_alignments(archive, batch_queries)
        with tempfile.TemporaryDirectory(prefix="validated-", dir=directory) as temporary:
            sources = {}
            # Validate all returned queries before publishing any cache entries.
            for seq, text in alignments.items():
                source = Path(temporary) / (sequence_hash(seq) + ".a3m")
                source.write_text(text)
                read_monomer(source, seq, max_sequences=10000)
                sources[seq] = source
            for seq, source in sources.items():
                origin = {"public_sequence": True, "public_source_id": approved_sequences[seq],
                          "public_source_sha256": hashlib.sha256(approved_sequences[seq].encode()).hexdigest()} if public else {
                          "public_sequence": False, "source_id": approved_sequences[seq],
                          "authorization_kind": authorization_kind, "explicit_sequence_upload_authorized": True}
                promote_monomer(cache_dir, seq, source, provenance={"provider": provider, "mode": mode, **origin,
                    "endpoint": "https://" + HOST, "database_revision": None,
                    "ticket_id": ticket, "batch_key": batch_key, "archive_sha256": state["archive_sha256"]})
                entries[seq] = _cached(cache_dir, seq, provider, mode, authorization_kind, approved_sequences[seq])
        state["status"] = "published"
        _save(receipt_path, state)
    return {"cache_dir": str(cache_dir), "entries": entries, "stats": stats}
