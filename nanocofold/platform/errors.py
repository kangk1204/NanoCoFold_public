"""Actionable public errors; parser details and private paths stay in logs."""
import re
from ..utils.presentation import portable_metadata

_GENERIC = "작업 처리에 실패했습니다. 실패한 단계와 서버 로그를 확인하세요. 원본 결과와 오류 기록은 보존됩니다."
# Actual path roots, not every slash in .pdb/.cif or true/false prose.
# Consume a path-bearing line tail so account/path spaces cannot leak fragments.
_PRIVATE_PATH = re.compile(r"(?<![\w./])(?:/(?:home|Users|mnt|tmp|var|opt|private|Volumes|nfs|gpfs)(?=/|$)|/(?=[A-Za-z0-9_.-]+/[^\s/]+)|file://|[A-Za-z]:[\\/]|\\\\[^\\\s]+\\)[^\r\n]*")
_QUOTED_PATH = re.compile(r'''(["'])((?:(?:/(?:home|Users|mnt|tmp|var|opt|private|Volumes|nfs|gpfs)(?=/)|file://|[A-Za-z]:[\\/]|\\\\)[^\r\n]*?|/(?!/)[^\r\n"'=()[\]{}]*?))\1''')
_ABSOLUTE_FILE = re.compile(r'''(?<![\w./:])/(?:[^\r\n/"'<>()[\]{};,`|:]+/)*[^\r\n/"'<>()[\]{};,`|:]+?\.[A-Za-z0-9]{1,12}(?=$|[\s"'<>()[\]{};,`|:])''')
_CUSTOM_DIRECTORY = re.compile(r'''(?<![\w./:])/(?:[^\r\n/"'<>()[\]{};,`|:]+/)+[^\s"'<>()[\]{};,`|:]+''')
_SOURCE_LINE = re.compile(r"^\s{2,}(?:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*[\[(]|return\s|raise\s|if\s|elif\s|else:|for\s|with\s|assert\s)")


def _redact_paths(message):
    # Quotes delimit the complete path, including its final spaced component.
    # Finite filenames then preserve following actions. Extensionless unquoted
    # roots have no reliable end: withhold their tail before token masking can
    # discard the root and leave private account/path fragments behind.
    message = _QUOTED_PATH.sub(lambda match: match[1] + "[서버 경로]" + match[1], message)
    message = _ABSOLUTE_FILE.sub("[서버 경로]", message)
    message = _PRIVATE_PATH.sub("[서버 경로]", message)
    message = _CUSTOM_DIRECTORY.sub("[서버 경로]", message)
    return portable_metadata(message).replace("[local path]", "[서버 경로]")

def public_error(exc, *, stage="operation", candidate_id=None):
    message = str(exc).strip()
    if isinstance(exc, KeyError) and len(message) > 1 and message[0] == message[-1] == "'":
        message = message[1:-1]
    if candidate_id is None:
        prefix = re.match(r"^([^\s/\\]{1,120}):\s+", message)
        if prefix and not prefix[1].endswith(("Error", "Exception")):
            candidate_id, message = prefix[1], message[prefix.end():]
    rendered = _public_message(exc, message, stage)
    return (str(candidate_id) + ": " if candidate_id is not None else "") + rendered


