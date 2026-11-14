"""Stored approval belongs to a researcher, not an input, draft or browser tab."""
import asyncio
import re

import httpx

from nanocofold.platform.app import create_app
from nanocofold.platform.lab import LabAccounts


def test_local_preference_survives_restart_and_overrides_stale_input(tmp_path, monkeypatch):
    from nanocofold.platform.inputs import InputManager
    monkeypatch.setattr(InputManager, 'review', lambda self, payload: payload)
    monkeypatch.setattr(InputManager, 'complex', lambda self, payload: payload)
    root = tmp_path / 'work'
    async def check(app, first):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1', headers={'x-platform-token':app.state.token}) as client:
            if first:
                assert (await client.get('/api/msa-consent')).json() == {'allowed':False,'scope':'installation'}
                for bad in ({'allowed':'true'},{'allowed':1},{'allowed':True,'owner':'other'},{}):
                    assert (await client.post('/api/msa-consent', json=bad)).status_code == 400
                assert (await client.post('/api/msa-consent', json={'allowed':True})).status_code == 200
            assert (await client.get('/api/session')).json()['public_msa_consent'] is True
            payload={'settings':{'msa_source':'cache','allow_public_msa_server':False},'config':{'screen':{'colabfold':{'msa_source':'local_mmseqs2'}}}}
            result=(await client.post('/api/inputs/review',json=payload)).json()
            assert result['settings']['allow_public_msa_server'] is True
            assert result['settings']['msa_source']=='cache'
            assert result['config']['screen']['colabfold']['msa_source']=='local_mmseqs2'
            if not first:
                await client.post('/api/msa-consent',json={'allowed':False})
                result=(await client.post('/api/inputs/complex',json={'settings':{'msa_source':'public_api','allow_public_msa_server':True}})).json()
                assert result['settings']['allow_public_msa_server'] is False
    for first in (True,False):
        app=create_app(root,start_workers=False)
        try: asyncio.run(check(app,first))
        finally: app.state.inputs.close();app.state.jobs.close()


def test_account_consent_is_shared_by_tabs_but_not_researchers(tmp_path,monkeypatch):
    from nanocofold.platform.batches import BatchManager
    monkeypatch.setattr(BatchManager,'review',lambda self,payload,**kw:payload)
    root=tmp_path/'work';app=create_app(root,start_workers=False);users=LabAccounts(root)
    users.set_password('Alpha','test-alpha-private');users.set_password('Beta','test-beta-private')
    async def check():
        transport=httpx.ASGITransport(app=app);origin='http://127.0.0.1'
        async with httpx.AsyncClient(transport=transport,base_url=origin) as a,httpx.AsyncClient(transport=transport,base_url=origin) as b:
            for client,user,password in ((a,'Alpha','test-alpha-private'),(b,'Beta','test-beta-private')):
                assert (await client.post('/lab/login',headers={'origin':origin},data={'name':user,'password':password})).status_code==303
                token=re.search(r'window.PLATFORM_TOKEN = "([0-9a-f]+)"',(await client.get('/')).text)[1]
                client.headers.update({'x-platform-token':token})
            await a.post('/api/msa-consent',json={'allowed':True})
            assert (await a.get('/api/msa-consent')).json()=={'allowed':True,'scope':'account'}
            assert (await b.get('/api/msa-consent')).json()=={'allowed':False,'scope':'account'}
            assert (await a.post('/api/batches/review',json={'api_consent':False})).json()['api_consent'] is True
            await a.post('/api/msa-consent',json={'allowed':False})
            assert (await a.post('/api/batches/review',json={'api_consent':True})).json()['api_consent'] is False
    try: asyncio.run(check())
    finally: app.state.inputs.close();app.state.jobs.close()
