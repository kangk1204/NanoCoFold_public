# Browser captures

`en/` contains English UI captures; `ko/` contains Korean UI captures.

The environment and input/review screens were captured from the real local application. No environment worker, GPU prediction, or MSA acquisition was executed. The review screens deliberately show a missing installation-check receipt; they are not proof of readiness.

Chart screens display a separate, read-only presentation copy of the previously verified rc5 barnase–barstar result (four evaluated models). Saved scientific data was compared for exact JSON equality. The original report, inputs and prior captures were preserved.

The automated responsive layout gate checks 390, 768, 1024, 1280 and 1440 CSS pixels in both languages. Browser fixture tests verify UI transitions; they do not establish GPU engine readiness or prediction quality.

On 16 October 2026, `en/01-purpose.jpg` and `en/04-review.jpg` were recaptured from version 0.4.8rc7 in a local CPU-only browser session to replace an incomplete loading screen and a collapsed review layout. The review capture has current-input MSA consent checked, but no environment-check receipt; analysis remains disabled. No environment worker, MSA acquisition or prediction was submitted. All other captures remain unchanged. The previous two files are preserved in the local validation evidence; their hashes and the new capture details are recorded in the manifest.

2026년 10월 16일 영어 목적 선택·검토 화면 두 장만 0.4.8rc7의 로컬 CPU 브라우저에서 다시 촬영했습니다. 검토 화면은 현재 입력의 MSA 동의가 체크되어 있으나 환경 확인 기록이 없어 분석 실행이 잠긴 상태입니다. 환경 확인 작업·MSA 수집·예측은 제출하지 않았고, 나머지 캡처와 기존 결과는 보존했습니다.

File digests are recorded in `capture_manifest.json`.
