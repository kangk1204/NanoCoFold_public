# README 화면 캡처

2026-10-10, 공개 NanoCoFold 웹 애플리케이션을 별도의 빈 작업 공간에서 실제 Chromium으로 열어 촬영했습니다.
캡처한 UI 코드는 공개 버전 0.4.0(`d5d70b9818b7805d862daa5f17f093904ab78880`)이며 0.4.1에서도 같습니다.
이미지 생성·화면 요소 합성·점수 편집을 하지 않았습니다. 03은 실제 검토 영역만 캡처했습니다.

| 이미지 | 화면과 데이터 |
|---|---|
| 01-web-input.png | 공개 IL-6·anti-IL-6 VHH 서열을 넣은 직접 입력 화면. 제출하지 않음 |
| 02-public-example-upload.png | 배포된 complex_batch.json을 파일 선택으로 업로드 |
| 03-public-example-reviewed.png | 실제 batch review 통과. 계산 미제출 상태 |
| 04-cpu-demo-results.png | 프로그램의 CPU 데모로 생성한 합성 결과. MOCK 표시 유지 |

네 모드의 배포 JSON을 각각 웹 UI에서 업로드·검토했고 모두 검토 완료/미제출임을 확인했습니다.
실행한 작업은 공개판의 CPU 데모 1개뿐입니다. 실제 엔진 추론·MSA 전송을 수행하지 않았습니다.
촬영은 macOS의 Chromium에서 했으므로 Ubuntu GPU 실행 검증으로 해석하지 않습니다.
외부 네트워크를 차단했으며 촬영 과정에서 외부 요청과 JavaScript 오류가 없었습니다.

기존 연구 결과·개인 계정·서버 경로를 화면에 포함하지 않았습니다.
합성 결과 폴더와 작업 DB는 배포하지 않고 화면 안내용 PNG만 포함합니다.

## 이미지 SHA256

```text
ee9d1011c05339b3446158cc50eb7b4998fde0297e5bd38f5d354e0d594e8fc5  01-web-input.png
7f6cefcfb52873ff17e8c1fed17a4eab4a1d88d18f8fe1fdd318f22ea0900ea1  02-public-example-upload.png
e368db07cdeb494f85769ba673432a5197d9a528ce4742eec2b8d9789487fac0  03-public-example-reviewed.png
09e9b61c4320caf6aa7700b66b9ba5e44f43f033ec34bb3207a533fff0f9510a  04-cpu-demo-results.png
```
