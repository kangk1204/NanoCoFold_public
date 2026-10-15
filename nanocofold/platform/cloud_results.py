"""Portable, read-only result bundle with bounded PAE tiles and exact structure bytes."""
from __future__ import annotations
import copy
import json
import math
import hashlib
import re
import zipfile
from pathlib import Path
from .results import ResultReader
from .errors import public_error, public_error_data
from .paths import native, sha256
from ..utils.atomic import atomic_writer, check_output, write_bytes, write_json


def write(path,value):
    write_json(path,native(value))


def _presentation_zip(path):
    """Only recognized derived delivery packages may be omitted from full data."""
    if path.name != 'report_delivery.zip':
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            info=archive.getinfo('delivery_manifest.json')
            if info.file_size>5*1024*1024:return False
            manifest=json.loads(archive.read(info))
            return isinstance(manifest,dict) and manifest.get('kind')=='presentation_only'
    except (OSError,ValueError,KeyError,zipfile.BadZipFile):
        return False


def _presentation_data_identity(content):
    """Exact saved data and scientific provenance, excluding publication metadata.

    Unknown/legacy documents are kept. This permits omitting a generated view
    only when its complete rows, axes, coordinates and PAE match the separate
    delivery report; historical reports with different results stay archived.
    """
    match = re.search(rb'<script id="result-data" type="application/json">(.*?)</script>', content, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match[1])
        exact = {key: payload[key] for key in ("candidates", "observations", "structures")}
        exact["pae_arrays"] = payload.get("pae_arrays", {})
        summary = copy.deepcopy(payload["summary"])
        for key in ("report_artifact", "delivery_artifact", "source_report_status", "source_delivery_status"):
            summary.pop(key, None)
        state = summary.get("state", {})
        for key in ("execution", "previous_execution", "report_source_execution", "current_phase"):
            state.pop(key, None)
        final = state.get("phases", {}).get("final", {})
        for key in ("report", "report_status", "report_error", "delivery_status", "delivery_error", "delivery"):
            final.pop(key, None)
        exact["summary"] = summary
        exact["filters"] = payload.get("filters", {})
        provenance = payload["provenance"]
        exact["engine_provenance"] = provenance.get("engine_provenance")
        exact["ranking_policy"] = provenance.get("ranking_policy")
        # Compare source bytes as well as interpreted rows. State is compared
        # above after excluding report-only publication and clock churn.
        authoritative = {"run_config.yaml", "complex_results.json", "03_score/scored_candidates.csv",
                         "05_final/selected_candidates.csv", "03_score/ranking_policy.json", "retained/manifest.json",
                         "02_comparison/comparison_identity.json", "02_comparison/comparison_status.json", "preflight.json"}
        exact["sources"] = sorted((row for row in provenance.get("source_artifacts", [])
                                   if row.get("path") in authoritative), key=lambda row: row["path"])
        return hashlib.sha256(json.dumps(exact, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _delivery_identity(path):
    if not _presentation_zip(path):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo('report.html')
            # An unrecognized/oversized old archive is preserved, not unpacked
            # merely to optimize another export.
            if info.file_size > 256 * 1024 * 1024:
                return None
            return _presentation_data_identity(archive.read(info))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


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
            issue=public_error(exc, stage='prediction', candidate_id=candidate)
            index['issues'].append(issue)
            reader.issues.append(issue)
    write(destination/'index.json',public_error_data(index))
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
            issue=f'{fmt} 내보내기: {public_error(exc, stage="export")}'
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
            issue=f'html 최종 상태 갱신: {public_error(exc, stage="export")}'
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
    write(destination/'index.json',public_error_data(index))
    # Keep native/input/result files and exact linked entry/explorer bytes.
    # Derived delivery ZIPs and an equivalent offline HTML need not be nested
    # again: they remain separate downloads. Record every omission explicitly.
    from ..report import report_explorer_path
    explorer=report_explorer_path(run_root/'report.html')
    delivery=destination/'report_delivery.zip'
    canonical=_delivery_identity(delivery) if delivery.is_file() else None
    duplicate_explorers=set()
    if canonical is not None:
        for path in run_root.iterdir():
            if (path.is_file() and not path.is_symlink()
                    and (path.name=='report_explorer.html' or re.fullmatch(r'.+_explorer-[a-f0-9]{64}\.html',path.name))
                    and _presentation_data_identity(path.read_bytes())==canonical):
                duplicate_explorers.add(path)
    offline=destination/'report_offline.html'
    duplicate_html=(canonical is not None and offline.is_file()
                    and canonical==_presentation_data_identity(offline.read_bytes()))
    omitted=[]
    with atomic_writer(destination/'results.zip',root=destination) as output:
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for p in run_root.rglob('*'):
                if not p.is_file() or p.is_symlink() or p.name=='.run.lock':continue
                name='original/'+str(p.relative_to(run_root))
                if canonical is not None and p.name=='report_delivery.zip' and _delivery_identity(p)==canonical:
                    omitted.append({'path':name,'sha256':sha256(p),'size':p.stat().st_size,'reason':'derived_presentation_package','separate_download':'report_delivery.zip'})
                elif p in duplicate_explorers or p==run_root/'report.html' and explorer in duplicate_explorers:
                    omitted.append({'path':name,'sha256':sha256(p),'size':p.stat().st_size,
                                    'reason':'equivalent_derived_presentation','separate_download':'report_delivery.zip',
                                    'scientific_payload_sha256':canonical})
                else:archive.write(p,name)
            for p in destination.rglob('*'):
                if p.is_file() and p.name!='results.zip' and not p.name.startswith('.publish-'):
                    name='exploration/'+str(p.relative_to(destination))
                    if p.name=='report_delivery.zip' or p==offline and duplicate_html:
                        omitted.append({'path':name,'sha256':sha256(p),'size':p.stat().st_size,'reason':'separate_presentation_download',
                                        'separate_download':'report_delivery.zip',
                                        'scientific_payload_sha256':canonical if p==offline else None})
                    else:archive.write(p,name)
            archive.writestr('report_packaging_manifest.json',json.dumps({'schema_version':1,'native_and_scientific_files_preserved':True,
                'source_readonly':True,'derived_reports_omitted':omitted},ensure_ascii=False,indent=2))
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
    return {'name':summary['name'],'mode':'complex','mock':False,'total':summary['total'],'status':'complete','readonly':True,'exports':['comparison.csv','KEAP1_NRF2_ETGE_report.html','KEAP1_NRF2_ETGE_package.zip']}
