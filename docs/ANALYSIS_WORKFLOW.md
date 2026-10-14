# NanoCoFold의 분석 과정과 결과가 만들어지는 방법

처음 실행할 때는 [README](../README.md)의 버튼 순서를 따르세요. 이 문서는 **입력이 어떤 계산을 거쳐 어떤 결과가 되는지** 설명합니다. 현재 소스의 기본 네 엔진 비교 경로를 기준으로 하며, 각 실행에 저장된 설정·조건·출처를 우선합니다.

## 처음 읽을 때 알아둘 흐름

**입력 검토 → 표적·MSA 준비 → 후보 생성 또는 기존 후보 사용 → 복합체 예측 → 구조·PAE 확인 → 지표·선별 → 표·HTML·ZIP 저장** 순서입니다. 입력 검토는 계산을 제출하지 않습니다. **분석 실행**을 누르면 서버의 GPU 작업으로 제출됩니다.

| 목적 | 입력에서 시작하는 일 | 복합체 예측 후 볼 결과 |
|---|---|---|
| complex | 단백질별 전체 서열, 순서와 사본 수를 체인 목록으로 확정 | 모델별 구조, 모든 체인 쌍의 지표, 전체 PAE |
| design | 검토한 표적에서 RFantibody·BoltzGen으로 새 VHH 후보 생성 | 생성기 원본과 별도로 각 예측 엔진에서 다시 계산한 표적–VHH 복합체 |
| screen | 제출한 전체 VHH 목록을 유지 | 같은 후보를 선택한 각 엔진에서 독립적으로 예측한 결과 |
| mature | WT의 CDR을 정하고 치환 라이브러리를 만들거나 설정한 변이 표본 준비 | 선택된 변이와 WT를 같은 엔진·조건에서 예측한 비교 결과 |

작은 시작 설정은 Seed 42, 엔진별 모델 1개와 ColabFold·Boltz-2·Chai-1·OpenDDE 비교를 사용합니다. complex는 최대 4모델, design은 생성기별 1후보씩 최대 8모델, screen은 **전체 입력 후보 수 × 선택 엔진 수**, mature는 최대 2변이와 WT를 평가해 최대 12모델을 계획합니다. 실제 생성·선별·성공 수는 별도로 기록합니다. 이 수치는 시간·메모리·성공 보장이 아닙니다.

## 1. 입력을 확정하고 기록하기

아미노산 FASTA 또는 지원하는 PDB/mmCIF 구조를 사용합니다. VHH는 전체 가변영역이며 CDR3 조각만으로 대체하지 않습니다. 입력 이름, 표준 잔기, 중복 ID, 체인·사본 수와 길이를 검토합니다. 구조 파일과 PDB ID는 대안 입력이며 동시에 추가할 필요가 없습니다. 파일로 불러온 VHH·WT도 화면에 채워진 FASTA가 실제 입력입니다.

구조 입력에서는 **관측된 서열·좌표와 사용자가 확인한 전체 서열**을 구분합니다. 누락 잔기, 원본 잔기 번호와 삽입 코드, 변형 잔기, 선택 체인, 분석 구간과 파트너 체인을 확인합니다. 서열로 새 구조를 준비하는 경로는 원본 좌표 사용과 다른 계산입니다. 지원하지 않는 변형 화학 구조나 모호한 번호 대응을 자동으로 정확한 구조로 취급하지 않습니다.

검토 결과와 최종 적용 설정은 실행 기록에 남습니다. 일반 complex의 FASTA는 위에서 아래 순서로 체인에 배정하며 동일 서열의 사본도 별도 체인 축을 유지합니다. 표시용 A·B·context 역할은 프로그램의 체인 식별을 위한 것이며 생물학적 역할을 증명하지 않습니다.

## 2. 표적과 MSA 준비하기

MSA는 각 입력 단백질과 관련된 서열을 정렬한 정보입니다. 기본 비교 경로는 **현재 표적·파트너·각 후보의 정확한 서열에 대응하는 monomer MSA**를 준비하고, 각 예측 엔진에 unpaired 입력으로 전달합니다. 생성·변이 후보도 실제 평가할 전체 서열로 준비합니다. WT MSA를 변이에 그대로 쓴 것으로 기록하지 않습니다.

- **공용 MSA**: 현재 표적·후보 서열을 `api.colabfold.com`으로 보내는 명시적 동의가 필요합니다. 요청·출처와 받은 정렬의 해시를 기록합니다.
- **검증된 캐시**: 현재 서열과 정확히 대응하는 정렬 및 receipt를 확인합니다. 캐시가 없거나 맞지 않으면 오류로 중단합니다.
- **등록된 로컬 검색**: 관리자가 등록한 검색 경로와 출처를 사용합니다. 사용자 임의 서버·명령을 그대로 실행하는 입력이 아닙니다.

