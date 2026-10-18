# rc9 screenshot record / rc9 화면 촬영 기록

These 26 screenshots were captured from the real NanoCoFold **0.4.8rc9** interface on **18 October 2026**, using public source commit [`26925c3311be3deff388aa9ccc473f398bd9ecb9`](https://github.com/kangk1204/NanoCoFold_public/commit/26925c3311be3deff388aa9ccc473f398bd9ecb9). They replace the mixed rc5/rc7 screenshots linked from the English and Korean quickstarts and READMEs. Historical capture folders remain available.

26개 그림은 **2026년 10월 18일**, 공개 소스 **0.4.8rc9**(`26925c3`)의 실제 브라우저 화면을 촬영한 것입니다. 한영 빠른 시작 안내와 README에 섞여 있던 rc5·rc7 그림을 교체했습니다. 이전 촬영 기록은 기존 폴더에 보존했습니다.

## Capture conditions / 촬영 조건

- Local browser preview with an isolated workspace and `start_workers=False`. No environment-check job, GPU inference, remote MSA request, or score recomputation was started.
- Native browser JPEG captures, including full-page and section captures. No image synthesis, text replacement, or data editing was used. The layout can vary with browser width and height.
- Input and settings use the application's public examples. Complex input is Barnase 110 aa plus Barstar 89 aa, one copy each. All four evaluation engines are selected; seed 42 and one model per engine are retained.
- Settings screenshots show MSA consent unchecked. Review screenshots show the current public input reviewed locally, with analysis disabled because the preview has no saved environment check. They do not establish GPU readiness.
- English and Korean result views select `chai1__seed_42_model_0`: two chains, 199 residues, ipSAE 0.946, whole-complex ipTM 0.939, and a 199 × 199 PAE matrix. These are displayed, rounded values from the saved result.
- `07-pae.jpg` shows the full PAE with the default ipSAE / interface pLDDT comparison. `charts.jpg` demonstrates changing the Y axis to interface PAE (Å). Both keep all four saved model records.

촬영은 작업 실행기를 끈 별도 로컬 작업 공간에서 진행했습니다. 환경 검사·GPU 예측·공용 MSA 전송·점수 재계산을 실행하지 않았습니다. 그림 4는 환경 확인 기록이 없어 분석 실행이 잠긴 상태입니다. 결과는 한영 모두 같은 Chai-1 모델을 선택했고, 표시 수치와 원본 데이터를 대조했습니다. 차트 그림에서는 Y축을 계면 PAE로 바꾸는 기능을 확인했습니다.

## Result provenance / 결과 출처

The result panels use the rc9 offline viewer with the **unchanged JSON payload** from the previously preserved, real four-engine Barnase–Barstar GPU report. This refresh updates the presentation; it is not a new rc9 inference run or evidence of biological validation.

결과 그림은 기존 실제 네 엔진 GPU 보고서의 **JSON 데이터를 그대로** rc9 오프라인 뷰어에 표시했습니다. 화면 촬영 시점과 계산 시점은 다릅니다. 이번 촬영은 rc9에서 새로 계산했거나 생물학적 유효성을 검증했다는 뜻이 아닙니다.

| Record | Value |
|---|---|
| Original job | `e6bf4fc488e85d81b55574f4c1fb8ca1` |
| Original compute source | `fe1942e3e4afd202adeadcdb3f03581e982a4151` |
| Original report SHA-256 | `71a81e954c535cde654961629720b623c5a10665ac9699da9ed70f410f36802a` |
| Canonical result JSON SHA-256 | `38d8ee1c9b1d69de44c602743386f3488ee9f3441a267e16f33a2bd1851ba6af` |
| rc9 rendered report SHA-256 | `7c11ea24c1444b854e7a8199a051b4e3083b73103ac9f9de0d223d0528436467` |
| Original report unchanged | Yes |
| Parsed result JSON equal before/after rendering | Yes |

Canonical JSON uses UTF-8, sorted keys, `ensure_ascii=False`, and separators `(',', ':')`. See the [original result capture record](../quickstart-rc5/README.md). The immutable `v0.4.8rc9` tag and release downloads remain at `26925c3`; this documentation refresh is published on `main`.

## Image index / 그림 목록

Each row has one English and one Korean capture. File hashes, dimensions and capture timestamps are in [capture_manifest.json](capture_manifest.json).

| Screen | English | 한국어 |
|---|---|---|
| Environment, before checking | [View](en/environment.jpg) | [보기](ko/environment.jpg) |
| 1. Purpose | [View](en/01-purpose.jpg) | [보기](ko/01-purpose.jpg) |
| 2. Public complex input | [View](en/02-input.jpg) | [보기](ko/02-input.jpg) |
| 3. Complex settings | [View](en/03-settings.jpg) | [보기](ko/03-settings.jpg) |
| 4. Review; environment check required | [View](en/04-review.jpg) | [보기](ko/04-review.jpg) |
| 5. Result counts | [View](en/05-results.jpg) | [보기](ko/05-results.jpg) |
| 6. Selected structure | [View](en/06-structure-pae.jpg) | [보기](ko/06-structure-pae.jpg) |
| 7. PAE and saved metrics | [View](en/07-pae.jpg) | [보기](ko/07-pae.jpg) |
| Design settings: at most 8 models | [View](en/settings-design.jpg) | [보기](ko/settings-design.jpg) |
| Screen settings: 1 input VHH × 4 engines | [View](en/settings-screen.jpg) | [보기](ko/settings-screen.jpg) |
| Mature settings: at most 12 models | [View](en/settings-mature.jpg) | [보기](ko/settings-mature.jpg) |
| Chart axis selection | [View](en/charts.jpg) | [보기](ko/charts.jpg) |
| Report navigation | [View](en/report-nav.jpg) | [보기](ko/report-nav.jpg) |
