from __future__ import annotations

import hashlib
import fcntl
import json
import os
import shutil
import tempfile
import re
from pathlib import Path

from ..config import PROJECT_ROOT
from . import seq as seq_utils
from . import shell


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(seq_utils.clean_sequence(sequence).encode()).hexdigest()[:16]


def empty_msa_token() -> str:
    return "empty"


def ensure_a3m(
    sequence: str,
    msa_cfg: dict,
    run_dir: Path | None = None,
    dry_run: bool = False,
) -> tuple[str, list[str]]:
    """설정에 따라 타깃 MSA를 확보한다. 반환: (a3m 경로 또는 'empty', 경고 목록).

    - `backend: empty` 는 캐시가 있어도 항상 empty(F10/G10).
    - dry_run 이면 어떤 외부 실행도 하지 않는다(G03).
    """
    warnings: list[str] = []
    backend = (msa_cfg or {}).get("backend", "empty")
    if backend == "empty":
        return empty_msa_token(), warnings
    if dry_run:
        warnings.append("dry-run: MSA 생성을 건너뜁니다(msa=empty).")
        return empty_msa_token(), warnings
    cache_dir = Path((msa_cfg or {}).get("cache_dir") or "data/msa_cache")
    if not cache_dir.is_absolute():
        cache_dir = PROJECT_ROOT / cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{sequence_hash(sequence)}.a3m"

    if backend == "cache":
        if target.exists():
            validate_target_a3m(target, sequence)
            return str(target), warnings
        warnings.append(
            f"캐시된 MSA가 없습니다: {target}. 서열만 입력으로 단일서열 모드(msa=empty)로 진행합니다. "
            "정확도를 높이려면 structure.msa.backend=colabfold_search 로 MSA를 먼저 생성하세요."
        )
        return empty_msa_token(), warnings
    if backend not in {"mmseqs", "colabfold_search"}:
        raise ValueError(f"지원하지 않는 MSA backend: {backend}. 허용값: cache, mmseqs, colabfold_search, empty.")
    return _search_a3m(sequence, msa_cfg, cache_dir, warnings)


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _search_identity(sequence, cfg):
    """Search reuse requires a declared DB revision, not merely a query match.

    Without a DB manifest, each search gets a new workspace. Explicit legacy
    ``backend=cache`` remains available and never rewrites the supplied cache.
    """
    backend = cfg["backend"]
    command = str(cfg.get("mmseqs_binary" if backend == "mmseqs" else "colabfold_search") or "")
    executable = shutil.which(command)
    if executable is None and Path(command).is_file():
        executable = str(Path(command).resolve())
    manifest = cfg.get("database_manifest")
    manifest_path = Path(manifest) if manifest else None
    if manifest_path and not manifest_path.is_absolute():
        manifest_path = PROJECT_ROOT / manifest_path
    database = Path(str(cfg.get("db") or ""))
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    return {"schema_version": 1, "query_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
            "backend": backend, "database": str(database.resolve()),
            "database_manifest_sha256": _digest(manifest_path) if manifest_path else None,
            "executable": str(Path(executable).resolve()) if executable else command,
            "executable_sha256": _digest(executable) if executable else None,
            "threads": int(cfg.get("threads", 8)), "sensitivity": cfg.get("sensitivity", 5.7),
            "max_seqs": int(cfg.get("max_seqs", 300)), "adapter_sha256": _digest(__file__)}