선택한 경로가 실패했다고 승인 없는 공용 전송으로 바꾸지 않습니다. 같은 monomer 원본을 사용해도 엔진별 서열 수 제한, Chai의 aligned parquet 변환 등 **실제 소비한 형식과 조건은 다릅니다.** 요청 방식, 실제 정렬 파일, 변환 receipt와 해시를 함께 읽으세요. 공용 데이터베이스는 변할 수 있으므로 같은 Seed만으로 새 MSA를 받은 실행의 결과 동일성을 보장하지 않습니다.

일반 complex의 네 엔진 경로도 이 준비 계층을 사용합니다. 같은 서열의 homomer 사본은 MSA 캐시를 공유할 수 있지만 구조·PAE에서 각각의 체인은 유지됩니다. 과거 `complex_batch.json`의 Boltz-2 단일 엔진·MSA 없음 조건은 현재 기본 비교 조건과 다릅니다.

## 3. 후보를 만들거나 제출한 후보를 유지하기

**design**은 RFantibody·BoltzGen의 생성 출처와 후보 전체 서열을 기록합니다. 생성 단계 구조는 설계 참고 구조입니다. 최종 복합체 예측과 그 지표를 대신하지 않습니다. 같은 후보를 ColabFold·Boltz-2·Chai-1·OpenDDE에 넣어 다시 예측하고 생성기 구조와 재예측 구조를 구분합니다.

**screen**은 제출한 후보를 각각 유지해 엔진별로 평가합니다. 작은 시작 설정은 후보를 몰래 앞부분만 선택하지 않습니다. 결과의 원본 후보 ID와 `source_sequence_id`, `predictor_engine`을 같이 확인하세요. 한 VHH가 네 엔진에서 평가돼 네 행이 생겨도 네 개의 서로 다른 VHH를 만든 것은 아닙니다.

**mature**는 ANARCI 주석 또는 명시적으로 검토한 서열 위치의 CDR 범위를 사용합니다. DMS는 해당 CDR의 단일 치환을 열거하고 Cys 보호 등 설정한 제외 규칙을 적용합니다. `max_variants`가 있으면 가능한 변이에서 Seed로 재현 가능한 표본을 선택합니다. 작은 시작 설정의 3변이는 전수 DMS 분석이 아닙니다. random 다중 치환 경로는 별도 설정과 고유 서열 수·변이 개수 기록을 사용합니다.

ESM ΔLLR는 변이 서열의 모델상 적합도 prior이며 결합 친화도 변화가 아닙니다. threshold 적용 여부와 `top_n`을 별도로 기록합니다. 작은 시작 설정은 제한된 변이에 ESM을 계산하되 threshold 강제 제외를 끄고 최대 2개와 WT를 계획합니다. AntiFold는 명시적으로 켜고 필요한 WT 구조가 있을 때의 별도 평가입니다. ANARCI·ESM 실패나 전체 미평가를 통과로 바꾸지 않습니다.

## 4. 엔진의 원본 구조와 PAE를 연결하기

각 예측에 엔진, 조건, Seed, 모델·샘플 ID와 산출물 출처를 기록합니다. **표 한 행의 점수는 그 행에서 선택한 구조·confidence·PAE에만 대응합니다.** 여러 샘플이 저장돼 있어도 자동으로 모든 샘플을 채점·평균한 결과가 아닙니다. `prediction_sample_index`, `prediction_retained_sample_count`, `prediction_sample_selection`을 확인하세요. 샘플을 명시하지 않는 디렉터리 채점 경로는 첫 보존 모델을 선택하고 이 선택을 기록합니다.

PAE는 정사각형, 유한한 비음수 값, 구조의 잔기 수와 차원 대응을 확인합니다. 체인·서열 순서와 native chain-axis 정보로 confidence의 체인 쌍을 연결합니다. 대응이 검증되지 않은 다중 체인 pair ipTM을 복합체 전체 ipTM으로 대신하지 않습니다.

**엔진 원본 좌표**와 **화면 표시 좌표**도 구분합니다. 일반 complex의 표시 구조는 원본 잔기 번호에 대응하며 참조 구조가 있으면 선택적으로 정렬합니다. 별도로 원본 엔진 파일과 SHA-256을 보존합니다. 다른 결과도 보고서에 표시한 `coordinate_source`, 잔기 대응과 참조 정렬 정보를 우선합니다. 체인 강조·확대와 참조 정렬은 저장된 예측 점수를 개선하거나 새 추론을 수행하는 기능이 아닙니다.

