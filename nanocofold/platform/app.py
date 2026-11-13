from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.background import BackgroundTask

from ..config import ASSET_ROOT, _deep_merge, cfg_get, exploration_config, read_config_yaml
from .inputs import InputManager, normalized_config
from .jobs import JobManager
from .paths import contained, native
from .results import ResultReader, TABLES

MAX_REQUEST = 20 * 1024 * 1024


def planning_defaults(workspace: Path) -> dict:
    """Display-only defaults from the same configuration used by input review.

    Never return paths, environment variables, sequences or credentials here.
    The browser still submits its explicit overrides, not these display values.
    """
    cfg = normalized_config(workspace)
    result = {}
    for dotted in (
        "screen.colabfold.msa_source", "screen.msa_mode", "structure.provider", "structure.chai1.msa_mode",
        "structure.msa.backend", "complex.msa_source",
        "run.seed", "score.boltz2.seed", "screen.opendde.samples",
        "design.engine", "design.num_designs", "design.mpnn.seqs_per_struct",
        "design.boltzgen.inverse_fold_num_sequences", "design.filters.top_n_to_score",
        "design.refinement.enabled", "screen.engine", "screen.tier2_n",
        "screen.comparison.enabled", "screen.comparison.engines", "screen.cascade.enabled",
        "screen.num_diffn_samples", "screen.colabfold.num_models", "screen.colabfold.num_seeds",
        "score.boltz2_diffusion_samples", "mature.prefilter.top_n",
        "mature.library.max_variants", "rank.final_n", "rank.include_wt_control",
    ):
        target = result
        parts = dotted.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = cfg_get(cfg, dotted)
    return result


class ExportFileResponse(FileResponse):
    """A cancelled/disconnected file send also releases its private spool."""
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with contextlib.suppress(OSError):
                Path(self.path).unlink(missing_ok=True)


