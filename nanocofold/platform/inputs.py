from __future__ import annotations

import json
import hashlib
import re
import secrets
import shutil
import threading
import warnings
from pathlib import Path
from functools import wraps

import yaml

from ..config import _deep_merge, _validate, cfg_get, cfg_set, load_config, read_config_yaml
from ..utils.seq import clean_sequence, validate_candidate_id, validate_candidate_ids, validate_fasta_controls, validate_fasta_id
from ..wizard import WizardSession
from ..utils.residue_mapping import write_residue_mapping
from .paths import contained, native, sha256
from .regions import apply_regions

AA = set("ACDEFGHIKLMNPQRSTVWY")
INPUT_PATHS = ("target.pdb", "target.fasta", "screen.vhh_fasta", "mature.wt_fasta", "mature.wt_structure",
               "complex.reference_path", "input_review.reference_path", "input_review.reference_structure_path")
RUNTIME_PATHS = (
    "design.env", "design.framework", "design.boltzgen.env", "design.boltzgen.cache_dir", "design.boltzgen.scaffold",
    "screen.env", "screen.boltz2_env", "screen.chai1.env", "screen.chai1.msa_directory",
    "screen.colabfold.env", "screen.colabfold.data_dir", "screen.colabfold.database_manifest",
    "screen.opendde.env", "screen.opendde.data_dir",
    "structure.boltz2.env", "structure.chai1.env", "structure.msa.database_manifest", "structure.msa.mmseqs_binary",
    "mature.anarci.env", "mature.esm.env", "mature.antifold.env", "mature.antifold.model_path", "score.ipsae.official_script",
    "structure.msa.db", "structure.msa.colabfold_search",
)


