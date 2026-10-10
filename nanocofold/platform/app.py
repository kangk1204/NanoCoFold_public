from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import mimetypes
import re
import secrets
import socket
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.background import BackgroundTask

from ..config import ASSET_ROOT, _deep_merge, exploration_config
from .inputs import InputManager, normalized_config
from .jobs import JobManager
from .paths import contained, native
from .results import ResultReader, TABLES

MAX_REQUEST = 20 * 1024 * 1024


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
    token = secrets.token_urlsafe(32)
    with manager.store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS result_paths (id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE)")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            if start_workers:
                manager.start()
            yield
        finally:
            try:
                manager.close()
            finally:
                inputs.close()

    app = FastAPI(title="NanoCoFold", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.state.workspace, app.state.jobs, app.state.inputs, app.state.token = workspace, manager, inputs, token

    @app.middleware("http")
    async def local_gate(request: Request, call_next):
        def secure(response):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
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
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-platform-token", request.headers.get("x-wizard-token", ""))
            if not secrets.compare_digest(supplied.encode(), token.encode()):
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
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; worker-src 'self' blob:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return secure(response)

    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(KeyError)
    async def unknown(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

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
        return payload

    @app.post("/api/inputs/complex")
    async def complex_input(request: Request):
        return await asyncio.to_thread(inputs.complex, await body(request))

    @app.get("/api/batches")
    def batch_list():
        return {"batches": batches.list()}

    @app.post("/api/batches/review")
    async def batch_review(request: Request):
        return await asyncio.to_thread(batches.review, await body(request))

    @app.get("/api/batches/{identifier}")
    def batch_get(identifier: str):
        return batches.get(identifier)

    @app.post("/api/batches/{identifier}/submit")
    async def batch_submit(identifier: str, request: Request):
        return await asyncio.to_thread(batches.submit, identifier, await body(request))

    @app.post("/api/batches/{identifier}/cancel")
    async def batch_cancel(identifier: str):
        return await asyncio.to_thread(batches.cancel, identifier)

    def effective(payload):
        cfg = payload.get("config", {})
        reviewed = None
        if payload.get("input_id"):
            reviewed = inputs.reviewed(payload["input_id"])
            cfg = _deep_merge(reviewed["config"], cfg)
        if payload.get("config_path"):
            import yaml
            path = contained(workspace, payload["config_path"], exists=True)
            config = yaml.safe_load(path.read_text())
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

    def reader(identifier):
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

    @app.get("/")
    def index():
        template = Path(__file__).resolve().parents[1] / "templates/platform.html"
        if template.exists():
            document = template.read_text().replace("__TOKEN__", token).replace("__WORKSPACE__", str(workspace))
        else:
            document = "<h1>NanoCoFold</h1><p>플랫폼 화면을 준비 중입니다.</p>"
        return HTMLResponse(document)

    @app.get("/assets/{filename}")
    def asset(filename: str):
        if filename not in {"3dmol-min.js", "plotly.min.js", "molstar.js", "molstar.css", "input-molstar.js", "result-molstar.js", "platform.js", "platform.css"}:
            raise FileNotFoundError("없는 자산입니다.")
        # App code and workspace copies can come from different releases.
        # Always serve this installed release's JS/CSS with its HTML template.
        template_assets = Path(__file__).resolve().parents[1] / "templates"
        if filename in {"platform.js", "platform.css", "input-molstar.js", "result-molstar.js"}:
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
    def session():
        return {"workspace": str(workspace), "modes": ["design", "screen", "mature"], "poll_interval_ms": 2000,
                "presets": {"researcher_exploration": exploration_config(), "standard": {}}}

    @app.get("/api/inputs")
    def input_list():
        return native({"inputs": inputs.list()})

    @app.post("/api/configs/parse")
    async def parse_config(request: Request):
        import yaml
        payload = await body(request)
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("YAML 설정 내용을 입력하세요.")
        try:
            cfg = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError("YAML 설정을 읽지 못했습니다.") from exc
        if not isinstance(cfg, dict):
            raise ValueError("YAML 설정은 객체여야 합니다.")
        return {"config": native(normalized_config(workspace, cfg))}

    @app.post("/api/inputs")
    async def input_create(request: Request):
        payload = await body(request)
        if payload.get("kind") != "fasta":
            raise ValueError("FASTA 입력은 kind=fasta로 지정하세요. 구조 파일은 /api/inputs/load를 사용하세요.")
        return native(await asyncio.to_thread(inputs.fasta, payload))

    @app.post("/api/inputs/load")
    async def input_load(request: Request):
        return native(await asyncio.to_thread(inputs.load, await body(request)))

    @app.post("/api/inputs/analyze")
    async def input_analyze(request: Request):
        return native(await asyncio.to_thread(inputs.analyze, await body(request)))

    @app.post("/api/inputs/review")
    async def input_review(request: Request):
        return native(await asyncio.to_thread(inputs.review, await body(request)))

    @app.get("/api/inputs/{identifier}/structure")
    def input_structure(identifier: str):
        return Response(inputs.structure(identifier), media_type="chemical/x-pdb")

    @app.get("/api/inputs/{identifier}")
    def input_get(identifier: str):
        return native(inputs.get(identifier))

    @app.get("/api/jobs")
    def job_list(limit: int = 100, before: float | None = None, before_id: str | None = None):
        history = manager.store.list(limit=limit, before=before, before_id=before_id)
        jobs = {job["id"]: job for job in history}
        if before is None:
            jobs.update({job["id"]: job for job in manager.store.list(states={"queued", "running", "cancelling"})})
        return {"jobs": sorted(jobs.values(), key=lambda job: (job["created"], job["id"]), reverse=True),
                "next_cursor": {"before": history[-1]["created"], "before_id": history[-1]["id"]} if len(history) == limit else None}

    @app.post("/api/jobs")
    async def job_create(request: Request):
        return native(await asyncio.to_thread(manager.create, effective(await body(request))))

    @app.get("/api/jobs/{identifier}")
    def job_get(identifier: str):
        return native(manager.store.get(identifier))

    @app.get("/api/jobs/{identifier}/logs")
    def job_logs(identifier: str, offset: int = 0):
        return manager.logs(identifier, offset)

    @app.post("/api/jobs/{identifier}/cancel")
    def job_cancel(identifier: str):
        return manager.cancel(identifier)

    @app.post("/api/jobs/{identifier}/resume")
    async def job_resume(identifier: str, request: Request):
        payload = await body(request)
        return await asyncio.to_thread(manager.resume, identifier, payload.get("additional_budget_seconds"))

    @app.post("/api/diagnostics")
    async def diagnostics(request: Request):
        payload = effective(await body(request))
        return native(await asyncio.to_thread(manager.create, payload, kind="diagnostics"))

    @app.get("/api/diagnostics")
    def diagnostic_list():
        return {"jobs": [job for job in manager.store.list(limit=100) if job["kind"] == "diagnostics"]}

    @app.post("/api/setup")
    async def setup(request: Request):
        return await asyncio.to_thread(manager.create, await body(request), kind="setup")

    @app.get("/api/results")
    def results_list(limit: int = 25, cursor: str | None = None, search: str = "", mode: str = "", retention: str = "",
                     sort: str = "created", descending: bool = True):
        page = manager.catalog.list(limit=limit, cursor=cursor, search=search, mode=mode, retention=retention,
                                    sort=sort, descending=descending)
        if not page["items"] and not any((cursor, search, mode, retention)):
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
            page = manager.catalog.list(limit=limit, search=search, mode=mode, retention=retention,
                                        sort=sort, descending=descending)
        return native({"results": page["items"], "next_cursor": page["next_cursor"]})

    @app.get("/api/result-catalog")
    def result_catalog(limit: int = 25, cursor: str | None = None, search: str = "", mode: str = "", retention: str = "",
                       sort: str = "created", descending: bool = True):
        return native(manager.catalog.list(limit=limit, cursor=cursor, search=search, mode=mode, retention=retention,
                                           sort=sort, descending=descending))

    @app.post("/api/results/open")
    async def results_open(request: Request):
        payload = await body(request)
        path = result_directory(payload.get("path", ""))
        summary = await asyncio.to_thread(lambda: ResultReader(path).summary())
        identifier = path.name if path.parent == workspace / "runs" else "import-" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
        with manager.store.connect() as db:
            db.execute("INSERT INTO result_paths VALUES(?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path", (identifier, str(path)))
        manager.catalog.register(identifier, path, state=summary.get("status", "incomplete"),
                                 retention="minimal" if summary.get("retention") == "minimal" else "full")
        summary["id"] = identifier
        return native(summary)

    @app.get("/api/results/{identifier}")
    def result_summary(identifier: str):
        summary = reader(identifier).summary()
        summary["id"] = identifier
        return native(summary)

    @app.get("/api/results/{identifier}/retention")
    def retention_plan(identifier: str):
        from .retention import RetentionManager
        return native(RetentionManager(workspace).plan(reader(identifier).root))

    @app.post("/api/results/{identifier}/retention")
    async def retention_apply(identifier: str, request: Request):
        from .retention import RetentionManager
        payload = await body(request)
        root = reader(identifier).root
        original_id = root.name  # Compact the canonical run, never an alias.
        identity = None
        with contextlib.suppress(KeyError):
            identity = manager.catalog.get(original_id).get("identity")
        result = await asyncio.to_thread(RetentionManager(workspace).apply, root, payload.get("plan_digest"), payload.get("confirmation"))
        # A verified compact snapshot is already authoritative even if bounded
        # file cleanup needs a retry. Report those states independently.
        manager.catalog.register(original_id, root, identity, state=reader(original_id).summary().get("status", "incomplete"), retention="minimal")
        return native(result)

    @app.get("/api/results/{identifier}/candidates")
    def candidates(identifier: str, search: str = "", sort: str = "composite_score", descending: bool = True,
                   eligible: bool | None = None, eligibility_basis: str = "recorded", hotspot_min: float | None = None,
                   offset: int = 0, limit: int = 100):
        if not 0 <= offset or not 1 <= limit <= 10000:
            raise ValueError("페이지 크기는 1–10,000개여야 합니다.")
        return native(reader(identifier).candidates(search=search, sort=sort, descending=descending, eligible=eligible,
                      eligibility_basis=eligibility_basis, hotspot_min=hotspot_min, offset=offset, limit=limit))

    @app.get("/api/results/{identifier}/observations")
    def observations(identifier: str, candidate_id: str | None = None):
        return native(reader(identifier).observations(candidate_id))

    @app.get("/api/results/{identifier}/structure")
    def structure(identifier: str, candidate_id: str, observation_id: str | None = None):
        return native(reader(identifier).structure(candidate_id, observation_id))

    @app.get("/api/results/{identifier}/artifacts")
    def artifacts(identifier: str, provenance_only: bool = False):
        result = reader(identifier)
        return {"provenance": result.provenance()} if provenance_only else result.artifacts()

    @app.get("/api/results/{identifier}/download")
    def download(identifier: str, path: str):
        file = reader(identifier).artifact_path(path)
        if not file.is_file():
            raise FileNotFoundError("산출물 파일이 없습니다.")
        return FileResponse(file, filename=file.name)

    @app.post("/api/results/{identifier}/export")
    async def export(identifier: str, request: Request):
        payload = await body(request)
        result = reader(identifier)
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


def serve(workspace: str | Path, host: str = "127.0.0.1", port: int = 8765, no_browser: bool = False):
    import uvicorn
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("개인 플랫폼은 loopback에 바인딩합니다. 원격 접속은 SSH 포트포워딩을 사용하세요.")
    app = create_app(workspace)
    sock = socket.socket(socket.AF_INET6 if host == "::1" else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    actual_port = sock.getsockname()[1]
    url = f"http://{'[' + host + ']' if host == '::1' else host}:{actual_port}/"
    print(f"NanoCoFold: {url}\n작업 공간: {Path(workspace).resolve()}\n원격: ssh -L {actual_port}:127.0.0.1:{actual_port} USER@HOST", flush=True)
    if not no_browser:
        threading.Timer(.8, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, host=host, port=actual_port, log_level="warning", access_log=False)
    try:
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        sock.close()
