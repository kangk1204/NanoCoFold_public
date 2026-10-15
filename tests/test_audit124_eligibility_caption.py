"""The real offline viewer localizes eligibility without altering saved data."""
import json
import os

import pytest
from test_reassessment_presentation import _payload,_write_document


@pytest.mark.skipif(os.environ.get('NANOCOFOLD_BROWSER_TEST')!='1',reason='Real Chromium caption gate')
@pytest.mark.parametrize('basis',['recorded','quality'])
@pytest.mark.parametrize('eligible',[True,False,None])
def test_real_caption_round_trip_preserves_model_and_numeric_payload(tmp_path,basis,eligible):
    from playwright.sync_api import sync_playwright,expect
    payload=_payload(tmp_path)
    row=payload['candidates']['rows'][0]
    row.update(final_eligible=eligible,quality_eligible=eligible)
    document=_write_document(tmp_path,payload)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        page=browser.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(document.as_uri())
        expect(page.locator('#viewer-candidate-select')).to_have_value(row['candidate_id'])
        page.locator('#eligibility-basis').select_option(basis)
        original=page.locator('#result-data').text_content()
        ko_basis='실행 당시 기록 판정' if basis=='recorded' else '품질 판정 (결합 부위 제외)'
        en_basis='Assessment recorded at execution' if basis=='recorded' else 'Quality assessment (excluding hotspots)'
        en_status={True:'Eligible',False:'Ineligible',None:'Not evaluated'}[eligible]
        ko_status={True:'적격',False:'부적격',None:'미평가'}[eligible]
        for language in ['en','ko','en']:
            page.locator('#language-select').select_option(language)
            expect(page.locator('#candidate-status')).to_contain_text(
                f'{en_basis}: {en_status}' if language=='en' else f'{ko_basis}: {ko_status}')
            expect(page.locator('#viewer-candidate-select')).to_have_value(row['candidate_id'])
            assert page.locator('#result-data').text_content()==original
        assert json.loads(original)['candidates']['rows'][0]['final_eligible'] is eligible
        assert errors==[]
        browser.close()
