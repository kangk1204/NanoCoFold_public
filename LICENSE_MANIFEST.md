# 외부 구성 요소와 이용 조건

NanoCoFold 자체 코드에는 별도 공개 라이선스를 부여하지 않습니다.
공개 저장소라는 사실만으로 수정·재배포·상업 이용 권한이 부여되는 것은 아닙니다.
개별 이용 허가는 저장소 소유자에게 문의하세요. 아래 외부 구성 요소에는 각각의
원래 라이선스가 적용됩니다.

## 저장소에 포함된 브라우저 라이브러리

| 구성 요소 | 라이선스 고지 | 배포 파일 |
|---|---|---|
| Mol* | [MIT 고지](assets/MOLSTAR_LICENSE.txt) | `assets/molstar.js`, `assets/molstar.css` |
| Plotly.js | [MIT 고지](assets/PLOTLY_LICENSE.txt) | `assets/plotly.min.js` |
| 3Dmol.js | [BSD-3-Clause 고지](assets/3DMOL_LICENSE.txt) | `assets/3dmol-min.js` |

버전과 파일 SHA256은 `assets/vendor_manifest.json`에 있습니다.
Mol* 번들은 사용하지 않는 MP4 초기화를 비활성화했습니다. 변경 방법과 원본 검증은
`scripts/vendor_molstar.py`에 기록되어 있습니다.

## 설치 중 별도로 받는 도구·모델

모델 가중치와 대형 데이터베이스는 이 저장소/Release에 포함하지 않습니다.
설치기가 공식 배포처에서 버전·SHA256을 확인해 다운로드합니다.
코드, 모델 가중치, 참조 데이터, 외부 서비스의 조건은 서로 다를 수 있으므로
사용 목적에 맞게 각 배포처의 조건을 확인하세요.

- [RFantibody](https://github.com/RosettaCommons/RFantibody) — 생성 및 구조 검증.
- [BoltzGen](https://github.com/HannesStark/boltzgen) — 후보 생성.
- [Boltz](https://github.com/jwohlwend/boltz) — 구조 예측.
- [Chai-1](https://github.com/chaidiscovery/chai-lab) — 구조 예측.
- [OpenDDE](https://github.com/aurekaresearch/OpenDDE) — 구조 예측. Upstream에서 Preview로 설명하며 버전 사이 재현성을 보장하지 않습니다.
- [ColabFold](https://github.com/sokrypton/ColabFold) 및 [AlphaFold](https://github.com/google-deepmind/alphafold) — 구조 예측. AlphaFold 모델 파라미터에는 별도 조건이 있습니다.
- [ESM](https://github.com/facebookresearch/esm), [ANARCI](https://github.com/oxpig/ANARCI), [AntiFold](https://github.com/oxpig/AntiFold), [ImmuneBuilder](https://github.com/oxpig/ImmuneBuilder) — 서열·항체 분석.

정확한 설치 버전과 출처는 `nanocofold/_data/locks/`,
가중치·자산 출처와 해시는 `nanocofold/_data/model-assets.json` 및
`nanocofold/_data/opendde-assets.json`에 있습니다.

## 공개 예제와 MSA

예제의 원본 주소·변환 내역·서열 SHA256은 `examples/tutorial/manifest.json`에 있습니다.
공개 PDB 데이터와 프로그램 자체의 권리는 구분합니다.
[wwPDB 이용 정책](https://www.wwpdb.org/about/usage-policies)을 참고하세요.

공용 MSA 서버를 선택하면 입력 서열이 외부로 전송됩니다. 프로그램 설치 자체는
서열 전송을 수행하지 않습니다. 공개 예제를 실행할 때 명시적으로 전송을 허용할 수
있습니다. 비공개 서열에는 조직의 승인을 받거나 로컬 MSA 경로를 사용하세요.
[ColabFold 서버 안내](https://github.com/sokrypton/ColabFold#faq)에 따라
한 IP에서 직렬 요청하고 대량·병렬 서비스처럼 사용하지 마세요.