def parse_fasta(content: str, *, single: bool = False, candidate_ids: bool = True) -> list[tuple[str, str]]:
    """Accept a FASTA record or an explicitly supported raw amino-acid paste.

    The browser assigns raw pasted sequence the neutral identifier ``sequence``;
    callers record that assignment in the review. File-based CLI FASTA parsing
    continues to require a header. ``single`` only controls the record count;
    reference-only consumers explicitly opt out of candidate pathname rules.
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("FASTA 서열을 입력하세요.")
    if len(content.encode()) > 20 * 1024 * 1024:
        raise ValueError("FASTA는 20MB 이하여야 합니다.")
    content = content.lstrip("\ufeff")
    validate_fasta_controls(content)
    nonempty = [line.strip() for line in content.splitlines() if line.strip()]
    if any(line.startswith(">") for line in nonempty) and not nonempty[0].startswith(">"):
        raise ValueError("FASTA 첫 서열 앞에 >ID 헤더가 필요합니다. 제목 없는 붙여넣기와 FASTA를 섞지 마세요.")
    entries = []
    name, sequence = None, []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if name is not None:
                entries.append((name, "".join(sequence)))
            name = line[1:].strip().split()[0] if line[1:].strip() else ""
            sequence = []
        else:
            if name is None:
                name = "sequence"
            sequence.append(re.sub(r"\s+", "", line))
    if name is not None:
        entries.append((name, "".join(sequence)))
    if not entries or len(entries) > 10000 or (single and len(entries) != 1):
        raise ValueError("표적·WT는 서열 하나를, 라이브러리는 1–10,000개를 입력하세요.")
    normalized = []
    for name, sequence in entries:
        # Validate original characters before Unicode upper() can expand them
        # into apparently valid amino acids (e.g. ß -> SS).
        sequence_clean = clean_sequence(sequence)
        if len(sequence_clean) > 2000:
            raise ValueError("개별 입력 서열은 2,000잔기 이하여야 합니다. 전체 VHH 또는 WT 서열과 입력 종류를 확인하세요.")
        # Both entry points reject residue-number digits instead of dropping them.
        if not name or not sequence_clean or len(sequence_clean) != len(sequence) or set(sequence_clean) - AA:
            raise ValueError("서열 이름과 표준 아미노산 20종으로 구성된 서열을 확인하세요.")
        (validate_candidate_id if candidate_ids else validate_fasta_id)(name)
        normalized.append((name, sequence_clean))
    if sum(len(sequence) for _, sequence in normalized) > 2_000_000:
        raise ValueError("라이브러리 입력은 합계 2,000,000잔기까지 지원합니다. 여러 분석으로 나누어 입력하세요.")
    if candidate_ids:
        validate_candidate_ids(name for name, _ in normalized)
    return normalized


def normalized_config(workspace: Path, override: dict | None = None) -> dict:
    if override is not None and not isinstance(override, dict):
        raise ValueError("설정은 객체여야 합니다.")
    workspace_default = workspace / "config/default.yaml"
    base = load_config(workspace_default if workspace_default.exists() else None)
    def check_shape(defaults, values, path=""):
        for key, value in values.items():
            expected = defaults.get(key)
            if isinstance(expected, dict):
                if not isinstance(value, dict):
                    raise ValueError(f"{path + key} 설정은 객체여야 합니다.")
                check_shape(expected, value, path + key + ".")
            elif isinstance(expected, bool) and not isinstance(value, bool):
                raise ValueError(f"{path + key} 설정은 true/false여야 합니다.")
    environment_path = workspace / "config/environment.yaml"
    if environment_path.exists():
        environment = read_config_yaml(environment_path.read_text()) or {}
        if not isinstance(environment, dict):
            raise ValueError("작업 공간 environment.yaml은 객체여야 합니다.")
        check_shape(base, environment)
        base = _deep_merge(base, environment)
    check_shape(base, override or {})
    def check_environment_overrides(values, path=()):
        for key, value in values.items():
            current = (*path, key)
            if key == "env_vars":
                if not isinstance(value, dict) or any(not isinstance(k, str) or (v is not None and not isinstance(v, (str, int, float, bool))) for k, v in value.items()):
                    raise ValueError("env_vars 설정은 환경변수 이름과 단순 값의 객체여야 합니다.")
                trusted = cfg_get(base, ".".join(current), {}) or {}
                if any(k not in trusted or trusted[k] != v for k, v in value.items()):
                    raise ValueError("실행 환경변수는 서버 관리자가 등록한 값만 사용할 수 있습니다. 작업 공간 environment.yaml에 등록한 설정을 선택하세요.")
            elif isinstance(value, dict):
                check_environment_overrides(value, current)
    check_environment_overrides(override or {})
    # Administrators register external tool/model locations in the trusted
    # workspace configuration. A request may reuse those exact locations;
    # a newly supplied executable, weights or framework must stay contained.
    for dotted in RUNTIME_PATHS:
        value = cfg_get(override or {}, dotted)
        if value is not None and value != cfg_get(base, dotted):
            if not isinstance(value, str):
                raise ValueError(f"{dotted} 경로는 문자열이어야 합니다.")
            contained(workspace, value)
    cfg = _deep_merge(base, override or {})
    _validate(cfg)
    backend = cfg_get(cfg, "structure.msa.backend", "empty")
    if not isinstance(backend, str) or backend not in {"empty", "cache", "mmseqs", "colabfold_search"}:
        raise ValueError("표적 구조의 MSA 방식은 empty/cache/mmseqs/colabfold_search 중 하나여야 합니다. 등록된 방식으로 입력을 다시 검토하세요.")
    if cfg.get("specificity", {}).get("enabled"):
        raise ValueError("현재 플랫폼 작업은 design/screen/mature 분석을 지원합니다.")
    cfg.setdefault("specificity", {})["enabled"] = False
    cfg["run"]["outdir"] = str(workspace / "runs")
    # Normalize *every* registered runtime location, including optional scripts
    # and weight paths. Relative values must not depend on the server cwd.
    for dotted in RUNTIME_PATHS:
        value = cfg_get(cfg, dotted)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{dotted} 경로는 비어 있지 않은 문자열 또는 null이어야 합니다.")
            if not Path(value).is_absolute():
                cfg_set(cfg, dotted, str(workspace / value))
    # Runtime inputs always refer to this workspace, independent of the server cwd.
    for dotted in INPUT_PATHS:
        section, key = dotted.split(".")
        value = cfg.get(section, {}).get(key)
        if value:
            cfg[section][key] = str(contained(workspace, value, exists=True))
    for section, key in (("design", "env"), ("screen", "env"), ("screen", "boltz2_env")):
        if cfg.get(section, {}).get(key) and not Path(cfg[section][key]).is_absolute():
            cfg[section][key] = str(workspace / cfg[section][key])
    for section in ("structure", "mature"):
        for values in cfg.get(section, {}).values():
            if isinstance(values, dict) and values.get("env") and not Path(values["env"]).is_absolute():
                values["env"] = str(workspace / values["env"])
    colabfold = cfg.get("screen", {}).get("colabfold", {})
    for key in ("env", "data_dir"):
        value = colabfold.get(key)
        if value and not Path(value).is_absolute():
            colabfold[key] = str(workspace / value)
    opendde = cfg.get("screen", {}).get("opendde", {})
    for key in ("env", "data_dir"):
        value = opendde.get(key)
        if value and not Path(value).is_absolute():
            opendde[key] = str(workspace / value)
    for key in ("msa_cache_dir", "msa_dir"):
        value = colabfold.get(key)
        if value:
            colabfold[key] = str(contained(workspace, value))
    boltzgen = cfg.get("design", {}).get("boltzgen", {})
    for key in ("env", "cache_dir", "scaffold"):
        value = boltzgen.get(key)
        if value and not Path(value).is_absolute():
            boltzgen[key] = str(workspace / value)
    value = cfg.get("design", {}).get("framework")
    if value and not Path(value).is_absolute():
        cfg["design"]["framework"] = str(workspace / value)
    msa = cfg.get("structure", {}).get("msa", {})
    if msa.get("cache_dir"):
        msa["cache_dir"] = str(contained(workspace, msa["cache_dir"]))
    return cfg


def normalized_fasta_config(workspace: Path, payload: dict) -> dict:
    """Validate the reviewed mode/hotspots, rather than a stale imported draft."""
    supplied = payload.get("config")
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("설정은 객체여야 합니다.")
    override = _deep_merge({}, supplied or {})
    # Do not replace malformed sections with valid reviewed fields and conceal
    # a schema error. Work on a copy, preserving the submitted manifest.
    for section in ("run", "target"):
        if section in override and not isinstance(override[section], dict):
            raise ValueError(f"{section} 설정은 객체여야 합니다.")
    if "mode" in payload:
        override.setdefault("run", {})["mode"] = payload["mode"]
    if "hotspots" in payload:
        override.setdefault("target", {})["hotspots"] = payload["hotspots"]
    return normalized_config(workspace, override)


def preserve_failed_review(method):
    """Quarantine only new, unpublished input copies with an explicit failure receipt."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            before = {p.name for p in self.root.iterdir() if p.is_dir()}
            try:
                return method(self, *args, **kwargs)
            except Exception as exc:
                from .errors import public_error
                for folder in list(self.root.iterdir()):
                    if (not folder.is_dir() or folder.is_symlink() or folder.name in before
                            or folder.name == "failed_reviews" or (folder / "platform_input.json").exists()):
                        continue
                    try:
                        files = [{"path": p.relative_to(folder).as_posix(), "sha256": sha256(p)}
                                 for p in sorted(folder.rglob("*")) if p.is_file() and not p.is_symlink()]
                        receipt = {"schema_version": 1, "state": "review_failed", "input_id": folder.name,
                                   "operation": method.__name__, "error": public_error(exc),
                                   "ready_for_execution": False, "original_input_preserved": True, "files": files}
                        (folder / "failed_review.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
                        destination = self.root / "failed_reviews"
                        destination.mkdir(exist_ok=True)
                        folder.rename(destination / folder.name)
                    except OSError:
                        warnings.warn("실패한 입력 검토 사본을 보존 폴더로 옮기지 못했습니다. 원래 사본을 유지합니다.", RuntimeWarning)
                raise
    return wrapped


class InputManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = workspace / "inputs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.wizard = WizardSession(self.root)
        self.lock = threading.RLock()

    def close(self):
        self.wizard.close()

    def list(self):
        return [self.get(path.parent.name) for path in sorted(self.root.glob("*/platform_input.json"))]

    def get(self, identifier: str):
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError("잘못된 입력 ID입니다.")
        path = contained(self.root, identifier, exists=True) / "platform_input.json"
        return json.loads(path.read_text())

    @staticmethod
    def _digest(value):
        return hashlib.sha256(json.dumps(native(value), ensure_ascii=False, sort_keys=True,
                                        allow_nan=False).encode()).hexdigest()

    def _review_files(self, folder: Path, cfg: dict):
        files = {}
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                raise ValueError("검토한 입력 파일은 심볼릭 링크일 수 없습니다.")
            if path.is_file() and path.name not in {"config.yaml", "platform_input.json"}:
                files["input/" + str(path.relative_to(folder))] = sha256(path)
        # Imported libraries/references can be outside this input folder but
        # still inside the workspace; bind their exact reviewed bytes too.
        for dotted in INPUT_PATHS:
            section, key = dotted.split(".")
            value = cfg.get(section, {}).get(key)
            if value:
                files[dotted] = sha256(contained(self.workspace, value, exists=True))
        return files

    def reviewed(self, identifier: str):
        """Load the stored review unchanged; old inputs require a new review."""
        result = self.get(identifier)
        folder = contained(self.root, identifier, exists=True)
        receipt = result.get("review_receipt")
        error = "검토한 입력과 설정이 달라졌거나 검토 영수증이 없습니다. 입력을 다시 검토하세요."
        if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
            raise ValueError(error)
        try:
            unchanged = (receipt.get("config_sha256") == self._digest(result["config"])
                         and receipt.get("config_file_sha256") == sha256(folder / "config.yaml")
                         and receipt.get("review_sha256") == self._digest(result["review"])
                         and receipt.get("files") == self._review_files(folder, result["config"]))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(error) from exc
        if not unchanged:
            raise ValueError(error)
        _validate(result["config"])
        return result

    def _save(self, folder: Path, cfg: dict, review: dict):
        with warnings.catch_warnings(record=True) as config_warnings:
            warnings.simplefilter("always", UserWarning)
            cfg = normalized_config(self.workspace, cfg)
        if config_warnings:
            review["warnings"] = list(dict.fromkeys([*review.get("warnings", []),
                                                    *(str(item.message) for item in config_warnings)]))
        from ..score_complex import iptm_availability_warnings
        from ..utils.seq import nucleotide_ambiguity_hint, read_fasta
        advisories = iptm_availability_warnings(cfg)
        if cfg.get("screen", {}).get("vhh_fasta"):
            library = read_fasta(cfg["screen"]["vhh_fasta"])
            if cfg.get("screen", {}).get("comparison", {}).get("enabled"):
                from ..engine_comparison import validate_candidate_ids
                validate_candidate_ids(cfg, library)
            hints = [nucleotide_ambiguity_hint(seq) for seq in library.values()]
            if any(hints):
                advisories.append("VHH 라이브러리: " + next(hint for hint in hints if hint))
        hint = nucleotide_ambiguity_hint(cfg.get("mature", {}).get("wt_sequence") or "")
        if hint:
            advisories.append("WT VHH: " + hint)
        if cfg.get("run", {}).get("mode") == "complex":
            from ..complex import proteins
            for entry in proteins(cfg.get("complex", {}).get("proteins")):
                hint = nucleotide_ambiguity_hint(entry["sequence"])
                if hint:
                    advisories.append(entry["name"] + ": " + hint)
        elif cfg_get(cfg, "rank.weights.dev_flags", 0):
            source = cfg_get(cfg, "mature.cdr.source", "auto")
            env = cfg_get(cfg, "mature.anarci.env")
            configured = cfg_get(cfg, "mature.cdr.regions", {})
            missing_annotation = configured is None or isinstance(configured, dict) and not configured
            if source == "config" and not missing_annotation:
                from ..mature import validate_cdr_ranges
                ends = [bounds[1] for bounds in configured.values()
                        if isinstance(bounds, (tuple, list)) and len(bounds) == 2 and type(bounds[1]) is int] if isinstance(configured, dict) else []
                validate_cdr_ranges(configured, max(ends, default=0))
            if (source == "config" and missing_annotation or cfg_get(cfg, "run.mock", False)
                    or cfg_get(cfg, "run.dry_run", False)
                    or source != "config" and not (env and (Path(env) / "bin/python").is_file())):
                advisories.append(
                    "CDR 개발가능성 평가가 준비되지 않으면 종합 순위와 최종 선별을 보류합니다. "
                    "실제 분석은 envs/setup_antibody.sh로 ANARCI를 설치하거나 "
                    "mature.cdr.source=config와 검증한 1-based CDR 범위를 지정하세요. "
                    "mock/dry-run은 실제 CDR 평가를 수행하지 않으며 미평가를 위험 없음으로 처리하지 않습니다."
                )
        if advisories:
            review["warnings"] = list(dict.fromkeys([*review.get("warnings", []), *advisories]))
        (folder / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        result = {"id": folder.name, "input_id": folder.name, "config": cfg, "review": review,
                  "config_path": str(folder / "config.yaml")}
        result["review_receipt"] = {"schema_version": 1,
                                    "config_sha256": self._digest(cfg),
                                    "config_file_sha256": sha256(folder / "config.yaml"),
                                    "review_sha256": self._digest(review),
                                    "files": self._review_files(folder, cfg)}
        (folder / "platform_input.json").write_text(json.dumps(native(result), ensure_ascii=False, indent=2))
        return result

    def _sequences(self, folder: Path, cfg: dict, payload: dict):
        if "mock" in payload and not isinstance(payload["mock"], bool):
            raise ValueError("mock는 true/false여야 합니다.")
        mode = payload.get("mode", cfg["run"]["mode"])
        if mode not in {"design", "screen", "mature"}:
            raise ValueError("지원하지 않는 분석 모드입니다.")
        cfg["run"]["mode"] = mode
        if payload.get("vhh_fasta"):
            rows = parse_fasta(payload["vhh_fasta"])
            path = folder / "vhh.fasta"
            path.write_text("".join(f">{name}\n{seq}\n" for name, seq in rows))
            cfg["screen"]["vhh_fasta"] = str(path)
        if payload.get("wt_fasta"):
            _, seq = parse_fasta(payload["wt_fasta"], single=True, candidate_ids=False)[0]
            cfg["mature"]["wt_sequence"] = seq
            cfg["mature"]["wt_fasta"] = None
        if payload.get("wt_structure"):
            upload = payload["wt_structure"]
            if not isinstance(upload, dict):
                raise ValueError("WT 구조는 filename/content 객체여야 합니다.")
            filename, content = upload.get("filename", ""), upload.get("content", "")
            if not isinstance(filename, str):
                raise ValueError("WT 구조의 filename은 문자열이어야 합니다.")
            suffix = Path(filename).suffix.lower()
            if Path(filename).name != filename or suffix not in {".pdb", ".ent", ".cif", ".mmcif"}:
                raise ValueError("WT 구조는 PDB/mmCIF 파일이어야 합니다.")
            if not isinstance(content, str) or not content.strip() or len(content.encode()) > 20 * 1024 * 1024:
                raise ValueError("WT 구조는 비어 있지 않은 20MB 이하 파일이어야 합니다.")
            from ..structure_input import inspect_structure
            source = folder / ("wt_source" + suffix)
            source.write_text(content)
            info = inspect_structure(source, folder / "wt_normalized", source={"kind": "upload", "filename": filename}, model_index=upload.get("model_index"))
            if info.get("blockers"):
                raise ValueError("WT 구조 입력 문제: " + "; ".join(info["blockers"]))
            cfg["mature"]["wt_structure"] = info["structure_path"]
            cfg["mature"]["wt_structure_review"] = {"chains": info["chains"], "sha256": sha256(Path(info["structure_path"])),
                                                       "warnings": info.get("warnings", []), "source": filename, "model_selection": info["source"]}
            (folder / "wt_structure_review.json").write_text(json.dumps(native(info), ensure_ascii=False, indent=2))
        if mode == "screen" and not cfg["screen"].get("vhh_fasta") and not cfg["run"].get("mock"):
            raise ValueError("screen 모드에는 VHH 라이브러리 FASTA가 필요합니다.")
        if mode == "mature" and not (cfg["mature"].get("wt_sequence") or cfg["mature"].get("wt_fasta")):
            raise ValueError("mature 모드에는 WT VHH 서열이 필요합니다.")

    @preserve_failed_review
    def complex(self, payload: dict):
        from ..complex import proteins, validate_settings
        mock = payload.get("mock", False)
        if type(mock) is not bool:
            raise ValueError("mock는 true/false여야 합니다.")
        entries = proteins(payload.get("proteins"))
        settings = validate_settings(payload.get("settings", {}))
        if settings.get("primary_pair") and not set(settings["primary_pair"]) <= {p["id"] for p in entries}:
            raise ValueError("primary_pair가 실제 분석 체인에 없습니다.")
        anchor = payload.get("anchor_chain", "A")
        interest = payload.get("interest_positions", [])
        if not isinstance(anchor, str) or anchor not in {entry["id"] for entry in entries}:
            raise ValueError("anchor_chain은 실제 분석 체인 ID여야 합니다.")
        positions = {position for entry in entries for position in entry["original_positions"]}
        if (not isinstance(interest, list) or any(type(position) is not int or position not in positions for position in interest)
                or len(set(interest)) != len(interest)):
            raise ValueError("interest_positions는 실제 분석 구간의 중복 없는 원본 잔기 번호 정수 목록이어야 합니다.")
        folder = self.root / secrets.token_hex(16)
        folder.mkdir()
        cfg = {"run": {"mode": "complex", "mock": mock}, "target": {"sequence": entries[0]["sequence"], "fasta": None, "pdb": None},
               "complex": {**settings, "proteins": payload["proteins"], "reference_path": None,
                           "anchor_chain": anchor, "interest_positions": interest}}
        from ..complex_comparison import configure
        configure(cfg, settings)
        upload = payload.get("reference")
        if upload:
            if not isinstance(upload, dict):
                raise ValueError("비교 구조는 filename/content 객체여야 합니다.")
            if not isinstance(upload.get("filename"), str):
                raise ValueError("비교 구조의 filename은 문자열이어야 합니다.")
            suffix = Path(upload.get("filename", "")).suffix.lower()
            if suffix not in {".pdb", ".cif", ".mmcif"} or not isinstance(upload.get("content"), str) or len(upload["content"].encode()) > 20*1024*1024:
                raise ValueError("비교 구조는 20MB 이하 PDB/mmCIF이어야 합니다.")
            source = folder / ("reference"+suffix)
            source.write_text(upload["content"])
            from ..structure_input import inspect_structure
            inspected = inspect_structure(source, folder / "reference_review", source={"kind": "upload"}, model_index=upload.get("model_index"))
            if inspected.get("blockers") or not inspected.get("chains"):
                raise ValueError("비교 구조의 원자·잔기·체인을 확인하세요: " + "; ".join(inspected.get("blockers", [])))
            reference = source
            if upload.get("model_index") is not None:
                # Keep the original upload and author chain/residue IDs. The
                # normalized inspection PDB renames chains and cannot replace
                # a complex reference with explicit reference_chains mappings.
                from Bio.PDB import MMCIFIO
                from Bio.PDB.Structure import Structure
                from ..utils import pdb_io
                raw = pdb_io.load_structure(source)
                selected = Structure("selected_reference")
                selected.add(list(raw.get_models())[upload["model_index"] - 1].copy())
                reference = folder / "reference_selected.cif"
                writer = MMCIFIO()
                writer.set_structure(selected)
                writer.save(str(reference))
            cfg["complex"]["reference_path"] = str(reference)
            cfg["complex"]["reference_model_selection"] = inspected["source"]
        return self._save(folder, cfg, {"mode": "complex", "mock": mock, "proteins": entries, "settings": settings, "source": "researcher_review"})

    def load(self, payload: dict):
        with self.lock:
            return self.wizard.load(payload)

    def structure(self, identifier: str) -> str:
        """Serve the reviewed, normalized chains rather than reparsing the upload."""
        with self.lock:
            info = self.wizard.input
            if not info or identifier != info["input_id"]:
                raise ValueError("구조 입력이 변경되었습니다. 구조를 다시 불러오세요.")
            return Path(info["structure_path"]).read_text()

    def analyze(self, payload: dict):
        with self.lock:
            return self.wizard.analyze(payload)

    @preserve_failed_review
    def review(self, payload: dict):
        with self.lock:
            if payload.get("preparation") == "sequence_prediction":
                return self._prediction_review(payload)
            if payload.get("preparation", "coordinates") != "coordinates":
                raise ValueError("지원하지 않는 입력 준비 방식입니다.")
            exported = self.wizard.export(payload)
            folder = Path(exported["export_dir"])
            cfg = yaml.safe_load((folder / "config.yaml").read_text())
            # The standalone wizard writes project-local environment paths.
            # Platform reviews bind runtime locations to the workspace's
            # registered configuration instead. User overrides remain checked
            # below and cannot use this internal translation as an escape.
            for dotted in RUNTIME_PATHS:
                keys = dotted.split(".")
                node = cfg
                for key in keys[:-1]:
                    node = node.get(key, {}) if isinstance(node, dict) else {}
                if isinstance(node, dict):
                    node.pop(keys[-1], None)
            def remove_internal_environment(values):
                # Wizard defaults describe its checkout, not this registered
                # platform workspace. User overrides are merged afterwards and
                # still pass the administrator-registration check.
                if isinstance(values, dict):
                    values.pop("env_vars", None)
                    for child in values.values():
                        remove_internal_environment(child)
            remove_internal_environment(cfg)
            cfg = _deep_merge(cfg, payload.get("config", {}))
            cfg["run"]["mock"] = bool(payload.get("mock", cfg["run"].get("mock", False)))
            # Reviewed mapping/hotspots/context come from the verified wizard export.
            original = yaml.safe_load((folder / "config.yaml").read_text())
            for key in ("target", "input_review", "truncate"):
                cfg[key] = original[key]
            cfg = normalized_config(self.workspace, cfg)
            self._sequences(folder, cfg, payload)
            review = json.loads((folder / "input_review.json").read_text())
            cfg, review = apply_regions(folder, cfg, review, payload)
            return self._save(folder, cfg, review)

    def _prediction_review(self, payload: dict):
        from ..protein_input import MAX_PREDICTION_RESIDUES, STANDARD_COMPONENTS, prediction_chain

        selection = self.wizard.selection
        if not selection or payload.get("selection_id") != selection["result"]["selection_id"]:
            raise ValueError("선택 내용이 바뀌었습니다. 표적과 서열을 다시 확인하세요.")
        if payload.get("acknowledge") is not True:
            raise ValueError("전체 서열·수정 잔기·예측 경로와 경고를 확인하세요.")
        selected = selection["result"]["retained_chains"]
        if len(selected) > 25:
            raise ValueError("후속 예측은 표적·파트너 합계 25개 체인까지 지원합니다.")
        provided = dict(parse_fasta(payload["prediction_sequences"])) if payload.get("prediction_sequences") else {}
        if set(provided) - {chain["id"] for chain in selected}:
            raise ValueError("전체 서열 FASTA 이름은 선택한 체인 ID와 같아야 합니다(예: >A).")
        chains = [prediction_chain(chain, provided.get(chain["id"])) for chain in selected]
        if sum(len(chain["sequence"]) for chain in chains) > MAX_PREDICTION_RESIDUES:
            raise ValueError(f"전체 서열 예측 입력은 합계 {MAX_PREDICTION_RESIDUES}잔기까지 지원합니다.")
        provider = payload.get("provider", "boltz2")
        if provider not in {"boltz2", "chai1"}:
            raise ValueError("전체 서열 구조 예측 엔진은 Boltz-2 또는 Chai-1이어야 합니다.")
        mock = payload.get("mock", False)
        if not isinstance(mock, bool):
            raise ValueError("mock는 true/false여야 합니다.")
        prediction_hotspots = payload.get("prediction_hotspots", "auto")
        hotspots = "auto"
        if prediction_hotspots == "reference":
            positions = payload.get("hotspots")
            allowed = chains[0]["observed_to_full"]
            if (not isinstance(positions, list) or not 1 <= len(positions) <= 12
                    or any(type(p) is not int or str(p) not in allowed for p in positions)
                    or len(set(positions)) != len(positions)):
                raise ValueError("선택한 결합 부위의 원본·전체 서열 대응이 불명확합니다. 예측 구조 자동 결합 부위를 사용하거나 대응을 확인하세요.")
            hotspots = [f"A:seq{allowed[str(p)]}" for p in sorted(positions)]
        elif prediction_hotspots == "none":
            if payload.get("hotspots", []) != []:
                raise ValueError("결합 부위 미지정 실행의 hotspot 목록은 비어 있어야 합니다.")
            hotspots = []
        elif prediction_hotspots != "auto":
            raise ValueError("예측 결합 부위는 auto, reference 또는 none을 선택하세요.")
        identifier = "sequence-" + secrets.token_hex(8)
        folder = self.root / identifier
        folder.mkdir()
        source = Path(self.wizard.input["source_file"])
        reference = folder / ("source.cif.gz" if source.name.endswith(".cif.gz") else "source" + source.suffix)
        shutil.copy2(source, reference)
        reference_structure = folder / "reference.pdb"
        shutil.copy2(selection["protein"], reference_structure)
        warnings = list(selection["result"]["warnings"])
        warnings.extend([
            "전체 서열로 새 단백질 구조를 예측합니다. 원본 좌표는 참조로만 보존하며 미관측 좌표를 채운 실험 구조로 표시하지 않습니다.",
            "원본의 수정 잔기·chromophore·당질·리간드·핵산·막 환경은 이 단백질 서열 예측에 재현되지 않습니다.",
            "결합 부위는 예측 구조에서 다시 계산합니다." if hotspots == "auto" else
            "결합 부위 미지정: 결합 위치를 제한하지 않고 탐색합니다." if hotspots == [] else
            "선택한 원본 결합 부위를 전체 서열 위치로 대응했습니다. 노출도와 접촉은 예측 구조에서 다시 평가합니다.",
        ])
        for original, chain in zip(selected, chains):
            if len(chain["observed_to_full"]) < len(original["sequence"]):
                warnings.append(f"체인 {original['original_id']}: 일부 관측 잔기의 전체 서열 위치가 불명확합니다. 원본 번호·결합 부위를 임의 대응하지 않았습니다.")
        for chain in selected:
            if chain.get("blockers"):
                warnings.append(f"체인 {chain['original_id']}: 원본 좌표 경로의 문제: " + "; ".join(chain["blockers"]))
        mapping = []
        for index, (original, chain) in enumerate(zip(selected, chains)):
            output_chain = chr(65 + index)
            inverse = {pos: row for row in original["residues"]
                       if (pos := chain["observed_to_full"].get(str(row["position"]))) is not None}
            components = {}
            for component in chain["sequence_review"].get("polymer_components", []):
                span = chain["template_spans"].get(component["label_position"])
                if span:
                    for position in range(span[0], span[1] + 1):
                        components.setdefault(position, []).append((component, span))
            for position, aa in enumerate(chain["sequence"], 1):
                observed = inverse.get(position)
                source_components = components.get(position, [])
                component, span = source_components[0] if len(source_components) == 1 else (None, None)
                kind = ("composite_component" if span and span[0] != span[1] else
                        "parent_component" if component and component["component"] not in STANDARD_COMPONENTS else
                        "standard_residue" if observed else "ambiguous_component" if source_components else
                        "unmapped_or_unobserved")
                mapping.append({"protein": chain["name"], "source_chain": chain["original_id"],
                                "author_chain": chain["author_chain"],
                                "source_residue": component["original_label"] if component else observed["original_label"] if observed else "",
                                "source_component": component["component"] if component else "",
                                "aa": aa, "output_chain": output_chain, "output_position": position,
                                "source_observed": bool(observed or source_components),
                                "source_standard_observed": observed is not None,
                                "source_mapping_kind": kind,
                                "coordinate_source": "predicted", "sequence_origin": chain["sequence_origin"]})
        write_residue_mapping(folder / "residue_mapping.csv", mapping)
        cfg = normalized_config(self.workspace, payload.get("config"))
        cfg["run"].update(name=cfg["run"].get("name", chains[0]["name"]), mock=mock)
        cfg["target"].update(name=chains[0]["name"], sequence=chains[0]["sequence"], fasta=None,
                              pdb=None, chain="A", hotspots=hotspots, hotspot_set=None,
                              aux_chains=[{"name": c["name"], "sequence": c["sequence"]} for c in chains[1:]])
        cfg["structure"]["provider"] = "mock" if mock else provider
        cfg["truncate"]["enabled"] = False
        # Coordinate faults trigger the prediction path; chemical/context
        # limitations on automatic design remain separately recorded.
        design_blockers = list(dict.fromkeys(b for c in selected for b in c.get("design_blockers", [])))
        cfg["input_review"] = {
            "preparation": "sequence_prediction", "reference_path": str(reference),
            "reference_sha256": sha256(reference), "reference_structure_path": str(reference_structure),
            "reference_structure_sha256": sha256(reference_structure),
            "target_name": chains[0]["name"], "target_sequence": chains[0]["sequence"],
            "context_names": [c["name"] for c in chains[1:]],
            "context_sequences": [c["sequence"] for c in chains[1:]],
            "prediction_hotspots": hotspots, "provider": cfg["structure"]["provider"],
            "design_blockers": design_blockers,
        }
        self._sequences(folder, cfg, payload)
        manifest = {"schema_version": 2, "preparation": "sequence_prediction", "source": self.wizard.input["source"],
                    "source_sha256": sha256(reference), "selection_id": selection["result"]["selection_id"],
                    "chains": chains, "reference_chains": selected,
                    "removed_chains": selection["result"]["removed_chains"],
                    "removed_components": self.wizard.input.get("removed", []),
                    "warnings": list(dict.fromkeys(warnings)), "predictions_executed": False,
                    "prediction_hotspots": hotspots, "design_blockers": design_blockers,
                    "reference_structure_sha256": sha256(reference_structure)}
        (folder / "input_review.json").write_text(json.dumps(native(manifest), ensure_ascii=False, indent=2))
        (folder / "target.fasta").write_text("".join(f">{chr(65+i)}\n{c['sequence']}\n" for i, c in enumerate(chains)))
        cfg, manifest = apply_regions(folder, cfg, manifest, payload)
        return self._save(folder, cfg, manifest)

    @preserve_failed_review
    def fasta(self, payload: dict):
        rows = parse_fasta(payload.get("content", ""), single=True, candidate_ids=False)
        from ..protein_input import MAX_PREDICTION_RESIDUES
        if len(rows[0][1]) > MAX_PREDICTION_RESIDUES:
            raise ValueError(f"표적 예측 입력은 {MAX_PREDICTION_RESIDUES}잔기까지 지원합니다. 전체 서열 길이를 확인하세요.")
        cfg = normalized_fasta_config(self.workspace, payload)
        mock_mode = payload.get("mock", cfg["run"].get("mock", False))
        if type(mock_mode) is not bool:
            raise ValueError("mock는 true/false여야 합니다.")
        provider = payload.get("provider", cfg.get("structure", {}).get("provider", "boltz2"))
        if not isinstance(provider, str) or (not mock_mode and provider not in {"boltz2", "chai1"}):
            raise ValueError("서열 입력은 Boltz-2 또는 Chai-1로 표적 구조를 먼저 예측해야 합니다.")
        msa_backend = payload.get("msa_backend", cfg_get(cfg, "structure.msa.backend", "empty"))
        if not isinstance(msa_backend, str) or msa_backend not in {"empty", "cache", "mmseqs", "colabfold_search"}:
            raise ValueError("지원하지 않는 표적 MSA 방식입니다. empty/cache/mmseqs/colabfold_search 중 등록된 방식을 선택하세요.")
        identifier = "fasta-" + secrets.token_hex(8)
        folder = self.root / identifier
        folder.mkdir()
        name, sequence = rows[0]
        cfg["run"].update(name=payload.get("name", name), mock=mock_mode)
        cfg["target"].update(name=payload.get("target_name", name), sequence=sequence, fasta=None,
                              pdb=None, chain="A", aux_chains=[], hotspots=payload.get("hotspots", "auto"))
        cfg["structure"]["provider"] = "mock" if cfg["run"]["mock"] else provider
        if not cfg["run"]["mock"] and cfg["structure"]["provider"] not in {"boltz2", "chai1"}:
            raise ValueError("서열 입력은 Boltz-2 또는 Chai-1로 표적 구조를 먼저 예측해야 합니다.")
        cfg["structure"]["msa"]["backend"] = msa_backend
        if cfg["structure"]["msa"]["backend"] not in {"empty", "cache", "mmseqs", "colabfold_search"}:
            raise ValueError("지원하지 않는 표적 MSA 방식입니다. empty/cache/mmseqs/colabfold_search 중 등록된 방식을 선택하세요.")
        cfg["truncate"]["enabled"] = False
        (folder / "target.fasta").write_text(f">{name}\n{sequence}\n")
        self._sequences(folder, cfg, payload)
        raw_paste = not any(line.strip().lstrip("\ufeff").startswith(">") for line in payload.get("content", "").splitlines())
        from ..utils.seq import nucleotide_ambiguity_hint
        sequence_warnings = ["서열 입력의 구조와 결합 부위는 예측됩니다. 실험 구조 확인과 구분하세요."]
        if raw_paste:
            sequence_warnings.append("FASTA 제목 없는 아미노산 붙여넣기입니다. 저장할 서열 이름을 sequence로 지정했습니다.")
        nucleotide_hint = nucleotide_ambiguity_hint(sequence)
        if nucleotide_hint:
            sequence_warnings.append(nucleotide_hint)
        review = {"source": {"kind": "raw_amino_acid_paste" if raw_paste else "fasta",
                              "assigned_sequence_id": name if raw_paste else None,
                              "sha256": sha256(folder / "target.fasta")},
                  "target_sequence": sequence, "warnings": sequence_warnings,
                  "predictions_executed": False}
        cfg, review = apply_regions(folder, cfg, review, payload)
        return self._save(folder, cfg, review)
