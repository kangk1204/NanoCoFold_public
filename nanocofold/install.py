"""Preserving workspace initialization and explicit, verified model installation."""
from __future__ import annotations

import hashlib
import http.client
import errno
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4
from pathlib import Path

from .startup_limits import READINESS_TIMEOUT_SECONDS as OFFLINE_IMPORT_TIMEOUT

# A malformed, lengthless response must not grow without bound. Released
# snapshots additionally enforce their exact size when the manifest records it.
MAX_DOWNLOAD_BYTES = 64 * 1024**3

PROFILES = {
    "core": (),
    "colabfold": ("colabfold",),
    "opendde": ("opendde",),
    "boltzgen": ("antibody", "boltzgen"),
    "complex": ("boltz2", "chai1"),
    "screen": ("antibody", "boltz2", "chai1"),
    "design": ("antibody", "boltz2", "chai1", "rfantibody", "boltzgen"),
    "mature": ("antibody", "boltz2", "chai1", "esm"),
    "all": ("antibody", "boltz2", "chai1", "rfantibody", "boltzgen", "esm", "colabfold", "opendde"),
}


def _package_root():
    return Path(__file__).resolve().parent


def _asset_root():
    source = _package_root().parent
    return source if (source / "config/default.yaml").exists() else _package_root() / "_data"


def _digest(path):
    from .provenance import file_digest
    return file_digest(path)


def _save(path, payload):
    from .utils.atomic import write_json
    write_json(Path(path), payload)


