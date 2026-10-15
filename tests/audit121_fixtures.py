"""Small public regression fixtures; no server, model download or GPU calls."""
import io
import json
import tarfile

import pytest
import yaml

from nanocofold import install, mock

VHH = "QVQLVESGGGLVQPGESLRLSCAASGSIFGIYAVHWFRMAPGKEREFTAGFGSHGSTNYAASVKGRFTMSRDNAKNTTYLQMNSLKPADTAVYYCHALIKNELGFLDYWGPGTQVTVSS"


def small_config(mode):
    return {"run":{"mock":True,"mode":mode},"target":{"sequence":"ACDEFGHIKLMNPQRSTVWY"*2,"hotspots":[10,15,20]},
            "structure":{"provider":"mock","msa":{"backend":"empty"}},"truncate":{"enabled":False},
            "screen":{"tier2_n":12,"boltz2_rescore_top":0},"rank":{"final_n":12},
            "mature":{"wt_sequence":VHH,"cdr":{"source":"config","regions":{"CDR1":[27,27],"CDR2":[56,56],"CDR3":[105,105]}},
                      "prefilter":{"top_n":12}}}


def fixture_run(tmp_path):
    workspace = tmp_path / "workspace"
    run = workspace / "runs/owned-run"
    for stage in ["02_screen","03_score","05_final"]:(run/stage).mkdir(parents=True,exist_ok=True)
    cfg={"run":{"mode":"screen","name":"owned fixture"},"target":{"sequence":"AAAA"},"screen":{"engine":"boltz2"}}
    (run/"run_config.yaml").write_text(yaml.safe_dump(cfg))
    (run/"run_state.json").write_text(json.dumps({"execution":{"state":"complete"},"phases":{"final":{}},"warnings":[]}))
    model=mock.write_pdb(mock.build_complex_structure("AAAA","AAAA",[2]),run/"02_screen/model.pdb")
    (run/"03_score/scored_candidates.csv").write_text("candidate_id,sequence,cif_path,engine,ipsae,iptm,final_eligible,final_evaluated,error\nsuccess,AAAA,02_screen/model.pdb,boltz2,0.7,0.8,true,true,\nfailed,CCCC,,chai1,,,false,false,engine failed\n")
    (run/"05_final/selected_candidates.csv").write_text("candidate_id\nsuccess\n")
    return workspace,run,model


def archive_fixture(tmp_path, monkeypatch, extra):
    install.init_workspace(tmp_path)
    name="alphafold_params_colab_2022-12-06.tar"
    cache=tmp_path/"models/colabfold";cache.mkdir(parents=True)
    contents={f"params_model_{i}_multimer_v3.npz":f"model{i}".encode() for i in range(1,6)}
    contents.update({"LICENSE":b"license",**extra})
    with tarfile.open(cache/name,"w") as archive:
        for filename,content in contents.items():
            member=tarfile.TarInfo(filename);member.size=len(content);archive.addfile(member,io.BytesIO(content))
    root=tmp_path/"package";(root/"_data").mkdir(parents=True)
    (root/"_data/model-assets.json").write_text(json.dumps({"hash_source":"test","assets":{name:{"engine":"colabfold","url":"https://invalid.example/model.tar","sha256":install._digest(cache/name)}}}))
    monkeypatch.setattr(install,"_package_root",lambda:root)
    monkeypatch.setattr(install.urllib.request,"urlopen",lambda *a,**k:pytest.fail("fixture must not download"))
    return cache,contents
