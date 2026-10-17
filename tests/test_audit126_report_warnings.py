"""Execute the actual subset embedded in rendered reports, never the full catalog."""
import copy
import html
import json
import re
import subprocess
from html.parser import HTMLParser

import pandas as pd
import pytest
import yaml

from nanocofold import rank_select, report
from nanocofold.config import load_config


SCIENCE_WARNINGS = [
    "결합 부위를 지정하지 않았습니다. 결합 위치를 제한하지 않고 탐색합니다.",
    "캐시된 MSA가 없습니다: msa/saved_{0}.a3m. 서열만 입력으로 단일서열 모드(msa=empty)로 진행합니다. 정확도를 높이려면 structure.msa.backend=colabfold_search 로 MSA를 먼저 생성하세요.",
    "final_eligible 후보가 0종입니다. 합성 목록은 비어 있습니다. 임계값/예측 품질을 점검하세요.",
    "전체 성공 후보에서 미평가인 순위 지표: esm_score, antifold. 해당 가중치를 제외하고 나머지 지표로 순위를 계산했습니다. 미평가를 0점이나 부적격으로 해석하지 마세요.",
    "handoff 게이트 미통과(2건): 발주 목록은 확정할 수 없습니다. `nanocofold validate-handoff` 로 항목을 확인하세요.",
    "mock/dry-run: CDR 의존 liability 는 미평가로 기록됩니다. rank.weights.dev_flags가 0이 아니면 종합 순위와 최종 선별을 보류합니다. 실제 분석은 envs/setup_antibody.sh로 ANARCI를 설치하거나 mature.cdr.source=config와 검증한 1-based CDR 범위를 지정하세요. 미평가를 위험 없음으로 처리하지 않습니다.",
    "ANARCI 환경 없음: CDR 의존 liability 는 미평가로 기록됩니다. rank.weights.dev_flags가 0이 아니면 종합 순위와 최종 선별을 보류합니다. 실제 분석은 envs/setup_antibody.sh로 ANARCI를 설치하거나 mature.cdr.source=config와 검증한 1-based CDR 범위를 지정하세요. 미평가를 위험 없음으로 처리하지 않습니다.",
]