def init_workspace(workspace, *, refresh_managed=False):
    """Keep user configuration; refresh owned setup helpers only during setup."""
    workspace = Path(workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    asset_root = _asset_root()
    created = []
    updated = []
    manifest_path = workspace / ".platform/managed-templates.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {"schema_version": 1, "files": {}}
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("설치 템플릿 관리 기록 형식이 잘못되었습니다. 기존 파일을 보존했습니다.")
    def managed_copy(source, destination):
        if source.resolve() == destination.resolve():
            return
        relative = str(destination.relative_to(workspace))
        source_hash = _digest(source)
        if destination.is_symlink():
            raise ValueError(f"설치 템플릿 심볼릭 링크를 변경하지 않습니다: {destination}")
        if destination.exists():
            actual = _digest(destination)
            if actual == source_hash:
                manifest["files"][relative] = source_hash
                return
            if not refresh_managed:
                return
            prior = manifest["files"].get(relative)
            if prior is not None and actual != prior:
                raise ValueError(f"사용자가 수정한 설치 템플릿을 보존했습니다: {destination}. "
                                 "새 작업 공간으로 설치하거나 이 파일을 별도로 보존한 뒤 패키지 템플릿으로 복구하세요.")
            preserved = workspace / ".platform/template-history" / uuid4().hex / relative
            preserved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination, preserved)
            print(f"[설치 템플릿 갱신] {relative} · 이전 파일: {preserved.relative_to(workspace)}", flush=True)
            updated.append(relative)
        else:
            created.append(relative)
        from .utils.atomic import write_bytes
        write_bytes(destination, source.read_bytes(), root=workspace)
        manifest["files"][relative] = source_hash
    for name in ("config", "assets"):
        directory = asset_root / name
        if directory.is_dir():
            for source in directory.iterdir():
                if not source.is_file():
                    continue
                destination = workspace / name / source.name
                if source.resolve() == destination.resolve():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(source, destination)
                    created.append(str(destination.relative_to(workspace)))
    scripts = asset_root / "envs"
    if not scripts.exists():
        scripts = asset_root / "install/envs"
    for source in scripts.glob("*"):
        if source.is_file() and source.suffix in {".sh", ".txt"}:
            destination = workspace / "envs" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            managed_copy(source, destination)
    # Standalone RFantibody installer expects this small local manifest/helper.
    helpers = ["_data/rfantibody-weights.sha256", "scripts/check_rfantibody_runtime.py"]
    helpers.extend(str(path.relative_to(_package_root())) for path in (_package_root() / "_data/locks").glob("*"))
    for relative in helpers:
        source = _package_root() / relative
        destination = workspace / "nanocofold" / relative
        if source.exists() and source.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            managed_copy(source, destination)
    for directory in ("runs", "prepared_targets", "models", ".platform", "data/frameworks"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    _save(workspace / ".platform/workspace.json", {"schema_version": 1, "workspace": str(workspace)})
    _save(manifest_path, manifest)
    return {"workspace": str(workspace), "created": created, "updated": updated,
            "next": "nanocofold serve --workspace " + str(workspace)}


def _download(asset, destination, *, reuse_paths=()):
    destination = Path(destination)
    expected = asset["sha256"]
    if destination.exists():
        if _digest(destination) != expected:
            raise ValueError(f"모델 해시 불일치: {destination}. 기존 파일을 보존했습니다.")
        print(f"[모델 확인] {destination.name}", flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Sources are explicitly selected by the operator and read only. A source
    # becomes usable only after matching the curated content hash; engines read
    # these symlinks and never receive a source cache directory to write into.
    for source in reuse_paths:
        source = Path(source).expanduser().resolve()
        if source.is_file() and _digest(source) == expected:
            destination.symlink_to(source)
            print(f"[검증한 모델 재사용] {destination.name}", flush=True)
            return
    partial = destination.with_name(destination.name + ".partial")
    if partial.is_symlink() or (partial.exists() and not partial.is_file()):
        raise ValueError(f"일반 파일이 아닌 다운로드 경로를 수정하지 않습니다: {partial}")
    # Retain interrupted bytes. Only a complete SHA256 match can publish a model;
    # a partial download is never registered as an installed asset.
    restarted = False
    for attempt in range(1, 5):
        digest = hashlib.sha256()
        offset = 0
        if partial.exists():
            with partial.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    offset += len(chunk)
            if digest.hexdigest() == expected:
                _publish_download(partial, destination)
                print(f"[모델 확인] {destination.name}", flush=True)
                return
        headers = {"User-Agent": "NanoCoFold/0.3", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        print(f"[다운로드] {destination.name} · 시도 {attempt}/4 · 저장 {offset // 1048576} MiB", flush=True)
        try:
            original_url = urlsplit(asset["url"])
            local_http = original_url.scheme == "http" and original_url.hostname in {"127.0.0.1", "::1", "localhost"}
            if original_url.scheme != "https" and not local_http:
                raise ValueError("모델 다운로드는 HTTPS URL만 허용합니다(로컬 시험 서버 예외).")
            request = urllib.request.Request(asset["url"], headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                final_url = urlsplit(response.geturl()) if hasattr(response, "geturl") else original_url
                if original_url.scheme == "https" and final_url.scheme != "https":
                    raise ValueError("모델 다운로드 HTTPS 리다이렉트가 암호화되지 않은 URL로 변경됐습니다.")
                length = int(response.headers.get("Content-Length", 0))
                status = response.getcode()
                if status == 206:
                    matched = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if (not matched or int(matched[1]) != offset
                            or not offset <= int(matched[2]) < int(matched[3])
                            or (length and length != int(matched[2]) - offset + 1)):
                        raise ValueError(f"이어받기 응답 범위 오류: {destination.name}. 저장된 파일을 보존했습니다.")
                    total = int(matched[3])
                    mode = "ab"
                    print(f"[이어받기] {destination.name}: {offset // 1048576} MiB부터", flush=True)
                elif status == 200:
                    if offset:
                        print(f"[다운로드] 서버가 이어받기를 지원하지 않아 {destination.name}을 처음부터 받습니다.", flush=True)
                    offset, digest, total, mode = 0, hashlib.sha256(), length, "wb"
                else:
                    raise ValueError(f"다운로드 HTTP 응답 오류: {status}. 저장된 파일을 보존했습니다.")
                received, last = offset, time.monotonic()
                declared_size = asset.get("size")
                if declared_size is not None and (type(declared_size) is not int or declared_size <= 0):
                    raise ValueError("모델 자산 size는 양의 정수여야 합니다.")
                maximum_size = min(declared_size or MAX_DOWNLOAD_BYTES, MAX_DOWNLOAD_BYTES)
                with partial.open(mode) as stream:
                    while True:
                        try:
                            chunk = response.read(1024 * 1024)
                        except http.client.IncompleteRead as exc:
                            stream.write(exc.partial)
                            raise
                        if not chunk:
                            break
                        if received + len(chunk) > maximum_size:
                            raise ValueError("다운로드가 고정한 모델 크기를 넘겼습니다. 저장한 부분 파일을 보존했습니다.")
                        stream.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if time.monotonic() - last >= 2:
                            print(f"[다운로드 진행] {destination.name}: {received // 1048576} MiB / {total // 1048576 if total else '?'} MiB", flush=True)
                            last = time.monotonic()
                    stream.flush()
                    os.fsync(stream.fileno())
                if total and received != total:
                    raise ConnectionError(f"응답 조기 종료: {received}/{total} bytes")
            if digest.hexdigest() != expected:
                if not restarted and attempt < 4:
                    _reject_partial(partial, "sha256_mismatch")
                    restarted = True
                    continue
                raise ValueError(f"다운로드 모델 해시 불일치: {destination.name}. 확인용 파일을 보존했습니다: {partial}")
            if declared_size is not None and received != declared_size:
                raise ValueError("다운로드 모델의 고정 크기 불일치. 확인용 부분 파일을 보존했습니다.")
            _publish_download(partial, destination)
            print(f"[다운로드 완료·해시 확인] {destination.name}", flush=True)
            return
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 416 and offset and not restarted and attempt < 4:
                _reject_partial(partial, "http_416")
                restarted = True
                continue
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in {408, 429, 500, 502, 503, 504}:
                raise RuntimeError(f"모델 다운로드 HTTP {exc.code}: {destination.name}. 저장된 파일: {partial}") from exc
            if (isinstance(exc, OSError) and not isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError,
                    ssl.SSLEOFError, ssl.SSLZeroReturnError)) and exc.errno not in
                    {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ECONNABORTED, errno.ETIMEDOUT}):
                raise
            if attempt == 4:
                raise RuntimeError(f"모델 다운로드가 4회 실패했습니다: {destination.name} ({exc}). "
                                   f"저장된 파일: {partial}. 같은 명령을 다시 실행하면 이어받습니다.") from exc
            delay = min(2 ** attempt, 8)
            print(f"[다운로드 재시도] {destination.name}: {exc} · {delay}초 후 재시도 · 저장된 파일 유지", flush=True)
            time.sleep(delay)


def _publish_download(partial, destination):
    """Promote only verified bytes and sync both the file and its directory."""
    import logging
    with partial.open("rb") as source:
        os.fsync(source.fileno())
    partial.replace(destination)
    try:
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        logging.getLogger(__name__).warning("모델 발행 완료, 디렉터리 동기화 미확인: %s", exc)


def _reject_partial(partial, reason):
    """Keep rejected evidence while allowing one bounded request from byte zero."""
    rejected = partial.with_name(partial.name + ".rejected-" + uuid4().hex)
    receipt = {"reason": reason, "original_path": str(partial), "preserved_path": str(rejected),
               "sha256": _digest(partial), "size": partial.stat().st_size}
    partial.replace(rejected)
    _save(rejected.with_name(rejected.name + ".json"), receipt)
    print(f"[다운로드 복구] {reason}: 확인용 파일 {rejected.name} 보존 후 한 번 처음부터 받습니다.", flush=True)


def _model_sources(workspace, relative):
    roots = [Path(value).expanduser() for value in os.environ.get("NANOCOFOLD_MODEL_SOURCE_ROOTS", "").split(os.pathsep) if value]
    candidates = []
    for root in roots:
        candidates.extend([root / relative, root / "models" / relative])
        if relative.startswith("chai1/"):
            suffix = relative.removeprefix("chai1/")
            candidates.extend(path / suffix for path in (root / "envs/chai1/lib").glob("python*/site-packages/downloads"))
        if relative.startswith("boltz2/"):
            candidates.append(Path.home() / ".boltz" / Path(relative).name)
        if relative.startswith("esm/"):
            candidates.append(Path(os.environ.get("TORCH_HOME") or Path.home() / ".cache/torch") / "hub/checkpoints" / Path(relative).name)
    return candidates


def _verify_boltz_molecules(cache, *, verified_index_sha=None, archive_path=None, write_index=True):
    """Every extracted molecule must match the verified tar snapshot."""
    index_path = cache / ".nanocofold-molecules.json"
    archive_path = Path(archive_path) if archive_path else cache / "mols.tar"
    if verified_index_sha and index_path.is_file() and _digest(index_path) == verified_index_sha:
        from .provenance import directory_identity
        saved = json.loads(index_path.read_text())
        if saved.get("verified_tar_sha256") == _digest(archive_path):
            expected = hashlib.sha256()
            for relative, row in sorted(saved["files"].items()):
                expected.update((relative + "\0" + row[-1] + "\n").encode())
            actual = directory_identity(cache / "mols")
            if actual["file_count"] != len(saved["files"]) or actual["sha256"] != expected.hexdigest():
                raise ValueError("Boltz 분자 사전 해시 불일치. 기존 파일을 보존했습니다.")
            return actual["file_count"]
    with tarfile.open(archive_path) as archive:
        count = 0
        index = {}
        for member in archive:
            if not (member.isdir() or member.isfile()) or not Path(member.name).is_relative_to("mols"):
                raise ValueError(f"Boltz 분자 사전의 예상하지 못한 경로/유형: {member.name}")
            if not member.isfile():
                continue
            destination = cache / member.name
            if (not destination.resolve().is_relative_to((cache / "mols").resolve()) or not destination.is_file()
                    or destination.is_symlink()):
                raise ValueError(f"Boltz 분자 사전 누락/경로 오류: {member.name}")
            source = archive.extractfile(member)
            digest = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
            if _digest(destination) != digest.hexdigest():
                raise ValueError(f"Boltz 분자 사전 해시 불일치: {member.name}. 기존 파일을 보존했습니다.")
            info = destination.stat()
            index[str(destination.relative_to(cache / "mols"))] = [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, digest.hexdigest()]
            count += 1
    if not count:
        raise ValueError("Boltz 분자 사전이 비어 있습니다.")
    actual = {str(path.relative_to(cache / "mols")) for path in (cache / "mols").rglob("*") if path.is_file()}
    if actual != set(index):
        raise ValueError("Boltz 분자 사전에 검증한 아카이브와 다른 파일이 있습니다.")
    if write_index:
        _save(cache / ".nanocofold-molecules.json", {"schema_version": 1, "verified_tar_sha256": _digest(archive_path), "files": index})
    return count


def _install_boltz_molecules(cache, *, verified_index_sha=None):
    """Verify a whole staged tree before replacing any incomplete extraction."""
    cache = Path(cache)
    if (cache / "mols").is_symlink():
        raise ValueError("Boltz 분자 사전 심볼릭 링크는 자동으로 변경하지 않습니다.")
    if (cache / "mols").exists():
        try:
            return _verify_boltz_molecules(cache, verified_index_sha=verified_index_sha)
        except (OSError, ValueError, tarfile.TarError):
            # The original tree stays in place until a replacement is complete.
            pass
    stage = Path(tempfile.mkdtemp(prefix=".mols-staging-", dir=cache))
    print("[설치] Boltz 분자 사전 검증·압축 해제", flush=True)
    with tarfile.open(cache / "mols.tar") as archive:
        for member in archive.getmembers():
            if not (member.isdir() or member.isfile()) or not Path(member.name).is_relative_to("mols"):
                raise ValueError(f"Boltz 분자 사전의 예상하지 못한 경로/유형: {member.name}")
        archive.extractall(stage, filter="data")
    _verify_boltz_molecules(stage, archive_path=cache / "mols.tar", write_index=False)
    if (cache / "mols").exists():
        rejected = cache / (".mols-rejected-" + uuid4().hex)
        (cache / "mols").replace(rejected)
        old_index = cache / ".nanocofold-molecules.json"
        if old_index.is_file():
            shutil.copyfile(old_index, rejected / ".nanocofold-molecules.json")
    (stage / "mols").replace(cache / "mols")
    stage.rmdir()
    return _verify_boltz_molecules(cache)


def _merge_environment(workspace, updates):
    import yaml
    from .config import _deep_merge
    path = workspace / "config/environment.yaml"
    existing = yaml.safe_load(path.read_text()) if path.exists() else {}
    if not isinstance(existing or {}, dict):
        raise ValueError("config/environment.yaml 형식이 잘못되었습니다.")
    merged = _deep_merge(existing or {}, updates)
    from .utils.atomic import write_bytes
    write_bytes(path, yaml.safe_dump(merged, allow_unicode=True, sort_keys=False).encode("utf-8"))


def install_model_assets(workspace, engines):
    """Install curated public snapshots; these hashes are local provenance, not publisher signatures."""
    workspace = Path(workspace).resolve()
    manifest = json.loads((_package_root() / "_data/model-assets.json").read_text())
    items = manifest["assets"]
    installed = []
    updates = {}
    evidence_path = workspace / ".platform/model-assets.json"
    previous_evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
    support_indexes = previous_evidence.get("support_indexes", {})
    for engine in engines:
        if engine == "opendde":
            from .opendde import assets as opendde_assets
            cache = workspace / "models/opendde"
            for relative, asset in opendde_assets().items():
                destination = cache / relative
                _download(asset, destination, reuse_paths=_model_sources(workspace, "opendde/" + relative))
                installed.append({"engine": engine, "path": str(destination), **asset})
            updates.setdefault("screen", {}).setdefault("opendde", {})["data_dir"] = str(cache)
        elif engine == "boltzgen":
            cache = workspace / "models/boltzgen"
            for key, asset in items.items():
                if asset.get("engine") != "boltzgen":
                    continue
                destination = cache / asset["relative_path"]
                _download(asset, destination, reuse_paths=_model_sources(workspace, f"boltzgen/{asset['relative_path']}"))
                installed.append({"engine": engine, "path": str(destination), **asset})
            updates.setdefault("design", {}).setdefault("boltzgen", {})["cache_dir"] = str(cache)
        elif engine == "colabfold":
            cache = workspace / "models/colabfold"
            key = "alphafold_params_colab_2022-12-06.tar"
            _download(items[key], cache / key, reuse_paths=_model_sources(workspace, f"colabfold/{key}"))
            installed.append({"engine": engine, "path": str(cache / key), **items[key]})
            params = cache / "params"
            params.mkdir(parents=True, exist_ok=True)
            files = {}
            with tarfile.open(cache / key) as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    if not re.fullmatch(r"LICENSE|params_model_[1-5](?:_ptm|_multimer_v3)?.npz", member.name):
                        raise ValueError("ColabFold 가중치 아카이브에 예상하지 못한 경로가 있습니다.")
                    if not re.fullmatch(r"params_model_[1-5]_multimer_v3.npz", member.name):
                        continue
                    destination = params / member.name
                    stream = archive.extractfile(member)
                    digest = hashlib.sha256()
                    if destination.exists():
                        while chunk := stream.read(1024 * 1024):
                            digest.update(chunk)
                        if _digest(destination) != digest.hexdigest():
                            raise ValueError("ColabFold 기존 가중치 해시 불일치. 파일은 보존했습니다.")
                    else:
                        partial = destination.with_suffix(".partial")
                        with partial.open("wb") as out:
                            while chunk := stream.read(1024 * 1024):
                                digest.update(chunk)
                                out.write(chunk)
                        os.replace(partial, destination)
                    files[member.name] = digest.hexdigest()
            if set(files) != {f"params_model_{i}_multimer_v3.npz" for i in range(1, 6)}:
                raise ValueError("ColabFold 5개 Multimer-v3 가중치가 모두 필요합니다.")
            _save(cache / "weights.json", {"archive_sha256": items[key]["sha256"], "files": files})
            (params / "download_complexes_multimer_v3_finished.txt").touch()
            updates.setdefault("screen", {}).setdefault("colabfold", {})["data_dir"] = str(cache)
        elif engine == "boltz2":
            cache = workspace / "models/boltz2"
            for key in ("boltz2_conf.ckpt", "boltz2_aff.ckpt", "mols.tar"):
                _download(items[key], cache / key, reuse_paths=_model_sources(workspace, f"boltz2/{key}"))
                installed.append({"engine": engine, "path": str(cache / key), **items[key]})
            index_path = cache / ".nanocofold-molecules.json"
            _install_boltz_molecules(cache, verified_index_sha=support_indexes.get(str(index_path), {}).get("sha256"))
            support_indexes[str(index_path)] = {"sha256": _digest(index_path), "verified_tar_sha256": items["mols.tar"]["sha256"]}
            updates.setdefault("screen", {}).setdefault("env_vars", {})["BOLTZ_CACHE"] = str(cache)
            updates.setdefault("structure", {}).setdefault("boltz2", {}).setdefault("env_vars", {})["BOLTZ_CACHE"] = str(cache)
        elif engine == "chai1":
            required = [key for key, value in items.items() if value["engine"] == "chai1"]
            cache = workspace / "models/chai1"
            for key in required:
                destination = cache / items[key]["relative_path"]
                _download(items[key], destination, reuse_paths=_model_sources(workspace, "chai1/" + items[key]["relative_path"]))
                installed.append({"engine": engine, "path": str(destination), **items[key]})
            updates.setdefault("screen", {}).setdefault("env_vars", {})["CHAI_DOWNLOADS_DIR"] = str(cache)
            updates.setdefault("structure", {}).setdefault("chai1", {}).setdefault("env_vars", {})["CHAI_DOWNLOADS_DIR"] = str(cache)
        elif engine == "esm":
            torch_home = workspace / "models/esm"
            cache = torch_home / "hub/checkpoints"
            for key in ("esm2_t33_650M_UR50D.pt", "esm2_t33_650M_UR50D-contact-regression.pt"):
                _download(items[key], cache / key, reuse_paths=_model_sources(workspace, f"esm/{key}"))
                installed.append({"engine": engine, "path": str(cache / key), **items[key]})
            updates.setdefault("mature", {}).setdefault("esm", {}).setdefault("env_vars", {})["TORCH_HOME"] = str(torch_home)
        elif engine == "antifold":
            destination = workspace / "models/antifold/model.pt"
            _download(items["antifold"], destination, reuse_paths=_model_sources(workspace, "antifold/model.pt"))
            installed.append({"engine": engine, "path": str(destination), **items["antifold"]})
            updates.setdefault("mature", {})["antifold"] = {"model_path": str(destination), "sha256": items["antifold"]["sha256"]}
    if updates:
        _merge_environment(workspace, updates)
    previous = previous_evidence.get("assets", [])
    by_path = {asset["path"]: asset for asset in previous + installed}
    _save(evidence_path, {"schema_version": 1, "hash_source": manifest["hash_source"], "assets": list(by_path.values()), "support_indexes": support_indexes})
    return installed


def _validate_environment(workspace, engine):
    from .utils import shell
    python = workspace / "envs" / engine / "bin/python"
    result = shell._budgeted_process([str(python), "-c", "import importlib.metadata as m,json; print(json.dumps({d.metadata['Name']:d.version for d in m.distributions() if d.metadata.get('Name')},sort_keys=True))"],
                                     cwd=None, env=None, timeout=shell.remaining_budget(OFFLINE_IMPORT_TIMEOUT))
    if result.returncode:
        raise ValueError(f"{engine} 설치 메타데이터 점검 실패(exit {result.returncode}). 기존 환경을 보존했습니다: " + (result.stdout or "")[-2048:])
    from .diagnostics import _loads
    installed = _loads(result.stdout)
    if (installed.get("parse_error") or any(not isinstance(name, str) or not isinstance(version, str)
                                           for name, version in installed.items())):
        raise ValueError(f"{engine} 설치 메타데이터 JSON이 유효하지 않습니다. 기존 환경을 보존했습니다.")
    normalized = {re.sub(r"[-_.]+", "-", name).lower(): version for name, version in installed.items()}
    source = _package_root() / "_data/locks" / f"{engine}-source.json"
    expected = json.loads(source.read_text())["packages"]
    mismatch = [f"{item['name']}=={item['version']} (installed: {normalized.get(re.sub(r'[-_.]+', '-', item['name']).lower(), 'missing')})" for item in expected if normalized.get(re.sub(r"[-_.]+", "-", item["name"]).lower()) != item["version"]]
    if mismatch:
        raise ValueError(f"{engine} dependency lock 불일치. 기존 환경은 보존했습니다: " + "; ".join(mismatch))
    if engine == "opendde":
        from .opendde import installation_identity
        identity = installation_identity(workspace / "envs/opendde")
        if not identity["ok"]:
            raise ValueError("OpenDDE 고정 소스 설치 정체성 불일치: " + str(identity))
    _save(workspace / ".platform" / f"environment-{engine}.json", installed)
    return installed


def _offline_readiness(workspace, engines):
    """Import/kernel/binary/model checks with network connections disabled."""
    import yaml
    from . import diagnostics
    from .config import _deep_merge
    from .utils import shell
    cfg = _deep_merge(yaml.safe_load((workspace / "config/default.yaml").read_text()), yaml.safe_load((workspace / "config/environment.yaml").read_text()) or {})
    reports = {}
    for engine in engines:
        if engine == "colabfold":
            env = workspace / "envs/colabfold"
            reports[engine] = diagnostics._probe_jax(env, diagnostics._engine_extra_env(cfg, engine), offline=True)
            reports[engine]["binaries"] = [diagnostics._probe_binary(env, "colabfold_batch", extra_env=diagnostics._engine_extra_env(cfg, engine))]
            reports[engine]["weights"] = diagnostics._engine_weights(cfg, engine, env)
            reports[engine]["headless_plot"] = diagnostics._probe_headless_plot(env, diagnostics._engine_extra_env(cfg, engine))
            reports[engine]["ok"] = reports[engine]["ok"] and reports[engine]["headless_plot"]["ok"] and reports[engine]["weights"]["ok"] and all(item["ok"] for item in reports[engine]["binaries"])
            _save(workspace / ".platform/offline-readiness.json", reports)
            if not reports[engine]["ok"]:
                raise ValueError("ColabFold JAX CUDA·그래프·가중치 점검 실패. .platform/offline-readiness.json을 확인하세요.")
            continue
        selected = "anarci" if engine == "antibody" else engine
        for name in ([selected, "antifold"] if engine == "esm" else [selected]):
            spec = diagnostics._engine_spec(cfg, name)
            env = workspace / "envs" / engine
            imports = list(spec["imports"])
            if engine == "antibody":
                imports.extend(["ImmuneBuilder", "openmm", "pdbfixer"])
            code = """import socket, importlib, json
def denied(*args, **kwargs):
    raise RuntimeError('network disabled during offline readiness')
socket.socket.connect = denied
socket.create_connection = denied
for name in IMPORTS:
    importlib.import_module(name)
import torch
report = {'ok': True, 'network_disabled': True, 'torch': torch.__version__}
if REQUIRE_CUDA:
    assert torch.cuda.is_available(), 'CUDA unavailable'
    x = torch.ones((8, 8), device='cuda')
    assert torch.isfinite(x @ x).all().item()
    torch.cuda.synchronize()
    report['device'] = torch.cuda.get_device_name(0)
print(json.dumps(report))
""".replace("IMPORTS", repr(imports)).replace("REQUIRE_CUDA", repr(bool(spec.get("requires_torch_kernel"))))
            print(f"[환경 점검] {name} · 패키지 import·CUDA 확인 (최대 {OFFLINE_IMPORT_TIMEOUT}초)", flush=True)
            try:
                process = shell._budgeted_process([str(env / "bin/python"), "-c", code], cwd=None,
                    env=diagnostics._env_process_env(env, diagnostics._engine_extra_env(cfg, name)),
                    timeout=shell.remaining_budget(OFFLINE_IMPORT_TIMEOUT))
            except subprocess.TimeoutExpired as exc:
                shell.remaining_budget()
                def tail(value):
                    return (value.decode(errors="replace") if isinstance(value, bytes) else value or "")[-8192:]
                reports[name] = {"ok": False, "status": "timeout", "timeout_seconds": OFFLINE_IMPORT_TIMEOUT,
                                 "stdout": tail(exc.stdout), "stderr": tail(exc.stderr)}
                _save(workspace / ".platform/offline-readiness.json", reports)
                raise ValueError(f"{name} 패키지 import·CUDA 점검이 {OFFLINE_IMPORT_TIMEOUT}초를 넘겼습니다. "
                                 ".platform/offline-readiness.json에 실패 기록을 보존했습니다.") from exc
            report = {"ok": process.returncode == 0, "returncode": process.returncode,
                      "stdout": (process.stdout or "")[-8192:], "stderr": (getattr(process, "stderr", None) or "")[-8192:]}
            report["binaries"] = [diagnostics._probe_binary(env, binary, extra_env=diagnostics._engine_extra_env(cfg, name)) for binary in spec.get("binaries", [])]
            report["weights"] = diagnostics._engine_weights(cfg, name, env)
            report["ok"] = report["ok"] and all(item["ok"] for item in report["binaries"]) and report["weights"]["ok"]
            if spec.get("requires_headless_plot"):
                report["headless_plot"] = diagnostics._probe_headless_plot(env, diagnostics._engine_extra_env(cfg, name))
                report["ok"] = report["ok"] and report["headless_plot"]["ok"]
            reports[name] = report
            _save(workspace / ".platform/offline-readiness.json", reports)
            if not report["ok"]:
                raise ValueError(f"{name} offline 실행 준비 실패. .platform/offline-readiness.json과 설치 로그를 확인하세요.")
    return reports


def setup_profile(workspace, profile="core"):
    if profile not in PROFILES:
        raise ValueError("알 수 없는 설치 프로필입니다.")
    workspace = Path(workspace).expanduser().resolve()
    if profile != "core" and (platform.system() != "Linux" or platform.machine() != "x86_64"):
        raise ValueError("모델 설치는 Linux x86_64 NVIDIA 호스트에서 지원합니다. 이 PC에서는 브라우저로 계산 호스트에 접속하세요.")
    from .state import RunLock
    from .utils import shell
    with RunLock(workspace / ".platform/install"):
        init_workspace(workspace, refresh_managed=True)
        updates = {}
        for engine in PROFILES[profile]:
            env = workspace / "envs" / engine
            python = env / "bin/python"
            marker = workspace / ".platform" / f"managed-install-{engine}.json"
            needs_install = not python.exists()
            if python.exists():
                print(f"[기존 환경 보존] {engine}", flush=True)
                try:
                    _validate_environment(workspace, engine)
                except ValueError:
                    managed = json.loads(marker.read_text()) if marker.exists() else {}
                    if managed.get("workspace") != str(workspace) or managed.get("engine") != engine or managed.get("status") == "complete":
                        raise
                    print(f"[중단된 설치 재개] {engine}", flush=True)
                    needs_install = True
            if needs_install:
                if not shutil.which("uv"):
                    raise ValueError("uv가 필요합니다. 설치 안내의 bootstrap 명령을 먼저 실행하세요.")
                script = workspace / "envs" / f"setup_{engine}.sh"
                if not script.exists():
                    raise FileNotFoundError(f"설치 스크립트 없음: {script}")
                _save(marker, {"workspace": str(workspace), "engine": engine, "status": "installing"})
                shell.run(["bash", str(script)], cwd=workspace, log_path=workspace / ".platform" / f"install-{engine}.log")
            _validate_environment(workspace, engine)
            # Reject missing/broken plotting dependencies before large model downloads.
            if engine in {"colabfold", "chai1", "boltzgen"}:
                from . import diagnostics
                plot = diagnostics._probe_headless_plot(env, {})
                _save(workspace / ".platform" / f"headless-plot-{engine}.json", plot)
                if not plot["ok"]:
                    raise ValueError(f"{engine} 그래프 저장 점검 실패. .platform/headless-plot-{engine}.json을 확인하세요. 모델은 다운로드하지 않았습니다.")
            if engine == "antibody":
                updates.setdefault("mature", {}).setdefault("anarci", {})["env"] = str(env)
            elif engine == "rfantibody":
                updates.setdefault("design", {}).update({"env": str(env), "framework": str(workspace / "data/frameworks/h-NbBCII10.pdb"), "env_vars": {"RFANTIBODY_ROOT": str(workspace / "tools/RFantibody"), "RFANTIBODY_WEIGHTS": str(workspace / "tools/RFantibody/weights")}})
            elif engine == "esm":
                for name in ("esm", "antifold"):
                    updates.setdefault("mature", {}).setdefault(name, {})["env"] = str(env)
            elif engine == "colabfold":
                updates.setdefault("screen", {}).setdefault("colabfold", {})["env"] = str(env)
            elif engine == "opendde":
                updates.setdefault("screen", {}).setdefault("opendde", {})["env"] = str(env)
            elif engine == "boltzgen":
                updates.setdefault("design", {}).setdefault("boltzgen", {})["env"] = str(env)
            else:
                updates.setdefault("structure", {}).setdefault(engine, {})["env"] = str(env)
                updates.setdefault("screen", {})["boltz2_env" if engine == "boltz2" else "env"] = str(env)
        if updates:
            _merge_environment(workspace, updates)
        engines = [e for e in PROFILES[profile] if e in {"boltz2", "chai1", "esm", "colabfold", "boltzgen", "opendde"}]
        if "esm" in engines:
            engines.append("antifold")
        assets = install_model_assets(workspace, engines) if engines else []
        readiness = _offline_readiness(workspace, PROFILES[profile]) if PROFILES[profile] else {}
        _save(workspace / ".platform" / f"offline-readiness-{profile}.json", readiness)
        for engine in PROFILES[profile]:
            _save(workspace / ".platform" / f"managed-install-{engine}.json", {"workspace": str(workspace), "engine": engine, "status": "complete"})
        scope = "CPU core 준비 완료. 브라우저에서 모델 없이 데모를 실행할 수 있습니다." if profile == "core" else "의존성 lock, 모델 해시, 오프라인 import/CUDA 준비 확인 완료. 실제 분석은 선택한 입력의 환경 점검으로 확인합니다."
        result = {"profile": profile, "workspace": str(workspace), "status": "complete", "engines": list(PROFILES[profile]), "verified_model_files": len(assets), "offline_ready": all(item["ok"] for item in readiness.values()), "scope": scope}
        _save(workspace / ".platform" / f"setup-{profile}.json", result)
        print("[설치 완료] " + profile, flush=True)
        return result
