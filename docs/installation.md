# 설치와 업데이트

[English](installation.en.md) · **한국어**

전달받은 공개 소스의 커밋·태그와 그 안의 README를 함께 사용하세요. GitHub의 **Latest release**는 현재 `main`보다 이전 버전일 수 있습니다. 버전 문자열만으로 소스 리비전을 식별할 수 없으므로 정확한 커밋 또는 ZIP 다운로드 주소를 설치 기록에 남깁니다.

## 컴퓨터 준비

- Ubuntu **x86_64**, **glibc 2.34 이상**인 NVIDIA 컴퓨터.
- `nvidia-smi`가 정상 작동하고 NVIDIA 드라이버가 **580 이상**이어야 합니다. 현재 고정 패키지 중 CUDA 13을 사용하는 환경이 있어 필요한 조건입니다. 설치기는 드라이버를 설치하거나 바꾸지 않습니다.
- 인터넷과 `sudo` 권한. 설치 도중 시스템 패키지 설치를 위해 Ubuntu 계정 암호를 요청할 수 있습니다.
- 권장 여유 공간: **SSD 150 GiB 이상**, **RAM 32 GiB 이상**, **GPU 메모리 16 GiB 이상**. 긴 서열·큰 MSA·대규모 후보 목록에는 더 많은 자원이 필요합니다.

Python, pip, NumPy, pandas, matplotlib, PyTorch, JAX, HMMER는 미리 설치하지 않아도 됩니다. Conda, Docker, CUDA Toolkit, Node.js도 이 설치 경로의 선행 조건이 아닙니다. 공용 MSA API를 쓰는 예제에는 대형 로컬 MSA 데이터베이스가 필요하지 않습니다.

## 첫 복합체 예제 설치

릴리스 소스 ZIP을 다운로드하고 압축을 푼 폴더에서 터미널을 엽니다. `README.md`와 `install.sh`가 보이는 위치에서 실행합니다.

README의 첫 복합체 예제는 Boltz-2·Chai-1·ColabFold·OpenDDE를 사용합니다. **같은 폴더에서 세 명령을 순서대로** 실행하고, 각각 **`[설치 완료]`**로 끝난 뒤 다음 명령을 실행하세요.

```bash
bash install.sh --profile complex
bash install.sh --profile colabfold
bash install.sh --profile opendde
```

설치기는 시스템 기본 도구를 `apt`로 준비하고, 고정 `uv`와 필요한 Python 버전을 자동 준비합니다. core와 선택한 엔진의 환경·가중치를 `.nanocofold/workspace` 안에 분리해 설치합니다. `complex`는 Boltz-2·Chai-1, 나머지 두 명령은 각각 ColabFold·OpenDDE를 준비합니다. matplotlib은 실제 그래프를 저장하는 엔진의 의존성에 포함되고, 화면 없는 환경에서 그림 저장도 검사합니다. 시스템 Python에 연구 패키지를 덮어 설치하지 않습니다.

각 패키지 파일과 모델은 고정 버전·SHA256으로 확인합니다. 모델 다운로드의 중간 파일을 보존하고 지원되는 경우 이어받습니다. 환경 import, 실제 CUDA 기본 연산, 필요한 도구·가중치 검사를 통과해야 설치 완료가 기록됩니다. 설치 후에는 웹 화면에서 환경 확인과 첫 분석을 진행하세요. 다운로드 시간은 연결 속도와 캐시에 따라 크게 다릅니다.

GPU가 한 개이면 자동 선택합니다. 여러 개라면 설치기가 멈추고 선택 방법을 안내합니다.

```bash
nvidia-smi -L
bash install.sh --profile complex --gpu GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
bash install.sh --profile colabfold --gpu GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
bash install.sh --profile opendde --gpu GPU-xxxxxxxx-xxxx-xxxx-xxxxxxxxxxxx
```

선택한 GPU에 대해 사용자별 공통 잠금을 사용합니다. 잠금 파일을 삭제하지 마세요. 설치가 끝나면 아래 명령으로 웹 화면을 엽니다.

```bash
bash start.sh --port 18765
```

같은 컴퓨터의 브라우저에서 터미널의 `NanoCoFold:` 다음에 표시된 주소(이 명령에서는 `http://127.0.0.1:18765`)를 엽니다. 원격 서버라면 `bash start.sh --no-browser --port 18765`를 사용하고 자신의 PC의 다른 터미널에서 다음처럼 연결합니다.

```bash
ssh -o ExitOnForwardFailure=yes -L 18765:127.0.0.1:18765 USER@SERVER
```

