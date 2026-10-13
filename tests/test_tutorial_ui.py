"""The example loader reads shipped inputs; no review, MSA or worker runs."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanocofold.platform.app import create_app
from nanocofold.platform.tutorials import load_public_example

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["complex", "screen", "mature", "design"])
def test_tutorial_endpoint_reads_exact_input_with_no_submission(tmp_path, mode):
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/tutorials/" + mode).status_code == 403
        response = client.get("/api/tutorials/" + mode, headers={"x-platform-token": app.state.token})
        assert response.status_code == 200
        example = response.json()
        expected = json.loads((ROOT / "examples/tutorial" / (mode + "_batch.json")).read_text())
        assert example["payload"] == expected
        assert example["payload"]["api_consent"] is False
        assert example["predictions_executed"] is False
        assert example["requires_api_consent"] is (mode != "complex")
        assert app.state.jobs.store.list() == []
        assert app.state.inputs.list() == []


def test_changed_tutorial_is_rejected_before_returning_input(tmp_path):
    shutil.copytree(ROOT / "examples/tutorial", tmp_path / "tutorial")
    root = tmp_path / "tutorial"
    (root / "complex_batch.json").write_text('{"mode":"complex","api_consent":true}')
    with pytest.raises(ValueError, match="변경"):
        load_public_example("complex", root=root)


@pytest.mark.parametrize("mode", ["../complex", "invalid", "complex.json"])
def test_unknown_tutorial_has_no_arbitrary_file_lookup(mode):
    with pytest.raises(ValueError, match="공개 예제"):
        load_public_example(mode, root=Path("/does-not-exist"))


def test_actual_frontend_tutorial_and_camera_contracts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the frontend contract")
    subprocess.run([node, "--test", str(Path(__file__).with_name("tutorial_viewer_contract.mjs"))],
                   check=True, capture_output=True, timeout=30)
