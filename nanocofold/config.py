from __future__ import annotations

import copy
import datetime as _dt
import json
import math
import os
import re
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


class ConfigLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently replacing reviewed settings."""


def _unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ValueError("YAML 설정 키는 단일 이름이어야 합니다.") from exc
        if duplicate:
            raise ValueError(f"YAML 설정 키가 중복되었습니다: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


ConfigLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def read_config_yaml(content):
    return yaml.load(content, Loader=ConfigLoader)

def _resolve_project_root() -> Path:
    """사용자 작업 root(상대 입력/출력 기준). 패키지 내부 자산은 절대 반환하지 않는다(H11)."""
    env_root = os.environ.get("NANOCOFOLD_ROOT")
    if env_root and (Path(env_root) / "config" / "default.yaml").exists():
        return Path(env_root).resolve()
    cwd = Path.cwd()
    if (cwd / "config" / "default.yaml").exists():
        return cwd.resolve()
    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "config" / "default.yaml").exists():
        return source_root
    return cwd.resolve()


def _resolve_asset_root(project_root: Path) -> Path:
    """기본 config·라이선스·정적 자산의 위치(소스 체크아웃 또는 패키지 _data)."""
    if (project_root / "config" / "default.yaml").exists():
        return project_root
    packaged = Path(__file__).resolve().parent / "_data"
    if (packaged / "config" / "default.yaml").exists():
        return packaged
    return project_root


PROJECT_ROOT = _resolve_project_root()
ASSET_ROOT = _resolve_asset_root(PROJECT_ROOT)
DEFAULT_CONFIG_PATH = ASSET_ROOT / "config" / "default.yaml"

def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _set_dotted(cfg: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    node = cfg
    for key in keys[:-1]:
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise ValueError(f"설정 경로 충돌: {dotted}")
    node[keys[-1]] = value


def _parse_scalar(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def exploration_config() -> dict:
    """Canonical browser/CLI exploration preset, including installed wheels."""
    root = Path(__file__).resolve().parents[1]
    path = root / "config/researcher_exploration.yaml"
    if not path.exists():
        path = Path(__file__).resolve().parent / "_data/config/researcher_exploration.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _external_viewer_url(cfg: dict) -> str | None:
    """A saved historical default URL is not consent to an external viewer."""
    allowed = cfg_get(cfg, "report.allow_external_viewer", False)
    if type(allowed) is not bool:
        raise ValueError("report.allow_external_viewer는 true/false여야 합니다.")
    if not allowed:
        return None
    external = cfg_get(cfg, "report.viewer_cdn")
    if not isinstance(external, str):
        raise ValueError("외부 뷰어를 허용하려면 report.viewer_cdn에 HTTPS URL을 명시하세요.")
    try:
        url = urlsplit(external)
    except ValueError as exc:
        raise ValueError("report.viewer_cdn은 사용자 정보가 없는 HTTPS URL이어야 합니다.") from exc
    if (external != external.strip() or url.scheme != "https" or not url.hostname
            or url.username or url.password):
        raise ValueError("report.viewer_cdn은 사용자 정보가 없는 HTTPS URL이어야 합니다.")
    return external


def load_config(path: str | Path | None = None, overrides: list[str] | None = None) -> dict:
    """default.yaml 위에 사용자 config와 CLI 오버라이드(a.b=c)를 병합한다."""
    # A partial substitute changes the scientific/runtime policy silently when a
    # wheel is damaged. User overrides cannot repair a missing installation asset.
    try:
        base = read_config_yaml(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(
            f"기본 설치 설정을 불러올 수 없습니다: {DEFAULT_CONFIG_PATH}. "
            "NanoCoFold 설치를 복구하거나 다시 설치하세요."
        ) from exc
    if not isinstance(base, dict) or not base:
        raise ValueError(
            f"기본 설치 설정은 비어 있지 않은 YAML 객체여야 합니다: {DEFAULT_CONFIG_PATH}. "
            "NanoCoFold 설치를 복구하거나 다시 설치하세요."
        )

    environment = PROJECT_ROOT / "config" / "environment.yaml"
    if environment.exists():
        installed = read_config_yaml(environment.read_text(encoding="utf-8")) or {}
        if not isinstance(installed, dict):
            raise ValueError("설치 환경 설정은 YAML 객체여야 합니다.")
        base = _deep_merge(base, installed)

    if path:
        user = read_config_yaml(Path(path).read_text(encoding="utf-8"))
        if user is None:
            user = {}
        if not isinstance(user, dict):
            raise ValueError("분석 설정은 YAML 객체여야 합니다.")
        cfg = _deep_merge(base, user)
    else:
        cfg = copy.deepcopy(base)

    for item in overrides or []:
        if "=" not in item:
            raise ValueError(f"--set 형식은 key=value 입니다: {item}")
        key, value = item.split("=", 1)
        _set_dotted(cfg, key.strip(), _parse_scalar(value.strip()))

    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    from .utils.tool_args import BOLTZ_MANAGED, CHAI_MANAGED, validate_extra_args, validate_colabfold_extra_args

    _validate_scalar_shapes(cfg)

    _external_viewer_url(cfg)
    for dotted in ("run", "target", "structure", "design", "screen", "mature", "score", "rank",
                   "score.thresholds", "score.ipsae", "rank.weights", "rank.cdr3"):
        if not isinstance(cfg_get(cfg, dotted, {}), dict):
            raise ValueError(f"{dotted} 설정은 객체여야 합니다.")
    for dotted in ("run.seed", "structure.boltz2.seed", "score.boltz2.seed", "mature.library.seed"):
        cfg_seed(cfg, dotted)
    for dotted in ("run.mock", "run.dry_run", "run.resume", "rank.fallback_to_ineligible",
                   "rank.include_wt_control", "score.ipsae.use_official"):
        if type(cfg_get(cfg, dotted, False)) is not bool:
            raise ValueError(f"{dotted} 설정은 true/false여야 합니다.")
    for metric, weight in cfg_get(cfg, "rank.weights", {}).items():
        if (not isinstance(metric, str) or not metric or isinstance(weight, bool)
                or not isinstance(weight, (int, float)) or not math.isfinite(weight)):
            raise ValueError("rank.weights는 지표 이름과 유한한 숫자 가중치의 객체여야 합니다.")
    bounds = {"ipsae": (0, 1), "ipsae_gray": (0, 1), "iptm": (0, 1),
              "hotspot_contact_frac": (0, 1), "plddt_interface": (0, 100),
              "max_clashes": (0, None)}
    thresholds = cfg_get(cfg, "score.thresholds", {})
    for key, (low, high) in bounds.items():
        if key not in thresholds:
            continue
        value = thresholds[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or value < low or (high is not None and value > high)):
            raise ValueError(f"score.thresholds.{key} 값의 범위를 확인하세요.")
    if thresholds.get("ipsae_gray", .4) > thresholds.get("ipsae", .6):
        raise ValueError("score.thresholds.ipsae_gray는 ipsae 이하이어야 합니다.")
    for dotted in ("score.contact_cutoff", "score.hbond_cutoff", "score.salt_bridge_cutoff",
                   "score.clash_cutoff", "score.interface_pae_cutoff", "score.ipsae.dist_cutoff"):
        value = cfg_get(cfg, dotted, 1)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{dotted} 값은 0보다 큰 유한한 숫자여야 합니다.")

    if cfg_get(cfg, "screen.engine") == "opendde" or cfg_get(cfg, "complex.engine") == "opendde":
        from .opendde import settings as opendde_settings
        opendde_settings(cfg)

    # Validate every stage before target preparation or candidate generation.
    # Adapter entry points also enforce this for programmatically mutated configs.
    for dotted, managed, abbreviated in (("structure.boltz2.extra_args", BOLTZ_MANAGED, False),
                                          ("score.boltz2_extra_args", BOLTZ_MANAGED, False),
                                          ("structure.chai1.extra_args", CHAI_MANAGED, True),
                                          ("screen.extra_args", CHAI_MANAGED, True)):
        validate_extra_args(cfg_get(cfg, dotted), setting=dotted, managed=managed, allow_abbrev=abbreviated)
    validate_colabfold_extra_args(cfg_get(cfg, "screen.colabfold.extra_args"))
    if cfg_get(cfg, "structure.provider") == "chai1":
        from .target_prep import chai_target_settings
        chai_target_settings(cfg)
    mode = cfg["run"].get("mode")
    if not isinstance(mode, str) or mode not in {"complex", "design", "screen", "mature"}:
        raise ValueError(f"run.mode 는 complex|design|screen|mature 중 하나여야 합니다: {mode}")
    if cfg["target"].get("fasta") and cfg["target"].get("sequence"):
        raise ValueError("target.sequence 와 target.fasta 를 동시에 지정할 수 없습니다.")
    if not isinstance(cfg.get("rank", {}).get("require_hotspot", False), bool):
        raise ValueError("rank.require_hotspot 은 true/false여야 합니다.")
    from .pose_assessment import settings as pose_settings
    pose_settings(cfg)
    severe = cfg_get(cfg, "rank.max_dev_severe", 1)
    if (isinstance(severe, bool) or not isinstance(severe, (int, float))
            or not math.isfinite(severe) or severe < 0):
        raise ValueError("rank.max_dev_severe 은 0 이상의 유한한 숫자여야 합니다.")
    for dotted in ("design.filters.enabled", "mature.prefilter.enforce_threshold"):
        if not isinstance(cfg_get(cfg, dotted, True), bool):
            raise ValueError(f"{dotted} 은 true/false여야 합니다.")
    design_engine = cfg_get(cfg, "design.engine", "rfantibody")
    if not isinstance(design_engine, str) or design_engine not in {"rfantibody", "boltzgen", "both"}:
        raise ValueError("design.engine must be rfantibody|boltzgen|both")
    hotspot_mode = cfg_get(cfg, "design.hotspot_mode", "none")
    if not isinstance(hotspot_mode, str) or hotspot_mode not in {"none", "specified"}:
        raise ValueError("design.hotspot_mode must be none|specified")
    if mode == "design" and hotspot_mode == "specified":
        hotspots = cfg_get(cfg, "target.hotspots")
        if not isinstance(hotspots, list) or not hotspots:
            raise ValueError("Specified design hotspots require an explicit nonempty target.hotspots list")
    refinement = cfg_get(cfg, "design.refinement", {})
    if not isinstance(refinement, dict) or type(refinement.get("enabled", False)) is not bool:
        raise ValueError("design.refinement.enabled must be true/false")
    if refinement.get("enabled", False):
        if mode != "design":
            raise ValueError("design.refinement requires design mode")
        for key, default, low, high in (("library_size", 1000, 2, 10000), ("parents_per_engine", 2, 1, 100),
                                       ("min_mutations", 1, 1, 50), ("max_mutations", 5, 1, 50), ("final_count", 100, 1, 2000)):
            value = refinement.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"design.refinement.{key} must be integer {low}..{high}")
        if refinement.get("min_mutations", 1) > refinement.get("max_mutations", 5):
            raise ValueError("design.refinement.min_mutations exceeds max_mutations")
        if refinement.get("final_count", 100) > refinement.get("library_size", 1000):
            raise ValueError("design.refinement.final_count exceeds library_size")
        if not cfg_get(cfg, "screen.cascade.enabled", False):
            raise ValueError("design.refinement requires the ColabFold→Boltz-2 cascade")
    if design_engine in {"boltzgen", "both"}:
        from .design_boltzgen import settings
        settings(cfg)
    count = cfg_get(cfg, "design.num_designs", 2)
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("design.num_designs must be integer 1..10000 per engine")
    library = cfg_get(cfg, "mature.library", {})
    if not isinstance(library, dict):
        raise ValueError("mature.library는 설정 객체여야 합니다.")
    strategy = library.get("strategy", "dms")
    if not isinstance(strategy, str) or strategy not in {"dms", "random"}:
        raise ValueError("mature.library.strategy는 dms/random 중 하나여야 합니다.")
    if strategy == "random":
        size = library.get("size", 10000)
        minimum, maximum = library.get("min_mutations", 1), library.get("max_mutations", 5)
        if isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 100000:
            raise ValueError("mature.library.size는 WT 포함 2–100000 사이의 정수여야 합니다.")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (minimum, maximum)) or not 1 <= minimum <= maximum <= 50:
            raise ValueError("mature.library 변이 개수는 1 ≤ min_mutations ≤ max_mutations ≤ 50이어야 합니다.")
        library_seed = library.get("seed")
        if library_seed is not None and (isinstance(library_seed, bool) or not isinstance(library_seed, int) or not 0 <= library_seed < 2**32):
            raise ValueError("mature.library.seed는 null 또는 0–4294967295 사이의 정수여야 합니다.")
        if cfg_get(cfg, "mature.prefilter.mode", "esm2") != "none":
            raise ValueError("random 다중 변이 라이브러리는 mature.prefilter.mode=none이 필요합니다.")
    from .engine_comparison import settings as comparison_settings
    msa_source = cfg_get(cfg, "screen.colabfold.msa_source", "cache")
    if not isinstance(msa_source, str) or msa_source not in {"cache", "local_mmseqs2", "public_mmseqs2", "public_api"}:
        raise ValueError("screen.colabfold.msa_source must be cache|local_mmseqs2|public_mmseqs2|public_api")
    consent = cfg_get(cfg, "screen.allow_public_msa_server", False)
    if type(consent) is not bool:
        raise ValueError("screen.allow_public_msa_server must be true/false")
    if msa_source == "public_api" and consent is not True:
        raise ValueError("public_api uploads target/generated sequences; screen.allow_public_msa_server=true is required")
    comparison = comparison_settings(cfg)
    if comparison["enabled"]:
        if mode not in {"design", "screen", "mature"} or cfg_get(cfg, "specificity.enabled", False):
            raise ValueError("Engine comparison requires design/screen/mature without specificity campaign")
        if cfg_get(cfg, "screen.cascade.enabled", False) or int(cfg_get(cfg, "screen.boltz2_rescore_top", 0) or 0):
            raise ValueError("Engine comparison cannot also enable cascade or Boltz-2 rescore; disable those explicitly")
        if refinement.get("enabled", False):
            raise ValueError("Direct engine comparison cannot also enable design.refinement")
        if "colabfold" in comparison["engines"]:
            from .colabfold import settings as colabfold_settings
            colabfold_settings(cfg)
    cascade = cfg_get(cfg, "screen.cascade", {})
    if not isinstance(cascade, dict):
        raise ValueError("screen.cascade 는 설정 객체여야 합니다.")
    if not isinstance(cascade.get("enabled", False), bool):
        raise ValueError("screen.cascade.enabled 은 true/false여야 합니다.")
    for key in ("prune_unselected", "calibrate_wt"):
        if type(cascade.get(key, False)) is not bool:
            raise ValueError(f"screen.cascade.{key} 는 true/false여야 합니다.")
    if cascade.get("prune_unselected", False) and (cascade.get("diversity_n", 0) or cascade.get("audit_n", 2)):
        raise ValueError("구조 삭제 실행은 diversity_n=audit_n=0이 필요합니다.")
    for key, default, minimum in (("top_n", 20, 1), ("diversity_n", 0, 0), ("audit_n", 2, 0)):
        value = cascade.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 10000:
            raise ValueError(f"screen.cascade.{key} 는 {minimum}–10000 사이의 정수여야 합니다.")
    metric = cascade.get("ranking_metric", "iptm")
    if not isinstance(metric, str) or metric not in {"iptm", "ipsae", "interface_plddt", "interface_pae", "n_clashes"}:
        raise ValueError("screen.cascade.ranking_metric 은 iptm|ipsae|interface_plddt|interface_pae|n_clashes 중 하나여야 합니다.")
    identity = cascade.get("diversity_identity", .85)
    if isinstance(identity, bool) or not isinstance(identity, (int, float)) or not 0 < identity <= 1:
        raise ValueError("screen.cascade.diversity_identity 는 0 초과 1 이하의 숫자여야 합니다.")
    seed = cascade.get("audit_seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32):
        raise ValueError("screen.cascade.audit_seed 는 null 또는 0–4294967295 사이의 정수여야 합니다.")
    if cascade.get("enabled", False):
        if mode not in {"screen", "mature"} and not (mode == "design" and refinement.get("enabled", False)):
            raise ValueError("ColabFold→Boltz-2 cascade requires screen/mature or enabled design.refinement")
        if cfg_get(cfg, "screen.engine") != "colabfold":
            raise ValueError("cascade 활성화에는 screen.engine=colabfold가 필요합니다.")
        if int(cfg_get(cfg, "screen.boltz2_rescore_top", 0) or 0) > 0:
            raise ValueError("cascade와 기존 screen.boltz2_rescore_top을 동시에 사용할 수 없습니다.")
    if cfg_get(cfg, "screen.engine") == "colabfold":
        from .colabfold import settings as colabfold_settings
        colabfold_settings(cfg)


def _validate_scalar_shapes(cfg):
    """Check downstream numeric/bool settings before any inference runs."""
    defaults = read_config_yaml(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    optional = {"run.seed", "screen.opendde.attention_chunk_size"}
    signed = {"mature.prefilter.threshold", "developability.hydrophobic_mean_kd",
              "developability.net_charge_min", "developability.net_charge_max"}
    def visit(default, current, prefix=""):
        if not isinstance(current, dict):
            raise ValueError(f"{prefix or '설정'}은 객체여야 합니다.")
        for key, expected in default.items():
            path = prefix + "." + key if prefix else key
            if key not in current:
                continue
            value = current[key]
            if isinstance(expected, dict):
                visit(expected, value, path)
            elif type(expected) is bool and type(value) is not bool:
                raise ValueError(f"{path}는 true/false여야 합니다.")
            elif type(expected) in {int, float}:
                if value is None and path in optional:
                    continue
                if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                        or (type(expected) is int and not float(value).is_integer())):
                    raise ValueError(f"{path}는 유한한 {'정수' if type(expected) is int else '숫자'}여야 합니다.")
                if value < 0 and path not in signed and not path.startswith("rank.weights."):
                    raise ValueError(f"{path}는 0 이상이어야 합니다.")
    visit(defaults, cfg)
    preset = cfg_get(cfg, "run.analysis_preset")
    if preset is not None and not isinstance(preset, str):
        raise ValueError("run.analysis_preset은 문자열 또는 null이어야 합니다.")
    chain = cfg_get(cfg, "target.chain")
    if chain is not None and (not isinstance(chain, str) or not chain.strip()):
        raise ValueError("target.chain은 체인 이름 문자열이어야 합니다. YAML에서 NO 같은 이름은 따옴표로 감싸세요.")
    hotspots = cfg_get(cfg, "target.hotspots")
    if hotspots is not None and not isinstance(hotspots, (str, list)):
        raise ValueError("target.hotspots는 문자열 또는 목록이어야 합니다. YAML에서 1:5 같은 표기는 따옴표로 감싸세요.")
    for path in ("rank.cluster_identity", "screen.cascade.diversity_identity", "hotspots.auto.min_relative_sasa"):
        value = cfg_get(cfg, path)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1):
            raise ValueError(f"{path}는 0–1 범위여야 합니다.")
    if cfg_get(cfg, "developability.pI_min", 5) > cfg_get(cfg, "developability.pI_max", 9.5):
        raise ValueError("developability.pI_min은 pI_max 이하이어야 합니다.")
    expression = cfg_get(cfg, "expression", {})
    if expression:
        if not isinstance(expression, dict):
            raise ValueError("expression은 객체여야 합니다.")
        codons = expression.get("organism_codons", "ecoli")
        if not isinstance(codons, str) or codons not in {"ecoli", "human"}:
            raise ValueError("expression.organism_codons는 ecoli/human 중 하나여야 합니다.")
        for key in ("tag", "signal_peptide"):
            value = expression.get(key, "")
            if value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]*", value):
                raise ValueError(f"expression.{key}는 대문자 아미노산 서열이어야 합니다.")
    # Warn about typo-shaped keys in fixed scientific sections. Dynamic metric
    # weights, CDR names, complex entities and campaign definitions stay open.
    dynamic = {"rank.weights", "mature.cdr.regions", "complex", "specificity"}
    def unknown(default, current, prefix=""):
        if prefix in dynamic or prefix.endswith(".env_vars") or not isinstance(current, dict):
            return
        for key, value in current.items():
            path = prefix + "." + str(key) if prefix else str(key)
            if key not in default:
                # Explicitly supported extension metadata is consumed outside
                # the baseline YAML; it is not an unknown analysis option.
                if path in dynamic or path in {"input_review", "hotspots.auto.exclude_positions"} or key in {"name", "id", "metadata", "case_id", "source", "reference", "api_msa_consent"}:
                    continue
                warnings.warn(f"기본 설정에 없는 키입니다. 효과가 있는 옵션인지 확인하세요: {path}", UserWarning, stacklevel=3)
            elif isinstance(default[key], dict):
                unknown(default[key], value, path)
    unknown(defaults, cfg)


def cfg_seed(cfg: dict, dotted: str, default: int = 42) -> int:
    """seed 값을 안전하게 해석한다(I01): null/누락이면 run.seed, 0은 유효값으로 보존."""
    value = cfg_get(cfg, dotted)
    if value is None:
        value = cfg_get(cfg, "run.seed", default)
    if value is None:
        return int(default)
    if type(value) is not int or not 0 <= value < 2**32:
        raise ValueError(f"{dotted} seed는 null 또는 0–4294967295 사이의 정수여야 합니다.")
    return value


def cfg_get(cfg: dict, dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def cfg_set(cfg: dict, dotted: str, value: Any) -> None:
    _set_dotted(cfg, dotted, value)


def make_run_id(cfg: dict) -> str:
    name = str(cfg["run"].get("name") or "run").strip()
    name = re.sub(r"[^A-Za-z0-9_.\-]+", "-", name) or "run"
    stamp = _dt.datetime.now().strftime("%y%m%d-%H%M%S")
    return f"{name}_{stamp}"


def resolve_run_dir(cfg: dict, run_id: str | None = None, resume_id: str | None = None) -> Path:
    outdir = Path(cfg["run"].get("outdir") or "runs")
    if not outdir.is_absolute():
        outdir = PROJECT_ROOT / outdir
    outdir = outdir.resolve()
    identifier = resume_id or run_id or make_run_id(cfg)
    if not isinstance(identifier, str) or Path(identifier).is_absolute():
        raise ValueError("run ID는 run.outdir 내부의 상대경로여야 합니다. 기존 절대경로에는 --run-dir을 사용하세요.")
    run_dir = (outdir / identifier).resolve()
    # Resolve aliases once, then keep both new and resumed named runs inside
    # their configured output root. Explicit operator --run-dir is separate.
    if run_dir == outdir or not run_dir.is_relative_to(outdir):
        raise ValueError("run ID는 run.outdir 자체 또는 외부 경로를 가리킬 수 없습니다.")
    if resume_id:
        if not run_dir.exists():
            raise FileNotFoundError(f"재개할 run 디렉터리가 없습니다: {run_dir}")
    return run_dir


def resolve_path(cfg: dict, value: str | Path | None) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


_RUN_ARTIFACT_DIRS = {
    "01_target", "01_msa", "02_design", "02_comparison", "02_screen", "02b_tier2", "02c_boltz2",
    "02_mature", "02b_mature_complex", "03_score", "04_developability", "05_final",
}


def resolve_run_artifact(run_dir: str | Path, value: str | Path | None) -> Path | None:
    """Resolve a run-produced path locally, including legacy absolute paths after copying a run."""
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
        return None
    run_dir = Path(run_dir)
    path = Path(value)
    if not path.is_absolute():
        return run_dir / path

    # Prefer the artifact inside the selected run so copied runs never depend on the source host.
    parts = path.parts
    for index, part in enumerate(parts):
        if part in _RUN_ARTIFACT_DIRS:
            rebased = run_dir.joinpath(*parts[index:])
            if rebased.exists():
                return rebased
    return path


def dump_resolved(cfg: dict, path: Path) -> None:
    from .utils.atomic import write_bytes
    write_bytes(path, yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).encode("utf-8"))


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
