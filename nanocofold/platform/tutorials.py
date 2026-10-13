"""Read shipped public examples without submitting or calculating."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODES = {
    "complex": ("복합체 예측 · 처음 실행 추천", "barnase 110 aa + barstar 89 aa · Boltz-2 모델 1개"),
    "screen": ("보유 나노바디 비교", "IL-6 + 공개 VHH 1개 · 네 엔진 모델 4개"),
    "mature": ("기존 나노바디 변이 평가", "IL-6 + 원본 VHH · WT와 선별 변이의 모델 최대 12개"),
    "design": ("새 나노바디 설계", "IL-6 · RFantibody·BoltzGen 후보와 네 엔진 모델 최대 8개"),
}


def tutorial_root() -> Path:
    package = Path(__file__).resolve().parents[1]
    packaged = package / "_data/examples/tutorial"
    return packaged if packaged.is_dir() else package.parent / "examples/tutorial"


def load_public_example(mode: str, *, root: Path | None = None) -> dict:
    if mode not in MODES:
        raise ValueError("공개 예제는 complex, screen, mature, design 중에서 선택하세요.")
    root = Path(root) if root is not None else tutorial_root()
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files_sha256"].items():
        path = root / name
        if path.parent != root or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"공개 예제 파일이 변경되었습니다: {name}. 원본 release를 다시 받으세요.")
    plan = manifest["modes"][mode]
    filename = plan["file"]
    if filename != mode + "_batch.json":
        raise ValueError("공개 예제 파일 이름이 예상과 다릅니다.")
    payload = json.loads((root / filename).read_text())
    if payload["mode"] != mode or payload.get("api_consent") is not False:
        raise ValueError("예제 모드 또는 API 동의 기본값이 예상과 다릅니다.")
    return {"mode": mode, "title": MODES[mode][0], "description": MODES[mode][1],
            "payload": payload, "requires_api_consent": mode != "complex",
            "maximum_representative_models": plan["maximum_representative_models"],
            "predictions_executed": False}
