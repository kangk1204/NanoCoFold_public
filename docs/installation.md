# 설치와 업데이트

## 컴퓨터 준비

- Ubuntu **x86_64**, **glibc 2.34 이상**인 NVIDIA 컴퓨터. 세부 Ubuntu 버전 번호의 고정 목록은 사용하지 않습니다.
- `nvidia-smi`가 정상 작동하고 NVIDIA 드라이버가 **580 이상**이어야 합니다. 현재 고정 패키지 중 CUDA 13을 사용하는 환경이 있어 필요한 조건입니다. 설치기는 드라이버를 설치하거나 바꾸지 않습니다.
- 인터넷과 `sudo` 권한. 설치 도중 시스템 패키지 설치를 위해 Ubuntu 계정 암호를 요청할 수 있습니다.
- 권장 여유 공간: **SSD 150 GiB 이상**, **RAM 32 GiB 이상**, **GPU 메모리 16 GiB 이상**. 표적·서열 길이·MSA 크기에 따라 더 필요합니다. 이 수치는 모든 입력이 성공한다는 보장이 아닙니다.

Python, pip, NumPy, pandas, matplotlib, PyTorch, JAX, HMMER는 미리 설치하지 않아도 됩니다. Conda, Docker, CUDA Toolkit, Node.js도 이 설치 경로의 선행 조건이 아닙니다. 공개 MSA API를 쓰는 예제에는 대형 로컬 MSA 데이터베이스가 필요하지 않습니다.

## 한 명령으로 설치

릴리스 소스 ZIP을 다운로드하고 압축을 푼 폴더에서 터미널을 엽니다. `README.md`와 `install.sh`가 보이는 위치에서 실행합니다.

```bash
bash install.sh
```

설치기는 시스템 기본 도구를 `apt`로 준비하고, 고정 `uv`와 여러 Python 버전을 자동 준비합니다. core, ANARCI/항체 도구, RFantibody, BoltzGen, Boltz-2, Chai-1, ColabFold, OpenDDE, ESM-2/AntiFold 환경·가중치를 `.nanocofold/workspace` 안에 분리해 설치합니다. matplotlib은 실제 그래프를 저장하는 엔진의 의존성에 포함되고, 화면 없는 환경에서 그림 저장도 검사합니다. 시스템 Python에 연구 패키지를 덮어 설치하지 않습니다.

각 패키지 파일과 모델은 고정 버전·SHA256으로 확인합니다. 모델 다운로드의 중간 파일을 보존하고 지원되는 경우 이어받습니다. 환경 import, 실제 CUDA 기본 연산, 필요한 도구·가중치 검사를 통과해야 설치 완료가 기록됩니다. **설치 완료는 단백질 분석 완료를 뜻하지 않습니다.** 다운로드 시간은 연결 속도와 캐시에 따라 크게 다릅니다.

GPU가 한 개이면 자동 선택합니다. 여러 개라면 설치기가 멈추고 선택 방법을 안내합니다.

```bash
nvidia-smi -L
bash install.sh --gpu GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

선택한 GPU에 대해 사용자별 공통 잠금을 사용합니다. 잠금 파일을 삭제하지 마세요. 설치가 끝나면 아래 명령으로 웹 화면을 엽니다.

```bash
bash start.sh
```

같은 컴퓨터의 브라우저에서 `http://127.0.0.1:8765`를 엽니다. 원격 서버라면 `bash start.sh --no-browser`를 사용하고 자신의 PC의 다른 터미널에서 다음처럼 연결합니다.

```bash
ssh -L 8765:127.0.0.1:8765 USER@SERVER
```

`USER@SERVER`는 자신의 계정과 서버 주소입니다. 설치 위치와 GPU는 `.nanocofold/install-location.json`에 기록되어 시작 명령이 다시 사용합니다.

## 공간·대상 선택

별도 디스크에 작업 공간을 둘 수 있습니다.

```bash
bash install.sh --workspace /your/data/NanoCoFold-workspace
```

