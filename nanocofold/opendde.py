"""Optional, pinned OpenDDE Preview protein co-folding adapter.

Existing VHH generation stays separate. All MSAs must already be reviewed and
present; this adapter never asks OpenDDE to search templates or upload queries.
"""
from __future__ import annotations

import json
import hashlib
import copy
import subprocess
from pathlib import Path

import numpy as np
from Bio.PDB.MMCIF2Dict import MMCIF2Dict

from . import diagnostics, provenance
from .config import cfg_get, cfg_seed, resolve_path
from .utils import pdb_io, shell
from .startup_limits import READINESS_TIMEOUT_SECONDS

SOURCE_COMMIT = "ddfa1df8aff1babf1fddac4247b7d2351bd0ce9f"
VERSION = "1.1.1"
_OFFLINE_GUARD = """import socket
def denied(*args, **kwargs):
    raise RuntimeError('OpenDDE inference network disabled; prepare all inputs and assets before execution')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
"""


def settings(cfg, *, require_enabled=True):
    value = cfg_get(cfg, "screen.opendde", {})
    if not isinstance(value, dict):
        raise ValueError("screen.opendde 설정은 객체여야 합니다.")
    allowed = {"enabled", "env", "data_dir", "checkpoint", "cycles", "steps", "samples", "dtype",
               "attention_chunk_size"}
    if set(value) - allowed:
        raise ValueError("지원하지 않는 OpenDDE 설정입니다. 임의 CLI 인수는 허용하지 않습니다.")
    if type(value.get("enabled", False)) is not bool:
        raise ValueError("OpenDDE enabled는 true/false여야 합니다.")
    if require_enabled and value.get("enabled") is not True:
        raise ValueError("OpenDDE Preview는 screen.opendde.enabled=true로 명시적으로 활성화하세요.")
    result = {"env": value.get("env", "envs/opendde"), "data_dir": value.get("data_dir", "models/opendde"),
              "checkpoint": value.get("checkpoint", "opendde_abag.pt"), "dtype": value.get("dtype", "fp32")}
    if any(not isinstance(result[k], str) or not result[k] for k in result):
        raise ValueError("OpenDDE 경로와 옵션은 비어 있지 않은 문자열이어야 합니다.")
    if result["checkpoint"] not in {"opendde.pt", "opendde_abag.pt"} or result["dtype"] not in {"fp32", "bf16"}:
        raise ValueError("OpenDDE checkpoint/dtype 설정 오류")
    for key, default, lo, hi in (("cycles", 10, 1, 20), ("steps", 200, 1, 1000), ("samples", 1, 1, 5)):
        number = value.get(key, default)
        if type(number) is not int or not lo <= number <= hi:
            raise ValueError(f"OpenDDE {key}: {lo}–{hi} 정수가 필요합니다.")
        result[key] = number
    chunk = value.get("attention_chunk_size", 4)
    if chunk is not None and (type(chunk) is not int or not 1 <= chunk <= 256):
        raise ValueError("OpenDDE attention_chunk_size: 1–256 정수 또는 null이 필요합니다.")
    result["attention_chunk_size"] = chunk
    return result


def inference_entrypoint(options):
    """Set only native execution tiling before invoking the pinned official CLI.

    The upstream dynamic score budget can allocate >10 GiB for a single
    structural-token attention temporary. Its existing fixed-chunk config
    avoids this without reducing MSA depth, changing precision or sampling.
    Installed upstream source files stay byte-for-byte pinned. ``None`` keeps
    the upstream policy; the wrapper and option both enter the reuse identity.
    """
    chunk = options["attention_chunk_size"]
    if chunk is not None and (type(chunk) is not int or not 1 <= chunk <= 256):
        raise ValueError("Invalid OpenDDE attention chunk size")
    code = _OFFLINE_GUARD
    if chunk is not None:
        code += f"""from runner import batch_inference as runtime
native_build = runtime.build_inference_config
def build_with_execution_chunk(*args, **kwargs):
    configs = native_build(*args, **kwargs)
    configs.infer_setting.chunk_size = {chunk}
    configs.infer_setting.dynamic_chunk_size = False
    if configs.infer_setting.chunk_size != {chunk} or configs.infer_setting.dynamic_chunk_size is not False:
        raise RuntimeError('OpenDDE fixed attention execution policy was not applied')
    print('NanoCoFold OpenDDE fixed attention chunk_size={chunk}; scientific inputs and schedule unchanged', flush=True)
    return configs
runtime.build_inference_config = build_with_execution_chunk
"""
    return code + "from runner.cli import opendde_cli; opendde_cli()"


