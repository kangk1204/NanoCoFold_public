# Update an existing installation · 기존 설치 업데이트

## English

Use this path to update the application while retaining the same workspace and results.

1. Wait until no job is queued or running.
2. Stop the web server with **Ctrl+C** in its terminal.
3. In the **original source folder**, update the source and application, then start it:

```bash
git pull --ff-only
bash update.sh
bash start.sh --port 18765
```

`git pull` applies to a Git clone. For a source ZIP, preserve local source changes, copy the updated source files into the original source folder, then run only the last two commands. Keep `.nanocofold/` (installation-location record and default workspace), inputs and results unchanged.

4. Open the URL printed after `NanoCoFold:` and reload the browser. On a remote server, keep your SSH tunnel open; see the [README](../README.md#2-open-the-browser).
5. Check the **installed executable**, which is what `start.sh` launches:

```bash
.nanocofold/workspace/.venv-dev/bin/nanocofold --version
```

For a custom workspace, use the `workspace` value from `.nanocofold/install-location.json` in place of `.nanocofold/workspace`. Running `python -m nanocofold --version` from the source folder checks that checkout and can give a different version.

`update.sh` reuses the saved workspace and GPU selection. It updates the app without launching predictions, running an environment-check job or reinstalling GPU engines. Running jobs or an open server block the update. If interrupted or failed, rerun `bash update.sh`; `start.sh` waits for a successful update before allowing startup. A new environment check is needed if the installation, GPU or chosen engine configuration changes.

A **separate fresh installation** is optional when you need a different environment or release. Extract it into a different folder and follow its installation instructions with a separate workspace. Keep the previous source, environment and results. Previous jobs do not automatically appear in the new workspace. Open the original workspace or a complete exported report ZIP to read previous results.

Existing structures and PAE are not automatically predicted again. Saved HTML and ZIPs preserve their original renderer; downloading an old ZIP again does not upgrade its language support. Use a current report viewer/export for current-language presentation. Record the release and [changelog](../CHANGELOG.md); do not assume score policies match across releases.

## 한국어

같은 작업 공간과 결과를 유지하면서 프로그램만 갱신하는 방법입니다.

1. 대기·실행 중인 작업이 모두 끝날 때까지 기다립니다.
2. 서버 터미널에서 **Ctrl+C**로 웹 서버를 닫습니다.
3. **처음 설치한 소스 폴더**에서 소스와 앱을 갱신한 뒤 다시 시작합니다.

```bash
git pull --ff-only
bash update.sh
bash start.sh --port 18765
```

`git pull`은 Git clone 설치에서만 사용합니다. 소스 ZIP은 로컬 소스 변경을 먼저 보존하고 최신 소스 파일을 원래 소스 폴더에 반영한 뒤 마지막 두 명령만 실행합니다. 설치 위치 기록과 기본 작업 공간이 들어 있는 `.nanocofold/`, 입력과 결과는 그대로 둡니다.

4. 터미널의 `NanoCoFold:` 다음 주소를 열고 브라우저를 새로고침합니다. 원격 서버는 SSH 연결도 유지합니다. [README의 원격 접속 안내](../README.ko.md#2-브라우저-열기)를 참고하세요.
5. `start.sh`가 사용하는 **설치본**을 확인합니다.

```bash
.nanocofold/workspace/.venv-dev/bin/nanocofold --version
```

별도 작업 공간이면 `.nanocofold/install-location.json`의 `workspace` 값으로 앞의 `.nanocofold/workspace`를 바꿉니다. 소스 폴더에서 `python -m nanocofold --version`을 실행하면 체크아웃 버전을 확인하므로 설치본과 다를 수 있습니다.

`update.sh`는 저장된 작업 공간·GPU 선택을 재사용합니다. 예측·환경 확인 작업이나 GPU 엔진 재설치는 시작하지 않습니다. 실행 작업이나 열린 웹 서버가 있으면 갱신을 차단합니다. 실패·중단 후에는 `bash update.sh`를 다시 실행하세요. 업데이트가 성공하기 전에는 `start.sh`가 시작을 차단합니다. 설치·GPU·선택 엔진 구성이 바뀌면 해당 구성의 환경 확인을 다시 합니다.

서로 다른 환경이나 릴리스가 필요할 때만 **새 폴더의 별도 설치**를 선택합니다. 해당 릴리스 안내에 따라 다른 작업 공간을 준비하고 이전 소스·환경·결과를 보존합니다. 새 작업 공간에는 이전 작업이 자동으로 나타나지 않습니다. 이전 결과는 원래 작업 공간이나 내보낸 전체 보고서 ZIP에서 읽으세요.

기존 구조·PAE가 자동으로 재예측되지는 않습니다. 저장된 HTML·ZIP은 당시 화면을 보존하므로 예전 ZIP을 다시 받는 것으로 언어 지원이 갱신되지 않습니다. 현재 언어가 필요하면 앱의 최신 보고서 보기·내보내기를 사용하세요. 릴리스와 [변경 기록](../CHANGELOG.md)을 기록하며, 버전별 점수 정책이 같다고 가정하지 않습니다.
