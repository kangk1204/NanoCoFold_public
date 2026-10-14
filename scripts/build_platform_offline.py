"""Build the self-contained explorer template from the same browser UI assets."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1] / "nanocofold/templates"
document = (root / "platform.html").read_text()
section = document.split('<section id="view-results" hidden>', 1)[1].split('<section id="view-environment"', 1)[0].rsplit('</section>', 1)[0]
section = '<section id="view-results">' + section + '</section>'
for identifier in ("refresh-results", "result-path", "open-result", "reanalyze", "compare-select", "compare-runs"):
    section = re.sub(r'(<(?:button|input|select)[^>]*id="' + identifier + r'"[^>]*)(>)', r'\1 hidden\2', section)
css = (root / "platform.css").read_text()
script = (root / "platform.js").read_text()
molstar_adapter = (root / "result-molstar.js").read_text()
result = '''<!doctype html><html lang="ko" data-renderer-version="nanocofold-results-v3"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NanoCoFold 오프라인 결과</title><style>__MOLSTAR_CSS__</style><style>''' + css + '''</style></head><body><header><strong>NanoCoFold · 오프라인 결과 탐색</strong><span>전체 후보 · 관측 · 구조 · 출처 포함</span></header><main><div id="notice" role="status" aria-live="polite" hidden></div>''' + section + '''</main><script id="result-data" type="application/json">__RESULT_DATA__</script><script>window.NANOCOFOLD_OFFLINE=JSON.parse(document.getElementById("result-data").textContent);</script><script>__PLOTLY_JS__</script><script>__3DMOL_JS__</script><script>__MOLSTAR_JS__</script><script>''' + molstar_adapter + '</script><script>' + script + '</script></body></html>'
(root / "platform_offline.html").write_text(result)
print("Built platform_offline.html from shared UI")
