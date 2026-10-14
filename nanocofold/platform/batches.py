"""Reviewed, resumable groups of ordinary platform jobs (no inference in review)."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from .inputs import INPUT_PATHS, parse_fasta, normalized_config, normalized_fasta_config, _deep_merge
from .jobs import TERMINAL
from .errors import public_error
from .paths import contained, sha256
from ..utils.seq import validate_candidate_id, candidate_id_key, candidate_id_aliases

MAX_BATCH = 200
MODES = {"complex", "design", "screen", "mature"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def cdr3_library(value):
    """Replace an explicitly supplied, inclusive, 1-based scaffold interval."""
    if not isinstance(value, dict) or set(value) - {"fasta", "scaffold_fasta", "start", "end"}:
        raise ValueError("CDR3는 fasta/scaffold_fasta/start/end 객체로 입력하세요.")
    _, scaffold = parse_fasta(value.get("scaffold_fasta", ""), single=True)[0]
    start, end = value.get("start"), value.get("end")
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(scaffold):
        raise ValueError("CDR3 교체 구간은 기준 VHH 전체 서열의 1-based 시작·끝 번호입니다.")
    rows = parse_fasta(value.get("fasta", ""))
    # Bound expansion before allocating each full scaffold copy.
    expected_bytes = sum(len(name.encode("utf-8")) + len(scaffold) - (end - start + 1) + len(seq) + 3
                         for name, seq in rows)
    if expected_bytes > 20 * 1024 * 1024:
        raise ValueError("CDR3 접목 후 전체 VHH 라이브러리는 20MB 이하여야 합니다.")
    return "".join(f">{name}\n{scaffold[:start-1]}{seq}{scaffold[end:]}\n" for name, seq in rows)


def expand_manifest(payload):
    if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload.get("version") != 1 or payload.get("mode") not in MODES:
        raise ValueError("배치는 version: 1과 분석 mode가 필요합니다.")
    if set(payload) - {"version", "mode", "name", "entries", "targets_fasta", "partners_fasta", "cdr3_library", "api_consent", "config", "mock", "vhh_fasta", "wt_fasta", "provider", "msa_backend", "hotspots"}:
        raise ValueError("지원하지 않는 배치 필드입니다.")
    mode = payload["mode"]
    if payload.get("entries") is not None and payload.get("targets_fasta") is not None:
        raise ValueError("entries와 targets_fasta 중 한 가지 입력 형식을 선택하세요.")
    if "mock" in payload and type(payload["mock"]) is not bool:
        raise ValueError("mock는 true/false여야 합니다.")
    common = {key: copy.deepcopy(payload[key]) for key in ("config", "mock", "vhh_fasta", "wt_fasta", "provider", "msa_backend", "hotspots") if key in payload}
    if payload.get("cdr3_library") is not None:
        if mode != "screen" or payload.get("vhh_fasta"):
            raise ValueError("CDR3 라이브러리는 screen에서 전체 VHH 라이브러리 대신 사용하세요.")
        common["vhh_fasta"] = cdr3_library(payload["cdr3_library"])
    entries = payload.get("entries")
    if entries is None:
        targets = parse_fasta(payload.get("targets_fasta", ""))
        entries = [{"id": name, "content": f">{name}\n{seq}\n"} for name, seq in targets]
        if mode == "complex":
            partners = parse_fasta(payload.get("partners_fasta", ""))
            for item, (_, seq) in zip(entries, targets):
                if item["id"] in {name for name, _ in partners}:
                    raise ValueError("표적과 공통 파트너의 이름은 달라야 합니다.")
                item["proteins"] = [{"name": item["id"], "sequence": seq}] + [{"name": name, "sequence": sequence} for name, sequence in partners]
                item.pop("content")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BATCH:
        raise ValueError(f"배치당 독립 분석 1–{MAX_BATCH}개를 입력하세요.")
    rows, names = [], set()
    allowed = {"id", "content", "proteins", "settings", "config", "vhh_fasta", "wt_fasta", "provider", "msa_backend", "hotspots", "mock", "reference", "anchor_chain", "interest_positions"}
    for number, item in enumerate(entries, 1):
        try:
            if not isinstance(item, dict) or set(item) - allowed:
                raise ValueError("지원하지 않는 항목 필드입니다. 경로/명령 대신 검토용 입력을 사용하세요.")
            name = item.get("id")
            if not isinstance(name, str) or validate_candidate_id(name) != name:
                raise ValueError("항목 ID는 앞뒤 공백 없는 후보 ID여야 합니다.")
            if candidate_id_key(name) in names:
                raise ValueError("항목 ID는 유니코드 정규화·대소문자를 무시해도 서로 달라야 합니다.")
            names.add(candidate_id_key(name))
            row = {**copy.deepcopy(common), **copy.deepcopy(item), "mode": mode}
            row.pop("id")
            if mode != "complex" and any(key in row for key in ("settings", "proteins", "reference", "anchor_chain", "interest_positions")):
                raise ValueError("로컬 서열 배치는 content와 config를 사용하세요. proteins/settings/reference는 복합체 입력입니다.")
            if mode == "complex":
                from ..complex import proteins, validate_settings
                proteins(row.get("proteins"))
                validate_settings(row.get("settings", {}))
            else:
                parse_fasta(row.get("content", ""), single=True)
                if mode == "screen":
                    parse_fasta(row.get("vhh_fasta", ""))
                if mode == "mature":
                    parse_fasta(row.get("wt_fasta", ""), single=True)
            if "config" in common and "config" in item:
                row["config"] = _deep_merge(common["config"], item["config"])
            rows.append({"id": name, "display_id": name, "alias": candidate_id_aliases([name])[name], "input": row})
        except (ValueError, TypeError) as exc:
            raise ValueError(f"항목 {number} ({item.get('id', '?') if isinstance(item, dict) else '?'}): {exc}") from exc
    return rows


class BatchManager:
    def __init__(self, inputs, jobs):
        self.inputs, self.jobs = inputs, jobs
        with jobs.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,created REAL NOT NULL,payload TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS batches_created ON batches(created DESC,id)")

    def _save(self, batch):
        with self.jobs.store.connect() as db:
            db.execute("INSERT INTO batches VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (batch["id"], batch["created"], json.dumps(batch, ensure_ascii=False)))

    def get(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("배치 ID 오류")
        with self.jobs.store.connect() as db:
            row = db.execute("SELECT payload FROM batches WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError(identifier)
        batch = json.loads(row["payload"])
        for row in batch["rows"]:
            if row.get("job_id"):
                row["job"] = self.jobs.store.get(row["job_id"])
        return batch

    def list(self):
        with self.jobs.store.connect() as db:
            rows = db.execute("SELECT payload FROM batches ORDER BY created DESC,id LIMIT 50").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def _files(self, cfg):
        files = {}
        for field in INPUT_PATHS:
            section, key = field.split(".")
            if cfg.get(section, {}).get(key):
                path = contained(self.inputs.workspace, cfg[section][key], exists=True)
                files[field] = sha256(path)
        return files

    def review(self, payload):
        rows = expand_manifest(payload)  # Validate all sequences before writing any inputs.
        configs = [(normalized_config(self.inputs.workspace, row["input"].get("config"))
                    if payload["mode"] == "complex" else normalized_fasta_config(self.inputs.workspace, row["input"]))
                   for row in rows]
        # Consent covers this exact manifest, never an earlier single-analysis draft.
        requires_api = any(cfg.get("screen", {}).get("allow_public_msa_server") or cfg.get("complex", {}).get("allow_public_msa_server") for cfg in configs)
        if requires_api and payload.get("api_consent") is not True:
            raise ValueError("이 배치의 표적과 후보 서열을 외부 MSA 서비스로 보내는 데 동의하세요.")
        batch = {"id": uuid.uuid4().hex, "name": str(payload.get("name", "배치 분석"))[:120], "mode": payload["mode"], "created": time.time(), "manifest_sha256": digest(payload), "rows": [], "predictions_executed": False}
        folder = self.jobs.store.root / "batches" / batch["id"]
        folder.mkdir(parents=True)
        source = folder / "manifest.json"
        source.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        batch.update(manifest_path=str(source), manifest_file_sha256=sha256(source))
        for number, row in enumerate(rows):
            entry = {key: row[key] for key in ("id", "display_id", "alias")}
            try:
                value = copy.deepcopy(row["input"])
                value.setdefault("mock", configs[number]["run"]["mock"])
                if batch["mode"] == "complex":
                    value["settings"] = _deep_merge(value.get("config", {}).get("complex", {}), value.get("settings", {}))
                    prepared = self.inputs.complex(value)
                    cfg = _deep_merge(prepared["config"], value.get("config", {}))
                    # The reviewed chains/intervals remain authoritative.
                    cfg["target"] = prepared["config"]["target"]
                    cfg["complex"] = prepared["config"]["complex"]
                    cfg["run"].update(mode="complex", mock=prepared["config"]["run"]["mock"])
                    prepared = self.inputs._save(Path(prepared["config_path"]).parent, cfg, prepared["review"])
                else:
                    supplied = value.get("config", {})
                    provider = supplied.get("structure", {}).get("provider")
                    if provider in {"boltz2", "chai1"}:
                        value.setdefault("provider", provider)
                    if "backend" in supplied.get("structure", {}).get("msa", {}):
                        value.setdefault("msa_backend", supplied["structure"]["msa"]["backend"])
                    if "hotspots" in supplied.get("target", {}):
                        value.setdefault("hotspots", supplied["target"]["hotspots"])
                    prepared = self.inputs.fasta(value)
                entry.update(input_id=prepared["input_id"], input_sha256=digest(prepared), input_files=self._files(prepared["config"]), config_file_sha256=sha256(Path(prepared["config_path"])), review=prepared["review"], job_id=None)
            except (ValueError, TypeError) as exc:
                entry["error"] = public_error(exc)
            batch["rows"].append(entry)
        batch["review_sha256"] = digest(batch)
        self._save(batch)
        return batch

    def submit(self, identifier, payload):
        path = self.jobs.store.root / f"batch-{identifier}.lock"
        # Validate ID before using it as a filename.
        self.get(identifier)
        with path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            batch = self.get(identifier)
            if payload.get("review_sha256") != batch["review_sha256"]:
                raise ValueError("검토한 배치와 제출 내용이 다릅니다. 다시 확인하세요.")
            source = contained(self.inputs.workspace, batch["manifest_path"], exists=True)
            if sha256(source) != batch["manifest_file_sha256"]:
                raise ValueError("검토한 배치 원본이 변경되었습니다.")
            selected = payload.get("selected", [row["id"] for row in batch["rows"]])
            if not isinstance(selected, list) or not selected or any(not isinstance(s, str) for s in selected) or len(set(selected)) != len(selected) or set(selected) - {row["id"] for row in batch["rows"]}:
                raise ValueError("검토한 항목 ID를 중복 없이 선택하세요.")
            selected = sorted(selected)
            budget = payload.get("budget_seconds", 36000)
            if type(budget) not in (int, float) or not 0 < budget <= 604800:
                raise ValueError("항목별 실행 시간 한도는 0초 초과, 7일 이하여야 합니다.")
            if "budget_seconds" in batch and batch["budget_seconds"] != budget:
                raise ValueError("제출한 배치의 시간 한도는 변경할 수 없습니다.")
            if batch.get("selected") is not None and batch["selected"] != selected:
                raise ValueError("제출을 시작한 배치의 선택 항목은 변경할 수 없습니다.")
            prepared = {}
            for row in batch["rows"]:
                row.pop("job", None)
                if row["id"] not in selected:
                    continue
                if row.get("error"):
                    raise ValueError(f"{row['id']}: 입력 검토 오류를 먼저 수정하거나 제외하세요.")
                value = self.inputs.get(row["input_id"])
                if digest(value) != row["input_sha256"] or self._files(value["config"]) != row["input_files"] or sha256(Path(value["config_path"])) != row["config_file_sha256"]:
                    raise ValueError(f"{row['id']}: 검토 이후 입력 또는 설정이 바뀌었습니다.")
                prepared[row["id"]] = value
            batch["selected"] = selected
            batch["budget_seconds"] = budget
            self._save(batch)
            for number, row in enumerate(batch["rows"]):
                if row["id"] not in selected:
                    continue
                # Replays resolve to the same ordinary job, including after a lost response.
                try:
                    job = self.jobs.create({"config": prepared[row["id"]]["config"], "mode": batch["mode"], "name": f"{batch['name']} · {row['id']}"[:120], "submission_id": f"batch-{identifier}-{number:03d}", "budget_seconds": budget})
                    row.update(job_id=job["id"], submission_error=None)
                except (ValueError, OSError) as exc:
                    row["submission_error"] = public_error(exc)
                self._save(batch)
            return self.get(identifier)

    def cancel(self, identifier):
        self.get(identifier)
        # A cancellation waits for any in-flight registration to finish, so it
        # cannot miss the later rows of a concurrently submitted batch.
        with (self.jobs.store.root / f"batch-{identifier}.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            batch = self.get(identifier)
            for row in batch["rows"]:
                if row.get("job") and row["job"]["state"] not in TERMINAL:
                    self.jobs.cancel(row["job_id"])
            return self.get(identifier)
