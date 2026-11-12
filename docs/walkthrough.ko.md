# NanoCoFold 실습 안내

처음 설치할 때는 [전체 실습 영상 (5분 21초)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=walkthrough&lang=ko)을 보세요. [29초 소개 영상](https://kangk1204.github.io/NanoCoFold_public/watch/?v=intro&lang=ko)으로 실제 구조와 결과 검색 화면을 먼저 살펴볼 수도 있습니다.

| 영상 위치 | 내용 |
|---|---|
| 00:00 | 시작 |
| 00:09 | 설치 |
| 01:05 | 복합체 예측 |
| 02:10 | 복합체 결과 |
| 02:43 | 새 나노바디 설계 |
| 03:23 | 보유 나노바디 비교 |
| 03:55 | 기존 나노바디 변이 평가 |
| 04:37 | 결과 DB와 공유 |

## 1. Ubuntu에 설치

이 실습은 `nvidia-smi`가 동작하는 Ubuntu x86_64의 GPU 한 개를 기준으로 합니다. 네 엔진 예제의 공개 안내 기준은 NVIDIA 드라이버 580 이상, VRAM 16 GB 이상, RAM 32 GB 이상, SSD 여유 150 GiB 이상, 인터넷과 sudo 권한입니다. GPU가 여러 개이거나 처음 실행할 컴퓨터의 사양이 다르면 [한글 설치 안내](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/README.ko.md#1-준비와-설치)를 참고하세요.

Git이 설치된 터미널에서 소스를 내려받습니다.

```bash
git clone --branch v0.4.8 https://github.com/kangk1204/NanoCoFold_public.git
cd NanoCoFold_public
git describe --tags --exact-match
```

마지막 명령에서 `v0.4.8`이 표시되면 영상에 맞는 정식 버전입니다. [Git 없이 ZIP으로 설치하기](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/README.ko.md#1-준비와-설치)

**목적에 맞는 설치 명령 한 개를 선택하세요.**

| 목적 | 명령 |
|---|---|
| 영상의 첫 복합체 예제만 Boltz-2로 실습 | `bash install.sh --profile complex` |
| 신규 설계·보유 후보·변이 평가와 네 엔진까지 실습 | `bash install.sh --profile all` |

터미널에 **[설치 완료]**가 나오면 같은 폴더에서 웹 서버를 시작합니다.

```bash
bash start.sh --port 18765
```

**NanoCoFold:** 뒤에 표시된 주소를 Ubuntu 컴퓨터의 브라우저에서 엽니다. 사용 중에는 터미널을 열어 두세요. 이미 사용 중인 포트라면 `bash start.sh --port 0`으로 출력된 새 주소를 엽니다. 다른 컴퓨터에서 원격 서버에 접속하거나 기존 설치본을 업데이트할 때는 해당 절차를 따르세요. [원격 접속 안내](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/README.ko.md#2-브라우저-열기) · [기존 설치 업데이트](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/docs/UPDATE.ko.md)

## 2. 데이터 준비와 첫 실행

처음에는 **Language → 한국어**를 선택합니다. **환경·설치 → 처음 한 번 환경 확인**에서 사용할 목적과 엔진을 확인하세요. 필수 검사가 통과하면 **분석 준비하기**를 누릅니다.

첫 실습은 **단백질 복합체 예측 → 이 목적의 공개 예제 불러오기**입니다. Barnase 110 aa와 Barstar 89 aa, 사본 각 1개를 확인합니다. **입력 확인 후 다음 → Boltz-2만 선택 → MSA 없음 → 검토·실행**에서 계획 1모델을 확인하고 **분석 실행**을 한 번 누릅니다. **작업·로그**에서 현재 단계와 성공·실패 수를 봅니다. 환경 확인만 완료된 경우에는 아직 예측을 시작하기 전입니다.

자신의 데이터는 FASTA의 각 항목을 `>이름` 줄과 전체 아미노산 서열로 준비합니다. 후보 라이브러리에서는 이름을 서로 다르게 지정하세요. 공용 MSA를 선택하면 서열을 외부 서비스에 보냅니다. 공개 예제 외의 데이터는 공유 가능 여부를 먼저 확인하세요. [실제 FASTA 예제](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/examples/tutorial/vhh_library.fasta) · [모드별 입력 예제](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/examples/tutorial/README.md)

| 분석 모드 | 먼저 준비할 데이터 | 보관된 GPU 결과에서 본 예 |
|---|---|---|
| 단백질 복합체 예측 | 체인별 서열과 사본 수 | Barnase–Barstar, Boltz-2 1모델 완료. 전체 ipTM 0.078, 평균 계면 PAE 24.84 Å로 이 모델의 상대 배치는 불확실합니다. |
| 새 나노바디 설계 | 표적 서열 또는 구조 | 공개 IL-6 예제에서 두 생성기로 후보 2종을 얻어 네 엔진으로 8모델 평가. 최종 선별 0종. |
| 보유 나노바디 비교 | 표적과 후보별 전체 VHH 서열 | anti-IL-6 VHH 1종을 네 엔진에서 평가. Boltz-2 ipSAE 0.378. 최종 선별 0종. |
| 기존 나노바디 변이 평가 | 표적과 원본 VHH(WT) | WT와 N100R을 네 엔진에서 평가해 8모델 완료. Boltz-2 ipSAE는 WT 0.378, N100R 0.013. 최종 선별 0종. |

ipTM과 ipSAE는 예측한 계면을 검토할 때 사용합니다. PAE는 한 잔기를 기준으로 다른 잔기의 위치에 대해 모델이 예상한 오차(Å)입니다. 변이와 WT는 같은 엔진·조건끼리 비교합니다. **계산 완료**는 예측 산출물이 생겼다는 뜻이고, **최종 선별**은 그중 설정한 기준을 통과한 후보 수입니다. 결합과 친화도 변화는 실험에서 확인하세요.

## 3. 결과 DB 검색과 공유

1. **작업·로그 → 완료된 분석 → 결과 열기**에서 입력, 성공 모델 수, 구조와 PAE를 확인합니다.
2. 웹에서 완료한 분석은 **결과 DB**에 등록됩니다. 기존 실행을 추가할 때는 원래 실행 폴더를 작업 공간 안에 두고 **결과 탐색 → 기존 결과 열기**에 그 경로를 입력합니다. **전달용 보고서 ZIP**은 공동연구자가 오프라인에서 읽는 파일입니다.
3. **결과 DB**에서 분석 이름이나 ID 일부를 입력하고 **검색**을 누릅니다. 영상의 예는 `public-complex`입니다. 결과 행을 누르면 새 탭에서 다시 열립니다.
4. **결과 탐색 → 전달용 보고서 ZIP → 내보내기**로 내려받습니다. 받은 사람은 **ZIP 전체를 압축 해제**하고 `report.html`을 브라우저에서 엽니다.

## 촬영 기준

2026년 11월 12일 공개 코드 `93b94de`로 촬영했습니다. 정식 릴리스 `v0.4.8`에는 촬영에 사용한 계산 패키지 `0.4.8rc12`와 실습 자료가 들어 있습니다. [촬영 커밋](https://github.com/kangk1204/NanoCoFold_public/tree/93b94de4f14e9a577f9d848cb2830a438b838b5e) · [정식 릴리스](https://github.com/kangk1204/NanoCoFold_public/releases/tag/v0.4.8)

실제 Ubuntu 터미널과 NanoCoFold 브라우저를 녹화했습니다. 긴 모델 다운로드는 편집했고, 전체 설치 완료는 보관된 설치 기록으로 보여줍니다. **분석 실행 버튼과 진행 표시**는 보유 나노바디 비교의 CPU 연습 모드에서 촬영했습니다. 복합체 예측은 GPU를 사용합니다. **복합체의 실제 GPU 완료·결과**는 RTX 3080 Ti Laptop에서 56.7초에 완료된 공개 실행을 다시 열었습니다. **설계·보유 후보·변이 결과**는 보관된 실제 GPU 예측(rc5)을 촬영일의 공개 보고서 코드로 열어 녹화했습니다. 이 숫자는 당시 예제의 관측값이며 다른 입력의 실행 시간이나 결과를 보장하지 않습니다.