def _public_message(exc, message, stage):
    if message.startswith("PDB 파싱 실패") or "defined twice" in message or "_atom_site." in message:
        return "구조 파일의 원자·잔기 레코드가 중복되거나 손상되었습니다. 원본 파일을 정리한 뒤 다시 불러오세요."
    if ("좌표" in message or "B-factor" in message or "B factor" in message) and ("NaN" in message or "Inf" in message):
        return "유효하지 않은 좌표(NaN/Inf)가 있습니다. 원본 구조 파일의 좌표를 확인하고 다시 불러오세요."
    if "MSA consent" in message or ("public_api" in message and any(key in message for key in ("allow_public_msa_server", "allow_public_server", "consent", "동의"))) or "allow_public_server" in message and "consent" in message.lower():
        return "공용 MSA 서버로 현재 서열을 보내는 동의가 필요합니다. 실행 조건에서 전송 동의를 확인하거나 등록된 로컬 MSA·캐시 방식을 선택하세요."
    if "invalid literal for int()" in message or "could not convert string to float" in message:
        return "숫자 입력 형식이 올바르지 않습니다. 잔기 번호·모델 수·설정값을 확인하세요."
    if not re.search(r"[가-힣]", message):
        if isinstance(exc, FileNotFoundError) or "[Errno 2]" in message or "No such file or directory" in message:
            return {"prediction": "예측 산출물을 찾지 못했습니다. 엔진 실행 상태와 작업 로그를 확인하세요.",
                    "export": "내보낼 산출물을 찾지 못했습니다. 저장된 결과와 내보내기 로그를 확인하세요.",
                    "input": "입력 파일을 찾지 못했습니다. 선택한 파일을 확인하세요."}.get(stage,
                    "작업에 필요한 파일을 찾지 못했습니다. 실패한 단계와 서버 로그를 확인하세요.")
        return {"prediction": "예측·평가 처리에 실패했습니다. 해당 후보의 엔진 실행 상태와 작업 로그를 확인하세요.",
                "export": "결과 내보내기에 실패했습니다. 저장된 산출물과 내보내기 로그를 확인하세요.",
                "input": "입력 처리에 실패했습니다. 파일 형식·서열·필수 항목과 서버 로그를 확인하세요."}.get(stage, _GENERIC)
    message = _redact_paths(message)
    message = re.sub(r"\s*\((?:Atom |Residue |invalid literal |could not convert)[^\n]*", "", message)
    message = re.sub(r"<Residue[^>]*>|<Atom[^>]*>", "[구조 레코드]", message)
    return message or _GENERIC


def public_log(text):
    """Share progress without path-bearing or traceback lines."""
    lines = []
    for line in str(text).splitlines():
        if _SOURCE_LINE.search(line) or line.lstrip().startswith(("Traceback ", 'File "')):
            lines.append("[서버 로그 상세는 로컬 로그 파일에 보존됩니다.]")
        elif re.search(r"[가-힣]", line):
            lines.append(public_error(ValueError(line)))
        elif (_PRIVATE_PATH.search(line) or _ABSOLUTE_FILE.search(line)
              or _CUSTOM_DIRECTORY.search(line) or portable_metadata(line) != line):
            lines.append(_redact_paths(line))
        else:
            lines.append(line)
    result = "\n".join(lines)
    return re.sub(r"(?:MIG-)?GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", "[GPU 식별자]", result)


class PublicIssues(list):
    """Sanitize presentation issues; original artifacts retain their details."""
    def append(self, value):
        super().append(public_error(ValueError(value), stage="prediction"))

    def extend(self, values):
        for value in values:
            self.append(value)


def public_error_data(value):
    """Copy error-bearing presentation fields without changing scientific data."""
    if isinstance(value, list):
        return [public_error_data(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and (key == "error" or key.endswith("_error")) and isinstance(item, str) and item:
                # Safe recorded failure labels remain exact in result exports.
                # Apply exception abstraction when the stored diagnostic carries
                # server paths or parser/traceback internals.
                private = (_PRIVATE_PATH.search(item) or _ABSOLUTE_FILE.search(item)
                           or _CUSTOM_DIRECTORY.search(item) or "_atom_site." in item
                           or "defined twice" in item or "Traceback " in item)
                result[key] = public_error(ValueError(item), stage="prediction" if key == "prediction_error" else "operation") if private else item
            elif key == "issues" and isinstance(item, list):
                result[key] = [public_error(ValueError(issue)) for issue in item]
            else:
                result[key] = public_error_data(item)
        return result
    return value