`USER@SERVER`는 자신의 계정과 서버 주소입니다. 다른 앱이 열리면 [README의 포트 충돌·원격 연결 안내](../README.ko.md#2-브라우저-열기)에 따라 별도 포트를 사용하세요. 설치 위치와 GPU는 `.nanocofold/install-location.json`에 기록되어 시작 명령이 다시 사용합니다.

## 모든 기능의 환경 설치

새 나노바디 설계·보유 후보·변이 평가까지 모든 기능을 사용할 때는 아래 명령으로 여덟 엔진·보조 환경을 준비합니다. 옵션 없는 `bash install.sh`도 같은 `all` 설치입니다. 첫 복합체 예제에 필요한 환경만 설치하려면 위의 세 명령을 사용하세요.

```bash
bash install.sh --profile all
```

`all`은 antibody(ANARCI/항체 도구), Boltz-2, Chai-1, RFantibody, BoltzGen, ESM-2/AntiFold, ColabFold, OpenDDE 환경과 가중치를 준비합니다. ESM-2와 AntiFold는 같은 환경을 사용합니다. 여러 GPU가 있다면 이 명령에도 사용할 `--gpu` UUID를 지정합니다.

설치·기본 GPU 연산 확인과 단백질의 실제 예측은 별도 범위입니다. 최초 전체 설치의 실제 조건과 릴리스별 완료 상태는 [검증 기록](VALIDATION.md)을 확인하세요.

## 공간·대상 선택

별도 디스크에 작업 공간을 둘 수 있습니다.

```bash
bash install.sh --profile complex --workspace /your/data/NanoCoFold-workspace
```

여러 프로필을 같은 별도 작업 공간에 설치한다면 모든 명령에 같은 `--workspace`와 GPU 선택을 사용합니다. 설치 위치가 달라지면 이전 명령의 환경을 공유하지 않습니다.

CPU 화면만 준비하려면 `--profile core`를 사용합니다. core는 GPU 모델을 설치하지 않아 실제 예측을 할 수 없습니다. 목적별 프로필은 다음 기본 환경만 준비하므로 화면에서 선택한 모든 엔진이 준비됐는지 검토·환경 점검에서 확인하세요.

| 프로필 | 준비하는 엔진·보조 환경 |
|---|---|
| `complex` | Boltz-2, Chai-1 |
| `colabfold` | ColabFold |
| `opendde` | OpenDDE |
| `screen` | antibody, Boltz-2, Chai-1 |
| `design` | antibody, Boltz-2, Chai-1, RFantibody, BoltzGen |
| `mature` | antibody, Boltz-2, Chai-1, ESM-2/AntiFold |
| `boltzgen` | antibody, BoltzGen |
| `all` | 위의 모든 환경 |

관리자가 이미 기본 패키지를 준비했다면 `--skip-system-packages`로 apt 단계만 건너뛸 수 있습니다. 이 옵션은 Python·curl·빌드 도구를 자동 준비하지 않습니다.

## 진행 확인과 중단 복구

설치는 오래 걸릴 수 있으며 단계와 다운로드가 터미널에 표시됩니다. 중복으로 설치를 실행하지 마세요. 설치 시도는 작업 공간의 `.platform/install-attempts/`, 엔진 로그는 `.platform/install-*.log`에 남습니다. 최종 준비 상태는 선택한 프로필의 `.platform/setup-<profile>.json`과 `.platform/offline-readiness-<profile>.json`에 남으며, `all`이면 파일 이름에 `all`이 들어갑니다.

오류가 발생하면 같은 폴더에서 **같은 옵션**으로 `bash install.sh`를 다시 실행하세요. 이미 검증된 환경·가중치는 재사용합니다. 손상되었거나 예상과 다른 파일은 그대로 보존하고 오류를 표시합니다. 로그를 확인한 뒤 관리자에게 문의하세요.

`nvidia-smi` 자체가 실패하거나 드라이버/라이브러리 불일치가 표시되면 관리자가 드라이버 상태를 먼저 해결해야 합니다. Python 설치를 반복해도 GPU 드라이버 문제는 해결되지 않습니다.

`Failed to initialize NVML: Driver/library version mismatch`는 실행 중인 NVIDIA 커널 모듈과 설치된 라이브러리의 버전이 다를 때 발생할 수 있습니다. 드라이버 업데이트 후 재부팅이 남아 있는지 확인하고, 해당 컴퓨터의 작업을 마친 뒤 관리자와 재부팅을 진행하세요. 재부팅 후 `nvidia-smi`가 GPU 표를 정상 출력하는지 확인한 다음 `bash install.sh`를 다시 실행합니다. 설치기는 재부팅하거나 드라이버를 바꾸지 않습니다.

v0.4.2 이하에서는 `nvidia-smi`가 표준 출력에 쓴 오류가 가려져 설치 명령이 아무 메시지 없이 끝날 수 있었습니다. v0.4.3은 원본 오류와 종료 코드를 표시합니다. 이 상태에서는 설치가 시작되지 않았으므로 `.nanocofold/install-location.json`이 없고 `start.sh`도 실행할 수 없습니다. 설치 위치 파일을 임의로 만들거나 GPU 점검을 우회하지 마세요.

`bash install.sh --check-host`는 환경 요구 사항만 확인하며 설치·분석·서열 전송을 하지 않습니다. 아주 빈 Ubuntu에서 Python이나 빌드 도구가 없으면 일반 설치로 먼저 준비하거나 관리자가 시스템 도구를 설치해야 이 읽기 전용 점검이 끝납니다.

## 새 릴리스로 업데이트

**같은 설치본의 앱만 갱신할 때**는 모든 대기·실행 작업이 끝난 뒤 서버 터미널에서 Ctrl+C로 웹 서버를 종료합니다. 원래 설치한 소스 폴더에서 다음 명령을 실행합니다.

```bash
git pull --ff-only
bash update.sh
bash start.sh --port 18765
```

`git pull`은 Git clone 설치에만 사용합니다. ZIP 설치는 기존 소스 변경을 먼저 보존하고 최신 소스 파일을 원래 소스 폴더에 반영한 뒤 마지막 두 명령을 실행합니다. 설치 위치 기록과 작업 공간이 있는 `.nanocofold/`를 덮어쓰거나 삭제하지 마세요. 파일을 옮길 때 기존 입력·결과도 그대로 둡니다.

`update.sh`는 저장된 작업 공간과 GPU 선택을 재사용하고 프로그램만 갱신합니다. 예측·환경 확인 작업이나 GPU 엔진 재설치를 시작하지 않습니다. 실행 중인 웹 서버·작업이 있으면 갱신을 거부합니다. 실패·중단 후에는 같은 `bash update.sh`로 다시 시도하며, 완료 전 `start.sh`는 시작을 차단합니다. 프로그램 재시작 뒤 브라우저를 새로고침합니다.

설치본 버전은 기본 설치에서 `.nanocofold/workspace/.venv-dev/bin/nanocofold --version`으로 확인합니다. 별도 작업 공간이면 `.nanocofold/install-location.json`의 `workspace` 경로를 사용하세요. 소스 폴더의 `python -m nanocofold --version`은 체크아웃 버전이므로 설치본 확인으로 사용하지 않습니다.

**Update the existing application:** finish queued/running jobs, stop its web server, then run the three commands above in the original source folder. ZIP users replace only source files after preserving local changes, keep `.nanocofold/` and all inputs/results, and run the last two commands. `update.sh` reuses the saved workspace/GPU and does not launch predictions or reinstall engines. Retry it after an interruption; startup remains blocked until it completes. Check the installed console executable, rather than `python -m` from the source folder. See [bilingual update instructions](UPGRADING.md).

새 폴더에 새 작업 공간을 설치하는 방법은 **별도 설치가 필요할 때의 선택 사항**입니다. 이전 폴더·작업 공간·원본 결과를 보존하며, 새 작업 공간에서는 이전 작업 목록이 자동으로 보이지 않습니다. 이전 결과만 읽으려면 원본 작업 공간이나 내보낸 전체 보고서 ZIP을 사용하세요. 엔진·모델까지 설치를 바꾸려면 해당 릴리스의 프로필 설치 안내를 별도로 따릅니다.

릴리스 태그와 `CHANGELOG.md`를 함께 기록하세요. 기존 구조·PAE는 자동으로 재예측되지 않습니다. 점수 정책이 바뀌는 릴리스는 변경 기록의 재채점 안내를 따릅니다. 이전 릴리스로 돌아갈 때는 기존 환경을 강제로 덮지 말고 별도 작업 공간을 사용합니다.

## 설치 범위와 검증 범위

설치기는 Ubuntu·CPU 아키텍처·glibc·드라이버·필수 도구를 확인합니다. 릴리스별 [검증 기록](VALIDATION.md)에 CPU 설치 검사·설치 회귀검사·실제 GPU 예측을 수행한 환경이 정리돼 있습니다.

외부 엔진 코드·모델·공용 MSA 서비스에는 각각의 별도 조건이 적용됩니다. [외부 도구 안내](../LICENSE_MANIFEST.md)를 확인하세요. 설치는 가중치·패키지를 내려받지만 입력 서열을 MSA 서버에 보내거나 분석 작업을 제출하지 않습니다.
