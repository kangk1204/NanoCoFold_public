"""Outbound-only Sites synchronizer. No listener, shell commands or arbitrary paths."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
import mimetypes
import os
import re
import signal
import threading
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit, quote

from .. import __version__
from .inputs import InputManager, normalized_config
from .jobs import JobManager, TERMINAL
from .paths import contained, sha256
from .errors import public_error, public_log

AGENT_VERSION=4


def json_sha256(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def checked_at():
    return datetime.now(timezone.utc).isoformat()


def public_diagnostics(value):
    """Keep readiness evidence while withholding host paths and raw probe output."""
    from ..utils.presentation import portable_metadata

    def without_secrets(item):
        if isinstance(item, dict):
            return {key: without_secrets(child) for key, child in item.items()
                    if key.lower() not in ('stdout','stderr','raw','command','cmd','sys_path','service_token','agent_token',
                        'authorization','headers','token','api_key','access_token','refresh_token','password','credentials')}
        if isinstance(item, list):
            return [without_secrets(child) for child in item]
        return item

    # Keep pathname keys and encoded payload context available to the sanitizer.
    return portable_metadata(without_secrets(value))


def validate_agent_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',value):
        raise ValueError('계산 agent ID 형식 오류')
    return value


class _NoSiteRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Service credentials belong only to the configured Site. urllib's
        # default handler copies custom authorization headers to redirects.
        raise ValueError('인증된 Sites 요청의 리다이렉트는 허용하지 않습니다.')


class Client:
    UPLOAD_LIMIT=100*1024*1024
    CHUNK_SIZE=32*1024*1024
    def __init__(self, config):
        self.url = config['site_url'].rstrip('/')
        parsed = urlsplit(self.url)
        if (parsed.scheme != 'https' or parsed.path not in ('', '/') or not parsed.hostname
                or not parsed.hostname.endswith('.chatgpt.site') or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.port not in (None,443)):
            raise ValueError('승인된 HTTPS Sites 주소가 필요합니다.')
        self.url='https://'+parsed.hostname
        self.hostname=parsed.hostname
        self.opener=urllib.request.build_opener(_NoSiteRedirects())
        self.use_proxy=bool(urllib.request.getproxies().get('https')) and not urllib.request.proxy_bypass(self.hostname)
        # Queue, heartbeat and export threads never share an in-flight socket.
        self.connections=threading.local()
        self.requested_agent_id=os.environ.get('NANOCOFOLD_AGENT_ID') if 'NANOCOFOLD_AGENT_ID' in os.environ else config.get('agent_id')
        self.headers = {'OAI-Sites-Authorization': 'Bearer '+config['service_token'], 'Authorization': 'Bearer '+config['agent_token']}
        self.set_agent_id(self.requested_agent_id if self.requested_agent_id is not None else 'legacy')
    def set_agent_id(self,value):
        self.agent_id=validate_agent_id(value)
        self.headers['X-NanoCoFold-Agent-ID']=self.agent_id
    def request(self, path, payload=None):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() if payload is not None else None
        headers={**self.headers,'Content-Type':'application/json'}
        if self.use_proxy:
            # Preserve configured proxy routing and its redirect protection.
            req=urllib.request.Request(self.url+'/api/'+path,data=data,headers=headers)
            with self.opener.open(req,timeout=30) as response:return json.load(response)
        return json.loads(self.exchange('GET' if data is None else 'POST','/api/'+path,
                                        body=data,headers=headers,timeout=30))
    def discard_connection(self,connection):
        try:connection.close()
        except OSError:pass
        finally:
            if getattr(self.connections,'connection',None) is connection:
                del self.connections.connection
    def exchange(self,method,path,*,body,headers,timeout,upload=False):
        connection=getattr(self.connections,'connection',None)
        if connection is None:
            connection=http.client.HTTPSConnection(self.hostname,timeout=timeout)
            self.connections.connection=connection
        try:
            connection.timeout=timeout
            if getattr(connection,'sock',None) is not None:connection.sock.settimeout(timeout)
            connection.request(method,path,body=body,headers=headers)
            response=connection.getresponse()
            if 300<=response.status<400:
                raise ValueError('인증된 Sites 요청의 리다이렉트는 허용하지 않습니다.')
            if (response.status!=200 if upload else not 200<=response.status<300):
                data=response.read(65536)
                if upload:raise RuntimeError(f'파일 동기화 실패 HTTP {response.status}: '+data.decode(errors='replace')[:300])
                raise urllib.error.HTTPError(self.url+path,response.status,getattr(response,'reason',''),
                                             getattr(response,'headers',{}),io.BytesIO(data))
            # Fully consume successful responses before socket reuse. Upload
            # acknowledgements are bounded; discard an unexpectedly large one.
            data=response.read(65537) if upload else response.read()
            if upload and len(data)>65536:self.discard_connection(connection)
            return data
        except Exception:
            self.discard_connection(connection)
            # Never replay a POST/PUT after an ambiguous acknowledgement.
            # The next agent tick retries using durable operation/job receipts.
            raise
    def upload(self, key, path):
        if path.stat().st_size<=self.UPLOAD_LIMIT:return self._upload(key,path)
        original=sha256(path);parts=[];combined=hashlib.sha256()
        with tempfile.TemporaryDirectory(prefix='nanocofold-transfer-') as temporary:
            with path.open('rb') as source:
                number=0
                while chunk:=source.read(self.CHUNK_SIZE):
                    combined.update(chunk);part=Path(temporary)/str(number);part.write_bytes(chunk)
                    name=key+'.chunks/'+original+'/'+str(number)
                    self._upload(name,part);parts.append({'key':name,'size':len(chunk),'sha256':sha256(part)})
                    number+=1
        if combined.hexdigest()!=original:raise ValueError('파일이 전송 중 바뀌었습니다.')
        self.request('agent/multipart',{'key':key,'sha256':original,'size':sum(p['size'] for p in parts),'content_type':mimetypes.guess_type(path.name)[0] or 'application/octet-stream','parts':parts})
    def _upload(self, key, path):
        with path.open('rb') as file:
            self.exchange('PUT','/api/agent/files/'+quote(key,safe='/'),body=file,timeout=120,upload=True,
                          headers={**self.headers,'Content-Length':str(path.stat().st_size),
                              'Content-Type':mimetypes.guess_type(path.name)[0] or 'application/octet-stream','X-Content-SHA256':sha256(path)})


class Agent:
    def __init__(self, workspace, client, *, agent_id=None):
        self.workspace=Path(workspace).resolve();self.client=client
        self.root=self.workspace/'.nanocofold/cloud';self.root.mkdir(parents=True,exist_ok=True)
        identity=self.root/'agent_id'
        requested=agent_id if agent_id is not None else os.environ.get('NANOCOFOLD_AGENT_ID') if 'NANOCOFOLD_AGENT_ID' in os.environ else getattr(client,'requested_agent_id',None)
        if requested is not None:requested=validate_agent_id(requested)
        if identity.exists():
            if identity.is_symlink():raise ValueError('agent ID 파일은 심볼릭 링크일 수 없습니다.')
            self.agent_id=validate_agent_id(identity.read_text().strip())
            if requested is not None and requested!=self.agent_id:raise ValueError('기존 작업 공간의 agent ID를 변경할 수 없습니다. 새 계산 서버는 새 작업 공간을 사용하세요.')
        else:
            self.agent_id=requested or 'legacy'
            try:
                with identity.open('x') as stream:
                    stream.write(self.agent_id+'\n');stream.flush();os.fsync(stream.fileno())
                identity.chmod(0o600)
            except FileExistsError:
                stored=validate_agent_id(identity.read_text().strip())
                if stored!=self.agent_id:raise ValueError('동시에 다른 agent ID가 등록되었습니다.')
        if hasattr(client,'set_agent_id'):client.set_agent_id(self.agent_id)
        self.manager=JobManager(self.workspace);self.inputs=InputManager(self.workspace)
        with self.manager.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cloud_operations (id TEXT PRIMARY KEY,response TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS cloud_sync (id TEXT PRIMARY KEY,remote_id TEXT,result_synced INTEGER DEFAULT 0,action_id TEXT)')
        self.stop=False;self.diagnostics={};self.sync_errors={}
        self.export_errors={};self.export_thread=None;self.export_lock=threading.Lock()
        self.heartbeat_stop=threading.Event()
    @staticmethod
    def capabilities():
        from ..engine_comparison import AVAILABLE_ENGINES
        from ..provenance import fingerprint
        root = Path(__file__).resolve().parents[1]
        python_files = {str(path.relative_to(root)): sha256(path)
                        for path in sorted(root.rglob('*.py'))
                        if '__pycache__' not in path.parts}
        return {'comparison_public_api':True,'review_bound_diagnostics':True,'resume_action_receipts_v1':True,
                'comparison_engine_selection':True,'comparison_predictors':list(AVAILABLE_ENGINES),
                'complex_comparison':True,'complex_comparison_schema':1,'complex_comparison_predictors':list(AVAILABLE_ENGINES),
                'package_version':__version__,'application_python_sha256':fingerprint(python_files),
                'application_python_sha256_algorithm':'sha256(canonical JSON relative Python-path to file-sha256 mapping)',
                'application_python_files':len(python_files),'cloud_agent_sha256':sha256(Path(__file__))}
    def installation_identity(self):
        from ..provenance import application_identity
        return {'agent_id':self.agent_id,'agent_version':AGENT_VERSION,'package_version':__version__,
                'cloud_agent_sha256':sha256(Path(__file__)), 'application_sha256':application_identity()}
    def bounded_settings(self,mode,settings,engine=None):
        self.workflow(mode,settings)
        values=dict(settings)
        if 'comparison_engines' not in values or mode!='complex':
            values['engine']=settings.get('engine',engine or 'boltz2')
        elif engine is not None:
            raise ValueError('복합체 비교 요청에는 단일 engine 필드를 함께 보내지 마세요.')
        if mode!='complex':return self.settings(mode,values)
        from ..complex import validate_settings
        from ..complex_comparison import configure
        allowed={k:values[k] for k in ('engine','comparison_engines','seeds','recycles','diffusion_steps','models_per_seed','msa','msa_source','allow_public_msa_server','restraints') if k in values}
        validated=validate_settings(allowed)
        return configure({'run':{'mode':mode},'complex':validated},validated)
    def input_fingerprints(self,folder):
        files={}
        for path in sorted(folder.rglob('*')):
            if path.is_symlink():raise ValueError('검토한 입력 파일은 심볼릭 링크일 수 없습니다.')
            if path.is_file() and path not in (folder/'config.yaml',folder/'platform_input.json'):
                files[str(path.relative_to(folder))]=sha256(path)
        return files
    def save_review_receipt(self,result,mode,settings):
        folder=contained(self.inputs.root,result['input_id'],exists=True)
        review=copy.deepcopy(result['review'])
        files=self.input_fingerprints(folder)
        receipt={'schema_version':1,'agent_id':self.agent_id,'input_id':result['input_id'],'mode':mode,
                 'installation_identity':self.installation_identity(),
                 'created_at':checked_at(),'settings_sha256':json_sha256(result['config']),
                 'request_settings_sha256':json_sha256(self.bounded_settings(mode,settings)),
                 'review_sha256':json_sha256(review),'files':files,
                 'input_sha256':json_sha256({'review':review,'files':files}),
                 'config_file_sha256':sha256(folder/'config.yaml'),
                 'msa_source':result['config'].get('screen',{}).get('colabfold',{}).get('msa_source'),
                 'allow_public_msa_server':result['config'].get('screen',{}).get('allow_public_msa_server',False)}
        receipt['receipt_sha256']=json_sha256(receipt)
        review['cloud_review_receipt']=receipt
        return self.inputs._save(folder,result['config'],review),receipt
    def reviewed_diagnostics_config(self,payload):
        identifier=payload['input_id']
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identifier):raise ValueError('잘못된 입력 ID입니다.')
        folder=contained(self.inputs.root,identifier,exists=True)
        if (folder/'platform_input.json').is_symlink():raise ValueError('검토한 입력 파일은 심볼릭 링크일 수 없습니다.')
        result=self.inputs.get(identifier)
        cfg=result['config'];review=copy.deepcopy(result['review']);receipt=review.pop('cloud_review_receipt',None)
        error='검토한 입력과 설정이 달라졌습니다. 입력을 다시 검토하세요.'
        if not isinstance(receipt,dict) or receipt.get('agent_id')!=self.agent_id or receipt.get('input_id')!=identifier:raise ValueError(error)
        if receipt.get('receipt_sha256')!=json_sha256({k:v for k,v in receipt.items() if k!='receipt_sha256'}):raise ValueError(error)
        if receipt.get('installation_identity')!=self.installation_identity():
            raise ValueError('계산 코드가 업데이트되었습니다. 현재 입력을 다시 검토하고 환경을 점검하세요.')
        files=self.input_fingerprints(folder)
        if (json_sha256(cfg)!=receipt.get('settings_sha256') or sha256(folder/'config.yaml')!=receipt.get('config_file_sha256')
                or json_sha256(review)!=receipt.get('review_sha256')
                or json_sha256({'review':review,'files':files})!=receipt.get('input_sha256')):raise ValueError(error)
        if 'mode' in payload and payload['mode']!=receipt['mode']:raise ValueError(error)
        if 'settings' in payload:
            requested=self.bounded_settings(receipt['mode'],payload['settings'],payload.get('engine'))
            if json_sha256(requested)!=receipt.get('request_settings_sha256'):raise ValueError(error)
        elif 'engine' in payload and payload['engine']!=cfg.get('complex' if receipt['mode']=='complex' else 'screen',{}).get('engine'):
            raise ValueError(error)
        for key in ('settings_sha256','input_sha256','receipt_sha256'):
            if key in payload and payload[key]!=receipt[key]:raise ValueError(error)
        # Validate the frozen config itself; never overlay current workspace defaults.
        from ..config import _validate
        _validate(cfg)
        return cfg,receipt
    def operation(self, op):
        if op.get('agent_id','legacy')!=self.agent_id:raise ValueError('다른 계산 서버의 요청을 실행할 수 없습니다.')
        with self.manager.store.connect() as db:
            saved=db.execute('SELECT response FROM cloud_operations WHERE id=?',(op['id'],)).fetchone()
        if saved: return json.loads(saved['response'])
        payload=json.loads(op['payload']); kind=op['kind']
        if payload.get('_file'):payload=self.client.request('agent/files/'+quote(payload['_file'],safe='/'))
        if kind=='inspect':
            allowed={k:payload[k] for k in ('filename','content','pdb_id','assembly_id','model_index') if k in payload}
            info=self.inputs.load(allowed)
            source=contained(self.root,op['id']+'.json')
            source.write_text(json.dumps(allowed,ensure_ascii=False))
            info['cloud_structure_id']=op['id']
            info['structure_content']=self.inputs.structure(info['input_id'])
            response={'state':'complete','response':info}
        elif kind=='uniprot':
            import re
            accession=str(payload.get('accession','')).strip()
            if not re.fullmatch(r'[A-Za-z0-9]{6,10}(?:-[0-9]+)?',accession):raise ValueError('UniProt accession 형식 오류')
            with urllib.request.urlopen('https://rest.uniprot.org/uniprotkb/'+accession+'.fasta',timeout=30) as r:
                fasta=r.read(100000).decode()
            from .inputs import parse_fasta
            name,sequence=parse_fasta(fasta,single=True,candidate_ids=False)[0]
            response={'state':'complete','response':{'name':name,'sequence':sequence,'accession':accession,'isoform':accession.split('-',1)[1] if '-' in accession else 'canonical','source_url':'https://rest.uniprot.org/uniprotkb/'+accession+'.fasta','fasta_sha256':hashlib.sha256(fasta.encode()).hexdigest()}}
        elif kind=='diagnostics':
            from ..diagnostics import doctor
            if 'input_id' in payload:
                cfg,receipt=self.reviewed_diagnostics_config(payload)
                context={k:receipt[k] for k in ('input_id','settings_sha256','input_sha256','receipt_sha256')}
                context.update(bound=True,settings_match=True,inputs_match=True)
            else:
                mode=payload.get('mode','complex')
                override=self.bounded_settings(mode,payload.get('settings',{}),payload.get('engine'))
                cfg=normalized_config(self.workspace,override)
                context={'input_id':None,'settings_sha256':json_sha256(cfg),'input_sha256':None,
                         'bound':False,'settings_match':False,'inputs_match':False}
            self.diagnostics=public_diagnostics(doctor(cfg))
            context.update(checked_at=checked_at(),installation_identity=self.installation_identity(),ok=self.diagnostics.get('ok') is True)
            self.diagnostics.update(review_context=context,capabilities=self.capabilities(),agent_version=AGENT_VERSION)
            response={'state':'complete','response':self.diagnostics}
        elif kind=='prepare':
            mode=payload.get('mode')
            if mode not in ('complex','design','screen','mature'):raise ValueError('분석 모드 오류')
            settings=payload.get('settings',{})
            self.workflow(mode,settings)
            if mode=='complex':
                if payload.get('cloud_structure_id'):
                    source=json.loads(contained(self.root,payload['cloud_structure_id']+'.json',exists=True).read_text())
                    info=self.inputs.load(source)
                    byid={c['id']:c for c in info['chains']}
                    selected=[payload['target']]+[c for c in payload.get('context',[]) if c!=payload['target']]
                    entries=[]
                    for c in selected:
                        row=byid[c]
                        positions=[]
                        for r in row['residues']:
                            label=r['original_label'].split(':',1)[-1]
                            if not label.lstrip('-').isdigit():raise ValueError('삽입 잔기 번호가 모호합니다. 서열·번호를 직접 확인하세요.')
                            positions.append(int(label))
                        entries.append({'name':row['name'],'sequence':row['sequence'],'original_positions':positions,'reference_chain':c})
                    payload={**payload,'proteins':entries}
                result=self.inputs.complex({k:payload[k] for k in ('proteins','settings','reference','anchor_chain','interest_positions','mock') if k in payload})
            else:
                override=self.settings(mode,settings)
                if mode=='design':
                    # Clear inactive inputs from a restored workspace/draft;
                    # a new VHH design has no WT reference or input library.
                    override['mature']={'wt_sequence':'','wt_fasta':None,'wt_structure':None}
                mock=payload.get('mock',False)
                if type(mock) is not bool:raise ValueError('mock는 true/false입니다.')
                common={'mode':mode,'mock':mock,'provider':override['structure']['provider'],
                        'vhh_fasta':payload.get('vhh_fasta') if mode=='screen' else None,
                        'wt_fasta':payload.get('wt_fasta') if mode=='mature' else None,
                        'prediction_sequences':payload.get('prediction_sequences'),'config':override}
                hotspot_mode,hotspot_positions=self.design_hotspots(settings) if mode=='design' else (None,[])
                hotspot_mapping=[]
                if payload.get('cloud_structure_id'):
                    source=json.loads(contained(self.root,payload['cloud_structure_id']+'.json',exists=True).read_text())
                    info=self.inputs.load(source)
                    selection=self.inputs.analyze({'input_id':info['input_id'],'target':payload['target'],'context':payload.get('context',[])})
                    if mode=='design':
                        hotspots,hotspot_mapping=self.structure_hotspots(info,payload['target'],hotspot_positions)
                        common.update(hotspot_mode=hotspot_mode,prediction_hotspots='none' if hotspot_mode=='none' else 'reference')
                    else:
                        hotspots=payload.get('hotspots') or (selection.get('patches') or [{}])[0].get('positions')
                    # Coordinate review validates exposure, blockers and original numbering.
                    result=self.inputs.review({**common,'selection_id':selection['selection_id'],'hotspots':hotspots,'acknowledge':payload.get('acknowledge',False),'preparation':payload.get('preparation','coordinates')})
                else:
                    values=payload.get('proteins') or []
                    if not values:raise ValueError('표적 서열이 필요합니다.')
                    if not isinstance(values,list) or len(values)!=1:raise ValueError('단일 분석에는 표적 한 개만 입력하세요. 여러 표적은 배치 분석을 사용하세요.')
                    from ..complex import AA
                    entry=values[0];sequence=''.join(entry.get('sequence','').split()).upper();start=entry.get('start',1);end=entry.get('end',len(sequence))
                    if not sequence or set(sequence)-AA or type(start) is not int or type(end) is not int or not 1<=start<=end<=len(sequence):raise ValueError('표적 서열 또는 분석 구간 오류')
                    if mode=='design':
                        if any(not start<=pos<=end for pos in hotspot_positions):raise ValueError('결합 부위는 선택한 표적 분석 구간 안에 있어야 합니다.')
                        hotspots=[f'A:seq{pos-start+1}' for pos in hotspot_positions]
                        hotspot_mapping=[{'source_chain':'sequence','source_residue':pos,'engine_chain':'A','engine_position':pos-start+1,'numbering':'sequence'} for pos in hotspot_positions]
                        override['target']['hotspots']=hotspots
                        common.update(hotspots=hotspots,hotspot_mode=hotspot_mode)
                    result=self.inputs.fasta({**common,'content':'>target\n'+sequence[start-1:end]+'\n','name':entry.get('name','target')})
                if mode=='design':
                    # The reviewed source determines the numbering schema. A
                    # restored structure flag may remain on a fresh FASTA request.
                    if result['review'].get('preparation')=='sequence_prediction' and hotspot_mapping:
                        observed_to_full=result['review']['chains'][0]['observed_to_full']
                        for row in hotspot_mapping:
                            row['engine_position']=observed_to_full[str(row['engine_position'])]
                            row['numbering']='sequence'
                    result['review'].update(hotspot_mode=hotspot_mode,hotspot_mapping=hotspot_mapping)
                    result=self.inputs._save(Path(result['config_path']).parent,result['config'],result['review'])
            result,receipt=self.save_review_receipt(result,mode,settings)
            response={'state':'complete','response':{'input_id':result['input_id'],'review':result['review'],'config':result['config'],'mode':mode,
                      'review_receipt':receipt,'capabilities':self.capabilities(),'agent_version':AGENT_VERSION}}
            # Preserve originals and applied config in private cloud storage.
            folder=Path(result['config_path']).parent
            for p in folder.rglob('*'):
                if p.is_file() and p.stat().st_size<=100*1024*1024:
                    self.client.upload('inputs/'+result['input_id']+'/'+str(p.relative_to(folder)),p)
        else:raise ValueError('지원하지 않는 동기화 요청')
        with self.manager.store.connect() as db:db.execute('INSERT INTO cloud_operations VALUES(?,?)',(op['id'],json.dumps(response,ensure_ascii=False)))
        return response
    @staticmethod
    def workflow(mode,p):
        if mode not in ('complex','design','screen','mature'):raise ValueError('분석 모드 오류')
        if not isinstance(p,dict):raise ValueError('분석 설정은 객체여야 합니다.')
        workflow=p.get('workflow','direct')
        if workflow not in ('direct','colabfold_boltz2','design_colabfold_boltz2','engine_comparison','design_engine_comparison'):raise ValueError('지원하지 않는 분석 workflow입니다.')
        if workflow=='colabfold_boltz2' and mode not in ('screen','mature'):
            raise ValueError('ColabFold→Boltz-2 workflow는 screen/mature만 지원합니다.')
        if workflow=='design_colabfold_boltz2' and mode!='design':
            raise ValueError('초기 백본·CDR ColabFold workflow는 design만 지원합니다.')
        if workflow=='engine_comparison' and mode not in ('screen','mature'):
            raise ValueError('동일 서열 엔진 비교는 screen/mature에서 사용하세요.')
        if workflow=='design_engine_comparison' and mode!='design':
            raise ValueError('신규 나노바디 설계·엔진 비교는 design에서 사용하세요.')
        return workflow
    @staticmethod
    def design_hotspots(p):
        mode=p.get('hotspot_mode','none')
        if mode not in ('none','specified'):raise ValueError('결합 부위는 none 또는 specified를 선택하세요.')
        if mode=='none':return mode,[]
        raw=p.get('hotspot_positions','')
        if isinstance(raw,str):
            if len(raw)>512 or not re.fullmatch(r'\s*-?\d+(?:\s*,\s*-?\d+)*\s*',raw):raise ValueError('결합 부위는 원본 잔기 번호를 쉼표로 구분하세요(예: 45,46,47).')
            positions=[int(part.strip()) for part in raw.split(',')]
        elif isinstance(raw,list):positions=raw
        else:raise ValueError('결합 부위 번호 형식 오류')
        if not 1<=len(positions)<=12 or any(type(pos) is not int or not -999999<=pos<=999999 for pos in positions) or len(set(positions))!=len(positions):raise ValueError('결합 부위는 중복 없는 원본 잔기 번호 1–12개입니다.')
        return mode,sorted(positions)
    @staticmethod
    def structure_hotspots(info,target,positions):
        selected=next((chain for chain in info['chains'] if chain['id']==target),None)
        if selected is None:raise ValueError('선택한 표적 체인이 없습니다.')
        mapped=[];review=[]
        for pos in positions:
            matches=[row for row in selected['residues'] if row['original_label'].rsplit(':',1)[-1]==str(pos)]
            if len(matches)!=1:raise ValueError(f'원본 결합 부위 {pos}: 누락·삽입 번호 또는 중복 대응을 확인하세요.')
            row=matches[0]
            # An insertion-coded residue sharing this numeric author position is ambiguous.
            labels=[r['original_label'].rsplit(':',1)[-1] for r in selected['residues']]
            if any(re.fullmatch(re.escape(str(pos))+r'[A-Za-z]+',label) for label in labels):raise ValueError(f'원본 결합 부위 {pos}: 삽입 잔기 번호 대응이 모호합니다.')
            mapped.append(row['position'])
            review.append({'source_chain':selected.get('original_id',target),'source_residue':row['original_label'],'engine_chain':'A','engine_position':row['position'],'numbering':'pdb',
                           **({'author_chain':selected['author_chain']} if selected.get('author_chain') else {})})
        if len(set(mapped))!=len(mapped):raise ValueError('결합 부위의 원본·엔진 번호 대응이 모호합니다.')
        return mapped,review
    def settings(self,mode,p):
        # Construct a bounded configuration; cloud requests never supply paths, env or commands.
        workflow=self.workflow(mode,p)
        if mode=='complex':raise ValueError('복합체 설정은 complex 입력에서 검토하세요.')
        design_cascade=workflow=='design_colabfold_boltz2'
        cascade=workflow in ('colabfold_boltz2','design_colabfold_boltz2')
        comparison=workflow in ('engine_comparison','design_engine_comparison')
        if 'comparison_engines' in p and not comparison:
            raise ValueError('comparison_engines는 독립 엔진 비교에서만 지정하세요.')
        engine=p.get('engine','boltz2')
        if engine not in ('boltz2','chai1'):raise ValueError('엔진 오류')
        if cascade and engine!='boltz2':raise ValueError('ColabFold workflow의 최종 엔진은 Boltz-2입니다.')
        if comparison and engine!='boltz2':raise ValueError('비교 실행의 표적 구조 준비 엔진은 Boltz-2입니다.')
        from ..complex import validate_settings
        seed=p.get('seed',42)
        if type(seed) is not int or not 0<=seed<2**31:raise ValueError('seed는 0–2147483647 정수입니다.')
        common=validate_settings({**{k:p[k] for k in ('engine','recycles','diffusion_steps','models_per_seed','msa','restraints') if k in p},'seeds':[seed]})
        count=p.get('count',1000 if design_cascade else 4)
        maximum=10000 if cascade else 2000
        if type(count) is not int or not 1<=count<=maximum:raise ValueError(f'후보 수는 1–{maximum}입니다.')
        cfg={'run':{'mode':mode,'seed':seed},'screen':{'engine':engine,'boltz2_rescore_top':0,'restraint':{'enabled':False},'num_trunk_recycles':common['recycles'],'num_diffn_timesteps':common['diffusion_steps'],'num_diffn_samples':1},
             'score':{'boltz2_recycling_steps':common['recycles'],'boltz2_extra_args':['--no_kernels','--num_workers','0','--sampling_steps',str(common['diffusion_steps'])]},
             'structure':{'provider':engine,'msa':{'backend':'empty'},'boltz2':{'recycling_steps':common['recycles'],'diffusion_samples':1,'extra_args':['--no_kernels','--num_workers','0','--sampling_steps',str(common['diffusion_steps'])]},'chai1':{'num_trunk_recycles':common['recycles'],'num_diffn_timesteps':common['diffusion_steps']}},
             'rank':{'require_hotspot':False,'final_n':count},'design':{'num_designs':count,'filters':{'enabled':False,'top_n_to_score':count}}}
        if mode=='design':
            # A restored draft may retain inputs from another mode. New VHH
            # design must not attach an unrelated library or WT to its receipt.
            cfg['screen']['vhh_fasta']=None
            backbone=p.get('backbone_engine','rfantibody')
            if backbone not in ('rfantibody','boltzgen','both'):raise ValueError('초기 백본 엔진은 RFantibody, BoltzGen 또는 both입니다.')
            hotspot_mode,hotspot_positions=self.design_hotspots(p)
            cfg['design'].update(engine=backbone,hotspot_mode=hotspot_mode,refinement={'enabled':False})
            # Input review replaces these author/sequence positions with its verified mapping.
            cfg['target']={'hotspots':[f'A:seq{position}' for position in hotspot_positions]}
            if 'backbone_count' in p or design_cascade:
                backbone_count=p.get('backbone_count',2)
                if type(backbone_count) is not int or not 1<=backbone_count<=50:raise ValueError('엔진별 초기 백본 수는 1–50 정수입니다.')
                cfg['design']['num_designs']=backbone_count
                cfg['design']['filters']['top_n_to_score']=backbone_count
            if design_cascade:
                parents=p.get('parents_per_engine',2);final_count=p.get('final_count',100)
                if type(parents) is not int or not 1<=parents<=10 or parents>backbone_count:raise ValueError('엔진별 부모 수는 1–10이며 초기 백본 수 이하여야 합니다.')
                controls=parents*(2 if backbone=='both' else 1)
                if type(final_count) is not int or not controls<final_count<=min(2000,count):raise ValueError('최종 평가 수는 부모 대조 수보다 크고 전체 라이브러리 수 이하여야 합니다(최대 2,000).')
                cfg['design']['refinement']={'enabled':True,'library_size':count,'parents_per_engine':parents,'min_mutations':1,'max_mutations':5,'final_count':final_count}
                cfg['rank']['final_n']=final_count
        if mode=='mature':
            antifold=p.get('antifold',False)
            if type(antifold) is not bool:raise ValueError('antifold는 true/false입니다.')
            cfg['mature']={'prefilter':{'top_n':count,'enforce_threshold':False},'antifold':{'enabled':antifold}}
            if 'max_variants' in p:
                limit=p['max_variants']
                if type(limit) is not int or not 1<=limit<=100000:raise ValueError('준비 변이 수는 1–100000 정수입니다.')
                cfg['mature']['library']={'strategy':'dms','max_variants':limit}
        if cascade:
            values={}
            for key,default,lo,hi in (('top_n',20,1,2000),('diversity_n',0,0,2000),('audit_n',0 if design_cascade else 2,0,2000),
                                      ('cf_recycles',1,1,20),('cf_batch_size',32,1,512),('cf_models',1,1,5),('cf_seeds',1,1,10)):
                value=p.get(key,default)
                if type(value) is not int or not lo<=value<=hi:raise ValueError(f'{key}: {lo}–{hi} 정수가 필요합니다.')
                values[key]=value
            cfg['screen']['engine']='colabfold'
            cfg['screen']['cascade']={'enabled':True,'top_n':values['top_n'],'diversity_n':values['diversity_n'],'audit_n':values['audit_n']}
            if design_cascade:
                if values['diversity_n'] or values['audit_n']:raise ValueError('백본 CDR 탐색에서는 추가 대조·다양성 수를 0으로 설정하세요. 부모 대조는 최종 평가 수에 포함됩니다.')
                cfg['screen']['cascade'].update(final_count=final_count,top_n=final_count)
            msa_source=p.get('msa_source','cache')
            if msa_source not in ('cache','public_mmseqs2'):raise ValueError('ColabFold MSA는 cache 또는 public_mmseqs2입니다.')
            preset=p.get('cf_public_preset')
            if preset is not None or msa_source=='public_mmseqs2':
                from ..cascade import approved_public_sequences
                approved_public_sequences(preset)
            cfg['screen']['colabfold']={'num_recycles':values['cf_recycles'],'batch_size':values['cf_batch_size'],
                                       'num_models':values['cf_models'],'num_seeds':values['cf_seeds'],'msa_mode':'precomputed',
                                       'msa_max_sequences':2048,'msa_source':msa_source}
            if preset is not None:cfg['screen']['colabfold']['cf_public_preset']=preset
            prune=p.get('prune_unselected',False)
            if type(prune) is not bool:raise ValueError('prune_unselected는 true/false입니다.')
            if prune:
                if values['diversity_n'] or values['audit_n']:raise ValueError('구조 삭제 실행은 다양성·상위 밖 대조 수를 0으로 설정하세요.')
                cfg['screen']['cascade']['prune_unselected']=True
                cfg['screen']['cascade']['calibrate_wt']=True
        if comparison:
            from ..engine_comparison import LEGACY_ENGINES, AVAILABLE_ENGINES
            # A request without this field is the historical three-engine
            # contract. Fresh clients send their four-engine choice explicitly.
            predictors=p.get('comparison_engines',list(LEGACY_ENGINES))
            if (not isinstance(predictors,list) or not predictors or
                    any(not isinstance(e,str) or e not in AVAILABLE_ENGINES for e in predictors) or
                    len(set(predictors))!=len(predictors)):
                raise ValueError('comparison_engines는 중복 없는 지원 예측 엔진 목록이어야 합니다.')
            values={}
            for key,default,lo,hi in (('cf_recycles',3,1,20),('cf_batch_size',32,1,512),('cf_models',1,1,5),('cf_seeds',1,1,10)):
                value=p.get(key,default)
                if type(value) is not int or not lo<=value<=hi:raise ValueError(f'{key}: {lo}–{hi} 정수가 필요합니다.')
                values[key]=value
            msa_source=p.get('msa_source','local_mmseqs2')
            if not isinstance(msa_source,str) or msa_source not in ('cache','local_mmseqs2','public_api'):raise ValueError('엔진 비교 MSA는 cache, local_mmseqs2 또는 public_api를 사용하세요.')
            consent=p.get('allow_public_msa_server',False)
            if type(consent) is not bool:raise ValueError('allow_public_msa_server는 true/false입니다.')
            if msa_source=='public_api' and consent is not True:raise ValueError('public_api는 표적·생성 서열을 외부 서버로 전송합니다. allow_public_msa_server=true 동의가 필요합니다.')
            cfg['screen']['allow_public_msa_server']=consent
            cfg['screen']['comparison']={'enabled':True,'engines':list(predictors),'continue_on_error':True}
            cfg['screen']['opendde']={'enabled':'opendde' in predictors}
            cfg['screen']['cascade']={'enabled':False}
            cfg['screen']['colabfold']={'num_recycles':values['cf_recycles'],'batch_size':values['cf_batch_size'],
                'num_models':values['cf_models'],'num_seeds':values['cf_seeds'],'msa_mode':'precomputed',
                'msa_max_sequences':2048,'msa_source':msa_source}
            cfg['design']['refinement']={'enabled':False}
            if mode=='design':
                # The browser count is backbones per generator with one
                # sequence per backbone, so RF/BoltzGen budgets are comparable.
                cfg['design']['mpnn']={'seqs_per_struct':1}
            cfg['rank']['final_n']=count*len(predictors)
        strategy=p.get('mutation_strategy','random' if design_cascade else 'dms')
        if strategy not in ('dms','random'):raise ValueError('치환 방법 오류')
        if strategy=='random':
            if mode not in ('mature','design') or not cascade:raise ValueError('무작위 다중 치환은 mature/design의 ColabFold workflow에서 지원합니다.')
            low,high=p.get('mutation_min',1),p.get('mutation_max',5)
            if type(low) is not int or type(high) is not int or not 1<=low<=high<=5:raise ValueError('치환 잔기 수는 1–5 범위입니다.')
            if count<2:raise ValueError('무작위 라이브러리는 WT 포함 2개 이상입니다.')
            if p.get('antifold',False):raise ValueError('다중 치환에서는 AntiFold 사전 평가를 끄세요.')
            if design_cascade:cfg['design']['refinement'].update(min_mutations=low,max_mutations=high)
            else:
                cfg['mature']['library']={'strategy':'random','size':count,'min_mutations':low,'max_mutations':high}
                cfg['mature']['prefilter']['mode']='none'
        if design_cascade and strategy!='random':raise ValueError('백본 CDR 탐색은 무작위 치환 라이브러리를 사용합니다.')
        return cfg
    def sync_result(self, cloud_id,job):
        from .cloud_results import build_bundle
        import uuid
        generation=self.result_generation(job)
        if self.result_generation(self.manager.store.get(job['id']))!=generation:return False
        revision=uuid.uuid4().hex
        root=Path(job['run_dir']); bundle=self.root/'bundles'/cloud_id/revision
        prefix='results/'+cloud_id+'/'+revision+'/'
        meta=build_bundle(root,bundle,snapshot_root=Path(job['config_path']).parent)
        if self.result_generation(self.manager.store.get(job['id']))!=generation:return False
        for p in bundle.rglob('*'):
            if p.is_file():self.client.upload(prefix+str(p.relative_to(bundle)),p)
        if self.result_generation(self.manager.store.get(job['id']))!=generation:return False
        meta={**meta,'index_key':prefix+'index.json','prefix':prefix,'bundle_revision':revision}
        self.client.request('agent/results',{'id':cloud_id,'metadata':meta})
        # A manual resume during transfer must leave the new attempt pending.
        # Do not hold a SQLite write transaction across a network request.
        with self.manager.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=json.loads(db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
            if self.result_generation(current)!=generation:return False
            db.execute('UPDATE cloud_sync SET result_synced=1 WHERE id=?',(cloud_id,))
        return True
    @staticmethod
    def result_generation(job):
        return (job['state'],job.get('finished'),len(job.get('history',[])))
    def schedule_result(self,identifier,job):
        # Keep queue actions, cancellation and live logs responsive while a large
        # archive is built or uploaded. SQLite retains pending work on restart.
        with self.export_lock:
            if self.stop or (self.export_thread and self.export_thread.is_alive()):return
            def transfer():
                try:
                    self.sync_result(identifier,job)
                    self.export_errors.pop(identifier,None)
                except Exception as exc:
                    self.export_errors[identifier]=f'{type(exc).__name__}: {exc}'
                    print(f'[export] {identifier}: {type(exc).__name__}: {exc}',flush=True)
            self.export_thread=threading.Thread(target=transfer,name='sites-result-upload',daemon=True)
            self.export_thread.start()
    def submitted_job(self,identifier):
        # JobManager commits a deterministic submission before cloud_sync is
        # written. Recover that independent snapshot after a synchronizer crash.
        import uuid
        local_id=uuid.uuid5(uuid.NAMESPACE_URL,'nanocofold-sites:'+identifier).hex
        try:job=self.manager.store.get(local_id)
        except KeyError:return None
        return job if job.get('submission_id')==identifier else None
    def tick(self):
        queue=self.client.request('agent/queue')
        if queue.get('agent_id',self.agent_id)!=self.agent_id:raise ValueError('Sites 응답의 agent ID가 현재 서버와 다릅니다.')
        for op in queue['operations']:
            if op.get('agent_id','legacy')!=self.agent_id:continue
            try: result=self.operation(op)
            except Exception as exc:
                print(f'[operation] {type(exc).__name__}: {exc}',flush=True)
                result={'state':'failed','response':{'error':public_error(exc)}}
            self.client.request('agent/operations',{'id':op['id'],**result})
        # Include terminal remote jobs not yet acknowledged/uploaded by the cloud.
        ids={j['id']:j for j in queue['jobs'] if j.get('agent_id','legacy')==self.agent_id}
        with self.manager.store.connect() as db:
            pending=db.execute('SELECT * FROM cloud_sync WHERE result_synced=0').fetchall()
        for row in pending:ids.setdefault(row['id'],{'id':row['id'],'remote_id':row['remote_id'],'agent_id':self.agent_id})
        for cloud in ids.values():
            identifier=cloud['id']
            with self.manager.store.connect() as db:existing=db.execute('SELECT * FROM cloud_sync WHERE id=?',(identifier,)).fetchone()
            job=None
            try:
                if existing:job=self.manager.store.get(existing['remote_id'])
                else:
                    job=self.submitted_job(identifier)
                    if job is None:
                        request=json.loads(cloud['request'])
                        # Repeat frozen-input validation only for new submissions.
                        # Committed jobs retain their independent snapshots even
                        # if the synchronizer stopped before writing cloud_sync.
                        cfg,_=self.reviewed_diagnostics_config({'input_id':request['input_id'],'mode':request['mode']})
                        # Cloud result prefixes are owned by the cloud submission.
                        # Enable canonical alias reuse only after both agent and
                        # Site support an ownership-checked source-result resolver.
                        job=self.manager.create({'config':cfg,'mode':request['mode'],'name':request['name'],'budget_seconds':request['budget_seconds'],'submission_id':identifier,'reuse_results':False})
                    with self.manager.store.connect() as db:db.execute('INSERT INTO cloud_sync(id,remote_id) VALUES(?,?)',(identifier,job['id']))
                action_id=cloud.get('action_id')
                if cloud.get('action'):
                    action=json.loads(cloud['action'])
                    if action['kind']=='resume' and action_id is None:
                        raise ValueError('Cloud resume requires a stable action ID.')
                    # Validate receipts even after acknowledgement, so a reused
                    # ID with changed request bytes cannot be silently accepted.
                    applied=self.manager.store.resume_action_applied(job['id'],action_id,action)
                    if not existing or action_id!=existing['action_id']:
                        if not applied:
                            if action['kind']=='cancel':job=self.manager.cancel(job['id'])
                            elif action['kind']=='resume' and job['state'] in TERMINAL-{'complete'}:
                                job=self.manager.resume(job['id'],action.get('budget_seconds'),
                                                        action_id=action_id,action_payload=action)
                        with self.manager.store.connect() as db:db.execute('UPDATE cloud_sync SET action_id=?,result_synced=0 WHERE id=?',(action_id,identifier))
                logs=self.manager.logs(job['id'],max(0,Path(job['log_path']).stat().st_size-64000) if Path(job['log_path']).exists() else 0)
                public={k:v for k,v in job.items() if k not in ('config_path','run_dir','log_path','gpu_lock','descendants','last_descendants')};public.update(logs=logs['text'],agent_id=self.agent_id)
                public['logs']=public_log(public['logs'])
                if public.get('error'):public['error']=public_error(ValueError(public['error']))
                public=public_diagnostics(public)
                self.client.request('agent/status',{'id':identifier,'status':public,'action_id':action_id})
                if job['state'] in TERMINAL and job.get('run_dir') and Path(job['run_dir']).exists() and (not existing or not existing['result_synced']):self.schedule_result(identifier,job)
                self.sync_errors.pop(identifier,None)
            except Exception as exc:
                # Once a local submission exists, mapping/log/transport errors
                # affect synchronization only. A failed status must never hide
                # a worker that is already queued, running, or complete.
                if job is None and not existing:job=self.submitted_job(identifier)
                if not existing and job is None:self.client.request('agent/status',{'id':identifier,'status':{'id':None,'state':'failed','agent_id':self.agent_id,'error':public_error(exc)}})
                else:
                    self.sync_errors[identifier]=f'{type(exc).__name__}: {exc}'
                    print(f'[sync] {identifier}: {type(exc).__name__}: {exc}',flush=True)
        self.heartbeat()
    def heartbeat(self):
        self.client.request('agent/heartbeat',{'host':os.uname().nodename,'agent_id':self.agent_id,'version':AGENT_VERSION,
            'capabilities':self.capabilities(),'installation_identity':self.installation_identity(),
            'diagnostics':public_diagnostics(self.diagnostics),'sync_errors':{k:public_error(ValueError(v)) for k,v in {**self.sync_errors,**self.export_errors}.items()},'queue_jobs':self.manager.store.count()})
    def heartbeat_loop(self):
        # File transfer and model diagnostics must not look like a disconnected server.
        while not self.heartbeat_stop.is_set():
            try:self.heartbeat()
            except Exception as exc:print(f'[heartbeat] {type(exc).__name__}: {exc}',flush=True)
            self.heartbeat_stop.wait(5)
    def run(self):
        self.manager.start()
        heartbeat=threading.Thread(target=self.heartbeat_loop,name='sites-heartbeat',daemon=True)
        heartbeat.start()
        try:
            while not self.stop:
                try:self.tick()
                except Exception as exc:print(f'[agent] {type(exc).__name__}: {exc}',flush=True)
                for _ in range(10):
                    if self.stop:break
                    time.sleep(.2)
        finally:
            self.heartbeat_stop.set();heartbeat.join(timeout=2)
            if self.export_thread:self.export_thread.join(timeout=2)
            self.manager.close();self.inputs.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--credentials',type=Path,required=True)
    args=parser.parse_args()
    if args.credentials.stat().st_mode & 0o077:raise ValueError('서버 인증 파일 권한은 0600이어야 합니다.')
    agent=Agent(args.workspace,Client(json.loads(args.credentials.read_text())))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:setattr(agent,'stop',True))
    agent.run()
if __name__=='__main__':main()
