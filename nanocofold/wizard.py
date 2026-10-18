"""Local, CPU-only target preparation wizard. Never launches model inference."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import secrets
import shlex
import shutil
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from . import hotspots
from .config import ASSET_ROOT, PROJECT_ROOT
from .utils import pdb_io
from .utils.residue_mapping import write_residue_mapping

MAX_REQUEST = 20 * 1024 * 1024
BOUNDARY = (
    "표면 특성을 바탕으로 결합 부위(hotspot)를 제안합니다. 결합 여부는 실험으로 확인하세요. "
    "결손 잔기·원자는 입력 상태로 유지합니다. 절단형 특이성에는 별도 입력과 검증이 필요합니다."
)


def _native(value):
    if isinstance(value, dict):
        return {str(k): _native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(v) for v in value]
    if hasattr(value, "item"):
        return _native(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def json_bytes(value) -> bytes:
    return json.dumps(_native(value), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def _keep_chains(source: Path, destination: Path, selected: list[str], *, nonprotein: bool = False):
    structure = pdb_io.load_structure(source)
    model = next(structure.get_models())
    for chain in list(model):
        if chain.id not in selected:
            # Embedded ligands/glycans leave together with their excluded protein
            # chain. Separately identified nonprotein chains remain explicit context.
            if not nonprotein or any(pdb_io.is_standard_residue(r) for r in chain):
                model.detach_child(chain.id)
    pdb_io.save_structure(structure, destination, drop_hetatm=not nonprotein)


class WizardSession:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir).resolve()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self._temp = tempfile.TemporaryDirectory(prefix="nanocofold-wizard-")
        self.root = Path(self._temp.name)
        self.input: dict | None = None
        self.selection: dict | None = None

    def close(self):
        self._temp.cleanup()

    def load(self, payload: dict) -> dict:
        from .structure_input import enrich_rcsb_metadata, fetch_pdb_assembly, inspect_structure

        # A failed replacement must not leave an old source eligible for export.
        self.input = self.selection = None
        for child in self.root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
        work = Path(tempfile.mkdtemp(prefix="input-", dir=self.root))
        if payload.get("pdb_id"):
            if payload.get("content") is not None:
                raise ValueError("PDB 번호와 파일은 한 번에 하나만 입력하세요.")
            assembly_id = str(payload.get("assembly_id", "1"))
            path = fetch_pdb_assembly(str(payload["pdb_id"]), assembly_id, work / "download")
            source = {"kind": "rcsb", "pdb_id": str(payload["pdb_id"]).strip().upper(),
                      "assembly_id": str(int(assembly_id)), "biological_assembly": True,
                      "type": "rcsb_pdb_assembly",
                      "url": f"https://files.rcsb.org/download/{str(payload['pdb_id']).strip().upper()}-assembly{int(assembly_id)}.cif.gz"}
        else:
            filename = str(payload.get("filename", ""))
            suffix = Path(filename).suffix.lower()
            if Path(filename).name != filename or suffix not in {".pdb", ".ent", ".cif", ".mmcif"}:
                raise ValueError("PDB 또는 mmCIF 파일(.pdb/.cif)을 선택하세요. 압축 파일은 먼저 풀어 주세요.")
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip() or len(content.encode()) > MAX_REQUEST:
                raise ValueError("구조 파일이 비어 있거나 20MB 한도를 넘었습니다.")
            path = work / ("uploaded" + suffix)
            path.write_text(content, encoding="utf-8")
            source = {"kind": "upload", "type": "local_upload", "filename": filename, "biological_assembly": "unverified"}
        info = inspect_structure(path, work / "normalized", source=source, model_index=payload.get("model_index"))
        if source["kind"] == "rcsb":
            enrich_rcsb_metadata(info, source["pdb_id"])
        info["input_id"] = secrets.token_hex(12)
        info["source_file"] = str(path)
        info["source"].pop("path", None)  # ephemeral server paths are not reproducible provenance
        if source["kind"] == "upload":
            info.setdefault("warnings", []).append(
                "업로드한 파일이 생물학적 복합체인지 확인하세요. 결정 내 중복 사본을 자동 판별하지 않습니다."
            )
        info["warnings"] = list(dict.fromkeys(info.get("warnings", []) + [BOUNDARY]))
        self.input = info
        return {k: v for k, v in info.items() if k not in {"source_file", "structure_path", "context_path"}}

    def analyze(self, payload: dict) -> dict:
        self.selection = None
        if not self.input or payload.get("input_id") != self.input["input_id"]:
            raise ValueError("입력이 변경되었거나 없습니다. 구조를 먼저 불러오세요.")
        by_id = {c["id"]: c for c in self.input["chains"]}
        target = str(payload.get("target", ""))
        context = payload.get("context", [])
        if target not in by_id:
            raise ValueError("단백질 이름과 원본 체인을 확인한 후 표적 하나를 선택하세요.")
        if not isinstance(context, list) or any(not isinstance(c, str) or c not in by_id for c in context):
            raise ValueError("유지할 파트너 체인 목록이 현재 입력과 맞지 않습니다.")
        selected = [target] + sorted(set(context) - {target})
        if sum(by_id[c]["length"] for c in selected) > 2000:
            raise ValueError("빠른 입력 검토는 합계 2,000잔기까지 지원합니다. 필요한 복합체 사본을 선택하세요.")
        fingerprint = _digest({"input": self.input["input_id"], "target": target, "context": selected})
        work = self.root / ("selection-" + fingerprint[:16])
        work.mkdir(exist_ok=True)
        protein = work / "proteins.pdb"
        context_path = work / "surface_context.pdb"
        _keep_chains(Path(self.input["structure_path"]), protein, selected)
        _keep_chains(Path(self.input.get("context_path") or self.input["structure_path"]),
                     context_path, selected, nonprotein=True)
        # Source B columns are not evidence of prediction confidence.
        table = hotspots.hotspot_table(context_path, target, confidence_kind="bfactor")
        proposals = hotspots.auto_hotspot_sets(table, {
            "num_sets": 3, "residues_per_set": [3, 6], "exclude_terminal": 5,
            "min_relative_sasa": .25, "patch_radius": 12.0, "conservation_weight": 0.0,
        }, context_path, target)
        original = {r["position"]: r for r in by_id[target]["residues"]}
        residues = []
        for row in table.to_dict("records"):
            position = int(row["position"])
            residues.append({**original[position], "relative_sasa": row["relative_sasa"],
                             "bfactor": row.get("plddt"), "dist_to_glycan": row["dist_to_glycan"]})
        patches = []
        for proposal in proposals:
            cautions = []
            if proposal.get("glycan_risk"):
                cautions.append("당질 근처의 부위입니다. 제거된 당질이 결합을 가릴 수 있습니다.")
            if proposal["hydrophobic_count"] < 3:
                cautions.append("소수성 잔기가 적은 부위입니다. 성공 가능성을 별도로 확인해야 합니다.")
            patches.append({"set_id": proposal["set_id"], "positions": proposal["residues_seq"],
                            "score": proposal["score"], "warnings": cautions,
                            "reason": f"평균 상대 노출도 {proposal['mean_relative_sasa']:.2f}, "
                                      f"소수성 잔기 {proposal['hydrophobic_count']}개. 점수는 후보 비교용이며 확률이 아닙니다."})
        warnings = list(self.input.get("warnings", []))
        context_structure = pdb_io.load_structure(context_path)
        standalone_context = [c.id for c in next(context_structure.get_models())
                              if not any(pdb_io.is_standard_residue(r) for r in c)]
        if standalone_context:
            warnings.append("별도 비단백질 체인 " + ", ".join(standalone_context)
                            + "은 표면 분석 문맥에 유지했습니다. 선택한 단백질과의 소속 관계는 자동 추정하지 않습니다.")
        blockers = list(self.input.get("blockers", []))
        for cid in selected:
            warnings.extend(by_id[cid].get("warnings", []))
            blockers.extend(by_id[cid].get("blockers", []))
        if not patches:
            warnings.append("자동 추천 조건을 만족하는 부위가 없습니다. 노출도를 확인하고 직접 선택할 수 있습니다.")
        design_blockers = list(blockers)
        for cid in selected:
            design_blockers.extend(by_id[cid].get("design_blockers", []))
        if len(selected) > 1:
            warnings.append(
                "복합체 설계: RFantibody의 T 표기는 여러 항원 사슬을 묶은 형식입니다. "
                "입력 서열·사슬 대응을 검사하고 출력 서열이 정확히 일치할 때만 사슬을 복원합니다. "
                "사슬 복원 후에도 모델 내부의 항원 사슬 경계 표현 한계는 남습니다. "
                "RF2 전체 항원 점수는 특정 사슬의 결합 증거가 아니며, 파트너를 포함한 후속 평가가 필요합니다."
            )
        if self.input.get("removed"):
            warnings.append("단백질 이외 성분은 설계 입력에 포함되지 않습니다. 아래 제거 내역과 결합 부위 영향을 확인하세요.")
        result = {
            "selection_id": fingerprint, "target": target,
            "retained_chains": [by_id[c] for c in selected],
            "removed_chains": [c for c in self.input["chains"] if c["id"] not in selected],
            "patches": patches, "residues": residues,
            "retained_nonprotein_chains": standalone_context,
            "warnings": list(dict.fromkeys(warnings)), "blockers": list(dict.fromkeys(blockers)),
            "design_blockers": list(dict.fromkeys(design_blockers)), "screen_blockers": list(dict.fromkeys(blockers)),
            "pdb": protein.read_text(),
            "preparation": {
                "recommended": "coordinates" if not blockers else "sequence_prediction",
                "coordinates_available": not blockers,
                "sequence_confirmation_required": [c for c in selected if not by_id[c].get("full_sequence")
                                                   or by_id[c].get("sequence_review", {}).get("unknown_positions")],
                "sequence_prediction_blockers": [issue for cid in selected
                                                 for issue in by_id[cid].get("sequence_review", {}).get("conflicts", [])],
            },
        }
        self.selection = {"result": result, "protein": protein, "selected": selected}
        return result

    def export(self, payload: dict) -> dict:
        if not self.selection or payload.get("selection_id") != self.selection["result"]["selection_id"]:
            raise ValueError("선택 내용이 바뀌었습니다. 표적과 결합 부위를 다시 확인하세요.")
        result = self.selection["result"]
        if result["blockers"]:
            raise ValueError("입력 문제를 먼저 해결하세요: " + "; ".join(result["blockers"]))
        if payload.get("acknowledge") is not True:
            raise ValueError("추천의 한계와 보존·제거 내역을 확인한 뒤 확인란을 선택하세요.")
        positions = payload.get("hotspots")
        hotspot_mode = payload.get("hotspot_mode", "specified")
        if hotspot_mode not in {"none", "specified"}:
            raise ValueError("hotspot_mode는 none 또는 specified여야 합니다.")
        allowed = {r["position"]: r for r in result["residues"]}
        if (not isinstance(positions, list)
                or (positions != [] if hotspot_mode == "none" else not 1 <= len(positions) <= 12)
                or any(type(p) is not int or p not in allowed for p in positions)
                or len(set(positions)) != len(positions)):
            raise ValueError("현재 표적에 실제로 존재하는 결합 부위를 중복 없이 1–12개 선택하세요.")
        positions = sorted(positions)
        warnings = list(result["warnings"])
        if hotspot_mode == "none":
            warnings.append("결합 부위 미지정: 결합 위치를 제한하지 않고 탐색합니다.")
        for pos in positions:
            row = allowed[pos]
            if row["relative_sasa"] is None or not math.isfinite(float(row["relative_sasa"])):
                raise ValueError(f"{row['original_label']}: 노출도를 계산할 수 없어 내보낼 수 없습니다.")
            if row["relative_sasa"] < .15:
                raise ValueError(f"{row['original_label']}: 복합체 내부에 매몰된 잔기입니다. 노출된 부위를 선택하세요.")
            if row.get("dist_to_glycan") is not None and math.isfinite(float(row["dist_to_glycan"])) and row["dist_to_glycan"] < 8:
                warnings.append(f"{row['original_label']}: 당질 근처 hotspot. 단백질만의 예측에는 이 차폐가 반영되지 않습니다.")
        fingerprint = _digest({"selection": result["selection_id"], "hotspots": positions, "hotspot_mode": hotspot_mode})
        self.output_dir.mkdir(parents=True, exist_ok=True)
        destination = self.output_dir / ("target-" + fingerprint[:10] + "-" + secrets.token_hex(3))
        with tempfile.TemporaryDirectory(prefix=".export-", dir=self.output_dir) as tmp:
            staging = Path(tmp)
            selected = result["retained_chains"]
            chain_map = {c["id"]: chr(65 + i) for i, c in enumerate(selected)}
            # The screen engine uses target A followed by context B,C,... . Use
            # that same representation in the exported structure and manifest.
            if len(selected) > 25:
                raise ValueError("후속 예측은 표적·파트너 합계 25개 체인까지 지원합니다.")
            structure = pdb_io.load_structure(self.selection["protein"])
            from Bio.PDB.Structure import Structure
            from Bio.PDB.Model import Model
            output_structure = Structure("reviewed_target")
            model = Model(0)
            output_structure.add(model)
            for entry in selected:
                chain = copy.deepcopy(pdb_io.get_chain(structure, entry["id"]))
                chain.detach_parent()
                chain.id = chain_map[entry["id"]]
                model.add(chain)
            pdb_io.save_structure(output_structure, staging / "target.pdb")
            mapping = []
            for entry in selected:
                output_chain = chain_map[entry["id"]]
                observed = pdb_io.chain_sequence(pdb_io.get_chain(output_structure, output_chain))
                if observed != entry["sequence"]:
                    raise ValueError("체인 서열 검증에 실패했습니다. 내보내기를 중단합니다.")
                for residue in entry["residues"]:
                    mapping.append({"protein": entry["name"], "source_chain": entry["original_id"],
                                    "author_chain": entry.get("author_chain", entry["original_id"]),
                                    "source_residue": residue["original_label"], "aa": residue["aa"],
                                    "output_chain": output_chain, "output_position": residue["position"]})
            write_residue_mapping(staging / "residue_mapping.csv", mapping)
            source_file = Path(self.input["source_file"])
            source_name = "source.cif.gz" if source_file.name.endswith(".cif.gz") else "source" + source_file.suffix
            shutil.copy2(source_file, staging / source_name)
            source_info = {**self.input["source"], "snapshot_file": source_name}
            design_blockers = list(result["design_blockers"])
            if any("당질 근처 hotspot" in warning for warning in warnings):
                design_blockers.append("선택한 부위의 당질 차폐를 반영하지 못하므로 자동 설계를 차단합니다.")
            cfg = {
                "run": {"name": "reviewed_target", "mode": "design", "outdir": str(destination / "runs"), "seed": 42},
                "target": {"name": selected[0]["name"], "pdb": str(destination / "target.pdb"),
                           "chain": "A", "sequence": selected[0]["sequence"],
                           "hotspots": [f"A:pdb{p}" for p in positions],
                           "aux_chains": [{"name": c["name"], "sequence": c["sequence"]} for c in selected[1:]]},
                "structure": {"provider": "none", "msa": {"backend": "empty"}},
                "truncate": {"enabled": False, "keep_other_chains": True},
                "design": {"env": str(PROJECT_ROOT / "envs/rfantibody"),
                           "framework": str(PROJECT_ROOT / "data/frameworks/h-NbBCII10.pdb"),
                           "num_designs": 2, "chunk_size": 2, "mpnn": {"seqs_per_struct": 2},
                           "env_vars": {"DGLBACKEND": "pytorch", "OMP_NUM_THREADS": "4"}},
                "screen": {"engine": "boltz2", "boltz2_env": str(PROJECT_ROOT / "envs/boltz2"),
                           "restraint": {"enabled": False}},
                "specificity": {"enabled": False},
                "input_review": {"source": source_info, "fingerprint": fingerprint,
                                 "design_blockers": design_blockers,
                                 "target_sha256": hashlib.sha256((staging / "target.pdb").read_bytes()).hexdigest(),
                                 "target_sequence": selected[0]["sequence"], "hotspot_positions": positions,
                                 "hotspot_mode": hotspot_mode,
                                 "target_name": selected[0]["name"],
                                 "context_names": [c["name"] for c in selected[1:]],
                                 "context_sequences": [c["sequence"] for c in selected[1:]],
                                 "statement": BOUNDARY},
            }
            config_path = destination / "config.yaml"
            (staging / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            quoted = shlex.quote(str(config_path))
            commands = {"doctor_command": f"nanocofold doctor -c {quoted}",
                        "prepare_command": f"nanocofold prepare -c {quoted}",
                        "design_command": None if design_blockers else f"nanocofold design -c {quoted}",
                        "screen_command": f"nanocofold screen -c {quoted} --vhh-fasta YOUR_VHH.fasta"}
            manifest = {"schema_version": 1, "fingerprint": fingerprint, "source": source_info,
                        "source_sha256": self.input.get("sha256"), "selection_id": result["selection_id"],
                        "target": selected[0], "retained_chains": selected, "removed_chains": result["removed_chains"],
                        "removed_components": self.input.get("removed", {}),
                        "retained_nonprotein_chains": result["retained_nonprotein_chains"],
                        "hotspots": [allowed[p] for p in positions], "hotspot_mode": hotspot_mode, "warnings": warnings,
                        "design_blockers": design_blockers, "predictions_executed": False,
                        "chain_mapping": chain_map, "commands": commands,
                        "target_sha256": cfg["input_review"]["target_sha256"]}
            (staging / "input_review.json").write_bytes(json_bytes(manifest))
            guide = (
                "# 검토한 입력\n\n이 디렉터리는 선택 당시의 독립적인 스냅샷입니다. 새 선택을 하면 다시 저장하세요.\n\n"
                + BOUNDARY + "\n\nGPU 추론은 실행하지 않았습니다. 먼저 doctor로 환경을 확인하세요. "
                + "모델 환경·가중치·framework 파일 설치 여부는 입력 준비와 별개입니다.\n\n## 다음 명령\n\n"
                + "\n\n".join(f"`{command}`" for command in commands.values() if command)
                + "\n\nYOUR_VHH.fasta는 보유 나노바디 서열 FASTA 경로로 바꾸세요.\n\n"
                + "\n".join("- " + warning for warning in warnings + design_blockers) + "\n"
            )
            guide = ("# Reviewed inputs\n\nThis directory is an independent snapshot of your reviewed selection. "
                     "Export again after changing inputs or selected residues. No GPU inference was run. "
                     "Binding-site suggestions are surface heuristics, not validated binding or affinity. "
                     "Missing atoms/residues are not repaired.\n\n"
                     "Run doctor first. Model environments, weights, and framework files require separate installation. "
                     "prepare prepares inputs; design and screen run GPU inference. "
                     "Replace YOUR_VHH.fasta with your own nanobody FASTA path in the commands below.\n\n"
                     + guide)
            (staging / "README.md").write_text(guide, encoding="utf-8")
            # All files are complete before the visible snapshot appears.
            staging.rename(destination)
        return {"export_dir": str(destination), "config_path": str(config_path), **commands,
                "warnings": warnings + design_blockers, "fingerprint": fingerprint, "chain_mapping": chain_map}


def create_server(output_dir: Path, port: int = 8766) -> ThreadingHTTPServer:
    session = WizardSession(output_dir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Request logs must not expose the session token or uploaded content.

        def _allowed_origin(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in allowed and (origin is None or origin in {f"http://{h}" for h in allowed})

        def _send(self, status, data, content_type="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; "
                             "worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._allowed_origin():
                return self._send(403, json_bytes({"error": "로컬 화면에서 접근하세요."}))
            path = urlsplit(self.path).path
            if path == "/":
                template = Path(__file__).parent / "templates/wizard.html"
                content = template.read_text(encoding="utf-8").replace("__TOKEN__", session.token)
                self._send(200, content.encode(), "text/html; charset=utf-8")
            elif path in {"/assets/3dmol-min.js", "/assets/platform-i18n.js"}:
                asset = (ASSET_ROOT / "assets/3dmol-min.js" if path.endswith("3dmol-min.js")
                         else Path(__file__).parent / "templates/platform-i18n.js")
                if asset.is_file():
                    self._send(200, asset.read_bytes(), "application/javascript; charset=utf-8")
                else:
                    self._send(404, json_bytes({"error": "로컬 3D 뷰어 자산이 없습니다. 표에서 잔기를 선택할 수 있습니다."}))
            else:
                self._send(404, json_bytes({"error": "없는 경로입니다."}))

        def do_POST(self):
            if (not self._allowed_origin()
                    or not secrets.compare_digest(self.headers.get("X-Wizard-Token", "").encode("utf-8"), session.token.encode("ascii"))):
                return self._send(403, json_bytes({"error": "인증이 만료되었거나 다른 사이트의 요청입니다. 화면을 새로 여세요."}))
            routes = {"/api/load": session.load, "/api/analyze": session.analyze, "/api/export": session.export}
            if self.path not in routes:
                return self._send(404, json_bytes({"error": "없는 요청입니다."}))
            if self.headers.get_content_type() != "application/json":
                return self._send(415, json_bytes({"error": "JSON 요청만 지원합니다."}))
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= MAX_REQUEST:
                    return self._send(413, json_bytes({"error": "요청은 20MB 이하여야 합니다."}))
                self.connection.settimeout(15)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("요청 형식이 올바르지 않습니다.")
            except (ValueError, OSError):
                return self._send(400, json_bytes({"error": "입력 데이터를 읽지 못했습니다. 파일과 형식을 확인하세요."}))
            if not session.lock.acquire(blocking=False):
                return self._send(409, json_bytes({"error": "앞선 입력을 처리 중입니다. 완료 후 다시 시도하세요."}))
            try:
                result = routes[self.path](payload)
                self._send(200, json_bytes(result))
            except (ValueError, OSError) as exc:
                from .platform.errors import public_error
                print(f"[wizard] {type(exc).__name__}: {exc}", file=sys.stderr)
                self._send(400, json_bytes({"error": public_error(exc)}))
            except Exception as exc:  # keep a malformed parser response from crashing the local service
                print(f"[wizard] {type(exc).__name__}: {exc}", file=sys.stderr)
                self._send(500, json_bytes({"error": "구조 처리에 실패했습니다. 올바른 단일 모델 PDB/mmCIF로 다시 시도하세요. 자세한 내용은 터미널에 표시됩니다."}))
            finally:
                session.lock.release()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except Exception:
        session.close()
        raise
    server.daemon_threads = False
    server.session = session
    return server


def serve(output_dir: Path, port: int = 8766, open_browser: bool = True):
    server = create_server(output_dir, port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"NanoCoFold input wizard / 입력 마법사: {url}\nOutput / 저장 위치: {Path(output_dir).resolve()}\nNo GPU inference / GPU 추론 없음 · Stop / 종료: Ctrl+C", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.session.close()
