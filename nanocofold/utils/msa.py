from __future__ import annotations

import hashlib
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

    if target.exists():
        validate_target_a3m(target, sequence)
        return str(target), warnings

    if backend == "cache":
        warnings.append(
            f"캐시된 MSA가 없습니다: {target}. 서열만 입력으로 단일서열 모드(msa=empty)로 진행합니다. "
            "정확도를 높이려면 structure.msa.backend=colabfold_search 로 MSA를 먼저 생성하세요."
        )
        return empty_msa_token(), warnings

    if backend == "empty":
        return empty_msa_token(), warnings

    if backend == "mmseqs":
        return _ensure_a3m_mmseqs(sequence, target, msa_cfg, warnings)

    if backend == "colabfold_search":
        executable = (msa_cfg or {}).get("colabfold_search")
        if not executable:
            warnings.append("structure.msa.colabfold_search 경로가 지정되지 않아 msa=empty 로 진행합니다.")
            return empty_msa_token(), warnings
        db = (msa_cfg or {}).get("db")
        if not db:
            warnings.append("structure.msa.db(UniRef30)가 지정되지 않아 msa=empty 로 진행합니다.")
            return empty_msa_token(), warnings
        fasta = cache_dir / f"{sequence_hash(sequence)}.fasta"
        seq_utils.write_fasta({"query": seq_utils.clean_sequence(sequence)}, fasta)
        argv = [executable, "--db-load-mode", "2", "-s", "0.5", str(fasta), str(db), str(cache_dir)]
        try:
            shell.run(argv, log_path=cache_dir / f"{sequence_hash(sequence)}.msa.log")
        except shell.ToolError as exc:
            warnings.append(f"ColabFold search 실패 → msa=empty 로 진행: {exc}")
            return empty_msa_token(), warnings
        produced = sorted(cache_dir.glob(f"{sequence_hash(sequence)}*.a3m"))
        if not produced:
            warnings.append("ColabFold search 결과 a3m을 찾지 못해 msa=empty 로 진행합니다.")
            return empty_msa_token(), warnings
        validate_target_a3m(produced[0], sequence)
        produced[0].rename(target)
        return str(target), warnings

    raise ValueError(f"지원하지 않는 MSA backend: {backend}. 허용값: cache, mmseqs, colabfold_search, empty.")


def _ensure_a3m_mmseqs(
    sequence: str,
    target: Path,
    msa_cfg: dict,
    warnings: list[str],
) -> tuple[str, list[str]]:
    """MMseqs2(GPL) 로컬 DB 검색으로 a3m을 생성한다 (상업적 이용 안전, 네트워크 불필요)."""
    binary = (msa_cfg or {}).get("mmseqs_binary")
    db = (msa_cfg or {}).get("db")
    if not binary or not Path(str(binary)).exists():
        warnings.append("structure.msa.mmseqs_binary 가 없어 msa=empty 로 진행합니다 (tools/mmseqs).")
        return empty_msa_token(), warnings
    if not db:
        warnings.append("structure.msa.db (예: data/msa_db/uniref50_db)가 없어 msa=empty 로 진행합니다.")
        return empty_msa_token(), warnings

    work_dir = target.parent / f"{target.stem}_mmseqs"
    unpacked = work_dir / "unpacked"
    if unpacked.exists():
        existing = sorted(unpacked.glob("*.a3m"))
        if existing:
            validate_target_a3m(existing[0], sequence)
            return str(existing[0]), warnings

    work_dir.mkdir(parents=True, exist_ok=True)
    query_fasta = work_dir / "query.fasta"
    seq_utils.write_fasta({"query": seq_utils.clean_sequence(sequence)}, query_fasta)
    qdb = work_dir / "qdb"
    alndb = work_dir / "alndb"
    msa_db = work_dir / "msaDB"
    tmp_dir = work_dir / "tmp"
    threads = str(int((msa_cfg or {}).get("threads", 8)))
    sensitivity = str((msa_cfg or {}).get("sensitivity", 5.7))
    max_seqs = str(int((msa_cfg or {}).get("max_seqs", 300)))
    log_path = work_dir / "mmseqs.log"

    commands = [
        [str(binary), "createdb", str(query_fasta), str(qdb), "-v", "0"],
        [str(binary), "search", str(qdb), str(db), str(alndb), str(tmp_dir),
         "--alignment-mode", "3", "--threads", threads,
         "-s", sensitivity, "--max-seqs", max_seqs, "-v", "0"],
        [str(binary), "result2msa", str(qdb), str(db), str(alndb), str(msa_db),
         "--msa-format-mode", "6", "-v", "0"],
        [str(binary), "unpackdb", str(msa_db), str(unpacked), "--unpack-suffix", ".a3m", "-v", "0"],
    ]
    try:
        for argv in commands:
            shell.run(argv, log_path=log_path)
    except shell.ToolError as exc:
        warnings.append(f"MMseqs MSA 생성 실패 → msa=empty 로 진행: {exc}")
        return empty_msa_token(), warnings

    produced = sorted(unpacked.glob("*.a3m"))
    if not produced:
        warnings.append("MMseqs 결과 a3m을 찾지 못해 msa=empty 로 진행합니다.")
        return empty_msa_token(), warnings
    validate_target_a3m(produced[0], sequence)
    produced[0].rename(target)
    return str(target), warnings


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
