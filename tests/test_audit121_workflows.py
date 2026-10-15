"""Supported optional-engine paths use real scoring/ranking with CPU predictions."""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from nanocofold import cli, mature
from audit121_fixtures import small_config, VHH


@pytest.mark.parametrize("path", ["no_hotspots", "missing_esm", "four_engines_missing_esm", "web_mature_antifold_off", "multimutant_no_prefilter"])
def test_optional_metric_paths_keep_eligible_candidates(tmp_path, monkeypatch, path):
    mode = "mature" if path in {"web_mature_antifold_off", "multimutant_no_prefilter"} else "screen"
    cfg = small_config(mode)
    # Synthetic test input removes a known NTT glycosylation sequon so a
    # candidate can satisfy the unchanged developability gate. Priors remain
    # genuinely unmeasured in the optional-engine branches below.
    sequence = VHH.replace("NTT", "QTT")
    cfg["mature"]["wt_sequence"] = sequence
    cfg["run"].update(name=path, outdir=str(tmp_path / "runs"))
    cfg["screen"].update(tier2_n=12, boltz2_rescore_top=0)
    cfg["rank"].update(final_n=12, cdr3={"per_cluster":12,"cluster_identity":.85})
    cfg["mature"].update(antifold={"enabled":False}, library={"max_variants":12})
    cfg["mature"]["prefilter"].update(top_n=12)
    if mode == "screen":
        library = tmp_path / "library.fasta"
        library.write_text("".join(f">Nb{i}\n{sequence[:100]}{aa}{sequence[101:]}\n" for i,aa in enumerate("ACDEFGHIKLMN")))
        cfg["screen"]["vhh_fasta"] = str(library)
    if path == "no_hotspots":
        cfg["target"]["hotspots"] = []
    if path == "four_engines_missing_esm":
        cfg["screen"]["comparison"] = {"enabled":True,"engines":["colabfold","boltz2","chai1","opendde"]}
    if "missing_esm" in path:
        original = cli._run_sequence_scores
        monkeypatch.setattr(cli,"_esm_env_available",lambda *_:False)
        def unavailable(settings,*args,**kwargs):
            real = copy.deepcopy(settings);real["run"]["mock"] = False
            return original(real,*args,**kwargs)
        monkeypatch.setattr(cli,"_run_sequence_scores",unavailable)
    if mode == "mature":
        cfg["mature"]["prefilter"]["mode"] = "none" if path == "multimutant_no_prefilter" else "esm2"
        if path == "multimutant_no_prefilter":
            cfg["mature"]["library"].update(strategy="random", size=12, min_mutations=2, max_mutations=2)
        original = mature.run_prefilter
        def prefilter(*args,**kwargs):
            kwargs["mock"] = False
            return original(*args,**kwargs)
        monkeypatch.setattr(mature,"run_prefilter",prefilter)
        monkeypatch.setattr(mature.shell,"env_python",lambda *_:Path("python"))
        def esm_process(env, argv, **kwargs):
            assert any(str(value).endswith("esm_llr.py") for value in argv)
            frame = pd.read_csv(argv[argv.index("--variants")+1])
            frame[["variant_id"]].assign(esm_llr=0.).to_csv(argv[argv.index("--output")+1],index=False)
        monkeypatch.setattr(mature.shell,"run_in_env",esm_process)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    assert cli.main(["run","--config",str(config_path)]) == 0
    run = next((tmp_path / "runs").iterdir())
    selected = pd.read_csv(run / "05_final/selected_candidates.csv")
    scored = pd.read_csv(run / "03_score/scored_candidates.csv")
    policy = json.loads((run / "03_score/ranking_policy.json").read_text())
    metric = "antifold" if mode == "mature" else "hotspot_contact_frac" if path == "no_hotspots" else "esm_score"
    assert scored[metric].isna().all()
    assert len(selected) > 0
    assert metric in policy["ignored_weights"] and metric not in policy["applied_weights"]
    assert not scored.ranking_unavailable_reason.eq("weighted_metrics_unavailable").all()
