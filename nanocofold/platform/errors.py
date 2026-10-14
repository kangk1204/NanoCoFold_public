"""Actionable public errors; parser details and private paths stay in logs."""
import re

_GENERIC = "입력 처리에 실패했습니다. 파일 형식·서열·필수 항목을 확인하고 다시 시도하세요. 자세한 내용은 서버 로그에서 확인할 수 있습니다."
# Actual path roots, not every slash in .pdb/.cif or true/false prose.
# Consume a path-bearing line tail so account/path spaces cannot leak fragments.
_PRIVATE_PATH = re.compile(r"(?<![\w./])(?:/(?:home|Users|mnt|tmp|var|opt|private|Volumes|nfs|gpfs)(?=/|$)|/(?=[A-Za-z0-9_.-]+/[^\s/]+)|file://|[A-Za-z]:[\\/]|\\\\[^\\\s]+\\)[^\r\n]*")
_SOURCE_LINE = re.compile(r"^\s{2,}(?:[A-Za-z_]\w*[\[(]|return\s|raise\s|if\s|elif\s|else:|for\s|with\s|assert\s)")

def public_error(exc):
    message = str(exc).strip()
    if isinstance(exc, KeyError) and len(message) > 1 and message[0] == message[-1] == "'":
        message = message[1:-1]
    if message.startswith("PDB 파싱 실패") or "defined twice" in message or "_atom_site." in message:
        return "구조 파일의 원자·잔기 레코드가 중복되거나 손상되었습니다. 원본 파일을 정리한 뒤 다시 불러오세요."
    if ("좌표" in message or "B-factor" in message or "B factor" in message) and ("NaN" in message or "Inf" in message):
        return "유효하지 않은 좌표(NaN/Inf)가 있습니다. 원본 구조 파일의 좌표를 확인하고 다시 불러오세요."
    if "MSA consent" in message or ("public_api" in message and any(key in message for key in ("allow_public_msa_server", "allow_public_server", "consent", "동의"))) or "allow_public_server" in message and "consent" in message.lower():
        return "공용 MSA 서버로 현재 서열을 보내는 동의가 필요합니다. 실행 조건에서 전송 동의를 확인하거나 등록된 로컬 MSA·캐시 방식을 선택하세요."
    if "invalid literal for int()" in message or "could not convert string to float" in message:
        return "숫자 입력 형식이 올바르지 않습니다. 잔기 번호·모델 수·설정값을 확인하세요."
    if not re.search(r"[가-힣]", message):
        return _GENERIC
    message = _PRIVATE_PATH.sub("[서버 경로]", message)
    message = re.sub(r"\s*\((?:Atom |Residue |invalid literal |could not convert)[^\n]*", "", message)
    message = re.sub(r"<Residue[^>]*>|<Atom[^>]*>", "[구조 레코드]", message)
    return message or _GENERIC


def public_log(text):
    """Share progress without path-bearing or traceback lines."""
    lines = []
    for line in str(text).splitlines():
        if _PRIVATE_PATH.search(line) or _SOURCE_LINE.search(line) or line.lstrip().startswith(("Traceback ", 'File "')):
            lines.append("[서버 로그 상세는 로컬 로그 파일에 보존됩니다.]")
        else:
            lines.append(line)
    result = "\n".join(lines)
    return re.sub(r"(?:MIG-)?GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", "[GPU 식별자]", result)
