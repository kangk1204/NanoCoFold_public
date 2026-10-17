"""Locate bad library entries and keep bilingual rendered prose readable."""
import json
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import pytest

from nanocofold import report
from nanocofold.config import load_config
from test_audit126_report_warnings import execute_subset, Warnings
from test_supplied_html_boot import supplied_server as shared_server


@pytest.fixture(scope="module")
def supplied_server(tmp_path_factory):
    yield from shared_server.__wrapped__(tmp_path_factory)


class Fragments(HTMLParser):
    def __init__(self, document):
        super().__init__();self.stack=[];self.parts=[];self.feed(document)
    def handle_starttag(self, tag, attrs):self.stack.append((tag,'data-report-i18n' in dict(attrs)))
    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:self.stack=self.stack[:index];break
    def handle_data(self, value):self.parts.append((value,any(owned for _,owned in self.stack)))


def test_actual_report_sentence_units_have_english_word_boundaries_and_original_values(tmp_path):
    cfg=load_config();cfg['report']['viewer']='none'
    raw=pd.DataFrame([{'design_id':'사용자_{0}', 'sequence':'ACDE', 'iptm':.30000000000000004}]);before=raw.copy(deep=True)
    document=report.generate_report(tmp_path,cfg,{},scored=raw).read_text()
    runtime=re.search(r'<script id="report-i18n-runtime">([\s\S]*?)</script>',document)[1]
    guide=document.split('id="report-guide"',1)[1].split('</details>',1)[0]
    fragments=Fragments(guide).parts
    messages=[text for text,owned in fragments if owned]
    translated=execute_subset(runtime,messages)
    for language in ['en','ko']:
        iterator=iter(translated[language]);text=''.join(next(iterator) if owned else fragment for fragment,owned in fragments)
        if language=='en':
            assert 'ipSAE and ipTM describe' in text
            assert 'interface pLDDT and interface PAE' in text
            assert 'contact fraction is' in text
            assert not re.search(r'ipSAEand|ipTMare|pLDDTand|fractionis|experimentsthrough',text)
        else:assert 'ipSAE와 ipTM은' in text and '결합 부위 접촉률은' in text
    assert 'data-recorded-value="0.30000000000000004"' in document
    pd.testing.assert_frame_equal(raw,before)


@pytest.mark.parametrize('target',['/Users/Research User/private_target','C:\\Users\\Research User\\private_target'])
def test_real_path_redaction_does_not_break_owned_msa_warning_translation(tmp_path,target):
    cfg=load_config();cfg['report']['viewer']='none'
    warning=f'캐시된 MSA가 없습니다: {target}. 서열만 입력으로 단일서열 모드(msa=empty)로 진행합니다. 정확도를 높이려면 structure.msa.backend=colabfold_search 로 MSA를 먼저 생성하세요.'
    context={'warnings':[warning]}
    document=report.generate_report(tmp_path,cfg,context).read_text()
    messages=Warnings(document).messages
    runtime=re.search(r'<script id="report-i18n-runtime">([\s\S]*?)</script>',document)[1]
    english=execute_subset(runtime,messages)['en'][0]
    assert english.startswith('No cached MSA is available: [local path]. Continuing')
    assert not re.search('[가-힣]',english) and 'private_target' not in document
    assert context['warnings']==[warning]


def test_actual_local_fasta_parser_names_the_bad_entry_and_keeps_valid_records():
    source=(Path(report.__file__).parent/'templates/platform.js').read_text()
    parser=source.split('function noviceFasta(content) {',1)[1].split('function noviceInputFasta',1)[0]
    source='function noviceFasta(content) {'+parser
    entries=[f'>cand_{i:03d}\nACDEFGHIK' for i in range(60)]
    invalid='\n'.join(entries).replace('>cand_040\nACDEFGHIK','>cand_040\nACDXBZ')
    script=source+'\nconst x=JSON.parse(require("fs").readFileSync(0,"utf8"));let error;try{noviceFasta(x.bad)}catch(e){error=e.message};process.stdout.write(JSON.stringify({error,rows:noviceFasta(x.good)}))'
    done=subprocess.run(['node','-e',script],input=json.dumps({'bad':invalid,'good':'\n'.join(entries)}),text=True,capture_output=True,check=True)
    result=json.loads(done.stdout)
    assert '41번째 후보 (cand_040)' in result['error'] and 'B, X, Z' in result['error']
    assert [row['name'] for row in result['rows']]==[f'cand_{i:03d}' for i in range(60)]
    assert all(row['sequence']=='ACDEFGHIK' for row in result['rows'])


