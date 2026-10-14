"""Portable, read-only result bundle with bounded PAE tiles and exact structure bytes."""
from __future__ import annotations
import copy
import json
import math
import zipfile
from pathlib import Path
from .results import ResultReader
from .paths import native, sha256
from ..utils.atomic import atomic_writer, check_output, write_bytes, write_json


def write(path,value):
    write_json(path,native(value))


def build_bundle(run_root, destination, snapshot_root=None):
    run_root=Path(run_root).resolve();destination=Path(destination).resolve()
    # Writing inside the source would mutate originals and let the ZIP include
    # itself while it is being written. Resolve aliases/symlinks before checking.
    if destination.is_relative_to(run_root):
        raise ValueError('결과 사본은 원본 작업 디렉터리와 별도 위치에 생성해야 합니다.')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('결과 사본은 빈 새 디렉터리에서 생성해야 합니다.')
    reader=ResultReader(run_root);destination.mkdir(parents=True,exist_ok=True)
    rows=reader.all_rows();summary=reader.summary();observations=reader.observations()['rows']
    index={'schema_version':2,'summary':summary,'rows':rows,'observations':observations,'structures':[],'issues':list(reader.issues)}
    unique={}
    for row in rows:
        if row.get('structure_available'):unique[(row['candidate_id'],None)]=row
    for obs in observations:
        if summary.get('mode')!='complex' and (obs.get('evaluated') or obs.get('cif_path')):unique[(obs['candidate_id'],obs.get('observation_id'))]=obs
    # Retain all candidates; structures are loaded only on selection in the Site.
    for i,(candidate,observation) in enumerate(unique):
        try:
            data=reader.structure(candidate,observation)
            entry=store_structure(data,destination,f'structures/{i}')
            index['structures'].append({'candidate_id':candidate,'observation_id':observation,**entry})
        except (OSError,ValueError,KeyError) as exc:
            issue=f'{candidate}: {exc}'
            index['issues'].append(issue)
            reader.issues.append(issue)
    write(destination/'index.json',index)
    if snapshot_root is not None:
        manifest_path=snapshot_root/'manifest.json'
        manifest=json.loads(manifest_path.read_text())
        for name in ['manifest.json','config.yaml',*[entry['snapshot'] for entry in manifest['inputs']]]:
            from .paths import contained
            source=contained(snapshot_root,name,exists=True)
            target=destination/'inputs'/name;target.parent.mkdir(parents=True,exist_ok=True)
            if name not in ('manifest.json','config.yaml'):
                expected=next(e['sha256'] for e in manifest['inputs'] if e['snapshot']==name)
                if sha256(source)!=expected:raise ValueError('실행 입력 사본의 해시가 바뀌었습니다.')
            elif name=='config.yaml' and sha256(source)!=manifest['config_sha256']:
                raise ValueError('실행 설정 사본의 해시가 바뀌었습니다.')
            write_bytes(target,source.read_bytes(),root=destination)
    html_issues=None
    for fmt in ('csv','xlsx','fasta','html','report_zip'):
        try:
            data,_,name=reader.export(fmt)
            write_bytes(destination/name,data,root=destination)
            if fmt=='html':html_issues=set(reader.issues)
        except (OSError,ValueError,KeyError) as exc:
            issue=f'{fmt} 내보내기: {exc}'
            index['issues'].append(issue)
            reader.issues.append(issue)
    if html_issues is not None and set(reader.issues)!=html_issues:
        # A later delivery export can fail (e.g. missing local MolStar assets).
        # Do not leave an earlier HTML claiming complete while the index is partial.
        try:
            data,_,name=reader.export('html')
            write_bytes(destination/name,data,root=destination)
        except (OSError,ValueError,KeyError) as exc:
            (destination/'report_offline.html').unlink(missing_ok=True)
            issue=f'html 최종 상태 갱신: {exc}'
            index['issues'].append(issue);reader.issues.append(issue)
    # Structure/PAE readers and exporters can discover errors after the initial
    # summary. Preserve their artifacts, but never publish a stale complete flag.
    summary=reader.summary()
    index['issues']=list(dict.fromkeys([*index['issues'],*reader.issues]))
    if index['issues'] and summary.get('status')=='complete':
        summary={**summary,'status':'partial_failure','complete':False}
    if index['issues']:
        summary={**summary,'issues':list(dict.fromkeys([*summary.get('issues',[]),*index['issues']]))}
    index['summary']=summary
    write(destination/'index.json',index)
    # Full original run, plus cloud index/exports, with no input/environment mutation.
    with atomic_writer(destination/'results.zip',root=destination) as output:
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for p in run_root.rglob('*'):
                if p.is_file() and not p.is_symlink() and p.name!='.run.lock':archive.write(p,'original/'+str(p.relative_to(run_root)))
            for p in destination.rglob('*'):
                if p.is_file() and p.name!='results.zip' and not p.name.startswith('.publish-'):
                    archive.write(p,'exploration/'+str(p.relative_to(destination)))
    manifest=[{'path':str(p.relative_to(destination)),'size':p.stat().st_size,'sha256':sha256(p)} for p in destination.rglob('*') if p.is_file()]
    write(destination/'manifest.json',{'schema_version':2,'source_readonly':True,'files':manifest})
    return {k:summary.get(k) for k in ('name','mode','mock','total','status','failed_candidates')} | {'exports':[p.name for p in destination.iterdir() if p.is_file()],'issues':index['issues']}


