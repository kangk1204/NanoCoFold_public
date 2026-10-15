"""Delivery fixes preserve native data and do not run any prediction engines."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from nanocofold import install, report
from nanocofold.platform.app import create_app
from nanocofold.platform.cloud_results import build_bundle
from nanocofold.platform.errors import PublicIssues, public_error
from nanocofold.platform.inputs import InputManager
from nanocofold.report_delivery import save_result_report
from test_audit21_presentation import saved_run
from audit121_fixtures import archive_fixture
from test_p3_report_publication import linked_explorer


@pytest.mark.parametrize("identifier", ["Nb1", "나노바디2", "Nb:2", "나노:2"])
def test_prediction_issues_keep_candidate_id_without_language_dependent_path_exposure(identifier):
    issues = PublicIssues()
    issues.append(identifier + ": [Errno 2] No such file or directory: '/home/private/model.cif'")
    assert issues[0].startswith(identifier + ": ")
    assert "예측 산출물" in issues[0] and "입력" not in issues[0]
    assert "/home" not in issues[0] and "private" not in issues[0]
    assert "내보낼 산출물" in public_error(FileNotFoundError("missing"), stage="export")
    assert "입력 파일" in public_error(FileNotFoundError("missing"), stage="input")


def test_engine_prefix_overflow_is_rejected_by_input_review_before_job_submission(tmp_path):
    manager = InputManager(tmp_path)
    try:
        with pytest.raises(ValueError, match="접두어"):
            manager.fasta({"mode":"screen", "mock":True, "content":">target\nACDEFG\n",
                           "vhh_fasta":">" + "가" * 37 + "\nACDEFG\n",
                           "config":{"screen":{"comparison":{"enabled":True,"engines":["colabfold"]}}}})
        assert manager.list() == []
        assert not list(tmp_path.rglob("run_state.json"))
    finally:
        manager.close()


def test_input_api_uses_input_guidance_without_misclassifying_other_operation_errors(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_workers=False)
    def invalid_input(_payload):
        raise ValueError("invalid schema")
    monkeypatch.setattr(app.state.inputs, "fasta", invalid_input)
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Platform-Token":app.state.token}) as client:
        response = client.post("/api/inputs", json={"kind":"fasta","content":">target\nACDEFG\n","mock":True})
        assert response.status_code == 400 and response.json()["error"].startswith("입력 처리에 실패했습니다.")
    assert "입력" not in public_error(ValueError("engine operation failed"))


def test_entry_download_includes_exact_saved_explorer_without_changing_results(tmp_path):
    run, cfg = saved_run(tmp_path / "runs", count=2)
    entry = report.generate_report(run, cfg, {})
    explorer, _ = linked_explorer(entry)
    before = {p:p.read_bytes() for p in run.rglob("*") if p.is_file()}
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Platform-Token":app.state.token}) as client:
        response = client.get(f"/api/results/{run.name}/download", params={"path":"report.html"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.read(entry.name) == entry.read_bytes()
            assert archive.read(explorer.name) == explorer.read_bytes()
    assert all(p.read_bytes()==content for p,content in before.items())


def test_cloud_result_zip_has_one_full_explorer_and_preserves_all_native_files(tmp_path):
    run, cfg = saved_run(tmp_path / "runs", count=2)
    entry = report.generate_report(run, cfg, {})
    explorer, _ = linked_explorer(entry)
    save_result_report(run)
    before = {p:p.read_bytes() for p in run.rglob("*") if p.is_file()}
    destination = tmp_path / "cloud"
    build_bundle(run, destination)
    with zipfile.ZipFile(destination / "results.zip") as archive:
        html = [name for name in archive.namelist() if name.endswith(".html") and b'id="result-data"' in archive.read(name)]
        assert html == ["original/" + explorer.name]
        assert archive.read("original/report.html") == entry.read_bytes()
        for path,content in before.items():
            if path.name == "report_delivery.zip":continue
            assert archive.read("original/" + str(path.relative_to(run))) == content
        manifest = json.loads(archive.read("report_packaging_manifest.json"))
        omitted = {row["path"] for row in manifest["derived_reports_omitted"]}
        assert {"original/report_delivery.zip", "exploration/report_delivery.zip", "exploration/report_offline.html"} <= omitted
    assert (destination / "report_offline.html").is_file()
    assert (destination / "report_delivery.zip").is_file()
    assert all(p.read_bytes()==content for p,content in before.items())


def test_colabfold_ignores_appledouble_without_extracting_metadata_or_unknown_paths(tmp_path, monkeypatch):
    cache,_ = archive_fixture(tmp_path, monkeypatch, extra={"._params_model_1_multimer_v3.npz":b"metadata", "__MACOSX/._LICENSE":b"metadata"})
    install.install_model_assets(tmp_path, ["colabfold"])
    assert not list((cache/"params").glob("._*"))
    assert len(list((cache/"params").glob("*.npz"))) == 5