def create_app(workspace: str | Path, *, start_workers: bool = True) -> FastAPI:
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    inputs = InputManager(workspace)
    manager = JobManager(workspace)
    from .batches import BatchManager
    batches = BatchManager(inputs, manager)
    from .lab import LabAccounts
    lab = LabAccounts(workspace)
    token = secrets.token_urlsafe(32)
    password_notice = secrets.token_urlsafe(24)  # One-time browser notice; never used for authentication.
    notice_cookie = lab.cookie + "_changed"
    login_gate = asyncio.Lock()
    pending_password_checks = 0  # Shared by login and password changes; owned by the event loop.

    @contextlib.asynccontextmanager
    async def password_check_slot():
        nonlocal pending_password_checks
        if pending_password_checks >= 8:
            yield False
            return
        pending_password_checks += 1
        try:
            async with login_gate:
                yield True
        finally:
            pending_password_checks -= 1

    account_inputs = {}
    account_lock = threading.Lock()

    def input_manager(request):
        owner = request.state.researcher
        if not owner:
            return inputs
        with account_lock:
            if owner not in account_inputs:
                folder = workspace / "inputs" / "lab" / hashlib.sha256(owner.encode()).hexdigest()
                account_inputs[owner] = InputManager(workspace, root=folder)
            return account_inputs[owner]

    def read_owner(request, requested=""):
        return request.state.owner or requested

    def result_access(request, identifier):
        owner = request.state.owner
        if not owner:
            return
        with manager.store.connect() as db:
            if db.execute("SELECT 1 FROM lab_usage WHERE researcher=? AND (result_id=? OR job_id=?) LIMIT 1",
                          (owner,identifier,identifier)).fetchone():
                return
        raise HTTPException(404)

    with manager.store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS result_paths (id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE)")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        from .maintenance import maintenance_lock
        try:
            with maintenance_lock(workspace):
                try:
                    if start_workers:
                        manager.start()
                    yield
                finally:
                    manager.close()
        finally:
            inputs.close()
            for personal in account_inputs.values():
                personal.close()

    app = FastAPI(title="NanoCoFold", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.state.workspace, app.state.jobs, app.state.inputs, app.state.token = workspace, manager, inputs, token

    @app.middleware("http")
    async def local_gate(request: Request, call_next):
        def secure(response):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; worker-src 'self' blob:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            return response
        if len(request.headers.getlist("host")) != 1:
            return secure(JSONResponse({"error": "Host 헤더는 하나여야 합니다."}, status_code=403))
        host = request.headers.get("host", "")
        parsed = urlsplit("http://" + host)
        origin = request.headers.get("origin")
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or (origin is not None and origin != "http://" + host):
            return secure(JSONResponse({"error": "loopback 주소 또는 SSH 포트포워딩으로 접속하세요."}, status_code=403))
        request.state.researcher = None
        request.state.admin = request.state.shared = False
        request.state.owner = None
        request.state.api_token = token
        if lab.enabled and not lab.db.is_file():
            return secure(JSONResponse({"error":"연구실 계정 DB를 복원하세요. / Restore the lab account database."}, status_code=503))
        if lab.enabled and request.url.path not in {"/lab/login", "/favicon.ico"}:
            session_key = request.cookies.get(lab.cookie, "")
            access = await asyncio.to_thread(lab.access, session_key)
            if access is None:
                if request.url.path == "/" and request.method == "GET":
                    changed = secrets.compare_digest(request.cookies.get(notice_cookie, "").encode(), password_notice.encode())
                    response = login_page(changed=changed)
                    if changed:
                        response.delete_cookie(notice_cookie, httponly=True, samesite="strict")
                    return secure(response)
                return secure(JSONResponse({"error":"연구실 로그인이 필요합니다. / Please sign in to the lab."}, status_code=401))
            researcher, request.state.admin, request.state.shared, pending = access
            request.state.researcher = researcher
            if pending and request.url.path not in {"/lab/activate","/api/lab/logout"}:
                if request.url.path == "/":
                    return secure(activation_page())
                return secure(JSONResponse({"error":"Activate the administrator account first."},status_code=403))
            if not request.state.admin and not request.state.shared:
                request.state.owner = researcher
            import hmac
            request.state.api_token = hmac.digest(token.encode(), session_key.encode(), "sha256").hex()
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-platform-token", request.headers.get("x-wizard-token", ""))
            if not secrets.compare_digest(supplied.encode(), request.state.api_token.encode()):
                return secure(JSONResponse({"error": "세션이 만료되었습니다. 화면을 다시 여세요."}, status_code=403))
        if request.method in {"POST", "PUT", "PATCH"}:
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                size = MAX_REQUEST + 1
            if size < 0 or size > MAX_REQUEST:
                return secure(JSONResponse({"error": "요청은 20MB 이하여야 합니다."}, status_code=413))
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; worker-src 'self' blob:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return secure(response)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        return JSONResponse({"error": "요청 인자 형식이 올바르지 않습니다. 숫자·범위·필수 항목을 확인하고 다시 시도하세요."}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        from .errors import public_error
        import logging
        logging.getLogger(__name__).warning("input error: %s", exc)
        stage = "input" if _request.url.path.startswith("/api/inputs") else "operation"
        return JSONResponse({"error": public_error(exc, stage=stage)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing(_request, exc):
        return JSONResponse({"error": "요청한 입력 또는 결과 파일을 찾지 못했습니다. 작업 목록에서 항목을 다시 선택하세요."}, status_code=404)

    @app.exception_handler(KeyError)
    async def unknown(_request, exc):
        return JSONResponse({"error": "저장된 항목을 찾지 못했습니다. 입력을 다시 검토하거나 작업 목록을 새로 확인하세요."}, status_code=404)

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.OperationalError)
    async def storage_error(request, exc):
        import logging
        logging.getLogger(__name__).error("storage error during %s: %s", request.url.path, exc)
        database_code = getattr(exc, "sqlite_errorcode", 0) & 255
        database_message = str(exc).lower() if isinstance(exc, sqlite3.OperationalError) else ""
        if (getattr(exc, "errno", None) in {errno.ENOSPC, errno.EDQUOT}
                or database_code == sqlite3.SQLITE_FULL or database_message == "database or disk is full"):
            code, status = "storage_full", 507
            message = "저장 공간이 부족해 요청을 마치지 못했습니다. 서버의 여유 공간을 확보한 뒤 작업·로그를 새로고침해 현재 상태를 확인하세요."
        elif (getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM, errno.EROFS}
                or database_code == sqlite3.SQLITE_READONLY):
            code, status = "storage_permission", 503
            message = "작업 공간의 읽기·쓰기 권한을 확인할 수 없습니다. 서버의 파일 권한을 확인한 뒤 작업·로그를 새로고침해 현재 상태를 확인하세요."
        elif database_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or database_message in {"database is locked", "database table is locked"}:
            code, status = "storage_busy", 503
            message = "다른 저장 요청을 처리 중입니다. 잠시 후 작업·로그를 새로고침해 현재 상태를 확인하세요."
        else:
            code, status = "storage_error", 503
            message = "저장소 요청을 마치지 못했습니다. 서버 로그를 확인한 뒤 작업·로그를 새로고침해 현재 상태를 확인하세요."
        return JSONResponse({"error": message, "code": code}, status_code=status)

    import yaml

    @app.exception_handler(yaml.YAMLError)
    async def yaml_error(_request, exc):
        import logging
        logging.getLogger(__name__).warning("YAML input error: %s", exc)
        return JSONResponse({"error": "YAML 설정 구문을 읽지 못했습니다. 들여쓰기·키와 값 형식을 확인하세요."}, status_code=400)

    def lab_payload_paths(request, payload):
        """Lab users upload scientific files. Server filenames belong to the operator."""
        if not request.state.researcher:
            return
        from .inputs import INPUT_PATHS, RUNTIME_PATHS
        from ..config import load_config
        personal = input_manager(request).root
        trusted = None
        def check_config(config):
            nonlocal trusted
            if not isinstance(config,dict):
                return
            for name in (*INPUT_PATHS,"input_review.residue_mapping_path","input_review.mapping_path"):
                value = cfg_get(config,name)
                if value and (not isinstance(value,str) or not contained(workspace,value).is_relative_to(personal)):
                    raise HTTPException(403,"Upload input files with your own account.")
            for name in (*RUNTIME_PATHS,"screen.colabfold.msa_dir","screen.colabfold.msa_cache_dir","structure.msa.cache_dir"):
                value=cfg_get(config,name)
                if not value:
                    continue
                if trusted is None:
                    default=workspace/"config/default.yaml"
                    trusted=load_config(default if default.is_file() else None)
                    env=workspace/"config/environment.yaml"
                    if env.is_file():
                        trusted=_deep_merge(trusted,read_config_yaml(env.read_text()) or {})
                expected=cfg_get(trusted,name)
                if not isinstance(value,str) or not isinstance(expected,str) or (workspace/value).resolve()!=(workspace/expected).resolve():
                    raise HTTPException(403,"Use a runtime location configured by the server administrator.")
        def visit(value):
            if isinstance(value, list):
                for item in value: visit(item)
            elif isinstance(value,dict):
                if value.get("config_path"):
                    path = contained(workspace,value["config_path"])
                    if not path.is_relative_to(personal):
                        raise HTTPException(403,"Use a configuration from your own reviewed input.")
                check_config(value.get("config"))
                if "settings" in value:
                    check_config({"complex": value["settings"]})
                for nested in value.values():
                    if isinstance(nested,(list,dict)): visit(nested)
        visit(payload)

    async def body(request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "JSON 요청만 지원합니다.")
        chunks, length = [], 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > MAX_REQUEST:
                raise HTTPException(413, "요청은 20MB 이하여야 합니다.")
            chunks.append(chunk)
        content = b"".join(chunks)
        try:
            payload = json.loads(content or b"{}")
        except ValueError:
            raise ValueError("JSON 입력을 읽지 못했습니다.")
        if not isinstance(payload, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        if "config" in payload and not isinstance(payload["config"], dict):
            raise ValueError("config는 JSON 객체여야 합니다.")
        for key in ("provider", "mode", "msa_backend", "preparation", "input_id", "selection_id", "config_path", "name", "target_name"):
            if key in payload and payload[key] is not None and not isinstance(payload[key], str):
                raise ValueError(f"{key} 입력은 문자열이어야 합니다.")
        for key in ("settings", "reference", "wt_structure"):
            if key in payload and payload[key] is not None and not isinstance(payload[key], dict):
                raise ValueError(f"{key} 입력은 JSON 객체여야 합니다.")
        if "mock" in payload and type(payload["mock"]) is not bool:
            raise ValueError("mock는 true/false여야 합니다.")
        if request.url.path.startswith(("/api/jobs","/api/inputs","/api/batches","/api/diagnostics","/api/environment","/api/configs")):
            lab_payload_paths(request,payload)
        return payload

    @app.post("/api/inputs/complex")
    async def complex_input(request: Request):
        return await asyncio.to_thread(input_manager(request).complex, await body(request))

    @app.get("/api/starter-presets")
    async def starter_presets():
        return json.loads((Path(__file__).parents[1] / "_data/starter-presets.json").read_text())

    @app.get("/api/capabilities")
    async def capabilities():
        from .cloud_agent import Agent
        return Agent.capabilities()

    def draft_path(request):
        if request.state.researcher:
            key = hashlib.sha256(request.state.researcher.encode()).hexdigest()
            return manager.store.root / ("draft-" + key + ".json")
        return workspace / "platform_draft_v1.json"

    @app.get("/api/draft")
    async def draft_read(request: Request):
        path = draft_path(request)
        if path.is_symlink():
            raise ValueError("초안 파일은 심볼릭 링크일 수 없습니다.")
        return json.loads(path.read_text()) if path.exists() else {"version": 1, "fields": {}}

    @app.post("/api/draft")
    async def draft_save(request: Request):
        from ..utils.atomic import write_json
        value = await body(request)
        if value.get("version") != 1 or not isinstance(value.get("fields"), dict):
            raise ValueError("초안 버전 또는 입력 형식 오류")
        path = draft_path(request)
        if path.is_symlink():
            raise ValueError("초안 파일은 심볼릭 링크일 수 없습니다.")
        await asyncio.to_thread(write_json, path, value)
        return {"saved": True, "version": 1}

    @app.get("/api/batches")
    def batch_list(request: Request):
        return {"batches": batches.list(request.state.owner)}

    @app.post("/api/batches/review")
    async def batch_review(request: Request):
        batch_manager = BatchManager(input_manager(request),manager) if request.state.researcher else batches
        return await asyncio.to_thread(batch_manager.review, await body(request), researcher=request.state.researcher)

    @app.get("/api/batches/{identifier}")
    def batch_get(identifier: str, request: Request):
        batch = batches.get(identifier)
        if request.state.owner and batch.get("researcher") != request.state.owner:
            raise HTTPException(404)
        return batch

    @app.post("/api/batches/{identifier}/submit")
    async def batch_submit(identifier: str, request: Request):
        batch_manager = BatchManager(input_manager(request),manager) if request.state.researcher else batches
        return await asyncio.to_thread(batch_manager.submit, identifier, await body(request),
                                       researcher=request.state.researcher, legacy_inputs=inputs)

    @app.post("/api/batches/{identifier}/cancel")
    async def batch_cancel(identifier: str, request: Request):
        payload = await body(request) if await request.body() else {}
        return await asyncio.to_thread(batches.cancel, identifier, expected_cancel=payload.get("expected_cancel"), researcher=request.state.researcher)

    def effective(request, payload):
        cfg = payload.get("config", {})
        reviewed = None
        if payload.get("input_id"):
            reviewed = input_manager(request).reviewed(payload["input_id"])
            cfg = _deep_merge(reviewed["config"], cfg)
        if payload.get("config_path"):
            path = contained(workspace, payload["config_path"], exists=True)
            if not path.is_file():
                raise ValueError("config_path에는 폴더가 아닌 YAML 설정 파일을 지정하세요.")
            config = read_config_yaml(path.read_text())
            if not isinstance(config, dict):
                raise ValueError("설정 파일은 YAML 객체여야 합니다.")
            cfg = _deep_merge(config, cfg)
        if reviewed is not None:
            # A config may be supplied again for compatibility, but submitting
            # an input_id must execute the exact scientifically reviewed setup.
            # Name, GPU, time budget and reuse choices remain job metadata.
            requested = _deep_merge({}, cfg)
            original = _deep_merge({}, reviewed["config"])
            if not isinstance(requested.get("run"), dict):
                raise ValueError("검토한 run 설정은 객체여야 합니다. 입력을 다시 검토하세요.")
            for key in ("mode", "mock"):
                if key in payload:
                    requested.setdefault("run", {})[key] = payload[key]
            requested.get("run", {}).pop("name", None)
            original.get("run", {}).pop("name", None)
            if inputs._digest(requested) != inputs._digest(original):
                raise ValueError("검토한 입력과 실행 조건이 달라졌습니다. 변경한 조건으로 입력을 다시 검토하세요.")
        result = dict(payload)
        result["config"] = cfg
        return result

    def result_directory(value):
        path = contained(workspace, value, exists=True)
        parts = path.relative_to(workspace).parts
        # Internal workspace trees and collection roots are not individual
        # results, even when an old result_paths alias points at them.
        internal_trees = {"config", "envs", "inputs", "nanocofold", "prepared_targets"}
        private_trees = {".nanocofold", ".platform", ".cache", ".git"}
        containers = {"runs", "models", "data", "assets"}
        if (not path.is_dir() or not parts or parts[0] in private_trees or parts[0].startswith(".venv")
                or parts[0] in internal_trees or (len(parts) == 1 and parts[0] in containers)):
            raise ValueError("작업 공간·설치·입력 폴더는 결과가 아닙니다. 개별 분석 결과 폴더를 선택하세요.")
        # Legacy table-only and partial runs remain readable; a parent folder
        # containing other runs cannot qualify through a recursive search.
        markers = ("run_config.yaml", "run_state.json", "platform_status.json", "complex_results.json",
                   "models.csv", "summary.json", "result.json", "retained/manifest.json", *TABLES)
        if not any(contained(path, marker).is_file() for marker in markers):
            raise ValueError("분석 결과 파일이 없는 폴더입니다. 개별 분석 결과 폴더를 선택하세요.")
        return path

    def reader(identifier, request):
        result_access(request,identifier)
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier)
                or identifier in {".", ".."} or ".." in identifier):
            raise ValueError("결과 ID는 작업 디렉터리의 단일 이름이어야 합니다.")
        with manager.store.connect() as db:
            entry = db.execute("SELECT path FROM result_paths WHERE id=?", (identifier,)).fetchone()
        path = entry["path"] if entry else workspace / "runs" / identifier
        if not entry:
            with contextlib.suppress(KeyError):
                indexed = manager.catalog.get(identifier)
                path = indexed["run_root"]
            with contextlib.suppress(KeyError):
                job = manager.store.get(identifier)
                if job.get("reused_result_id"):
                    path = job["run_dir"]
        path = result_directory(path)
        return ResultReader(path)

    def login_page(failed=False, *, busy=False, changed=False):
        template = Path(__file__).resolve().parents[1] / "templates/lab_login.html"
        error = '<p role="alert">계정과 비밀번호를 확인하세요. / Check your account and password.</p>' if failed else ""
        headers = {"Referrer-Policy": "same-origin"}
        if changed:
            error = '<p role="status">비밀번호를 변경했습니다. 새 비밀번호로 로그인하세요.<br><span lang="en">Password changed. Sign in with your new password.</span></p>'
        if busy:
            error = '<p role="alert">로그인이 몰렸습니다. 잠시 뒤 다시 로그인하세요. / Sign-in is busy. Please try again shortly.</p>'
            headers["Retry-After"] = "3"
        return HTMLResponse(template.read_text().replace("<!--LOGIN_ERROR-->", error),
                            status_code=429 if busy else 401 if failed else 200, headers=headers)

    def activation_page(failed=False):
        page = (Path(__file__).resolve().parents[1] / "templates/lab_activate.html").read_text()
        return HTMLResponse(page.replace("<!--ERROR-->", '<p role="alert">코드 또는 비밀번호를 확인하세요. / Check the code and password.</p>' if failed else ""),
                            headers={"Referrer-Policy": "same-origin"})

    @app.post("/lab/activate")
    async def activate_admin(request: Request):
        from urllib.parse import parse_qs
        from fastapi.responses import RedirectResponse
        if request.state.researcher != "admin" or request.headers.get("origin") != "http://" + request.headers["host"]:
            raise HTTPException(403)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>4096:
                raise HTTPException(413)
        try:
            form = parse_qs(data.decode(),max_num_fields=3)
            password = form["password"][0]
            ok = password == form["confirm"][0] and await asyncio.to_thread(lab.activate,form["code"][0],password)
        except (KeyError,ValueError):
            ok = False
        if not ok:
            return activation_page(True)
        response = RedirectResponse("/",status_code=303)
        response.delete_cookie(lab.cookie)
        return response

    @app.post("/api/lab/sharing")
    async def sharing(request: Request):
        if not request.state.admin:
            raise HTTPException(403)
        shared = (await body(request)).get("shared")
        await asyncio.to_thread(lab.set_shared,request.state.researcher,shared)
        return {"shared":shared}

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.post("/lab/login")
    async def login(request: Request):
        from urllib.parse import parse_qs
        from fastapi.responses import RedirectResponse
        if not lab.enabled or request.headers.get("origin") != "http://" + request.headers["host"]:
            raise HTTPException(403)
        if request.headers.get("content-type","").split(";")[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 4096:
                raise HTTPException(413)
            data.extend(chunk)
        try:
            form = parse_qs(data.decode(), max_num_fields=2)
        except (UnicodeError, ValueError):
            raise HTTPException(400)
        async with password_check_slot() as available:
            if not available:
                return login_page(busy=True)
            key = await asyncio.to_thread(lab.login, form.get("name",[""])[0], form.get("password",[""])[0])
            if not key:
                await asyncio.sleep(.35)
                return login_page(True)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(lab.cookie, key, max_age=12*3600, httponly=True, samesite="strict")
        return response

    @app.post("/api/lab/password")
    async def change_password(request: Request):
        if not request.state.researcher:
            raise HTTPException(403)
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            raise HTTPException(415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 16384:
                raise HTTPException(413)
            data.extend(chunk)
        try:
            form = json.loads(data)
            if not isinstance(form, dict) or set(form) != {"current", "new", "confirm"}:
                raise ValueError
            current, new, confirm = (form[k] for k in ("current", "new", "confirm"))
            if not all(isinstance(p, str) and len(p) <= 256 and p.encode("utf-8") for p in (current, new, confirm)):
                raise ValueError
        except (ValueError, RecursionError):
            return JSONResponse({"error": "비밀번호 입력란을 다시 확인하세요."}, status_code=400)
        if new != confirm:
            return JSONResponse({"error": "두 번 입력한 새 비밀번호가 다릅니다."}, status_code=400)
        if not 10 <= len(new) <= 256 or new == current:
            return JSONResponse({"error": "현재 비밀번호와 다른 10~256자 비밀번호를 입력하세요."}, status_code=400)
        async with password_check_slot() as available:
            if not available:
                return JSONResponse({"error": "잠시 후 다시 시도하세요."}, status_code=429, headers={"Retry-After": "3"})
            ok = await asyncio.to_thread(lab.change_own_password, request.cookies[lab.cookie], current, new)
            if not ok:
                await asyncio.sleep(.35)
                return JSONResponse({"error": "현재 비밀번호를 확인하세요."}, status_code=400)
        response = JSONResponse({"ok": True})
        response.delete_cookie(lab.cookie, httponly=True, samesite="strict")
        response.set_cookie(notice_cookie, password_notice, max_age=60, httponly=True, samesite="strict")
        return response

    @app.post("/api/lab/logout")
    def logout(request: Request):
        if not request.state.researcher:
            raise HTTPException(400)
        lab.logout(request.cookies[lab.cookie])
        response = JSONResponse({"ok":True})
        response.delete_cookie(lab.cookie, httponly=True, samesite="strict")
        return response

    @app.get("/")
    def index(request: Request):
        template = Path(__file__).resolve().parents[1] / "templates/platform.html"
        if template.exists():
            document = template.read_text().replace("__TOKEN__", request.state.api_token).replace("__WORKSPACE__", str(workspace))
        else:
            document = "<h1>NanoCoFold</h1><p>플랫폼 화면을 준비 중입니다.</p>"
        return HTMLResponse(document)

    @app.get("/assets/{filename}")
    def asset(filename: str):
        if filename not in {"3dmol-min.js", "plotly.min.js", "molstar.js", "molstar.css", "input-molstar.js", "result-molstar.js", "metric-guide.js", "platform.js", "platform-i18n.js", "platform.css"}:
            raise FileNotFoundError("없는 자산입니다.")
        # App code and workspace copies can come from different releases.
        # Always serve this installed release's JS/CSS with its HTML template.
        template_assets = Path(__file__).resolve().parents[1] / "templates"
        if filename in {"metric-guide.js", "platform.js", "platform-i18n.js", "platform.css", "input-molstar.js", "result-molstar.js"}:
            content = (template_assets / filename).read_bytes()
        else:
            from ..report import _resolve_viewer_asset
            content, evidence = _resolve_viewer_asset({}, filename, asset_root=ASSET_ROOT, local_only=True)
            if not evidence["ok"]:
                raise FileNotFoundError(evidence["blocker"])
        # FileResponse's ASGI pathsend bypasses GZip in a real Uvicorn server.
        # Bounded browser assets should use the compressed body path.
        return Response(content, media_type=mimetypes.guess_type(filename)[0])

    @app.get("/api/session")
    def session(request: Request):
        from .. import __version__
        try:
            defaults = planning_defaults(workspace)
        except (ValueError, OSError, KeyError):
            # A broken installation remains inspectable through Environment.
            # Do not invent numeric defaults before input review succeeds.
            defaults = {}
        return {"workspace": str(workspace), "researcher": request.state.researcher, "lab_admin":request.state.admin, "lab_shared":request.state.shared, "version": __version__, "modes": ["complex", "design", "screen", "mature"], "poll_interval_ms": 2000,
                "presets": {"researcher_exploration": exploration_config(), "standard": {}},
                "planning_defaults": defaults}

    @app.get("/api/inputs")
    def input_list(request: Request):
        return native({"inputs": input_manager(request).list()})

    @app.get("/api/tutorials/{mode}")
    def public_tutorial(mode: str):
        from .tutorials import load_public_example
        return load_public_example(mode)

    @app.post("/api/configs/parse")
    async def parse_config(request: Request):
        import yaml
        payload = await body(request)
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("YAML 설정 내용을 입력하세요.")
        try:
            cfg = read_config_yaml(content)
        except yaml.YAMLError as exc:
            raise ValueError("YAML 설정을 읽지 못했습니다.") from exc
        if not isinstance(cfg, dict):
            raise ValueError("YAML 설정은 객체여야 합니다.")
        lab_payload_paths(request,{"config":cfg})
        return {"config": native(normalized_config(workspace, cfg))}

    @app.post("/api/inputs")
    async def input_create(request: Request):
        payload = await body(request)
        if payload.get("kind") != "fasta":
            raise ValueError("FASTA 입력은 kind=fasta로 지정하세요. 구조 파일은 /api/inputs/load를 사용하세요.")
        return native(await asyncio.to_thread(input_manager(request).fasta, payload))

    @app.post("/api/inputs/load")
    async def input_load(request: Request):
        return native(await asyncio.to_thread(input_manager(request).load, await body(request)))

    @app.post("/api/inputs/analyze")
    async def input_analyze(request: Request):
        return native(await asyncio.to_thread(input_manager(request).analyze, await body(request)))

    @app.post("/api/inputs/review")
    async def input_review(request: Request):
        return native(await asyncio.to_thread(input_manager(request).review, await body(request)))

    @app.get("/api/inputs/{identifier}/structure")
    def input_structure(identifier: str, request: Request):
        return Response(input_manager(request).structure(identifier), media_type="chemical/x-pdb")

    @app.get("/api/inputs/{identifier}")
    def input_get(identifier: str, request: Request):
        return native(input_manager(request).get(identifier))

    @app.get("/api/jobs")
    def job_list(request: Request, limit: int = 100, before: float | None = None, before_id: str | None = None, active: bool = False):
        active_states = {"queued", "running", "cancelling"}
        history = manager.store.list(researcher=request.state.owner,limit=limit, before=before, before_id=before_id,
                                     states=active_states if active else None)
        cursor = lambda rows: {"before": rows[-1]["created"], "before_id": rows[-1]["id"]} if len(rows) == limit else None
        result = {"jobs": history, "next_cursor": cursor(history)}
        # Keep old active work visible without making a two-second poll return
        # an unbounded queue. Each stream has its own bounded cursor.
        if before is None and not active:
            running = manager.store.list(states=active_states, limit=limit,researcher=request.state.owner)
            result.update(active_jobs=running, active_next_cursor=cursor(running))
        return result

    @app.post("/api/jobs")
    async def job_create(request: Request):
        payload = effective(request, await body(request))
        payload.pop("researcher", None)
        if request.state.researcher:
            payload["researcher"] = request.state.researcher
        return native(await asyncio.to_thread(manager.create, payload))

    @app.get("/api/jobs/{identifier}")
    def job_get(identifier: str, request: Request):
        record = manager.store.get(identifier)
        if request.state.owner and record.get("researcher") != request.state.owner:
            result_access(request,identifier)
        return native(record)

    @app.get("/api/jobs/{identifier}/logs")
    def job_logs(identifier: str, request: Request, offset: int = 0):
        if request.state.owner:
            job_get(identifier,request)
        from .errors import public_log
        result = manager.logs(identifier, offset)
        return {**result, "text": public_log(result["text"])}

    def require_owner(request, record):
        if request.state.researcher and record.get("researcher") != request.state.researcher:
            raise HTTPException(403, "담당자만 변경할 수 있습니다. / Only the submitting researcher can change this record.")

    @app.post("/api/jobs/{identifier}/cancel")
    async def job_cancel(identifier: str, request: Request):
        if request.state.researcher:
            require_owner(request, await asyncio.to_thread(manager.store.get, identifier))
        payload = await body(request) if await request.body() else {}
        return await asyncio.to_thread(manager.cancel, identifier, expected_cancel=payload.get("expected_cancel"))

    @app.post("/api/jobs/{identifier}/resume")
    async def job_resume(identifier: str, request: Request):
        if request.state.researcher:
            require_owner(request, await asyncio.to_thread(manager.store.get, identifier))
        payload = await body(request)
        return await asyncio.to_thread(manager.resume, identifier, payload.get("additional_budget_seconds"))

    @app.post("/api/diagnostics")
    async def diagnostics(request: Request):
        payload = effective(request, await body(request))
        payload["researcher"] = request.state.researcher
        return native(await asyncio.to_thread(manager.create, payload, kind="diagnostics"))

    @app.post("/api/environment/check")
    async def environment_check(request: Request):
        payload = effective(request, await body(request))
        payload["researcher"] = request.state.researcher
        payload["environment_only"] = True
        return native(await asyncio.to_thread(manager.create, payload, kind="diagnostics"))

    @app.post("/api/environment/status")
    async def environment_status(request: Request):
        from .environment import readiness, normalized_installation_config
        payload = effective(request, await body(request))
        cfg = normalized_installation_config(workspace, payload.get("config"), mode=payload.get("mode"))
        from .jobs import allowed_gpu
        gpu = allowed_gpu(payload.get("gpu", os.environ.get("NANOCOFOLD_GPU", "0")))
        return native(await asyncio.to_thread(readiness, workspace, cfg, manager.store, gpu=gpu, researcher=request.state.owner))

    @app.get("/api/diagnostics")
    def diagnostic_list(request: Request):
        return {"jobs": [job for job in manager.store.list(limit=100,researcher=request.state.owner) if job["kind"] == "diagnostics"]}

    @app.post("/api/setup")
    async def setup(request: Request):
        if request.state.researcher:
            raise HTTPException(403, "공유 설치는 서버 터미널에서 관리하세요. / Manage shared installations from the server terminal.")
        return await asyncio.to_thread(manager.create, await body(request), kind="setup")

    @app.get("/api/results")
    def results_list(request: Request, limit: int = 25, cursor: str | None = None, search: str = "", mode: str = "", retention: str = "",
                     sort: str = "created", descending: bool = True):
        page = manager.catalog.list(researcher=read_owner(request),limit=limit, cursor=cursor, search=search, mode=mode, retention=retention,
                                    sort=sort, descending=descending)
        if not request.state.owner and not page["items"] and not any((cursor, search, mode, retention)):
            # Compatibility discovery is bounded. Index an older workspace
            # explicitly with `nanocofold catalog --index-existing`.
            import itertools
            import os
            runs = workspace / "runs"
            if runs.is_dir():
                with os.scandir(runs) as entries:
                    for entry in itertools.islice((e for e in entries if e.is_dir(follow_symlinks=False)), limit):
                        with contextlib.suppress(ValueError, OSError):
                            manager.catalog.register(entry.name, entry.path, state="complete")
            page = manager.catalog.list(researcher=read_owner(request),limit=limit, search=search, mode=mode, retention=retention,
                                        sort=sort, descending=descending)
        return native({"results": page["items"], "next_cursor": page["next_cursor"]})

    @app.get("/api/lab/usage")
    def lab_usage(request: Request, researcher: str="", before: float | None=None, before_id: str="", limit: int=25):
        if not request.state.researcher:
            raise HTTPException(403)
        return manager.store.usage(read_owner(request,researcher), before, before_id, limit)

    @app.get("/api/result-catalog")
    def result_catalog(request: Request, researcher: str = "", limit: int = 25, cursor: str | None = None, search: str = "", mode: str = "", retention: str = "",
                       sort: str = "created", descending: bool = True):
        return native(manager.catalog.list(limit=limit, cursor=cursor, search=search, mode=mode, retention=retention,
                                           sort=sort, descending=descending, researcher=read_owner(request,researcher)))

    @app.get("/api/candidate-catalog")
    def candidate_rows(request: Request, search: str="", target: str="", researcher: str="", engine: str="", decision: str="",
                       sort: str="created", descending: bool=True, include_mock: bool=False, offset: int=0, limit: int=25):
        from .candidate_catalog import page
        return page(manager.catalog,search=search,target=target,researcher=read_owner(request,researcher),engine=engine,
                    decision=decision,sort=sort,descending=descending,include_mock=include_mock,offset=offset,limit=limit)

    @app.get("/api/candidate-catalog/export")
    def candidate_excel(request: Request, search: str="", target: str="", researcher: str="", engine: str="", decision: str="",
                        sort: str="created", descending: bool=True, include_mock: bool=False, lang: str="en"):
        from .candidate_catalog import workbook
        path=workbook(manager.catalog,lang,search=search,target=target,researcher=read_owner(request,researcher),engine=engine,
                      decision=decision,sort=sort,descending=descending,include_mock=include_mock)
        return ExportFileResponse(path,filename="NanoCoFold-candidates.xlsx",media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  background=BackgroundTask(path.unlink,missing_ok=True))

    @app.post("/api/results/open")
    async def results_open(request: Request):
        payload = await body(request)
        if request.state.owner:
            raise HTTPException(403, "Open your submitted analyses from Results database.")
        def open_result():
            path = result_directory(payload.get("path", ""))
            summary = ResultReader(path).summary()
            identifier = path.name if path.parent == workspace / "runs" else "import-" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
            with manager.store.connect() as db:
                db.execute("INSERT INTO result_paths VALUES(?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path", (identifier, str(path)))
            manager.catalog.register(identifier, path, state=summary.get("status", "incomplete"),
                                     retention="minimal" if summary.get("retention") == "minimal" else "full")
            summary["id"] = identifier
            return native(summary)
        # Catalog registration hashes artifacts and walks the result tree. Keep
        # all result I/O off the event loop so status and cancellation stay live.
        return await asyncio.to_thread(open_result)

    @app.get("/api/results/{identifier}")
    def result_summary(identifier: str, request: Request):
        summary = reader(identifier, request).summary()
        summary["id"] = identifier
        return native(summary)

    @app.get("/api/results/{identifier}/retention")
    def retention_plan(identifier: str, request: Request):
        from .retention import RetentionManager
        return native(RetentionManager(workspace).plan(reader(identifier, request).root))

    @app.post("/api/results/{identifier}/retention")
    async def retention_apply(identifier: str, request: Request):
        from .retention import RetentionManager
        payload = await body(request)
        def apply_retention():
            root = reader(identifier, request).root
            if request.state.researcher:
                try:
                    owner_job = manager.store.get(root.name)
                except KeyError:
                    raise HTTPException(403, "이전 결과는 서버 관리자가 정리할 수 있습니다. / Ask the server operator to compact older results.")
                require_owner(request, owner_job)
            original_id = root.name  # Compact the canonical run, never an alias.
            identity = None
            with contextlib.suppress(KeyError):
                identity = manager.catalog.get(original_id).get("identity")
            result = RetentionManager(workspace).apply(root, payload.get("plan_digest"), payload.get("confirmation"))
            # A verified compact snapshot is already authoritative even if bounded
            # file cleanup needs a retry. Report those states independently.
            manager.catalog.register(original_id, root, identity, state=reader(original_id, request).summary().get("status", "incomplete"), retention="minimal")
            return native(result)
        return await asyncio.to_thread(apply_retention)

    @app.get("/api/results/{identifier}/candidates")
    def candidates(identifier: str, request: Request, search: str = "", sort: str = "composite_score", descending: bool = True,
                   eligible: bool | None = None, eligibility_basis: str = "recorded", hotspot_min: float | None = None,
                   offset: int = 0, limit: int = 100):
        if not 0 <= offset or not 1 <= limit <= 10000:
            raise ValueError("페이지 크기는 1–10,000개여야 합니다.")
        return native(reader(identifier, request).candidates(search=search, sort=sort, descending=descending, eligible=eligible,
                      eligibility_basis=eligibility_basis, hotspot_min=hotspot_min, offset=offset, limit=limit))

    @app.get("/api/results/{identifier}/observations")
    def observations(identifier: str, request: Request, candidate_id: str | None = None):
        return native(reader(identifier, request).observations(candidate_id))

    @app.get("/api/results/{identifier}/structure")
    def structure(identifier: str, request: Request, candidate_id: str, observation_id: str | None = None):
        return native(reader(identifier, request).structure(candidate_id, observation_id))

    @app.get("/api/results/{identifier}/artifacts")
    def artifacts(identifier: str, request: Request, provenance_only: bool = False):
        result = reader(identifier, request)
        return {"provenance": result.provenance()} if provenance_only else result.artifacts()

    @app.get("/api/results/{identifier}/download")
    def download(identifier: str, request: Request, path: str):
        file = reader(identifier, request).artifact_path(path)
        if not file.is_file():
            raise FileNotFoundError("산출물 파일이 없습니다.")
        if file.suffix.lower() == ".html" and "_explorer" not in file.stem:
            from ..report import report_snapshot_files
            from ..report_delivery import package_report
            files = report_snapshot_files(file)
            if files is not None:
                content = package_report(files, {"scope": "exact_saved_entry_and_linked_explorer",
                                                "source_readonly": True})
                return Response(content, media_type="application/zip", headers={
                    "Content-Disposition": 'attachment; filename="report_bundle.zip"'})
        return FileResponse(file, filename=file.name)

    @app.post("/api/results/{identifier}/export")
    async def export(identifier: str, request: Request):
        payload = await body(request)
        result = reader(identifier, request)
        format = payload.get("format", "zip")
        if format == "zip":
            task = asyncio.create_task(asyncio.to_thread(result.export_to_file, format, payload.get("filters")))
            try:
                path, media_type, filename = await asyncio.shield(task)
            except BaseException:
                # Cancelling the request cannot cancel an already writing
                # thread. Remove its completed spool even after disconnect.
                def cleanup(completed):
                    with contextlib.suppress(BaseException):
                        completed.result()[0].unlink(missing_ok=True)
                task.add_done_callback(cleanup)
                raise
            return ExportFileResponse(path, media_type=media_type, filename=filename,
                                background=BackgroundTask(path.unlink, missing_ok=True))
        data, media_type, filename = await asyncio.to_thread(result.export, format, payload.get("filters"))
        return Response(data, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    return app


def serve(workspace: str | Path, host: str = "127.0.0.1", port: int = 18765, no_browser: bool = False):
    import uvicorn
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("개인 플랫폼은 loopback에 바인딩합니다. 원격 접속은 SSH 포트포워딩을 사용하세요.")
    sock = socket.socket(socket.AF_INET6 if host == "::1" else socket.AF_INET, socket.SOCK_STREAM)
    timer = None
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise ValueError(f"NanoCoFold port {port} is already in use. Keep the other application running; choose --port 0 and open the NanoCoFold URL printed in this terminal.") from exc
            raise
        sock.listen(128)
        actual_port = sock.getsockname()[1]
        app = create_app(workspace)
        url = f"http://{'[' + host + ']' if host == '::1' else host}:{actual_port}/"
        remote_host = "[::1]" if host == "::1" else "127.0.0.1"
        print(f"NanoCoFold: {url}\n작업 공간: {Path(workspace).resolve()}\n원격: ssh -o ExitOnForwardFailure=yes -L {actual_port}:{remote_host}:{actual_port} USER@HOST", flush=True)
        if not no_browser:
            timer = threading.Timer(.8, lambda: webbrowser.open(url))
            timer.start()
        config = uvicorn.Config(app, host=host, port=actual_port, log_level="warning", access_log=False)
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        if timer is not None:
            timer.cancel()
        sock.close()