def assets():
    return json.loads((Path(__file__).parent / "_data/opendde-assets.json").read_text())["assets"]


def installation_identity(env):
    manifest = json.loads((Path(__file__).parent / "_data/opendde-assets.json").read_text())
    expected = manifest["source_archive_sha256"]
    files = manifest["source_files"]
    # uv can omit archive hashes in PEP 610 metadata even after requiring
    # download hashes. Verify the installed source bytes rather than accepting
    # a version string or URL as proof of the source revision.
    code = """import importlib.metadata as m,json,hashlib,sys
from pathlib import Path
d=m.distribution('opendde')
keys=json.loads(sys.argv[1])
files={k:hashlib.sha256(d.locate_file(k).read_bytes()).hexdigest() if d.locate_file(k).is_file() else None for k in keys}
extra=[str(p.relative_to(d.locate_file(''))) for root in ('opendde','runner') for p in Path(d.locate_file(root)).rglob('*.py') if str(p.relative_to(d.locate_file(''))) not in files]
print(json.dumps({'version':d.version,'source':json.loads(d.read_text('direct_url.json') or '{}'),'files':files,'extra':extra}))
"""
    try:
        result = shell._budgeted_process([str(Path(env) / "bin/python"), "-c", code, json.dumps(list(files))],
                                         cwd=None, env=None, timeout=shell.remaining_budget(READINESS_TIMEOUT_SECONDS))
        value = diagnostics._loads(result.stdout) if result.returncode == 0 else {"error": (result.stdout or "")[-2048:]}
        if not isinstance(value, dict) or not isinstance(value.get("source", {}), dict):
            return {"ok": False, "error": "invalid installed source metadata"}
        archive = value.get("source", {}).get("archive_info", {})
        actual = archive.get("hashes", {}).get("sha256") if isinstance(archive, dict) else None
        installed = value.pop("files", {})
        value["source_mismatch"] = [k for k, digest in files.items() if installed.get(k) != digest]
        value["source_sha256"] = provenance.fingerprint(installed)
        value["checked_source_files"] = len(installed)
        value["source_commit"] = SOURCE_COMMIT
        value["archive_hash_present"] = actual is not None
        url = "https://github.com/aurekaresearch/OpenDDE/archive/" + SOURCE_COMMIT + ".tar.gz"
        value["ok"] = (value.get("version") == VERSION and value.get("source", {}).get("url") == url and
                       not value["source_mismatch"] and not value.get("extra") and
                       actual in (None, expected))
        return value
    except (shell.ExecutionCancelled, TimeoutError, subprocess.TimeoutExpired):
        raise
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"ok": False, "error": str(exc)}


def model_paths(cfg):
    options = settings(cfg, require_enabled=False)
    root = resolve_path(cfg, options["data_dir"])
    return {key: root / key for key in assets() if not key.startswith("checkpoint/") or key.endswith("/" + options["checkpoint"])}


