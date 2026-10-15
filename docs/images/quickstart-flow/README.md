# Browser captures

`en/` contains English UI captures; `ko/` contains Korean UI captures.

The environment and input/review screens were captured from the real local application. No environment worker, GPU prediction, or MSA acquisition was executed. The review screens deliberately show a missing installation-check receipt; they are not proof of readiness.

Chart screens display a separate, read-only presentation copy of the previously verified rc5 barnase–barstar result (four evaluated models). Saved scientific data was compared for exact JSON equality. The original report, inputs and prior captures were preserved.

The automated responsive layout gate checks 390, 768, 1024, 1280 and 1440 CSS pixels in both languages. Browser fixture tests verify UI transitions; they do not establish GPU engine readiness or prediction quality.

File digests are recorded in `capture_manifest.json`.
