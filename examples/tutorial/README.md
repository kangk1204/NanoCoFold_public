# 공개 예제 입력과 기존 실행 조건

**저장소와 전달 ZIP의 차이:** `examples/current_native`, `examples/outputs`, `START_HERE.html`, `validation/status.json`은 별도 공동연구자 전달 ZIP 안의 경로입니다. GitHub clone/Download ZIP에는 공개 예제 입력만 있으며 해당 GPU 결과 폴더는 포함되지 않습니다. 소스만 받은 경우 브라우저에서 공개 예제를 불러오고 별도로 분석합니다.

이 폴더에는 공개 서열과 **보존된 JSON 실행 조건**이 있습니다. 미리 계산한 구조·점수는 이 폴더에 없습니다. 처음 웹 화면을 쓰면 [README의 한 가지 시작 순서](../../README.ko.md#3-첫-공개-예제-따라-하기)를 따르세요. **단백질 복합체 예측 · 입력하기 → 이 목적의 공개 예제 불러오기**로 시작합니다.

현재 화면의 작은 시작 설정은 네 엔진 비교를 사용합니다. 이 폴더의 JSON과 `run_example.sh`는 과거 공개 입력의 조건과 해시를 보존하므로 **complex는 Boltz-2 한 엔진**입니다. 아래 명령은 그 보존된 조건을 명시적으로 실행하는 참고 경로입니다. 현재 웹 화면의 네 엔진 설정은 README를 따르세요.

| 모드 | 보존된 입력 | 이 JSON의 최대 대표 모델 | 전달된 과거 output의 실제 모델 |
|---|---|---:|---:|
| complex | barnase 110 aa + barstar 89 aa | 1 | 1 |
| screen | IL-6 183 aa + anti-IL-6 VHH 132 aa | 4 | 4 |
| mature | 같은 IL-6 + WT VHH; DMS → ESM-2 최대 2개 + WT | 12 | 8 (WT + N100R) |
| design | IL-6; RFantibody 1개 + BoltzGen 1개 | 8 | 8 |

screen·mature·design은 ColabFold·Boltz-2·Chai-1·OpenDDE를 사용합니다. Seed는 42입니다. 실제 모델 수는 생성·선별·예측 결과에 따라 달라집니다.

함께 받은 묶음의 `START_HERE.html`에서 과거 네 output을 읽을 수 있습니다. 원본 계산은 **v0.4.1 / 2026-10-10 / Ubuntu 25.10 / RTX 5070 Ti 16 GB**에서 진행했습니다. mature는 첫 MSA 실패를 보존하고 검증된 표적·ESM 단계 복구를 거쳐 계산한 결과입니다. 새 GPU 재실행이나 재채점 없이 보존한 자료이며 최신 코드의 네 모드 검증 자료가 아닙니다. 각 output의 `test_input.json`과 이 폴더의 해당 `<모드>_batch.json`은 바이트가 같습니다. 새로 계산하면 MSA 서비스·조건·엔진 환경에 따라 결과가 달라질 수 있습니다.

## 1. 일반 복합체

```bash
bash run_example.sh complex
```

두 단백질 서열을 읽고 Boltz-2 예측과 점수 계산을 수행한 뒤 결과 경로를 표시합니다. 나노바디 없이 단백질–단백질 복합체를 분석할 때 사용하는 모드입니다. 이 작은 예제는 single-sequence 설정으로 실행하며 공용 MSA API를 사용하지 않습니다.

## 2. 기존 VHH 평가

```bash
bash run_example.sh screen --allow-public-msa
```

`target.fasta`의 표적과 `vhh_library.fasta`의 VHH를 읽습니다. 이 예제는 anti-IL-6 VHH 한 서열을 네 엔진에서 각각 평가합니다. 자신의 라이브러리를 분석할 때는 전체 VHH 서열들을 FASTA에 넣어 `screen` 입력으로 제출하세요.

`--allow-public-msa`는 **이 예제의 표적·VHH 및 이후 예제에서 새로 생성하는 변이/설계 VHH 서열을 `https://api.colabfold.com`에 보내는 것에 동의**하는 옵션입니다. MSA는 유사 서열 정보를 얻는 단계입니다. 자신의 비공개 서열로 파일을 바꾸고 이 옵션을 재사용하지 마세요. 예제 실행기는 SHA-256이 다른 입력을 거부합니다. 자신의 입력은 웹 화면 또는 별도 배치 JSON으로 검토해 실행하세요.

## 3. VHH 성숙

```bash
bash run_example.sh mature --allow-public-msa
```

기준 VHH인 WT를 읽고 ANARCI로 CDR 구간을 정합니다. CDR의 단일 치환 라이브러리를 만들고 ESM-2로 평가한 뒤, 기준을 통과한 상위 2개 변이와 WT를 네 엔진으로 평가합니다. 이 예제에는 실험 구조를 입력하지 않으므로 AntiFold는 꺼져 있습니다.

결과에서 같은 엔진의 WT와 변이 점수를 비교하세요. ESM-2는 서열의 모델상 적합도를 평가합니다. 선정한 변이의 결합과 KD 변화는 실험으로 확인합니다.

## 4. 새로운 VHH 설계

```bash
bash run_example.sh design --allow-public-msa
```

표적 구조를 준비하고 RFantibody와 BoltzGen이 각각 새 VHH 후보 1개를 생성합니다. 생성된 후보를 네 엔진으로 평가합니다. hotspot은 지정하지 않습니다. 생성기 사전 필터는 작은 예제 후보를 모두 살펴보도록 꺼져 있지만 최종 점수·품질 선별 기준은 유지합니다. 생성·선별한 후보는 후속 결합 실험에 사용합니다.

## 결과 열기와 전달

실행 중 단계와 경과 시간을 터미널에 표시합니다. 종료하면 배치 ID와 결과 폴더가 표시됩니다. 결과의 `report.html`을 브라우저로 열어 표의 후보를 선택하면 구조와 PAE를 볼 수 있습니다. 다른 사람에게는 같은 폴더의 `report_delivery.zip`을 전달하세요. 받은 사람은 ZIP 전체를 풀고 `report.html`을 열면 됩니다.

보고서에는 입력, 후보 서열, 엔진별 지표, 구조, PAE, 실패·미평가 사유가 포함됩니다. 예측 상태와 선별 통과 여부를 확인한 뒤, 발주 준비 검사에 필요한 실험 정보를 작성하세요. 낮은 점수와 실행 실패는 별도로 표시하며 미평가 값은 결측으로 남습니다.

## 다시 실행하거나 중간 상태 확인

같은 명령을 다시 실행하면 저장된 배치를 확인합니다. 이미 완료한 분석을 자동으로 다시 계산하지 않습니다. 종료된 분석을 별도로 한 번 더 실행하려면 `--new-run`을 붙이세요. 이전 입력·구조·보고서는 보존됩니다.

입력 검토만 하려면 `--review-only`를 추가합니다. 이 단계에서는 GPU 예측을 실행하지 않습니다. 실제 설치 환경이 있어야 하며, 공용 MSA 서버를 쓰는 모드에서는 배치 입력 검토에도 명시적인 전송 동의 옵션이 필요합니다.

```bash
bash run_example.sh screen --allow-public-msa --review-only
# 검토한 같은 배치를 실행
bash run_example.sh screen --allow-public-msa
# 종료된 배치를 보존하고 별도 새 분석 생성
bash run_example.sh screen --allow-public-msa --new-run
```

터미널을 유지하세요. 장시간 원격 실행에는 `tmux`를 사용할 수 있습니다. 연결이 끊겼다면 같은 명령으로 기존 배치 상태부터 확인하세요. 자동으로 새 배치를 만들지 않습니다.

## 입력 출처와 정확한 구성물

- **barnase–barstar:** [RCSB PDB 1BRS](https://www.rcsb.org/structure/1BRS)의 entity 1/2 서열을 그대로 사용합니다. barstar는 논문에 명시된 C40A/C82A 구성물입니다. [원 논문](https://doi.org/10.1021/bi00196a004)
- **IL-6:** [UniProt P05231](https://www.uniprot.org/uniprotkb/P05231/entry)의 212 aa 전구체에서 공식 주석의 성숙 사슬 **30–212번(183 aa)**을 사용합니다. 신호 펩타이드 1–29번만 제외하며 아미노산을 치환하지 않습니다. 이 서열 입력은 당화 등 번역 후 변형을 지정하지 않습니다.
- **anti-IL-6 VHH:** [RCSB PDB 9VQ6](https://www.rcsb.org/structure/9VQ6)의 entity 1 서열 **132 aa 전체 구성물**을 그대로 사용합니다. 기탁된 말단과 C 말단 His 태그를 포함합니다. 9VQ6는 IL-6 특이적 나노바디 단독 구조이며 IL-6와 결합한 복합체 구조가 아닙니다. [원 논문](https://doi.org/10.1016/j.cej.2025.170017)

`sources.json`에는 원본 UniProt/RCSB API 주소·공개 서열·변환 내용·입력 서열 SHA-256이 있고, `manifest.json`에는 파일 SHA-256과 모드별 계획 수가 있습니다. RCSB PDB 공개 데이터 사용 정책은 [RCSB 정책 안내](https://www.rcsb.org/pages/policies), UniProt 데이터의 CC BY 4.0 조건은 [UniProt 이용 조건](https://www.uniprot.org/help/license)을 참고하세요.
