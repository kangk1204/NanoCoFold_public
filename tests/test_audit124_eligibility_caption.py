"""The real offline viewer localizes eligibility without altering saved data."""
import json
import os
import copy

import pytest
from test_reassessment_presentation import _payload,_write_document


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST')!='1',reason='Real Chromium caption gate')
@pytest.mark.parametrize('basis',['recorded','quality'])
@pytest.mark.parametrize('eligible',[True,False,None,'held','wt_control','parent_control'])
def test_real_caption_round_trip_preserves_model_and_numeric_payload(tmp_path,basis,eligible):
    from playwright.sync_api import sync_playwright,expect
    payload=_payload(tmp_path)
    row=payload['candidates']['rows'][0]
    decision=False if eligible in ('held','wt_control','parent_control') else eligible
    row.update(final_eligible=decision,quality_eligible=decision)
    if eligible=='held':
        row.update(dev_assessment_policy='sequence_liability_screen_v2',
                   dev_assessment_status='review_required',
                   exclusion_reasons=['developability_review_required'])
    elif eligible in ('wt_control','parent_control'):
        row.update(exclusion_reason=eligible,exclusion_reasons=[eligible])
    document=_write_document(tmp_path,payload)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page=browser.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(document.as_uri())
        expect(page.locator('#viewer-candidate-select')).to_have_value(row['candidate_id'])
        page.locator('#eligibility-basis').select_option(basis)
        # The viewer releases the duplicate JSON text after parsing. Verify the
        # retained payload and the saved document rather than the freed buffer.
        saved_document=document.read_bytes()
        original=page.evaluate('JSON.stringify(window.NANOCOFOLD_OFFLINE)')
        assert json.loads(original)['candidates']['rows']==payload['candidates']['rows']
        assert page.locator('#result-data').text_content()==''
        ko_basis='실행 당시 기록 판정' if basis=='recorded' else '품질 판정 (결합 부위 제외)'
        en_basis='Assessment recorded at execution' if basis=='recorded' else 'Quality assessment (excluding hotspots)'
        en_status={True:'Eligible',False:'Criteria not met',None:'Not evaluated','held':'Held for review','wt_control':'WT / control','parent_control':'WT / control'}[eligible]
        ko_status={True:'적격',False:'기준 미충족',None:'미평가','held':'검토 보류','wt_control':'WT·대조군','parent_control':'WT·대조군'}[eligible]
        for language in ['en','ko','en']:
            page.locator('#language-select').select_option(language)
            expect(page.locator('#candidate-status')).to_contain_text(
                f'{en_basis}: {en_status}' if language=='en' else f'{ko_basis}: {ko_status}')
            expect(page.locator('#candidate-detail > p').first).to_have_text(
                f'Assessment under selected criteria: {en_status}' if language=='en' else f'선택한 기준의 판정: {ko_status}')
            expect(page.locator('#viewer-candidate-select')).to_have_value(row['candidate_id'])
            assert page.evaluate('JSON.stringify(window.NANOCOFOLD_OFFLINE)')==original
            assert page.locator('#result-data').text_content()==''
        assert document.read_bytes()==saved_document
        assert json.loads(original)['candidates']['rows'][0]['final_eligible'] is decision
        assert errors==[]
        browser.close()


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST')!='1',reason='Real Chromium decision guide gate')
def test_controls_have_a_separate_filter_and_translated_guide_without_changing_records(tmp_path):
    from playwright.sync_api import sync_playwright,expect
    payload=_payload(tmp_path)
    original_row=payload['candidates']['rows'][0]
    rows=[]
    for candidate,decision,reason in [('eligible',True,''),('notmet',False,'ipsae'),
                                     ('held',False,'developability_review_required'),
                                     ('WT',False,'wt_control'),('parent',False,'parent_control'),
                                     ('unknown',None,'')]:
        row=copy.deepcopy(original_row)
        row.update(candidate_id=candidate,final_eligible=decision,quality_eligible=decision,
                   exclusion_reason=reason,exclusion_reasons=[reason] if reason else [])
        if candidate=='held':
            row.update(dev_assessment_policy='sequence_liability_screen_v2',dev_assessment_status='review_required')
        rows.append(row)
    payload['candidates']['rows']=rows
    before=copy.deepcopy(payload)
    document=_write_document(tmp_path,payload)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page=browser.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(document.as_uri())
        expect(page.locator('#candidate-table tbody tr')).to_have_count(6)
        assert page.locator('.decision-guide').get_attribute('open') is None
        page.locator('.decision-guide summary').click()
        for locale in ['en','ko','en']:
            page.locator('#language-select').select_option(locale)
            expect(page.locator('.decision-guide dt')).to_have_count(6)
            expect(page.locator('.decision-guide')).to_contain_text('WT / control' if locale=='en' else 'WT·대조군')
            if locale=='en':
                assert not any('\uac00'<=char<='\ud7a3' for char in page.locator('.decision-guide').inner_text())
            page.locator('#eligibility').select_option('false')
            expect(page.locator('#candidate-table tbody tr')).to_have_count(2)
            assert set(page.locator('#candidate-table tbody tr').evaluate_all('(rows)=>rows.map(row=>row.dataset.id)'))=={'notmet','held'}
            page.locator('#eligibility').select_option('control')
            expect(page.locator('#candidate-table tbody tr')).to_have_count(2)
            assert set(page.locator('#candidate-table tbody tr').evaluate_all('(rows)=>rows.map(row=>row.dataset.id)'))=={'WT','parent'}
            expected='WT / control' if locale=='en' else 'WT·대조군'
            assert page.locator('#candidate-table tbody td[data-column-key="final_eligible"]').all_text_contents()==[expected,expected]
        assert page.evaluate('window.NANOCOFOLD_OFFLINE.candidates.rows')==rows
        assert payload==before and errors==[]
        browser.close()