def _search_a3m(sequence, cfg, cache_dir, warnings):
    from .atomic import write_json, write_bytes, check_output
    backend = cfg["backend"]
    binary = cfg.get("mmseqs_binary" if backend == "mmseqs" else "colabfold_search")
    if not binary or (backend == "mmseqs" and not Path(str(binary)).is_file() and not shutil.which(str(binary))):
        warnings.append("structure.msa.mmseqs_binary 가 없어 msa=empty 로 진행합니다 (tools/mmseqs)." if backend == "mmseqs"
                        else "structure.msa.colabfold_search 경로가 지정되지 않아 msa=empty 로 진행합니다.")
        return empty_msa_token(), warnings
    if not cfg.get("db"):
        warnings.append("structure.msa.db (예: data/msa_db/uniref50_db)가 없어 msa=empty 로 진행합니다." if backend == "mmseqs"
                        else "structure.msa.db(UniRef30)가 지정되지 않아 msa=empty 로 진행합니다.")
        return empty_msa_token(), warnings
    sequence = seq_utils.clean_sequence(sequence)
    identity = _search_identity(sequence, cfg)
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = cache_dir / "searches" / key
    check_output(root / "latest.json", cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    # Serialize only the same search request; completed/failed attempts are retained.
    lock_path = root / ".lock"
    check_output(lock_path, cache_dir)
    with os.fdopen(os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reusable = bool(identity["database_manifest_sha256"] and identity["executable_sha256"])
        if reusable:
            try:
                saved = json.loads((root / "latest.json").read_text())
                result = root / saved["result"]
                if (saved["identity"] == identity and result.resolve().is_relative_to(root.resolve())
                        and not result.is_symlink() and _digest(result) == saved["sha256"]):
                    validate_target_a3m(result, sequence)
                    return str(result), warnings
            except (OSError, ValueError, KeyError, TypeError):
                pass
        work = Path(tempfile.mkdtemp(prefix="attempt-", dir=root))
        fasta = work / "query.fasta"
        seq_utils.write_fasta({"query": sequence}, fasta)
        write_json(work / "request.json", {"identity": identity, "reusable": reusable})
        database = identity["database"]
        if backend == "colabfold_search":
            output = work / "output"
            output.mkdir()
            commands = [[str(binary), "--db-load-mode", "2", "-s", str(identity["sensitivity"]),
                         "--threads", str(identity["threads"]), str(fasta), database, str(output)]]
            # ColabFold names this from the FASTA header, not the input filename.
            produced = output / "query.a3m"
        else:
            qdb, alndb, msa_db, unpacked = (work / name for name in ("qdb", "alndb", "msaDB", "unpacked"))
            commands = [
                [str(binary), "createdb", str(fasta), str(qdb), "-v", "0"],
                [str(binary), "search", str(qdb), database, str(alndb), str(work / "tmp"),
                 "--alignment-mode", "3", "--threads", str(identity["threads"]),
                 "-s", str(identity["sensitivity"]), "--max-seqs", str(identity["max_seqs"]), "-v", "0"],
                [str(binary), "result2msa", str(qdb), database, str(alndb), str(msa_db), "--msa-format-mode", "6", "-v", "0"],
                [str(binary), "unpackdb", str(msa_db), str(unpacked), "--unpack-suffix", ".a3m", "-v", "0"],
            ]
            produced = unpacked / "0.a3m"
        try:
            for argv in commands:
                shell.run(argv, log_path=work / "search.log")
        except shell.ToolError as exc:
            warnings.append(f"ColabFold search 실패 → msa=empty 로 진행: {exc}" if backend == "colabfold_search"
                            else f"MMseqs MSA 생성 실패 → msa=empty 로 진행: {exc}")
            return empty_msa_token(), warnings
        if not produced.is_file():
            warnings.append("ColabFold search 결과 a3m을 찾지 못해 msa=empty 로 진행합니다." if backend == "colabfold_search"
                            else "MMseqs 결과 a3m을 찾지 못해 msa=empty 로 진행합니다.")
            return empty_msa_token(), warnings
        validate_target_a3m(produced, sequence)
        if identity != _search_identity(sequence, cfg):
            raise ValueError("MSA 검색 중 입력이 변경되었습니다. 새 분석으로 제출하세요. / MSA search inputs changed during execution; submit a new analysis.")
        result = work / "alignment.a3m"
        write_bytes(result, produced.read_bytes(), root=root)
        receipt = {"identity": identity, "reusable": reusable, "result": str(result.relative_to(root)), "sha256": _digest(result)}
        write_json(work / "receipt.json", receipt, root=root)
        write_json(root / "latest.json", receipt, root=root)
        return str(result), warnings


def validate_target_a3m(path: str | Path, sequence: str) -> None:
    """Validate legacy target alignments without requiring a nonquery homolog.

    Query-only alignments remain supported by this legacy path. Exact query,
    canonical metadata and every aligned row are checked before any reuse or
    promotion; a filename hash alone is never evidence of input identity.
    """
    from ..colabfold_msa import _aligned, _query, _records

    expected = _query(seq_utils.clean_sequence(sequence))
    metadata, records = _records(path)
    if _query(records[0][1]) != expected:
        raise ValueError("A3M first query sequence differs from requested target sequence")
    if metadata is not None and metadata != ([len(expected)], [1]):
        raise ValueError("Target A3M metadata must contain the exact one-chain length/cardinality")
    for _, row in records:
        # Insertion gaps are supported by this legacy A3M reader.
        aligned = _aligned(row.replace(".", ""))
        if len(aligned) != len(expected):
            raise ValueError("Target A3M aligned row width differs from query length")


def _clean_a3m_line(line: str) -> str:
    """a3m 열 정렬 규칙: 소문자 삽입부와 '.'(삽입 갭)은 제거, '-'(정렬 결손)은 보존."""
    return re.sub(r"[a-z.]", "", line)


def read_a3m(path: str | Path) -> tuple[str, list[str]]:
    """a3m 파서 (F12).

    - 소문자 삽입부와 '.' 삽입 갭은 제거한다(공식 ESM 리더와 동일).
    - '-' 정렬 결손은 열 정렬 유지를 위해 보존한다.
    - 여러 줄에 걸친 시퀀스 레코드를 올바르게 이어붙인다(이전 구현은 query 첫 줄만 유지).
    """
    records: list[tuple[str, str]] = []
    current_header: str | None = None
    current_chunks: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_header is not None:
                records.append((current_header, _clean_a3m_line("".join(current_chunks))))
            current_header = line[1:]
            current_chunks = []
        else:
            if current_header is None:
                continue
            current_chunks.append(line)
    if current_header is not None:
        records.append((current_header, _clean_a3m_line("".join(current_chunks))))
    if not records:
        return "", []
    query = records[0][1]
    sequences = [sequence for _, sequence in records[1:] if sequence]
    return query, sequences


def column_conservation(path: str | Path, query: str | None = None) -> list[float]:
    """쿼리 서열 기준 열별 보존도(0-1). a3m 갭('-','.')은 비보존으로 집계한다.

    read_a3m 이 갭을 보존하므로 열 인덱스가 쿼리 위치와 일치한다.
    """
    query_seq, sequences = read_a3m(path)
    if not sequences:
        return []
    query = query or query_seq
    length = len(query)
    conservation: list[float] = []
    for column in range(length):
        aa_query = query[column]
        conserved = sum(1 for s in sequences if column < len(s) and s[column] == aa_query)
        conservation.append(round(conserved / len(sequences), 3))
    return conservation