def readiness(cfg):
    expected, paths = assets(), model_paths(cfg)
    found, errors = [], []
    for key, path in paths.items():
        if not path.is_file():
            errors.append(key + ": missing")
            continue
        receipt = {"path": str(path), "size": path.stat().st_size, "sha256": provenance.file_digest(path)}
        found.append(receipt)
        if receipt["size"] != expected[key]["size"] or receipt["sha256"] != expected[key]["sha256"]:
            errors.append(key + ": released asset identity mismatch")
    return {"ok": not errors, "required": list(paths), "found": found, "missing": errors,
            "blocker": "; ".join(errors) if errors else None, "stage": "pinned_opendde_assets"}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def input_job(chains, seed, chain_msas=None):
    from .colabfold_msa import read_monomer
    if (not isinstance(chains, dict) or not 2 <= len(chains) <= 25 or
            list(chains) != [chr(65 + i) for i in range(len(chains))]):
        raise ValueError("OpenDDE는 순서가 명확한 A–Y 단백질 체인 2–25개를 지원합니다.")
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError("OpenDDE seed 범위를 확인하세요.")
    msa = chain_msas if chain_msas is not None else {}
    if not isinstance(msa, dict):
        raise ValueError("OpenDDE MSA는 체인별 파일 경로 객체여야 합니다.")
    if msa and set(msa) != set(chains):
        raise ValueError("OpenDDE MSA는 모든 입력 체인에 지정하거나 모두 비워야 합니다.")
    entities, hashes = [], {}
    for letter, sequence in chains.items():
        if not isinstance(sequence, str) or not sequence or set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"):
            raise ValueError("OpenDDE 입력은 표준 아미노산 단백질 서열입니다.")
        entry = {"sequence": sequence, "count": 1, "id": [letter]}
        if msa:
            path = Path(msa[letter]).resolve()
            read_monomer(path, sequence)
            entry["unpairedMsaPath"] = str(path)
            hashes[letter] = provenance.file_digest(path)
        entities.append({"proteinChain": entry})
    if sum(len(s) for s in chains.values()) > 2000:
        raise ValueError("OpenDDE 지원 입력은 합계 2,000잔기 이하입니다.")
    return [{"name": "nanocofold", "modelSeeds": [seed], "sequences": entities}], hashes


