"""Operator-only, resumable CPU MSA search on a registered shared host.

No arbitrary hosts, shell commands, remote paths or public API fallback are
accepted here. The server supplies an exact allowlist from reviewed inputs or
verified generated VHH records. Query origins travel with the immutable receipt.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import inspect
import json
import os
import re
import signal
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .runtime_policy import runtime_policy

# Public policy has no registered destination; private operator data is separate.
PROFILE = runtime_policy()["local_msa_profile"]
MAX_A3M_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 96 * 1024 * 1024
DEFAULT_MIN_FREE_BYTES = 80 * 1024**3
RESOURCE_POLICY = {
    "split_memory_limit": "16G", "split_mode": 0, "db_load_mode": 2,
    # Larger database partitions have a separate conservative memory ceiling.
    "split_memory_limit_by_database": {"colabfold_envdb_202108_db": "32G"},
    "max_group_rss_bytes": 64 * 1024**3,
    "min_host_free_bytes": 12 * 1024**3,
    "poll_seconds": 1.0,
}
AMINO = set("ACDEFGHIKLMNPQRSTVWY")


class LocalMSAError(ValueError):
    pass


class LocalMSATransientError(RuntimeError):
    """Temporary registered transport loss; persisted tickets stay authoritative."""


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_encoded(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            try:
                os.fsync(directory)
            except OSError as exc:
                import warnings
                warnings.warn(f"MSA 저장 후 디렉터리 동기화 미지원: {exc}", RuntimeWarning)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise LocalMSAError("Local MSA receipt missing/oversized")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise LocalMSAError("Local MSA receipt must be an object")
    return value


def _bounds(threads, min_free_bytes):
    if type(threads) is not int or not 1 <= threads <= 32:
        raise LocalMSAError("Local MSA threads must be integer 1..32")
    if type(min_free_bytes) is not int or not 16 * 1024**3 <= min_free_bytes <= 1024 * 1024**3:
        raise LocalMSAError("Local MSA min_free_bytes must be integer 16 GiB..1 TiB")


def _below(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise LocalMSAError("Relative registered-root path required")
    result = (Path(root) / relative).resolve()
    if not result.is_relative_to(Path(root).resolve()):
        raise LocalMSAError("Local MSA path escapes its registered root")
    return result


@contextmanager
def _lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise LocalMSAError("Local MSA lock cannot be a symbolic link")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LocalMSAError("Local MSA request/cache is held by another operator process") from exc
        yield descriptor
    finally:
        os.close(descriptor)


def _memory_available():
    values = {line.split(":", 1)[0]: line.split(":", 1)[1].split()[0]
              for line in Path("/proc/meminfo").read_text().splitlines()}
    return int(values["MemAvailable"]) * 1024


def _search_processes():
    found = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = path.read_bytes().split(b"\0")
            if any(Path(arg.decode(errors="replace")).name in {"mmseqs", "colabfold_search"}
                   for arg in arguments[:2] if arg):
                found.append(int(path.parent.name))
        except (OSError, ValueError):
            continue
    return found


def _pid_identity(pid):
    try:
        # starttime makes a recycled numeric PID insufficient for recovery.
        text = (Path("/proc") / str(pid) / "stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


def _ready_manifest():
    """Verify operator-sealed installation; never hash huge archives on submit."""
    if not PROFILE.get("enabled"):
        raise LocalMSAError("Local MSA search is not configured. Use a verified MSA cache or explicitly consent to public API search.")
    if socket.gethostname() != PROFILE["hostname"]:
        raise LocalMSAError("Registered local-search hostname differs")
    root = Path(PROFILE["db_root"])
    path = root / "nanocofold_db_manifest.json"
    if not path.is_file():
        raise LocalMSAError("Operator ready database manifest is missing: " + str(path))
    manifest = _json(path)
    if manifest.get("schema_version") != 1 or manifest.get("status") != "ready" or manifest.get("profile") != PROFILE["name"]:
        raise LocalMSAError("Operator database manifest has not been published as ready")
    if any(p.name.endswith((".aria2", ".partial", ".part", ".tmp")) for p in root.iterdir()):
        raise LocalMSAError("Database download/installation is incomplete")
    files = manifest.get("files")
    sources = manifest.get("databases")
    if not isinstance(files, dict) or not isinstance(sources, dict) or not files:
        raise LocalMSAError("Database manifest lacks file/source ledger")
    if any(not isinstance(entry, dict) for entry in files.values()):
        raise LocalMSAError("Database file ledger entries must be objects")
    for name, version in PROFILE["databases"].items():
        source = sources.get(name, {})
        if (not isinstance(source, dict) or source.get("version") != version or source.get("verified") is not True
                or not isinstance(source.get("verified_at"), str) or not source["verified_at"]
                or not re.fullmatch("[a-f0-9]{64}", str(source.get("source_sha256", "")))):
            raise LocalMSAError("Database source version/hash/verification missing")
        for suffix in ("", "_seq", "_seq_h", "_aln"):
            prefix = name + suffix
            for ending in (".dbtype", ".index"):
                relative = prefix + ending
                if relative not in files:
                    raise LocalMSAError("Converted database/header/index file missing from manifest: " + relative)
                if ending == ".dbtype" and not isinstance(files[relative].get("sha256"), str):
                    raise LocalMSAError("Database dbtype sentinel requires a verified content hash")
            if not any(relative == prefix or re.fullmatch(re.escape(prefix) + r"\.[0-9]+", relative) for relative in files):
                raise LocalMSAError("Converted database data/segments missing: " + prefix)
    for relative, entry in files.items():
        path = _below(root, relative)
        if not isinstance(entry, dict) or not path.is_file():
            raise LocalMSAError("Database manifest file missing")
        stat = path.stat()
        if (type(entry.get("size")) is not int or entry["size"] < 1 or stat.st_size != entry["size"]
                or type(entry.get("mtime_ns")) is not int or stat.st_mtime_ns != entry["mtime_ns"]):
            raise LocalMSAError("Database file changed since operator verification")
        if entry.get("sha256"):
            if not re.fullmatch("[a-f0-9]{64}", str(entry["sha256"])):
                raise LocalMSAError("Database file SHA256 is malformed")
            # Large DB byte hashes are verified once by the installer and
            # sealed into this manifest. Submit checks size/mtime plus small
            # sentinel hashes, avoiding repeated hundreds-of-GB disk scans.
            if stat.st_size <= 16 * 1024 * 1024 and entry["sha256"] != _digest(path):
                raise LocalMSAError("Database sentinel hash mismatch")
    tools = manifest.get("tools", {})
    if not isinstance(tools, dict):
        raise LocalMSAError("Database tool ledger must be an object")
    for name in ("colabfold_search", "mmseqs"):
        entry = tools.get(name, {})
        path = Path(entry.get("path", "/missing"))
        if (not path.is_absolute() or not any(path.resolve().is_relative_to(Path(top).resolve()) for top in PROFILE["executable_roots"])
                or not path.is_file() or not os.access(path, os.X_OK) or entry.get("sha256") != _digest(path)
                or not isinstance(entry.get("version"), str) or not entry["version"]):
            raise LocalMSAError("Registered executable path/version/hash missing or changed")
    if tools["colabfold_search"]["version"] != "1.6.3":
        raise LocalMSAError("Local-search adapter supports validated ColabFold 1.6.3")
    source = tools["colabfold_search"].get("source", {})
    source_path = Path(source.get("path", "/missing"))
    if (not source_path.is_absolute() or not any(source_path.resolve().is_relative_to(Path(top).resolve()) for top in PROFILE["executable_roots"])
            or not source_path.is_file() or source.get("sha256") != _digest(source_path)):
        raise LocalMSAError("ColabFold search source identity missing or changed")
    return manifest, _digest(root / "nanocofold_db_manifest.json")


def _probe(threads=8, min_free_bytes=DEFAULT_MIN_FREE_BYTES):
    _bounds(threads, min_free_bytes)
    try:
        manifest, digest = _ready_manifest()
        identity = {"profile": PROFILE["name"], "database_manifest_sha256": digest,
                    "databases": manifest["databases"], "tools": manifest["tools"]}
        memory, processes = _memory_available(), _search_processes()
        status = "busy" if processes else "resource_wait" if memory < min_free_bytes else "ready"
        return {"status": status, "identity": identity, "mem_available_bytes": memory,
                "min_free_bytes": min_free_bytes, "search_pids": processes}
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        return {"status": "not_ready", "reason": str(exc)}


def _request(payload):
    queries = payload.get("queries")
    _bounds(payload.get("threads"), payload.get("min_free_bytes"))
    if any(not re.fullmatch("[a-f0-9]{64}", str(payload.get(name, ""))) for name in ("database_manifest_sha256", "adapter_sha256")):
        raise LocalMSAError("Local MSA database/adapter identities must be SHA256 hashes")
    if not isinstance(queries, list) or not 1 <= len(queries) <= 25:
        raise LocalMSAError("Local MSA accepts 1..25 explicitly approved base sequences")
    hashes = []
    for entry in queries:
        sequence = entry.get("sequence") if isinstance(entry, dict) else None
        if (not isinstance(sequence, str) or not 1 <= len(sequence) <= 2000 or any(a not in AMINO for a in sequence)
                or set(entry) not in ({"sequence", "query_sha256", "source_id"},
                                      {"sequence", "query_sha256", "source_id", "query_kind"})
                or entry.get("query_kind", "public_base") not in {"public_base", "reviewed_target", "reviewed_vhh", "generated_vhh"}
                or entry.get("query_sha256") != _sha(sequence.encode())
                or not isinstance(entry.get("source_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", entry["source_id"])):
            raise LocalMSAError("Invalid local MSA approved base query")
        hashes.append(entry["query_sha256"])
    if hashes != sorted(set(hashes)) or sum(len(x["sequence"]) for x in queries) > 5000:
        raise LocalMSAError("Local MSA queries must be deduplicated/hash-sorted and total <=5000 residues")
    identity = {key: payload[key] for key in ("queries", "threads", "min_free_bytes", "database_manifest_sha256", "adapter_sha256")}
    if payload.get("run_id") != _sha(_encoded(identity)):
        raise LocalMSAError("Local MSA ticket differs from immutable request hash")
    return identity


def _ticket(run_id):
    if not isinstance(run_id, str) or not re.fullmatch("[a-f0-9]{64}", run_id):
        raise LocalMSAError("Local MSA ticket must be a SHA256")
    return _below(PROFILE["work_root"], "tickets/" + run_id)


def _status(run_id):
    directory = _ticket(run_id)
    if not (directory / "state.json").exists():
        return {"status": "not_found", "run_id": run_id}
    state = _json(directory / "state.json")
    if state.get("status") in {"queued", "running"}:
        launcher = _json(directory / "launcher.json") if (directory / "launcher.json").exists() else state
        pid, start = launcher.get("pid"), launcher.get("process_start")
        if type(pid) is not int or start is None or _pid_identity(pid) != start:
            state = {**state, "status": "interrupted", "reason": "Recorded worker process is no longer alive; explicit resume required"}
            _atomic(directory / "state.json", state)
    return state


def _write_mmseqs_wrapper(directory, manifest):
    """Bound prefilters and load DB pages lazily without editing ColabFold."""
    binary = manifest["tools"]["mmseqs"]
    if _digest(binary["path"]) != binary["sha256"]:
        raise LocalMSAError("Registered MMseqs executable changed before bounded launch")
    split_limits = {str(Path(PROFILE["db_root"]) / name): limit
                    for name, limit in RESOURCE_POLICY.get("split_memory_limit_by_database", {}).items()}
    source = ("#!" + sys.executable + "\nimport os,sys,hashlib\n" +
              "binary=" + repr(binary["path"]) + "\n" +
              "expected=" + repr(binary["sha256"]) + "\n" +
              "split_limits=" + repr(split_limits) + "\n" +
              "digest=hashlib.sha256()\nwith open(binary,'rb') as stream:\n" +
              " for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)\n" +
              "if digest.hexdigest()!=expected: raise RuntimeError('Registered MMseqs executable changed')\n" +
              "args=sys.argv[1:]\n" +
              # ColabFold resets mode to auto when MMSEQS_IGNORE_INDEX is set.
              # Enforce mmap at the native boundary, including expandaln/align,
              # where prefilter splitting does not prevent whole-DB preloading.
              "load_flags=[i for i,a in enumerate(args) if a.split('=',1)[0]=='--db-load-mode']\n" +
              "if load_flags:\n" +
              " i=load_flags[0]\n" +
              " if len(load_flags)!=1 or args[i]!='--db-load-mode' or i+1>=len(args) or args[i+1] not in ('0','1','2','3'):\n" +
              "  raise RuntimeError('Ambiguous registered MSA database loading mode')\n" +
              " args[i+1]=" + repr(str(RESOURCE_POLICY["db_load_mode"])) + "\n" +
              "if args and args[0] in ('search','prefilter'):\n" +
              " if any(a.split('=',1)[0] in ('--split-memory-limit','--split-mode') for a in args):\n" +
              "  raise RuntimeError('Unreviewed override of registered MSA memory policy')\n" +
              " limit=split_limits.get(args[2] if len(args)>2 else ''," + repr(RESOURCE_POLICY["split_memory_limit"]) + ")\n" +
              " args += ['--split-memory-limit',limit,'--split-mode'," + repr(str(RESOURCE_POLICY["split_mode"])) + "]\n" +
              "os.execv(binary,[binary,*args])\n")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "mmseqs_bounded.py"
    if path.is_symlink():
        raise LocalMSAError("Bounded MMseqs wrapper cannot be a symbolic link")
    if path.exists() and path.read_text() != source:
        raise LocalMSAError("Retained MSA wrapper differs from immutable resource policy")
    if not path.exists():
        path.write_text(source)
        path.chmod(0o700)
    return path


def _group_rss_bytes(pgid):
    """Conservative RSS sum for our new session; shared pages may be counted twice."""
    total = 0
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                total += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            continue
    return total


def _group_members(pgid):
    members = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                members[int(path.parent.name)] = fields[19]
        except (OSError, ValueError, IndexError):
            continue
    return members


def _terminate_search_group(process):
    members = _group_members(process.pid)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    # A child can ignore TERM after its parent exits. A still-matching birth
    # stamp proves this group remains ours, even after its leader was reaped.
    if any(_pid_identity(pid) == stamp for pid, stamp in members.items()):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def _run_bounded_search(argv, environment, log):
    """Watch only an owned process group and retain an explicit resource failure.

    MMseqs' split limit bounds prefilter structures, not total RSS. The sampled
    watchdog provides a second guard; it is not a kernel-enforced memory cap.
    """
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               env=environment, start_new_session=True)
    peak, minimum, reason = 0, None, None
    try:
        while not _search_exit_ready(process):
            rss, free = _group_rss_bytes(process.pid), _memory_available()
            peak = max(peak, rss)
            minimum = free if minimum is None else min(minimum, free)
            if rss > RESOURCE_POLICY["max_group_rss_bytes"]:
                reason = "owned_search_rss_limit"
            elif free < RESOURCE_POLICY["min_host_free_bytes"]:
                reason = "host_memory_pressure"
            if reason:
                _terminate_search_group(process)
                log.write(("\n[NanoCoFold resource guard] " + reason + "\n").encode())
                log.flush()
                break
            time.sleep(RESOURCE_POLICY["poll_seconds"])
    except BaseException:
        _terminate_search_group(process)
        raise
    # A successful search launcher can leave an owned child writing artifacts.
    # Keep the leader unreaped on Linux until the existing birthstamp-checked
    # group cleanup has run; only then may the worker release its shared lease.
    _terminate_search_group(process)
    returncode = process.wait()
    return {"returncode": returncode, "resource_abort": reason,
            "peak_sampled_group_rss_bytes": peak, "min_sampled_host_free_bytes": minimum,
            "worker_reaped": process.poll() is not None, "resource_policy": RESOURCE_POLICY}


def _search_exit_ready(process):
    if hasattr(os, "waitid") and hasattr(os, "WNOWAIT"):
        event = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        return event is not None
    return process.poll() is not None


def _argv(request, directory, manifest, mmseqs=None):
    tools = manifest["tools"]
    return [tools["colabfold_search"]["path"], str(directory / "queries.fasta"), PROFILE["db_root"], str(directory / "raw"),
            "--mmseqs", str(mmseqs or tools["mmseqs"]["path"]), "--threads", str(request["threads"]), "--gpu", "0",
            "--use-env", "1", "--use-templates", "0", "--pair-mode", "unpaired", "--db-load-mode", str(RESOURCE_POLICY["db_load_mode"]),
            "--db1", "uniref30_2302_db", "--db3", "colabfold_envdb_202108_db", "--unpack", "1"]


def _worker(run_id, lock_fd):
    directory = _ticket(run_id)
    request = _json(directory / "request.json")
    _request(request)
    previous = _json(directory / "state.json")
    attempt = previous["attempt"]
    work = _below(directory, f"attempts/{attempt:04d}")
    try:
        readiness = _probe(request["threads"], request["min_free_bytes"])
        if readiness["status"] != "ready" or readiness["identity"]["database_manifest_sha256"] != request["database_manifest_sha256"]:
            raise LocalMSAError("Readiness/database identity changed before search: " + readiness["status"])
        manifest, _ = _ready_manifest()
        work.mkdir(parents=True, exist_ok=True)
        fasta = "".join(f">{q['query_sha256']}\n{q['sequence']}\n" for q in request["queries"])
        (work / "queries.fasta").write_text(fasta)
        bounded = _write_mmseqs_wrapper(work, manifest)
        argv = _argv(request, work, manifest, mmseqs=bounded)
        _atomic(work / "command.json", {"argv": argv, "input_sha256": _sha(fasta.encode()),
                                       "database_manifest_sha256": request["database_manifest_sha256"],
                                       "resource_policy": RESOURCE_POLICY, "wrapper_sha256": _digest(bounded)})
        state = {"status": "running", "run_id": run_id, "attempt": attempt,
                 "pid": os.getpid(), "process_start": _pid_identity(os.getpid()), "started": time.time()}
        _atomic(directory / "state.json", state)
        environment = {**os.environ, "MMSEQS_IGNORE_INDEX": "1", "OMP_NUM_THREADS": str(request["threads"])}
        with (work / "search.log").open("ab") as log:
            result = _run_bounded_search(argv, environment, log)
        _atomic(work / "resource_usage.json", result)
        outputs = {}
        for query in request["queries"]:
            path = work / "raw" / (query["query_sha256"] + ".a3m")
            if path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_A3M_BYTES:
                outputs[query["query_sha256"]] = {"path": str(path.relative_to(directory)), "sha256": _digest(path), "bytes": path.stat().st_size}
        status = "complete" if result["returncode"] == 0 and not result["resource_abort"] and len(outputs) == len(request["queries"]) else "partial_failure" if outputs else "failed"
        _atomic(directory / "state.json", {**state, "status": status, "finished": time.time(),
                                           "returncode": result["returncode"], "outputs": outputs,
                                           "resource_abort": result["resource_abort"], "resource_usage": result})
    except Exception as exc:
        _atomic(directory / "state.json", {"status": "failed", "run_id": run_id, "attempt": attempt,
                                           "reason": f"{type(exc).__name__}: {exc}", "finished": time.time()})
    finally:
        os.close(lock_fd)  # Detached worker owns the inherited shared-host lease.


def _submit(payload, worker_source):
    identity = _request(payload)
    readiness = _probe(payload["threads"], payload["min_free_bytes"])
    if readiness["status"] != "ready":
        return readiness
    if readiness["identity"]["database_manifest_sha256"] != payload["database_manifest_sha256"]:
        return {"status": "not_ready", "reason": "Database identity changed since cache selection"}
    directory = _ticket(payload["run_id"])
    with _lock(Path(PROFILE["work_root"]) / "tickets" / (payload["run_id"] + ".lock")):
        if (directory / "request.json").exists():
            existing = _json(directory / "request.json")
            if _request(existing) != identity:
                raise LocalMSAError("Existing local MSA ticket inputs changed")
            state = _status(payload["run_id"])
            if state["status"] in {"queued", "running", "complete"}:
                return state
            if not payload.get("resume", False):
                return {**state, "resume_required": True}
            attempt = int(state.get("attempt", 1)) + 1
        else:
            attempt = 1
        # Keep the FD locked across SSH exit; child inherits this descriptor.
        lease = Path(PROFILE["work_root"]) / "shared_search.lock"
        lease.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lease, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy", "reason": "Another NanoCoFold local MSA worker holds the shared search lease"}
            directory.mkdir(parents=True, exist_ok=True)
            _atomic(directory / "request.json", {**identity, "run_id": payload["run_id"]})
            source_path = Path(PROFILE["work_root"]) / ("worker_" + _sha(worker_source.encode()) + ".py")
            if not source_path.exists():
                source_path.write_text(worker_source)
            elif source_path.read_text() != worker_source:
                raise LocalMSAError("Persistent local MSA worker source differs from its hash")
            _atomic(directory / "state.json", {"status": "queued", "run_id": payload["run_id"], "attempt": attempt})
            process = subprocess.Popen([sys.executable, str(source_path), "--worker", payload["run_id"], str(descriptor)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=True, pass_fds=(descriptor,))
            _atomic(directory / "launcher.json", {"pid": process.pid, "process_start": _pid_identity(process.pid), "attempt": attempt})
            return {"status": "queued", "run_id": payload["run_id"], "attempt": attempt}
        finally:
            os.close(descriptor)


def _fetch(run_id, query_hash):
    state = _status(run_id)
    if state.get("status") not in {"complete", "partial_failure", "failed"}:
        return {"status": state["status"], "run_id": run_id}
    entry = state.get("outputs", {}).get(query_hash)
    if not entry:
        return {"status": "missing", "run_id": run_id, "reason": "No committed output for requested query"}
    path = _below(_ticket(run_id), entry["path"])
    if not path.is_file() or path.stat().st_size > MAX_A3M_BYTES or _digest(path) != entry["sha256"]:
        raise LocalMSAError("Committed local MSA output is missing/corrupt")
    return {"status": "complete", "run_id": run_id, "sha256": entry["sha256"],
            "a3m_base64": base64.b64encode(path.read_bytes()).decode()}


def _serve(request, worker_source):
    if not isinstance(request, dict):
        raise LocalMSAError("Local MSA protocol request must be an object")
    if request.get("profile", PROFILE["name"]) != PROFILE["name"]:
        raise LocalMSAError("Unregistered local MSA profile")
    action = request.get("action")
    fields = {"probe": {"threads", "min_free_bytes"},
              "submit": {"queries", "threads", "min_free_bytes", "database_manifest_sha256", "adapter_sha256", "run_id", "resume"},
              "status": {"run_id"}, "fetch": {"run_id", "query_sha256"}}
    if action not in fields or set(request) - fields[action] - {"action", "profile"}:
        raise LocalMSAError("Local MSA protocol contains unsupported operation/fields")
    if action == "submit" and type(request.get("resume", False)) is not bool:
        raise LocalMSAError("Local MSA resume must be a boolean")
    if action == "probe":
        return _probe(request.get("threads", 8), request.get("min_free_bytes", DEFAULT_MIN_FREE_BYTES))
    if action == "submit":
        return _submit(request, worker_source)
    if action == "status":
        return _status(request.get("run_id"))
    if action == "fetch":
        if not re.fullmatch("[a-f0-9]{64}", str(request.get("query_sha256", ""))):
            raise LocalMSAError("Invalid local MSA query hash")
        return _fetch(request.get("run_id"), request["query_sha256"])
    raise LocalMSAError("Local MSA accepts probe/submit/status/fetch only")


def _remote_program():
    """Freeze stdlib-only backend source; remote stdin contains data, never code."""
    functions = (LocalMSAError, _sha, _digest, _encoded, _atomic, _json, _bounds, _below, _lock,
                 _memory_available, _search_processes, _pid_identity, _ready_manifest, _probe,
                 _request, _ticket, _status, _write_mmseqs_wrapper, _group_rss_bytes, _group_members,
                 _terminate_search_group, _search_exit_ready, _run_bounded_search,
                 _argv, _worker, _submit, _fetch, _serve)
    prefix = "import base64,fcntl,hashlib,json,os,re,signal,socket,subprocess,sys,tempfile,time\nfrom pathlib import Path\nfrom contextlib import contextmanager\n"
    prefix += "PROFILE=" + repr(PROFILE) + "\nMAX_A3M_BYTES=" + repr(MAX_A3M_BYTES) + "\nDEFAULT_MIN_FREE_BYTES=" + repr(DEFAULT_MIN_FREE_BYTES) + "\nRESOURCE_POLICY=" + repr(RESOURCE_POLICY) + "\nAMINO=set(" + repr("".join(sorted(AMINO))) + ")\n"
    body = prefix + "\n".join(inspect.getsource(function) for function in functions)
    worker = body + "\nif __name__=='__main__':\n _worker(sys.argv[2],int(sys.argv[3]))\n"
    return body + "\nWORKER_SOURCE=" + repr(worker) + "\ntry:\n request=json.loads(sys.stdin.buffer.read(1024*1024+1))\n result=_serve(request,WORKER_SOURCE)\nexcept Exception as exc:\n result={'status':'error','reason':str(exc)}\nprint(json.dumps(result,allow_nan=False))\n"


class SSHTransport:
    """Fixed registered SSH profile; sequence data travels only through stdin."""
    def call(self, action, **payload):
        if not PROFILE.get("enabled"):
            raise LocalMSAError("Local MSA search is not configured; no remote request was sent.")
        program = _remote_program()
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", PROFILE["host"],
                "python3", "-c", shlex.quote(program)]
        try:
            result = subprocess.run(argv, input=_encoded({"action": action, "profile": PROFILE["name"], **payload}),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        except subprocess.TimeoutExpired:
            # Do not stringify the exception: its argv contains the complete
            # native backend program. A later call reconciles retained tickets.
            raise LocalMSATransientError("Registered local MSA connection timed out; waiting for reconnection") from None
        if result.returncode == 255:
            message = result.stderr.decode(errors="replace").lower()
            if any(marker in message for marker in ("timed out", "connection reset", "connection refused",
                                                     "no route to host", "network is unreachable")):
                raise LocalMSATransientError("Registered local MSA connection unavailable; waiting for reconnection")
        if result.returncode != 0 or len(result.stdout) > MAX_RESPONSE_BYTES:
            raise LocalMSAError("Registered SSH MSA transport failed; ticket must be reconciled before retry")
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("status") == "error":
            raise LocalMSAError(value.get("reason", "Malformed local MSA transport response"))
        return value


class LocalTransport(SSHTransport):
    """Same protocol on the registered search server; no SSH credentials needed."""
    def call(self, action, **payload):
        if not PROFILE.get("enabled"):
            raise LocalMSAError("Local MSA search is not configured; no process was started.")
        if socket.gethostname() != PROFILE["hostname"]:
            raise LocalMSAError("LocalTransport can run only on the registered search host")
        program = _remote_program()
        result = subprocess.run([sys.executable, "-c", program],
                                input=_encoded({"action": action, "profile": PROFILE["name"], **payload}),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if result.returncode != 0 or len(result.stdout) > MAX_RESPONSE_BYTES:
            raise LocalMSAError("Local MSA transport failed; reconcile its persisted ticket")
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("status") == "error":
            raise LocalMSAError(value.get("reason", "Malformed local MSA transport response"))
        return value


def acquire_local_monomers(cache_root, queries, *, approved_base_sequences, allow_search=False,
                           profile=None, transport=None, threads=8,
                           min_free_bytes=DEFAULT_MIN_FREE_BYTES, resume=False):
    """Return cache hits or one persisted base-query ticket; never poll/restart.

    ``approved_base_sequences`` is a server-created exact sequence -> source ID
    or {source_id, query_kind} map, never accepted from browser configuration.
    Generated sequences must be verified pipeline outputs. Repeat the call to
    retrieve a completed ticket. ``resume=True`` explicitly retries a confirmed
    failed/interrupted ticket in a new retained attempt; uncertain submission is
    reconciled against server state first. Changed DB/tool/options get new cache.
    """
    from .colabfold_msa import cached_monomer, promote_monomer
    _bounds(threads, min_free_bytes)
    profile = PROFILE["name"] if profile is None else profile
    if profile != PROFILE["name"] or type(allow_search) is not bool or type(resume) is not bool:
        raise LocalMSAError("Registered operator profile and boolean search/resume required")
    if not isinstance(queries, (list, tuple)) or any(not isinstance(sequence, str) for sequence in queries):
        raise LocalMSAError("Local MSA queries must be a list/tuple of exact base sequences")
    sequences = sorted(set(queries))
    if not sequences or len(sequences) > 25 or not isinstance(approved_base_sequences, dict):
        raise LocalMSAError("Local MSA accepts 1..25 approved base queries")
    records = []
    for sequence in sequences:
        if sequence not in approved_base_sequences:
            raise LocalMSAError("Unapproved sequence cannot be searched")
        origin = approved_base_sequences[sequence]
        record = {"sequence": sequence, "query_sha256": _sha(sequence.encode())}
        if isinstance(origin, str):
            record["source_id"] = origin
        elif isinstance(origin, dict) and set(origin) == {"source_id", "query_kind"}:
            record.update(origin)
        else:
            raise LocalMSAError("Reviewed local MSA origin must be source_id/query_kind")
        records.append(record)
    records.sort(key=lambda record: record["query_sha256"])
    # Full query validation is performed before invoking even the read-only RPC.
    _request({"queries": records, "threads": threads, "min_free_bytes": min_free_bytes,
              "database_manifest_sha256": "0" * 64, "adapter_sha256": "0" * 64,
              "run_id": _sha(_encoded({"queries": records, "threads": threads, "min_free_bytes": min_free_bytes,
                                        "database_manifest_sha256": "0" * 64, "adapter_sha256": "0" * 64}))})
    transport = transport or SSHTransport()
    readiness = transport.call("probe", threads=threads, min_free_bytes=min_free_bytes)
    identity = readiness.get("identity")
    if not isinstance(identity, dict) or not re.fullmatch("[a-f0-9]{64}", str(identity.get("database_manifest_sha256", ""))):
        return {"status": "not_ready", "entries": {}, "stats": {"cache_hits": 0, "search_submissions": 0}, "readiness": readiness}
    settings = {"threads": threads, "min_free_bytes": min_free_bytes, "adapter_sha256": _digest(__file__)}
    namespace = _sha(_encoded({"source": identity, "settings": settings}))
    cache = Path(cache_root).resolve() / "local_mmseqs" / PROFILE["name"] / namespace
    if not cache.resolve().is_relative_to(Path(cache_root).resolve()):
        raise LocalMSAError("Local MSA namespace cannot escape the operator cache root")
    entries, missing = {}, []
    stats = {"cache_hits": 0, "search_submissions": 0, "unique_queries": len(records)}
    with ExitStack() as locks:
        for record in records:
            locks.enter_context(_lock(cache / ".locks" / (record["query_sha256"] + ".lock")))
        for record in records:
            directory = cache / record["query_sha256"]
            if directory.exists():
                entries[record["sequence"]] = cached_monomer(cache, record["sequence"])
                stats["cache_hits"] += 1
            else:
                missing.append(record)
        tickets = {}
        for record in missing:
            path = cache / ".query_tickets" / (record["query_sha256"] + ".json")
            if path.exists():
                run_id = _json(path).get("run_id")
                if not isinstance(run_id, str) or not re.fullmatch("[a-f0-9]{64}", run_id):
                    raise LocalMSAError("Local MSA query receipt has an invalid ticket hash")
                tickets[run_id] = None
        pending = []
        for run_id in tickets:
            state = transport.call("status", run_id=run_id)
            local = _json(cache / ".tickets" / run_id / "state.json")
            if state["status"] in {"complete", "partial_failure", "failed"}:
                for record in list(missing):
                    ticket_path = cache / ".query_tickets" / (record["query_sha256"] + ".json")
                    if not ticket_path.exists() or _json(ticket_path)["run_id"] != run_id:
                        continue
                    response = transport.call("fetch", run_id=run_id, query_sha256=record["query_sha256"])
                    if response["status"] != "complete":
                        continue
                    raw = base64.b64decode(response["a3m_base64"], validate=True)
                    if len(raw) > MAX_A3M_BYTES or _sha(raw) != response.get("sha256"):
                        raise LocalMSAError("Local search output download hash/size mismatch")
                    source = cache / ".tickets" / run_id / (record["query_sha256"] + ".a3m")
                    source.write_bytes(raw)
                    entry = promote_monomer(cache, record["sequence"], source,
                        {"provider": "registered_local_mmseqs", "profile": PROFILE["name"], "database": identity,
                         "settings": settings, "run_id": run_id, "source_id": record["source_id"],
                         "query_kind": record.get("query_kind", "public_base"),
                         "private_mutant_search": record.get("query_kind") == "generated_vhh"})
                    entries[record["sequence"]] = entry
                    missing.remove(record)
            outstanding = [record for record in missing if (cache / ".query_tickets" / (record["query_sha256"] + ".json")).exists()
                           and _json(cache / ".query_tickets" / (record["query_sha256"] + ".json"))["run_id"] == run_id]
            if not outstanding:
                continue
            if state["status"] == "not_found":
                if local.get("status") == "rejected":
                    for record in outstanding:
                        (cache / ".query_tickets" / (record["query_sha256"] + ".json")).unlink()
                    continue
                raise LocalMSAError("Persisted local submission has uncertain outcome; operator reconciliation required")
            if state["status"] in {"failed", "partial_failure", "interrupted"} and resume and allow_search:
                # Search only the still-missing siblings. A complete member of
                # a partially failed batch is never inferred again on resume.
                for record in outstanding:
                    (cache / ".query_tickets" / (record["query_sha256"] + ".json")).unlink()
                continue
            pending.append({"run_id": run_id, **state})
        if pending:
            return {"status": pending[0]["status"], "cache_dir": str(cache), "entries": entries, "tickets": pending, "stats": stats}
        if not missing:
            return {"status": "complete", "cache_dir": str(cache), "entries": entries, "stats": stats}
        if not allow_search or readiness["status"] != "ready":
            return {"status": "cache_miss" if not allow_search else readiness["status"], "cache_dir": str(cache),
                    "entries": entries, "missing": [r["query_sha256"] for r in missing], "readiness": readiness, "stats": stats}
        request = {"queries": missing, **settings, "database_manifest_sha256": identity["database_manifest_sha256"]}
        run_id = _sha(_encoded(request))
        request["run_id"] = run_id
        directory = cache / ".tickets" / run_id
        if (directory / "request.json").exists() and _json(directory / "request.json") != request:
            raise LocalMSAError("Immutable local-search request changed")
        _atomic(directory / "request.json", request)
        _atomic(directory / "state.json", {"status": "submission_intent", "run_id": run_id})
        for record in missing:
            _atomic(cache / ".query_tickets" / (record["query_sha256"] + ".json"), {"run_id": run_id})
        try:
            state = transport.call("submit", **request, resume=resume)
        except Exception:
            _atomic(directory / "state.json", {"status": "uncertain", "run_id": run_id})
            raise
        accepted = state["status"] in {"queued", "running", "complete"}
        _atomic(directory / "state.json", {"status": "accepted" if accepted else "rejected", "run_id": run_id, "server": state})
        stats["search_submissions"] += int(state["status"] == "queued")
        return {"status": state["status"], "run_id": run_id, "cache_dir": str(cache), "entries": entries, "stats": stats, "server": state}
