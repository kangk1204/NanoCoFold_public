# 변이 100종을 네 엔진으로 비교하기

[설정 파일](../examples/mature_100_four_engines.yaml)을 내려받아 NanoCoFold 폴더에 저장하세요. 최신 코드로 업데이트한 뒤 사용합니다. 예제 입력은 IL-6 표적과 anti-IL-6 VHH입니다. 자신의 단백질을 분석하려면 `target.fasta`와 `mature.wt_fasta`를 바꾸세요.

이 설정은 CDR의 **단일 아미노산 치환** 후보를 만든 뒤 ESM 사전 평가로 최대 100종을 고릅니다. 시작 설정의 3종 제한은 해제했습니다. 변이 100종과 WT 1종을 ColabFold, Boltz-2, Chai-1, OpenDDE에서 평가하므로, 후보가 충분하면 **총 404개 구조**를 요청합니다. 생성 가능한 변이가 100종보다 적거나 엔진이 실패하면 실제 완료 수는 줄어듭니다. 최종 적격·선별 수는 별도입니다.

## 실행

진행 중인 분석을 끝낸 후 업데이트하세요. NanoCoFold 환경을 활성화하고 저장소 폴더에서 실행합니다.

```bash
nanocofold run --config examples/mature_100_four_engines.yaml
```

기본은 `cache`입니다. 표적·WT·평가할 변이 각각과 정확히 일치하는 MSA가 필요합니다. 처음 만드는 변이는 캐시에 없을 수 있습니다. 공개 MSA 검색에 서열 전송을 동의하는 경우에만 다음 명령을 사용하세요.

```bash
nanocofold run --config examples/mature_100_four_engines.yaml \
  --set screen.colabfold.msa_source=public_api \
  --set screen.allow_public_msa_server=true
```

외부 전송 없이 검색하려면 관리자가 로컬 MSA DB를 등록한 뒤 `screen.colabfold.msa_source=local_mmseqs2`를 사용합니다. 네 예측 엔진과 ESM·CDR 도구의 설치도 필요합니다. 이 안내는 계산을 자동으로 시작하지 않습니다.

## 웹에서 설정

나노바디 성숙화 → 고급 YAML 가져오기에서 설정을 불러오세요. 검토 화면에서 후보 상한 100, WT 포함, 엔진 4개, 엔진당 샘플 1개, Seed 42를 확인합니다. 공개 MSA를 선택한 경우 현재 입력의 전송 동의도 확인합니다.

## 결과 확인

작업 완료 후 결과 탐색에서 엔진별 성공·실패 수와 지표를 확인하세요. 네 엔진의 점수는 합산하지 않습니다. ColabFold의 recycling과 다른 엔진의 recycling은 같은 횟수라도 같은 계산을 뜻하지 않습니다. 모든 엔진을 함께 비교할 때 ColabFold·OpenDDE의 입력 길이 제한(총 2,000잔기)을 만족해야 합니다.

한 후보에서 **2개 이상을 동시에 치환**하려면 이 단일 치환 설정 대신 웹에서 `CDR 무작위 다중 잔기 치환`을 선택하고, 변이 수 100과 최소·최대 치환 수를 설정하세요. CDR3만 또는 지정 CDR 구간을 선택할 수 있습니다.
