#!/usr/bin/env python3
"""Run one hash-checked public example using the ordinary batch CLI.

A saved batch is reused on repeated invocations. It is never silently replaced.
Only --new-run creates another batch, and only after the existing one is terminal.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples' / 'tutorial'
TERMINAL = {'complete', 'failed', 'cancelled', 'interrupted', 'partial_failure', 'budget_exhausted'}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_payload(mode):
    manifest = json.loads((EXAMPLES / 'manifest.json').read_text())
    for name, expected in manifest['files_sha256'].items():
        path = EXAMPLES / name
        if path.parent != EXAMPLES or sha256(path) != expected:
            raise ValueError(f'공개 예제 파일이 변경되었습니다: {name}. 원본 release를 다시 받으세요.')
    payload = json.loads((EXAMPLES / manifest['modes'][mode]['file']).read_text())
    if payload['mode'] != mode or payload.get('api_consent') is not False:
        raise ValueError('예제 모드 또는 API 동의 기본값이 예상과 다릅니다.')
    return payload


def atomic_json(path, value):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def cli(workspace, args, *, output=None):
    command = [sys.executable, '-m', 'nanocofold', 'batch', *args, '--workspace', str(workspace)]
    result = subprocess.run(command, cwd=workspace, text=True, stdout=subprocess.PIPE)
    if output:
        output.write_text(result.stdout)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f'배치 명령이 유효한 상태를 반환하지 않았습니다 (exit {result.returncode}). 로그와 저장된 배치를 확인하세요.')
    return result.returncode, value


def show(batch):
    print(f"\n배치 ID: {batch['id']}")
    print(f"모드: {batch['mode']}")
    for row in batch['rows']:
        job = row.get('job') or {}
        print(f"  {row['id']}: {job.get('state', 'reviewed')}")
        if row.get('error') or row.get('submission_error'):
            print('  오류:', row.get('error') or row.get('submission_error'))
        if job.get('run_dir'):
            folder = Path(job['run_dir'])
            print(f'  결과 폴더: {folder}')
            for name in ('report.html', 'report_delivery.zip'):
                matches = sorted(folder.rglob(name))
                # Archived snapshots are never the current report.
                matches = [p for p in matches if not any(part in {'.history', '.compact', 'source_snapshot'} for part in p.relative_to(folder).parts)]
                for path in matches[:2]:
                    print(f'  {name}: {path}')
    print('예측 완료와 후보 선별 통과는 별도입니다. 결과의 실패·미평가·제외 사유를 함께 확인하세요.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='공개 예제 하나를 검토하고 실행합니다.')
    parser.add_argument('mode', choices=['complex', 'screen', 'mature', 'design'])
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--review-only', action='store_true', help='입력 검토만 수행; GPU 추론 없음')
    parser.add_argument('--allow-public-msa', action='store_true', help='이 공개 예제 표적·VHH 및 생성/변이 후보의 ColabFold API 전송에 동의')
    parser.add_argument('--new-run', action='store_true', help='기존 배치 종료를 확인한 뒤 별도 새 배치 생성; 이전 결과 보존')
    args = parser.parse_args(argv)
    try:
        payload = checked_payload(args.mode)
        if args.mode != 'complex' and not args.allow_public_msa:
            parser.error('이 예제는 공개 MSA API를 사용합니다. 안내를 확인한 뒤 --allow-public-msa를 붙이세요. 입력 검토에서도 이 배치 동의가 필요합니다.')
        payload['api_consent'] = bool(args.allow_public_msa)
        workspace = args.workspace.expanduser().resolve()
        if not (workspace / 'config' / 'environment.yaml').is_file():
            if not args.review_only:
                raise ValueError('예측 실행에 필요한 environment.yaml이 없습니다. 먼저 bash install.sh로 GPU 엔진 설치를 완료하세요. core 설치는 --review-only 입력 검토만 가능합니다.')
            core_receipt = workspace / '.platform' / 'setup-core.json'
            try:
                core = json.loads(core_receipt.read_text())
            except (OSError, ValueError) as exc:
                raise ValueError('완료된 core 설치 영수증이 없습니다. 먼저 bash install.sh를 완료하세요.') from exc
            if (not (workspace / 'config' / 'default.yaml').is_file()
                    or not isinstance(core, dict)
                    or core.get('profile') != 'core'
                    or core.get('workspace') != str(workspace)
                    or core.get('status') != 'complete'
                    or core.get('offline_ready') is not True):
                raise ValueError('core 설치의 기본 설정·완료·준비 상태를 확인하지 못했습니다. 입력을 제출하지 않았습니다.')
        folder = workspace / '.nanocofold' / 'tutorial' / args.mode
        folder.mkdir(parents=True, exist_ok=True)
        pointer = folder / 'current.json'
        with (folder / 'runner.lock').open('a+') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('이 모드의 예제 실행 명령이 이미 열려 있습니다. 중복 실행하지 않았습니다.')
            current = json.loads(pointer.read_text()) if pointer.exists() else None
            if current:
                code, batch = cli(workspace, ['status', '--id', current['batch_id']])
                if args.new_run:
                    jobs = [row.get('job') for row in batch['rows']]
                    if not all(job and job['state'] in TERMINAL for job in jobs):
                        raise ValueError('기존 배치가 종료되지 않았거나 검토 단계입니다. 새 배치를 중복 생성하지 않았습니다.')
                    current = None
                elif args.review_only or all(row.get('job') and row['job']['state'] in TERMINAL for row in batch['rows']):
                    show(batch)
                    return code
            if current is None:
                attempt = folder / (time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
                attempt.mkdir()
                request = attempt / 'request.json'
                atomic_json(request, payload)
                code, batch = cli(workspace, ['review', '--manifest', str(request)], output=attempt / 'review.json')
                if code or any(row.get('error') for row in batch['rows']):
                    show(batch)
                    return code or 1
                current = {'batch_id': batch['id'], 'review_sha256': batch['review_sha256'], 'attempt': str(attempt), 'request_sha256': sha256(request)}
                atomic_json(pointer, current)
                if args.review_only:
                    show(batch)
                    print('검토만 완료했습니다. 같은 명령에서 --review-only를 빼면 이 배치를 실행합니다.')
                    return 0
            attempt = Path(current['attempt'])
            if sha256(attempt / 'request.json') != current['request_sha256']:
                raise ValueError('저장된 검토 입력이 변경되었습니다. 자동으로 재제출하지 않았습니다.')
            print(f"{args.mode} 예제를 실행합니다. 배치 {current['batch_id']}의 상태를 계속 표시합니다.", flush=True)
            code, batch = cli(workspace, ['submit', '--id', current['batch_id'], '--review-sha256', current['review_sha256'], '--wait'], output=attempt / ('status-' + uuid.uuid4().hex + '.json'))
            show(batch)
            return code
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'[오류] {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
