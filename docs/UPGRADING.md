# 새 버전으로 업데이트

1. 실행 중인 분석이 모두 끝날 때까지 기다립니다.
2. GitHub Releases에서 새 버전의 소스 ZIP과 SHA256SUMS를 받습니다.
3. 이전 폴더를 보존하고 새 ZIP을 **다른 폴더**에 압축 해제합니다.
4. 새 폴더에서 `bash install.sh`를 실행합니다.
5. 새 버전의 공개 예제로 확인한 뒤 자신의 입력을 사용합니다.

기본 설치 위치는 각 소스 폴더의 `.nanocofold/workspace`입니다.
다른 폴더에 설치하면 이전 결과를 덮어쓰지 않습니다. 기존 환경을 새 버전으로
제자리 갱신하거나 기존 모델을 수동 삭제할 필요가 없습니다.
다운로드 용량을 줄이기 위한 캐시 재사용은 고급 설정이며 기본 안내에서는 사용하지 않습니다.

결과와 실행 조건은 작업 공간에 남습니다. 보관할 작업의 HTML/ZIP도 별도로
내보내세요. 서로 다른 버전의 점수 정책은 같다고 가정하지 마세요.

현재 버전 확인:

```bash
.nanocofold/workspace/.venv-dev/bin/nanocofold --version
```

Git으로 관리한다면 `git describe --tags --always`로 소스 태그를 확인할 수 있습니다.
GitHub 저장소에서 Watch → Custom → Releases를 선택하면 새 배포를 알림받을 수 있습니다.