화면의 **선택 예측 구조 저장**은 표시 좌표, **원본 엔진 좌표 다운로드**는 보존된 원본 파일입니다. 원본이 전달 보고서에 없으면 원본 다운로드 버튼을 활성화하지 않습니다.

## 5. 계면 지표와 hotspot을 계산하기

표적–VHH 경로는 지정된 표적·결합자 쌍의 원자 좌표와 PAE를 사용합니다. 일반 complex는 모든 체인 조합의 지표를 계산합니다. 일반 complex 모델 표의 전체 ipTM과 체인 쌍 상세의 방향별 ipTM은 다른 항목입니다. 요약 ipSAE는 기본적으로 검토한 체인 순서의 첫 쌍의 값입니다. `complex.primary_pair`를 명시한 실행은 해당 체인쌍을 사용하며 전체 평균·최솟값과 구분합니다. `max_pair_ipsae`와 `min_pair_ipsae`는 유한한 값이 기록된 체인쌍의 최댓값·최솟값이며 평가 쌍 수와 전체 쌍 수를 함께 확인합니다. `ipsae_scope`·적용 체인·범위 출처를 확인하고 모든 체인 쌍은 상세 표·CSV로 읽습니다.

| 항목 | 현재 코드에서 읽거나 계산하는 값 | 해석 범위 |
|---|---|---|
| ipTM | native confidence의 검증된 표적–결합자 pair. 두 방향이 있으면 작은 값 사용; 정확히 두 체인인 경우에만 native global fallback 가능 | pair 출처·체인 축·미제공 사유를 함께 확인 |
| 계면 pLDDT | 설정한 접촉 거리 안의 계면 잔기 confidence 평균 | confidence가 없으면 미평가. 원본 B-factor의 전부 0인 값을 신뢰도 측정으로 취급하지 않음 |
| 계면 PAE | 접촉 잔기 쌍의 양방향 PAE와 방향별·중앙값·90백분위 요약 | 같은 모델의 잔기 대응과 접촉 정의에 의존 |
| ipSAE | PAE 기반 자체 계산 또는 요청한 공식 scorer의 결과 | `ipsae_source`, `ipsae_scope`, 적용 체인, 공식 scorer 상태·오류와 precision 확인. 공식 평가 실패를 숨기지 않음 |
| 접촉·충돌 | heavy-atom 잔기 접촉 및 설정한 clash 거리의 원자 쌍; context–binder 충돌도 별도 포함 | 기본 접촉 cutoff 5 Å, clash 2 Å. 실행의 저장 설정 우선 |
| N/O 근접·반대 전하 접촉 원자쌍 수 | 설정한 거리와 원자 유형을 사용하는 기하학적 후보 수 | 계산된 근접수이며 실험 결합·에너지 측정값이 아님 |
| hotspot 접촉률 | 요청한 잔기 번호를 대응시키고 실제 최소 거리로 판정한 접촉 분율 | 반올림 전 값으로 판정. 미지정·미평가·번호 대응 실패·실제 0접촉을 구분 |

hotspot 또는 분석 domain을 지정하면 **어떤 표적 구간·잔기를 포함했는지와 대응·좌표 coverage**를 확인합니다. 요청한 부위가 없거나 대응이 실패했는데 접촉률 0 또는 부위 일치로 읽지 않습니다. 선택한 단편의 결과를 전체 표적의 모든 domain에 대한 결론으로 확대하지 않습니다.

Boltz-2·Chai의 설정된 restraint와 예측 후 hotspot 평가는 구분합니다. ColabFold·OpenDDE 비교 경로는 해당 restraint를 지원하지 않아 예측 후 부위 접촉을 평가합니다. 공통 서열을 썼다는 이유만으로 동일한 제약 계산이라고 주장하지 않습니다.

일반 복합체의 체인쌍 평가에서는 한 모델의 구조·PAE·신뢰도 맵을 재사용합니다. 거리 기준에서 접촉이 없는 쌍의 SASA는 미평가로 남기며 물리적 매몰 면적0으로 단정하지 않습니다.

참조 구조 비교에서 여러 사본은 사본 수와 같은 길이의 `reference_chains`로 대응을 명시할 수 있습니다. 동일 서열 사본의 대칭 순열은 최대4,096개 범위에서 정확 탐색하며, 한도를 넘으면 최적 대응이라고 주장하지 않고 비교 불가 사유를 기록합니다. `reference_symmetry=false`는 고정 대응 비교입니다. anchor 기준 RMSD와 복합체 전체 최적 적합 RMSD의 범위를 구분해 읽으세요.

