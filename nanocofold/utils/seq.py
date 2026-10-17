from __future__ import annotations

import re
import hashlib
import unicodedata
from pathlib import Path

from Bio.SeqUtils.IsoelectricPoint import IsoelectricPoint
from Bio.SeqUtils.ProtParam import ProteinAnalysis

AA1 = "ACDEFGHIKLMNPQRSTVWY"
AA3TO1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
    "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
    "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V", "MSE": "M",
    "SEC": "C", "PYL": "K",
}
AA1TO3 = {
    "A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS", "Q": "GLN", "E": "GLU",
    "G": "GLY", "H": "HIS", "I": "ILE", "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE",
    "P": "PRO", "S": "SER", "T": "THR", "W": "TRP", "Y": "TYR", "V": "VAL",
}

KYTE_DOOLITTLE = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5, "G": -0.4,
    "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8, "P": -1.6, "S": -0.8,
    "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
}

ECOLI_CODONS = {
    "A": "GCG", "R": "CGT", "N": "AAT", "D": "GAT", "C": "TGT", "Q": "CAG", "E": "GAA",
    "G": "GGC", "H": "CAT", "I": "ATT", "L": "CTG", "K": "AAA", "M": "ATG", "F": "TTT",
    "P": "CCG", "S": "AGC", "T": "ACC", "W": "TGG", "Y": "TAT", "V": "GTG",
}

HUMAN_CODONS = {
    "A": "GCC", "R": "AGG", "N": "AAC", "D": "GAC", "C": "TGC", "Q": "CAG", "E": "GAG",
    "G": "GGC", "H": "CAC", "I": "ATC", "L": "CTG", "K": "AAG", "M": "ATG", "F": "TTC",
    "P": "CCC", "S": "AGC", "T": "ACC", "W": "TGG", "Y": "TAC", "V": "GTG",
}

ORGANISM_CODONS = {
    "ecoli": ECOLI_CODONS,
    "human": HUMAN_CODONS,
    "mammalian": HUMAN_CODONS,
}



def read_fasta(path: str | Path, *, candidate_ids: bool = True) -> dict[str, str]:
    """간단 FASTA 파서 (id → sequence, 첫 토큰을 id로 사용).

    중복 ID와 빈 ID는 조용히 덮어쓰지 않고 오류로 처리한다(F17).
    """
    records: dict[str, str] = {}
    folded_ids: set[str] = set()
    name: str | None = None
    chunks: list[str] = []
    def finish():
        if name is None:
            return
        if candidate_id_key(name) in folded_ids:
            raise ValueError(f"FASTA에 중복 ID가 있습니다: '{name}' (서로 다른 후보가 덮어써질 수 있음)")
        if not chunks:
            raise ValueError(f"FASTA 서열이 비어 있습니다: '{name}'")
        records[name] = "".join(chunks)
        folded_ids.add(candidate_id_key(name))
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"FASTA 파일을 UTF-8로 다시 저장한 뒤 입력하세요: {path}") from exc
    validate_fasta_controls(text)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            finish()
            tokens = line[1:].split()
            if not tokens:
                raise ValueError("FASTA 헤더에 ID가 없습니다.")
            name = validate_candidate_id(tokens[0]) if candidate_ids else validate_fasta_id(tokens[0])
            chunks = []
        else:
            if name is None:
                raise ValueError("FASTA 서열 앞에 >ID 헤더가 필요합니다.")
            chunks.append(line)
    finish()
    if not records:
        raise ValueError("FASTA 파일에 서열이 없습니다.")
    return records


def write_fasta(records: dict[str, str] | list[tuple[str, str]], path: str | Path, width: int = 60) -> None:
    items = records.items() if isinstance(records, dict) else records
    lines: list[str] = []
    for name, seq in items:
        lines.append(f">{name}")
        lines.extend(seq[i : i + width] for i in range(0, len(seq), width))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_fasta_id(identifier: str) -> str:
    """Reference FASTA headers are labels, never candidate output paths."""
    if not isinstance(identifier, str) or not identifier or any(
            ch.isspace() or unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in identifier):
        raise ValueError("FASTA 머리글 ID는 비어 있거나 숨은 줄바꿈·제어 문자를 포함할 수 없습니다.")
    return identifier


