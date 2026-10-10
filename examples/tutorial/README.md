# 네 가지 모드 따라 하기

이 폴더에는 **입력 서열과 실행 설정**이 있습니다. 미리 계산한 구조나 점수는 포함하지 않습니다. 처음에는 `complex`를 실행해 결과를 열고, 다음 모드를 하나씩 진행하세요. 저장소의 `install.sh`를 완료한 상태에서 저장소 폴더에서 실행합니다.

**웹에서도 실행할 수 있습니다.** `bash start.sh`로 연 브라우저에서 **배치 분석 → JSON**을 고르고
이 폴더의 `<모드>_batch.json`을 업로드하세요. 입력 검토 후 실행하며,
design·screen·mature는 외부 MSA 전송 동의가 필요합니다.
[실제 화면을 보며 따라 하기](../../README.md#브라우저에서-실행)

| 모드 | 하는 일 | 이 예제의 입력 | 최대 대표 복합체 모델 |
|---|---|---|---:|
| `complex` | 주어진 단백질들이 만드는 복합체 예측 | barnase 110 aa + barstar 89 aa | 1 |
| `screen` | 이미 가진 VHH를 표적과 함께 평가 | IL-6 183 aa + anti-IL-6 VHH 132 aa | 4 |
| `mature` | 기준 VHH의 CDR 변이를 만들고 후보를 평가 | 같은 IL-6 + anti-IL-6 VHH; DMS → ESM-2 상위 2개 + WT | 12 |
| `design` | 표적에서 새로운 VHH 후보를 생성하고 평가 | IL-6; RFantibody 1개 + BoltzGen 1개 | 8 |

`screen`, `mature`, `design`은 ColabFold, Boltz-2, Chai-1, OpenDDE로 평가합니다. `complex`는 처음 따라 하기 쉽도록 Boltz-2 한 엔진을 사용합니다. 네 모드는 총 4개 작업이며, 모두 성공하고 후보가 계획대로 생성·선택될 때 최대 25개 대표 모델입니다. ESM 기준을 통과하는 변이가 적거나 생성·예측이 실패하면 실제 모델 수가 줄어듭니다. seed는 42입니다.

## 1. 일반 복합체

```bash
bash run_example.sh complex
```

두 단백질 서열을 읽고 Boltz-2 예측과 점수 계산을 수행한 뒤 결과 경로를 표시합니다. 나노바디 없이 단백질–단백질 복합체를 분석할 때 사용하는 모드입니다. 이 작은 예제는 single-sequence 설정으로 실행하며 공개 MSA API를 사용하지 않습니다.

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

결과에서 변이와 같은 엔진의 WT 점수를 비교하세요. ESM-2 점수가 좋아졌다는 사실만으로 결합이 개선됐다고 판단할 수 없습니다. 실제 결합이나 KD 변화는 실험으로 확인해야 합니다.

## 4. 새로운 VHH 설계

```bash
bash run_example.sh design --allow-public-msa
```

표적 구조를 준비하고 RFantibody와 BoltzGen이 각각 새 VHH 후보 1개를 생성합니다. 생성된 후보를 네 엔진으로 평가합니다. hotspot은 지정하지 않습니다. 생성기 사전 필터는 작은 예제 후보를 모두 살펴보도록 꺼져 있지만 최종 점수·품질 선별 기준은 유지합니다. 새로운 VHH가 생성되는 것과 실제 결합자가 확보되는 것은 다릅니다.

## 결과 열기와 전달

실행 중 단계와 경과 시간을 터미널에 표시합니다. 종료하면 배치 ID와 결과 폴더가 표시됩니다. 결과의 `report.html`을 브라우저로 열어 표의 후보를 선택하면 구조와 PAE를 볼 수 있습니다. 다른 사람에게는 같은 폴더의 `report_delivery.zip`을 전달하세요. 받은 사람은 ZIP 전체를 풀고 `report.html`을 열면 됩니다.

보고서에는 입력, 후보 서열, 엔진별 지표, 구조, PAE, 실패·미평가 사유가 포함됩니다. **예측 완료**, **선별 통과**, **실험 발주 준비**는 서로 다른 상태입니다. 실험 관련 메타데이터가 없는 예제는 발주 준비 검사에서 보류될 수 있습니다. 점수가 낮아도 실행 실패가 아니며, 미평가는 0점과 다릅니다.

## 다시 실행하거나 중간 상태 확인

같은 명령을 다시 실행하면 저장된 배치를 확인합니다. 이미 완료한 분석을 자동으로 다시 계산하지 않습니다. 종료된 분석을 별도로 한 번 더 실행하려면 `--new-run`을 붙이세요. 이전 입력·구조·보고서는 보존됩니다.

입력 검토만 하려면 `--review-only`를 추가합니다. 이 단계에서는 GPU 예측을 실행하지 않습니다. 실제 설치 환경이 있어야 하며, 공개 MSA를 쓰는 모드에서는 배치 입력 검토에도 명시적인 전송 동의 옵션이 필요합니다.

```bash
bash run_example.sh screen --allow-public-msa --review-only
# 검토한 같은 배치를 실행
bash run_example.sh screen --allow-public-msa
# 종료된 배치를 보존하고 별도 새 분석 생성
bash run_example.sh screen --allow-public-msa --new-run
```

터미널을 유지하세요. 장시간 원격 실행에는 `tmux`를 사용할 수 있습니다. 연결이 끊겼다면 같은 명령으로 기존 배치 상태부터 확인하세요. 자동으로 새 배치를 만들지 않습니다.

## 입력 출처와 정확한 구성물

- **barnase–barstar:** [RCSB PDB 1BRS](https://www.rcsb.org/structure/1BRS)의 entity 1/2 서열을 그대로 사용합니다. barstar는 논문에 명시된 C40A/C82A 구성물이며 야생형 전체 단백질이라고 부르지 않습니다. [원 논문](https://doi.org/10.1021/bi00196a004)
- **IL-6:** [UniProt P05231](https://www.uniprot.org/uniprotkb/P05231/entry)의 212 aa 전구체에서 공식 주석의 성숙 사슬 **30–212번(183 aa)**을 사용합니다. 신호 펩타이드 1–29번만 제외하며 아미노산을 치환하지 않습니다. 이 서열 입력은 당화 등 번역 후 변형을 지정하지 않습니다.
- **anti-IL-6 VHH:** [RCSB PDB 9VQ6](https://www.rcsb.org/structure/9VQ6)의 entity 1 서열 **132 aa 전체 구성물**을 그대로 사용합니다. 기탁된 말단과 C 말단 His 태그를 포함합니다. 9VQ6는 IL-6 특이적 나노바디 단독 구조이며 IL-6와 결합한 복합체 구조가 아닙니다. [원 논문](https://doi.org/10.1016/j.cej.2025.170017)

`sources.json`에는 원본 UniProt/RCSB API 주소·공개 서열·변환 내용·입력 서열 SHA-256이 있고, `manifest.json`에는 파일 SHA-256과 모드별 계획 수가 있습니다. RCSB PDB 공개 데이터 사용 정책은 [RCSB 정책 안내](https://www.rcsb.org/pages/policies), UniProt 데이터의 CC BY 4.0 조건은 [UniProt 이용 조건](https://www.uniprot.org/help/license)을 참고하세요.