def normalize_outputs(native, output, chains, samples):
    """Require exact sequences and explicit atom→token→chain PAE mapping."""
    from .score_complex import validate_pae
    expected = {f"nanocofold_sample_{i}.cif" for i in range(samples)}
    if {p.name for p in native.glob("*.cif")} != expected:
        raise ValueError("OpenDDE 요청 모델 수와 실제 CIF 출력이 다릅니다.")
    output.mkdir(parents=True, exist_ok=True)
    for index in range(samples):
        source = native / f"nanocofold_sample_{index}.cif"
        structure = pdb_io.load_structure(source)
        if pdb_io.chain_ids(structure) != list(chains):
            raise ValueError("OpenDDE 출력 체인과 검토 입력 순서가 다릅니다.")
        for letter, sequence in chains.items():
            chain = pdb_io.get_chain(structure, letter)
            residues = list(pdb_io.iter_residues(chain))
            if pdb_io.chain_sequence(chain) != sequence or any("CA" not in residue for residue in residues):
                raise ValueError("OpenDDE 출력 서열/Cα와 검토 입력이 다릅니다.")
        if any(not np.isfinite(atom.coord).all() or not 0 <= atom.bfactor <= 100 for atom in structure.get_atoms()):
            raise ValueError("OpenDDE 좌표 또는 atom pLDDT 값이 잘못되었습니다.")
        full = json.loads((native / f"nanocofold_full_data_sample_{index}.json").read_text())
        summary = native / f"nanocofold_summary_confidence_sample_{index}.json"
        confidence = json.loads(summary.read_text())
        if not isinstance(full, dict) or not isinstance(confidence, dict):
            raise ValueError("OpenDDE confidence 출력은 객체여야 합니다.")
        matrix = np.asarray(full["token_pair_pae"], dtype=float)
        validate_pae(matrix)
        raw_tokens = np.asarray(full["atom_to_token_idx"])
        asym = np.asarray(full["token_asym_id"])
        ccd = MMCIF2Dict(str(source))
        letters, numbers, names = [ccd[k] for k in ("_atom_site.label_asym_id", "_atom_site.label_seq_id", "_atom_site.label_atom_id")]
        plddt = np.asarray(full["atom_plddt"], dtype=float)
        bfactor = np.asarray(ccd["_atom_site.B_iso_or_equiv"], dtype=float)
        # Full JSON rounds probabilities to two decimals; CIF keeps the
        # original score on a 0–100 scale. Permit only that rounding error.
        if (plddt.shape != (len(names),) or bfactor.shape != plddt.shape or
                not np.isfinite(plddt).all() or np.any(plddt < 0) or np.any(plddt > 1) or
                np.any(np.abs(bfactor - 100 * plddt) > .511)):
            raise ValueError("OpenDDE CIF pLDDT와 atom confidence 대응이 다릅니다.")
        if (raw_tokens.ndim != 1 or len(raw_tokens) != len(names) or asym.shape != (len(matrix),) or
                not np.isfinite(raw_tokens).all() or not np.isfinite(asym).all() or
                np.any(raw_tokens != np.floor(raw_tokens)) or np.any(asym != np.floor(asym))):
            raise ValueError("OpenDDE atom/token 대응이 불명확합니다.")
        tokens = raw_tokens.astype(int)
        if np.any(tokens < 0) or np.any(tokens >= len(matrix)):
            raise ValueError("OpenDDE token 범위 오류")
        mapped, order = {}, []
        for i, (letter, number, atom) in enumerate(zip(letters, numbers, names)):
            token = int(tokens[i])
            if letter not in chains or not number.isdigit() or not 1 <= int(number) <= len(chains[letter]) or int(asym[token]) != list(chains).index(letter):
                raise ValueError("OpenDDE token과 CIF 체인/잔기 대응이 다릅니다.")
            key = (letter, int(number))
            if key in mapped and mapped[key] != token:
                raise ValueError("OpenDDE 잔기에 여러 token이 대응합니다.")
            mapped[key] = token
            if atom == "CA":
                order.append(key)
        expected_order = [(letter, i) for letter, sequence in chains.items() for i in range(1, len(sequence) + 1)]
        if order != expected_order or len(set(mapped.values())) != len(matrix) or len(mapped) != len(matrix):
            raise ValueError("OpenDDE PAE와 표준 잔기 순서가 다릅니다.")
        ordered = [mapped[key] for key in order]
        pae = matrix[np.ix_(ordered, ordered)]
        for key in ("iptm", "ptm"):
            if type(confidence.get(key)) not in (int, float) or not np.isfinite(confidence[key]) or not 0 <= confidence[key] <= 1:
                raise ValueError("OpenDDE native confidence 범위 오류")
        if "chain_pair_iptm" in confidence:
            pairs = np.asarray(confidence["chain_pair_iptm"], dtype=float)
            if pairs.shape != (len(chains), len(chains)) or not np.isfinite(pairs).all() or np.any(pairs < 0) or np.any(pairs > 1):
                raise ValueError("OpenDDE chain_pair_iptm 체인 대응/범위 오류")
        confidence.update(engine="opendde", experimental=True, native_summary_source=summary.name,
                          native_summary_sha256=provenance.file_digest(summary),
                          native_structure_sha256=provenance.file_digest(source),
                          chain_axis=list(chains), pae_token_indices=ordered,
                          pae_axis="CIF chain/residue order; atom_to_token_idx and token_asym_id verified")
        (output / f"opendde_model_{index}.cif").write_bytes(source.read_bytes())
        _write(output / f"confidence_opendde_model_{index}.json", confidence)
        np.savez_compressed(output / f"pae_opendde_model_{index}.npz", pae=pae)


