# Release checks

Run these checks from the repository root in the development environment before pushing a release. Use an isolated temporary directory with enough free space. Existing analyses and results must remain unchanged.

```bash
python scripts/build_platform_offline.py --check
CUDA_VISIBLE_DEVICES='' NANOCOFOLD_BROWSER_TEST=1 NANOCOFOLD_WORKFLOW_TEST=1 python -m pytest --junitxml=release-tests.xml
```

The browser flag is required. A run that skips the Chromium cases does not establish that the user interface passed. Install the development dependencies and Chromium before testing. Record passed, failed and skipped counts separately; keep the reason for every skipped case. The public GitHub workflow repeats the full checks on Ubuntu 22.04 and 24.04, builds a wheel and checks its installed UI.

The Linux process tests verify cancellation of owned children and release of their reservations while an unrelated control process survives. Run them on Linux or confirm their results in public CI. A macOS skip does not close that check. The approved private remote-CI exception does not exempt public CI.

After package changes, compare and reseal the shared core against the other source repository, refresh the Site runtime fingerprint, then check the Site tests, type checker and production build. Publish the exact pushed source. Verify the deployed language controls, report and downloads using preserved results.

## 한국어

릴리스 전에는 개발 환경에서 위 명령을 실행합니다. 기존 분석과 결과를 보존하고, 충분한 여유 공간이 있는 별도 임시 폴더를 사용하세요.

`NANOCOFOLD_BROWSER_TEST=1`을 켜야 실제 Chromium 화면 검사가 실행됩니다. 통과·실패·건너뜀 수를 구분하고 건너뛴 이유를 남기세요. 공개 CI의 Ubuntu 검사와 wheel 설치본 검사가 끝나야 최종 통과로 판정합니다. 비공개 원격 CI 승인 예외는 공개 CI에 적용되지 않습니다.

취소 후 하위 프로세스와 예약 자원 반환은 Linux 검사로 확인합니다. macOS에서 건너뛴 결과로 대체하지 않습니다. 공개·비공개 공유 코어와 사이트 런타임 지문을 갱신하고, 검사한 최종 소스를 push한 뒤 배포합니다. 운영에서는 보존된 결과로 한영 전환·보고서·다운로드를 확인하세요.
