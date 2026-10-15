/* Shared browser/offline explorer. Data is displayed with textContent, not HTML injection. */
"use strict";
const $ = (id) => document.getElementById(id);
const metricCaptions = Object.freeze({ptm:"pTM",pdockq:"pDockQ",pdockq2:"pDockQ2 (최소)",interface_pae_ab:"PAE 행 표적·열 VHH (Å)",interface_pae_ba:"PAE 행 VHH·열 표적 (Å)",interface_pae_median:"계면 PAE 중앙값 (Å)",interface_pae_p90:"계면 PAE 90백분위 (Å)",target_plddt:"표적 pLDDT",binder_plddt:"VHH pLDDT",complex_plddt:"전체 복합체 pLDDT",n_hotspots_requested:"지정 결합 부위 잔기 수",n_hotspots_contacted:"접촉한 지정 잔기 수",n_target_clashes:"표적–VHH 충돌 원자쌍",n_context_clashes:"파트너–VHH 충돌 원자쌍",candidate_id:"후보",ipsae:"ipSAE",iptm:"ipTM",interface_plddt:"계면 pLDDT",interface_pae:"계면 PAE (Å)",hotspot_contact_frac:"결합 부위 접촉률",seed:"Seed",model:"모델",error:"평가 오류",chain_a:"체인 1",chain_b:"체인 2",iptm_a_to_b:"체인 1→2 ipTM",iptm_b_to_a:"체인 2→1 ipTM",a:"잔기 1",b:"잔기 2",distance:"거리 (Å)",kind:"구분",pdockq2_target:"pDockQ2 표적 방향",pdockq2_binder:"pDockQ2 VHH 방향",dev_flags:"전체 평가 위험 수",dev_severe:"심한 위험 수 (5종)",n_hbonds:"N/O 근접 원자쌍",n_salt_bridges:"반대 전하 원자쌍",esm_score:"ESM 서열 prior (친화도 아님)",composite_score:"배치 상대 종합점수",n_clashes:"충돌 원자쌍",site_satisfied:"지정 결합 부위 충족",site_selection_eligible:"지정 결합 부위 포함 적격",pose_consensus_status:"동일 후보 자세 일치",pose_supporting_engines:"자세 일치 엔진 수",pose_quality_supporting_engines:"기록 적격·자세 일치 엔진 수",design_consistency_status:"설계·재예측 일치",design_binder_rmsd_A:"설계·재예측 VHH RMSD (Å)",review_rank:"검토 순위 (동률 허용)",review_rank_scope:"검토 순위 범위",review_priority_reason:"검토 판정 이유",candidate_review_priority:"후보 검토 판정",review_priority:"모델 검토 판정"});
function iptmCaption(records) {
  const rows = Array.isArray(records) ? records : records && typeof records === "object" ? [records] : [];
  const scopes = new Set(rows.map(row => ["whole_complex", "selected_target_binder_pair"].includes(row?.iptm_scope) ? row.iptm_scope : "unrecorded"));
  if (scopes.size > 1) return "ipTM · 적용 범위 혼합";
  if (scopes.has("whole_complex")) return "전체 복합체 ipTM";
  if (scopes.has("selected_target_binder_pair")) return "표적–VHH ipTM";
  return "ipTM · 적용 범위 미기록";
}
const complexMetricCaptions = Object.freeze({hotspot_contact_frac:"관심 잔기 접촉률",interface_pae_ab:"PAE 행 기준 체인·열 파트너 (Å)",interface_pae_ba:"PAE 행 파트너·열 기준 체인 (Å)",target_plddt:"기준 체인 pLDDT",binder_plddt:"파트너 pLDDT",pdockq2_target:"pDockQ2 기준 체인 방향",pdockq2_binder:"pDockQ2 파트너 방향"});
const metricCaption = (key, records, mode) => key === "iptm" ? iptmCaption(records) : (mode === "complex" ? complexMetricCaptions[key] : null) || metricCaptions[key] || key;
function refreshMetricCaptions(records, mode) {
  for (const id of ["sort-metric", "plot-x", "plot-y"])
    for (const option of $(id).options) {
      const value = option.value;
      if (metricCaptions[value]) {
        option.value = value;
        I18N.text(option, I18N.term(metricCaption(value, records, mode)));
      }
    }
}
const decisionCaption = value => typeof value === "boolean" ? value ? "적격" : "부적격" : "미평가";
const offline = Boolean(window.NANOCOFOLD_OFFLINE);
const state = {
  input: null,
  readiness: null,
  diagnostic: null,
  environment: null,
  environmentJob: null,
  environmentTarget: null,
  environmentRequest: 0,
  environmentKey: null,
  environmentLoading: false,
  environmentSubmitting: false,
  loaded: null,
  analyzed: null,
  jobs: [],
  job: null,
  logOffset: 0,
  result: null,
  rows: [],
  filtered: [],
  observations: [],
  candidate: null,
  structure: null,
  viewer: null,
  structureRequest: 0,
  resultRequest: 0,
  pollingJobs: false,
  jobsRefreshRequested: false,
  jobSelectionVersion: 0,
  jobOperations: {},
  jobActionKey: null,
  jobActionButtons: [],
  resumeBudgetMinutes: {},
  analysisSubmitting: false,
  currentView: "input",
  navigationReady: false,
  inputRevision: 0,
  inputReadinessRequest: 0,
  presets: {},
  starter: null,
  capabilities: {},
  submissionId: null,
  inputViewer: null,
  inputStructureRequest: 0,
  catalogCursor: null,
  catalogNext: null,
  catalogSort: "created",
  catalogDescending: true,
  catalogRequest: 0,
  retentionPlan: null,
};
const metricKeys = [
  "ipsae",
  "iptm",
  "interface_plddt",
  "interface_pae",
  "ptm",
  "pdockq",
  "pdockq2",
  "pdockq2_target",
  "pdockq2_binder",
  "interface_pae_ab",
  "interface_pae_ba",
  "interface_pae_median",
  "interface_pae_p90",
  "target_plddt",
  "binder_plddt",
  "complex_plddt",
  "hotspot_contact_frac",
  "n_clashes",
  "dev_severe",
  "dev_flags",
  "n_hbonds",
  "n_salt_bridges",
  "esm_score",
  "composite_score",
];
const states = {
  queued: "대기",
  running: "실행 중",
  cancelling: "취소 중",
  cancelled: "취소",
  interrupted: "중단 · 직접 재개",
  failed: "실패",
  partial_failure: "부분 실패",
  budget_exhausted: "시간 소진",
  complete: "완료",
};
const fmt = (value) =>
  value === null ||
  value === undefined ||
  (typeof value === "number" && !Number.isFinite(value))
    ? "미평가"
    : typeof value === "number"
      ? Number.isInteger(value) ? String(value) : Math.abs(value) < 0.0005 ? "0.000" : value.toFixed(3)
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
/* REPORT_SEMANTICS_START */
function recordedEvaluationError(row) {
  for (const error of [row?.error, row?.prediction_error]) {
    if (typeof error === "string" && error.trim()) return error.trim();
    if (error && typeof error !== "string") return "평가 오류 · 상세 기록 확인";
  }
  return "";
}
function evaluationErrorCaption(row) {
  const error = recordedEvaluationError(row);
  return error ? error.split(/\r?\n/).find(line => line.trim()).replace(/\s+/g, " ").slice(0, 200)
    : row?.evaluated === true ? "오류 없음" : "미평가";
}
function modelAssessmentCaption(row) {
  return recordedEvaluationError(row) ? "평가 오류 · 상세 기록 확인" : row?.evaluated === true ? "모델 평가 완료" : "모델 미평가";
}