def test_export_preserves_control_filter_and_all_original_records(tmp_path):
    from nanocofold.platform.results import ResultReader
    from test_offline_fasta_export import _payload as read_payload
    import pandas as pd
    _payload(tmp_path)
    scored=tmp_path/'synthetic/03_score/scored_candidates.csv'
    seed=pd.read_csv(scored).iloc[0].to_dict()
    records=[{**seed,'design_id':candidate,'final_eligible':eligible,'exclusion_reason':reason}
             for candidate,eligible,reason in [('WT',False,'wt_control'),('parent',False,'parent_control'),
                                              ('notmet',False,'ipsae'),('eligible',True,'')]]
    pd.DataFrame(records).to_csv(scored,index=False)
    reader=ResultReader(tmp_path/'synthetic')
    original=scored.read_bytes()
    for status,expected in [('control',{'WT','parent'}),('false',{'notmet'})]:
        raw,_,_=reader.export('html',{'eligibility':status,'eligibility_basis':'recorded'})
        payload=read_payload(raw)
        assert payload['filters']['eligibility']==status
        assert set(payload['provenance']['filtered_ids'])==expected
        assert {row['candidate_id'] for row in payload['candidates']['rows']}=={'WT','parent','notmet','eligible'}
        assert scored.read_bytes()==original


def test_static_control_caption_and_explicit_guide_preserve_scores(tmp_path):
    import pandas as pd
    from nanocofold import report
    from html.parser import HTMLParser
    frame=pd.DataFrame([{'design_id':'WT','sequence':'ACCDEFGHIK','final_eligible':False,
                         'exclusion_reason':'wt_control','iptm':.8123456789,'ipsae':.723456789}])
    original=frame.copy(deep=True)
    path=report.generate_report(tmp_path,{'run':{'mode':'mature'},'report':{'viewer':'none'}},{},scored=frame)
    class Content(HTMLParser):
        def __init__(self):
            super().__init__();self.tables=[];self.active=None;self.guide=False;self.terms=[];self.definitions=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='table': self.active=[]
            if tag=='details' and attrs.get('class')=='decision-guide':
                assert 'open' not in attrs
                self.guide=True
            if self.guide and tag in ('dt','dd'):
                assert 'data-report-i18n' in attrs
                (self.terms if tag=='dt' else self.definitions).append(tag)
        def handle_data(self,text):
            if self.active is not None: self.active.append(text)
        def handle_endtag(self,tag):
            if tag=='table' and self.active is not None:
                self.tables.append(''.join(self.active));self.active=None
            if tag=='details': self.guide=False
    parsed=Content();parsed.feed(path.read_text())
    tables=[table for table in parsed.tables if 'WT·대조군' in table]
    assert tables and all('기준 미충족' not in table for table in tables)
    assert len(parsed.terms)==len(parsed.definitions)==6
    pd.testing.assert_frame_equal(frame,original)