## 6. 개발가능성, 상대 순위와 선별하기

VHH 경로는 Cys, glycosylation motif, CDR 내 deamidation·isomerization·oxidation·소수성 patch, pI·전하 등 **설정된 서열 기반 위험 규칙**을 계산합니다. CDR 평가에는 후보별 유효한 CDR1/2/3 대응이 필요합니다. CDR이 미평가이면 관련 flag를 False나 0위험으로 만들지 않습니다. `dev_severe`는 명시된 중대 규칙 수, `dev_flags`는 평가된 전체 True 규칙 수이며 같은 값이 아닙니다. 이 규칙이 실제 발현·안정성·면역원성을 측정한 것은 아닙니다.

표준 네 엔진 비교의 `composite_score`는 **같은 예측 엔진의 해당 배치 내 상대 점수**입니다. 설정 가중치의 각 지표를 batch z-score로 변환하고 방향·가중치를 적용합니다. 배치 구성과 가중치가 바뀌면 점수도 달라지므로 다른 엔진·다른 배치의 composite 값을 직접 비교하지 않습니다.

정규화에는 기록된 대조군도 포함하며, 선별 풀에서 WT·대조군을 뺀 뒤 점수를 다시 정규화하지 않습니다. 무접촉(`n_contacts=0`) 또는 가중 계면 지표가 미평가인 행은 composite·rank와 선별을 보류하고 정규화 집단에서 제외합니다. 나머지 허용된 지표 항의 결측은 해당 정규화 집단의 중앙값으로 보완될 수 있고 상수 또는 유한값 2개 미만의 항은 상대 점수 기여가 0입니다. **이는 계산상 상대 순위 처리이며 raw 결측을 측정값 0이나 기준 통과로 대체하는 것이 아닙니다.** CDR 위험 가중치를 사용하는데 평가가 불완전하면 composite·rank와 선별을 보류합니다. 실패·중단 행은 기록하되 정규화와 선별에서 제외합니다.

최종 VHH 선별은 ipSAE·pair ipTM·계면 pLDDT·충돌 기준, 설정한 hotspot 필수 여부, CDR 개발가능성 평가와 중대 위험 한도를 확인합니다. `pass_ipsae_strict`는 별도 진단이며 최종 선별 게이트로 대신 사용하지 않습니다. `final_evaluated`, `final_eligible`, `exclusion_reason`과 기록된 threshold를 같이 읽으세요.

통과 후보에 CDR3 다양성 제한과 최종 개수 제한을 적용합니다. CDR3 유사도는 정규화한 편집거리와 대표 기반 군집입니다. 대표는 서열 빈도 내림차순, 길이 내림차순, 사전순 오름차순으로 결정한 순서에 따라 탐욕적으로 선택하며 생물학적으로 보정된 거리나 결합 부위 동일성 측정이 아닙니다. WT는 대조군으로 남고 발주 선별에서는 제외합니다. 적격 0개이면 기본 설정은 빈 선별 목록을 보존합니다.

엔진별 점수를 평균해 통합 순위를 만들지 않습니다. 자세 일치·부위 충족의 추가 진단도 원래 점수와 엔진 순위를 덮어쓰지 않습니다. 과거 선택적 부분 재채점 경로에 `mixed_engine_batch_weighted_zscore_legacy`가 기록돼 있다면 그 혼합 정규화 범위를 그대로 공개한 과거 결과입니다. 엔진 간 통합 우위나 공통 신뢰도 척도로 사용하지 않습니다. 실제 적용 정책은 `03_score/ranking_policy.json`을 확인하세요.

일반 complex는 VHH 후보를 설계하거나 발주 후보로 선별하는 경로가 아닙니다. 구조·체인 쌍을 열람하며 일반 complex의 `final_eligible=null`, `selected=false`를 VHH 품질 게이트 실패로 읽지 않습니다.

## 7. 결과를 저장하고 실패를 해석하기

| 저장 항목 | 확인할 내용 |
|---|---|
| `run_config.yaml`, `run_state.json` | 최종 조건과 단계·실패·복구·시간 이력 |
| MSA manifest·acquisition receipt | 입력 서열 대응, provider, 요청/캐시 출처, 실제 정렬·변환 해시 |
| comparison identity·completion receipt | 후보·엔진·Seed·런타임·가중치와 실제 산출물의 동일성 |
| `03_score/scored_candidates.csv` | 평가된 원본 지표, 미평가, 오류·게이트·순위 출처 |
| `05_final/selected_candidates.csv` | 설정한 선별·다양성 정책을 거친 실제 후보 |
| 일반 complex의 `complex_results.json`, `models.csv` | 입력 체인·모델·체인 쌍 지표와 성공·실패 |
| HTML·CSV/XLSX·FASTA·전달 ZIP | 보존한 모델의 결과를 열고 공동연구자가 읽을 파일 |