function reportSourceIdentity(row) {
  if (typeof row.source_sequence_id === "string" && row.source_sequence_id) return row.source_sequence_id;
  const id = row.candidate_id || row.design_id || row.variant_id || row.label;
  if (typeof id !== "string" || !id) return null;
  for (const engine of [row.predictor_engine, row.engine, "colabfold", "boltz2", "chai1", "opendde"])
    if (engine && id.startsWith(engine + "__")) return id.slice(engine.length + 2);
  return id;
}
function reportModelCounts(rows, mode, config) {
  const sequences = mode === "complex" ? (config?.complex?.proteins || []).map(r => r.sequence) : rows.map(r => r.sequence);
  const complete = sequences.length > 0 && sequences.every(s => typeof s === "string" && s.trim().length);
  const models = rows.filter(r=>r.model_record!==false);
  const decisions = models.map(r => r.evaluated === true && !recordedEvaluationError(r) && typeof r.final_eligible === "boolean" ? r.final_eligible : null);
  const sourceIds = rows.map(reportSourceIdentity);
  return {
    unique_sequences: complete ? new Set(sequences).size : (mode !== "complex" && !rows.length ? 0 : null),
    source_candidates: mode === "complex" ? null : sourceIds.every(Boolean) ? new Set(sourceIds).size : complete ? new Set(sequences).size : null,
    model_total: models.length,
    prepared_record_total: rows.length,
    preprediction_record_total: rows.filter(r=>r.model_record===false).length,
    evaluated_total: models.filter(r => r.evaluated === true && !recordedEvaluationError(r)).length,
    eligible_total: mode === "complex" ? null : (!rows.length || decisions.some(v => v !== null) ? decisions.filter(v => v === true).length : null),
    eligibility_missing: mode === "complex" ? null : decisions.filter(v => v === null).length,
    evaluation_missing: models.filter(r => r.evaluated !== true || recordedEvaluationError(r)).length,
  };
}
function reportAliases(rows) {
  const aliases = Object.create(null), seen = new Map(), counters = Object.create(null);
  for (const row of rows) {
    const id = row.candidate_id;
    const origin = String(row.design_origin || row.parent_engine || row.source || row.source_sequence_id || id || "").toLowerCase();
    const prefix = origin.includes("rfantibody") || origin.startsWith("rf_") ? "RF" : origin.includes("boltzgen") || origin.startsWith("bg_") ? "BG" : "";
    const key = JSON.stringify([prefix, reportSourceIdentity(row) || id]);
    if (!seen.has(key)) {
      if (prefix) {
        counters[prefix] = (counters[prefix] || 0) + 1;
        seen.set(key, prefix + "-" + String(counters[prefix]).padStart(2, "0"));
      } else seen.set(key, reportSourceIdentity(row) || id);
    }
    aliases[id] = seen.get(key);
  }
  return aliases;
}
function reportReasonLabel(code) {
  const labels = {clash:"거리 기준 충돌", ipsae:"ipSAE 기준 미통과", iptm:"ipTM 기준 미통과", interface_plddt:"계면 pLDDT 기준 미통과", hotspot:"결합 부위 접촉 기준 미통과", developability:"개발가능성 경고 기준 미통과", developability_unassessed:"CDR 개발가능성 미평가", scoring_error:"구조 점수 계산 오류", not_scored:"구조 점수 미평가", eligibility_unassessed:"적격 판정 미기록·미평가", parent_control:"원본 후보 대조군", wt_control:"야생형 대조군", prediction_error:"예측 오류",prefilter_not_selected:"예측 전 준비·미선정",cross_binding_risk:"전장형 교차 결합 위험",incomplete_comparison:"비교 미완료",first_stage_only:"1차 평가만 완료",boltz_failed_or_incomplete:"Boltz 최종 평가 실패·미완료"};
  return Object.hasOwn(labels, code) ? labels[code] : "기록된 제외·미평가 사유";
}
function reportReasonCounts(rows, mode) {
  const counts = new Map();
  for (const row of rows) {
    const codes = new Set(Array.isArray(row.exclusion_reasons) ? row.exclusion_reasons : String(row.exclusion_reason || "").split(";").filter(Boolean));
    if (row.model_record === false) codes.add("prefilter_not_selected");
    else if (row.evaluated !== true) codes.add("not_scored");
    else if (mode !== "complex" && typeof row.final_eligible !== "boolean") codes.add("eligibility_unassessed");
    if (row.error || row.prediction_error) codes.add("prediction_error");
    for (const code of codes) counts.set(code, (counts.get(code) || 0) + 1);
  }
  return [...counts].map(([code, count]) => ({code, count, label:reportReasonLabel(code)})).sort((a,b) => b.count-a.count);
}
/* REPORT_SEMANTICS_END */
function renderReportReasons(rows, mode) {
  const reasons = reportReasonCounts(rows, mode);
  if (!reasons.length) $("exclusion-summary").replaceChildren(el("p", I18N.term("기록된 제외·미평가 사유가 없습니다. 사유 미기록만으로 판정 통과를 추정하지 않습니다.")));
  else table($("exclusion-summary"), reasons.map(r => ({이유:r.label, 기록수:r.count, 기록코드:r.code})), ["이유", "기록수", "기록코드"]);
}
const phaseLabel = (phase) => ({
  comparison_msa_search: "서열 정렬(MSA) 준비", starting: "시작 준비",
  input: "입력 준비", target: "표적 준비", prediction: "복합체 예측",
  design: "새 후보 생성", screen: "후보 비교", mature: "변이 후보 준비",
  tier2: "최종 복합체 평가", engine_comparison: "엔진별 비교",
  final: "결과·보고서 저장", complete: "완료", resume_validation: "재개 조건 확인",
  colabfold_screen: "ColabFold 예측", boltz_rescore: "Boltz-2 재평가",
}[phase] || phase || "준비");
function jobPhaseCaption(job) {
  if (job.phase === "complete" && job.state === "running") return "결과·보고서 저장 중";
  if (job.phase === "complete" && job.state === "cancelling") return "종료 정리 중";
  return phaseLabel(job.phase);
}
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) I18N.text(node, text);
  if (cls) node.className = cls;
  return node;
}
function notice(message, error = false) {
  $("notice").hidden = false;
  I18N.text($("notice"), I18N.term(message));
  $("notice").className = error ? "error" : "";
}
async function api(path, body, {timeoutMs = 0, readOnly = false} = {}) {
  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: {"X-Platform-Token": window.PLATFORM_TOKEN, "Content-Type": "application/json"},
      ...(controller ? {signal: controller.signal} : {}),
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    if (!response.ok) {
      let data;
      try { data = await response.json(); } catch { data = {error: response.statusText}; }
      const error = Error(data.error || JSON.stringify(data.detail) || response.statusText);
      error.responseRejected = true;
      throw error;
    }
    return await response.json();
  } catch (error) {
    if (error.responseRejected) throw error;
    // A missing mutation response cannot tell us whether the server accepted it.
    // Never replay a POST here; the next request is an ordinary status read.
    throw Error(body === undefined || readOnly
      ? "상태 조회 연결이 끊겼거나 응답이 지연됩니다. 마지막 표시 상태이며, 연결이 복구되면 다시 확인합니다. 화면 이동은 분석을 취소하지 않습니다."
      : "요청의 처리 여부를 확인하지 못했습니다. 작업·로그에서 새로고침으로 현재 상태를 확인한 뒤 다시 요청하세요. 자동으로 다시 요청하지 않습니다.");
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}
function action(id, fn) {
  if ($(id))
    $(id).addEventListener("click", async () => {
      const button = $(id);
      button.disabled = true;
      try {
        await fn();
      } catch (e) {
        notice(e.message, true);
      } finally {
        button.disabled = id === "diagnose-environment" ? Boolean(state.environmentJob || state.environmentSubmitting || state.environmentLoading)
          : id === "start-job" ? Boolean(state.analysisSubmitting || !state.input || !state.readiness) : false;
      }
    });
}
function options(node, items, value) {
  node.replaceChildren(
    ...items.map((item) => {
      const option = el("option", typeof item === "string" ? I18N.term(item) : (item.value === "" || item.value === "all" || node.id === "export-format") ? I18N.term(item.label) : item.label);
      option.value = typeof item === "string" ? item : item.value;
      return option;
    }),
  );
  if (value !== undefined) node.value = value;
}
function warnings(node, messages) {
  node.replaceChildren(
    ...[...new Set(messages || [])].map((text) => el("div", I18N.term(text), "warning")),
  );
}
function table(node, rows, columns) {
  if (node.classList.contains("scroll")) {node.tabIndex=0;node.setAttribute("role","region");if(!node.hasAttribute("aria-label"))I18N.attribute(node, "aria-label", I18N.term("상세 표 스크롤"));}
  const target = el("table"),
    head = el("thead"),
    body = el("tbody"),
    tr = el("tr");
  target.append(el("caption", (I18N.attributeValue(node, "aria-label") || I18N.term(columns.join(" · ")))));
  columns.forEach((key) => {const th=el("th",I18N.term(metricCaption(key, rows)));th.scope="col";tr.append(th);});
  head.append(tr);
  rows.forEach((row) => {
    const r = el("tr");
    columns.forEach((key) => r.append(el("td", row[key] == null ? I18N.term("미평가") : ["이유", "조치", "구분"].includes(key) ? I18N.term(fmt(row[key])) : fmt(row[key]))));
    body.append(r);
  });
  target.append(head, body);
  node.replaceChildren(target);
}
function rememberLocation(replace = true) {
  if (offline || !state.navigationReady) return;
  const url = new URL(window.location.href);
  url.searchParams.set("view", state.currentView);
  if (state.job) url.searchParams.set("job", state.job);
  else url.searchParams.delete("job");
  // Keep result deep links and section hashes; no credentials or input paths enter history.
  if (url.href !== window.location.href)
    window.history[replace ? "replaceState" : "pushState"](null, "", url);
}
async function restoreNavigation() {
  const query = new URLSearchParams(window.location.search);
  const selected = query.get("job");
  if (selected && state.jobs.some(job => job.id === selected) && selected !== state.job)
    await selectJob(selected, false);
  const name = query.get("view") || (query.get("result") ? "results" : "input");
  view($("view-" + name) ? name : "input", false);
}
function view(name, remember = true) {
  if (!$("view-" + name)) return;
  const changed = state.currentView !== name;
  state.currentView = name;
  if (remember && changed) rememberLocation(false);
  for (const section of document.querySelectorAll("main>section"))
    section.hidden = section.id !== "view-" + name;
  for (const button of document.querySelectorAll("[data-view]")) {
    if (button.dataset.view === name)
      button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (name === "batch") refreshBatchHistory().catch(e => notice(e.message, true));
  if (name === "jobs") refreshJobs();
  if (name === "results") refreshResults();
  if (name === "catalog") refreshCatalog().catch(e => notice(e.message, true));
}
const bytesLabel = value => value === null || value === undefined ? "미측정" :
  value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GiB` : `${(value / 1024 ** 2).toFixed(2)} MiB`;
async function refreshCatalog() {
  if (offline) return;
  const request = ++state.catalogRequest;
  const status = $("catalog-status");
  I18N.text(status, I18N.term("결과 DB를 조회하고 있습니다…"));
  const params = new URLSearchParams({limit: "25", search: $("catalog-search").value,
    retention: $("catalog-retention").value, sort: state.catalogSort, descending: String(state.catalogDescending)});
  if (state.catalogCursor) params.set("cursor", state.catalogCursor);
  const page = await api("/api/result-catalog?" + params);
  if (request !== state.catalogRequest) return;
  state.catalogNext = page.next_cursor;
  $("catalog-next").disabled = !page.next_cursor;
  const table = el("table"), head = el("thead"), headings = el("tr"), body = el("tbody");
  const columns = [["name", "분석"], ["mode", "모드"], ["created", "실행일"], ["candidate_count", "서열"],
    ["model_count", "구조 모델"], ["eligible_model_count", "기준 통과"], ["status", "계산 상태"],
    ["retention", "보관"], ["elapsed_seconds", "계산 시간"], ["storage_bytes", "서버 용량"]];
  for (const [key, label] of columns) {
    const th = el("th"), button = el("button", label + (state.catalogSort === key ? state.catalogDescending ? " ↓" : " ↑" : ""), "sort-header");
    th.scope = "col"; th.setAttribute("aria-sort", state.catalogSort === key ? state.catalogDescending ? "descending" : "ascending" : "none");
    button.onclick = () => {state.catalogDescending = state.catalogSort === key ? !state.catalogDescending : true; state.catalogSort = key; state.catalogCursor = null; refreshCatalog().catch(e => notice(e.message, true));};
    th.append(button); headings.append(th);
  }
  head.append(headings);
  for (const item of page.items) {
    const tr = el("tr");
    for (const [key] of columns) {
      const td = el("td");
      if (key === "name") {
        const link = el("a", (item.name || item.id) + (item.mock ? " · MOCK" : ""));
        link.href = "/?result=" + encodeURIComponent(item.id); link.target = "_blank"; link.rel = "noopener";
        td.append(link);
      } else I18N.text(td, key === "elapsed_seconds" ? item[key] === null || item[key] === undefined ? I18N.term("미기록") : I18N.concat([Math.round(item[key]), I18N.term("초")]) : key === "storage_bytes" ? I18N.term(bytesLabel(item[key])) : key === "created" ? new Date(item[key] * 1000).toLocaleString() : key === "retention" ? item[key] === "minimal" ? I18N.term("핵심 보관") : I18N.term("원본 보관") : key === "status" ? (states[item[key]] ? I18N.term(states[item[key]]) : item[key]) : fmt(item[key]));
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(head, body); $("catalog-table").replaceChildren(table);
  I18N.text(status, I18N.format("{0}개 표시 · 용량은 색인 시점의 측정값", [page.items.length]));
}
async function previewRetention() {
  if (!state.result || offline) return;
  const resultId = state.result.id;
  state.retentionPlan = null;
  $("retention-consent").checked = false; $("retention-apply").disabled = true;
  $("retention-confirmation").hidden = true;
  I18N.text($("retention-plan"), I18N.term("최종 구조와 보존할 파일을 검증하고 용량을 계산하고 있습니다… 파일 수에 따라 시간이 걸립니다."));
  const plan = await api(`/api/results/${encodeURIComponent(resultId)}/retention`);
  if (state.result?.id !== resultId) return;
  state.retentionPlan = {resultId, plan};
  const explanation = el("p", I18N.format("현재 {0} → 보관 예상 {1} · 순 절감 예상 {2} · 최종 구조 {3}개", [I18N.term(bytesLabel(plan.current_bytes)), I18N.term(bytesLabel(plan.kept_bytes)), I18N.term(bytesLabel(plan.estimated_net_reclaimed_bytes)), plan.preserved_model_count]));
  const files = el("details"), summary = el("summary", I18N.format("삭제 예정 파일 {0}개 · 전체 목록 확인", [plan.deletable_files.length])), listing = el("pre", plan.deletable_files.map(item => `${item.path} · ${bytesLabel(item.size)}`).join("\n"));
  files.append(summary, listing); $("retention-plan").replaceChildren(explanation, files);
  $("retention-confirmation").hidden = !plan.deletable_files.length;
}
async function applyRetention() {
  const reviewed = state.retentionPlan;
  if (!reviewed || reviewed.resultId !== state.result?.id || !$("retention-consent").checked)
    throw Error("현재 결과의 정리 계획을 확인하고 파일 삭제에 동의하세요.");
  const result = await api(`/api/results/${encodeURIComponent(reviewed.resultId)}/retention`, {plan_digest: reviewed.plan.plan_digest, confirmation: true});
  $("retention-confirmation").hidden = true;
  state.retentionPlan = null;
  await loadResult(reviewed.resultId);
  notice(result.state === "flushed" ? `핵심 보관 완료 · 삭제한 파일 ${bytesLabel(result.deleted_bytes)}. 지표·서열·최종 구조는 유지되었습니다.` : `보관본은 저장했으나 파일 정리는 일부 남았습니다: ${result.error || result.state}`, result.state !== "flushed");
  await refreshResults();
}
function merge(base, extra) {
  const out = structuredClone(base || {});
  for (const [key, val] of Object.entries(extra || {}))
    out[key] =
      val && typeof val === "object" && !Array.isArray(val)
        ? merge(out[key], val)
        : val;
  return out;
}
function inputSettings(forPlan = false) {
  let advanced;
  try {
    advanced = JSON.parse($("advanced").value || "{}");
  } catch {
    throw Error("고급 설정 JSON 형식을 확인하세요.");
  }
  if (!advanced || typeof advanced !== "object" || Array.isArray(advanced))
    throw Error("고급 설정은 객체여야 합니다.");
  let cfg = merge($("analysis-preset").value === "small-four-v1"
    ? state.starter?.modes[$("mode").value]?.native_override || {}
    : state.presets[$("analysis-preset").value] || {}, advanced);
  const simulated = $("mock").checked && $("mode").value !== "complex";
  if ($("analysis-preset").value === "small-four-v1") {
    cfg.screen.colabfold.msa_source = $("novice-msa").value;
    cfg.screen.allow_public_msa_server = $("novice-consent").checked;
    cfg.screen.comparison.engines = [...document.querySelectorAll('[data-novice-engine]:checked')].map(i => i.value);
    if (!cfg.screen.comparison.engines.length) throw Error("평가 엔진을 하나 이상 선택하세요.");
    if (!forPlan && !simulated && cfg.screen.colabfold.msa_source === "public_api" && !cfg.screen.allow_public_msa_server)
      throw Error("현재 입력의 단백질 서열 전송 동의가 필요합니다.");
  }
  if (simulated) {
    // CPU demonstrations create synthetic data and never acquire an MSA.
    cfg = merge(cfg, {structure:{msa:{backend:"empty"}},screen:{msa_mode:"single",allow_public_msa_server:false,colabfold:{msa_source:"cache"}}});
  }
  const publicMsa = !simulated && (cfg.complex?.msa_source === "public_api" || cfg.screen?.colabfold?.msa_source === "public_api" || cfg.screen?.msa_mode === "msa" || cfg.structure?.msa?.backend === "public_api");
  if (!forPlan && publicMsa && !$("novice-consent").checked) throw Error("가져온 설정도 현재 입력에 대한 외부 MSA 전송 동의를 다시 확인해야 합니다.");
  if (publicMsa) cfg.screen = merge(cfg.screen, {allow_public_msa_server: true});
  for (const id of ["candidate-limit", "design-count"]) {
    if (id === "design-count" && $("mode").value !== "design") continue;
    if (id === "candidate-limit" && $("mode").value === "screen") continue;
    if (!$(id).value) continue;
    const value = $(id).valueAsNumber;
    if (!Number.isSafeInteger(value) || value < 1)
      throw Error("후보 상한과 백본 수는 1 이상의 정수여야 합니다.");
    cfg = merge(cfg, id === "design-count" ? { design: { num_designs: value } } : {
      design: { filters: { top_n_to_score: value } },
      screen: { tier2_n: value },
      mature: { prefilter: { top_n: value } },
    });
  }
  return merge(cfg, {
    run: {
      mode: $("mode").value,
      name: $("job-name").value,
      seed: Number($("seed").value),
      mock: $("mock").checked,
      analysis_preset: $("analysis-preset").value,
    },
    screen: cfg.screen?.comparison?.enabled ? {} : {
      engine: $("screen-engine").value,
      env: "envs/" + $("screen-engine").value,
    },
  });
}
function noviceComplexSettings(cfg) {
  const current = cfg.complex || {}, small = $("analysis-preset").value === "small-four-v1";
  const engines = small ? cfg.screen?.comparison?.engines : current.comparison_engines;
  if (!engines) return {...current, engine: current.engine || $("screen-engine").value,
    seeds: current.seeds || [Number($("seed").value)]};
  const source = small ? cfg.screen?.colabfold?.msa_source : current.msa_source ?? cfg.screen?.colabfold?.msa_source;
  if (!source) throw Error("복합체 비교의 MSA 준비 방식을 지정하세요. 자동으로 다른 방식을 선택하지 않습니다.");
  const preserved = {...current};
  delete preserved.engine;
  return {...preserved, comparison_engines: engines, seeds: current.seeds || [Number($("seed").value)], msa: "precomputed",
    msa_source: source, allow_public_msa_server: source === "public_api" && $("novice-consent").checked,
    models_per_seed: current.models_per_seed ?? 1, recycles: current.recycles ?? 3,
    diffusion_steps: current.diffusion_steps ?? 200};
}
function noviceModelPlan(cfg, mode, libraryCount) {
  if (mode === "complex") {
    const settings = cfg.complex?.proteins ? cfg.complex : noviceComplexSettings(cfg);
    return `계획 모델 ${(settings.comparison_engines || [settings.engine]).length * settings.seeds.length * settings.models_per_seed}개 · 실제 성공·실패·미평가는 결과에서 구분합니다.`;
  }
  const screen = cfg.screen || {}, design = cfg.design || {}, mature = cfg.mature || {};
  const engines = screen.comparison?.enabled ? screen.comparison.engines?.length : 1;
  if (!engines) return "평가 엔진을 선택하세요.";
  if (screen.cascade?.enabled || design.refinement?.enabled)
    return "다단계 선별 경로입니다. 전체 입력·생성 수와 단계별 확정 평가 수를 실행 기록에서 구분합니다.";
  if (mode === "screen") return `입력 VHH 전체 ${libraryCount}개 × 엔진 ${engines}개 = 계획 ${libraryCount * engines}모델 · 후보를 자동 생략하지 않습니다.`;
  if (mode === "design") {
    if (!Number.isInteger(design.num_designs)) return "설정 검토 후 생성 후보·평가 모델의 계획 상한을 확인하세요.";
    const selected = design.engine === "both" ? ["rfantibody", "boltzgen"] : [design.engine];
    let candidates = 0;
    for (const engine of selected) {
      const sequences = engine === "rfantibody" ? design.mpnn?.seqs_per_struct : design.boltzgen?.inverse_fold_num_sequences;
      const generated = design.num_designs * (sequences ?? 1);
      candidates += engine === "rfantibody" && Number.isInteger(design.filters?.top_n_to_score)
        ? Math.min(generated, design.filters.top_n_to_score) : generated;
    }
    return `최대 계획 후보 ${candidates}개 × 엔진 ${engines}개 = 최대 ${candidates * engines}모델 · 실제 생성·선별 이후 확정 수를 기록합니다.`;
  }
  const top = mature.prefilter?.top_n, variants = mature.library?.max_variants;
  if (!Number.isInteger(top)) return "설정 검토 후 치환 후보와 WT의 계획 평가 상한을 확인하세요.";
  const selected = Number.isInteger(variants) ? Math.min(top, variants) : top;
  return `${Number.isInteger(variants) ? `변이 최대 ${variants}개 준비 · ` : ""}최대 ${selected}개와 WT × 엔진 ${engines}개 = 최대 ${(selected + 1) * engines}모델 · 실제 선별 수는 실행 후 확정됩니다.`;
}
function fields() {
  const mode = $("mode").value;
  for (const [id, visible] of [["candidate-limit", mode !== "screen"], ["design-count", mode === "design"]]) {
    $(id).hidden = !visible;
    document.querySelector(`label[for="${id}"]`).hidden = !visible;
  }
  $("library-fields").hidden = mode !== "screen";
  $("wt-fields").hidden = mode !== "mature";
  $("fasta-fields").hidden = $("input-kind").value !== "fasta";
  $("structure-fields").hidden = $("input-kind").value !== "structure";
  state.input = null;
  $("review-output").hidden = true;
  syncAlternativeInputs();
}
// Alternative sources feed the same canonical input; keep the other raw draft for recovery.
let structureInputSource = null;
const alternativeInputDrafts = new Map(), openFastaImports = new Set();
function syncAlternativeInputs() {
  const selected = structureInputSource || ($("structure-file").files?.[0] ? "file" : $("pdb-id").value.trim() ? "pdb" : null);
  $("structure-file-option").hidden = selected === "pdb";
  $("structure-pdb-option").hidden = selected === "file";
  $("structure-file").disabled = selected === "pdb";
  for (const id of ["pdb-id", "assembly-id"]) $(id).disabled = selected === "file";
  $("structure-input-choice").hidden = !selected;
  I18N.text($("structure-input-help"), selected === "file" ? I18N.term("구조 파일을 사용합니다. PDB ID를 추가할 필요가 없습니다.") : I18N.term("PDB ID를 사용합니다. 구조 파일을 추가할 필요가 없습니다."));
  for (const [file, text] of [["vhh-file", "vhh-fasta"], ["wt-file", "wt-fasta"]]) {
    const entered = !!$(text).value.trim(), collapsed = entered && !openFastaImports.has(file);
    $(file).hidden = collapsed; $(file).disabled = collapsed;
    $(file + "-choice").hidden = !entered && !alternativeInputDrafts.has(text);
    I18N.text($(file + "-help"), entered ? I18N.term("아래 FASTA가 분석 입력입니다. 파일을 추가할 필요가 없습니다.") : I18N.term("FASTA가 비어 있습니다. 직접 입력하거나 이전 FASTA를 복원하세요."));
    $(file + "-change").hidden = !collapsed;
    $(file + "-restore").hidden = !alternativeInputDrafts.has(text);
  }
}
function chooseStructureInput(source) {
  structureInputSource = source;
  clearInputStructure();
  const consent = $("novice-consent"); if (consent) consent.checked = false;
  syncAlternativeInputs();
}
async function importCanonicalFasta(fileId, textId) {
  const file = $(fileId).files?.[0]; if (!file) return;
  const revision = state.inputRevision, before = $(textId).value;
  const content = await file.text();
  if (revision !== state.inputRevision || before !== $(textId).value)
    throw Error("파일을 읽는 동안 입력이 변경되었습니다. 현재 FASTA를 유지합니다.");
  alternativeInputDrafts.set(textId, before);
  $(textId).value = content; openFastaImports.delete(fileId);
  $(textId).dispatchEvent(new Event("input", { bubbles: true }));
  syncAlternativeInputs();
}
function initAlternativeInputs() {
  $("structure-file").addEventListener("change", () => chooseStructureInput($("structure-file").files?.[0] ? "file" : null));
  $("pdb-id").addEventListener("input", () => chooseStructureInput($("pdb-id").value.trim() ? "pdb" : null));
  $("change-structure-input").onclick = () => {
    const current = structureInputSource || ($("structure-file").files?.[0] ? "file" : "pdb");
    chooseStructureInput(current === "file" ? "pdb" : "file");
    $(current === "file" ? "pdb-id" : "structure-file").focus();
  };
  for (const [file, text] of [["vhh-file", "vhh-fasta"], ["wt-file", "wt-fasta"]]) {
    $(text).addEventListener("input", syncAlternativeInputs);
    $(file).onchange = () => importCanonicalFasta(file, text).catch(e => notice(e.message, true));
    $(file + "-change").onclick = () => {
      // Clear only the picker so choosing the same file emits change again.
      // Canonical FASTA and its recovery draft remain intact until the read completes.
      $(file).value = "";
      openFastaImports.add(file); syncAlternativeInputs(); $(file).focus();
    };
    $(file + "-restore").onclick = () => {
      const current = $(text).value; $(text).value = alternativeInputDrafts.get(text);
      alternativeInputDrafts.set(text, current); openFastaImports.delete(file);
      $(text).dispatchEvent(new Event("input", { bubbles: true })); syncAlternativeInputs(); $(text).focus();
    };
  }
  syncAlternativeInputs();
}
function invalidateInput() {
  state.inputRevision++;
  state.input = null;
  state.readiness = null;
  state.submissionId = null;
  $("review-output").hidden = true;
  $("start-job").disabled = true;
}
function chainLabel(chain) {
  const author = chain.author_chain && chain.author_chain !== chain.original_id
    ? ` · author ${chain.author_chain}` : "";
  return `${chain.id} · 원본 ${chain.original_id}${author} · ${chain.name || "단백질"} · ${chain.length} aa`;
}
function selectedPartners() {
  return [...$("partner-chains").querySelectorAll("input:checked")].map((i) => i.value);
}
function resetChainReview() {
  invalidateInput();
  state.analyzed = null;
  $("hotspot-review").hidden = true;
  $("acknowledge").checked = false;
}
function predictionFields() {
  const predict = $("input-preparation").value === "sequence_prediction";
  $("prediction-fields").hidden = !predict;
  $("reference-hotspot-fields").hidden = predict && $("prediction-hotspots").value === "auto";
}
function renderInputAssessment() {
  const data = state.analyzed;
  const root = $("input-assessment");
  root.replaceChildren(el("p", data.blockers?.length ? I18N.term("실험 좌표 경로에 확인할 문제가 있습니다. 전체 서열을 검토해 새 구조를 예측할 수 있습니다.") : I18N.term("실험 좌표 경로를 사용할 수 있습니다. 관측 서열과 전체 서열의 차이를 확인하세요.")));
  table(root.appendChild(el("div", undefined, "scroll")), data.retained_chains.map(c => ({
    체인: chainLabel(c), 관측: c.length, 전체: c.sequence_review?.full_length ?? "정보 없음",
    미관측: c.sequence_review?.missing_positions?.length ?? "대응 미확인",
    수정잔기: c.sequence_review?.modifications?.length ?? 0,
    불명확: c.sequence_review?.unknown_positions?.join(", ") || "없음",
  })), ["체인", "관측", "전체", "미관측", "수정잔기", "불명확"]);
  $("input-preparation").value = data.preparation?.recommended || "coordinates";
  $("prediction-sequences").value = data.retained_chains.map(c =>
    `>${c.id} ${c.name || "protein"}\n${c.full_sequence || ""}`).join("\n");
  $("prediction-hotspots").value = "auto";
  predictionFields();
}
function syncChainSelection() {
  const target = $("target-chain").value;
  for (const checkbox of $("partner-chains").querySelectorAll("input")) {
    checkbox.disabled = checkbox.value === target;
    if (checkbox.disabled) checkbox.checked = false;
  }
  const partners = selectedPartners();
  $("chain-selection").replaceChildren(
    el("span", I18N.format("표적: {0}", [(target ? target : I18N.term("선택 필요"))]), "chain-target"),
    document.createTextNode(" · "),
    el("span", I18N.format("파트너: {0}", [(partners.join(", ") ? partners.join(", ") : I18N.term("없음"))]), "chain-partner"),
    el("span", I18N.term(" · 회색 체인은 분석에서 제외")),
  );
  const viewer = state.inputViewer;
  viewer?.select(target, partners).catch((error) => {
    if (viewer !== state.inputViewer) return;
    I18N.text($("input-viewer-status"), I18N.format("구조 표시 오류: {0}. 아래 목록에서 체인을 선택하세요.", [error.message]));
  });
}
function pickInputChain(id, role) {
  if (!state.loaded?.chains.some((c) => c.id === id)) return;
  const target = $("target-chain");
  if (role === "target") {
    if (target.value === id) return;
    target.value = id;
  } else {
    if (target.value === id) {
      I18N.text($("input-viewer-status"), I18N.term("표적 체인은 파트너로 중복 선택할 수 없습니다."));
      return;
    }
    const checkbox = [...$("partner-chains").querySelectorAll("input")].find((c) => c.value === id);
    checkbox.checked = !checkbox.checked;
  }
  resetChainReview();
  syncChainSelection();
  I18N.text($("input-viewer-status"), I18N.term("선택한 체인으로 ‘체인·노출도 검토’를 진행하세요."));
}
function clearInputStructure() {
  state.inputStructureRequest++;
  state.inputViewer?.dispose();
  state.inputViewer = null;
  state.loaded = null;
  $("input-structure-view").replaceChildren();
  I18N.text($("input-chain-hover"), "");
  $("chain-review").hidden = true;
  resetChainReview();
}
async function showInputStructure(loaded, request) {
  I18N.text($("input-viewer-status"), I18N.term("MolStar 구조를 준비하고 있습니다…"));
  try {
    const response = await fetch(`/api/inputs/${encodeURIComponent(loaded.input_id)}/structure`, {
      headers: { "X-Platform-Token": window.PLATFORM_TOKEN },
    });
    if (!response.ok) throw Error("구조 좌표를 읽지 못했습니다.");
    const pdb = await response.text();
    if (request !== state.inputStructureRequest) return;
    // Use an isolated host so an obsolete async viewer cannot replace a new one.
    const host = el("div");
    host.style.cssText = "position:absolute;inset:0";
    $("input-structure-view").replaceChildren(host);
    const viewer = await NanoCoFoldChainViewer.create(host, pdb, loaded.chains,
      (id, role) => {
        if (request === state.inputStructureRequest) pickInputChain(id, role);
      },
      (location) => {
        if (request !== state.inputStructureRequest) return;
        I18N.text($("input-chain-hover"), location ? I18N.format("{0} · 원본 잔기 {1} (서열 위치 {2})", [chainLabel(location.chain), (location.residue?.original_label ? location.residue?.original_label : location.position), location.position]) : "");
      });
    if (request !== state.inputStructureRequest) { viewer.dispose(); return; }
    state.inputViewer = viewer;
    await viewer.select($("target-chain").value, selectedPartners());
    I18N.text($("input-viewer-status"), I18N.term("초록: 표적 · 파랑: 파트너 · 회색: 제외. Ctrl+클릭으로 진행할 체인을 선택하세요."));
  } catch (error) {
    if (request !== state.inputStructureRequest) return;
    I18N.text($("input-viewer-status"), I18N.format("MolStar를 열지 못했습니다: {0}. 아래 목록에서 체인을 선택할 수 있습니다.", [error.message]));
  }
}
function regionSettings() {
  const region = {};
  for (const [id, key] of [["region-start", "start"], ["region-end", "end"]]) {
    if (!$(id).value) continue;
    const value = $(id).valueAsNumber;
    if (!Number.isSafeInteger(value) || value < 1) throw Error("분석 구간은 1 이상의 정수여야 합니다.");
    region[key] = value;
  }
  const exclusions = $("hotspot-exclusions").value.split(/\n/).filter(line => line.trim()).map(line => {
    const match = line.trim().match(/^(\d+)\s*[-–:]\s*(\d+)\s*(.*)$/);
    if (!match) throw Error("결합 부위 제외 구간은 한 줄에 시작-끝 이름으로 입력하세요.");
    return {start: Number(match[1]), end: Number(match[2]), label: match[3] || "사용자 제외 구간"};
  });
  return {target_region: region, hotspot_exclusions: exclusions};
}
function etaText(job) {
  const eta = job.eta;
  if (job.mock || job.state !== "running" || !eta) return "";
  if (eta.status === "awaiting_candidate_pool") return "복합체 예측 시간: 후보 목록이 확정되면 실측을 시작합니다.";
  if (eta.status === "complete") return `예측 단계 ${eta.completed}/${eta.total} 처리 완료 · 작업은 실행 중이며 결과 저장·후처리는 남아 있을 수 있습니다.`;
  if (eta.status !== "estimated") return "복합체 예측 시간: 첫 실제 완료 후보를 기다리는 중";
  if (eta.remaining_seconds === 0) return `복합체 예측 ${eta.completed}/${eta.total} 완료 · 후처리 시간은 추정에 포함하지 않습니다.`;
  const minutes = seconds => Math.max(1, Math.ceil(seconds / 60));
  return `복합체 예측 ${eta.completed}/${eta.total} · 남은 예상 ${minutes(eta.lower_seconds)}–${minutes(eta.upper_seconds)}분 · 같은 실행의 ${eta.sample_count}개 실측값 (${eta.seconds_per_candidate}초/후보). 준비·후처리·대기는 제외한 참고 범위입니다.`;
}
async function prepareInput() {
  const revision = state.inputRevision,
    cfg = inputSettings(),
    payload = {
      mode: $("mode").value,
      mock: $("mock").checked,
      config: cfg,
      ...regionSettings(),
      vhh_fasta: $("mode").value === "screen" ? $("vhh-fasta").value : "",
      wt_fasta: $("mode").value === "mature" ? $("wt-fasta").value : "",
    };
  const wt = $("wt-structure").files[0];
  if (wt && $("mode").value === "mature")
    payload.wt_structure = { filename: wt.name, content: await wt.text() };
  let result;
  if ($("mode").value === "complex") {
    const proteins = noviceComplexProteins();
    const comparison = $("analysis-preset").value === "small-four-v1" || Array.isArray(cfg.complex?.comparison_engines);
    if (comparison && state.capabilities.complex_comparison !== true)
      throw Error("현재 서버는 일반 복합체 네 엔진 비교를 지원하지 않습니다. 새 기능 패키지로 업데이트하세요.");
    result = await api("/api/inputs/complex", {proteins, mock: false, name: $("job-name").value,
      settings: noviceComplexSettings(cfg)});
  } else if ($("input-kind").value === "fasta") {
    result = await api("/api/inputs", {
      ...payload,
      kind: "fasta",
      content: $("target-fasta").value,
      provider: $("provider").value,
      name: $("job-name").value,
    });
  } else {
    if (!state.analyzed)
      throw Error("구조를 불러오고 체인·노출도를 검토하세요.");
    if (!$("acknowledge").checked)
      throw Error("검토한 체인·결합 부위와 경고를 확인해 주세요.");
    const useReference = $("input-preparation").value === "coordinates" || $("prediction-hotspots").value === "reference";
    const hotspots = useReference ? $("hotspots")
      .value.split(/[ ,;]+/)
      .filter(Boolean)
      .map(Number) : [];
    if (hotspots.some((v) => !Number.isInteger(v)))
      throw Error("결합 부위에는 정수 위치를 입력하세요.");
    result = await api("/api/inputs/review", {
      ...payload,
      selection_id: state.analyzed.selection_id,
      hotspots,
      acknowledge: true,
      preparation: $("input-preparation").value,
      provider: $("prediction-provider").value,
      prediction_sequences: $("prediction-sequences").value,
      prediction_hotspots: $("prediction-hotspots").value,
    });
  }
  if (revision !== state.inputRevision)
    throw Error("검토 중 입력이 변경되었습니다. 적용 설정을 다시 확인하세요.");
  state.input = result;
  state.submissionId = crypto.randomUUID().replaceAll("-", "");
  state.readiness = Boolean(result.config.run.mock);
  state.diagnostic = null;
  $("start-job").disabled = !state.readiness;
  I18N.text($("resolved-config"), JSON.stringify(result.config, null, 2));
  if ($("novice-reviewed-summary")) {
    const cfg=result.config, mode=cfg.run.mode, complex=mode==="complex", summary=$("novice-reviewed-summary");
    const engines=complex?cfg.complex.comparison_engines||[cfg.complex.engine]:cfg.screen.comparison?.enabled?cfg.screen.comparison.engines:[cfg.screen.engine];
    const entries=complex?result.review.proteins:[{id:"A",sequence:cfg.target.sequence}];
    summary.replaceChildren(el("h3",I18N.term("이번에 실행할 입력과 조건")),
      el("p",`${mode} · ${entries.map(p=>`${p.id}: ${p.sequence.length} aa`).join(" · ")} · Seed ${cfg.run.seed}`),
      el("p",I18N.format("평가 엔진: {0} · 엔진 점수를 합산하지 않습니다.", [engines.join(" · ")])),
      el("p",I18N.format("MSA: {0} · 공용 전송 동의: {1}", [complex ? (cfg.complex.msa_source ? cfg.complex.msa_source : cfg.complex.msa) : (cfg.screen.colabfold?.msa_source ? cfg.screen.colabfold?.msa_source : cfg.screen.msa_mode), cfg.screen.allow_public_msa_server===true ? I18N.term("현재 입력에 동의") : I18N.term("외부 전송 없음")])),
      el("p",I18N.term(noviceModelPlan(cfg,mode,mode==="screen"?noviceFasta($("vhh-fasta").value).length:0))));
  }
  warnings($("review-summary"), result.review?.warnings);
  $("review-output").hidden = false;
  if (state.readiness) {
    renderInputReadiness();
    notice("CPU 데모 입력을 확인했습니다. 분석 실행을 선택하세요.");
  } else {
    I18N.text($("input-next-step"), I18N.term("저장된 환경 확인 기록을 확인합니다."));
    await readInputReadiness(result, revision);
    if (revision === state.inputRevision && state.input?.id === result.id)
      notice(state.readiness ? "입력 확인 완료 · 분석 실행을 선택하세요." : "선택한 엔진의 환경 확인이 필요합니다.");
  }
  return result;
}
function renderInputReadiness() {
  $("start-job").disabled = Boolean(state.analysisSubmitting || !state.input || !state.readiness);
  $("diagnose-input").hidden = Boolean(state.readiness);
  I18N.text($("input-next-step"), I18N.term(state.readiness
    ? "입력 확인 완료 · 아래 분석 실행을 누르세요. 아직 구조 예측은 시작하지 않았습니다."
    : "입력은 확인했습니다. 선택한 엔진의 환경 확인 기록이 필요합니다. 환경 확인 후 이 화면으로 돌아오세요."));
}
async function readInputReadiness(input, revision = state.inputRevision) {
  const request = ++state.inputReadinessRequest;
  const result = await api("/api/environment/status", {input_id: input.id, mode: input.config.run.mode}, {timeoutMs: 15000, readOnly: true});
  if (request !== state.inputReadinessRequest || revision !== state.inputRevision || state.input?.id !== input.id) return;
  state.readiness = result.ready === true;
  renderInputReadiness();
}
function environmentPayload() {
  if (state.environmentTarget) return state.environmentTarget;
  const mode = $("environment-mode").value;
  return {mode, config: state.starter.modes[mode].native_override};
}
function renderEnvironmentStatus() {
  const waiting = Boolean(state.environmentJob || state.environmentSubmitting), ready = state.environment?.ready === true;
  I18N.text($("environment-context"), I18N.term(state.environmentTarget ? "현재 입력에서 선택한 엔진을 확인합니다." : "작은 시작 설정의 기본 엔진을 확인합니다."));
  I18N.text($("environment-status"), I18N.term(waiting
    ? "환경을 확인하고 있습니다. 이 화면에서 기다려 주세요. 구조 예측은 실행하지 않습니다."
    : state.environmentLoading ? "환경 확인 기록을 불러오고 있습니다."
    : ready ? "환경 확인 완료 · 분석 준비하기를 누르세요."
    : state.environment?.reason === "status_unavailable" ? "환경 확인 기록을 불러오지 못했습니다. 잠시 후 다시 조회합니다. 기존 결과는 계속 열 수 있습니다."
    : state.environment?.reason === "check_not_passed" ? "환경 확인 미충족 · 세부 항목에서 필요한 설치를 확인하세요."
    : "이 설정의 환경 확인 기록이 없습니다. 처음 한 번 환경 확인을 시작하세요."));
  $("diagnose-environment").disabled = waiting || state.environmentLoading;
  $("environment-mode").disabled = state.environmentSubmitting;
  I18N.text($("diagnose-environment"), I18N.term(ready ? "환경 다시 확인" : "환경 확인 시작"));
  if (state.environment?.diagnostics) renderDiagnostics(state.environment.diagnostics);
  else $("diagnostics-output").replaceChildren();
}
async function refreshEnvironmentStatus() {
  // A submission is followed by its own fresh lookup. An older lookup must not
  // restore a previous successful receipt while a new check is being submitted.
  if (state.environmentSubmitting) return;
  const request = ++state.environmentRequest, payload = environmentPayload();
  const key = JSON.stringify(payload);
  if (key !== state.environmentKey) {
    state.environmentKey = key;
    state.environment = null;
    state.environmentJob = null;
  }
  state.environmentLoading = true;
  renderEnvironmentStatus();
  const current = () => request === state.environmentRequest && key === JSON.stringify(environmentPayload());
  try {
    const status = await api("/api/environment/status", payload, {timeoutMs: 15000, readOnly: true});
    if (!current()) return;
    state.environment = status;
    state.environmentJob = ["queued", "running", "cancelling"].includes(status.state) ? status.job_id : null;
  } catch (error) {
    if (!current()) return;
    state.environment = {ready: false, reason: "status_unavailable"};
    state.environmentJob = null;
    notice(error.message, true);
  } finally {
    if (current()) {
      state.environmentLoading = false;
      renderEnvironmentStatus();
    }
  }
}
async function openEnvironment(current = false) {
  state.environmentTarget = current && state.input
    ? {mode: state.input.config.run.mode, config: state.input.config} : null;
  $("environment-mode").value = state.environmentTarget?.mode || $("mode").value;
  view("environment");
  await refreshEnvironmentStatus();
}
async function submitDiagnostic() {
  if (state.environmentJob || state.environmentSubmitting) return;
  const payload = environmentPayload();
  ++state.environmentRequest;
  state.environmentLoading = false;
  state.environmentSubmitting = true;
  state.environment = null;
  if (state.input && !state.input.config.run.mock) {
    ++state.inputReadinessRequest;
    state.readiness = false;
    renderInputReadiness();
  }
  renderEnvironmentStatus();
  try {
    await api("/api/environment/check", payload);
  } finally {
    state.environmentSubmitting = false;
    // Query the currently selected purpose, even if navigation changed it while
    // submission was pending. Never attach a different purpose's returned job.
    await refreshEnvironmentStatus();
    if (state.input && !state.input.config.run.mock) await readInputReadiness(state.input);
  }
  // Stay on the separate environment page. Input review never submits this job.
}
async function selectJob(identifier, remember = true) {
  state.jobSelectionVersion = (state.jobSelectionVersion || 0) + 1;
  state.job = identifier;
  state.jobActionKey = null;
  if (remember) rememberLocation();
  state.logOffset = 0;
  I18N.text($("job-title"), I18N.term("작업 불러오는 중"));
  $("job-detail").replaceChildren(el("p", I18N.term("작업 상태를 확인합니다.")));
  $("job-actions").replaceChildren();
  I18N.text($("job-log"), "");
  if (state.pollingJobs) state.jobsRefreshRequested = true;
  await refreshJobs();
}
function jobProgressCaption(job) {
  const progress = job.progress || {};
  const count = value => Number.isInteger(value) && value >= 0 ? String(value) : "미기록";
  const counts = `${count(progress.completed)}/${count(progress.total)}`;
  const attempted = Object.hasOwn(progress, "succeeded") || Object.hasOwn(progress, "failed");
  // Structured model progress takes precedence, including partial/unknown outcomes.
  if (attempted)
    return `처리 ${counts} · 성공 ${count(progress.succeeded)} · 실패 ${count(progress.failed)}`;
  const terminalNative = job.kind === "analysis" && job.mock === false &&
    ["screen", "mature", "design"].includes(job.mode) &&
    ["complete", "partial_failure"].includes(job.state);
  if (terminalNative) {
    const summary = job.summary;
    const recorded = value => Number.isSafeInteger(value) && value >= 0;
    if (summary && typeof summary === "object" && !Array.isArray(summary)) {
      const models = summary.model_record_total;
      const succeeded = summary.scored_total;
      const failed = summary.failed_candidates;
      const expected = summary.expected_total;
      const hasExpected = Object.hasOwn(summary, "expected_total") && expected !== null;
      const total = hasExpected ? expected : models;
      const contextMatches = (!Object.hasOwn(summary, "mode") || summary.mode === job.mode) &&
        (!Object.hasOwn(summary, "mock") || summary.mock === false);
      if (contextMatches && recorded(models) && recorded(succeeded) && recorded(failed) &&
          recorded(total) && succeeded <= models && failed <= models &&
          succeeded + failed === models && total >= models &&
          (job.state !== "complete" || (failed === 0 && total === models)))
        return `처리 ${models}/${total} · 성공 ${succeeded} · 실패 ${failed}`;
    }
    // Candidate/preparation totals are not model outcomes; keep explicit stage progress.
    return `단계 진행 ${counts}`;
  }
  return `진행 ${counts}`;
}
async function refreshJobs() {
  if (offline || document.hidden || !$("job-list") || state.pollingJobs) return;
  state.pollingJobs = true;
  try {
    state.jobs = (await api("/api/jobs", undefined, {timeoutMs: 15000})).jobs;
    if (state.environmentJob) {
      const checked = state.jobs.find(j => j.id === state.environmentJob);
      if (checked && !["queued", "running", "cancelling"].includes(checked.state)) {
        state.environmentJob = null;
        await refreshEnvironmentStatus();
        if (state.input && !state.input.config.run.mock) await readInputReadiness(state.input);
      }
    }
    if (state.environment?.reason === "status_unavailable") await refreshEnvironmentStatus();
    const jobButton = job => {
      const type = job.kind === "analysis" ? "분석" : job.kind === "diagnostics" ? "환경 확인" : "프로그램 설치";
      const outcome = job.kind === "diagnostics" && job.state === "complete"
        ? job.diagnostics?.ok === true ? "환경 확인 통과" : "환경 확인 미충족"
        : states[job.state] || job.state;
      const button = el("button", I18N.concat([I18N.term(type), " · ", job.kind === "analysis" ? job.name : job.mode,
        " · ", I18N.term(outcome)]), "job-row" + (job.id === state.job ? " active" : ""));
      button.onclick = () => selectJob(job.id);
      return button;
    };
    $("job-list").replaceChildren(...state.jobs.filter(j => j.kind === "analysis").map(jobButton));
    const history = state.jobs.filter(j => j.kind !== "analysis");
    if (history.length) {
      const details = el("details", null, "job-history");
      details.open = !state.jobs.some(j => j.kind === "analysis") || history.some(j => j.id === state.job);
      details.append(el("summary", I18N.term("환경 확인·설치 기록")), ...history.map(jobButton));
      $("job-list").append(details);
    }
    if (!state.jobs.length)
      I18N.text($("job-list"), I18N.term("아직 작업이 없습니다. 입력 탭에서 CPU 데모를 시작해 보세요."));
    if (!state.job && state.jobs.length) {
      state.job = (state.jobs.find(j => j.kind === "analysis") || state.jobs[0]).id;
      state.logOffset = 0;
    }
    if (state.job) {
      const chosen = state.job, selection = state.jobSelectionVersion;
      const job = await api("/api/jobs/" + chosen, undefined, {timeoutMs: 15000});
      if (state.job !== chosen || state.jobSelectionVersion !== selection) return;
      I18N.text($("job-title"), job.name);
      if (state.submissionNoticeJob === job.id && !["queued","running","cancelling"].includes(job.state)) {
        notice(`${states[job.state] || job.state} · ${job.result_id?"결과 열기로 결과를 확인하세요.":"작업 상세와 로그에서 종료 사유를 확인하세요."}`,job.state!=="complete");
        state.submissionNoticeJob=null;
      }
      $("job-detail").replaceChildren(
        el(
          "p",
          I18N.format("{0} · {1} · {2} · {3}초", [(states[job.state] ? I18N.term(states[job.state]) : job.state), I18N.term(jobPhaseCaption(job)), I18N.term(jobProgressCaption(job)), Math.round(job.elapsed_seconds || 0)]),
        ),
      );
      if (job.reused_result_id) $("job-detail").append(el("p", I18N.format("저장 결과 연결 · 새 계산 0초 · 원본 {0}{1}. 원래 계산 시간 {2}", [job.reused_result_id, job.reuse_kind === "view_only" ? I18N.term(" · 핵심 보관본") : "", job.source_elapsed_seconds === null ? I18N.term("미기록") : I18N.concat([Math.round(job.source_elapsed_seconds || 0), I18N.term("초")])])));
      if (etaText(job)) $("job-detail").append(el("p", I18N.term(etaText(job)), "eta-note"));
      if (job.mock)
        $("job-detail").append(el("span", I18N.term("MOCK · 합성 결과"), "badge"));
      if (job.error)
        $("job-detail").append(el("div", I18N.term(job.error), "warning error"));
      const actionKey = JSON.stringify([job.id, job.state, Boolean(job.can_resume), job.kind, job.result_id || null]);
      const rebuildActions = state.jobActionKey !== actionKey;
      if (rebuildActions) {
        // Keep an edited budget input and its focus intact during ordinary polling.
        $("job-actions").replaceChildren();
        state.jobActionButtons = [];
      for (const [label, operation] of [
        ["작업 취소", "cancel"],
        ["검증 후 재개", "resume"],
      ]) {
        if (
          (operation === "cancel" &&
            !["queued", "running"].includes(job.state)) ||
          (operation === "resume" && !job.can_resume)
        )
          continue;
        let extension;
        if (operation === "resume" && job.state === "budget_exhausted") {
          const label = el("label", I18N.term("추가 시간 (분)")),
            input = el("input");
          input.type = "number";
          input.min = "1";
          input.value = state.resumeBudgetMinutes[job.id] ?? "60";
          input.oninput = () => { state.resumeBudgetMinutes[job.id] = input.value; };
          I18N.attribute(input, "aria-label", I18N.term("재개 추가 시간 (분)"));
          label.append(input);
          $("job-actions").append(label);
          extension = input;
        }
        const button = el("button", I18N.term(label), "secondary");
        button.disabled = Boolean(state.jobOperations[job.id]);
        button.onclick = async () => {
          if (state.jobOperations[job.id]) return;
          const body = extension ? {additional_budget_seconds: Number(extension.value) * 60} : {};
          if (extension && (!Number.isFinite(body.additional_budget_seconds) || body.additional_budget_seconds <= 0)) {
            notice("추가 시간은 0보다 큰 숫자로 입력하세요.", true);
            return;
          }
          state.jobOperations[job.id] = operation;
          button.disabled = true;
          try {
            const updated = await api(`/api/jobs/${job.id}/${operation}`, body, {timeoutMs: 30000});
            notice(operation !== "cancel"
              ? "재개를 요청했습니다. 기록과 입력을 검증한 뒤 실행합니다. 작업 상태를 확인하세요."
              : updated?.state === "cancelled"
                ? "작업이 취소되었습니다. 저장된 작업과 결과는 보존합니다."
                : updated?.state === "cancelling"
                  ? "취소를 요청했습니다. 상태가 취소로 바뀔 때까지 종료 정리를 기다리세요. 저장된 결과는 보존합니다."
                  : "작업이 이미 종료되었거나 상태가 변경되었습니다. 현재 작업 상태와 저장된 결과를 확인하세요.");
          } catch (e) {
            notice(e.message, true);
          } finally {
            delete state.jobOperations[job.id];
            if (state.pollingJobs) state.jobsRefreshRequested = true;
            await refreshJobs();
          }
        };
        $("job-actions").append(button);
        state.jobActionButtons.push(button);
      }
      }
      if (job.kind === "diagnostics") {
        $("job-detail").append(el("p", I18N.term("이 작업은 환경 확인입니다. 단백질 구조 예측 결과는 만들지 않습니다."), "job-next-step"));
        if (rebuildActions) {
          const button = el("button", I18N.term("분석 준비하기"));
          button.onclick = () => { view("input"); state.showInputReview?.(); };
          $("job-actions").prepend(button);
        }
      } else if (job.kind === "analysis" && !job.result_id) {
        $("job-detail").append(el("p", I18N.term(["queued", "running", "cancelling"].includes(job.state)
          ? job.state === "cancelling"
            ? "취소 요청 후 종료 정리 중입니다. 아직 실행이 끝난 것은 아닙니다. 취소 상태가 될 때까지 기다리세요."
            : "분석 중입니다. 화면을 이동하거나 브라우저를 닫아도 계속 실행됩니다. 진행 표시가 오래 같아도 계산 중일 수 있으니 상세 실행 로그를 확인하세요."
          : job.can_resume
            ? "분석이 중단되었습니다. 저장된 작업과 결과는 보존합니다. 상세 실행 로그에서 사유를 확인하고, 문제가 해결되면 검증 후 재개를 누르세요. 자동으로 재시작하지 않습니다."
            : "저장된 결과가 연결되지 않았습니다. 상세 실행 로그에서 종료 사유를 확인하세요."), "job-next-step"));
      }
      if (rebuildActions && job.kind === "analysis" && job.result_id) {
        const button = el("button", I18N.term("결과 열기"));
        button.onclick = async () => {
          view("results");
          try {
            await loadResult(job.result_id);
          } catch (e) {
            notice(e.message, true);
          }
        };
        $("job-actions").append(button);
      }
      state.jobActionKey = actionKey;
      for (const button of state.jobActionButtons)
        button.disabled = Boolean(state.jobOperations[job.id]);
      const log = await api(
        `/api/jobs/${job.id}/logs?offset=${state.logOffset}`, undefined, {timeoutMs: 15000},
      );
      if (state.job !== chosen || state.jobSelectionVersion !== selection) return;
      state.logOffset = log.next_offset;
      $("job-log").textContent += log.text;
      $("job-log").scrollTop = $("job-log").scrollHeight;

    }
  } catch (e) {
    notice(e.message, true);
  } finally {
    state.pollingJobs = false;
    if (state.jobsRefreshRequested) {
      state.jobsRefreshRequested = false;
      await refreshJobs();
    }
  }
}
async function refreshResults() {
  if (offline) return;
  try {
    const data = await api("/api/results");
    const current = state.result?.id;
    const choices = data.results.map((r) => ({
      value: r.id,
      label: `${r.name || r.id} · ${r.mode || "기존"}${r.mock ? " · MOCK" : ""}`,
    }));
    options(
      $("result-select"),
      [{ value: "", label: "실행을 선택하세요" }, ...choices],
      current,
    );
    options($("compare-select"), [
      { value: "", label: "비교할 실행을 선택하세요" },
      ...choices,
    ]);
  } catch (e) {
    notice(e.message, true);
  }
}
function resultMsaCaption(summary) {
  const complex = summary.mode === "complex";
  const mode = summary.conditions?.msa ?? summary.config?.complex?.msa;
  if (complex && mode === "empty") return "MSA 없음 · 서열만 사용";
  const source = complex
    ? summary.conditions?.msa_source || summary.config?.complex?.msa_source || summary.conditions?.msa || summary.config?.complex?.msa
    : summary.config?.screen?.colabfold?.msa_source;
  if (["public_api", "explicitly_authorized_api_cache"].includes(source)) return "API에서 확보한 MSA";
  if (["cache", "local_mmseqs2", "public_mmseqs2"].includes(source)) return "로컬·캐시 MSA 실행";
  return "MSA 출처 미기록";
}
async function loadResult(id) {
  if (!id) return;
  const request = ++state.resultRequest;
  const summary = offline
    ? window.NANOCOFOLD_OFFLINE.summary
    : await api(`/api/results/${id}`);
  let rows = [];
  if (offline) rows = window.NANOCOFOLD_OFFLINE.candidates?.rows || [];
  else {
    let offset = 0,
      total;
    do {
      const data = await api(
        `/api/results/${id}/candidates?offset=${offset}&limit=10000`,
      );
      rows.push(...data.rows);
      total = data.filtered_total;
      offset += data.rows.length;
      if (!data.rows.length) break;
    } while (offset < total);
  }
  const observations = offline
    ? window.NANOCOFOLD_OFFLINE.observations?.rows || []
    : (await api(`/api/results/${id}/observations`)).rows;
  let provenance = offline ? window.NANOCOFOLD_OFFLINE.provenance || {} : null;
  if (!offline) {
    try {
      const artifacts = await api(`/api/results/${id}/artifacts?provenance_only=true`);
      if (!artifacts?.provenance || typeof artifacts.provenance !== "object" || Array.isArray(artifacts.provenance))
        throw new Error("파일 출처 응답 형식이 올바르지 않습니다.");
      provenance = artifacts.provenance;
    } catch (error) {
      provenance = {status: "unavailable", issue: "파일 출처를 가져오지 못했습니다. 후보와 구조는 계속 볼 수 있습니다.",
        error: error.message};
    }
  }
  if (request !== state.resultRequest) return;
  state.result = summary;
  $("notice").hidden = true;  // A successfully opened result dismisses the prior completion notice.
  state.retentionPlan = null;
  if ($("retention-plan")) $("retention-plan").replaceChildren();
  if ($("retention-confirmation")) $("retention-confirmation").hidden = true;
  if (summary.retention === "minimal") $("result-summary").setAttribute("data-retention", "minimal");
  else $("result-summary").removeAttribute("data-retention");
  const generalComplex = summary.mode === 'complex';
  for (const id of ["eligibility", "eligibility-basis", "hotspot-filter"])
    $(id).disabled = generalComplex;
  if (generalComplex) {
    $("eligibility").value = "all";
    $("hotspot-filter").checked = false;
  }
  for (const [layer, caption] of Object.entries(generalComplex
    ? {target:'기준 체인', binder:'파트너 체인', context:'다른 파트너', hotspots:'관심 잔기'}
    : {target:'표적', binder:'VHH', context:'파트너', hotspots:'결합 부위(hotspot)'})) {
    const checkbox = document.querySelector(`[data-layer="${layer}"]`);
    if (checkbox) checkbox.parentElement.replaceChildren(checkbox, el("span", I18N.term(caption)));
  }
  state.rows = rows.map(row => ({...row, predictor_engine: row.predictor_engine || row.engine || summary.conditions?.engine || "unknown", design_origin: row.design_origin || row.parent_engine || row.source || null}));
  syncResultModeControls();
  refreshMetricCaptions(state.rows, state.result?.mode);
  const savedSort = offline ? window.NANOCOFOLD_OFFLINE.filters?.sort : null;
  if (!savedSort) {
    const defaultSort = defaultResultSort(state.rows, summary.mode);
    const select = $("sort-metric");
    if (![...select.options].some(option => option.value === defaultSort.sort)) {const option = el("option", I18N.term(metricCaption(defaultSort.sort, state.rows)));option.value = defaultSort.sort;select.append(option);}
    select.value = defaultSort.sort;
    $("descending").checked = defaultSort.descending;
  }
  options($("engine-filter"), [{value:"all",label:"전체 엔진"}, ...[...new Set(state.rows.map(row => row.predictor_engine))].sort().map(engine => ({value:engine,label:engine}))], offline ? (window.NANOCOFOLD_OFFLINE.filters?.engine_filter || "all") : "all");
  state.observations = observations;
  clearCandidate();
  $("result-select").value = id;
  $("explorer").hidden = false;
  state.displayAliases = reportAliases(state.rows);
  const counts = reportModelCounts(state.rows, summary.mode, summary.config);
  const msaCaption = resultMsaCaption(summary);
  $("result-input").replaceChildren(el("p", I18N.format("실행: {0} · 모드: {1} · {2}", [(summary.name || summary.id ? (summary.name ? summary.name : summary.id) : I18N.term("미기록")), (summary.mode ? summary.mode : I18N.term("미기록")), I18N.term(msaCaption)])));
  I18N.text($("result-input-config"), JSON.stringify({conditions: summary.conditions, config: summary.config}, null, 2));
  $("result-summary").replaceChildren(el("h2", I18N.term("02 후보·모델 수")), el("p", I18N.format("분석: {0}", [(summary.name || summary.id ? (summary.name ? summary.name : summary.id) : I18N.term("미기록"))]), "muted"));
  if (summary.retention === "minimal") $("result-summary").append(el("p", I18N.term("핵심 보관본 · 서열·점수·모든 최종 구조를 유지했습니다. PAE 배열·MSA·중간 파일은 용량 정리로 삭제되어, 이 상세 자료가 필요하면 전체 분석을 새로 실행해야 합니다."), "report-interpretation"));
  const stats = el("div", "", "report-stats");
  for (const [caption,value] of [[generalComplex?"고유 입력 단백질 서열":"원본 후보",generalComplex?counts.unique_sequences:counts.source_candidates],["전체 모델 기록",summary.model_record_total ?? counts.model_total],["평가 완료 모델",counts.evaluated_total],["기록 적격 모델",generalComplex?"적용 안 함":counts.eligible_total]]) {
    const stat=el("div","","report-stat");stat.append(el("span",I18N.term(caption)),el("strong",value === null ? I18N.term("미평가·미기록") : I18N.term(String(value))));stats.append(stat);
  }
  $("result-summary").append(stats,el("p", generalComplex ? I18N.format("평가 미완료 {0} 모델. 일반 복합체에는 VHH 적격 판정을 적용하지 않습니다.", [counts.evaluation_missing]) : I18N.format("고유 후보 서열 {0}종 · 평가 미완료 {1} · 적격 판정 미평가 {2} · 기록 선별 {3} 모델. 원본 후보는 source_sequence_id 및 원본 ID의 엔진 접두어로 구분합니다. 서열이 모두 기록된 경우에만 고유 서열 수를 계산합니다.", [counts.unique_sequences === null ? I18N.term("미기록") : counts.unique_sequences, counts.evaluation_missing, counts.eligibility_missing, fmt(summary.selected_total)]), "muted"));
  if (!generalComplex && summary.quality_eligible_total !== null && summary.quality_eligible_total !== undefined)
    $("result-summary").append(el("p", I18N.format("실행 기록 요약: 기록 적격 {0} · 품질 적격 {1} (결합 부위 제외). 품질 적격은 원본 선별을 바꾸지 않는 탐색 값입니다.", [fmt(summary.eligible_total), fmt(summary.quality_eligible_total)]), "muted"));
  const expected = summary.expected_total;
  $("result-summary").append(el("p", I18N.format("기록된 계획 모델 {0} · 실제 모델 기록 {1} · 평가 완료 {2} · 전체 후보·모델 기록 {3} · 예측 전 준비·미선정 {4}", [expected == null ? I18N.term("미기록") : expected, (summary.model_record_total != null ? summary.model_record_total : counts.model_total), counts.evaluated_total, (summary.candidate_record_total != null ? summary.candidate_record_total : counts.prepared_record_total), (summary.preprediction_record_total != null ? summary.preprediction_record_total : counts.preprediction_record_total)]), "muted"));
  $("result-summary").append(el("p", I18N.term("집계 범위: 저장된 전체 후보·모델 기록. 예측 전 준비·미선정은 평가 미완료 모델 수에 포함하지 않습니다."), "muted"));
  if (summary.engine_provenance && Object.keys(summary.engine_provenance).length) {
    const provenance = el("details"); provenance.append(el("summary", I18N.term("예측 엔진 버전·가중치·출처")), el("pre", JSON.stringify(summary.engine_provenance, null, 2))); $("result-summary").append(provenance);
  }
  const engineSummary=el("div","","report-engine-summary");
  const names={colabfold:"ColabFold",boltz2:"Boltz-2",chai1:"Chai-1",opendde:"OpenDDE Preview"};
  for (const engine of [...new Set(state.rows.map(r=>r.predictor_engine))].sort()) {
    const models=state.rows.filter(r=>r.predictor_engine===engine),card=el("div"), engineCounts=reportModelCounts(models, summary.mode, summary.config);
    card.append(el("b",(names[engine] ? names[engine] : engine)),el("span",I18N.format("평가 {0}/{1} · 오류 {2}{3}", [engineCounts.evaluated_total, engineCounts.model_total, models.filter(r=>recordedEvaluationError(r)).length, generalComplex ? '' : I18N.format(" · 기록 적격 {0} · 판정 미평가 {1}", [engineCounts.eligible_total === null ? I18N.term("미평가") : engineCounts.eligible_total, engineCounts.eligibility_missing])])));engineSummary.append(card);
  }
  $("result-summary").append(engineSummary,el("p",I18N.term("동일 후보 서열의 엔진별 재예측은 별도 관측입니다. 상대 점수의 정규화 범위는 기록된 ranking_policy를 확인하세요. 기존 부분 재평가 경로는 혼합 배치일 수 있으며, 독립 비교에서는 엔진별로 순위를 매깁니다. 결합 확률·KD로 해석하지 않습니다."),"report-interpretation"));
  renderReportReasons(state.rows, summary.mode);
  const guide=el("details","","report-guide");guide.append(el("summary",I18N.term("지표 읽는 법과 평가 한계")));
  const list=el("ul");
  for(const text of ["ipTM·ipSAE: 높을수록 예측 신뢰도가 높습니다. 같은 엔진·조건 안에서 비교하세요.","pLDDT: 국소 구조 신뢰도. PAE: 상대 배치 오차(Å)로 낮을수록 좋으며, 행→열의 방향별 값이 다를 수 있습니다.","pDockQ2: CA 8 Å 접촉과 방향별 PAE를 사용합니다. AlphaFold-Multimer 보정이며 Boltz·Chai·OpenDDE 값은 탐색용입니다.","적격 0건은 실행 실패와 구분합니다. 미평가 항목은 0점이나 판정 통과로 취급하지 않습니다.","현재 필터를 바꿔도 원래 적격 판정·선별 목록·전체 모델과 출처는 내보내기에 보존됩니다."])list.append(el("li",I18N.term(text)));
  guide.append(list);$("result-summary").append(guide);
  $("result-summary").append(
    el(
      "span",
      summary.mock ? I18N.term("MOCK · 실제 예측 아님") : summary.failed_candidates ? I18N.term("부분 실패 · 후보 오류 확인") : summary.status &&
              states[summary.status] &&
              summary.status !== "complete" ? I18N.concat([I18N.term(states[summary.status]), I18N.term(" · 부분 결과")]) : summary.complete ? generalComplex ? I18N.term("분석 완료 · 일반 복합체 평가") : counts.eligible_total === null ? I18N.term("분석 완료 · 적격 판정 미평가") : summary.zero_eligible ? summary.quality_eligible_total > 0 ? I18N.format("정상 완료 · 결합 부위 제외 품질 적격 {0}개", [summary.quality_eligible_total]) : I18N.term("정상 완료 · 적격 후보 0개") : I18N.term("분석 완료") : I18N.term("미완료 / 부분 결과"),
      "badge",
    ),
  );
  warnings($("result-issues"), [
    ...(summary.issues || []),
    ...(summary.warnings || []).map(message => "실행 당시 기록 경고: " + message),
  ]);
  I18N.text($("result-provenance"), JSON.stringify(provenance || {}, null, 2));
  if (provenance?.status === "unavailable") notice(provenance.issue, true);
  I18N.text($("result-conditions"), JSON.stringify(
    {
      conditions: summary.conditions,
      config: summary.config,
      execution: summary.state?.execution,
      readonly: true,
    },
    null,
    2,
  ));
  renderCandidates();
  if (state.filtered.length)
    await selectCandidate(state.filtered[0].candidate_id);
}
function filters() {
  return {
    eligibility: $("eligibility").value,
    eligibility_basis: $("eligibility-basis").value,
    hotspot_min: $("hotspot-filter").checked ? $("hotspot-min").valueAsNumber / 100 : null,
    selected_candidate_id: state.candidate?.candidate_id,
    selected_observation_id: $("observation-select").value || null,
    result_layers: resultLayers(),
    viewer_display: resultDisplay(),
    selected_residues: state.viewer?.selectedLabels || [],
    distance_selection: {a: $("residue-a").value, b: $("residue-b").value},
    plot_x: $("plot-x").value,
    plot_y: $("plot-y").value,
    search: $("candidate-search").value,
    sort: $("sort-metric").value,
    descending: $("descending").checked,
    engine_filter: $("engine-filter").value,
    ...(["true", "false"].includes($("eligibility").value)
      ? { eligible: $("eligibility").value === "true" }
      : {}),
  };
}
function clearCandidate() {
  state.candidate = null;
  state.structure = null;
  state.structureRequest++;
  disposeResultViewer();
  I18N.text($("candidate-title"), I18N.term("후보 선택"));
  $("candidate-detail").replaceChildren(
    el("p", I18N.term("현재 실행·탐색 조건에서 후보를 선택하세요.")),
  );
  $("observation-select").replaceChildren();
  I18N.text($("structure-note"), I18N.term("선택한 구조가 없습니다."));
  $("structure-details").replaceChildren();
  I18N.text($("observation-note"), "");
  // Purge the previous heatmap before a new run changes its dimensions.
  // Overlapping empty/nonempty Plotly.react updates can stall the renderer.
  if (window.Plotly)
    for (const id of ["pae", "distribution"]) {
      Plotly.purge($(id));
      $(id).replaceChildren();
    }
  syncCandidateNavigation();
}
function syncCandidateNavigation() {
  const rows = state.filtered || [], selected = state.candidate?.candidate_id;
  const select = $("viewer-candidate-select");
  if (!select) return;
  options(select, [{value:"",label:rows.length?"후보 선택":"현재 조건에 표시할 후보가 없습니다"},
    ...rows.map(row => ({value:row.candidate_id,
      label:`${state.displayAliases?.[row.candidate_id] || row.candidate_id} · ${row.predictor_engine || "엔진 미기록"} · ipSAE ${fmt(row.ipsae)}`}))], selected || "");
  const index = rows.findIndex(row => row.candidate_id === selected);
  select.disabled = !rows.length;
  $("previous-candidate").disabled = index <= 0;
  $("next-candidate").disabled = !rows.length || index === rows.length - 1;
  I18N.text($("viewer-candidate-position"), index < 0 ? I18N.format("선택 없음 / {0}개", [rows.length]) : I18N.format("{0} / {1}개", [index + 1, rows.length]));
  const row = state.candidate;
  if (row) {
    const eligibility = candidateEligibility(row);
    const basis = $("eligibility-basis").value === "quality" ? "품질 판정 (결합 부위 제외)" : "실행 당시 기록 판정";
    const assessment = recordedEvaluationError(row) ? I18N.term(modelAssessmentCaption(row)) : !row.evaluated ? I18N.term("미평가") : state.result.mode === "complex" ? I18N.term(modelAssessmentCaption(row))
      : I18N.concat([I18N.term(basis), ": ", I18N.term(decisionCaption(eligibility))]);
    I18N.text($("candidate-status"), I18N.format("{0} · ipSAE {1} · {2} {3} · {4}", [(row.predictor_engine ? row.predictor_engine : I18N.term("엔진 미기록")), fmt(row.ipsae), I18N.term(metricCaption("iptm", row)), fmt(row.iptm), assessment]));
  } else I18N.text($("candidate-status"), I18N.term("현재 목록에서 후보를 선택하세요."));
}
function stepCandidate(offset) {
  const rows = state.filtered || [];
  const index = rows.findIndex(row => row.candidate_id === state.candidate?.candidate_id);
  const next = index < 0 ? (offset > 0 ? 0 : -1) : index + offset;
  if (next >= 0 && next < rows.length) return selectCandidate(rows[next].candidate_id);
}
function reviewCandidate(id) {
  const result = selectCandidate(id);
  if (window.matchMedia("(max-width:1100px)").matches)
    $("candidate-title").scrollIntoView({block:"start"});
  return result;
}
function candidateSearchText(row) {
  return [row.candidate_id, state.displayAliases?.[row.candidate_id], row.sequence, row.exclusion_reason,
    ...(row.exclusion_reasons || []), row.error]
    .filter((value) => value !== null && value !== undefined)
    .join(" ").toLowerCase();
}
function candidateEligibility(row) {
  return $("eligibility-basis").value === "quality" ? row.quality_eligible : row.final_eligible;
}
function hotspotSite(row) {
 const count=v=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0?v:null;
 const requested=count(row.n_hotspots_requested),contacted=count(row.n_hotspots_contacted),fraction=row.hotspot_contact_frac;
 const validFraction=typeof fraction==='number'&&Number.isFinite(fraction)&&fraction>=0&&fraction<=1;
 const check=Array.isArray(row.threshold_checks)?row.threshold_checks.find(c=>c&&typeof c==='object'&&!Array.isArray(c)&&c.metric==='hotspot_contact_frac'):undefined;
 const rowPassed=typeof row.pass_hotspot==='boolean'?row.pass_hotspot:null,checkPassed=typeof check?.passed==='boolean'?check.passed:null;
 const passed=rowPassed??checkPassed;
 const inconsistent=(requested!==null&&contacted!==null&&contacted>requested)||
  (requested!==null&&requested>0&&contacted!==null&&validFraction&&Math.abs(fraction-contacted/requested)>0.0005+1e-12)||
  (rowPassed!==null&&checkPassed!==null&&rowPassed!==checkPassed);
 let status='not_assessed',label='결합 부위 미평가';
 if(row.hotspot_mapping_failed===true){status='mapping_failed';label='결합 부위 번호 대응 실패';}
 else if(inconsistent){label='결합 부위 기록 불일치';}
 else if(requested===0){status='not_requested';label='결합 부위 미지정';}
 else if(requested!==null&&contacted!==null&&contacted<=requested&&validFraction){
  if(contacted===0){status='no_contact';label='지정 결합 부위 미접촉';}
  else if(passed===true){status='threshold_met';label='결합 부위 접촉 기준 충족';}
  else if(passed===false){status='threshold_unmet';label='결합 부위 접촉 기준 미달';}
  else{status='threshold_unrecorded';label='결합 부위 판정 미기록';}
 }
 const assessed=!['mapping_failed','not_assessed','not_requested'].includes(status);
 return {status,label,requested,contacted,contacts:assessed?`${contacted}/${requested}개`:'—',
  contact_fraction:assessed?fraction:null,passed:assessed?passed:null,threshold:check?.threshold??null};
}
/* POSE_PRESENTATION_START */
const poseStatusLabels = Object.freeze({
 pose_consensus_status:{multi_engine_supported:"다중 엔진 지지·탐색용",single_engine_promising:"엔진별 단독 유망·탐색용",supported:"여러 엔진 자세 일치",quality_support_insufficient:"자세 일치·기록 적격 지지 부족",single_engine:"단일 엔진·비교 부족",discordant:"엔진 간 자세 불일치",not_assessed:"미평가",disabled:"평가 비활성"},
 design_consistency_status:{consistent:"설계·재예측 일치",inconsistent:"설계·재예측 불일치",not_assessed:"미평가",not_applicable:"적용 안 함",disabled:"평가 비활성"},
 review_priority:{multi_engine_supported:"다중 엔진 지지·탐색용",single_engine_promising:"엔진별 단독 유망·탐색용",prioritized:"우선 검토·탐색용",site_unmet:"지정 결합 부위 미충족·보류",site_not_assessed:"지정 결합 부위 미평가·보류",confidence_gate_unmet:"기록 적격 미통과·보류",confidence_not_assessed:"기록 적격 미평가·보류",geometry_not_assessed:"자세 미평가·보류",design_inconsistent:"설계 자세 미재현·보류",design_not_assessed:"설계 일치 미평가·보류",consensus_insufficient:"자세 일치 지지 부족·보류",identity_not_assessed:"후보 대응 미평가·보류",disabled:"평가 비활성"}
});
function poseStatusLabel(row,key) {
 const value=row[key];
 if(["site_satisfied","site_selection_eligible"].includes(key))return value===true?"충족":value===false?"미충족":"미평가";
 const labels=poseStatusLabels[["candidate_review_priority","review_priority_reason"].includes(key)?"review_priority":key]||{};
 return typeof value==="string"&&Object.hasOwn(labels,value)?labels[value]:"미평가";
}
function hasPoseAssessment(rows) {
 return rows.some(row=>Object.keys(poseStatusLabels).some(key=>Object.hasOwn(row,key)));
}
function defaultResultSort(rows,mode) {
 const ranked=mode!=="complex"&&rows.some(row=>typeof row.review_rank==="number"&&Number.isSafeInteger(row.review_rank)&&row.review_rank>0);
 return ranked?{sort:"review_rank",descending:false}:{sort:"ipsae",descending:true};
}
function residueLayers(layers,residues) {
 return residues?{...layers}:{...layers,hotspots:false,contacts:false,clashes:false};
}
/* POSE_PRESENTATION_END */
function siteValue(row,key) {
 if(Object.hasOwn(poseStatusLabels,key)||["site_satisfied","site_selection_eligible","candidate_review_priority"].includes(key)) {
  const label=poseStatusLabel(row,key);return label==="미평가"?null:label;
 }
 if(['n_hotspots_contacted','hotspot_contact_frac','hotspot_site_status','pass_hotspot'].includes(key)) {
  const assessment=hotspotSite(row);
  if(assessment.contact_fraction===null)return null;
  if(key==='n_hotspots_contacted')return assessment.contacted;
  if(key==='hotspot_contact_frac')return assessment.contact_fraction;
  return assessment.label;
 }
 return row[key];
}
function hotspotFilterFraction(row) {
 if(row.hotspot_mapping_failed)return null;
 const assessment=hotspotSite(row);
 if(assessment.contact_fraction!==null)return assessment.contact_fraction;
 if(!Object.hasOwn(row,'n_hotspots_requested')&&!Object.hasOwn(row,'n_hotspots_contacted')) {
  const check=Array.isArray(row.threshold_checks)?row.threshold_checks.find(c=>c&&typeof c==='object'&&!Array.isArray(c)&&c.metric==='hotspot_contact_frac'):undefined;
  if(typeof row.pass_hotspot==='boolean'&&typeof check?.passed==='boolean'&&row.pass_hotspot!==check.passed)return null;
  const fraction=row.hotspot_contact_frac;
  if(typeof fraction==='number'&&Number.isFinite(fraction)&&fraction>=0&&fraction<=1)return fraction;
 }
 return null;
}
function hotspotMatchesMinimum(row,minimum) {
 const fraction=hotspotFilterFraction(row);
 return fraction!==null&&fraction>=minimum;
}
function syncResultModeControls() {
  const complex = state.result?.mode === "complex";
  for (const node of document.querySelectorAll("[data-vhh-results]")) node.hidden = complex;
  for (const id of ["eligibility", "eligibility-basis", "hotspot-min"]) {
    const node = $(id), label = document.querySelector(`label[for="${id}"]`);
    if (node) node.hidden = complex;
    if (label) label.hidden = complex;
  }
  for (const layer of ["hotspots", "cdr"]) {
    const node = document.querySelector(`[data-layer="${layer}"]`);
    if (node) {node.parentElement.hidden = complex;if (complex) node.checked = false;}
  }
  if ($("cdr-actions")) $("cdr-actions").hidden = complex;
  for (const id of ["sort-metric", "plot-x", "plot-y"]) {
    const select = $(id);
    for (const option of select?.options || []) {
      const unavailable = complex && ["composite_score", "dev_flags", "dev_severe", "esm_score", "review_rank", "candidate_review_priority", "site_selection_eligible"].includes(option.value);
      option.hidden = unavailable;option.disabled = unavailable;
    }
    if (select?.selectedOptions[0]?.disabled) select.value = id === "plot-x" ? "iptm" : "ipsae";
  }
}
function renderCandidates() {
  syncResultModeControls();
  if (!state.result) return;
  const f = filters(),
    status = $("eligibility").value;
  $("hotspot-min").disabled = !$("hotspot-filter").checked;
  if (f.hotspot_min !== null && (!Number.isFinite(f.hotspot_min) || f.hotspot_min < 0 || f.hotspot_min > 1)) {
    notice("최소 결합 부위 접촉률은 0–100% 사이로 입력하세요.", true);
    return;
  }
  state.filtered = state.rows.filter(
    (row) =>
      (f.engine_filter === "all" || row.predictor_engine === f.engine_filter) &&
      (!f.search ||
        candidateSearchText(row).includes(f.search.toLowerCase())) &&
      (status === "all" || status === "missing"
        ? status === "all" || candidateEligibility(row) == null
        : candidateEligibility(row) === f.eligible) &&
      (f.hotspot_min === null || hotspotMatchesMinimum(row,f.hotspot_min)),
  );
  const keyed = state.filtered.map((row,position)=>({row,position,value:siteValue(row,f.sort)}));
  keyed.sort((a, b) => {
    const x = a.value,
      y = b.value;
    const missing = value => value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value));
    if (missing(x)) return missing(y) ? 0 : 1;
    if (missing(y)) return -1;
    return (
      (typeof x === "number" && typeof y === "number"
        ? x - y
        : String(x).localeCompare(String(y))) * (f.descending ? -1 : 1)
    ) || a.position-b.position;
  });
  state.filtered = keyed.map(item=>item.row);
  I18N.text($("candidate-count"), I18N.format("표시 {0} / 전체 {1} 후보·모델 기록 · 미평가는 0으로 바꾸지 않습니다.", [state.filtered.length, state.rows.length]));
  refreshMetricCaptions(state.filtered, state.result?.mode);
  I18N.text($("candidate-sort-note"), I18N.format("정렬 기준: {0} · {1} · 미평가 마지막 · {2}.", [I18N.term(metricCaption(f.sort, state.filtered, state.result?.mode)), f.descending ? I18N.term("높은 값부터") : I18N.term("낮은 값부터"), f.sort === "review_rank" ? I18N.term("동일 범위의 후보별 검토 순위·동률 허용·1위도 보류 가능") : I18N.term("탐색 순서이며 통합 순위가 아닙니다")]));
  const columns = state.result.mode === "complex" ? ["candidate_id", "predictor_engine", "seed", "model", "iptm", "ipsae", "pdockq2", "pdockq2_target", "pdockq2_binder", "interface_pae", "n_clashes", "error"] : [
      "candidate_id",
      "predictor_engine",
      "design_origin",
      "quality_eligible",
      "final_eligible",
      "selected",
      "ipsae",
      "iptm",
      "interface_plddt",
      "pdockq2",
      "pdockq2_target",
      "pdockq2_binder",
      "interface_pae",
      "hotspot_contact_frac",
      "n_hotspots_contacted",
      "hotspot_site_status",
      "n_clashes",
      "exclusion_reasons",
    ],
    head = el("tr");
  if(state.result.mode!=="complex"&&hasPoseAssessment(state.rows))columns.splice(columns.indexOf("n_clashes"),0,"site_selection_eligible","pose_consensus_status","pose_supporting_engines","pose_quality_supporting_engines","design_consistency_status","design_binder_rmsd_A","review_rank","candidate_review_priority","review_priority");
  const expanded = $("candidate-extra-columns")?.checked === true;
  if (!expanded) {
    const core = state.result.mode === "complex"
      ? ["candidate_id","predictor_engine","seed","model","iptm","ipsae","n_clashes","error"]
      : ["candidate_id","predictor_engine","final_eligible","selected","ipsae","iptm","interface_plddt","interface_pae","hotspot_site_status","n_clashes","exclusion_reasons"];
    columns.splice(0,columns.length,...core);
  }
  const captions = {quality_eligible: "품질 적격 (결합 부위 제외)", final_eligible: "기록 적격", selected: "기록 선별", exclusion_reasons: "기록 제외 사유"};
  captions.n_hotspots_contacted = "결합 부위 접촉 수"; captions.hotspot_site_status = "지정 결합 부위 충족";
  captions.predictor_engine = "예측 엔진"; captions.design_origin = "생성 엔진";
  captions.n_clashes = "충돌 원자쌍";
  Object.assign(captions, metricCaptions);
  if (state.result.mode === "complex") Object.assign(captions, complexMetricCaptions);
  captions.iptm=metricCaption("iptm", state.filtered);
  if (state.result.mode === "complex") captions.ipsae="ipSAE · 표시 체인 쌍";
  columns.forEach(k => {
    const th = el("th"), button = el("button", I18N.concat([(captions[k] ? I18N.term(captions[k]) : k), f.sort === k ? f.descending ? " ↓" : " ↑" : " ↕"]), "sort-header");
    th.setAttribute("aria-sort", f.sort === k ? (f.descending ? "descending" : "ascending") : "none");
    th.dataset.columnKey = k;
    button.type = "button";
    button.onclick = () => {
      const select = $("sort-metric");
      if (![...select.options].some(option => option.value === k)) {const option = el("option", (captions[k] ? I18N.term(captions[k]) : k));option.value = k;select.append(option);}
      $("descending").checked = f.sort === k ? !f.descending : !["candidate_id","predictor_engine","design_origin","seed","model","hotspot_site_status","pass_hotspot","site_selection_eligible","pose_consensus_status","design_consistency_status","review_priority","candidate_review_priority","design_binder_rmsd_A","review_rank"].includes(k);
      select.value = k; renderCandidates();
    };
    th.append(button);head.append(th);
  });
  $("candidate-table").querySelector("thead").replaceChildren(head);
  const body = $("candidate-table").querySelector("tbody");
  body.replaceChildren(
    ...state.filtered.map((row) => {
      const tr = el("tr");
      tr.dataset.id = row.candidate_id;
      tr.className =
        row.candidate_id === state.candidate?.candidate_id ? "selected" : "";
      columns.forEach((key, i) => {
        const td = el("td");
        td.dataset.columnKey = key;
        if (i === 0) {
          const button = el("button", (state.displayAliases?.[row.candidate_id] ? state.displayAliases?.[row.candidate_id] : row[key]));
          button.title = row.candidate_id;
          button.onclick = () => reviewCandidate(row.candidate_id);
          td.append(button);
        } else if(["final_eligible","quality_eligible"].includes(key)) I18N.text(td, I18N.term(decisionCaption(row[key])));
        else if(key === "error") I18N.text(td, I18N.term(evaluationErrorCaption(row)));
        else if(key === "selected") I18N.text(td, typeof row[key]==="boolean" ? row[key] ? I18N.term("선별") : I18N.term("미선별") : I18N.term("미기록"));
        else if(key === "exclusion_reasons") I18N.text(td, (row[key]||[]).length ? I18N.concat(row[key].flatMap((code,index) => [index ? " · " : "", I18N.term(reportReasonLabel(code))])) : I18N.term("없음"));
        else if(key === "ipsae" && state.result.mode === "complex") I18N.text(td, I18N.concat([fmt(row[key]), row.ipsae_chain_a && row.ipsae_chain_b ? I18N.format(" ({0}–{1} 체인쌍)", [row.ipsae_chain_a, row.ipsae_chain_b]) : I18N.term(" (적용 체인 미기록)")]));
        else if(key === "hotspot_site_status") I18N.text(td, I18N.term(hotspotSite(row).label));
        else if(Object.hasOwn(poseStatusLabels,key)||["site_satisfied","site_selection_eligible","candidate_review_priority"].includes(key))I18N.text(td, I18N.term(poseStatusLabel(row,key)));
        else if(key === "n_hotspots_contacted") I18N.text(td, hotspotSite(row).contacts);
        else if(key === "hotspot_contact_frac") I18N.text(td, fmt(hotspotSite(row).contact_fraction));
        else I18N.text(td, row[key] == null ? I18N.term("미평가") : fmt(row[key]));
        const recorded = siteValue(row, key);
        if (typeof recorded === "number" && Number.isFinite(recorded))
          td.title = String(recorded);
        tr.append(td);
      });
      return tr;
    }),
  );
  if (
    state.candidate &&
    !state.filtered.some((r) => r.candidate_id === state.candidate.candidate_id)
  )
    clearCandidate();
  syncCandidateNavigation();
  renderScatter();
}
function renderScatter() {
  if (!window.Plotly || !state.result) return;
  const x = $("plot-x").value,
    y = $("plot-y").value,
    rows = state.filtered.filter(
      (r) => Number.isFinite(r[x]) && Number.isFinite(r[y]),
    );
  const node = $("scatter");
  Plotly.react(
    node,
    [
      {
        x: rows.map((r) => r[x]),
        y: rows.map((r) => r[y]),
        customdata: rows.map((r) => r.candidate_id),
        text: rows.map((r) => r.candidate_id),
        type: "scatter",
        mode: "markers",
        marker: {
          size: rows.map((r) =>
            r.candidate_id === state.candidate?.candidate_id ? 14 : 8,
          ),
          color: rows.map((r) =>
            candidateEligibility(r) === true
              ? "#17695f"
              : candidateEligibility(r) === false
                ? "#b96840"
                : "#8797a3",
          ),
        },
        hovertemplate:
          "%{text}<br>" + I18N.value(I18N.term(metricCaption(x, state.filtered, state.result?.mode))) + ": %{x}<br>" + I18N.value(I18N.term(metricCaption(y, state.filtered, state.result?.mode))) + ": %{y}<extra></extra>",
      },
    ],
    {
      font: { size: 14 },
      // Reserve space above data points so the Plotly toolbar cannot intercept
      // clicks on high-valued observations in a narrow three-panel layout.
      margin: { t: 50, l: 50, r: 10, b: 65 },
      xaxis: { automargin: true, title: { text: I18N.value(I18N.term(metricCaption(x, state.filtered, state.result?.mode))), font: { size: 14 }, standoff: 12 } },
      yaxis: { automargin: true, title: { text: I18N.value(I18N.term(metricCaption(y, state.filtered, state.result?.mode))), font: { size: 14 }, standoff: 8 } },
      annotations: rows.length
        ? []
        : [{ text: I18N.value(I18N.term("표시할 평가값이 없습니다")), showarrow: false }],
      paper_bgcolor: "#fff",
    },
    { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["sendChartToCloud", "sendDataToCloud", "editInChartStudio"] },
  );
  node.removeAllListeners?.("plotly_click");
  node.on("plotly_click", (event) =>
    selectCandidate(event.points[0].customdata),
  );
}
async function selectCandidate(id) {
  state.candidate = state.rows.find((r) => r.candidate_id === id);
  const row = state.candidate;
  if (!row) return;
  I18N.text($("candidate-title"), (state.displayAliases?.[id] ? state.displayAliases?.[id] : id));
  syncCandidateNavigation();
  const detail = $("candidate-detail");
  detail.replaceChildren(
    el(
      "p",
      recordedEvaluationError(row) ? I18N.term(modelAssessmentCaption(row)) : state.result.mode === 'complex' ? I18N.term(modelAssessmentCaption(row)) : row.evaluated ? candidateEligibility(row) === true ? I18N.term("선택한 판정 기준 적격") : candidateEligibility(row) === false ? I18N.term("선택한 판정 기준 부적격") : I18N.term("필수 지표 판정 미평가") : I18N.term("구조 점수 미평가"),
    ),
  );
  if(state.result.mode !== "complex") {
    const h=hotspotSite(row),note=el("div");note.setAttribute("role","status");
    note.append(el("h3",I18N.term("결합 위치 · 신뢰도와 별도 확인")),el("p",I18N.concat([I18N.term(h.label)," · ",I18N.term(h.contacts)])));
    if(h.status==='no_contact')note.append(el("p",I18N.term("신뢰도 점수가 높아도 지정 결합 부위에는 접촉하지 않았습니다. 지정 결합 부위 재현 성공으로 해석하지 마세요.")));
    note.append(el("p",I18N.term("PAE는 참조 구조와의 실측 오차가 아니며 pDockQ2는 실측 DockQ가 아닙니다. 지정 결합 부위 진단은 기존 점수·제외 사유를 바꾸지 않습니다."),"muted"));detail.append(note);
  }
  if(state.result.mode!=="complex"&&hasPoseAssessment([row])) {
    const panel=el("div");panel.setAttribute("role","status");panel.className="pose-assessment";
    panel.append(el("h3",I18N.term("신뢰도 · 지정 결합 부위 · 자세 일관성")));
    panel.append(el("p",I18N.format("실행 당시 기록 적격: {0} · 지정 결합 부위 포함 적격: {1}", [row.final_eligible===true ? I18N.term("적격") : row.final_eligible===false ? I18N.term("부적격") : I18N.term("미평가"), I18N.term(poseStatusLabel(row,"site_selection_eligible"))])));
    panel.append(el("p",I18N.format("동일 후보 자세 일치: {0} · 자세 일치 엔진 {1}개 · 기록 적격 지지 {2}개", [I18N.term(poseStatusLabel(row,"pose_consensus_status")), fmt(row.pose_supporting_engines), fmt(row.pose_quality_supporting_engines)])));
    panel.append(el("p",I18N.format("설계·재예측: {0} · 표적 정렬 후 VHH RMSD {1} Å", [I18N.term(poseStatusLabel(row,"design_consistency_status")), fmt(row.design_binder_rmsd_A)])));
    panel.append(el("p",I18N.format("검토 순위: {0} · 범위 {1} · 1위도 보류일 수 있으며 동률을 허용합니다.", [fmt(row.review_rank), fmt(row.review_rank_scope)])));
    panel.append(el("p",I18N.format("후보 검토 판정: {0} · 모델 검토 판정: {1}", [I18N.term(poseStatusLabel(row,"candidate_review_priority")), I18N.term(poseStatusLabel(row,"review_priority"))])));
    panel.append(el("p",I18N.format("후보 검토 판정 이유: {0}", [I18N.term(poseStatusLabel(row,"review_priority_reason"))])));
    panel.append(el("p",I18N.term("검토 정책 v3는 품질·지정 결합 부위를 충족한 엔진별 단독 유망 예측도 표시합니다. 같은 자세에서 두 엔진 이상이 충족하면 다중 엔진 지지로 구분합니다. 엔진 간 자세 차이와 설계·재예측 불일치는 별도 진단이며 자동 비결합·탈락 사유가 아닙니다."),"muted"));
    panel.append(el("p",I18N.term("동일 후보 서열의 표적 정렬 후 자세를 비교합니다. 엔진 점수를 평균하지 않으며, 엔진 지지 수는 독립 실험 횟수가 아닙니다. 새 VHH를 기존 VHH 자세에 맞춰 선별하지 않습니다. 탐색적 우선순위는 기존 적격·선별 기록을 바꾸지 않으며 실제 결합·KD를 입증하지 않습니다."),"muted"));
    if(row.pose_assessment&&typeof row.pose_assessment==="object") {const raw=el("details");raw.append(el("summary",I18N.term("자세 평가 조건·출처")),el("pre",JSON.stringify(row.pose_assessment,null,2)));panel.append(raw);}
    detail.append(panel);
  }
  const original = el("details");
  original.append(el("summary", I18N.term("원본 모델 ID·서열")), el("p", id, "sequence"), el("p", (row.sequence ? row.sequence : I18N.term("서열 미기록")), "sequence"));
  detail.append(original);
  if (state.result.mode !== "complex") detail.append(el("p", I18N.format("품질 적격 (결합 부위 제외): {0} · 실행 당시 기록 적격: {1}", [I18N.term(decisionCaption(row.quality_eligible)), I18N.term(decisionCaption(row.final_eligible))])));
  if (row.quality_eligibility_source === "hotspot_only_exclusion_removed")
    detail.append(el("p", I18N.term("실행 당시 결합 부위 조건만으로 제외됐습니다. 나머지 기록 기준은 통과했으며 원본 판정·선별 목록은 보존됩니다.")));
  warnings(detail.appendChild(el("div")), [
    ...(row.primary_pair_warning ? [row.primary_pair_warning] : []),
    ...(row.exclusion_reasons || []).map((r) => "기록 제외 사유: " + reportReasonLabel(r) + " (" + r + ")"),
    ...(recordedEvaluationError(row) ? ["후보 평가 오류: " + recordedEvaluationError(row)] : []),
    ...(row.missing_metrics?.length
      ? ["미평가 지표: " + row.missing_metrics.map(key => metricCaption(key, row, state.result?.mode)).join(", ")]
      : []),
  ]);
  const dl = el("dl");
  if (row.structure_stage === "rf2") detail.append(el("p", I18N.format("RF2 설계 단계 구조 · PAE {0} Å · pLDDT {1} · 설계–RF2 RMSD {2} Å. 최종 복합체 재예측 지표와 구분하세요.", [fmt(row.rf2_pae), fmt(row.rf2_plddt), fmt(row.rmsd)]), "muted"));
  for (const key of metricKeys) {
    const term = el("dt", I18N.term(metricCaption(key, row, state.result.mode)));term.dataset.columnKey = key;
    dl.append(term, el("dd", row[key] == null ? I18N.term("미평가") : fmt(row[key])));
  }
  detail.append(dl);
  const confidenceScope=el("details");confidenceScope.append(el("summary",I18N.term("ipSAE·ipTM 적용 체인과 출처")));
  confidenceScope.append(el("p", I18N.format("ipSAE 적용 체인: {0}–{1} · {2}", [(row.ipsae_chain_a ? row.ipsae_chain_a : I18N.term("미기록")), (row.ipsae_chain_b ? row.ipsae_chain_b : I18N.term("미기록")), ["selected_chain_pair","legacy_first_recorded_pair"].includes(row.ipsae_scope) ? I18N.term("표시된 체인 쌍") : I18N.term("범위 미기록·상세 원문 확인")])));
  confidenceScope.append(el("p",I18N.term(state.result?.mode === "complex" ? "요약 ipSAE는 명시한 체인쌍 또는 검토 순서의 첫 쌍입니다. 전체 평균·최솟값과 구분하고 모든 체인쌍의 결과는 상세 표·CSV에서 확인하세요. ipTM은 기록된 적용 범위로 해석하며, 범위가 없으면 전체 복합체 값으로 추정하지 않습니다." : "일반 복합체의 요약 ipSAE는 기본적으로 검토한 체인 순서의 첫 쌍이며, 체인쌍을 명시한 실행에서는 해당 쌍입니다. 전체 평균·최솟값과 구분하고 모든 체인 쌍의 결과는 상세 표·CSV에서 확인합니다. ipTM 전체 값과 선택 표적–VHH 쌍 값은 구분하며, 범위가 없으면 전체 복합체로 추정하지 않습니다."),"muted"));
  if (state.result?.mode === "complex") {
    const selection = row.ipsae_selection === "explicit_primary_pair" ? "명시한 체인쌍" : row.ipsae_selection === "first_pair_in_reviewed_chain_order" ? "검토 순서의 첫 체인쌍" : "선택 기준 미기록";
    confidenceScope.append(el("p", I18N.format("요약 쌍 선택: {0} · 유한한 기록값의 최솟값 {1} / 최댓값 {2} · 평가 쌍 {3} / 전체 쌍 {4}. 미평가 체인쌍은 최솟값·최댓값에서 제외됩니다.", [selection, fmt(row.min_pair_ipsae), fmt(row.max_pair_ipsae), fmt(row.pair_ipsae_assessed_count), fmt(row.pair_ipsae_total_count)])));
  }
  confidenceScope.append(el("pre",JSON.stringify(Object.fromEntries(Object.entries(row).filter(([key])=>/^ipsae_(scope|chain|source|selection)|^iptm_(scope|source|unavailable)|^(primary_pair|(max|min)_pair_ipsae|pair_ipsae_)/.test(key))),null,2)));detail.append(confidenceScope);
  const methodReceipts = Object.entries(row.metric_provenance || {});
  if (methodReceipts.length) {
    const methods = el("details");
    methods.append(el("summary", I18N.term("pDockQ·pDockQ2 계산법과 기존 저장값")));
    for (const [metric, receipt] of methodReceipts) {
      if (receipt.status === "not_assessed") continue;
      methods.append(el("p", I18N.format("{0}: {1}", [metric, (receipt.recorded_method ? receipt.recorded_method : I18N.term("계산법 미기록"))]), "sequence"));
      if (receipt.caveat) methods.append(el("p", receipt.caveat));
      if (receipt.effective_method && receipt.effective_method !== receipt.recorded_method)
        methods.append(el("p", receipt.effective_method, "sequence"));
    }
    detail.append(methods);
  }
  detail.append(el("p", I18N.term("PAE는 Å 단위입니다. pDockQ2는 CA 8 Å 접촉과 방향별 PAE를 사용하며 두 체인 중 최소값을 표시합니다. AlphaFold-Multimer에서 보정됐고 Boltz·Chai·OpenDDE 값은 탐색용입니다. 예측 지표는 실제 결합이나 KD를 입증하지 않습니다."), "muted"));
  detail.append(el("p", I18N.term("종합점수는 실행 당시 배치의 상대 점수입니다. 정규화 범위는 저장된 ranking_policy를 확인하세요. 독립 비교는 엔진별로 계산하며, 일부 후보만 다른 엔진으로 재평가하는 기존 경로는 혼합 배치로 기록됩니다. 선별 순위는 적격 게이트와 다양성 제한을 추가 적용합니다. ESM·AntiFold는 서열/폴딩 prior입니다. dev_severe는 심한 5종 게이트, dev_flags는 전체 평가 위험 수 가중치입니다. N/O 근접·반대 전하 접촉·충돌의 단위는 원자쌍이며, 확인된 수소결합 수나 원소별 반데르발스 판정이 아닙니다."), "muted"));
  detail.append(el("p", I18N.term("같은 seed라도 엔진·장치 전체의 비트 단위 재현을 보장하지 않습니다. 엄격한 CUDA 결정론 설정과 동일 환경 반복 검증은 Chai 경로에 한정됩니다. 생성 단계의 결합 부위 OFF/ON을 동일 난수 상태로 가정하지 않습니다."), "muted"));
  if (row.threshold_checks !== undefined) {
    const facts = el("details"),
      caption = el("summary", I18N.term("지표 해석·실행 당시 선별 기준"));
    facts.append(caption);
    table(
      facts.appendChild(el("div", undefined, "scroll")),
      (Array.isArray(row.threshold_checks)?row.threshold_checks:[]).filter(c=>c&&typeof c==="object"&&!Array.isArray(c)).map((c) => ({
        지표: metricCaption(c.metric, row),
        값: c.value,
        조건: c.operator,
        기준: c.threshold,
        통과: c.passed === true ? "통과" : c.passed === false ? "미통과" : "미평가",
        출처: c.threshold_source,
      })),
      ["지표", "값", "조건", "기준", "통과", "출처"],
    );
    facts.append(
      el(
        "p",
        I18N.term("ipSAE는 계면 PAE 기반 구조 일관성, ipTM은 상대 배치 신뢰도입니다. PAE는 Å 단위 오차입니다. 이 값은 결합 확률·KD가 아닙니다."),
      ),
    );
    const rawChecks=el("details");
    rawChecks.append(el("summary",I18N.term("원본 판정 기록")),el("pre",JSON.stringify(row.threshold_checks,null,2)));
    facts.append(rawChecks);
    detail.append(facts);
  }

  const cdr = Object.fromEntries(
    Object.entries(row).filter(([k]) => /cdr|dev_|mutation|risk/i.test(k)),
  );
  if (Object.keys(cdr).length) {
    const details = el("details");
    details.append(
      el("summary", I18N.term("CDR·개발가능성·변이 상세 값")),
      el(
        "pre",
        JSON.stringify(
          {
            cdr: row.cdr_details,
            developability: row.developability_details,
            recorded: cdr,
          },
          null,
          2,
        ),
      ),
    );
    detail.append(details);
  }
  const observations = state.observations.filter((o) => o.candidate_id === id);
  options($("observation-select"), [
    { value: "", label: "선별된 / 대표 구조" },
    ...observations.map((o) => ({
      value: o.observation_id,
      label: `${o.engine || "engine"} · seed ${fmt(o.seed)} · ${o.observation_id}`,
    })),
  ]);
  for (const tr of $("candidate-table").querySelectorAll("tbody tr"))
    tr.classList.toggle("selected", tr.dataset.id === id);
  renderScatter();
  renderDistribution(observations);
  await loadStructure();
}
function renderDistribution(observations) {
  const groups = new Map();
  for (const row of observations) {
    const key = `${row.engine || "engine"} / seed=${fmt(row.seed)} / MSA=${row.msa || "미기록"} / restraint=${fmt(row.restraint)}`;
    if (Number.isFinite(row.iptm)) {
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row.iptm);
    }
  }
  I18N.text($("observation-note"), I18N.concat([I18N.format("현재 후보 {0}개 관측. 조건별 {1}을 따로 표시하며 서로 다른 조건을 합산하지 않습니다. ", [observations.length, I18N.term(metricCaption("iptm", observations))]), I18N.concat([...groups.keys()].flatMap((name, i) => [i ? " · " : "", I18N.format("조건 {0}: {1}", [i+1, name])]))]));
  if (window.Plotly)
    Plotly.react(
      $("distribution"),
      [...groups].map(([name, values]) => ({
        type: "box",
        name,
        y: values,
        boxpoints: "all",
        jitter: 0.15,
        pointpos: 0,
      })),
      {
        margin: { t: 20, l: 50, b: 95, r: 20 },
        // Full condition names remain in the note and hover; short axis labels
        // keep long MSA names from extending beyond a narrow report viewport.
        xaxis: { tickmode: "array", tickvals: [...groups.keys()],
          ticktext: [...groups.keys()].map((_, i) => I18N.value(I18N.format("조건 {0}", [i+1]))) },
        yaxis: { title: { text: I18N.value(I18N.term(metricCaption("iptm", observations))) } },
        annotations: groups.size
          ? []
          : [{ text: I18N.value(I18N.term("반복 관측의 ipTM 미평가")), showarrow: false }],
      },
      { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["sendChartToCloud", "sendDataToCloud", "editInChartStudio"] },
    );
}
function localizeEmptyPaeAnnotation() {
  const plot = $("pae");
  if (!window.Plotly || plot?.data?.length !== 0 || !plot.layout?.annotations?.length) return;
  const text = !state.structure ? "구조 / PAE 미평가"
    : state.structure.pae_status === "removed_by_retention" ? "PAE · 용량 정리로 삭제됨" : "PAE 미평가";
  Plotly.relayout(plot, {"annotations[0].text": I18N.value(I18N.term(text))});
}
async function loadStructure() {
  if (!state.candidate) return;
  const id = state.candidate.candidate_id,
    observation = $("observation-select").value,
    request = ++state.structureRequest;
  try {
    const loaded = offline
      ? (window.NANOCOFOLD_OFFLINE.structures || []).find(
          (s) =>
            s.candidate_id === id &&
            (!observation || s.observation_id === observation),
        )
      : await api(
          `/api/results/${state.result.id}/structure?candidate_id=${encodeURIComponent(id)}${observation ? "&observation_id=" + encodeURIComponent(observation) : ""}`,
        );
    if (
      request !== state.structureRequest ||
      state.candidate?.candidate_id !== id
    )
      return;
    state.structure = loaded;
    if (!state.structure) throw Error("이 후보의 구조는 보고서에 없습니다.");
    await renderStructure();
  } catch (e) {
    if (request !== state.structureRequest) return;
    state.structure = null;
    disposeResultViewer();
    I18N.text($("structure-note"), I18N.term(e.message));
    $("structure-details").replaceChildren();
    if (window.Plotly) {
      await Plotly.react(
        $("pae"),
        [],
        { annotations: [{ text: I18N.value(I18N.term("구조 / PAE 미평가")), showarrow: false }] },
        { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["sendChartToCloud", "sendDataToCloud", "editInChartStudio"] },
      );
      if (request === state.structureRequest) localizeEmptyPaeAnnotation();
    }
  }
}
function labelSelection(label) {
  const match = String(label).match(/^([^:]+):(-?\d+)([A-Za-z]?)$/);
  return match
    ? {
        chain: match[1],
        resi: Number(match[2]),
        ...(match[3] ? { icode: match[3] } : {}),
      }
    : {};
}
function disposeResultViewer() {
  state.viewerRequest = (state.viewerRequest || 0) + 1;
  state.viewer?.dispose(); state.viewer = null; state.viewerData = null;
  $("structure-view").replaceChildren();
  syncResultChainControls();
  for (const id of ["structure-actions", "cdr-actions"]) $(id).replaceChildren();
  I18N.text($("reference-note"), "");
  for (const id of ["residue-a", "residue-b"]) $(id).replaceChildren();
}
function resultLayers() {
  return Object.fromEntries([...document.querySelectorAll("[data-layer]")].map(node => [node.dataset.layer, node.checked]));
}
function resultDisplay() {
  return Object.fromEntries([...document.querySelectorAll("[data-viewer-display]")].map(node => [node.dataset.viewerDisplay, node.checked]));
}
function coordinatePresentation(data) {
  const source = data?.coordinate_source;
  const verified = source?.schema_version === 2 && source.raw_preserved === true &&
    source.verification?.status === "verified" && typeof data.raw_content === "string" && data.raw_content.length > 0;
  if (verified && source.display_relation === "native_identical" && data.content === data.raw_content)
    return {rawAvailable:true, caption:"검증된 원본 엔진 좌표와 표시 좌표가 동일합니다."};
  if (verified && source.display_relation === "transformed_display") {
    const transforms = {"native origin not verified":"원본 좌표 출처 미확인", "none":"변환 없음",
      "display differs from verified native bytes; transform not recorded":"검증된 원본과 다른 표시 좌표 · 변환 과정 미기록",
      "coordinate serialization and recorded numbering mapping":"좌표 직렬화와 기록된 잔기 번호 대응",
      "coordinate serialization and recorded numbering mapping; recorded reference alignment":"좌표 직렬화·기록된 잔기 번호 대응·참조 구조 정렬"};
    return {rawAvailable:true, caption:`검증된 원본 엔진 좌표를 별도로 보존합니다. 화면은 변환된 표시 좌표입니다 · ${transforms[source.display_transform] || source.display_transform || "변환 종류 미기록"}`};
  }
  if (verified)
    return {rawAvailable:true, caption:"검증된 원본 엔진 좌표를 별도로 보존합니다. 표시 좌표와 원본의 관계는 미확인입니다."};
  if (source?.verification?.status === "hash_mismatch")
    return {rawAvailable:false, caption:"저장된 표시 좌표 · 원본 좌표의 해시가 검증 기록과 불일치하여 원본 다운로드를 제공하지 않습니다."};
  return {rawAvailable:false, caption:"저장된 표시 좌표 · 이 보고서에는 검증된 원본 엔진 좌표가 없습니다. 원본이 없거나 검증 근거가 미기록입니다."};
}
function structureDataNote(data) {
  const metrics = [];
  if (Number.isFinite(data.global_iptm)) metrics.push(`전체 복합체 ipTM ${data.global_iptm.toFixed(3)}`);
  if (data.pair_metrics?.length === 1) {
    const pair = data.pair_metrics[0];
    if (Number.isFinite(pair.interface_plddt)) metrics.push(`계면 pLDDT ${pair.interface_plddt.toFixed(1)}`);
    if (Number.isFinite(pair.interface_pae)) metrics.push(`계면 PAE ${pair.interface_pae.toFixed(1)} Å`);
  }
  return coordinatePresentation(data).caption + " " + (metrics.length ? metrics.join(" · ") + ". " : "") +
    "신뢰도 지표와 PAE를 함께 확인하세요. 계산 완료는 구조 품질 통과나 실제 결합 확인을 뜻하지 않습니다.";
}
function localizedStructureDataNote(data) {
  const metrics=[];
  if (Number.isFinite(data.global_iptm)) metrics.push(I18N.format("전체 복합체 ipTM {0}", [data.global_iptm.toFixed(3)]));
  if (data.pair_metrics?.length === 1) {
    const pair=data.pair_metrics[0];
    if (Number.isFinite(pair.interface_plddt)) metrics.push(I18N.format("계면 pLDDT {0}", [pair.interface_plddt.toFixed(1)]));
    if (Number.isFinite(pair.interface_pae)) metrics.push(I18N.format("계면 PAE {0} Å", [pair.interface_pae.toFixed(1)]));
  }
  const parts=[I18N.term(coordinatePresentation(data).caption), " "];
  metrics.forEach((metric,index)=>parts.push(index ? " · " : "",metric));
  if(metrics.length)parts.push(". ");
  parts.push(I18N.term("신뢰도 지표와 PAE를 함께 확인하세요. 계산 완료는 구조 품질 통과나 실제 결합 확인을 뜻하지 않습니다."));
  return I18N.concat(parts);
}
function resultChain(role) {
  const data = state.structure;
  const id = role === "target" ? data?.target_chain : data?.binder_chain;
  return data?.chains?.find(chain => chain.id === id);
}
function syncResultChainControls() {
  const complex = state.result?.mode === "complex";
  for (const role of ["target", "binder"]) {
    const button = $("select-result-" + role), chain = resultChain(role);
    const label = role === "target" ? (complex ? "기준 체인" : "표적") : (complex ? "파트너" : "나노바디");
    I18N.text(button, I18N.term(role === "target" ? (complex ? "기준 체인 선택" : "표적 선택") : (complex ? "파트너 선택" : "나노바디 선택")));
    button.disabled = !state.viewer || !chain?.residues?.length;
    I18N.attribute(button, "title", I18N.term(chain?.residues?.length ? `체인 ${chain.id} · ${chain.residues.length}개 잔기를 선택하고 확대합니다.` : "해당 체인의 좌표가 없습니다."));
  }
}
async function focusResultChain(role) {
  const data = state.structure, viewer = state.viewer, chain = resultChain(role);
  if (!viewer || !chain?.residues?.length) throw Error("선택할 체인의 구조가 준비되지 않았습니다.");
  // Reveal a hidden chain before selecting its saved residue labels.
  const toggle = document.querySelector(`[data-layer="${chain.role}"]`);
  if (toggle) toggle.checked = true;
  await viewer.layers(resultLayers());
  if (state.structure !== data || state.viewer !== viewer) return;
  await viewer.focus(chain.residues.map(residue => residue.label));
  if (state.structure !== data || state.viewer !== viewer) return;
  I18N.text($("structure-note"), I18N.format("{0} · 체인 {1} · {2}개 잔기", [$("select-result-" + role).textContent, chain.id, chain.residues.length]));
}
async function focusStructure(labels, text, reveal = false) {
  const viewer = state.viewer, data = state.structure;
  if (!viewer) throw Error("MolStar 구조가 준비될 때까지 기다리세요.");
  await viewer.focus(labels);
  if (state.viewer !== viewer || state.structure !== data) return;
  I18N.text($("structure-note"), (text ? text : I18N.format("선택 잔기: {0}", [labels.join(" · ")])));
  if (reveal) $("structure-view").scrollIntoView({block: "center", behavior: "auto"});
}
function focusButton(label, labels, text, measurement) {
  const button = el("button", I18N.term(label), "secondary");
  button.onclick = async () => {
    try {
      const viewer = state.viewer, data = state.structure;
      await focusStructure(labels, text);
      if (state.viewer !== viewer || state.structure !== data) return;
      if (measurement) await viewer.distance(...measurement);
    } catch (error) { notice(error.message, true); }
  };
  return button;
}
async function renderStructure() {
  const data = state.structure;
  if (!data) return;
  I18N.text($("structure-data-note"), localizedStructureDataNote(data));
  if (state.viewerData !== data) {
    disposeResultViewer();
    const request = state.viewerRequest;
    state.viewerData = data;
    I18N.text($("structure-note"), I18N.term("MolStar 구조·서열 준비 중"));
    const host = el("div", undefined, "molstar-host");
    $("structure-view").replaceChildren(host);
    state.viewerReady = NanoCoFoldResultViewer.create(host, data, location => {
      if (state.structure !== data) return;
      const original = location.mapping ? `${location.mapping.author_chain || location.mapping.source_chain}:${location.mapping.source_residue || "미관측"}` : null;
      I18N.text($("structure-note"), I18N.format("{0} · 구조 author 번호{1}", [I18N.term(location.label), original ? I18N.concat([I18N.term(" · 입력 원본 "), original]) : I18N.term(" · 원본 대응은 아래 표 확인")]));
      $("residue-a").value = location.label;
    });
    let viewer;
    try { viewer = await state.viewerReady; }
    catch (error) {
      if (request !== state.viewerRequest) return;
      I18N.text($("structure-note"), I18N.concat([I18N.term("MolStar 표시 실패: "), error.message]));
      return;
    }
    if (request !== state.viewerRequest || state.structure !== data) { viewer.dispose(); return; }
    state.viewer = viewer;
    syncResultChainControls();
    if ($("structure-source")) I18N.text($("structure-source"), I18N.format("현재 모델 {0} · 체인 {1} · {2}", [data.candidate_id, data.chains.map(c=>c.id).join(", "), I18N.term(coordinatePresentation(data).caption)]));
    if ($("save-native-structure")) $("save-native-structure").disabled = !coordinatePresentation(data).rawAvailable;
    if ($("save-pae")) $("save-pae").disabled = !data.pae;
  } else if (!state.viewer) {
    try { await state.viewerReady; } catch { return; }
    if (state.structure !== data || !state.viewer) return;
  }
  await state.viewer.layers(resultLayers());
  await state.viewer.display(resultDisplay());
  if (state.structure !== data) return;
  I18N.text($("structure-note"), I18N.format("MolStar · {0} {1} · {2} {3} · 접촉 잔기쌍 {4} · 충돌 잔기쌍 {5}. 서열·PAE·아래 버튼에서 잔기를 선택하세요.", [state.result.mode === 'complex' ? I18N.term('기준 체인') : I18N.term('표적'), data.target_chain, state.result.mode === 'complex' ? I18N.term('파트너') : 'VHH', data.binder_chain, (data.contacts?.length ? data.contacts?.length : 0), (data.clashes?.length ? data.clashes?.length : 0)]));
  const residues = (data.chains || []).flatMap(c => c.residues.map(r => ({value: r.label, label: `${r.label} ${r.name}`})));
  for (const id of ["residue-a", "residue-b"]) {
    const saved = $(id).value;
    options($(id), [{value: "", label: "잔기 선택"}, ...residues]);
    if (residues.some(r => r.value === saved)) $(id).value = saved;
  }
  const reference = data.reference;
  const toggle = document.querySelector('[data-layer="reference"]');
  toggle.disabled = !reference?.available;
  I18N.text($("reference-note"), reference?.available ? I18N.format("입력 좌표 중첩: 표적 Cα {0}개 · RMSD {1} Å. {2}", [reference.matched_ca, reference.rmsd.toFixed(2), (reference.note ? reference.note : "")]) : I18N.format("입력 좌표 중첩 미평가: {0}", [I18N.term(reference?.reason || "보존된 참조 좌표 없음")]));
  const actions = $("structure-actions"); actions.replaceChildren();
  if (data.clashes?.length)
    actions.append(focusButton(`충돌 잔기쌍 · ${data.clashes.length}쌍 보기`, data.clashes.flatMap(p => [p.a, p.b])));
  if ((state.candidate.developability_details?.warnings || []).some(r => /cys/i.test(r)) && data.cysteines?.residues.length)
    actions.append(focusButton("Cys 위험 사유 · 좌표 확인", data.cysteines.residues, data.cysteines.note));
  const cdrActions = $("cdr-actions"); cdrActions.replaceChildren(el("span", (data.cdr?.note ? data.cdr?.note : I18N.term("CDR 미평가")), "muted"));
  for (const [name, region] of Object.entries(data.cdr?.ranges || {})) {
    const button = focusButton(`${name} ${region.start}–${region.end}`, region.labels);
    cdrActions.append(button);
  }
  const details = $("structure-details");
  details.replaceChildren();
  if (data.pair_metrics) {
    details.append(el("h3", I18N.term("체인 쌍별 지표")));
    table(details.appendChild(el("div")), data.pair_metrics, ["chain_a", "chain_b", "iptm_a_to_b", "iptm_b_to_a", "ipsae", "interface_pae", "n_clashes"]);
  }
  if (reference?.chains) {
    details.append(el("h3", I18N.term("기준 체인 정렬 후 참조 비교")));
    details.append(el("p", I18N.format("기준 체인 {0} → 참조 체인 {1} · 전체 복합체 최적 중첩 RMSD {2} Å. 화면은 기준 체인 정렬 좌표입니다.", [reference.anchor_chain || I18N.term("미기록"), reference.reference_anchor_chain || reference.chain_permutation_map?.[reference.anchor_chain] || I18N.term("미기록"), fmt(reference.complex_rmsd ?? reference.all_chain_ca_rmsd_angstrom)])));
    table(details.appendChild(el("div")), reference.chains, ["chain", "reference_chain", "matched_ca", "ca_rmsd_angstrom", "interest_ca_rmsd_angstrom"]);
    details.append(el("p", I18N.format("5 Å 접촉 회복률: {0} ({1}/{2})", [fmt(reference.fnat_5A), reference.recovered_contacts_5A, reference.reference_contacts_5A])));
  }
  details.append(el("h3", I18N.term("접촉")));
  table(details.appendChild(el("div")), data.contacts || [], [
    "a",
    "b",
    "distance",
    "kind",
  ]);
  details.append(el("h3", I18N.term("충돌 잔기쌍")),el("p",I18N.term("표의 충돌 원자쌍 수와 아래 중복 제거된 잔기쌍 수는 집계 단위가 다릅니다. 각 잔기쌍의 가장 가까운 원자와 해당 cutoff를 표시합니다."),"muted"));
  for (const pair of data.clashes || []) {
    const text = `${pair.a}:${pair.atom_a} – ${pair.b}:${pair.atom_b} · ${pair.distance.toFixed(2)} Å < ${pair.cutoff} Å (${pair.kind})`;
    details.append(focusButton(text, [pair.a, pair.b], text, [pair.a, pair.b, pair.atom_a, pair.atom_b]));
  }
  if (state.result.mode !== "complex") details.append(el("h3", I18N.term("VHH Cys SG–SG 거리")), el("p", (data.cysteines?.note ? data.cysteines?.note : I18N.term("미평가"))));
  for (const pair of data.cysteines?.pairs || []) {
    const text = `${pair.a}:SG – ${pair.b}:SG · ${pair.distance == null ? "SG 미관측" : pair.distance.toFixed(3) + " Å"}`;
    details.append(focusButton(text, [pair.a, pair.b], text, pair.distance == null ? null : [pair.a, pair.b, "SG", "SG"]));
  }
  if (data.cysteines?.missing_sg.length) details.append(el("p", I18N.concat([I18N.term("SG 없는 Cys: "), data.cysteines.missing_sg.join(", ")])));
  if (data.target_region) details.append(el("h3", I18N.term("분석 구간·결합 부위 제외 영역")), el("pre", JSON.stringify(data.target_region, null, 2)));
  details.append(el("h3", I18N.term("원본 잔기 대응")));
  const mapping = data.residue_mapping || [];
  if (mapping.length)
    table(details.appendChild(el("div")), mapping, Object.keys(mapping[0]));
  else
    details.append(
      el(
        "p",
        I18N.term("별도 입력 매핑 없음. 위 3D와 PAE는 구조의 author 체인·번호를 표시합니다."),
      ),
    );
  warnings(details.appendChild(el("div")), data.issues);
  if (window.Plotly) {
    const pae = data.pae;
    await Plotly.react(
      $("pae"),
      pae
        ? [
            {
              type: "heatmap",
              z: pae.values,
              x: pae.labels,
              y: pae.labels,
              colorscale: "Blues", zmin: 0, zmax: 30,
              reversescale: true,
              colorbar: { title: { text: "Å" }, thickness: 12, tickfont: { size: 12 } },
              hovertemplate:
                I18N.value(I18N.term("정렬 기준 (행) %{y}<br>평가 잔기 (열) %{x}<br>PAE %{z:.2f} Å<extra></extra>")),
            },
          ]
        : [],
      {
        font: { size: 14 },
        margin: { t: 15, b: 70, l: 50, r: 10 },
        xaxis: { automargin: true, nticks: 5, tickangle: -45, title: { text: I18N.value(I18N.term("열 · 평가 잔기")), font: { size: 14 }, standoff: 12 }, type: "category" },
        yaxis: { automargin: true, nticks: 6, type: "category", autorange: "reversed", title:{text:I18N.value(I18N.term("행 · 정렬 기준 잔기")),font:{size:14},standoff:8} },
        shapes: pae ? pae.labels.flatMap((label,i)=>{
          if(!i||label.split(":")[0]===pae.labels[i-1].split(":")[0])return [];
          return [{type:"line",xref:"x",yref:"paper",x0:i-.5,x1:i-.5,y0:0,y1:1,line:{color:"#586b78",width:1}},
                  {type:"line",xref:"paper",yref:"y",x0:0,x1:1,y0:i-.5,y1:i-.5,line:{color:"#586b78",width:1}}];
        }) : [],
        annotations: pae ? [] : [{ text: data.pae_status === "removed_by_retention" ? I18N.value(I18N.term("PAE · 용량 정리로 삭제됨")) : I18N.value(I18N.term("PAE 미평가")), showarrow: false }],
      },
      { responsive: true, displaylogo: false, displayModeBar: false },
    );
    if (!pae && state.structure === data) localizeEmptyPaeAnnotation();
    const resetPae = $("pae-reset");
    if (resetPae) resetPae.onclick = () => Plotly.relayout($("pae"), {"xaxis.autorange":true, "yaxis.autorange":"reversed"});
    $("pae").removeAllListeners?.("plotly_click");
    $("pae").on("plotly_click", event => {
      const point = event.points?.[0];
      if (!point) return;
      const layout = $("pae")._fullLayout;
      const span = axis => axis?.range ? Math.abs(axis.range[1]-axis.range[0]) : pae?.labels.length || 0;
      if (pae && (span(layout?.xaxis)>60 || span(layout?.yaxis)>60)) {
        const x = pae.labels.indexOf(point.x), y = pae.labels.indexOf(point.y);
        if (x<0 || y<0) return;
        Plotly.relayout($("pae"), {"xaxis.range":[Math.max(-.5,x-15.5),Math.min(pae.labels.length-.5,x+15.5)],
          "yaxis.range":[Math.min(pae.labels.length-.5,y+15.5),Math.max(-.5,y-15.5)]})
          .catch(error => notice(error.message, true));
        notice(I18N.term("PAE 구간을 확대했습니다. 셀을 다시 선택하면 정확한 두 잔기로 이동합니다."));
        return;
      }
      focusStructure([point.x, point.y], `PAE ${Number(point.z).toFixed(2)} Å · ${point.x} / ${point.y}`, true).catch(error => notice(error.message, true));
    });
  }
}
async function exportResult() {
  const format = $("export-format").value;
  if (offline) {
    const data = window.NANOCOFOLD_OFFLINE;
    const blob =
      format === "fasta"
        ? new Blob(
            [
              state.rows
                .filter((r) => r.sequence)
                .map((r) => `>${r.candidate_id}\n${r.sequence}\n`)
                .join(""),
            ],
            { type: "text/plain" },
          )
        : new Blob([JSON.stringify({ ...data, filters: filters(), provenance: {
            ...data.provenance, filters: filters(), filtered_ids: state.filtered.map(r => r.candidate_id),
          } }, null, 2)], {
            type: "application/json",
          });
    saveBlob(
      blob,
      format === "fasta" ? "candidates.fasta" : "exploration.json",
    );
    return;
  }
  const response = await fetch(`/api/results/${state.result.id}/export`, {
    method: "POST",
    headers: {
      "X-Platform-Token": window.PLATFORM_TOKEN,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ format, filters: filters() }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw Error(error.error || "내보내기 실패");
  }
  const name =
    response.headers
      .get("content-disposition")
      ?.match(/filename="([^"]+)"/)?.[1] || "results." + format;
  saveBlob(await response.blob(), name);
  notice("전체 후보와 선택 조건을 저장했습니다.");
}
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob),
    a = el("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function renderDiagnostics(diagnosis) {
  if (!diagnosis) return;
  const output = $("diagnostics-output");
  output.replaceChildren(
    el("h3", diagnosis.ok ? I18N.term("실행 준비 통과") : I18N.term("실행 준비 미충족")),
  );
  if (!Array.isArray(diagnosis.checks) || !diagnosis.checks.length) {
    output.replaceChildren(el("p", I18N.term("환경 확인 세부 기록이 아직 없습니다. 작업·로그에서 진행 상태나 종료 사유를 확인하세요.")));
    return;
  }
  const rows = diagnosis.checks.map((c) => ({
    항목: c.name,
    필수: c.required,
    준비: c.ok,
    조치: c.ok
      ? "확인 완료"
      : c.name.includes("wt_structure")
        ? "WT VHH 구조를 입력하거나 AntiFold를 끄세요."
        : c.name.includes("cuda") || c.name === "gpu"
          ? "GPU 드라이버·CUDA·계산 호스트 환경을 확인하세요."
          : "선택한 모드의 설치 프로필을 설치하고 작업 로그를 확인하세요.",
    근거: JSON.stringify(c.details || c.detail || c.evidence || c),
  }));
  table(output.appendChild(el("div", undefined, "scroll")), rows, [
    "항목",
    "필수",
    "준비",
    "조치",
    "근거",
  ]);
}
function setupExplorer() {
  for (const id of [
    "candidate-search",
    "eligibility",
    "eligibility-basis",
    "hotspot-filter",
    "hotspot-min",
    "sort-metric",
    "engine-filter",
    "descending",
  ])
    $(id).addEventListener(
      id === "candidate-search" ? "input" : "change",
      renderCandidates,
    );
  for (const id of ["plot-x", "plot-y"])
    $(id).addEventListener("change", renderScatter);
  $("observation-select").addEventListener("change", loadStructure);
  $("viewer-candidate-select").addEventListener("change", () => {
    const id = $("viewer-candidate-select").value;
    if (id) selectCandidate(id);
    else clearCandidate();
  });
  for (const [id, offset] of [["previous-candidate", -1], ["next-candidate", 1]])
    $(id).addEventListener("click", async () => {
      try {
        await stepCandidate(offset);
      } catch (error) {
        notice(error.message, true);
      } finally {
        // Navigation availability follows the current selection and filters,
        // even when an older structure request finishes after they change.
        syncCandidateNavigation();
      }
    });
  for (const checkbox of document.querySelectorAll("[data-layer]"))
    checkbox.addEventListener("change", () => {
      if (checkbox.checked && ["hotspots", "contacts", "clashes"].includes(checkbox.dataset.layer)) {
        document.querySelector('[data-viewer-display="residues"]').checked = true;
        state.viewer?.display(resultDisplay()).catch(error => notice(error.message, true));
      }
      state.viewer?.layers(resultLayers()).catch(error => notice(error.message, true));
    });
  for (const checkbox of document.querySelectorAll("[data-viewer-display]"))
    checkbox.addEventListener("change", () => {
      if(checkbox.dataset.viewerDisplay==="residues"&&!checkbox.checked) {
        const layers=residueLayers(resultLayers(),false);
        for(const key of ["hotspots","contacts","clashes"]) {const node=document.querySelector(`[data-layer="${key}"]`);if(node)node.checked=layers[key];}
        state.viewer?.layers(layers).catch(error=>notice(error.message,true));
      }
      state.viewer?.display(resultDisplay()).catch(error => notice(error.message, true));
    });
  $("candidate-table").addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const buttons = [...$("candidate-table").querySelectorAll("tbody button")],
      index = buttons.indexOf(document.activeElement);
    const next = buttons[index + (event.key === "ArrowDown" ? 1 : -1)];
    if (next) {
      event.preventDefault();
      next.focus();
      next.click();
    }
  });
  action("fit-result-structure", () => state.viewer?.fit());
  for (const role of ["target", "binder"])
    action("select-result-" + role, () => focusResultChain(role));
  action("clear-result-selection", async () => {
    await state.viewer?.clearSelection();
    I18N.text($("structure-note"), I18N.term("잔기 선택 강조를 해제했습니다."));
  });
  action("save-result-structure", () => {
    if (!state.structure?.content) throw Error("선택 모델의 구조가 없습니다.");
    const data = state.structure;
    saveBlob(new Blob([data.content], {type:"text/plain;charset=utf-8"}),
      `${data.candidate_id.replace(/[^A-Za-z0-9_.-]/g, "_")}_${(data.observation_id || "representative").replace(/[^A-Za-z0-9_.-]/g, "_")}.${data.format === "pdb" ? "pdb" : "cif"}`);
  });
  action("save-native-structure", () => {
    const data=state.structure;
    if (!coordinatePresentation(data).rawAvailable) throw Error("이 보고서에 검증된 원본 엔진 좌표가 없습니다. 출처·원본 산출물을 확인하세요.");
    saveBlob(new Blob([data.raw_content],{type:"text/plain;charset=utf-8"}),
      `${data.candidate_id.replace(/[^A-Za-z0-9_.-]/g,"_")}_native.${data.raw_format||"cif"}`);
  });
  action("save-pae", () => {
    const data=state.structure;
    if (!data?.pae) throw Error("이 모델의 PAE는 미제공입니다. 다른 모델의 값을 대신 쓰지 않습니다.");
    saveBlob(new Blob([JSON.stringify(data.pae,null,2)],{type:"application/json"}),
      `${data.candidate_id.replace(/[^A-Za-z0-9_.-]/g,"_")}_PAE.json`);
  });
  action("focus-result-residues", () => {
    const labels = [$("residue-a").value, $("residue-b").value].filter(Boolean);
    if (!labels.length) throw Error("잔기를 선택하세요.");
    return focusStructure(labels);
  });
  action("measure-result-distance", async () => {
    const a = $("residue-a").value, b = $("residue-b").value;
    if (!a || !b || a === b) throw Error("서로 다른 두 잔기를 선택하세요.");
    const viewer = state.viewer, data = state.structure;
    const residues = data?.chains.flatMap(c => c.residues) || [];
    const caA = residues.find(r => r.label === a)?.ca, caB = residues.find(r => r.label === b)?.ca;
    if (!caA || !caB) throw Error("Cα 좌표가 없는 잔기입니다.");
    const distance = Math.hypot(...caA.map((v, i) => v - caB[i]));
    await focusStructure([a, b], `${a}:CA – ${b}:CA · ${distance.toFixed(3)} Å`);
    if (state.viewer !== viewer || state.structure !== data) return;
    await viewer.distance(a, b);
  });
  action("export-result", exportResult);
}
let activeBatch = null, batchBusy = false, batchSelected = [], batchInputRevision = 0;
function syncBatchFields() {
  const json = $("batch-format").value === "json", mode = $("batch-mode").value;
  for (const node of document.querySelectorAll("[data-batch-field]"))
    node.hidden = json || (node.dataset.batchField !== "name" && node.dataset.batchField !== mode);
  $("batch-mode").closest("label").hidden = json;
  I18N.text($("batch-format-help"), json ? I18N.term("JSON 안의 목적·이름·서열·조건을 사용합니다. 공통 입력 칸은 필요하지 않습니다.") : I18N.term("목적에 필요한 공통 입력만 아래에 표시됩니다. 표적마다 다른 조건은 JSON으로 지정하세요."));
}
function resetBatchReview(revokeConsent = true) {
  batchInputRevision++;
  activeBatch = null; batchSelected = [];
  if (revokeConsent) {
    $("batch-api-consent").checked = false;
    $("batch-tutorial-summary").hidden = true;
  }
  $("batch-submit").disabled = true;
  I18N.text($("batch-submit"), I18N.term("3. 선택한 분석 실행"));
  $("batch-cancel").disabled = true;
  I18N.text($("batch-summary"), I18N.term("입력을 준비한 뒤 검토하세요. 아직 계산을 제출하지 않았습니다."));
  I18N.text($("batch-next-step"), I18N.term("2단계 · 입력이 바뀌었습니다. 필요한 MSA 동의를 확인하고 배치 입력 검토를 누르세요."));
  $("batch-rows").replaceChildren();
  syncBatchFields();
}
async function loadTutorial(mode) {
  if (batchBusy) return;
  await batchAction("공개 입력을 불러오고 있습니다. 계산은 시작하지 않습니다", async () => {
    const revision = ++batchInputRevision;
    const example = await api("/api/tutorials/" + encodeURIComponent(mode));
    if (revision !== batchInputRevision) return;
    $("batch-mode").value = example.mode;
    $("batch-format").value = "json";
    $("batch-content").value = JSON.stringify(example.payload, null, 2);
    $("batch-name").value = example.payload.name;
    $("batch-file").value = "";
    $("batch-use-config").checked = false;
    for (const id of ["batch-library", "batch-wt", "batch-partners"]) $(id).value = "";
    resetBatchReview();
    $("batch-content-details").open = false;
    const summary = $("batch-tutorial-summary");
    I18N.text(summary, I18N.concat([example.title + " · " + example.description, I18N.term(" · GPU 계산 미제출")]));
    summary.hidden = false;
    I18N.text($("batch-next-step"), example.requires_api_consent ? I18N.term("2단계 · 공개 서열의 외부 MSA 전송 동의를 직접 체크한 뒤 배치 입력 검토를 누르세요.") : I18N.term("2단계 · 예제 입력이 준비되었습니다. 배치 입력 검토를 누르세요. 이 예제는 외부 MSA 전송이 필요 없습니다."));
    I18N.text($("tutorial-status"), I18N.term("공개 예제를 불러왔습니다. 다음 화면에서 입력을 검토하세요."));
    view("batch");
    (example.requires_api_consent ? $("batch-api-consent") : $("batch-review")).focus();
  });
}
function renderBatch(batch) {
  if (activeBatch?.id !== batch.id) batchSelected = batch.selected || batch.rows.filter(r => !r.error).map(r => r.id);
  activeBatch = batch;
  I18N.text($("batch-summary"), I18N.concat([I18N.concat([batch.name + " · " + batch.rows.length, I18N.term("개 · 계산 완료 ")]), batch.rows.filter(r => r.job?.state === "complete").length]));
  const body=el("tbody"), target=el("table"), head=el("thead"), header=el("tr");
  for(const label of ["실행 선택", "항목", "입력 검토", "계산", "결과"]) header.append(el("th",I18N.term(label)));
  head.append(header); target.append(head,body);
  const labels={queued:"대기",running:"실행 중",complete:"완료",failed:"실패",partial_failure:"일부 실패",interrupted:"중단",cancelled:"취소",budget_exhausted:"시간 한도",cancelling:"취소 중"};
  for(const row of batch.rows){
    const line=el("tr"), choice=el("td"), check=el("input"), status=el("td",(row.submission_error||labels[row.job?.state] ? (row.submission_error ? row.submission_error : I18N.term(labels[row.job?.state])) : I18N.term("미제출")));
    check.type="checkbox";I18N.attribute(check, "aria-label", I18N.concat([row.id, I18N.term(" 실행 선택")]));check.checked=batchSelected.includes(row.id);check.disabled=batchBusy||!!batch.selected||!!row.error;
    check.onchange=()=>{batchSelected=check.checked?[...batchSelected,row.id]:batchSelected.filter(id=>id!==row.id);renderBatch(activeBatch);};choice.append(check);
    if(row.error||row.submission_error){line.style.background="#fff1ef";I18N.attribute(line, "aria-label", I18N.concat([row.id, I18N.term(" 오류")]));}
    for(const action of row.job?.can_resume?["resume"]:row.job&&['queued','running','cancelling'].includes(row.job.state)?["cancel"]:[]){const button=el("button",action==="resume" ? I18N.term("항목 재개") : I18N.term("항목 취소"));button.disabled=batchBusy;button.onclick=()=>batchAction("항목 요청 처리 중",async()=>{await api("/api/jobs/"+row.job_id+"/"+action,{additional_budget_seconds:36000},{timeoutMs:30000});renderBatch(await api("/api/batches/"+batch.id,undefined,{timeoutMs:15000}));});status.append(button);}
    line.append(choice,el("td",row.id),el("td",(row.error ? row.error : I18N.term("검토 완료"))),status);
    const result=el("td");if(row.job?.result_available===true&&["complete","failed","partial_failure","cancelled","interrupted","budget_exhausted"].includes(row.job.state)){const partial=row.job.state!=="complete",link=el("a",I18N.term(partial?"부분 결과 열기":"결과 열기"));link.href="/?result="+encodeURIComponent(row.job_id);link.target="_blank";link.rel="noopener";result.append(link);if(partial)result.append(el("p",I18N.term("중단 전 저장된 부분 결과입니다. 전체 분석 완료를 뜻하지 않습니다.")));}else I18N.text(result, "—");line.append(result);body.append(line);
  }
  $("batch-rows").replaceChildren(target);
  const retryMissing = Boolean(batch.selected) && batch.rows.some(row => batch.selected.includes(row.id) && !row.job_id && !row.job && !row.error);
  $("batch-submit").disabled=batchBusy||(Boolean(batch.selected)&&!retryMissing)||!batchSelected.length||batch.rows.some(r=>batchSelected.includes(r.id)&&r.error);
  I18N.text($("batch-submit"), batch.selected ? I18N.term(retryMissing ? "미제출 항목 다시 제출" : "이미 제출된 분석") : I18N.concat([I18N.concat([I18N.term("3. 선택한 "), batchSelected.length]), I18N.term("개 분석 실행")]));
  I18N.text($("batch-next-step"), batch.selected && retryMissing ? I18N.term("일부 항목을 제출하지 못했습니다. 항목별 오류를 확인한 뒤 미제출 항목 다시 제출을 누르세요. 이미 등록된 작업은 같은 작업으로 연결됩니다.") : batch.selected ? I18N.term("4단계 · 계산을 제출했습니다. 완료된 행의 결과 열기를 누르세요. 실행 중 상태는 자동으로 갱신됩니다.") : batch.rows.some(row => row.error) ? I18N.term("2단계 · 검토 오류가 있습니다. 아래 항목별 오류를 확인하고 입력을 수정한 뒤 다시 검토하세요.") : I18N.term("3단계 · 입력 검토 완료, 계산 미제출입니다. 실행할 항목을 확인하고 선택한 분석 실행을 누르세요."));
  $("batch-cancel").disabled=batchBusy||!batch.rows.some(r=>r.job&&!['complete','failed','cancelled','partial_failure','budget_exhausted','interrupted'].includes(r.job.state));
}
async function refreshBatchHistory(){const data=await api("/api/batches");const node=$("batch-history");node.replaceChildren();for(const batch of data.batches){const button=el("button",I18N.concat([batch.name+" · "+batch.rows.length, I18N.term("개")]));button.onclick=async()=>renderBatch(await api("/api/batches/"+batch.id,undefined,{timeoutMs:15000}));node.append(button);}if(!data.batches.length)I18N.text(node, I18N.term("아직 검토한 배치가 없습니다."));}
async function batchAction(message, fn) {
  if (batchBusy) return;
  batchBusy = true;
  const started = Date.now(), update = () => { I18N.text($("batch-activity"), I18N.concat([message + " · " + Math.floor((Date.now() - started) / 1000), I18N.term("초")])); };
  const editable = ["batch-review", "batch-file", "batch-content", "batch-mode", "batch-format", "batch-name", "batch-library", "batch-wt", "batch-partners", "batch-api-consent", "batch-use-config", "batch-example", "load-tutorial", "tutorial-mode"];
  update(); const timer = setInterval(update, 1000);
  for (const id of [...editable, "batch-submit", "batch-cancel"]) $(id).disabled = true;
  try {
    await fn(); I18N.text($("batch-activity"), I18N.term("요청 완료"));
  } catch (error) {
    notice(error.message, true);
    I18N.text($("batch-activity"), I18N.concat([I18N.term("요청 실패 · "), error.message]));
    $("batch-content-details").open = true;
    $("batch-content").setAttribute("aria-invalid", "true");
    $("batch-content").focus();
  } finally {
    clearInterval(timer); batchBusy = false;
    for (const id of editable) $(id).disabled = false;
    if (activeBatch) renderBatch(activeBatch);
  }
}

function initBatch(){
  setInterval(async()=>{if(batchBusy||document.hidden||$("view-batch").hidden||!activeBatch)return;const id=activeBatch.id;try{const next=await api("/api/batches/"+id);if(activeBatch?.id===id&&!batchBusy)renderBatch(next);}catch(e){I18N.text($("batch-activity"), I18N.concat([I18N.term("마지막 상태를 표시합니다. "), e.message]));}},4000);
  const reset=()=>resetBatchReview();
  syncBatchFields();
  $("batch-api-consent").addEventListener("input", () => resetBatchReview(false));
  // The purpose wizard owns the public-example action.
  for(const id of ["batch-mode","batch-format","batch-content","batch-name","batch-library","batch-wt","batch-partners","batch-use-config"])$(id).addEventListener("input",reset);
  $("batch-file").onchange=async()=>{
    const file=$("batch-file").files[0]; if(!file)return;
    reset(); const revision=batchInputRevision;
    if(file.size>20*1024*1024){notice("파일은 20MB 이하입니다.",true);return;}
    try {
      const content=await file.text(); if(revision!==batchInputRevision)return;
      $("batch-content").value=content;
      if(file.name.toLowerCase().endsWith(".json"))$("batch-format").value="json";
      $("batch-content-details").open=true; syncBatchFields();
    } catch(error) {notice("파일을 읽지 못했습니다: "+error.message,true);}
  };
  $("batch-example").onclick=()=>{$("batch-format").value="json";$("batch-content").value=JSON.stringify({version:1,mode:"design",name:"CPU 배치 데모",mock:true,targets_fasta:">target-1\nACDEFGHIKLMNPQRSTVWY\n>target-2\nACDEFGHIKLMNPQRSTVWY\n",config:{design:{num_designs:1},screen:{tier2_n:1},rank:{final_n:1}}},null,2);reset();};
  $("batch-review").onclick=()=>batchAction("배치 입력을 검토하고 있습니다. 계산은 시작하지 않습니다",async()=>{let payload;if($("batch-format").value==="json")payload=JSON.parse($("batch-content").value);else payload={version:1,mode:$("batch-mode").value,name:$("batch-name").value,targets_fasta:$("batch-content").value,...($("batch-mode").value==="screen"?{vhh_fasta:$("batch-library").value}:{}),...($("batch-mode").value==="mature"?{wt_fasta:$("batch-wt").value}:{}),...($("batch-mode").value==="complex"?{partners_fasta:$("batch-partners").value}:{})};if($("batch-use-config").checked){if(!state.input)throw Error("단일 분석에서 조건을 먼저 검토하세요.");payload.config=state.input.config;}payload.api_consent=$("batch-api-consent").checked;renderBatch(await api("/api/batches/review",payload));$("batch-content").removeAttribute("aria-invalid");await refreshBatchHistory();});
  $("batch-submit").onclick=()=>batchAction("검토한 배치를 제출하고 있습니다",async()=>{if(!activeBatch)throw Error("배치를 먼저 검토하세요.");renderBatch(await api("/api/batches/"+activeBatch.id+"/submit",{review_sha256:activeBatch.review_sha256,selected:activeBatch.selected||batchSelected,...(activeBatch.budget_seconds!==undefined?{budget_seconds:activeBatch.budget_seconds}:{})},{timeoutMs:30000}));});
  $("batch-refresh").onclick=async()=>{try{if(activeBatch)renderBatch(await api("/api/batches/"+activeBatch.id));await refreshBatchHistory();}catch(e){notice(e.message,true);}};
  $("batch-cancel").onclick=()=>batchAction("미완료 작업 취소 요청 중",async()=>renderBatch(await api("/api/batches/"+activeBatch.id+"/cancel",{},{timeoutMs:30000})));
}

function platformStartup(status, error = "") {
  const panel = $("platform-startup"); if (!panel) return;
  document.body.dataset.startup = status;
  panel.hidden = status === "ready";
  I18N.text($("platform-startup-message"), status === "failed" ? I18N.concat([I18N.term("화면을 준비하지 못했습니다. "), error]) : I18N.term("화면을 준비하고 있습니다."));
  const retry = $("platform-startup-retry");
  retry.hidden = status !== "failed";
  // Reload the document after a partial boot, avoiding duplicated handlers or wizard nodes.
  retry.onclick = () => window.location.reload();
  for (const button of document.querySelectorAll("[data-view]")) button.disabled = status !== "ready";
}
async function startPlatform() {
  if (offline) {
    await boot().catch(error => notice(error.message, true));
    return;
  }
  platformStartup("loading");
  try { await boot(); platformStartup("ready"); }
  catch (error) { platformStartup("failed", error.message); notice(error.message, true); }
}

async function boot() {
  $("candidate-extra-columns").addEventListener("change", renderCandidates);
  refreshMetricCaptions([]);
  setupExplorer();
  if (offline) {
    if ($("result-retention-panel")) $("result-retention-panel").hidden = true;
    const catalogButton = document.querySelector('[data-view="catalog"]');
    if (catalogButton) catalogButton.hidden = true;
    const saved = window.NANOCOFOLD_OFFLINE.filters || {};
    $("candidate-search").value = saved.search || "";
    if (saved.sort) {
      const select = $("sort-metric");
      if (![...select.options].some(option => option.value === saved.sort)) {const option = el("option", saved.sort);option.value = saved.sort;select.append(option);}
      select.value = saved.sort;
    }
    $("descending").checked = saved.descending !== false;
    if (saved.eligibility) $("eligibility").value = saved.eligibility;
    else if (saved.eligible !== undefined)
      $("eligibility").value = String(saved.eligible);
    $("eligibility-basis").value = saved.eligibility_basis || "recorded";
    $("hotspot-filter").checked = saved.hotspot_min !== null && saved.hotspot_min !== undefined;
    if ($("hotspot-filter").checked) $("hotspot-min").value = saved.hotspot_min * 100;
    for (const [key, value] of Object.entries(saved.result_layers || {})) {
      const checkbox = document.querySelector(`[data-layer="${key}"]`);
      if (checkbox) checkbox.checked = value;
    }
    for (const [key, value] of Object.entries(saved.viewer_display || {})) {
      const checkbox = document.querySelector(`[data-viewer-display="${key}"]`);
      if (checkbox) checkbox.checked = value === true;
    }
    if (saved.plot_x) $("plot-x").value = saved.plot_x;
    if (saved.plot_y) $("plot-y").value = saved.plot_y;
    options($("result-select"), [
      {
        value: window.NANOCOFOLD_OFFLINE.summary.id,
        label: window.NANOCOFOLD_OFFLINE.summary.name,
      },
    ]);
    options($("export-format"), [
      { value: "json", label: "탐색 조건·전체 후보 JSON" },
      { value: "fasta", label: "FASTA" },
    ]);
    await loadResult(window.NANOCOFOLD_OFFLINE.summary.id);
    if (
      saved.selected_candidate_id &&
      state.filtered.some((r) => r.candidate_id === saved.selected_candidate_id)
    )
      await selectCandidate(saved.selected_candidate_id);
    if (saved.selected_observation_id) {
      $("observation-select").value = saved.selected_observation_id;
      await loadStructure();
    }
    for (const key of ["a", "b"]) if (saved.distance_selection?.[key]) $("residue-" + key).value = saved.distance_selection[key];
    if (saved.selected_residues?.length && state.viewer) await state.viewer.focus(saved.selected_residues);
    return;
  }


  initBatch();
  for (const button of document.querySelectorAll("[data-view]"))
    button.onclick = () => view(button.dataset.view);
  action("catalog-refresh", () => {state.catalogCursor = null; return refreshCatalog();});
  action("catalog-first", () => {state.catalogCursor = null; return refreshCatalog();});
  action("catalog-next", () => {state.catalogCursor = state.catalogNext; return refreshCatalog();});
  action("retention-preview", previewRetention);
  action("retention-apply", applyRetention);
  $("retention-consent").onchange = () => {$("retention-apply").disabled = !$("retention-consent").checked || !state.retentionPlan;};
  for (const id of ["mode", "input-kind", "mock"])
    $(id).addEventListener("change", fields);
  initAlternativeInputs();
  $("yaml-file").onchange = async () => {
    try {
      const parsed = await api("/api/configs/parse", {
        content: await $("yaml-file").files[0].text(),
      });
      $("advanced").value = JSON.stringify(parsed.config, null, 2);
      $("analysis-preset").value = "standard";
      $("candidate-limit").value = "";
      $("design-count").value = "";
      $("mode").value = parsed.config.run.mode;
      $("screen-engine").value = parsed.config.screen.engine;
      $("seed").value = parsed.config.run.seed;
      $("mock").checked = parsed.config.run.mock;
      fields();
      notice(
        "YAML 설정을 가져왔습니다. 입력을 다시 검토해 적용 설정을 확인하세요.",
      );
    } catch (e) {
      notice(e.message, true);
    }
  };
  action("load-structure", async () => {
    clearInputStructure();
    const request = state.inputStructureRequest;
    const activeSource = structureInputSource || ($("structure-file").files[0] ? "file" : "pdb");
    const file = activeSource === "file" ? $("structure-file").files[0] : null;
    if (activeSource === "file" && !file) throw Error("구조 파일을 선택하세요.");
    if (activeSource === "pdb" && !$("pdb-id").value.trim()) throw Error("PDB ID를 입력하세요.");
    const loaded = await api(
      "/api/inputs/load",
      file
        ? { filename: file.name, content: await file.text(), model_index: $("model-index").valueAsNumber }
        : { pdb_id: $("pdb-id").value, assembly_id: $("assembly-id").value, model_index: $("model-index").valueAsNumber },
    );
    if (request !== state.inputStructureRequest) return;
    state.loaded = loaded;
    options(
      $("target-chain"),
      state.loaded.chains.map((c) => ({
        value: c.id,
        label: chainLabel(c),
      })),
    );
    $("partner-chains").replaceChildren(
      ...state.loaded.chains.map((c) => {
        const label = el("label", undefined, "check"),
          input = el("input");
        input.type = "checkbox";
        input.value = c.id;
        input.addEventListener("change", () => { resetChainReview(); syncChainSelection(); });
        label.append(input, el("span", I18N.term(chainLabel(c))));
        return label;
      }),
    );
    $("chain-review").hidden = false;
    $("hotspot-review").hidden = true;
    state.analyzed = null;
    warnings($("input-warnings"), [
      ...(state.loaded.warnings || []),
      ...(state.loaded.blockers || []),
    ]);
    notice("구조를 불러왔습니다. 표적과 보존 파트너를 선택하세요.");
    syncChainSelection();
    await showInputStructure(loaded, request);
  });
  $("target-chain").addEventListener("change", () => { resetChainReview(); syncChainSelection(); });
  action("fit-input-structure", () => state.inputViewer?.fit());
  action("clear-partners", () => {
    for (const checkbox of $("partner-chains").querySelectorAll("input")) checkbox.checked = false;
    resetChainReview();
    syncChainSelection();
  });
  for (const id of ["assembly-id", "model-index"])
    $(id).addEventListener("input", clearInputStructure);
  window.addEventListener("pagehide", (event) => { if (!event.persisted) state.inputViewer?.dispose(); });
  action("analyze-structure", async () => {
    resetChainReview();
    const revision = state.inputRevision;
    const target = $("target-chain").value,
      context = selectedPartners();
    if (context.includes(target))
      throw Error("표적 체인을 파트너로 중복 선택할 수 없습니다.");
    const analyzed = await api("/api/inputs/analyze", {
      input_id: state.loaded.input_id,
      target,
      context,
    });
    if (revision !== state.inputRevision) return;
    state.analyzed = analyzed;
    $("hotspot-review").hidden = false;
    renderInputAssessment();
    $("hotspot-patches").replaceChildren(
      ...(state.analyzed.patches || []).map((p) => {
        const button = el(
          "button",
          I18N.format("{0} · {1}", [p.positions.join(", "), (p.reason ? p.reason : I18N.term("추천 패치"))]),
          "secondary",
        );
        button.onclick = () => {
          invalidateInput();
          $("hotspots").value = p.positions.join(", ");
        };
        return button;
      }),
    );
    $("hotspots").value =
      state.analyzed.patches?.[0]?.positions.join(", ") || "";
    table($("residue-review"), state.analyzed.residues || [], [
      "position",
      "original_label",
      "relative_sasa",
    ]);
    warnings($("input-warnings"), [
      ...(state.analyzed.warnings || []),
      ...(state.analyzed.blockers || []),
    ]);
    $("acknowledge").checked = false;
  });
  $("input-preparation").addEventListener("change", () => {
    predictionFields(); invalidateInput(); $("acknowledge").checked = false;
  });
  for (const id of ["prediction-provider", "prediction-sequences", "prediction-hotspots"])
    $(id).addEventListener(id === "prediction-sequences" ? "input" : "change", () => {
      predictionFields();
      invalidateInput(); $("acknowledge").checked = false;
    });
  for (const id of ["region-start", "region-end", "hotspot-exclusions"])
    $(id).addEventListener("input", () => { invalidateInput(); $("acknowledge").checked = false; });
  action("fill-demo", () => {
    $("analysis-preset").value = "small-four-v1";
    $("candidate-limit").value = "";
    $("design-count").value = "";
    $("region-start").value = ""; $("region-end").value = ""; $("hotspot-exclusions").value = "";
    $("input-kind").value = "fasta";
    $("mock").checked = true;
    $("job-name").value = "cpu-demo-" + $("mode").value;
    $("target-fasta").value =
      ">demo_target\nVPPGEDSKDVAAPHRQPLTSSERIDKQIRYILDGISALRKETCNKSNMCESSKEALAENNLNLPKMAEKDGCFQSGFNEETCLVKIITGLLEFEVYLEYLQNRFESSEEQARAVQMSTKVLIQFLQKKAKNLDAITTPDPTTNASLLTKLQAQNQWLQDMTTHLILRSFKEFLQSSLRALRQM";
    const vhh =
      ">demo_vhh\nMAQVQLVESGGGLVQPGGSLRLSCAASGFTFSSYRMYWFRQAPGQGLEAVAAISAGGGSTYYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCANRAGWGMGDYWGQGTLVTVSKLAAALEHHHHHH";
    $("vhh-fasta").value = vhh;
    $("wt-fasta").value = vhh;
    $("advanced").value = JSON.stringify(
      {
        design: { num_designs: 1, mpnn: { seqs_per_struct: 1 } },
        screen: { tier2_n: 2 },
        mature: {
          prefilter: { top_n: 2 },
          cdr: {
            source: "config",
            regions: { CDR1: [27, 27], CDR2: [56, 56], CDR3: [105, 105] },
          },
        },
      },
      null,
      2,
    );
    fields();
    notice("CPU 데모 입력을 채웠습니다. 입력·적용 설정 확인을 누르세요.");
  });
  action("review-input", prepareInput);
  action("diagnose-input", () => openEnvironment(true));
  action("open-environment", () => openEnvironment(false));
  action("environment-continue", async () => {
    if (state.input && !state.input.config.run.mock) await readInputReadiness(state.input);
    if (!state.input) {
      $("mode").value = $("environment-mode").value;
      $("tutorial-mode").value = $("mode").value;
      fields();
    }
    view("input"); state.showInputReview?.();
  });
  $("environment-mode").onchange = () => { state.environmentTarget = null; refreshEnvironmentStatus().catch(e => notice(e.message, true)); };
  action("diagnose-environment", submitDiagnostic);
  action("start-job", async () => {
    if (state.analysisSubmitting) return;
    if (!state.input) throw Error("먼저 입력·적용 설정을 확인하세요.");
    if (!state.readiness)
      throw Error("현재 입력의 필수 환경 점검을 먼저 통과해야 합니다.");
    const budget = $("budget").value;
    state.analysisSubmitting = true;
    try {
    const job = await api("/api/jobs", {
      submission_id: state.submissionId || (state.submissionId = crypto.randomUUID().replaceAll("-", "")),
      reuse_results: !$("force-rerun").checked,
      input_id: state.input.id,
      mode: state.input.config.run.mode,
      name: $("job-name").value,
      mock: $("mock").checked,
      ...(budget ? { budget_seconds: Number(budget) * 60 } : {}),
    }, {timeoutMs: 30000});
    await selectJob(job.id);
    view("jobs");
    state.submissionNoticeJob=job.id;
    notice("작업을 등록했습니다. 브라우저를 닫아도 실행됩니다.");
    } finally { state.analysisSubmitting = false; renderInputReadiness(); }
  });
  action("install-profile-button", async () => {
    const job = await api("/api/setup", {
      profile: $("install-profile").value,
    });
    await selectJob(job.id);
    view("jobs");
  });
  action("refresh-jobs", refreshJobs);
  action("refresh-results", refreshResults);
  $("result-select").onchange = () =>
    loadResult($("result-select").value).catch((e) => notice(e.message, true));
  action("open-result", async () => {
    const result = await api("/api/results/open", {
      path: $("result-path").value,
    });
    await refreshResults();
    await loadResult(result.id);
  });
  action("reanalyze", () => {
    $("advanced").value = JSON.stringify(state.result.config, null, 2);
    $("analysis-preset").value = "standard";
    $("candidate-limit").value = "";
    $("design-count").value = "";
    $("mode").value = state.result.mode;
    $("job-name").value = (state.result.name || "analysis") + "-new";
    fields();
    view("input");
    notice(
      "새 분석 설정을 가져왔습니다. 표적·라이브러리·WT 입력을 검토한 뒤 별도 실행하세요.",
    );
  });
  action("compare-runs", async () => {
    if (!state.result || !$("compare-select").value)
      throw Error("현재 실행과 비교 실행을 선택하세요.");
    const other = await api("/api/results/" + $("compare-select").value),
      node = $("comparison");
    node.replaceChildren(
      el(
        "p",
        I18N.term("조건이 다른 점수는 자동 합산하지 않습니다. 입력·엔진·seed·MSA·restraint를 확인하세요."),
        "warning",
      ),
    );
    const pair = el("div", undefined, "comparison");
    for (const run of [state.result, other]) {
      const box = el("div");
      box.append(
        el("h3", run.name),
        el(
          "p",
          I18N.format("후보 {0} · 적격 {1} · {2}", [run.total, run.eligible_total, run.mock ? "MOCK" : I18N.term("실제 예측")]),
        ),
        el("pre", JSON.stringify(run.conditions, null, 2)),
      );
      pair.append(box);
    }
    node.append(pair);
  });
  for (const id of [
    "job-name",
    "analysis-preset",
    "candidate-limit",
    "design-count",
    "seed",
    "screen-engine",
    "advanced",
    "target-fasta",
    "vhh-fasta",
    "wt-fasta",
    "wt-structure",
    "hotspots",
    "target-chain",
    "provider",
  ]) {
    $(id).addEventListener("input", () => {
      state.input = null;
      state.readiness = null;
      $("review-output").hidden = true;
    });
  }
  $("view-input").addEventListener("input", invalidateInput);
  const session = await api("/api/session");
  state.presets = session.presets || {};
  [state.starter, state.capabilities] = await Promise.all([api("/api/starter-presets"), api("/api/capabilities")]);
  I18N.text($("workspace-path"), session.workspace);
  fields();
  await initNoviceFlow();
  // Restore the last independent installation check without submitting work.
  await refreshEnvironmentStatus();
  await refreshJobs();
  await refreshResults();
  const requested = new URLSearchParams(window.location.search).get("result");
  if (requested) await loadResult(requested);
  state.navigationReady = true;
  await restoreNavigation();
  rememberLocation();
  window.addEventListener("popstate", () => restoreNavigation().catch(error => notice(error.message, true)));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshJobs(); });
  window.addEventListener("online", refreshJobs);
  window.addEventListener("pageshow", event => { if (event.persisted) refreshJobs(); });
  setInterval(refreshJobs, 2000);
}
function noviceFasta(content) {
  const rows = []; let current;
  for (const raw of content.trim().split(/\r?\n/)) {
    const line = raw.trim(); if (!line) continue;
    if (line.startsWith(">")) { current = {name: line.slice(1).trim(), sequence: "", copies: 1}; rows.push(current); }
    else { if (!current) throw Error("FASTA는 >이름으로 시작해야 합니다."); current.sequence += line.replace(/\s/g, "").toUpperCase(); }
  }
  if (!rows.length || rows.some(r => !r.name || !r.sequence || /[^ACDEFGHIKLMNPQRSTVWY]/.test(r.sequence)))
    throw Error("이름과 표준 아미노산 서열을 확인하세요. X·DNA/RNA·빈 서열은 사용할 수 없습니다.");
  if (new Set(rows.map(r => r.name.toLowerCase())).size !== rows.length) throw Error("FASTA 이름은 중복될 수 없습니다.");
  return rows;
}
function noviceComplexProteins() {
  if ($("input-kind").value === "structure") {
    if (!state.loaded || !$("acknowledge").checked) throw Error("구조와 체인을 검토하고 확인란을 선택하세요.");
    return [$("target-chain").value, ...selectedPartners()].map(id => {
      const c = state.loaded.chains.find(c => c.id === id);
      if (!c) throw Error("선택한 체인을 다시 확인하세요.");
      return {name: c.name || id, sequence: c.sequence, copies: 1};
    });
  }
  const entries = noviceFasta($("complex-fasta").value);
  for (const [i, row] of entries.entries()) row.copies = Number($("complex-copies-" + i)?.value || 1);
  const n = entries.reduce((n,r) => n+r.copies,0), length = entries.reduce((n,r) => n+r.copies*r.sequence.length,0);
  if (entries.some(r => !Number.isInteger(r.copies) || r.copies<1) || n<2 || n>25 || length>2000)
    throw Error("복합체는 사본을 포함해 2–25체인, 합계 2,000잔기까지 지원합니다.");
  return entries;
}
async function initNoviceFlow() {
  const host = $("view-input"), grid = host.querySelector(".grid"), [inputCard, settingsCard] = [...grid.children];
  const purposeCard = host.querySelector(".quickstart"), reviewCard = el("section", null, "card");
  purposeCard.id = "novice-purpose"; inputCard.id = "novice-input"; settingsCard.id = "novice-settings"; reviewCard.id = "novice-review";
  const modeLabel = document.querySelector('label[for="mode"]'); purposeCard.prepend(modeLabel, $("mode"));
  modeLabel.hidden = true; $("mode").hidden = true;
  const purposeChoices = el("div", null, "novice-purpose-choices");
  for (const [value, title, description] of [
    ["complex", "단백질 복합체 예측", "두 개 이상 단백질의 서열 또는 구조를 함께 예측합니다."],
    ["design", "새 나노바디 설계", "표적 단백질에서 새 VHH 후보를 만들고 구조를 비교합니다."],
    ["screen", "보유 나노바디 비교", "표적과 보유 VHH 전체 서열 목록을 평가합니다."],
    ["mature", "기존 나노바디 변이 평가", "표적과 WT VHH로 치환 후보를 준비하고 평가합니다."],
  ]) {
    const button = el("button", null, "novice-purpose-choice");
    button.type = "button"; I18N.attribute(button, "aria-label", I18N.concat([I18N.term(title), I18N.term(" · 입력하기")]));
    button.append(el("strong", I18N.term(title)), el("span", I18N.term(description)), el("span", I18N.term("입력하기")));
    button.onclick = () => { const changed = $("mode").value !== value, wasMock = $("mock").checked; $("mode").value = value; $("tutorial-mode").value = value; if (value === "complex") $("mock").checked = false; if (changed || wasMock !== $("mock").checked) invalidateInput(); fields(); jump(1); };
    purposeChoices.append(button);
  }
  purposeCard.append(purposeChoices);
  const tutorialControls = $("load-tutorial").parentElement;
  tutorialControls.querySelector('label[for="tutorial-mode"]').hidden = true;
  $("tutorial-mode").hidden = true; I18N.text($("load-tutorial"), I18N.term("이 목적의 공개 예제 불러오기"));
  inputCard.prepend(tutorialControls);
  inputCard.append($("tutorial-status"));
  const demoControls = el("details"); demoControls.append(el("summary", I18N.term("GPU 없이 사용 흐름 연습 · 합성 데모")), $("mock").closest("label"), $("fill-demo")); inputCard.append(demoControls);
  I18N.text(purposeCard.querySelector("h2"), I18N.term("어떤 분석을 시작할까요?")); purposeCard.querySelectorAll("p").forEach(node => node.hidden = true);
  const purposeHelp = el("p", I18N.term("복합체: 단백질들을 함께 예측 · 설계: 새 VHH 생성 · 비교: 보유 VHH 전체 평가 · 변이: WT와 치환 후보 평가"));
  purposeHelp.hidden = true; purposeCard.prepend(purposeHelp);
  const start = el("button", I18N.term("이 분석 시작")); start.id = "novice-start"; start.type = "button"; start.hidden = true; purposeCard.append(start);
  for (const n of [$("review-input"), $("review-output")]) reviewCard.append(n);
  grid.append(reviewCard);
  const reviewed=el("div");reviewed.id="novice-reviewed-summary";$("review-output").prepend(reviewed);
  const raw=el("details");raw.append(el("summary",I18N.term("전체 적용 설정 JSON · 고급")),$("resolved-config"));$("review-output").append(raw);
  reviewCard.prepend(el("h2", I18N.term("검토·실행 · 입력 확인 후 분석 시작"))); grid.append(reviewCard);
  grid.classList.add("novice-grid");
  const rail = host.querySelector(".workflow-steps"); rail.replaceChildren(...state.starter.steps.map((name,i) => el("li", I18N.format("{0}. {1}", [i+1, I18N.term(name)]))));
  const heading = el("h2", "", "novice-heading"), task = el("p", "", "next-step"); heading.tabIndex=-1;task.setAttribute("role","status");host.insertBefore(heading,purposeCard);host.insertBefore(task,purposeCard);
  I18N.text(inputCard.querySelector("h2"), I18N.term("입력 준비 · 필요한 서열 또는 파일"));
  I18N.text(settingsCard.querySelector("h2"), I18N.term("실행 조건 · 작은 시작 설정"));
  const intro = $("analysis-preset").nextElementSibling;
  I18N.text(intro, I18N.term("새 분석의 기본값은 Seed 42와 엔진별 모델 1개입니다. screen은 모든 입력 후보를 평가합니다. 복원한 설정은 그대로 유지합니다."));
  const advanced = el("details"); advanced.id="novice-advanced";advanced.append(el("summary",I18N.term("고급 설정 · 후보 수·Seed·기존 설정 가져오기")));
  while (intro.nextSibling) advanced.append(intro.nextSibling);settingsCard.append(advanced);
  const controls=el("fieldset");controls.append(el("legend",I18N.term("평가 엔진 · 동일 입력의 독립 결과")));
  for (const engine of state.starter.engines) {const label=el("label", null,"check"), box=el("input");box.type="checkbox";box.checked=true;box.value=engine;box.dataset.noviceEngine="";label.append(box,document.createTextNode(({colabfold:"ColabFold",boltz2:"Boltz-2",chai1:"Chai-1",opendde:"OpenDDE Preview"})[engine]||engine));controls.append(label);}
  const msa=el("select");msa.id="novice-msa"; for(const [v,label] of [["public_api","공용 MSA 서버 · 정확한 서열 전송 동의 필요"],["cache","검증된 MSA 캐시 재사용"],["local_mmseqs2","등록된 로컬 MSA 검색"]]) {const o=el("option",I18N.term(label));o.value=v;msa.append(o);}
  const ml=el("label",I18N.term("MSA 준비 방식"));ml.htmlFor=msa.id;controls.append(ml,msa);
  const consent=el("input");consent.id="novice-consent";consent.type="checkbox";const cl=el("label",null,"check");cl.append(consent,el("span", I18N.term("이 입력의 단백질 서열을 api.colabfold.com으로 전송하는 데 동의합니다.")));controls.append(cl);
  const demoMsaNote=el("p",I18N.term("CPU 합성 데모는 MSA를 준비하거나 외부 서버로 서열을 전송하지 않습니다."),"muted");controls.append(demoMsaNote);
  const plan=el("p","","next-step");plan.id="novice-model-plan";settingsCard.insertBefore(controls,advanced);settingsCard.insertBefore(plan,advanced);
  settingsCard.insertBefore(cl,advanced);
  const complex=el("div");complex.id="novice-complex-fields";complex.hidden=true;
  const label=el("label",I18N.term("복합체 단백질 FASTA · 위에서 아래 순서로 체인 배정"));label.htmlFor="complex-fasta";const fasta=el("textarea");fasta.id="complex-fasta";fasta.rows=8;fasta.spellcheck=false;
  const copies=el("div");copies.id="novice-complex-copies";complex.append(label,fasta,copies);inputCard.insertBefore(complex,$("fasta-fields"));
  const footer=el("div",null,"novice-footer"),prev=el("button",I18N.term("이전"),"secondary"),next=el("button",I18N.term("입력 확인 후 다음")),reason=el("p");prev.id="novice-previous";next.id="novice-next";reason.id="novice-blocker";reason.setAttribute("role","alert");footer.append(prev,next,reason);host.append(footer);
  let step=0,restoring=false,draftTimer,saveQueue=Promise.resolve();
  host.insertBefore(rail, task);
  const tasks=["목적을 고르세요. 필요한 입력과 결과를 확인하면 다음으로 이동합니다.","전체 서열·체인 순서·사본 수를 확인하세요. 오류 없는 필수 입력이면 다음으로 이동합니다.","최대 계획 모델 수·선택 엔진·MSA 전송 여부를 확인하세요.","입력 확인을 누른 뒤 적용 조건을 읽고 분석 실행을 누르세요. 환경 확인 기록은 재사용하며, 입력 확인은 계산을 시작하지 않습니다."];
  function render(){
    host.querySelector(".setup-entry").hidden = step !== 0;
    [purposeCard,inputCard,settingsCard,reviewCard].forEach((n,i)=>n.hidden=i!==step);I18N.text(heading, I18N.format("{0}/4 · {1}", [step+1, I18N.term(state.starter.steps[step])]));I18N.text(task, I18N.term(tasks[step]));prev.hidden=step===0;next.hidden=step===0||step===3;I18N.text(next, step===1 ? I18N.term("입력 확인 후 다음") : I18N.term("조건 확인 후 다음"));
    [...rail.children].forEach((n,i)=>n.setAttribute("aria-current",i===step?"step":"false"));
    const isComplex=$("mode").value==="complex";
    complex.hidden=!isComplex||$("input-kind").value!=="fasta";$("fasta-fields").hidden=isComplex||$("input-kind").value!=="fasta";
    controls.hidden=$("analysis-preset").value!=="small-four-v1";$("mock").disabled=isComplex;demoControls.hidden=isComplex;
    const simulated=$("mock").checked&&!isComplex;ml.hidden=simulated;msa.hidden=simulated;cl.hidden=simulated;demoMsaNote.hidden=!simulated;
    try {I18N.text(plan, I18N.term(noviceModelPlan(inputSettings(true),$("mode").value,$("mode").value==="screen"?noviceFasta($("vhh-fasta").value).length:0)));}catch(e){I18N.text(plan, I18N.term(e.message));}
    I18N.text($("review-input"), I18N.term("입력 확인"));I18N.text($("start-job"), I18N.term("분석 실행"));
  }
  function jump(n){step=n;I18N.text(reason, "");render();heading.focus();}
  state.showInputReview = () => jump(state.input ? 3 : 1);
  function validateInputs(){
    if($("mode").value==="complex") return noviceComplexProteins();
    if($("input-kind").value==="fasta") {if(noviceFasta($("target-fasta").value).length!==1)throw Error("단일 분석은 표적 1개입니다. 여러 표적은 배치 분석을 사용하세요.");}
    else if(!state.analyzed||!$("acknowledge").checked)throw Error("구조를 불러오고 체인·준비 방식과 경고를 확인하세요.");
    if($("mode").value==="screen")noviceFasta($("vhh-fasta").value);
    if($("mode").value==="mature"&&noviceFasta($("wt-fasta").value).length!==1)throw Error("WT VHH 1개를 입력하세요.");
  }
  next.onclick=()=>{try {if(step===1)validateInputs();else inputSettings();jump(step+1);}catch(e){I18N.text(reason, I18N.term(e.message));const field=step===2?consent:$("mode").value==="complex"?fasta:$("input-kind").value==="fasta"?$("target-fasta"):$("structure-file");field.focus();field.setAttribute("aria-invalid","true");}};
  start.onclick=()=>{fields();jump(1);};prev.onclick=()=>jump(step-1);
  function copiesReview(){try{const old=[...copies.querySelectorAll("input")].map(i=>i.value),rows=noviceFasta(fasta.value);copies.replaceChildren(...rows.map((r,i)=>{const l=el("label",I18N.format("{0} · {1} · {2} aa · 사본 수", [String.fromCharCode(65+i), r.name, r.sequence.length])),n=el("input");n.id="complex-copies-"+i;n.type="number";n.min="1";n.max="25";n.value=old[i]||"1";l.append(n);return l;}));}catch(e){I18N.text(copies, I18N.term(e.message));}}
  fasta.addEventListener("input",copiesReview);
  host.addEventListener("input",e=>{if(e.target!==consent)consent.checked=false;I18N.text(reason, "");render();if(restoring)return;clearTimeout(draftTimer);draftTimer=setTimeout(()=>{const fields={};for(const n of host.querySelectorAll("input[id],select[id],textarea[id]")){if(n.type!=="file"&&n.id!=="novice-consent")fields[n.id]=n.type==="checkbox"?n.checked:n.value;}fields.comparison_engines=[...controls.querySelectorAll('[data-novice-engine]:checked')].map(i=>i.value);saveQueue=saveQueue.catch(()=>{}).then(()=>api("/api/draft",{version:1,fields})).catch(e=>{I18N.text(reason, I18N.concat([I18N.term("초안 저장 실패: "), e.message]));});},500);});
  action("load-tutorial",async()=>{const revision=state.inputRevision;const example=await api("/api/tutorials/"+$("tutorial-mode").value),entry=example.payload.entries[0];if(revision!==state.inputRevision)throw Error("불러오는 동안 입력이 변경되었습니다. 현재 입력을 유지합니다.");$("mode").value=example.mode;$("input-kind").value="fasta";$("analysis-preset").value="small-four-v1";$("advanced").value="{}";$("mock").checked=false;$("candidate-limit").value="";$("design-count").value="";$("seed").value="42";$("job-name").value=example.payload.name;$("target-fasta").value=entry.content||"";$("vhh-fasta").value=entry.vhh_fasta||"";$("wt-fasta").value=entry.wt_fasta||"";fasta.value=(entry.proteins||[]).map(p=>`>${p.name}\n${p.sequence}`).join("\n");consent.checked=false;invalidateInput();copiesReview();fields();jump(1);I18N.text($("tutorial-status"), I18N.term("공개 입력을 불러왔습니다. 아직 계산은 시작하지 않았습니다."));});
  render();const draftRevision=state.inputRevision;purposeChoices.querySelectorAll("button").forEach(button=>button.disabled=true);start.disabled=true;$("load-tutorial").disabled=true;try{const draft=await api("/api/draft");if(draftRevision!==state.inputRevision)throw Error("입력이 변경되어 저장된 초안을 덮어쓰지 않았습니다.");restoring=true;for(const [id,value]of Object.entries(draft.fields||{})){const n=$(id);if(n&&host.contains(n)&&n.type!=="file"){if(n.type==="checkbox")n.checked=value===true;else n.value=String(value);}}if(draft.fields?.comparison_engines)for(const n of controls.querySelectorAll('[data-novice-engine]'))n.checked=draft.fields.comparison_engines.includes(n.value);consent.checked=false;fields();copiesReview();if(Object.keys(draft.fields||{}).length)I18N.text(task, I18N.term("초안 입력을 복원했습니다. 구조 파일을 다시 선택하고 전송 동의와 입력을 확인하세요. 설치 환경 확인 기록은 재사용합니다."));}catch(e){I18N.text(reason, I18N.concat([I18N.term("초안 복원 실패: "), e.message]));}finally{restoring=false;purposeChoices.querySelectorAll("button").forEach(button=>button.disabled=false);start.disabled=false;$("load-tutorial").disabled=false;render();}
}
I18N.init();
document.addEventListener("nanocofold:language", () => {
  if (!state.result) return;
  renderScatter();
  renderDistribution((state.observations || []).filter(row => row.candidate_id === state.candidate?.candidate_id));
  const pae = $("pae");
  if (window.Plotly && pae?.data) {
    Plotly.relayout(pae, {"xaxis.title.text": I18N.value(I18N.term("열 · 평가 잔기")),
      "yaxis.title.text": I18N.value(I18N.term("행 · 정렬 기준 잔기"))});
    if (pae.data.length) Plotly.restyle(pae, {hovertemplate: I18N.value(I18N.term("정렬 기준 (행) %{y}<br>평가 잔기 (열) %{x}<br>PAE %{z:.2f} Å<extra></extra>"))});
    else localizeEmptyPaeAnnotation();
  }
});
startPlatform();