def test_actual_cdr_zero_weight_fallback_notice_survives_delivered_translation(tmp_path,monkeypatch):
    from nanocofold.state import RunState
    from test_usability_cli_guidance import test_cdr_guidance_distinguishes_ranking_weight_from_final_selection
    recorded=[];original=RunState.add_warnings
    def capture(self,*values):
        recorded.extend(values)
        return original(self,*values)
    monkeypatch.setattr(RunState,'add_warnings',capture)
    test_cdr_guidance_distinguishes_ranking_weight_from_final_selection(tmp_path,monkeypatch,0.,'ranked',True)
    messages=[message for group in recorded for message in (group if isinstance(group,list) else [group])]
    assert any('CDR 평가가 없으면 최종 적격은 통과하지 않으며' in message for message in messages)
    cfg=load_config();cfg['report']['viewer']='none'
    document=report.generate_report(tmp_path,cfg,{'warnings':messages}).read_text()
    runtime=re.search(r'<script id="report-i18n-runtime">([\s\S]*?)</script>',document)[1]
    english=execute_subset(runtime,Warnings(document).messages)['en']
    assert all(not re.search('[가-힣]',message) for message in english), english
    assert any('does not pass final eligibility' in message for message in english)
    assert any('not approval for a synthesis order' in message for message in english)


@pytest.mark.skipif(os.getenv('NANOCOFOLD_BROWSER_TEST')!='1',reason='Actual local HTTP UI Chromium check')
@pytest.mark.parametrize('width',[390,1400])
def test_bad_library_entry_is_live_localized_and_blocks_advance_until_corrected(supplied_server,width):
    from playwright.sync_api import expect,sync_playwright
    _,url=supplied_server
    entries=[f'>cand_{i:03d}\nACDEFGHIK' for i in range(60)]
    good='\n'.join(entries);bad=good.replace('>cand_040\nACDEFGHIK','>cand_040\nACDXBZ')
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page=browser.new_page(viewport={'width':width,'height':900});errors=[];computations=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:computations.append(request.url) if request.method=='POST' and re.search(r'/api/(?:jobs|environment/check)$',request.url) else None)
        page.add_init_script("localStorage.setItem('nanocofold.language','ko')")
        page.goto(url);expect(page.locator('body')).to_have_attribute('data-startup','ready')
        page.get_by_role('button',name='보유 나노바디 비교 · 입력하기',exact=True).click()
        page.locator('#input-kind').select_option('fasta')
        page.locator('#target-fasta').fill('>target\nACDEFGHIKLMNPQRSTVWY')
        page.locator('#vhh-fasta').fill(bad)
        help=page.locator('#vhh-fasta-validation');next_button=page.locator('#novice-next')
        for language in ['en','ko','en']:
            page.locator('#language-select').select_option(language)
            expect(help).to_contain_text('cand_040');expect(help).to_contain_text('B, X, Z')
            expect(help).to_contain_text('entry 41' if language=='en' else '41번째 후보')
            expect(next_button).to_be_disabled();expect(page.locator('#vhh-fasta')).to_have_value(bad)
        page.locator('#vhh-fasta').fill(good)
        expect(help).to_be_hidden();expect(next_button).to_be_enabled()
        expect(page.locator('#vhh-fasta')).to_have_value(good)
        assert errors==[] and computations==[]
        browser.close()