CPU 화면만 준비하려면 `--profile core`를 사용합니다. core는 GPU 모델을 설치하지 않아 실제 예측을 할 수 없습니다. `--profile complex`, `design`, `screen`, `mature`는 해당 기본 환경만 설치하는 관리자 옵션입니다. **네 엔진을 모두 사용하는 예제를 실행하려면 기본 `all` 설치를 권합니다.**

관리자가 이미 기본 패키지를 준비했다면 `--skip-system-packages`로 apt 단계만 건너뛸 수 있습니다. 이 옵션은 Python·curl·빌드 도구를 자동 준비하지 않습니다.

## 진행 확인과 중단 복구

설치는 오래 걸릴 수 있으며 단계와 다운로드가 터미널에 표시됩니다. 중복으로 설치를 실행하지 마세요. 설치 시도는 작업 공간의 `.platform/install-attempts/`, 엔진 로그는 `.platform/install-*.log`, 최종 준비 상태는 `.platform/setup-all.json`과 `.platform/offline-readiness-all.json`에 남습니다.

오류가 발생하면 같은 폴더에서 **같은 옵션**으로 `bash install.sh`를 다시 실행하세요. 이미 검증된 환경·가중치는 재사용합니다. 손상되었거나 예상과 다른 기존 파일은 자동 삭제하지 않고 오류를 냅니다. 기존 가중치·환경을 임의 삭제해서 우회하지 마세요.

`nvidia-smi` 자체가 실패하거나 드라이버/라이브러리 불일치가 표시되면 관리자가 드라이버 상태를 먼저 해결해야 합니다. Python 설치를 반복해도 GPU 드라이버 문제는 해결되지 않습니다.

`bash install.sh --check-host`는 환경 요구 사항만 확인하며 설치·분석·서열 전송을 하지 않습니다. 아주 빈 Ubuntu에서 Python이나 빌드 도구가 없으면 일반 설치로 먼저 준비하거나 관리자가 시스템 도구를 설치해야 이 읽기 전용 점검이 끝납니다.

## 새 릴리스로 업데이트

진행 중인 분석을 모두 끝내고 웹 서버를 종료합니다. 새 릴리스 ZIP을 **새 폴더**에 압축 해제한 뒤 `bash install.sh`로 그 폴더의 새 작업 공간을 준비하는 방법을 권합니다. 이전 소스 폴더·작업 공간·분석 원본은 보관하세요. [업데이트 안내](UPGRADING.md)를 따릅니다.

기존 작업 공간의 제자리 갱신은 관리자 옵션입니다. 이 공개 설치기가 만든 `.platform/public-core-install.json` 소유 기록이 있고, 모든 작업과 웹 서버를 종료한 경우에만 `--workspace /기존/절대/경로`로 지정하세요. 대기/실행 중 작업이 확인되면 환경 갱신을 거부합니다. 기존 웹 서버의 실행 여부까지 자동 감지하지는 않으므로 먼저 직접 종료해야 합니다. 다른 설치기가 만든 환경은 자동으로 변경하지 않습니다.

릴리스 태그와 `CHANGELOG.md`를 함께 기록하세요. 이미 생성한 구조·PAE가 새 버전으로 자동 재예측되지는 않습니다. 점수 정책이 바뀌는 릴리스는 변경 기록의 재채점 안내를 따릅니다. 이전 릴리스로 돌릴 때에는 기존 환경을 강제로 덮지 말고 별도 작업 공간을 설치하세요.

## 설치 범위와 검증 범위

설치기는 Ubuntu·CPU 아키텍처·glibc·드라이버·필수 도구를 확인합니다. Ubuntu 버전 번호만으로 설치를 거부하지 않지만, 모든 GPU 기종·Ubuntu 조합의 새 설치와 추론을 인증한다는 뜻은 아닙니다. 릴리스의 검증 기록에서 CPU 설치 검사, 모의 설치 회귀 검사, 실제 GPU 추론 확인을 구분해 확인하세요.

외부 엔진 코드·모델·공개 MSA 서비스에는 각각의 별도 조건이 적용됩니다. [외부 도구 안내](../LICENSE_MANIFEST.md)를 확인하세요. 설치는 가중치·패키지를 내려받지만 입력 서열을 MSA 서버에 보내거나 분석 작업을 제출하지 않습니다.