class Warnings(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.messages = []
        self.active = None
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        if tag == "li" and "data-report-i18n" in dict(attrs):
            self.active = []

    def handle_data(self, text):
        if self.active is not None:
            self.active.append(text)

    def handle_endtag(self, tag):
        if tag == "li" and self.active is not None:
            self.messages.append("".join(self.active))
            self.active = None


def execute_subset(runtime, messages):
    script = """
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const context=vm.createContext({});vm.runInContext(input.runtime,context);
const terms=input.messages.map(value=>context.I18N.term(value)),output={};
for(const locale of ['en','ko','en']){
  context.I18N.setLanguage(locale,{persist:false});
  output[locale]=terms.map(value=>context.I18N.value(value));
}
process.stdout.write(JSON.stringify(output));
"""
    done = subprocess.run(["node", "-e", script], input=json.dumps({"runtime": runtime, "messages": messages}), text=True, capture_output=True, check=True)
    return json.loads(done.stdout)


def catalog(runtime):
    return json.loads(re.search(r"const NCF_TRANSLATIONS = Object.freeze\((\{[\s\S]*?\})\);", runtime)[1])


def joined_notice():
    cfg = load_config()
    cfg["rank"]["weights"] = {"iptm": 1., "hotspot_contact_frac": .5, "esm_score": .3, "dev_flags": -.4}
    cfg["rank"]["fallback_to_ineligible"] = True
    records = pd.DataFrame([
        {"composite_score": float("nan"), "iptm": .8, "hotspot_contact_frac": .5,
         "esm_score": 1., "dev_flags": 0, "dev_cdr_evaluated": False, "n_contacts": 3},
        {"composite_score": float("nan"), "iptm": .7, "hotspot_contact_frac": .5,
         "esm_score": 1., "dev_flags": 0, "dev_cdr_evaluated": True, "n_contacts": 0},
        {"composite_score": float("nan"), "iptm": .6, "hotspot_contact_frac": .5,
         "esm_score": 1., "dev_flags": 0, "dev_cdr_evaluated": True, "n_contacts": 3, "hotspot_mapping_failed": True},
        {"composite_score": float("nan"), "iptm": .5, "hotspot_contact_frac": .5,
         "esm_score": float("nan"), "dev_flags": 0, "dev_cdr_evaluated": True, "n_contacts": 3},
        {"composite_score": .1, "iptm": .4, "hotspot_contact_frac": .5,
         "esm_score": 1., "dev_flags": 0, "dev_cdr_evaluated": True, "n_contacts": 3},
    ])
    return rank_select.ranking_warnings(records, cfg)[0]


def test_rendered_warning_subset_translates_science_and_joined_clauses_without_source_mutation(tmp_path):
    cfg = load_config()
    cfg["report"]["viewer"] = "none"
    cfg["target"]["hotspots"] = []
    run = tmp_path / "run"
    run.mkdir()
    config = run / "run_config.yaml"
    config.write_text(yaml.safe_dump(cfg, allow_unicode=True))
    original_config = config.read_bytes()
    raw = {"raw_notes": ["완료", "미평가", "saved/원본_{0}.fasta", '</script><img id="must-not-exist">']}
    context = {**raw, "warnings": SCIENCE_WARNINGS + [joined_notice()]}
    source_context = copy.deepcopy(context)
    frame = pd.DataFrame([{"design_id": "나노바디2(A3);O'Neil", "sequence": "ACDEFG", "iptm": .30000000000000004}])
    source_frame = frame.copy(deep=True)
    path = report.generate_report(run, cfg, context, scored=frame)
    document = path.read_text()
    messages = Warnings(document).messages
    assert messages[:7] == SCIENCE_WARNINGS
    assert joined_notice() in messages
    runtime = re.search(r'<script id="report-i18n-runtime">([\s\S]*?)</script>', document)[1]
    translated = execute_subset(runtime, messages)
    assert translated["ko"] == messages
    assert all(not re.search(r"[가-힣]", message) for message in translated["en"])
    assert "msa/saved_{0}.a3m" in translated["en"][1]
    assert "Composite score and recorded rank are held for 4/5 models." in translated["en"][7]
    assert "mapping is incomplete" in translated["en"][7]
    assert "without median imputation" in translated["en"][7]
    assert "fallback_to_ineligible=true" in translated["en"][7]
    assert not document.count('<img id="must-not-exist">')
    assert "나노바디2(A3);O&#39;Neil" in document
    originals = [json.loads(html.unescape(block)) for block in re.findall(r"<pre[^>]*>([\s\S]*?)</pre>", document) if '"raw_notes"' in block]
    assert len(originals) == 1 and originals[0]["raw_notes"] == raw["raw_notes"]
    assert context == source_context
    pd.testing.assert_frame_equal(frame, source_frame)
    assert config.read_bytes() == original_config


@pytest.mark.parametrize("marked,authored", [(True, True), (False, True), (True, False)])
def test_clause_selection_is_limited_to_owned_ranking_notice_prefix(marked, authored):
    notice = joined_notice() if authored else "사용자 메모. ANARCI 환경 또는 서열에 맞는 CDR 범위를 확인하세요."
    tag = 'span data-report-i18n' if marked else 'pre'
    runtime = report._report_i18n_runtime(f'<{tag}>{html.escape(notice)}</{tag.split()[0]}>')
    selected = catalog(runtime)
    key = "ANARCI 환경 또는 서열에 맞는 CDR 범위를 확인하세요."
    assert (key in selected) is (marked and authored)
    if marked and authored:
        assert execute_subset(runtime, [notice])["en"][0].startswith("Composite score and recorded rank")
    else:
        assert execute_subset(runtime, [notice])["en"] == [notice]
