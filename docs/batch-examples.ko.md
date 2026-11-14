# 배치 예제 입력 확인

[English](batch-examples.en.md)

NanoCoFold Python 환경을 활성화한 뒤 소스 폴더에서 실행합니다. `/path/to/workspace`는 설치할 때 정한 작업 공간 경로로 바꾸세요.

```bash
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/complex_batch.json
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/screen_batch.json
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/mature_batch.json
nanocofold batch review --workspace /path/to/workspace --manifest examples/tutorial/design_batch.json
```

입력을 확인하고 검토할 배치를 저장하는 명령입니다. 예측 계산이나 외부 서열 전송은 시작하지 않습니다. screen·mature·design 예제는 **캐시 MSA**를 사용합니다. 실제 계산에는 생성한 후보를 포함해 각 서열과 일치하는 MSA가 지정한 캐시에 있어야 합니다.

새 예제에서 공용 MSA API를 사용하려면 예제 실행 명령에 `--allow-public-msa`를 붙입니다.

```bash
python scripts/run_example.py screen --workspace /path/to/workspace --review-only --allow-public-msa
```

필요에 따라 `screen`을 `mature` 또는 `design`으로 바꾸세요. 이 옵션은 공용 MSA를 선택하고 예제의 표적·VHH·생성 서열을 api.colabfold.com에 보내는 데 동의한다는 뜻입니다. 저장소의 예제 파일은 바꾸지 않습니다. 같은 명령에서 `--review-only`를 빼면 검토한 배치를 실행합니다. 터미널을 열어 두면 진행 상태를 볼 수 있습니다. 명령을 다시 실행하면 저장된 배치를 열며, 이전 배치가 끝난 뒤 `--new-run`을 붙이면 별도 배치를 만듭니다.

웹에서는 처음 저장한 MSA 전송 동의를 이후 분석과 배치에도 적용합니다. **환경·설치**에서 동의를 변경할 수 있습니다. 동의가 저장되어 있어도 캐시·로컬 MSA 설정이 공용 API로 바뀌지는 않습니다.

새 CLI·웹·복합체 실행의 seed는 **0–2147483647**입니다. 이전 결과의 seed는 보고서에서 원래 값으로 읽습니다. 선택하지 않은 엔진의 설정을 바꾸면 적용되지 않는 항목을 안내합니다. 해당 엔진을 선택하거나 불필요한 설정을 지운 뒤 실행하세요. 임계값 하나만 바꾸려면 `--set score.thresholds.ipsae=0.6`처럼 항목 이름까지 지정하면 나머지 값이 유지됩니다.