def validate_candidate_id(candidate_id: str) -> str:
    """후보 ID를 파일 경로 성분으로 안전하게 사용할 수 있는지 검증한다(N06)."""
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in str(candidate_id)):
        raise ValueError("후보 ID에 제어 문자를 사용할 수 없습니다.")
    text = str(candidate_id).strip()
    # Leave room for engine/seed prefixes and filenames on common filesystems.
    if len(text.encode("utf-8")) > 120:
        raise ValueError("후보 ID는 UTF-8 기준 120바이트 이하여야 합니다. 짧은 이름으로 바꿔 입력하세요.")
    if not text or text in (".", ".."):
        raise ValueError(f"후보 ID가 유효하지 않습니다: {candidate_id!r}")
    if text.startswith(("=", "+", "-", "@")):
        raise ValueError("후보 ID는 스프레드시트 수식 시작 문자(= + - @)로 시작할 수 없습니다. ID를 바꿔 입력하세요.")
    if any(sep in text for sep in ("/", "\\")) or Path(text).is_absolute() or ".." in Path(text).parts:
        raise ValueError(f"후보 ID에 경로 문자를 사용할 수 없습니다: {candidate_id!r}")
    return text


def validate_candidate_ids(candidate_ids) -> list[str]:
    """Reject Unicode/case equivalents while preserving the original spelling."""
    identifiers = [validate_candidate_id(value) for value in candidate_ids]
    folded = [candidate_id_key(value) for value in identifiers]
    if len(set(folded)) != len(folded):
        raise ValueError("후보 ID는 유니코드 정규화·대소문자를 무시해도 서로 달라야 합니다.")
    return identifiers


def candidate_id_key(identifier):
    """Filesystem identity only; never rewrite a scientific identifier."""
    return unicodedata.normalize("NFKC", identifier).casefold()


def validate_fasta_controls(text):
    """Reject hidden delimiters before splitlines can silently split an ID."""
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} and ch not in "\n\r\t" for ch in text):
        raise ValueError("FASTA에 숨은 제어·서식 문자를 사용할 수 없습니다. 일반 줄바꿈과 탭만 사용하세요.")


def candidate_id_aliases(candidate_ids, *, preserve_ascii=True):
    """Validate the whole ID set before deriving collision-checked ASCII aliases."""
    originals = [str(value) for value in candidate_ids]
    checked = validate_candidate_ids(originals)
    if checked != originals:
        raise ValueError("후보 ID 앞뒤의 공백을 제거한 뒤 다시 입력하세요.")
    aliases = {}
    for identifier in originals:
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        aliases[identifier] = (identifier if preserve_ascii and re.fullmatch(r"[A-Za-z0-9_.-]+", identifier)
                               and not identifier.startswith("ncf_") else "ncf_" + digest)
    if len({value.casefold() for value in aliases.values()}) != len(aliases):
        raise ValueError("후보 ID의 안전 별칭이 중복됩니다. 서로 다른 ID를 사용하세요.")
    return aliases


def clean_sequence(seq: str, strict: bool = True) -> str:
    """서열 정규화. strict=True면 표준 20개 아미노산 외 문자를 조용히 지우지 않고 오류를 낸다(F17)."""
    # Unicode uppercase expansion can turn invalid letters into valid residues.
    if strict and any(not ch.isascii() and not ch.isspace() for ch in seq):
        raise ValueError("서열은 ASCII 표준 아미노산 문자로 입력하세요.")
    if strict and any(ch.isdigit() for ch in seq):
        raise ValueError("서열에 잔기 번호나 숫자를 넣을 수 없습니다. 표준 아미노산 문자만 입력하세요.")
    if not strict:
        seq = "".join(ch for ch in seq if ch.isascii())
    stripped = re.sub(r"[\s\d]", "", seq.upper())
    invalid = sorted({ch for ch in stripped if ch not in AA1})
    if invalid and strict:
        raise ValueError(
            f"서열에 허용되지 않는 문자가 있습니다: {invalid}. "
            "(표준 20개 아미노산만 허용; 좌표가 조용히 어긋나는 것을 방지)"
        )
    return re.sub(r"[^ACDEFGHIKLMNPQRSTVWY]", "", stripped)


