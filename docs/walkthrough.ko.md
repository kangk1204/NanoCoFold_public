# NanoCoFold 실습 안내

처음 설치한다면 아래 변이 실습을 따라 하세요. 한 작업을 설치부터 보고서 공유까지 진행합니다. 영상은 영어 음성이며 재생기에서 한영 자막을 선택할 수 있습니다.

## 첫 실습 · EGFR 나노바디 변이 10종

[실습 영상](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=ko) · [실습 입력 ZIP](https://github.com/kangk1204/NanoCoFold_public/releases/download/v0.4.8/NanoCoFold-EGFR-10-inputs.zip) · [영상의 결과 보고서 ZIP](https://github.com/kangk1204/NanoCoFold_public/releases/download/v0.4.8/NanoCoFold-EGFR-10-report.zip)

[입력 파일 준비 (11:31)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=ko&chapter=inputs) · [결합 부위 번호 읽기 (13:09)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=ko&chapter=hotspots) · [변이 설정 (16:50)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=ko&chapter=variants) · [결과 해석 (20:38)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=ko&chapter=results) · [DB 검색 (25:07)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=ko&chapter=database) · [ZIP 열기 (26:08)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=ko&chapter=zip)

### 입력 파일 네 개 알아보기

ZIP을 전부 푼 뒤 아래 네 파일을 사용합니다. FASTA는 단백질 서열을 적은 텍스트 파일입니다. 텍스트 편집기로 열면 첫 줄의 `>` 뒤에 이름이 있고, 다음 줄에는 아미노산이 한 글자씩 적혀 있습니다.

| 파일 | 어디에 쓰나요? |
|---|---|
| `4KRL.cif` | EGFR과 7D12가 만난 모습을 담은 실험 구조입니다. **구조 파일**에서 선택합니다. |
| `EGFR-target.fasta` | 결합 상대인 **표적 EGFR**입니다. **전체 표적 서열** 칸에 `>B`부터 끝까지 복사합니다. |
| `7D12-WT.fasta` | 바꾸기 전 **원래 나노바디(WT)**입니다. **WT VHH 서열** 칸에 복사합니다. |
| `hotspots.csv` | **표적에서 나노바디와 가까운 자리**를 정리한 표입니다. 스프레드시트로 열고 첫 열의 `19, 40, 42`를 읽습니다. 이 파일을 업로드할 필요는 없습니다. |

**번호 읽는 법.** 표적 서열의 첫 글자가 1번입니다. 예를 들어 19번째 글자 L은 원본 구조에서는 저자 체인 A의 325번입니다. 이 실습의 **결합 부위 위치**에는 `19, 40, 42`를 넣고 **추천 제외 구간**은 비워 두세요. 이 세 자리는 고정합니다. 바꾸는 두 자리는 나노바디 끝쪽의 고리인 **CDR3**에서 고릅니다.

**내 표적에 적용할 때.** 구조가 있다면 먼저 해당 구조와 입력 서열의 번호가 어떻게 대응하는지 확인하세요. 서열만 있다면 표적 FASTA를 입력하고 예측 구조에서 결합 부위를 자동 추천받을 수 있습니다.

### 실습 순서

1. NVIDIA GPU가 표시되는 Ubuntu 컴퓨터에 공개 버전을 내려받습니다. 소스 폴더에서 `bash install.sh --profile screen`를 실행합니다. 이 프로필은 이 실습에 필요한 Boltz-2와 ANARCI를 설치합니다. 웹에서 선택할 분석 모드는 **변이 평가**입니다. 촬영한 컴퓨터는 시스템 도구가 설치되어 있어 `--skip-system-packages`를 붙였습니다. 새 컴퓨터에서는 위 명령을 그대로 실행하고, sudo 비밀번호를 요청하면 입력하세요. 설치가 완료되면 `bash start.sh`를 실행하고 **터미널에 표시된 주소**를 여세요.
2. 웹 서버 터미널은 열어 두세요. 위의 **실습 입력 ZIP**을 내려받아 파일 관리자에서 **다운로드**를 엽니다. ZIP을 오른쪽 클릭해 **여기에 압축 풀기**를 선택하세요. 영상처럼 명령을 입력하려면 다운로드 폴더의 빈 곳을 오른쪽 클릭해 **터미널에서 열기**를 선택합니다. NanoCoFold 브라우저로 돌아와 **변이 평가**를 선택합니다. 작업 이름에 `EGFR 10 variants walkthrough`를 입력합니다. 구조 입력에서 `4KRL.cif`를 열고 **Epidermal growth factor receptor**(체인 B, 저자 체인 A)를 표적으로 선택합니다. 파트너는 비워 두고 **체인·노출도 검토**를 누릅니다.
3. **전체 서열로 구조 예측**을 선택하고 `EGFR-target.fasta`를 제목 `>B`부터 모두 붙여 넣습니다. 선택한 결합 부위를 전체 서열 위치로 대응하도록 설정한 뒤 **결합 부위 위치**에 `19, 40, 42`를 입력합니다. WT VHH 입력란에는 `7D12-WT.fasta`를 넣습니다. 이 세 번호는 표적 EGFR 서열 기준입니다. 뒤에서 바꾸는 CDR3 잔기는 나노바디 서열 기준입니다.
4. 실행 조건에서 **기존 기본 설정 · 사전 필터 적용**을 선택합니다. 변이 생성은 **CDR 무작위 다중 잔기 치환**, 범위는 **CDR3만**으로 설정합니다. 후보 수에 **10**, 최소와 최대 치환 수에 각각 **2**를 입력하고 WT를 포함합니다. 고급 설정에서 복합체 예측 엔진은 **Boltz-2**를 선택합니다.
5. 입력 검토에서 **표적 214잔기, Seed 42, Boltz-2 한 개, 변이 10종 + WT 1종 = 11모델**을 확인합니다. 이 입력의 **환경 확인으로 이동**을 눌러 검사한 뒤 분석을 시작합니다. ‘작업·로그’에서 같은 이름의 상태와 완료 수가 바뀌는지 확인하세요.
6. 작업이 완료되면 **결과 열기**를 누릅니다. 모델 수와 선별 수를 먼저 확인하고 WT와 변이를 하나씩 선택하세요. 같은 엔진에서 ipTM·ipSAE, 계면 pLDDT, PAE(Å), 지정 결합 부위 세 곳의 접촉을 함께 봅니다. 실험적 결합과 친화도는 별도 실험에서 확인합니다.
7. **결과 DB**에서 `EGFR 10 variants walkthrough`를 검색하고 첫 열의 분석 이름을 누릅니다. 보고서의 **02 후보·모델 수**에서 전달 점검 상태와 오류·경고를 확인하세요. 보고서 ZIP을 내려받아 **전체 압축 해제**하고, 폴더 안의 `report.html`을 엽니다.

표적과 WT는 PDB 4KRL에 기탁된 EGFR 세포외 도메인 III와 나노바디 7D12입니다. 표적 서열 19, 40, 42번은 저자 체인 A의 325, 346, 348번이며, 모두 결정 구조에서 7D12와 5 Å 이내에 있습니다. [RCSB PDB 4KRL](https://www.rcsb.org/structure/4KRL)

**실습 결과와 맞춰 보기.** RTX 3060에서 17분 48초 동안 WT 1종과 변이 10종의 구조를 모두 계산했습니다. 각 변이에는 CDR3 치환이 정확히 두 개 있습니다. 예를 들어 `RND_000001`은 나노바디 98번의 A를 L로, 110번의 E를 L로 바꾼 서열입니다. 이 변이는 모델에서 표적 세 자리 모두와 접촉하지만 계면 신뢰도(ipSAE)는 0.015입니다(WT 0.055). 변이 10종 모두 ipSAE와 ipTM 선별 기준에 미달해 최종 선별은 0종입니다. 이 실습에서는 두 단백질에 유사 서열 정렬(MSA)을 사용하지 않았습니다. **전달 점검**의 주문 준비 상태가 차단되어 있어도 결과 ZIP을 내려받아 구조와 점수를 살펴볼 수 있습니다.

[플랫폼 소개 (28초)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=intro&lang=ko) · [Ubuntu 설치 개요 (2분 30초)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=install&lang=ko) · [네 모드 소개 (5분 47초)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=walkthrough&lang=ko)

### 설치 영상 목차

- 0:00　정식 릴리스 열기
- 0:10　GPU 확인
- 0:21　소스 내려받기
- 0:31　릴리스 폴더 열기
- 0:45　설치 프로필 선택
- 0:54　첫 복합체용 설치
- 1:05　네 모드 전체 설치
- 1:15　기본 앱 설치 실행
- 1:26　완료 메시지 확인
- 1:39　GPU 설치 완료 기록
- 1:52　웹 앱 시작
- 2:05　출력된 주소 열기
- 2:17　설치한 엔진 확인

### 네 모드 소개 목차

- 0:00　시작
- 0:09　Ubuntu 설치
- 1:18　복합체 예측
- 2:20　복합체 결과 해석
- 2:57　새 나노바디 설계
- 3:42　보유 나노바디 비교
- 4:13　변이 평가
- 5:00　결과 DB 검색과 공유

## 1. Ubuntu에 설치

이 실습은 `nvidia-smi`가 동작하는 Ubuntu x86_64의 GPU 한 개를 기준으로 합니다. 네 엔진 예제의 공개 안내 기준은 NVIDIA 드라이버 580 이상, VRAM 16 GB 이상, RAM 32 GB 이상, SSD 여유 150 GiB 이상, 인터넷과 sudo 권한입니다. GPU가 여러 개이거나 처음 실행할 컴퓨터의 사양이 다르면 [한글 설치 안내](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8-install.9/README.ko.md#1-준비와-설치)를 참고하세요.

Git이 설치된 터미널에서 소스를 내려받습니다.

```bash
git clone --branch v0.4.8-install.9 https://github.com/kangk1204/NanoCoFold_public.git
cd NanoCoFold_public
git describe --tags --exact-match
```

마지막 명령에서 `v0.4.8-install.9`가 표시되면 연구실 공유 기능이 포함된 수정판입니다. 영상은 원래 `v0.4.8`로 촬영했습니다. [Git 없이 ZIP으로 설치하기](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8-install.9/README.ko.md#1-준비와-설치)

**목적에 맞는 설치 명령 한 개를 선택하세요.**

| 목적 | 명령 |
|---|---|
| Boltz-2로 EGFR CDR3 변이 평가 | `bash install.sh --profile screen` |
| 영상의 첫 복합체 예제만 Boltz-2로 실습 | `bash install.sh --profile complex` |
| 신규 설계·보유 후보·변이 평가와 네 엔진까지 실습 | `bash install.sh --profile all` |

터미널에 **[설치 완료]**가 나오면 같은 폴더에서 웹 서버를 시작합니다.

```bash
bash start.sh --port 18765
```

**NanoCoFold:** 뒤에 표시된 주소를 Ubuntu 컴퓨터의 브라우저에서 엽니다. 사용 중에는 터미널을 열어 두세요. 이미 사용 중인 포트라면 `bash start.sh --port 0`으로 출력된 새 주소를 엽니다. 다른 컴퓨터에서 원격 서버에 접속하거나 기존 설치본을 업데이트할 때는 해당 절차를 따르세요. [원격 접속 안내](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8-install.9/README.ko.md#2-브라우저-열기) · [기존 설치 업데이트](https://github.com/kangk1204/NanoCoFold_public/blob/main/docs/UPDATE.ko.md)

## 2. 네 분석 모드 소개 영상의 예제 따라 하기

1. **Language → 한국어**를 선택합니다. Barnase–Barstar 예제는 **입력·실행 → 단백질 복합체 예측 → 이 목적의 공개 예제 불러오기**에서 시작합니다. Barnase 110잔기와 Barstar 89잔기, 사본 각 1개를 확인합니다.
2. 실행 조건에서 **기존 기본 설정 · 사전 필터 적용**을 선택하고 **고급 설정**을 펼쳐 **복합체 예측 엔진 → Boltz-2**를 고릅니다. 입력 검토에서 Seed 42, MSA empty, 계획 1모델을 확인합니다.
3. 그 입력의 **환경 확인으로 이동 → 환경 확인 시작**을 누릅니다. 검사 후 **분석 준비하기**로 돌아와 입력을 확인하고 **분석 실행**을 한 번 누릅니다. `complex` 설치 프로필에는 Boltz-2와 Chai-1이 들어 있습니다. 네 엔진 환경 검사는 ColabFold와 OpenDDE도 설치한 경우에 사용할 수 있습니다.
4. **작업·로그**에서 본인이 입력한 분석 이름을 고릅니다. 진행 단계와 완료·실패 수를 확인하고 **완료 → 결과 열기**를 누릅니다. `diagnostics`는 환경 검사의 작업 이름입니다.

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
3. **결과 DB**에서 분석 이름이나 ID 일부를 입력하고 **검색**을 누릅니다. 직접 입력한 분석 이름으로 검색합니다. 기존 소개 영상의 `public-complex`는 촬영할 때 등록해 둔 이름입니다. 첫 열의 **분석 이름**을 누르면 새 탭으로 열립니다. 같은 조건으로 기존 결과를 재사용한 경우 **작업·로그 → 결과 열기**에서도 찾을 수 있습니다.
4. **결과 탐색 → 전달용 보고서 ZIP → 내보내기**로 내려받습니다. 받은 사람은 **ZIP 전체를 압축 해제**하고 `report.html`을 브라우저에서 엽니다.

## 촬영 기준

Ubuntu 설치 영상에서는 RTX 5070 Ti 컴퓨터의 별도 폴더에서 정식 태그 `v0.4.8`을 내려받았습니다. 기본 앱 설치(core), 완료 메시지와 첫 브라우저 접속은 실제 녹화입니다. GPU 프로필의 긴 다운로드는 생략했고, 전체 설치 완료는 보관된 실제 설치 기록으로 보여줍니다. 예측을 실행할 때는 위 표에서 목적에 맞는 GPU 프로필을 선택하세요.

네 모드 소개 영상에서는 공개 예제를 입력하고 결과를 다시 열었습니다. 분석 실행 버튼과 진행 표시는 보유 나노바디 비교의 CPU 연습 모드에서 촬영했습니다. 복합체 예측은 RTX 3080 Ti Laptop에서 56.7초에 완료한 실제 GPU 실행입니다. 설계·보유 후보·변이 결과는 보관된 GPU 예측(`0.4.8rc5`)을 영상에 사용한 공개 보고서에서 다시 열었습니다. [공개 릴리스](https://github.com/kangk1204/NanoCoFold_public/releases/tag/v0.4.8)