결과 준비, 지표 채점, 보고서 생성, ZIP 전달은 각각 실패할 수 있습니다. 예측이 끝났어도 보고서나 ZIP 생성이 실패했다면 전달 완료로 표시하지 않습니다. 실패·부분 완료와 이전 산출물을 보존합니다. 재개·재사용은 현재 입력·조건·소스/런타임·가중치·MSA와 산출물 해시가 맞는 범위에서만 수행합니다. 조건이 바뀌면 이전 계산을 보존하고 별도 실행을 사용합니다.

읽기 전용 보고서 재발행은 새 GPU 계산이 아닙니다. 재채점도 새 추론과 구분합니다. 과거 출력의 표시 UI 갱신·개인 경로 익명화는 원본 계산 버전과 과학 데이터 보존 여부를 따로 기록합니다. 원본 파일·해시가 없는 과거 결과에 현재 런타임 검증을 소급해서 부여하지 않습니다.

발주/실험 인계 게이트는 별도입니다. host·vector·assay, construct/vendor 검토, antigen QC·controls 및 필요한 실제 서열·후보 승인 정보를 확인합니다. 계산의 선별 통과만으로 발주 준비나 실험 결합 확인을 대신하지 않습니다. 여러 엔진 비교에서는 엔진별 추천을 보존하며 통합 발주 후보는 연구자가 검토해 결정합니다.

## 소스에서 확인할 위치

다음은 이 설명의 소스 지도입니다. 처음 사용하는 사람은 수정할 필요가 없습니다.

| 단계 | 소스 |
|---|---|
| CLI 단계 실행·최종 채점·출판 상태 | [cli.py](../nanocofold/cli.py) |
| 기본 설정과 작은 시작 설정 | [default.yaml](../config/default.yaml), [starter-presets.json](../nanocofold/_data/starter-presets.json) |
| 입력 구조·서열·번호 검토 | [platform/inputs.py](../nanocofold/platform/inputs.py) |
| 일반 complex 체인·사본·모델·pair 출력 | [complex.py](../nanocofold/complex.py) |
| 일반 complex 네 엔진·공통 monomer 입력 변환 | [complex_comparison.py](../nanocofold/complex_comparison.py) |
| 표적–VHH 엔진 비교·MSA·독립 순위 | [engine_comparison.py](../nanocofold/engine_comparison.py) |
| 생성기 선택·설계 구조 출처 | [design_routing.py](../nanocofold/design_routing.py), [pose_integration.py](../nanocofold/pose_integration.py) |
| 엔진 실행·구조 산출물 | [screen.py](../nanocofold/screen.py), [colabfold.py](../nanocofold/colabfold.py), [opendde.py](../nanocofold/opendde.py) |
| CDR·변이·ESM prefilter | [mature.py](../nanocofold/mature.py) |
| 모델 선택·PAE·계면 지표·threshold | [score_complex.py](../nanocofold/score_complex.py) |
| 서열 위험·CDR 미평가 | [developability.py](../nanocofold/developability.py) |
| batch 정규화·CDR3 군집·선별 | [rank_select.py](../nanocofold/rank_select.py) |
| 부위·자세·coverage 진단 | [binding_site_assessment.py](../nanocofold/binding_site_assessment.py), [pose_assessment.py](../nanocofold/pose_assessment.py) |
| 코드·환경·모델·산출물 해시 | [provenance.py](../nanocofold/provenance.py) |
| HTML·결과 읽기·전달 manifest | [report.py](../nanocofold/report.py), [platform/results.py](../nanocofold/platform/results.py), [report_delivery.py](../nanocofold/report_delivery.py) |
| 발주·실험 인계 조건 | [handoff_validation.py](../nanocofold/handoff_validation.py) |

현재 새 설치의 GUI complex 네 엔진 실행과 별도 다중 체인·사본 수 보충 시험은 확인했습니다. 나머지 모드와 최종 전달본의 정확한 완료·실패·복구 범위는 [VALIDATION](VALIDATION.md) 및 묶음의 `validation/status.json`을 우선합니다. 이 문서는 구현된 계산 과정을 설명하며 예측 정확도·새로움·결합 효과를 검증했다고 주장하지 않습니다.