def nucleotide_ambiguity_hint(sequence: str) -> str | None:
    """Non-destructive heuristic: A/C/G/T are also valid amino-acid letters."""
    compact = "".join(sequence.split()).upper()
    if len(compact) >= 30 and set(compact) <= set("ACGTN") and sum(compact.count(ch) for ch in "ACGT") / len(compact) >= 0.8:
        return ("서열이 A/C/G/T와 일부 N 문자로 이루어져 DNA 서열일 가능성이 있습니다. "
                "단백질 아미노산 서열인지 확인하세요. 이 문자들은 아미노산으로도 유효하므로 "
                "서열을 삭제하거나 임의 번역하지 않았습니다.")
    return None


def molecular_weight(seq: str) -> float:
    return round(ProteinAnalysis(seq).molecular_weight() / 1000.0, 2)


def isoelectric_point(seq: str) -> float:
    return round(IsoelectricPoint(seq).pi(), 2)


def net_charge(seq: str, ph: float = 7.4) -> float:
    return round(ProteinAnalysis(seq).charge_at_pH(ph), 2)


def gravy(seq: str) -> float:
    return round(ProteinAnalysis(seq).gravy(), 3)


def hydrophobic_patches(seq: str, window: int = 5, threshold: float = 1.6) -> list[tuple[int, int, float]]:
    """슬라이딩 윈도우 평균 Kyte-Doolittle이 임계값을 넘는 구간 (start, end, max_mean)."""
    hits: list[tuple[int, int, float]] = []
    if len(seq) < window:
        return hits
    means = [
        sum(KYTE_DOOLITTLE.get(aa, 0.0) for aa in seq[i : i + window]) / window
        for i in range(len(seq) - window + 1)
    ]
    start: int | None = None
    best = -99.0
    for i, mean in enumerate(means):
        if mean >= threshold:
            start = i if start is None else start
            best = max(best, mean)
        elif start is not None:
            hits.append((start + 1, i + window - 1, round(best, 2)))
            start, best = None, -99.0
    if start is not None:
        hits.append((start + 1, len(seq), round(best, 2)))
    return hits


def find_motif_hits(seq: str, patterns: dict[str, str]) -> list[dict]:
    hits: list[dict] = []
    for label, pattern in patterns.items():
        if not pattern:
            continue
        for match in re.finditer(f"(?=({pattern}))", seq):
            hits.append({"type": label, "motif": match.group(1), "pos": match.start() + 1})
    return hits


def codon_optimize(protein: str, organism: str = "ecoli") -> str:
    table = ORGANISM_CODONS.get(organism)
    if table is None:
        raise ValueError(f"지원하지 않는 organism: {organism} (ecoli|human)")
    codons: list[str] = []
    for aa in protein:
        codon = table.get(aa)
        if codon is None:
            raise ValueError(f"코돈 테이블에 없는 잔기: {aa}")
        codons.append(codon)
    return "".join(codons) + "TAA"


def suggest_construct(
    vhh: str,
    signal_peptide: str = "",
    tag: str = "",
    host: str = "ecoli_periplasmic",
    optimize: bool = False,
    organism: str = "ecoli",
) -> str:
    """발현 구성물을 만든다. optimize=True 이면 host와 무관하게 organism 코돈으로 DNA를 반환한다."""
    protein = f"{signal_peptide}{vhh}{tag}"
    if optimize:
        return codon_optimize(protein, organism)
    return protein


def cdr3_from_sequence(seq: str, cdr_ranges: dict[str, tuple[int, int]]) -> str:
    if "CDR3" not in cdr_ranges:
        return ""
    start, end = cdr_ranges["CDR3"]
    return seq[start - 1 : end]