def predict(cfg, candidate_dir, chains, *, chain_msas=None, dry_run=False):
    options = settings(cfg)
    seed = cfg_seed(cfg, "run.seed")
    job, msa_hashes = input_job(chains, seed, chain_msas)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    root = resolve_path(cfg, options["data_dir"])
    env = resolve_path(cfg, options["env"])
    output, raw = candidate_dir / "opendde_out", candidate_dir / "opendde_native"
    marker, receipt = candidate_dir / "prediction_complete.json", candidate_dir / "opendde_input.json"
    msa_dir, source = candidate_dir / "opendde_msas", candidate_dir / "opendde.json"
    installed = candidate_dir / "opendde_installation.json"
    identity = {"input": job, "msa_sha256": msa_hashes, "settings": options,
                "adapter_sha256": provenance.file_digest(Path(__file__)),
                "runtime": provenance.runtime_identity(cfg, "opendde") if not dry_run else None}
    fingerprint = provenance.fingerprint(identity)
    if not dry_run:
        check = readiness(cfg)
        if not check["ok"]:
            raise ValueError("OpenDDE 실행 준비: " + check["blocker"])
        version = installation_identity(env)
        if not version["ok"]:
            raise ValueError("OpenDDE 고정 버전/소스 불일치: " + str(version))
        if cfg_get(cfg, "run.resume", True) and marker.is_file() and receipt.is_file():
            saved, complete = json.loads(receipt.read_text()), json.loads(marker.read_text())
            if (saved.get("fingerprint") == fingerprint and complete.get("status") == "complete" and
                    source.is_file() and provenance.file_digest(source) == complete.get("input_sha256") and
                    msa_dir.is_dir() and complete.get("msa_outputs") == provenance.output_manifest(msa_dir) and
                    provenance.outputs_match(output, complete.get("outputs")) and
                    provenance.outputs_match(raw, complete.get("native_outputs"))):
                return output
        provenance.archive_existing([output, raw, marker, receipt, source, msa_dir, installed,
                                     candidate_dir / "opendde.log"], candidate_dir)
        _write(installed, version)
    else:
        source = candidate_dir / "opendde.dry-run.json"
        msa_dir = candidate_dir / "opendde_dry_msas"
    # Freeze reviewed alignments in the owned run directory. Missing input
    # paths make upstream search publicly; never hand it mutable cache paths.
    execution_job = copy.deepcopy(job)
    msa_dir.mkdir(exist_ok=True)
    if msa_hashes:
        for entity in execution_job[0]["sequences"]:
            entry = entity["proteinChain"]
            letter = entry["id"][0]
            content = Path(entry["unpairedMsaPath"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != msa_hashes[letter]:
                raise ValueError("OpenDDE 검토 후 MSA 파일이 변경되었습니다.")
            destination = msa_dir / f"{letter}-{msa_hashes[letter]}.a3m"
            if destination.exists() and destination.read_bytes() != content:
                raise ValueError("OpenDDE 고정 MSA 파일이 변경되었습니다.")
            destination.write_bytes(content)
            entry["unpairedMsaPath"] = str(destination.resolve())
    _write(source, execution_job)
    # Run the pinned official console entry point with outbound sockets denied.
    # A deleted MSA/cache can therefore fail, but cannot trigger a public search.
    entrypoint = inference_entrypoint(options)
    command = [str(shell.env_executable(env, "python", dry_run=dry_run)), "-c", entrypoint, "pred", "-i", str(source.resolve()),
               "-o", str(raw.resolve()), "-n", "opendde_v1", "--load_checkpoint_path", str(root / "checkpoint" / options["checkpoint"]),
               "--device", "cuda", "--dtype", options["dtype"], "--use_msa", "true" if msa_hashes else "false",
               "--use_template", "false", "--use_rna_msa", "false", "--need_atom_confidence", "true",
               "--sample", str(options["samples"]), "--step", str(options["steps"]), "--cycle", str(options["cycles"]),
               "--triatt_kernel", "torch", "--trimul_kernel", "torch", "--foldcp_mode", "single"]
    shell.run_in_env(env, command, extra_env=diagnostics._engine_extra_env(cfg, "opendde"),
                     log_path=candidate_dir / "opendde.log", dry_run=dry_run)
    if not dry_run:
        normalize_outputs(raw / "nanocofold" / f"seed_{seed}" / "predictions", output, chains, options["samples"])
        _write(receipt, {"fingerprint": fingerprint, **identity, "execution_input": execution_job})
        _write(marker, {"status": "complete", "input_sha256": provenance.file_digest(source),
                        "msa_outputs": provenance.output_manifest(msa_dir),
                        "outputs": provenance.output_manifest(output), "native_outputs": provenance.output_manifest(raw)})
    return output