def store_structure(data,destination,prefix):
    destination=Path(destination)
    if not isinstance(prefix,str) or not prefix or Path(prefix).is_absolute() or '..' in Path(prefix).parts:
        raise ValueError('구조 출력 경로는 선택한 결과 디렉터리 내부의 상대경로여야 합니다.')
    check_output(destination/prefix,destination)
    data=copy.deepcopy(data);pae=data.get('pae');tiles=[]
    if pae:
        values=pae.pop('values');n=len(values)
        if not n or any(len(row)!=n for row in values):
            raise ValueError('PAE는 비어 있지 않은 정사각 행렬이어야 합니다.')
        tile_size=max(200,math.ceil(n/4))
        for y in range(0,n,tile_size):
            for x in range(0,n,tile_size):
                name=f'{prefix}/pae_{y}_{x}.json'
                write(destination/name,{'x':x,'y':y,'values':[r[x:x+tile_size] for r in values[y:y+tile_size]]})
                tiles.append({'x':x,'y':y,'file':name,'sha256':sha256(destination/name)})
        pae.update(size=n,tiles=tiles,tile_size=tile_size,tiling_schema_version=1)
    name=prefix+'/structure.json';write(destination/name,data)
    structure_name=prefix+'/model.'+('pdb' if data['format']=='pdb' else 'cif')
    write_bytes(destination/structure_name,data['content'].encode('utf-8'),root=destination)
    return {'file':name,'model_file':structure_name}


def import_nrf2(source,destination):
    """Import completed campaign without inference or modification of originals."""
    data=json.loads((source/'analysis.json').read_text()); destination.mkdir(parents=True,exist_ok=True)
    structures=[];rows=[]
    for obs in data['observations']:
        identifier='seed_'+str(obs['seed']);row={'candidate_id':identifier,**obs['metrics'],'evaluated':True,'structure_available':True}
        structure=obs['structure'];structure['candidate_id']=identifier
        structures.append({'candidate_id':identifier,'observation_id':None,**store_structure(structure,destination,'structures/'+identifier)})
        rows.append(row)
    summary={'name':'KEAP1–NRF2 ETGE · 기존 결과','mode':'complex','mock':False,'total':len(rows),'status':'complete','readonly':True,'conditions':{'engine':'boltz2','seeds':[42,123,2026],'msa':'empty','restraints':False,'source':'prior_cli_prepare','training_overlap_possible':True},'config':data['campaign']}
    write(destination/'index.json',{'schema_version':2,'summary':summary,'rows':rows,'observations':[],'structures':structures,'issues':[]})
    for name in ('comparison.csv','KEAP1_NRF2_ETGE_report.html','KEAP1_NRF2_ETGE_package.zip'):
        write_bytes(destination/name,(source/name).read_bytes(),root=destination)
    return {'name':summary['name'],'mode':'complex','mock':False,'total':3,'status':'complete','readonly':True,'exports':['comparison.csv','KEAP1_NRF2_ETGE_report.html','KEAP1_NRF2_ETGE_package.zip']}
