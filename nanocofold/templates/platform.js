/* Shared browser/offline explorer. Data is displayed with textContent, not HTML injection. */
"use strict";
/** Display recorded prediction seeds without changing saved rows or inferring defaults. */
function recordedCandidateSeed(row, observations = []) {
  const valid = value => (typeof value === 'number' || (typeof value === 'string' && /^\d+$/.test(value)))
    && Number.isSafeInteger(Number(value)) && Number(value) >= 0 && Number(value) <= 4294967295;
  if (row.seed != null && row.seed !== '') return valid(row.seed) ? Number(row.seed) : null;
  if (row.model_record === false || !row.candidate_id) return null;
  const engine = value => value === 'boltz' ? 'boltz2' : value;
  const selectedEngine = engine(row.predictor_engine || row.engine);
  if (!selectedEngine || selectedEngine === 'unknown' || selectedEngine === 'not_predicted') return null;
  const matching = observations.filter(record => record.candidate_id === row.candidate_id
    && engine(record.predictor_engine || record.engine) === selectedEngine);
  if (!matching.length || matching.some(record => !valid(record.seed))) return null;
  const seeds = [...new Set(matching.map(record => Number(record.seed)))].sort((a, b) => a - b);
  return seeds.length === 1 ? seeds[0] : seeds.join(', ');
}

const $ = (id) => document.getElementById(id);
const metricCaptions = Object.freeze({ptm:"pTM",pdockq:"pDockQ",pdockq2:"pDockQ2 (최소)",interface_pae_ab:"PAE 행 표적·열 VHH (Å)",interface_pae_ba:"PAE 행 VHH·열 표적 (Å)",interface_pae_median:"계면 PAE 중앙값 (Å)",interface_pae_p90:"계면 PAE 90백분위 (Å)",target_plddt:"표적 pLDDT",binder_plddt:"VHH pLDDT",complex_plddt:"전체 복합체 pLDDT",n_hotspots_requested:"지정 결합 부위 잔기 수",n_hotspots_contacted:"접촉한 지정 잔기 수",n_target_clashes:"표적–VHH 충돌 원자쌍",n_context_clashes:"파트너–VHH 충돌 원자쌍",candidate_id:"후보",ipsae:"ipSAE",iptm:"ipTM",interface_plddt:"계면 pLDDT",interface_pae:"계면 PAE (Å)",hotspot_contact_frac:"결합 부위 접촉률",seed:"Seed",model:"모델",error:"평가 오류",chain_a:"체인 1",chain_b:"체인 2",iptm_a_to_b:"체인 1→2 ipTM",iptm_b_to_a:"체인 2→1 ipTM",a:"잔기 1",b:"잔기 2",distance:"거리 (Å)",kind:"구분",pdockq2_target:"pDockQ2 표적 방향",pdockq2_binder:"pDockQ2 VHH 방향",dev_flags:"전체 평가 위험 수",dev_severe:"서열 검토 항목 수",n_hbonds:"N/O 근접 원자쌍",n_salt_bridges:"반대 전하 원자쌍",esm_score:"ESM 서열 점수",composite_score:"배치 상대 종합점수",n_clashes:"충돌 원자쌍",site_satisfied:"지정 결합 부위 판정 (기록)",site_selection_eligible:"지정 결합 부위 포함 적격",pose_consensus_status:"동일 후보 자세 일치",pose_supporting_engines:"자세 일치 엔진 수",pose_quality_supporting_engines:"기록 적격·자세 일치 엔진 수",design_consistency_status:"설계·재예측 일치",design_binder_rmsd_A:"설계·재예측 VHH RMSD (Å)",review_rank:"검토 순위 (동률 허용)",review_rank_scope:"검토 순위 범위",review_priority_reason:"검토 판정 이유",candidate_review_priority:"후보 검토 판정",review_priority:"모델 검토 판정"});
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
function developabilityReviewOnly(row) {
  const reasons = Array.isArray(row?.exclusion_reasons) ? row.exclusion_reasons : String(row?.exclusion_reason || "").split(";").filter(Boolean);
  return row?.dev_assessment_policy === "sequence_liability_screen_v2" && row.dev_assessment_status === "review_required" && reasons.length > 0 && reasons.every(code => code === "developability_review_required");
}
const decisionCaption = (value, row) => typeof value === "boolean" ? value ? "적격" : developabilityReviewOnly(row) ? "검토 보류" : "부적격" : "미평가";
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
  navigationRequest: 0,
  inputRevision: 0,
  inputReadinessRequest: 0,
  presets: {},
  planningDefaults: {},
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
    ? I18N.value(I18N.term("미평가"))
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
  const labels = {wt_control_not_in_model_pool:"WT 대조군 예측 풀에서 제외",sequence_prior_top_n_not_selected:"서열 prior 평가 한도 밖 (구조 미평가)",missing_metrics:"필수 지표 미평가",sequence_prior_prefilter_unmet:"서열 prior 기준 미달",sequence_prior_prefilter_unassessed:"서열 prior 미평가",clash:"거리 기준 충돌", ipsae:"ipSAE 기준 미통과", iptm:"ipTM 기준 미통과", interface_plddt:"계면 pLDDT 기준 미통과", hotspot:"결합 부위 접촉 기준 미통과", developability:"개발가능성 경고 기준 미통과", developability_review_required:"서열 검토 필요 · 자동 선별 보류",developability_unassessed:"CDR 개발가능성 미평가", scoring_error:"구조 점수 계산 오류", not_scored:"구조 점수 미평가", eligibility_unassessed:"적격 판정 미기록·미평가", parent_control:"원본 후보 대조군", wt_control:"야생형 대조군", prediction_error:"예측 오류",prefilter_not_selected:"예측 전 준비·미선정",cross_binding_risk:"전장형 교차 결합 위험",incomplete_comparison:"비교 미완료",first_stage_only:"1차 평가만 완료",boltz_failed_or_incomplete:"Boltz 최종 평가 실패·미완료"};
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
  if (!reasons.length) $("exclusion-summary").replaceChildren(el("p", I18N.term("제외·미평가 사유가 기록되지 않았습니다. 작업 상태와 실행 로그를 확인하세요.")));
  else table($("exclusion-summary"), reasons.map(r => ({이유:r.label, 기록수:r.count, 기록코드:r.code})), ["이유", "기록수", "기록코드"]);
}
const phaseLabel = (phase) => ({
  waiting_for_gpu: "GPU 대기", comparison_msa_search: "서열 정렬(MSA) 준비", starting: "시작 준비",
  input: "입력 준비", target: "표적 준비", prediction: "복합체 예측",
  design: "새 후보 생성", screen: "후보 비교", mature: "변이 후보 준비",
  tier2: "최종 복합체 평가", engine_comparison: "엔진별 비교",
  final: "결과·보고서 저장", complete: "완료", resume_validation: "재개 조건 확인",
  cancelling: "종료 정리 중", cancelled: "취소",
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
      ? "상태 응답이 지연되고 있습니다. 연결이 복구되면 현재 상태를 다시 확인합니다."
      : "처리 상태를 확인하지 못했습니다. 작업·로그를 새로고침해 현재 상태를 확인하세요.");
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
// Keep summaries outside Plotly/MolStar-owned DOM so redraws and click handlers
// cannot erase their textual data alternative. This creates no extra plot.
function describeResultFigure(id, label, summary, alternative = "#candidate-table", empty = false) {
  const node = $(id);
  if (!node?.parentElement) return;
  node.setAttribute("role", "group");
  I18N.attribute(node, "aria-label", I18N.term(label));
  const summaryId = id + "-summary";
  let note = $(summaryId);
  if (!note) {
    note = el("p", undefined, "muted"); note.id = summaryId;
    node.parentElement.insertBefore(note, node.nextSibling);
  }
  const link = el("a", I18N.term("원본 자료·CSV 확인")); link.href = alternative;
  note.replaceChildren(el("span", summary), " ", link);
  node.setAttribute("aria-describedby", summaryId);
  if (empty) {node.style.height = id === "structure-view" ? "auto" : "160px"; node.style.minHeight = "0";}
  else {node.style.removeProperty("height"); node.style.removeProperty("min-height");}
}
function mockZeroSelectionNotice(summary, rows) {
  const mock = typeof summary.mock === "boolean" ? summary.mock : summary.config?.run?.mock;
  if (mock !== true || summary.selected_total !== 0) return null;
  const note = el("div", undefined, "report-interpretation"); note.dataset.mockZeroSelection = "true";
  note.append(el("strong", I18N.term("합성 mock 연습 · 기록 선별 0건")), el("p", I18N.term("이 연습의 선별 결과는 0건입니다. 아래에서 제외·미평가 사유와 실행 상태를 확인하고, 점수·구조 탐색과 파일 저장을 연습하세요.")));
  const missing = rows.filter(row => row.model_record !== false && (row.dev_cdr_evaluated === false || String(row.exclusion_reason || "").split(";").includes("developability_unassessed") || row.exclusion_reasons?.includes("developability_unassessed"))).length;
  if (missing) note.append(el("p", I18N.format("CDR 미평가 모델: {0}건. CDR 범위와 도구 설정을 확인하세요. 이 연습에서는 점수·구조 탐색과 파일 저장을 확인할 수 있습니다.", [missing])));
  return note;
}
function table(node, rows, columns, {localizedValues = []} = {}) {
  if (node.classList.contains("scroll")) {node.tabIndex=0;node.setAttribute("role","region");if(!node.hasAttribute("aria-label"))I18N.attribute(node, "aria-label", I18N.term("상세 표 스크롤"));}
  const target = el("table"),
    head = el("thead"),
    body = el("tbody"),
    tr = el("tr");
  target.append(el("caption", I18N.attributeValue(node, "aria-label") || I18N.concat(columns.flatMap((key, index) => [index ? " · " : "", I18N.term(metricCaption(key, rows))]))));
  columns.forEach((key) => {const th=el("th",I18N.term(metricCaption(key, rows)));th.scope="col";tr.append(th);});
  head.append(tr);
  rows.forEach((row) => {
    const r = el("tr");
    columns.forEach((key) => {
      const cell = el("td", row[key] == null ? I18N.term("미평가") : typeof row[key] === "object" && I18N.term(row[key]) === row[key] ? row[key] : ["이유", "조치", "구분", ...localizedValues].includes(key) ? I18N.term(fmt(row[key])) : fmt(row[key]));
      if (typeof row[key] === "number" && Number.isFinite(row[key])) cell.title = String(row[key]);
      r.append(cell);
    });
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
  const request = ++state.navigationRequest;
  const url = window.location.href;
  const query = new URL(url).searchParams;
  const selected = query.get("job");
  if (selected && state.jobs.some(job => job.id === selected) && selected !== state.job)
    await selectJob(selected, false);
  // A newer history entry or explicit selection owns the screen after this await.
  if (request !== state.navigationRequest || url !== window.location.href) return;
  const name = query.get("view") || (query.get("result") ? "results" : "input");
  view($("view-" + name) ? name : "input", false);
}
function view(name, remember = true) {
  if (!$("view-" + name)) return;
  if (state.currentView !== name) dismissCancellation();
  state.currentView = name;
  if (remember) {
    ++state.navigationRequest;
    // Reselecting the displayed tab can supersede a pending history restoration.
    rememberLocation(false);
  }
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
  if (name === "catalog") ($("catalog-candidate-panel").hidden ? refreshCatalog() : refreshVhhTable()).catch(e => notice(e.message, true));
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
  const owner = $("catalog-owner")?.value.trim();
  if (owner) params.set("researcher", owner);
  const page = await api("/api/result-catalog?" + params);
  if (request !== state.catalogRequest) return;
  state.catalogNext = page.next_cursor;
  $("catalog-next").disabled = !page.next_cursor;
  const table = el("table"), head = el("thead"), headings = el("tr"), body = el("tbody");
  const columns = [["name", "분석"], ["mode", "모드"], ["created", "실행일"], ["candidate_count", "서열"],
    ["model_count", "저장 구조 관측"], ["eligible_model_count", "기준 통과"], ["status", "계산 상태"],
    ["retention", "보관"], ["elapsed_seconds", "계산 시간"], ["storage_bytes", "서버 용량"]];
  if (state.canBrowseLab || page.items.some(item => item.researchers?.length)) {
    $("catalog-owner-label").hidden = !state.canBrowseLab;
    columns.splice(1, 0, ["researchers", "담당자"]);
  }
  for (const [key, label] of columns) {
    if (key === "researchers") {
      const th = el("th", I18N.term(label)); th.scope="col"; headings.append(th); continue;
    }
    const th = el("th"), button = el("button", I18N.concat([I18N.term(label), state.catalogSort === key ? state.catalogDescending ? " ↓" : " ↑" : ""]), "sort-header");
    th.scope = "col"; th.setAttribute("aria-sort", state.catalogSort === key ? state.catalogDescending ? "descending" : "ascending" : "none");
    button.onclick = () => {state.catalogDescending = state.catalogSort === key ? !state.catalogDescending : true; state.catalogSort = key; state.catalogCursor = null; refreshCatalog().catch(e => notice(e.message, true));};
    th.append(button); headings.append(th);
  }
  head.append(headings);
  for (const item of page.items) {
    const tr = el("tr");
    for (const [key] of columns) {
      const td = el("td");
      if (key === "researchers") td.textContent = (item.researchers || []).join(", ") || "—";
      else if (key === "name") {
        const link = el("a", (item.name || item.id) + (item.mock ? " · MOCK" : ""));
        link.href = "/?result=" + encodeURIComponent(item.id); link.target = "_blank"; link.rel = "noopener";
        td.append(link);
        if (item.matching_candidate) {
          const multiple = item.matching_candidate_count > 1;
          const match = el("a", multiple ? I18N.format("일치 후보 {0}개 보기", [item.matching_candidate_count]) : I18N.format("일치 후보 열기 · {0}", [item.matching_candidate]));
          match.href = link.href + "&candidate=" + encodeURIComponent(item.matching_candidate);
          if (multiple) match.hash = new URLSearchParams({matches:params.get("search")}).toString();
          match.target = "_blank"; match.rel = "noopener"; td.append(el("br"), match);
        }
      } else I18N.text(td, key === "elapsed_seconds" ? item[key] === null || item[key] === undefined ? I18N.term("미기록") : I18N.concat([Math.round(item[key]), I18N.term("초")]) : key === "storage_bytes" ? I18N.term(bytesLabel(item[key])) : key === "created" ? new Date(item[key] * 1000).toLocaleString() : key === "retention" ? item[key] === "minimal" ? I18N.term("핵심 보관") : I18N.term("원본 보관") : key === "status" ? (states[item[key]] ? I18N.term(states[item[key]]) : item[key]) : fmt(item[key]));
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(head, body); $("catalog-table").replaceChildren(table, el("p", I18N.term("저장 구조 관측은 1차 평가 구조도 포함합니다. 최종 평가 모델 수는 결과 보고서에서 따로 확인하세요."), "muted"));
  const notes = [I18N.format("{0}개 표시 · 용량은 색인 시점의 측정값", [page.items.length])];
  if (page.sequence_index_pending) notes.push(I18N.format(" · 이전 결과 {0}건의 후보 검색을 준비하고 있습니다.", [page.sequence_index_pending]));
  if (page.sequence_index_unavailable) notes.push(I18N.format(" · 이전 결과 {0}건의 후보 색인을 읽지 못했습니다. 원본 폴더를 확인한 뒤 기존 결과 열기를 사용하세요.", [page.sequence_index_unavailable]));
  I18N.text(status, I18N.concat(notes));
}
const vhhGrid = {sort:"created", descending:true, offset:0, revision:0, applied:null};
const vhhColumns = [["candidate_id","후보 ID"],["name","분석"],["target","표적"],["researchers","담당자"],["engine","예측 엔진"],
  ["decision","기록 판정"],["selection_rank","최종 선별 순위"],["ipsae","ipSAE"],["iptm","ipTM"],["interface_pae","계면 PAE (Å)"],["sequence","VHH 서열"],["created","실행일"]];
const vhhDecisions = {eligible:"적격",ineligible:"부적격",unassessed:"미평가",held:"검토 보류",failed:"평가 오류",control:"WT·대조군"};
function vhhParams() {
  const p = new URLSearchParams({sort:vhhGrid.sort,descending:String(vhhGrid.descending)});
  for (const [key,id] of [["search","search"],["target","target"],["researcher","owner"],["engine","engine"],["decision","decision"]]) {
    const value=$("db-vhh-"+id).value.trim();
    if(value) p.set(key,value);
  }
  if ($("db-vhh-mock").checked) p.set("include_mock","true");
  return p;
}
async function refreshVhhTable() {
  const revision=++vhhGrid.revision, params=vhhParams();
  const status=$("db-vhh-status"), output=$("db-vhh-table");
  vhhGrid.applied=null; $("db-vhh-export").disabled=true;
  I18N.text(status,I18N.term("결과 DB를 조회하고 있습니다…"));
  output.replaceChildren();
  const data=await api("/api/candidate-catalog?"+params+"&offset="+vhhGrid.offset);
  if(revision!==vhhGrid.revision) return;
  if(vhhGrid.offset && !data.items.length) {vhhGrid.offset=0;return refreshVhhTable();}
  const table=el("table"), header=el("tr"), body=el("tbody");
  for(const [key,label] of vhhColumns) {
    const th=el("th"), btn=el("button",I18N.concat([I18N.term(label),key===vhhGrid.sort?(vhhGrid.descending?" ↓":" ↑"):""]),"sort-header");
    th.scope="col";th.setAttribute("aria-sort",key===vhhGrid.sort?(vhhGrid.descending?"descending":"ascending"):"none");
    btn.onclick=()=>{vhhGrid.descending=key===vhhGrid.sort?!vhhGrid.descending:["ipsae","iptm","created"].includes(key);vhhGrid.sort=key;vhhGrid.offset=0;refreshVhhTable().catch(e=>notice(e.message,true));};
    th.append(btn);header.append(th);
  }
  const head=el("thead");head.append(header);table.append(head,body);
  for(const row of data.items) {
    const tr=el("tr");
    for(const [key] of vhhColumns) {
      const td=el("td"), v=row[key];
      if(key==="candidate_id" || key==="name"){
        const link=el("a",String(v)+(key==="name"&&row.mock?" · MOCK":""));
        link.href="/?"+new URLSearchParams(key==="name"?{result:row.result_id}:{result:row.result_id,candidate:row.candidate_id});
        link.target="_blank";link.rel="noopener";td.append(link);
      } else if(key==="sequence" && v) {
        const show=el("details");show.append(el("summary",I18N.term("서열 보기")),el("code",v));td.append(show);
      } else if(key==="decision") I18N.text(td,I18N.term(vhhDecisions[v]||"미평가"));
      else td.textContent=v==null?"—":key==="created"?new Date(v*1000).toLocaleDateString():typeof v==="number"?fmt(v):String(v);
      tr.append(td);
    }
    body.append(tr);
  }
  output.append(table);
  const notes=[I18N.format("전체 {0}건 중 {1}–{2}",[data.total,data.total?vhhGrid.offset+1:0,vhhGrid.offset+data.items.length])];
  if (data.sequence_index_pending) notes.push(I18N.format(" · 이전 결과 {0}건의 후보 검색을 준비하고 있습니다.",[data.sequence_index_pending]));
  if (data.sequence_index_unavailable) notes.push(I18N.format(" · 이전 결과 {0}건의 색인을 읽지 못했습니다. 서버 관리자에게 문의하세요.",[data.sequence_index_unavailable]));
  I18N.text(status,I18N.concat(notes));
  $("db-vhh-prev").disabled=!vhhGrid.offset;
  $("db-vhh-next").disabled=vhhGrid.offset+data.items.length>=data.total;
  vhhGrid.applied=params.toString();$("db-vhh-export").disabled=!data.total;
}
function setupCandidateCatalog() {
  for (const [id,candidates] of [["show-run-table",false],["show-candidate-table",true]]) action(id,async()=>{
    $("catalog-run-panel").hidden=candidates;$("catalog-candidate-panel").hidden=!candidates;
    $("show-run-table").setAttribute("aria-pressed",String(!candidates));$("show-candidate-table").setAttribute("aria-pressed",String(candidates));
    if(candidates)await refreshVhhTable();else await refreshCatalog();
  });
  action("db-vhh-apply",()=>{vhhGrid.offset=0;return refreshVhhTable();});
  for (const id of ["search","target","owner","engine","decision","mock"]) {
    const input=$("db-vhh-"+id);
    input.onkeydown=e=>{if(e.key==="Enter")$("db-vhh-apply").click();};
    input.oninput=()=>{++vhhGrid.revision;vhhGrid.applied=null;$("db-vhh-export").disabled=true;I18N.text($("db-vhh-status"),I18N.term("검색을 눌러 변경한 조건을 적용하세요."));};
  }
  for(const [id,step] of [["prev",-25],["next",25]]) $("db-vhh-"+id).onclick=()=>{
    $("db-vhh-prev").disabled=$("db-vhh-next").disabled=true;
    vhhGrid.offset=Math.max(0,vhhGrid.offset+step);refreshVhhTable().catch(e=>notice(e.message,true));
  };
  action("db-vhh-export",async()=>{
    if(vhhGrid.applied===null) return;
    const p=new URLSearchParams(vhhGrid.applied);p.set("lang",I18N.language);
    const response=await fetch("/api/candidate-catalog/export?"+p,{headers:{"X-Platform-Token":window.PLATFORM_TOKEN}});
    if(!response.ok)throw Error("Excel 내보내기에 실패했습니다. 다시 로그인한 뒤 검색하세요.");
    saveBlob(await response.blob(),"NanoCoFold-candidates.xlsx");
  });
}
let usageRequest = 0, usageCursor = null, usageOwner = "";
async function refreshLabUsage(nextPage = false) {
  if (!state.researcher || !$("lab-usage").open) return;
  const request = ++usageRequest, owner = $("catalog-owner").value.trim();
  const params = new URLSearchParams({researcher:owner});
  if (nextPage && usageCursor && usageOwner === owner) {
    params.set("before", usageCursor.before); params.set("before_id", usageCursor.before_id);
  }
  const page = await api("/api/lab/usage?" + params);
  if (request !== usageRequest) return;
  usageCursor = page.next_cursor; usageOwner = owner;
  $("lab-usage-next").hidden = !usageCursor;
  const rows = page.items.map(item => ({
    "담당자":item.researcher, "분석":item.name, "제출 시각":new Date(item.submitted * 1000).toLocaleString(),
    "계산 상태": item.reused
      ? I18N.concat([I18N.term(states[item.state] || item.state), " · ", I18N.term("재사용")])
      : I18N.term(states[item.state] || item.state)
  }));
  table($("lab-usage-rows"), rows, ["담당자", "분석", "제출 시각", "계산 상태"]);
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
function usesPublicMsa(cfg) {
  return ($("mode").value === "complex" && cfg.complex?.msa_source === "public_api") || cfg.screen?.colabfold?.msa_source === "public_api" ||
    cfg.screen?.msa_mode === "msa" || cfg.structure?.msa?.backend === "public_api" ||
    (cfg.structure?.provider === "chai1" && cfg.structure?.chai1?.msa_mode === "msa");
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
  if (forPlan) cfg = merge(state.planningDefaults || {}, cfg);
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
  if ($("analysis-preset").value === "small-four-v1" && cfg.complex) {
    cfg = merge(cfg, {complex:{msa_source:$("novice-msa").value}});
  }
  const effectiveMsa = merge(state.planningDefaults || {}, cfg);
  const publicMsa = !simulated && usesPublicMsa(effectiveMsa);
  if (!forPlan && publicMsa && !$("novice-consent").checked) throw Error("가져온 설정도 현재 입력에 대한 외부 MSA 전송 동의를 다시 확인해야 합니다.");
  if (publicMsa) cfg.screen = merge(cfg.screen || {}, {allow_public_msa_server: $("novice-consent").checked});
  if (effectiveMsa.structure?.provider === "chai1" && effectiveMsa.structure?.chai1?.msa_mode === "msa")
    cfg.structure = merge(cfg.structure, {allow_public_msa_server: !simulated && $("novice-consent").checked});
  const enteredSeed = $("seed").value;
  if ($("seed").validity?.badInput) throw Error("Seed는 0–4294967295 정수를 입력하세요.");
  const effectiveSeed = enteredSeed === "" ? cfg.run?.seed ?? state.planningDefaults?.run?.seed ?? 42 : Number(enteredSeed);
  if (!Number.isSafeInteger(effectiveSeed) || effectiveSeed < 0 || effectiveSeed >= 2 ** 32)
    throw Error("Seed는 0–4294967295 정수를 입력하세요.");
  for (const id of ["candidate-limit", "design-count"]) {
    if (id === "design-count" && $("mode").value !== "design") continue;
    if (id === "candidate-limit" && $("mode").value === "screen") continue;
    if ($(id).validity?.badInput) throw Error("후보 상한과 백본 수는 1 이상의 정수여야 합니다.");
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
  const selection = $("selection-limit");
  if (selection?.value && ["design", "mature"].includes($("mode").value)) {
    if (!Number.isSafeInteger(selection.valueAsNumber) || selection.valueAsNumber < 1)
      throw Error("최종 선별 상한은 1 이상의 정수여야 합니다.");
    cfg = merge(cfg, {rank: {final_n: selection.valueAsNumber}});
  }
  cfg = matureInputSettings(cfg);
  return merge(cfg, {
    run: {
      mode: $("mode").value,
      name: $("job-name").value,
      seed: effectiveSeed,
      mock: $("mock").checked,
      analysis_preset: $("analysis-preset").value,
    },
    screen: cfg.screen?.comparison?.enabled ? {} : {
      engine: $("screen-engine").value,
      // The server resolves the selected engine using its registered installation.
    },
  });
}
// MUTATION_SCOPE_START
function parseMutationRanges(text) {
  if (typeof text !== 'string' || !text.trim()) throw Error('치환할 CDR 잔기 번호를 입력하세요. 예: 99-103, 106');
  const ranges = text.trim().split(/[,\n]/).map(part => {
    const match = part.trim().match(/^(\d+)(?:\s*-\s*(\d+))?$/);
    if (!match) throw Error('치환 구간은 1부터 시작하는 시작-끝 번호로 입력하세요. 예: 99-103, 106');
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start)
      throw Error('치환 구간은 1부터 시작하는 시작-끝 번호로 입력하세요. 예: 99-103, 106');
    return [start, end];
  }).sort((a,b) => a[0]-b[0]);
  if (ranges.some((range,i) => i && range[0] <= ranges[i-1][1]))
    throw Error('지정 치환 구간이 겹칩니다. 각 잔기 번호를 한 번만 지정하세요.');
  return ranges;
}
// MUTATION_SCOPE_END
function matureInputSettings(cfg) {
  if ($('mature-library-settings')) $('mature-library-settings').hidden = $('mode').value !== 'mature';
  if ($('mode').value !== 'mature') return cfg;
  const selected = $('mutation-strategy')?.value || '';
  const library = cfg.mature?.library || {};
  const strategy = selected || library.strategy || state.planningDefaults?.mature?.library?.strategy || 'dms';
  if ($('random-mutation-fields')) $('random-mutation-fields').hidden = strategy !== 'random';
  const region = $('mutation-region')?.value || library.mutation_region || 'all_cdr';
  if ($('mutation-ranges-field')) $('mutation-ranges-field').hidden = region !== 'custom';
  if (!['dms', 'random'].includes(strategy)) throw Error('지원하는 치환 방식을 선택하세요.');
  if (selected) cfg = merge(cfg, {mature: {library: {strategy,
    ...(selected === 'dms' ? {mutation_region: 'all_cdr', mutation_ranges: []} : {})}}});
  const wt = $('mutation-wt')?.value || '';
  if (wt && !['include', 'exclude'].includes(wt)) throw Error('WT 대조군 포함 여부를 확인하세요.');
  if (wt || selected === 'random') cfg = merge(cfg, {rank: {include_wt_control: wt ? wt === 'include' : true}});
  if (strategy !== 'random') return cfg;
  const value = (id, fallback, maximum) => {
    const input = $(id), entered = input?.value;
    const result = entered ? Number(entered) : fallback;
    if (input?.validity?.badInput || !Number.isSafeInteger(result) || result < 1 || result > maximum)
      throw Error('변이 수와 치환 수는 허용 범위의 정수로 입력하세요.');
    return result;
  };
  // Selecting random from DMS starts with 100 variants, rather than reusing
  // the inactive 10,000-sequence random-library default. Imported random
  // settings retain their recorded size.
  const size = selected === 'random' && library.strategy !== 'random'
    ? 101 : library.size ?? state.planningDefaults?.mature?.library?.size ?? 10000;
  const count = value('mutation-count', Number.isSafeInteger(size) && size >= 2 && size <= 100000 ? size - 1 : null, 99999);
  const min = value('mutation-min', library.min_mutations ?? 1, 50);
  const max = value('mutation-max', library.max_mutations ?? 5, 50);
  if (min > max) throw Error('최대 치환 수는 최소 치환 수 이상이어야 합니다.');
  if (!['all_cdr','cdr3','custom'].includes(region)) throw Error('치환 범위는 all_cdr, cdr3, custom 중 하나여야 합니다.');
  const ranges = region === 'custom' ? parseMutationRanges($('mutation-ranges')?.value?.trim()
    || (library.mutation_ranges || []).map(([start,end]) => `${start}-${end}`).join(', ')) : [];
  return merge(cfg, {mature: {library: {strategy, size: count + 1, min_mutations: min, max_mutations: max,
    mutation_region: region, mutation_ranges: ranges},
    prefilter: {mode: 'none'}, antifold: {enabled: false}}, rank: {weights: {esm_score: 0, antifold: 0}}});
}
function resetMutationControls() {
  for (const id of ['mutation-strategy','mutation-count','mutation-min','mutation-max','mutation-wt','mutation-region','mutation-ranges'])
    if ($(id)) $(id).value = '';
}
function noviceComplexSettings(cfg) {
  const current = cfg.complex || {}, small = $("analysis-preset").value === "small-four-v1";
  const engines = small ? cfg.screen?.comparison?.engines : current.comparison_engines;
  if (!engines) return {...current, engine: current.engine || $("screen-engine").value,
    seeds: current.seeds || [Number($("seed").value)]};
  const source = small ? cfg.screen?.colabfold?.msa_source : current.msa_source ?? cfg.screen?.colabfold?.msa_source;
  if (!source) throw Error("복합체 비교에 사용할 MSA 준비 방식을 선택하세요.");
  const preserved = {...current};
  delete preserved.engine;
  return {...preserved, comparison_engines: engines, seeds: current.seeds || [Number($("seed").value)], msa: "precomputed",
    msa_source: source, allow_public_msa_server: source === "public_api" && $("novice-consent").checked,
    models_per_seed: current.models_per_seed ?? 1, recycles: current.recycles ?? 3,
    diffusion_steps: current.diffusion_steps ?? 200};
}
// QUANTITY_PLAN_START
/** @param {any} cfg @param {string} [mode] @param {number|null} [libraryCount] */
function analysisQuantityPlan(cfg = {}, mode = cfg.run?.mode, libraryCount = null) {
  const positive = value => Number.isSafeInteger(value) && value > 0 ? value : null;
  const product = values => values.every(v => Number.isSafeInteger(v) && v > 0) && Number.isSafeInteger(values.reduce((a,b)=>a*b,1)) ? values.reduce((a,b)=>a*b,1) : null;
  const screen = cfg.screen || {}, design = cfg.design || {}, mature = cfg.mature || {};
  const compared = mode === 'complex' ? cfg.complex?.comparison_engines || (screen.comparison?.enabled ? screen.comparison.engines : null) : screen.comparison?.enabled ? screen.comparison.engines : null;
  const engines = Array.isArray(compared) ? [...compared] : [mode === 'complex' ? cfg.complex?.engine || screen.engine || 'boltz2' : screen.engine || 'boltz2'];
  const seed = cfg.run?.seed ?? 42;
  const conditions = {
    boltz2: {seed: cfg.score?.boltz2?.seed ?? seed, samples: screen.engine === 'boltz2' || engines.includes('boltz2') ? cfg.score?.boltz2_diffusion_samples ?? 1 : null},
    chai1: {seed, samples: screen.num_diffn_samples ?? 1},
    colabfold: {seed, models: screen.colabfold?.num_models ?? 1, seeds: screen.colabfold?.num_seeds ?? 1},
    opendde: {seed, samples: screen.opendde?.samples ?? 1}
  };
  const staged = mode !== 'complex' && Boolean(screen.cascade?.enabled || (mode === 'design' && design.refinement?.enabled));
  const plan = {mode, generators: [], requestedBackbones: null, uniqueSequences: null, candidateUpper: null, modelUpper: null, nativeModelUpper: null,
    variantUpper: null, preparedVariants: null, libraryStrategy: null, prefilterApplied: true,
    engines, engineCount: positive(engines.length), seed, conditions: Object.fromEntries(engines.filter(engine=>conditions[engine]).map(engine=>[engine,conditions[engine]])), staged, wtIncluded: cfg.rank?.include_wt_control === true || (mode === 'mature' && screen.cascade?.enabled === true),
    rfCap: positive(design.filters?.top_n_to_score), tier2Cap: positive(screen.tier2_n), finalSelection: positive(cfg.rank?.final_n)};
  if (mode === 'complex') {
    const settings = cfg.complex || {}, seeds = settings.seeds || [seed];
    plan.seeds = Array.isArray(seeds) ? [...seeds] : null;
    plan.modelsPerSeed = positive(settings.models_per_seed ?? 1);
    plan.conditions = Object.fromEntries(engines.map(engine=>[engine,{seeds:plan.seeds,modelsPerSeed:plan.modelsPerSeed}]));
    plan.candidateUpper = 1;
    plan.modelUpper = product([plan.engineCount, positive(plan.seeds?.length), plan.modelsPerSeed]);
    plan.nativeModelUpper = plan.modelUpper;
    return plan;
  }
  if (mode === 'design') {
    const selected = design.engine === 'both' ? ['rfantibody','boltzgen'] : ['rfantibody','boltzgen'].includes(design.engine) ? [design.engine] : [];
    for (const engine of selected) {
      const backbones = positive(design.num_designs);
      const sequencesPerBackbone = positive(engine === 'rfantibody' ? design.mpnn?.seqs_per_struct ?? 1 : design.boltzgen?.inverse_fold_num_sequences ?? 1);
      const proposed = product([backbones, sequencesPerBackbone]);
      const candidates = cfg.run?.mock ? backbones : engine === 'rfantibody' ? proposed && plan.rfCap ? Math.min(proposed, plan.rfCap) : null : proposed;
      plan.generators.push({engine, backbones, sequencesPerBackbone, proposed, candidates});
    }
    if (plan.generators.length && plan.generators.every(g=>g.backbones !== null)) plan.requestedBackbones = plan.generators.reduce((sum,g)=>sum+g.backbones,0);
    if (plan.generators.length && plan.generators.every(g=>g.candidates !== null)) {
      const total = plan.generators.reduce((sum,g)=>sum+g.candidates,0);
      plan.candidateUpper = screen.comparison?.enabled ? total : plan.tier2Cap === null ? null : Math.min(total, plan.tier2Cap);
    }
  } else if (mode === 'mature') {
    const top = positive(mature.prefilter?.top_n), random = mature.library?.strategy === 'random';
    const size = positive(mature.library?.size);
    const variants = random ? size !== null && size > 1 ? size - 1 : null : positive(mature.library?.max_variants);
    // Native `none` keeps the full library; mock mode still applies top_n.
    // random.size includes WT, while DMS.max_variants excludes it.
    const unfiltered = mature.prefilter?.mode === 'none' && cfg.run?.mock !== true;
    const selected = unfiltered ? variants : top === null ? null : variants === null ? top : Math.min(top, variants);
    plan.libraryStrategy = random ? 'random' : 'dms';
    plan.preparedVariants = variants;
    plan.prefilterApplied = !unfiltered;
    plan.variantUpper = selected;
    plan.candidateUpper = selected === null ? null : selected + Number(plan.wtIncluded);
  } else if (mode === 'screen') plan.candidateUpper = positive(libraryCount);
  if (!staged) plan.modelUpper = product([plan.candidateUpper, plan.engineCount]);
  if (plan.modelUpper !== null) {
    const samples = engines.map(engine=>engine === 'colabfold' ? product([positive(conditions.colabfold.models),positive(conditions.colabfold.seeds)]) : positive(conditions[engine]?.samples));
    if (samples.every(v=>v !== null)) plan.nativeModelUpper = product([plan.candidateUpper,samples.reduce((a,b)=>a+b,0)]);
  }
  return plan;
}
function largeQuantityPlan(plan) {
  return plan.modelUpper === null || plan.nativeModelUpper === null || plan.modelUpper >= 100 || plan.nativeModelUpper >= 100 || plan.requestedBackbones >= 100;
}
// QUANTITY_PLAN_END
function noviceModelPlan(cfg, mode, libraryCount) {
  const plan = analysisQuantityPlan(cfg, mode, libraryCount);
  if (plan.staged) return '다단계 선별 경로입니다. 전체 입력·생성 수와 단계별 확정 평가 수를 실행 기록에서 구분합니다.';
  if (plan.modelUpper === null) return '계획 미확정 · 입력과 적용 설정을 확인하세요.';
  if (mode === 'complex') return `계획 모델 ${plan.modelUpper}개 · 실제 성공·실패·미평가는 결과에서 구분합니다.`;
  if (mode === 'screen') return `입력 VHH 전체 ${libraryCount}개 × 엔진 ${plan.engineCount}개 = 계획 ${plan.modelUpper}모델 · 후보를 자동 생략하지 않습니다.`;
  if (mode === 'mature') {
    if (!plan.prefilterApplied && plan.libraryStrategy === 'random')
      return plan.wtIncluded
        ? `변이 ${plan.variantUpper}개와 WT 1개 × 엔진 ${plan.engineCount}개 = 계획 ${plan.modelUpper}모델 · 생성한 변이를 모두 평가합니다.`
        : `변이 ${plan.variantUpper}개 × 엔진 ${plan.engineCount}개 = 계획 ${plan.modelUpper}모델 · WT 제외 · 생성한 변이를 모두 평가합니다.`;
    const prepared = cfg.mature?.library?.max_variants;
    const prefix = Number.isSafeInteger(prepared) && prepared > 0 ? `변이 최대 ${prepared}개 준비 · ` : '';
    return prefix + (plan.wtIncluded
      ? `최대 ${plan.variantUpper}개와 WT × 엔진 ${plan.engineCount}개 = 최대 ${plan.modelUpper}모델 · 실제 선별 수는 실행 후 확정됩니다.`
      : `변이 후보 최대 ${plan.variantUpper}개 × 엔진 ${plan.engineCount}개 = 최대 ${plan.modelUpper}모델 · WT 제외.`);
  }
  return `최대 계획 후보 ${plan.candidateUpper}개 × 엔진 ${plan.engineCount}개 = 최대 ${plan.modelUpper}모델 · 실제 생성·선별 이후 확정 수를 기록합니다.`;
}
function quantitySource(id, path) {
  if (id === 'seed' && $(id)?.dataset?.quantitySource !== 'direct') {
    if ($(id)?.dataset?.quantitySource === 'imported') return '가져온 설정·고급 설정';
    if ($(id)?.dataset?.quantitySource === 'restored') return '복원한 입력';
    if ($('analysis-preset').value === 'small-four-v1') return '작은 시작 설정';
  }
  if ($(id)?.value) return '직접 입력';
  let current;
  try { current = JSON.parse($('advanced').value || '{}'); } catch { return '설정 확인 필요'; }
  for (const part of path.split('.')) current = current && Object.hasOwn(current, part) ? current[part] : undefined;
  if (current !== undefined) return '가져온 설정·고급 설정';
  return $('analysis-preset').value === 'small-four-v1' ? '작은 시작 설정' : $('analysis-preset').value === 'researcher_exploration' ? '연구자 탐색' : '작업 공간 기본값';
}
function clearScalePreview() {
  for (const id of ['design-count','candidate-limit','selection-limit','mutation-count','mutation-min','mutation-max']) {
    if ($(id)) I18N.attribute($(id), 'placeholder', I18N.term('설정 확인 필요'));
    if ($(id+'-current')) I18N.text($(id+'-current'), I18N.term('적용값 미확정 · 설정을 확인하세요.'));
  }
  if ($('seed-current')) I18N.text($('seed-current'), I18N.term('적용값 미확정 · 설정을 확인하세요.'));
  if ($('quantity-conditions')) I18N.text($('quantity-conditions'), I18N.term('계획 미확정 · 입력과 적용 설정을 확인하세요.'));
  if ($('quantity-breakdown')) I18N.text($('quantity-breakdown'), I18N.term('계획 미확정 · 입력과 적용 설정을 확인하세요.'));
  if ($('mutation-current')) I18N.text($('mutation-current'), I18N.term('적용값 미확정 · 설정을 확인하세요.'));
  if ($('mutation-scope-current')) I18N.text($('mutation-scope-current'), I18N.term('적용값 미확정 · 설정을 확인하세요.'));
}
function applyAnalysisSettings(cfg) {
  resetMutationControls();
  $('advanced').value = JSON.stringify(cfg, null, 2);
  $('analysis-preset').value = 'standard';
  for (const id of ['candidate-limit','design-count','selection-limit']) if ($(id)) $(id).value = '';
  $('mode').value = cfg.run.mode;
  $('seed').value = String(cfg.run.seed ?? 42);
  if ($('seed').dataset) $('seed').dataset.quantitySource = 'imported';
  $('mock').checked = cfg.run.mock === true;
  const engine = $('screen-engine'), selected = cfg.screen?.engine;
  if (typeof selected === 'string' && selected) {
    if (engine.options && ![...engine.options].some(option=>option.value===selected)) {
      const option = el('option', selected); option.value = selected; engine.append(option);
    }
    engine.value = selected;
  }
  if ($('novice-consent')) $('novice-consent').checked = false;
  invalidateInput(); fields();
}
async function findAcceptedAnalysis(submissionId, stillCurrent) {
  // A lost response is ambiguous. Only attach a job carrying this exact receipt.
  for (let attempt = 0; attempt < 8 && stillCurrent(); attempt++) {
    if (attempt) await new Promise(resolve => setTimeout(resolve, 2000));
    if (!stillCurrent()) return null;
    try {
      const page = await api('/api/jobs?limit=100', undefined, {timeoutMs: 5000});
      const job = [...(page.active_jobs || []), ...page.jobs].find(
        item => item.kind === 'analysis' && item.submission_id === submissionId);
      if (job) return job;
    } catch (error) {
      if (error.responseRejected) throw error;
    }
  }
  return null;
}
async function submitReviewedAnalysis() {
  if (state.analysisSubmitting) return;
  if (!state.input) throw Error('먼저 입력·적용 설정을 확인하세요.');
  if (!state.readiness) throw Error('현재 입력의 필수 환경 점검을 먼저 통과해야 합니다.');
  const input = state.input, revision = state.inputRevision;
  const mode = input.config.run.mode;
  const libraryCount = mode === 'screen' ? noviceFasta($('vhh-fasta').value).length : null;
  const plan = analysisQuantityPlan(input.config, mode, libraryCount);
  state.analysisSubmitting = true;
  try {
    if (largeQuantityPlan(plan)) {
      const details = I18N.value(I18N.term(noviceModelPlan(input.config, mode, libraryCount)));
      const message = I18N.value(I18N.format('분석 규모 확인\n{0}\n요청 백본 합계: {1}\n엔진 내부 예측 요청 상한: {2}\n고유 서열 수는 생성 후 확정됩니다. 엔진별 샘플 설정은 적용 설정에 보존됩니다.\n이 규모로 작업을 제출하시겠습니까?', [details, plan.requestedBackbones ?? I18N.value(I18N.term('해당 없음')), plan.nativeModelUpper ?? I18N.value(I18N.term('미확정'))]));
      if (typeof window.confirm !== 'function') throw Error('규모 확인창을 열지 못했습니다. 브라우저에서 다시 시도하세요.');
      if (!await window.confirm(message)) return;
    }
    if (revision !== state.inputRevision || input !== state.input) throw Error('입력이나 설정이 변경되었습니다. 다시 검토하세요.');
    const budget = $('budget').value;
    const submissionId = state.submissionId || (state.submissionId = crypto.randomUUID().replaceAll('-', ''));
    const navigation = state.navigationRequest;
    const stillCurrent = () => state.input === input && revision === state.inputRevision && navigation === state.navigationRequest;
    const payload = {submission_id:submissionId, reuse_results:!$('force-rerun').checked,
      input_id:input.id, mode, name:$('job-name').value, mock:input.config.run.mock,
      ...(budget ? {budget_seconds:Number(budget)*60}: {})};
    notice('서버에서 분석 입력과 설치 파일을 확인 중입니다. 몇 분 걸릴 수 있습니다. 다시 누르지 않아도 접수되면 작업 화면이 열립니다.');
    let job;
    try {
      // Cold verification of shared model files can take several minutes on a lab server.
      job = await api('/api/jobs', payload, {timeoutMs:600000});
    } catch (error) {
      if (error.responseRejected) throw error;
      if (!stillCurrent()) return;
      notice('서버에서 이 요청의 작업을 찾고 있습니다.');
      job = await findAcceptedAnalysis(submissionId, stillCurrent);
      if (!stillCurrent()) return;
      if (!job) throw error;
    }
    if (!stillCurrent()) {
      // A confirmed submission can finish after the researcher switches tabs.
      // Retire that form without changing the page or touching newer edits.
      if (state.input === input && revision === state.inputRevision) state.nextAnalysis?.();
      return;
    }
    view('jobs'); state.nextAnalysis?.(); state.submissionNoticeJob=job.id;
    await selectJob(job.id);
    notice('작업을 등록했습니다. 브라우저를 닫아도 실행됩니다.');
  } finally { state.analysisSubmitting=false; renderInputReadiness(); }
}
function renderAnalysisScale(cfg, mode) {
  const known = value => Number.isSafeInteger(value) && value > 0 ? value : I18N.term("입력 검토에서 확인");
  const design = cfg.design || {}, screen = cfg.screen || {}, mature = cfg.mature || {};
  const customSeeds = mode === 'complex' && Array.isArray(cfg.complex?.seeds);
  $("seed").hidden = customSeeds;
  const seedLabel = document.querySelector('label[for="seed"]'); if (seedLabel) seedLabel.hidden = customSeeds;
  if ($("seed-current")) I18N.text($("seed-current"), customSeeds
    ? I18N.format("복합체 Seed 목록: {0} · 가져온 목록은 고급 설정에서 수정합니다.", [cfg.complex.seeds.join(', ')])
    : I18N.format("적용값: {0} · 출처: {1}", [cfg.run?.seed ?? 42, I18N.term(quantitySource('seed', 'run.seed'))]));
  const defaults = {"design-count": design.num_designs,
    "candidate-limit": mode === "mature" ? mature.prefilter?.top_n : design.filters?.top_n_to_score,
    "selection-limit": cfg.rank?.final_n};
  for (const [id, value] of Object.entries(defaults)) {
    if ($(id)) I18N.attribute($(id), "placeholder", I18N.format("현재 설정 {0} · 비우면 유지", [known(value)]));
    const path = id === "design-count" ? "design.num_designs" : id === "selection-limit" ? "rank.final_n" : mode === "mature" ? "mature.prefilter.top_n" : "design.filters.top_n_to_score";
    if ($(id + "-current")) I18N.text($(id + "-current"), I18N.format("적용값: {0} · 출처: {1}", [known(value), I18N.term(quantitySource(id, path))]));
  }
  const quantities = analysisQuantityPlan(cfg, mode, null);
  if ($("quantity-breakdown")) {
    const lines = quantities.generators.map(g => el("p", I18N.format("{0}: 요청 백본 {1}개 · 백본당 제안 서열 {2}개 · 제안 예산 최대 {3}개", [g.engine, known(g.backbones), known(g.sequencesPerBackbone), known(g.proposed)])));
    lines.push(el("p", I18N.term("고유 후보 서열 수: 생성·중복 확인 후 확정")));
    $("quantity-breakdown").replaceChildren(...lines);
  }
  if ($("quantity-conditions")) I18N.text($("quantity-conditions"), JSON.stringify(quantities.conditions, null, 2));
  I18N.text($("design-count-help"), I18N.format("백본은 단백질의 골격 구조입니다. 생성기별 백본 {0}개 · RFantibody 백본당 서열 {1}개 · BoltzGen 백본당 서열 {2}개. 실제 생성 수는 결과에서 확인합니다.",
    [known(design.num_designs), known(design.mpnn?.seqs_per_struct), known(design.boltzgen?.inverse_fold_num_sequences)]));
  const unfiltered = mode === 'mature' && !quantities.prefilterApplied;
  if ($('mature-library-settings')) {
    $('mature-library-settings').hidden = mode !== 'mature';
    $('random-mutation-fields').hidden = mature.library?.strategy !== 'random';
    if ($('mutation-ranges-field')) $('mutation-ranges-field').hidden = mature.library?.mutation_region !== 'custom';
    if ($('mutation-scope-current')) I18N.text($('mutation-scope-current'), I18N.format('치환 범위: {0}', [
      I18N.term(mature.library?.mutation_region === 'cdr3' ? 'CDR3만' : mature.library?.mutation_region === 'custom' ? '지정 CDR 구간' : '전체 CDR'),
    ]));
    for (const [id, value] of [['mutation-count', quantities.preparedVariants], ['mutation-min', mature.library?.min_mutations ?? 1], ['mutation-max', mature.library?.max_mutations ?? 5]])
      if ($(id)) I18N.attribute($(id), 'placeholder', known(value));
    I18N.text($('mutation-current'), I18N.format('적용 방식: {0} · WT {1}',
      [I18N.term(mature.library?.strategy === 'random' ? 'CDR 무작위 다중 잔기 치환' : 'CDR 단일 잔기 치환 (DMS)'), I18N.term(quantities.wtIncluded ? '포함' : '제외')]));
  }
  for (const node of [$('candidate-limit'), $('candidate-limit-help'), $('candidate-limit-current'), document.querySelector('label[for="candidate-limit"]')])
    if (node) node.hidden = !['design','mature'].includes(mode) || unfiltered;
  const limitLabel = document.querySelector('label[for="candidate-limit"]');
  if (limitLabel) I18N.text(limitLabel, I18N.term(mode === 'mature' ? '구조 평가할 변이 후보 상한' : screen.comparison?.enabled ? 'RFantibody 후속 평가 후보 상한' : '후속 평가 후보 수 설정'));
  I18N.text($("candidate-limit-help"), mode === "mature"
    ? I18N.format("변이 후보 최대 {0}개를 평가하며 WT는 별도 대조군입니다.", [known(mature.prefilter?.top_n)])
    : I18N.format("RFantibody 후속 후보 최대 {0}개 · 단일 엔진 평가 최대 {1}개. BoltzGen 후보 수는 백본·서열 설정을 따르며, 비교에서는 후속 후보마다 엔진별 모델을 계산합니다.",
      [known(design.filters?.top_n_to_score), known(screen.tier2_n)]));
  I18N.text($("selection-limit-help"), I18N.format("최종 선별은 최대 {0}개입니다. 여러 엔진 비교에서는 엔진별로 적용하며, 적격 기준과 다양성 제한에 따라 줄어들 수 있습니다.", [known(cfg.rank?.final_n)]));
  const comparison = mode === "complex" ? cfg.complex?.comparison_engines || (screen.comparison?.enabled ? screen.comparison.engines : null)
    : screen.comparison?.enabled ? screen.comparison.engines : null;
  $("screen-engine").hidden = Array.isArray(comparison);
  document.querySelector('label[for="screen-engine"]').hidden = Array.isArray(comparison);
  const small = $("analysis-preset").value === "small-four-v1";
  I18N.text($("analysis-scale-note"), I18N.term(small
    ? "작은 시작 설정은 설치 확인용 소규모 분석입니다. 아래 수를 바꾸면 규모를 늘릴 수 있습니다. 후보 서열 수·예측 모델 수·최종 선별 수는 서로 다릅니다."
    : "현재 설정의 생성·평가 규모를 확인하세요. 백본 수와 서열 수는 다를 수 있으며, 후보가 늘면 계산 시간과 저장 공간도 늘어납니다."));
  for (const [id, visible] of [["design-count-help", mode === "design"], ["design-count-current", mode === "design"], ["candidate-limit-help", ["design", "mature"].includes(mode) && !unfiltered]]) $(id).hidden = !visible;
  $("analysis-scale").hidden = mode === "complex" || mode === "screen";
}
function fields() {
  const mode = $("mode").value;
  for (const [id, visible] of [["candidate-limit", ["design", "mature"].includes(mode)], ["design-count", mode === "design"], ["selection-limit", mode !== "complex"]]) {
    if (!$(id)) continue;
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
  return I18N.format("{0} · 원본 {1}{2} · {3} · {4} aa", [chain.id, chain.original_id, author, chain.name || I18N.term("단백질"), chain.length]);
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
    체인: chainLabel(c), 관측: c.length, 전체: c.sequence_review?.full_length ?? I18N.term("정보 없음"),
    미관측: c.sequence_review?.missing_positions?.length ?? I18N.term("대응 미확인"),
    수정잔기: c.sequence_review?.modifications?.length ?? 0,
    불명확: c.sequence_review?.unknown_positions?.join(", ") || I18N.term("없음"),
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
      el("p",I18N.format("MSA: {0} · 공용 전송 동의: {1}", [complex ? (cfg.complex.msa_source ? cfg.complex.msa_source : cfg.complex.msa) : (cfg.screen.engine === "colabfold" ? cfg.screen.colabfold?.msa_source : cfg.screen.engine === "chai1" ? cfg.screen.msa_mode : cfg.structure?.msa?.backend), cfg.screen.allow_public_msa_server===true ? I18N.term("현재 입력에 동의") : I18N.term("외부 전송 없음")])),
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
    ? "GPU·프로그램·모델 파일을 확인하고 있습니다. 이 화면에서 완료를 기다려 주세요."
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
/* CANCEL_CONFIRMATION_START */
let pendingCancellation = null;
function dismissCancellation() {
  pendingCancellation?.(false);
}
function confirmCancellation({name, id, batch = false, pendingResume = false}) {
  // One confirmation at a time; the displayed target never follows polling/selection.
  if (pendingCancellation) return Promise.resolve(false);
  return new Promise(resolve => {
    const previousFocus = document.activeElement;
    const dialog = el("dialog", undefined, "cancel-confirmation");
    dialog.id = "cancel-confirmation";
    dialog.setAttribute("aria-labelledby", "cancel-confirmation-title");
    dialog.setAttribute("aria-describedby", "cancel-confirmation-target cancel-confirmation-detail");
    const title = el("h2", I18N.term(batch ? "배치의 미완료 분석을 모두 취소할까요?" : pendingResume ? "대기 중인 재개를 취소할까요?" : "이 작업을 취소할까요?"));
    title.id = "cancel-confirmation-title";
    const target = el("p", undefined, "cancel-confirmation-target");
    target.textContent = String(name || id);
    target.id = "cancel-confirmation-target";
    // User-supplied names/IDs remain literal in either language.
    target.setAttribute("data-i18n-ignore", "");
    const identity = el("code");
    identity.textContent = String(id);
    target.append(identity);
    const detail = el("p", I18N.term(pendingResume
      ? "이 재개 요청을 취소합니다. 저장된 결과는 보존합니다. 나중에 다시 재개할 수 있으며, 미완료 모델은 다시 계산할 수 있습니다."
      : batch
        ? "이 배치의 미완료 작업에 취소를 요청합니다. 완료된 작업과 저장된 결과는 보존합니다. 다시 재개할 때 미완료 모델은 다시 계산할 수 있습니다."
        : "이 작업에 취소를 요청합니다. 저장된 결과는 보존합니다. 다시 재개할 때 미완료 모델은 다시 계산할 수 있습니다."));
    detail.id = "cancel-confirmation-detail";
    const actions = el("div", undefined, "cancel-confirmation-actions");
    const keep = el("button", I18N.term(pendingResume ? "재개 계속 대기" : "계속 진행"), "secondary");
    const confirm = el("button", I18N.term(batch ? "미완료 작업 취소" : pendingResume ? "재개 요청 취소" : "작업 취소"), "cancel-confirmation-submit");
    keep.type = confirm.type = "button";
    keep.id = "cancel-confirmation-keep";
    confirm.id = "cancel-confirmation-submit";
    keep.autofocus = true;
    let settled = false;
    const finish = accepted => {
      if (settled) return;
      settled = true;
      confirm.disabled = keep.disabled = true;
      pendingCancellation = null;
      window.removeEventListener("popstate", dismissCancellation);
      window.removeEventListener("hashchange", dismissCancellation);
      window.removeEventListener("pagehide", dismissCancellation);
      if (dialog.open) dialog.close();
      dialog.remove();
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(accepted === true);
    };
    pendingCancellation = finish;
    keep.onclick = () => finish(false);
    confirm.onclick = () => finish(true);
    dialog.oncancel = event => { event.preventDefault(); finish(false); };
    dialog.onclose = () => finish(false);
    dialog.onclick = event => { if (event.target === dialog) finish(false); };
    dialog.onkeydown = event => {
      // There is no form/default submit. Enter only confirms after explicitly
      // focusing the destructive button, never from the initial safe focus.
      if (event.key === "Enter" && document.activeElement !== confirm && document.activeElement !== keep) event.preventDefault();
    };
    actions.append(keep, confirm);
    dialog.append(title, target, detail, actions);
    document.body.append(dialog);
    window.addEventListener("popstate", dismissCancellation);
    window.addEventListener("hashchange", dismissCancellation);
    window.addEventListener("pagehide", dismissCancellation);
    try { dialog.showModal(); keep.focus(); }
    catch { finish(false); notice("취소 확인창을 열지 못했습니다. 페이지를 새로고침한 뒤 다시 시도하세요.", true); }
  });
}
function cancellationExpectation(job) {
  return {job_id:job.id,state:job.state,pid:job.pid??null,
    process_stamp:job.process_stamp??null,started:job.started??null,
    resume:job.resume===true,history_length:(job.history||[]).length};
}
function cancellationGeneration(job) {
  return JSON.stringify(cancellationExpectation(job));
}
function cancellationIsCurrent(before, latest) {
  return ["queued", "running"].includes(latest?.state) &&
    cancellationGeneration(before) === cancellationGeneration(latest);
}
function cancellationChanged() {
  notice("작업 상태가 바뀌었습니다. 현재 상태를 확인한 뒤 취소를 다시 선택하세요.", true);
}
async function verifyCancellationTarget(job) {
  const latest = await api("/api/jobs/" + job.id, undefined, {timeoutMs: 15000});
  if (cancellationIsCurrent(job, latest)) return true;
  cancellationChanged();
  return false;
}
function batchCancellationGeneration(batch) {
  return JSON.stringify(batch.rows.filter(row => row.job && ["queued", "running", "cancelling"].includes(row.job.state))
    .map(row => [row.job_id, cancellationGeneration({...row.job, id: row.job_id}), row.job.state === "cancelling"])
    .sort((a, b) => String(a[0]).localeCompare(String(b[0]))));
}
/* CANCEL_CONFIRMATION_END */
async function selectJob(identifier, remember = true) {
  if (remember) ++state.navigationRequest;
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
    const page = await api("/api/jobs", undefined, {timeoutMs: 15000});
    state.jobs = [...new Map([...(page.jobs || []), ...(page.active_jobs || [])].map(job => [job.id, job])).values()]
      .sort((a, b) => b.created - a.created || String(b.id).localeCompare(String(a.id)));
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
        notice(I18N.format(job.result_id ? "{0} · 결과 열기로 결과를 확인하세요." : "{0} · 작업 상세와 로그에서 종료 사유를 확인하세요.", [I18N.term(states[job.state] || job.state)]), job.state!=="complete");
        state.submissionNoticeJob=null;
      }
      $("job-detail").replaceChildren(
        el(
          "p",
          I18N.format("{0} · {1} · {2} · {3}초", [(states[job.state] ? I18N.term(states[job.state]) : job.state), I18N.term(jobPhaseCaption(job)), I18N.term(jobProgressCaption(job)), Math.round(job.elapsed_seconds || 0)]),
        ),
      );
      if (job.researcher) $("job-detail").append(el("p", I18N.concat([I18N.term("담당자"), " · ", job.researcher])));
      if (job.state === "queued" && job.phase === "waiting_for_gpu") {
        const reading = job.gpu_reading || {};
        const label = {capacity:"다음 분석을 시작할 GPU 메모리를 기다립니다.", exclusive:"설치 또는 단독 GPU 작업이 끝나기를 기다립니다.", admission:"앞선 작업의 GPU 메모리 예약을 확인하고 있습니다.", policy:"서버의 GPU 메모리 예약 설정을 확인하세요.", process:"다른 계산이 GPU를 사용하고 있습니다.", memory:"GPU 여유 메모리를 기다립니다.",
          utilization:"GPU 사용률이 낮아지기를 기다립니다.", telemetry:"GPU 상태를 읽을 수 없어 다시 확인합니다."};
        const note = el("p", I18N.term(label[reading.reason] || "앞선 작업이 GPU를 사용하고 있습니다."));
        if (Number.isFinite(reading.free_mib))
          note.append(el("span", I18N.format(" 여유 {0}/{1} MiB · 사용률 {2}%", [reading.free_mib, reading.total_mib, reading.utilization])));
        if (Number.isFinite(reading.available_mib))
          note.append(el("span", I18N.format(" · 예약 반영 후 여유 {0} MiB / 이 작업에 필요한 예약 {1} MiB", [reading.available_mib, reading.job_mib])));
        note.append(el("span", I18N.term(" 자리가 나면 자동으로 시작합니다.")));
        $("job-detail").append(note);
      }
      if (job.reused_result_id) $("job-detail").append(el("p", I18N.format("저장 결과 연결 · 새 계산 0초 · 원본 {0}{1}. 원래 계산 시간 {2}", [job.reused_result_id, job.reuse_kind === "view_only" ? I18N.term(" · 핵심 보관본") : "", job.source_elapsed_seconds === null ? I18N.term("미기록") : I18N.concat([Math.round(job.source_elapsed_seconds || 0), I18N.term("초")])])));
      if (etaText(job)) $("job-detail").append(el("p", I18N.term(etaText(job)), "eta-note"));
      if (job.mock)
        $("job-detail").append(el("span", I18N.term("MOCK · 합성 결과"), "badge"));
      if (job.error)
        $("job-detail").append(el("div", I18N.term(job.error), "warning error"));
      const actionKey = JSON.stringify([job.id, job.state, Boolean(job.can_resume), job.kind, job.result_id || null, cancellationGeneration(job)]);
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
          (state.researcher && job.researcher !== state.researcher) ||
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
          const cancelTarget = operation === "cancel" ? JSON.parse(JSON.stringify(job)) : null;
          if (operation === "cancel" && !await confirmCancellation({name: job.name, id: job.id, pendingResume: job.state === "queued" && (job.resume || job.phase === "resume_validation")})) return;
          if (state.jobOperations[job.id]) return;
          const body = cancelTarget ? {expected_cancel:cancellationExpectation(cancelTarget)} : extension ? {additional_budget_seconds: Number(extension.value) * 60} : {};
          if (extension && (!Number.isFinite(body.additional_budget_seconds) || body.additional_budget_seconds <= 0)) {
            notice("추가 시간은 0보다 큰 숫자로 입력하세요.", true);
            return;
          }
          state.jobOperations[job.id] = operation;
          button.disabled = true;
          try {
            if (cancelTarget && !await verifyCancellationTarget(cancelTarget)) return;
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
        $("job-detail").append(el("p", I18N.term("환경 확인이 완료됐습니다. 분석 준비하기를 눌러 입력을 준비하세요."), "job-next-step"));
        if (rebuildActions) {
          const button = el("button", I18N.term("분석 준비하기"));
          button.onclick = () => { view("input"); state.showInputReview?.(); };
          $("job-actions").prepend(button);
        }
      } else if (job.kind === "analysis" && !job.result_id) {
        $("job-detail").append(el("p", I18N.term(["queued", "running", "cancelling"].includes(job.state)
          ? job.state === "cancelling"
            ? "작업을 종료하고 있습니다. 취소로 표시될 때까지 기다려 주세요."
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
// HANDOFF_PRESENTATION_START
function resultHandoffView(summary) {
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const d = summary?.handoff_diagnostics;
  const saved = summary?.state?.phases?.final?.handoff_gate ?? d?.recorded_summary;
  const recorded = saved && typeof saved.ok === "boolean" && count(saved.n_errors) !== null && count(saved.n_warnings) !== null
    && saved.ok === (saved.n_errors === 0) ? saved : null;
  const entries = list => Array.isArray(list) && list.every(item => item && typeof item.code === "string" && typeof item.message === "string");
  const detailed = d?.detail_status === "available" && typeof d.ok === "boolean" && entries(d.errors) && entries(d.warnings)
    && d.n_errors === d.errors.length && d.n_warnings === d.warnings.length && d.ok === (d.errors.length === 0);
  const consistent = detailed && (!recorded || (recorded.ok === d.ok && recorded.n_errors === d.n_errors && recorded.n_warnings === d.n_warnings));
  const status = consistent ? d.ok ? "passed" : "blocked" : recorded?.ok === false ? "blocked" : "unknown";
  return {status, detail_status: consistent ? "available" : d?.detail_status === "available" ? "invalid" : d?.detail_status || "missing",
    n_errors: consistent ? d.n_errors : recorded ? recorded.n_errors : null,
    n_warnings: consistent ? d.n_warnings : recorded ? recorded.n_warnings : null,
    errors: consistent ? d.errors : [], warnings: consistent ? d.warnings : [],
    source: typeof d?.source === "string" ? d.source : "05_final/handoff_gate.json",
    source_sha256: typeof d?.source_sha256 === "string" ? d.source_sha256 : null,
    empty_msa: summary?.mode === "complex" ? (summary?.conditions?.msa ?? summary?.config?.complex?.msa) === "empty" : summary?.config?.structure?.msa?.backend === "empty",
    visible: summary?.mode !== "complex" || !!recorded || (d && d.detail_status !== "missing")};
}
// HANDOFF_PRESENTATION_END

function renderHandoffStatus(summary) {
  const gate = resultHandoffView(summary);
  if (!gate.visible) return;
  const panel = el("section", undefined, "report-guide");
  panel.setAttribute("data-handoff-status", gate.status);
  panel.append(el("h3", I18N.term("계산 상태와 별도의 인계 확인")));
  panel.append(el("strong", I18N.term(gate.status === "passed" ? "저장된 인계 검사 통과" : gate.status === "blocked" ? "인계 미완료" : "인계 준비 상태 미확인")));
  panel.append(el("p", I18N.format("기록 오류 {0}건 · 경고 {1}건", [gate.n_errors ?? I18N.term("미기록"), gate.n_warnings ?? I18N.term("미기록")])));
  panel.append(el("p", I18N.term("계산 완료는 인계 승인이나 실험 검증을 뜻하지 않습니다. 저장된 판정만 표시하며 재검증·자동 승인을 하지 않습니다.")));
  if (gate.empty_msa) panel.append(el("p", I18N.term("MSA 없이 실행한 기록입니다. 예측 품질의 한계이며 인계 확인으로 해소되지 않습니다."), "warning"));
  if (gate.detail_status !== "available") panel.append(el("p", I18N.term(gate.detail_status === "missing"
    ? "상세 인계 JSON이 없어 항목별 이유를 확인할 수 없습니다. 미확인을 오류 0건이나 통과로 해석하지 마세요."
    : "상세 인계 JSON을 신뢰할 수 없습니다. 파일 형식·접근 상태 또는 기록 요약과의 불일치를 확인하세요."), "warning"));
  for (const [key, label] of [["errors", "인계 차단 사유"], ["warnings", "저장된 인계 경고"]]) {
    if (!gate[key].length) continue;
    panel.append(el("h4", I18N.term(label)));
    const list = el("ul");
    for (const item of gate[key]) {
      const row = el("li");
      row.append(el("code", item.code), document.createTextNode(" · "), el("span", I18N.bilingual(item.message_i18n?.ko ?? item.message, item.message_i18n?.en ?? item.message)));
      if (typeof item.path === "string") row.append(el("code", " · " + item.path));
      list.append(row);
    }
    panel.append(list);
  }
  const detail = el("details");
  detail.append(el("summary", I18N.term("저장된 진단 JSON·출처")), el("pre", JSON.stringify(summary.handoff_diagnostics ?? {recorded_summary: summary.state?.phases?.final?.handoff_gate ?? null, detail_status: "missing"}, null, 2)));
  panel.append(detail);
  $("result-summary").append(panel);
}

function resultMsaCaption(summary, observations = []) {
  const missing = "MSA 출처 미기록";
  // Completed preparation records describe actual inputs; configured defaults do not.
  const comparison = summary.state?.phases?.comparison_msa;
  const comparisonSource = comparison?.mode === "exact_cached_monomers" &&
    /^[a-f0-9]{64}$/i.test(comparison.manifest_sha256 || "") &&
    typeof comparison.manifest === "string" && comparison.manifest.startsWith("01_msa/") &&
    ["cache", "local_mmseqs2", "public_api"].includes(comparison.provider)
    ? comparison.provider : undefined;
  if (summary.msa_provenance?.method === "shared_parent_homologs" && observations.length &&
      observations.every(row => ["precomputed", "precomputed_unpaired"].includes(row.msa_used ?? row.msa)))
    return "사전 준비 MSA · WT 상동 서열 재사용";
  const caption = (mode, source) => {
    if (["empty", "single", "chai1_single", "chai1_single_fallback"].includes(mode)) return "MSA 없음 · 서열만 사용";
    if (mode === "partial_precomputed") return "일부 체인에 사전 준비 MSA 사용";
    if (["public_api", "explicitly_authorized_api_cache", "chai1_public_msa"].includes(mode)) return "API에서 확보한 MSA";
    if (!["precomputed", "precomputed_unpaired", "chai1_local_msa", "local", "local_mmseqs2", "public_mmseqs2", "mmseqs", "colabfold_search", "cache"].includes(mode)) return missing;
    if (["public_api", "explicitly_authorized_api_cache"].includes(source)) return "API에서 확보한 MSA";
    if (["cache", "local_mmseqs2", "public_mmseqs2"].includes(source)) return "로컬·캐시 MSA 실행";
    return ["precomputed", "precomputed_unpaired", "chai1_local_msa"].includes(mode) ? "사전 준비 MSA · 출처 미기록" : missing;
  };
  // Actual per-model records take precedence over configured defaults, including
  // a cache miss or an engine's single-sequence fallback. Do not flatten a mix.
  if (observations.length) {
    const values = new Set(observations.map(row => caption(row.msa_used ?? row.msa,
      row.msa_source ?? (summary.mode === "complex" ? summary.conditions?.msa_source :
        row.comparison === true ? comparisonSource : undefined))));
    return values.size > 1 ? "MSA 조건 혼합 · 관측별 기록 확인" : [...values][0];
  }
  const complex = summary.mode === "complex";
  const mode = summary.conditions?.msa ?? (complex ? summary.config?.complex?.msa : undefined);
  const source = summary.conditions?.msa_source ?? (complex ? summary.config?.complex?.msa_source : undefined);
  if (mode != null) return caption(mode, source);
  if (complex) return missing;
  const engine = summary.conditions?.engine ?? summary.config?.screen?.engine;
  if (engine === "colabfold") {
    const config = summary.config?.screen?.colabfold;
    return caption(config?.msa_mode ?? config?.msa_source, config?.msa_source);
  }
  // Old reports may lack model-level MSA records. An explicit empty backend is
  // unambiguous; a configured cache/search is not proof that an MSA was used.
  if (["boltz2", "opendde"].includes(engine) && summary.config?.structure?.msa?.backend === "empty")
    return caption("empty");
  if (engine === "chai1" && summary.config?.screen?.msa_mode === "single") return caption("single");
  return missing;
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
  state.catalogCandidateSearch = null;
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
  state.rows = rows.map(row => ({...row, recorded_engine: row.predictor_engine || row.engine, seed: recordedCandidateSeed({...row, predictor_engine: row.predictor_engine || row.engine || summary.conditions?.engine}, observations), predictor_engine: row.predictor_engine || row.engine || summary.conditions?.engine || "unknown", design_origin: row.design_origin || row.parent_engine || row.source || null}));
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
  const msaCaption = resultMsaCaption(summary, observations);
  $("result-input").replaceChildren(el("p", I18N.format("실행: {0} · 모드: {1} · {2}", [(summary.name || summary.id ? (summary.name ? summary.name : summary.id) : I18N.term("미기록")), (summary.mode ? summary.mode : I18N.term("미기록")), I18N.term(msaCaption)])));
  if (summary.msa_provenance) {
    const evidence = el("details"); evidence.id = "msa-provenance";
    evidence.append(el("summary", I18N.term("MSA 출처")));
    if (summary.msa_provenance.method === "shared_parent_homologs")
      evidence.append(el("p", I18N.term("공개 표적·WT 서열로 확보한 MSA를 사용했습니다. 변이별 첫 서열만 바꾸고 WT 상동 서열은 유지했습니다. 변이별 독립 검색은 하지 않았습니다.")));
    evidence.append(el("pre", JSON.stringify(summary.msa_provenance, null, 2)));
    $("result-input").append(evidence);
  }
  if (summary.reference_provenance?.role === "comparison_only")
    $("result-input").append(el("p", I18N.format("WT 참조 구조: {0}. 구조 화면에서 ‘참조 구조’를 켜면 표적에 정렬해 비교합니다. 예측 입력이나 선별 기준으로 사용하지 않았습니다.", [summary.reference_provenance.source_id || I18N.term("미기록")])));
  const mockNotice = mockZeroSelectionNotice(summary, state.rows);
  if (mockNotice) $("result-input").prepend(mockNotice);
  I18N.text($("result-input-config"), JSON.stringify(summary.assessment
    ? {conditions: summary.conditions, current_assessment: summary.assessment, prediction_config: summary.config}
    : {conditions: summary.conditions, config: summary.config}, null, 2));
  $("result-summary").replaceChildren(el("h2", I18N.term("02 후보·모델 수")), el("p", I18N.format("분석: {0}", [(summary.name || summary.id ? (summary.name ? summary.name : summary.id) : I18N.term("미기록"))]), "muted"));
  $("result-summary").append(el("p", I18N.term("결과 탐색기는 소수 3자리, 정적 보고서는 유효숫자 8자리까지 표시합니다. 원값은 툴팁과 CSV에서 확인하세요. 순위와 적격 판정은 저장된 값을 사용합니다."), "muted"));
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
  $("result-summary").append(el("p", I18N.term("저장된 전체 후보·모델을 집계합니다. 예측 전 준비·미선정 후보와 평가 미완료 모델은 따로 계산합니다."), "muted"));
  if (summary.engine_provenance && Object.keys(summary.engine_provenance).length) {
    const provenance = el("details"); provenance.append(el("summary", I18N.term("예측 엔진 버전·가중치·출처")), el("pre", JSON.stringify(summary.engine_provenance, null, 2))); $("result-summary").append(provenance);
  }
  const engineSummary=el("div","","report-engine-summary");
  const names={colabfold:"ColabFold",boltz2:"Boltz-2",chai1:"Chai-1",opendde:"OpenDDE Preview"};
  for (const engine of [...new Set(state.rows.map(r=>r.predictor_engine))].sort()) {
    const models=state.rows.filter(r=>r.predictor_engine===engine),card=el("div"), engineCounts=reportModelCounts(models, summary.mode, summary.config);
    card.append(el("b",(names[engine] ? names[engine] : engine)),el("span",I18N.format("평가 {0}/{1} · 오류 {2}{3}", [engineCounts.evaluated_total, engineCounts.model_total, models.filter(r=>recordedEvaluationError(r)).length, generalComplex ? '' : I18N.format(" · 기록 적격 {0} · 판정 미평가 {1}", [engineCounts.eligible_total === null ? I18N.term("미평가") : engineCounts.eligible_total, engineCounts.eligibility_missing])])));engineSummary.append(card);
  }
  $("result-summary").append(engineSummary,el("p",I18N.term("같은 후보의 엔진별 예측을 별도로 표시합니다. 순위는 같은 엔진·후보 집합 안에서 비교하세요. 이전 결과의 비교 범위는 ranking_policy에서 확인할 수 있습니다."),"report-interpretation"));
  renderReportReasons(state.rows, summary.mode);
  const guide=el("details","","report-guide");guide.append(el("summary",I18N.term("지표 읽는 법과 평가 한계")));
  const list=el("ul");
  for(const text of ["ipTM·ipSAE: 높을수록 예측 신뢰도가 높습니다. 같은 엔진·조건 안에서 비교하세요.","pLDDT: 국소 구조 신뢰도. PAE: 상대 배치 오차(Å)로 낮을수록 좋으며, 행→열의 방향별 값이 다를 수 있습니다.","pDockQ2: CA 8 Å 접촉과 방향별 PAE를 사용합니다. AlphaFold-Multimer 보정이며 Boltz·Chai·OpenDDE 값은 탐색용입니다.","적격 0건은 실행 실패와 구분합니다. 미평가 항목은 0점이나 판정 통과로 취급하지 않습니다.","현재 필터를 바꿔도 원래 적격 판정·선별 목록·전체 모델과 출처는 내보내기에 보존됩니다."])list.append(el("li",I18N.term(text)));
  guide.append(list);$("result-summary").append(guide);
  $("result-summary").append(
    el(
      "span",
      summary.mock ? I18N.term("CPU/mock 데모") : summary.failed_candidates ? I18N.term("부분 실패 · 후보 오류 확인") : summary.status &&
              states[summary.status] &&
              summary.status !== "complete" ? I18N.concat([I18N.term(states[summary.status]), I18N.term(" · 부분 결과")]) : summary.complete ? generalComplex ? I18N.term("분석 완료 · 일반 복합체 평가") : counts.eligible_total === null ? I18N.term("분석 완료 · 적격 판정 미평가") : summary.zero_eligible ? summary.quality_eligible_total > 0 ? I18N.format("정상 완료 · 결합 부위 제외 품질 적격 {0}개", [summary.quality_eligible_total]) : I18N.term("정상 완료 · 적격 후보 0개") : I18N.term("분석 완료") : I18N.term("미완료 / 부분 결과"),
      "badge",
    ),
  );
  renderHandoffStatus(summary);
  warnings($("result-issues"), [
    ...(summary.issues || []),
    ...[...new Set((summary.warnings || []).map(String))].map(message =>
      I18N.concat([I18N.term("실행 당시 기록 경고: "), I18N.term(message)])),
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
  const leads = renderSelectionLeads(summary, state.rows);
  renderCandidates();
  if (state.filtered.length) {
    const preferred = leads.length === 1 && state.filtered.includes(leads[0]) ? leads[0] : state.filtered[0];
    await selectCandidate(preferred.candidate_id);
  }
}
function renderSelectionLeads(summary, rows) {
  const panel = $("selection-leads"), list = $("selection-lead-list");
  panel.hidden = summary.mode === "complex";
  list.replaceChildren();
  if (panel.hidden) return [];
  const eligible = rows.filter(row => row.evaluated === true && row.final_eligible === true && !recordedEvaluationError(row)
    && row.prediction_status !== "failed" && !row.is_parent_control && row.candidate_id !== "WT" && row.source_sequence_id !== "WT");
  const mixed = summary.config?.screen?.comparison?.enabled === true || new Set(rows.filter(row => row.evaluated).map(row => row.predictor_engine)).size > 1;
  const ranked = rows.filter(row => row.selected === true && row.selection_rank === 1);
  const rankCounts = new Map();
  for (const row of ranked) rankCounts.set(row.predictor_engine, (rankCounts.get(row.predictor_engine) || 0) + 1);
  const leads = eligible.filter(row => row.selected === true && row.selection_rank === 1 && typeof row.sequence === "string" && row.sequence.trim()
    && ["boltz2","colabfold","chai1","opendde"].includes(row.recorded_engine) && rankCounts.get(row.predictor_engine) === 1
    && (!mixed || row.ranking_scope === row.predictor_engine)
    && row.ranking_status !== "held" && (row.dev_assessment_policy !== "sequence_liability_screen_v2" || row.dev_assessment_status === "screen_pass"));
  const reason = summary.mock ? "연습 실행입니다. 예시 서열과 구조는 03 후보 비교에서 볼 수 있습니다."
    : !summary.complete ? "완료된 최종 선별 기록이 필요합니다. 현재 결과는 03 후보 비교에서 볼 수 있습니다."
    : !eligible.length ? "기록된 적격 VHH 후보가 없습니다. 04 제외·미평가에서 이유를 확인하세요."
    : !leads.length ? "적격 후보는 있지만 확인할 수 있는 최종 선별 1위가 없습니다. 03 후보 비교에서 개별 후보를 확인하세요." : null;
  if (reason) {
    list.append(el("p", I18N.term(reason)));
    const exclusions = !summary.mock && summary.complete && !eligible.length;
    const link = el("a", I18N.term(exclusions ? "04 제외·미평가" : "03 후보 비교"));
    link.href = exclusions ? "#report-exclusions" : "#report-comparison"; list.append(link); return [];
  }
  list.append(el("p", I18N.term("최종 선별표에 기록된 1위입니다. 여러 엔진을 사용한 경우 엔진별로 표시합니다. 실제 결합력은 실험으로 확인하세요.")));
  for (const row of leads) {
    const line = el("p"), button = el("button", I18N.term("서열과 예측 구조 보기"), "secondary");
    line.append(el("strong", row.candidate_id), el("span", " · " + row.predictor_engine + " · ipSAE " + fmt(row.ipsae) + " "));
    button.type = "button"; button.onclick = () => openExactCandidate(row.candidate_id).catch(e => notice(e.message, true));
    line.append(button); list.append(line);
  }
  return leads;
}
async function openExactCandidate(id, matches = "") {
  if (!state.rows.some(row => row.candidate_id === id)) return;
  state.catalogCandidateSearch = matches || null;
  $("candidate-search").value = matches; $("engine-filter").value = "all";
  $("hotspot-filter").checked = false; $("eligibility").value = "all"; $("eligibility-basis").value = "recorded";
  renderCandidates();
  const selection = selectCandidate(id);
  $(matches ? "report-comparison" : "candidate-title").scrollIntoView({block:"start"});
  await selection;
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
function showVhhSequence(row) {
  const panel = $("vhh-sequence-panel");
  if (!panel) return;
  const value = $("vhh-sequence-value"), copy = $("copy-vhh-sequence"), save = $("save-vhh-sequence");
  const status = $("vhh-sequence-feedback");
  const sequence = typeof row?.sequence === "string" ? row.sequence.trim() : "";
  value.value = sequence;
  panel.hidden = !row || state.result?.mode === "complex";
  copy.disabled = save.disabled = !sequence;
  I18N.text(status, "");
  if (panel.hidden) return;
  const id = row.candidate_id;
  I18N.text($("vhh-sequence-record"), sequence ? I18N.format("{0} · 아미노산 {1}개", [id, sequence.length]) : id);
  if (!sequence) I18N.text(status, I18N.term("이 후보의 서열은 결과에 기록되지 않았습니다."));
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(sequence);
      if (state.candidate === row) I18N.text(status, I18N.term("VHH 서열을 복사했습니다."));
    } catch {
      if (state.candidate !== row) return;
      value.focus(); value.select();
      I18N.text(status, I18N.term("서열을 선택했습니다. Ctrl+C 또는 ⌘C로 복사하세요."));
    }
  };
  save.onclick = () => {
    const header = String(id).replace(/[\r\n]/g, " ");
    const filename = header.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 100);
    saveBlob(new Blob([`>${header}\n${sequence}\n`], {type:"text/plain;charset=utf-8"}), "VHH-" + filename + ".fasta");
  };
}
function clearCandidate() {
  state.candidate = null;
  showVhhSequence(null);
  state.structure = null;
  setPaeErrorStatus(null);
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
  describeResultFigure("pae", "PAE · 예측 정렬 오차", I18N.term("후보와 구조를 선택하면 PAE가 표시됩니다. 저장 자료는 출처·산출물에서 확인하세요."), "#report-provenance", true);
  describeResultFigure("distribution", "선택 후보의 조건별 ipTM 분포", I18N.term("후보 표에서 후보를 선택하면 관측 분포가 표시됩니다."), "#candidate-table", true);
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
      : I18N.concat([I18N.term(basis), ": ", I18N.term(decisionCaption(eligibility, row))]);
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
  const search = f.search.toLowerCase(), fragment = search.replace(/\s/g, "");
  const catalogSearch = Boolean(search) && state.catalogCandidateSearch === f.search;
  state.filtered = state.rows.filter(
    (row) =>
      (f.engine_filter === "all" || row.predictor_engine === f.engine_filter) &&
      (!search || (catalogSearch ? row.candidate_id.toLowerCase().includes(search) ||
        (fragment.length >= 3 && row.sequence?.toLowerCase().includes(fragment)) : candidateSearchText(row).includes(search))) &&
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
  I18N.text($("candidate-count"), I18N.format("표시 {0} / 전체 {1} 후보·모델 기록 · 미평가는 별도 표시", [state.filtered.length, state.rows.length]));
  refreshMetricCaptions(state.filtered, state.result?.mode);
  I18N.text($("candidate-sort-note"), I18N.format("정렬 기준: {0} · {1} · 미평가 마지막 · {2}.", [I18N.term(metricCaption(f.sort, state.filtered, state.result?.mode)), f.descending ? I18N.term("높은 값부터") : I18N.term("낮은 값부터"), f.sort === "review_rank" ? I18N.term("동일 범위의 후보별 검토 순위·동률 허용·1위도 보류 가능") : I18N.term("선택한 지표로 정렬")]));
  const columns = state.result.mode === "complex" ? ["candidate_id", "predictor_engine", "seed", "model", "iptm", "ipsae", "pdockq2", "pdockq2_target", "pdockq2_binder", "interface_pae", "n_clashes", "error"] : [
      "candidate_id",
      "predictor_engine",
      "seed",
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
      : ["candidate_id","predictor_engine","seed","final_eligible","selected","ipsae","iptm","interface_plddt","interface_pae","hotspot_site_status","n_clashes","exclusion_reasons"];
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
        } else if(["final_eligible","quality_eligible"].includes(key)) I18N.text(td, I18N.term(decisionCaption(row[key], row)));
        else if(key === "seed") I18N.text(td, row[key] == null ? I18N.term("미기록") : String(row[key]));
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
  if (!state.result) return;
  const x = $("plot-x").value,
    y = $("plot-y").value,
    rows = state.filtered.filter(
      (r) => Number.isFinite(r[x]) && Number.isFinite(r[y]),
    );
  const node = $("scatter");
  describeResultFigure("scatter", "계면 점수 비교 산점도", rows.length
    ? I18N.format("현재 탐색 모델 {0}건 중 두 축 모두 평가된 {1}건을 표시합니다. 축 값 미평가 {2}건. X: {3} · Y: {4}. 각 점의 원값은 후보 표·CSV에서 확인하세요.", [state.filtered.length, rows.length, state.filtered.length - rows.length, I18N.term(metricCaption(x, state.filtered, state.result?.mode)), I18N.term(metricCaption(y, state.filtered, state.result?.mode))])
    : I18N.term("표시할 평가값이 없습니다. 축 값 미평가와 필터 조건을 후보 표에서 확인하세요."), "#candidate-table", !rows.length);
  if (!window.Plotly) return;
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
  showVhhSequence(row);
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
    if(h.status==='no_contact')note.append(el("p",I18N.term("지정 결합 부위에 접촉하지 않은 예측입니다. 후보 구조와 접촉 잔기를 확인하세요.")));
    note.append(el("p",I18N.term("PAE는 상대 위치의 불확실성, pDockQ2는 구조 품질 추정값입니다. 결합 부위 진단은 원래 점수·제외 사유와 함께 확인하세요."),"muted"));detail.append(note);
  }
  if(state.result.mode!=="complex"&&hasPoseAssessment([row])) {
    const panel=el("div");panel.setAttribute("role","status");panel.className="pose-assessment";
    panel.append(el("h3",I18N.term("신뢰도 · 지정 결합 부위 · 자세 일관성")));
    panel.append(el("p",I18N.format("실행 당시 기록 적격: {0} · 지정 결합 부위 포함 적격: {1}", [I18N.term(decisionCaption(row.final_eligible, row)), I18N.term(poseStatusLabel(row,"site_selection_eligible"))])));
    panel.append(el("p",I18N.format("동일 후보 자세 일치: {0} · 자세 일치 엔진 {1}개 · 기록 적격 지지 {2}개", [I18N.term(poseStatusLabel(row,"pose_consensus_status")), fmt(row.pose_supporting_engines), fmt(row.pose_quality_supporting_engines)])));
    panel.append(el("p",I18N.format("설계·재예측: {0} · 표적 정렬 후 VHH RMSD {1} Å", [I18N.term(poseStatusLabel(row,"design_consistency_status")), fmt(row.design_binder_rmsd_A)])));
    panel.append(el("p",I18N.format("검토 순위: {0} · 범위 {1} · 1위도 보류일 수 있으며 동률을 허용합니다.", [fmt(row.review_rank), fmt(row.review_rank_scope)])));
    panel.append(el("p",I18N.format("후보 검토 판정: {0} · 모델 검토 판정: {1}", [I18N.term(poseStatusLabel(row,"candidate_review_priority")), I18N.term(poseStatusLabel(row,"review_priority"))])));
    panel.append(el("p",I18N.format("후보 검토 판정 이유: {0}", [I18N.term(poseStatusLabel(row,"review_priority_reason"))])));
    panel.append(el("p",I18N.term("검토 정책 v3: 품질·지정 결합 부위 기준을 충족한 예측을 엔진별로 표시합니다. 두 엔진 이상이 같은 자세에서 기준을 충족하면 다중 엔진 지지로 표시합니다. 자세 차이와 설계·재예측 불일치는 별도 진단에서 확인하세요."),"muted"));
    panel.append(el("p",I18N.term("동일 후보의 표적 구조를 정렬한 뒤 자세를 비교합니다. 점수와 지지 엔진 수는 엔진별 예측 결과로 표시합니다. 탐색 우선순위와 기존 적격·선별 기록은 각각 확인할 수 있습니다."),"muted"));
    if(row.pose_assessment&&typeof row.pose_assessment==="object") {const raw=el("details");raw.append(el("summary",I18N.term("자세 평가 조건·출처")),el("pre",JSON.stringify(row.pose_assessment,null,2)));panel.append(raw);}
    detail.append(panel);
  }
  const original = el("details");
  original.append(el("summary", I18N.term("원본 모델 ID·서열")), el("p", id, "sequence"), el("p", (row.sequence ? row.sequence : I18N.term("서열 미기록")), "sequence"));
  detail.append(original);
  if (state.result.mode !== "complex") detail.append(el("p", I18N.format("품질 적격 (결합 부위 제외): {0} · 실행 당시 기록 적격: {1}", [I18N.term(decisionCaption(row.quality_eligible, row)), I18N.term(decisionCaption(row.final_eligible, row))])));
  if (row.quality_eligibility_source === "hotspot_only_exclusion_removed")
    detail.append(el("p", I18N.term("실행 당시 결합 부위 조건만으로 제외됐습니다. 나머지 기록 기준은 통과했으며 원본 판정·선별 목록은 보존됩니다.")));
  warnings(detail.appendChild(el("div")), [
    ...(row.primary_pair_warning ? [row.primary_pair_warning] : []),
    ...(row.exclusion_reasons || []).map((r) => I18N.concat([I18N.term("기록 제외 사유: "), I18N.term(reportReasonLabel(r)), " (", r, ")"])),
    ...(recordedEvaluationError(row) ? [I18N.concat([I18N.term("후보 평가 오류: "), I18N.term(recordedEvaluationError(row))])] : []),
    ...(row.missing_metrics?.length
      ? [I18N.concat([I18N.term("미평가 지표: "), ...row.missing_metrics.flatMap((key, index) => [index ? ", " : "", I18N.term(metricCaption(key, row, state.result?.mode))])])]
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
    confidenceScope.append(el("p", I18N.format("요약 쌍 선택: {0} · 유한한 기록값의 최솟값 {1} / 최댓값 {2} · 평가 쌍 {3} / 전체 쌍 {4}. 미평가 체인쌍은 최솟값·최댓값에서 제외됩니다.", [I18N.term(selection), fmt(row.min_pair_ipsae), fmt(row.max_pair_ipsae), fmt(row.pair_ipsae_assessed_count), fmt(row.pair_ipsae_total_count)])));
  }
  confidenceScope.append(el("pre",JSON.stringify(Object.fromEntries(Object.entries(row).filter(([key])=>/^ipsae_(scope|chain|source|selection)|^iptm_(scope|source|unavailable)|^(primary_pair|(max|min)_pair_ipsae|pair_ipsae_)/.test(key))),null,2)));detail.append(confidenceScope);
  if (row.dev_assessment_policy === "sequence_liability_screen_v2") detail.append(el("p", I18N.term("v2 서열 검토는 모티프, Cys 개수와 선형 소수성 창을 검사합니다. 상한 초과는 검토 보류이며 실험적 개발 불가를 뜻하지 않습니다. 구조 표면의 소수성이나 실제 분해·응집은 별도 평가가 필요합니다."), "report-interpretation"));
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
  detail.append(el("p", I18N.term("PAE는 상대 위치의 불확실성을 Å 단위로 나타냅니다. pDockQ2는 CA 8 Å 접촉과 방향별 PAE로 계산하며, 두 체인 중 낮은 값을 표시합니다. AlphaFold-Multimer에서 보정된 지표로, Boltz·Chai·OpenDDE 결과는 탐색용으로 사용합니다."), "muted"));
  detail.append(el("p", I18N.term("종합점수는 같은 엔진·후보 집합 안에서 비교합니다. 가중치는 ranking_policy에서 확인하세요. dev_severe는 선별용 서열 검토 항목 수이며 항목 정의는 기록된 정책을 따릅니다. 접촉·충돌은 거리 기준 원자쌍 수입니다."), "muted"));
  detail.append(el("p", I18N.term("재현 조건은 엔진·장치·seed·실행 설정을 함께 확인하세요. 엄격한 CUDA 결정론 설정과 동일 환경 반복 검증은 Chai 경로에서 지원합니다. 결합 부위 OFF/ON 실행의 난수 조건은 각각 확인하세요."), "muted"));
  detail.append(el("p", I18N.term("dev_flags는 종합점수 가중치입니다. CDR 미평가와 v2 서열 검토 보류는 fallback 설정으로 해제되지 않습니다."), "muted"));
  if (row.threshold_checks !== undefined) {
    const facts = el("details"),
      caption = el("summary", I18N.term(row.assessment_status ? "현재 평가 기준" : "지표 해석·실행 당시 선별 기준"));
    facts.append(caption);
    if (row.assessment_status) {
      if (row.assessment_status !== "verified") facts.append(el("p", I18N.term("현재 평가 기준 미확인")));
      facts.append(el("code", row.assessment_source));
    }
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
      {localizedValues: ["지표", "통과"]},
    );
    facts.append(
      el(
        "p",
        I18N.term("ipSAE는 계면의 구조 일관성, ipTM은 상대 배치 신뢰도를 나타냅니다. PAE는 상대 위치의 불확실성을 Å 단위로 표시합니다."),
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
    const key = `${row.engine || "engine"} / seed=${fmt(row.seed)} / MSA=${row.msa || I18N.value(I18N.term("미기록"))} / restraint=${fmt(row.restraint)}`;
    if (Number.isFinite(row.iptm)) {
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row.iptm);
    }
  }
  const evaluated = [...groups.values()].reduce((total, values) => total + values.length, 0);
  describeResultFigure("distribution", "선택 후보의 조건별 ipTM 분포", evaluated
    ? I18N.format("선택 후보 관측 {0}건 · ipTM 평가 {1}건 · 미평가 {2}건 · 서로 합산하지 않는 조건 {3}개. 조건별 원값은 아래 후보 상세 표에서 확인하세요.", [observations.length, evaluated, observations.length - evaluated, groups.size])
    : I18N.term(!state.candidate && !observations.length ? "후보 표에서 후보를 선택하면 관측 분포가 표시됩니다." : "반복 관측의 ipTM이 미평가 상태입니다. 후보 상세 표에서 관측 기록과 사유를 확인하세요."), "#candidate-detail", !evaluated);
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
// Decode only selected offline PAE arrays. Pool records stay compressed; a small
// promise cache coalesces concurrent language/selection renders without retaining
// every candidate matrix. Older reports and live API matrices remain supported.
const offlinePaeCache = new Map();
const OFFLINE_PAE_VIEW_BYTES = 128 * 1024 * 1024;
async function offlinePaeValues(pae, payload = window.NANOCOFOLD_OFFLINE) {
  if (!pae || Array.isArray(pae.values)) return pae?.values;
  const reference = pae.values_ref, record = payload?.pae_arrays?.[reference];
  const dimension = record?.dimension, expected = dimension * dimension * 8;
  if (!/^[a-f0-9]{64}$/.test(reference || "") || !record || record.sha256 !== reference ||
      !["gzip-base64-float64le", "gzip-base64-shuffled-float64le"].includes(record.encoding) || !Number.isSafeInteger(dimension) || dimension < 1 ||
      !Number.isSafeInteger(expected) || record.bytes !== expected ||
      !Array.isArray(pae.labels) || pae.labels.length !== dimension)
    throw Error("PAE 압축 데이터를 읽지 못했습니다. 표와 구조는 계속 확인할 수 있습니다.");
  if (expected > OFFLINE_PAE_VIEW_BYTES)
    throw Error("PAE가 브라우저 표시 한도(128 MiB)를 넘었습니다. 전체 행렬은 보고서에 보존되며 PAE 저장으로 받을 수 있습니다.");
  if (typeof DecompressionStream !== "function" || !globalThis.crypto?.subtle)
    throw Error("이 브라우저는 압축 PAE 보기를 지원하지 않습니다. 최신 Chrome·Edge·Firefox를 사용하세요. 전체 PAE는 저장할 수 있습니다.");
  if (!offlinePaeCache.has(reference)) {
    const pending = (async () => {
      if (typeof record.data !== "string" || record.data.length > Math.ceil((expected + 65536) * 4 / 3))
        throw Error("Invalid compressed PAE size");
      const compressed = Uint8Array.from(atob(record.data), value => value.charCodeAt(0));
      const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("gzip")).getReader();
      const chunks = []; let total = 0;
      try {
        for (;;) {
          const {value, done} = await stream.read(); if (done) break;
          total += value.byteLength;
          if (total > expected) throw Error("Decoded PAE exceeds its declared size");
          chunks.push(value);
        }
      } catch (error) { await stream.cancel().catch(() => {}); throw error; }
      finally { stream.releaseLock(); }
      if (total !== expected) throw Error("Truncated PAE array");
      const bytes = new Uint8Array(expected); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      if (record.encoding === "gzip-base64-shuffled-float64le") {
        const shuffled = bytes.slice(), count = expected / 8;
        for (let lane = 0; lane < 8; lane++)
          for (let index = 0; index < count; index++) bytes[index * 8 + lane] = shuffled[lane * count + index];
      }
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2,"0")).join("");
      if (digest !== reference) throw Error("PAE array hash mismatch");
      const view = new DataView(bytes.buffer), rows = [];
      for (let i = 0; i < dimension; i++) {
        const row = [];
        for (let j = 0; j < dimension; j++) {
          const value = view.getFloat64((i * dimension + j) * 8, true);
          if (!Number.isFinite(value)) throw Error("Nonfinite PAE value");
          row.push(value);
        }
        rows.push(row);
      }
      return rows;
    })();
    offlinePaeCache.set(reference, pending);
    // Keep the current and most recent matrix; original pool entries are untouched.
    while (offlinePaeCache.size > 2) offlinePaeCache.delete(offlinePaeCache.keys().next().value);
  }
  try { return await offlinePaeCache.get(reference); }
  catch (error) {
    offlinePaeCache.delete(reference);
    throw Error("PAE 압축 데이터를 읽지 못했습니다. 표와 구조는 계속 확인할 수 있습니다.", {cause:error});
  }
}

const offlineStructureCache = new Map();
async function offlineStructure(binding, payload = window.NANOCOFOLD_OFFLINE) {
  if (!binding?.structure_ref) return binding;
  const reference = binding.structure_ref, record = payload?.structure_assets?.[reference];
  if (!/^[a-f0-9]{64}$/.test(reference) || record?.sha256 !== reference || record.encoding !== "gzip-base64-json" ||
      !Number.isSafeInteger(record.bytes) || record.bytes < 2 || record.bytes > 128 * 1024 * 1024 ||
      typeof record.data !== "string" || record.data.length > Math.ceil((record.bytes + 65536) * 4 / 3))
    throw Error("압축 구조 자료의 크기 또는 해시가 올바르지 않습니다.");
  if (!offlineStructureCache.has(reference)) {
    const pending = (async () => {
      if (typeof DecompressionStream !== "function" || !globalThis.crypto?.subtle)
        throw Error("압축 구조 보기는 최신 Chrome·Edge·Firefox에서 지원합니다.");
      const compressed = Uint8Array.from(atob(record.data), value => value.charCodeAt(0));
      const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("gzip")).getReader();
      const bytes = new Uint8Array(record.bytes); let total = 0;
      try {
        for (;;) {
          const {value, done} = await stream.read(); if (done) break;
          if (total + value.byteLength > bytes.length) throw Error("압축 구조 자료의 크기 또는 해시가 올바르지 않습니다.");
          bytes.set(value, total); total += value.byteLength;
        }
      } catch (error) { await stream.cancel().catch(() => {}); throw error; }
      finally { stream.releaseLock(); }
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2,"0")).join("");
      if (total !== bytes.length || digest !== reference) throw Error("압축 구조 자료의 크기 또는 해시가 올바르지 않습니다.");
      const body = JSON.parse(new TextDecoder("utf-8", {fatal:true}).decode(bytes));
      if (!body || typeof body.content !== "string" || !["pdb", "cif"].includes(body.format))
        throw Error("압축 구조 자료의 크기 또는 해시가 올바르지 않습니다.");
      if (body.raw_content_ref === "content") { body.raw_content = body.content; delete body.raw_content_ref; }
      return body;
    })();
    offlineStructureCache.set(reference, pending);
    while (offlineStructureCache.size > 2) offlineStructureCache.delete(offlineStructureCache.keys().next().value);
  }
  try {
    const body = await offlineStructureCache.get(reference);
    return {...body, ...binding,
      pae:body.pae ? {...body.pae} : body.pae};
  } catch (error) { offlineStructureCache.delete(reference); throw error; }
}

function setPaeErrorStatus(error = null) {
  const plot = $("pae");
  if (!plot) return;
  let status = $("pae-status");
  if (!status && error) {
    // Plotly SVG annotations do not wrap long text on narrow screens. Keep
    // the complete explanation in normal text flow beside the plot instead.
    status = el("p", undefined, "warning");
    status.id = "pae-status";
    status.setAttribute("role", "status");
    status.style.overflowWrap = "anywhere";
    status.style.whiteSpace = "normal";
    plot.after(status);
  }
  if (!status) return;
  status.hidden = !error;
  I18N.text(status, error ? I18N.term(error) : "");
}
function localizeEmptyPaeAnnotation() {
  const plot = $("pae");
  const renderError = state.paeRenderError && state.paeRenderError.data === state.structure ? state.paeRenderError.message : null;
  setPaeErrorStatus(renderError || (plot?.data?.length === 0 ? state.structure?.pae_error : null));
  if (!window.Plotly || plot?.data?.length !== 0 || !plot.layout?.annotations?.length) return;
  const text = !state.structure ? "구조 / PAE 미평가"
    : state.structure.pae_error ? "PAE" : state.structure.pae_status === "removed_by_retention" ? "PAE · 용량 정리로 삭제됨" : "PAE 미평가";
  Plotly.relayout(plot, {"annotations[0].text": I18N.value(I18N.term(text))});
}
async function loadStructure() {
  if (!state.candidate) return;
  setPaeErrorStatus(null);
  const id = state.candidate.candidate_id,
    observation = $("observation-select").value,
    request = ++state.structureRequest;
  try {
    const loaded = offline
      ? await offlineStructure((window.NANOCOFOLD_OFFLINE.structures || []).find(
          (s) =>
            s.candidate_id === id &&
            (!observation || s.observation_id === observation),
        ))
      : await api(
          `/api/results/${state.result.id}/structure?candidate_id=${encodeURIComponent(id)}${observation ? "&observation_id=" + encodeURIComponent(observation) : ""}`,
        );
    if (
      request !== state.structureRequest ||
      state.candidate?.candidate_id !== id
    )
      return;
    state.structure = offline && loaded ? {...loaded, pae: loaded.pae ? {...loaded.pae} : loaded.pae} : loaded;
    if (!state.structure) throw Error("이 후보의 구조는 보고서에 없습니다.");
    await renderStructure();
  } catch (e) {
    if (request !== state.structureRequest) return;
    state.structure = null;
    disposeResultViewer();
    I18N.text($("structure-note"), I18N.term(e.message));
    describeResultFigure("pae", "PAE · 예측 정렬 오차", I18N.term("후보와 구조를 선택하면 PAE가 표시됩니다. 저장 자료는 출처·산출물에서 확인하세요."), "#report-provenance", true);
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
  describeResultFigure("structure-view", "선택 후보 3D 구조", I18N.term("후보와 관측을 선택하면 구조를 표시합니다. 미평가 사유는 후보 상세에서 확인하세요."), "#report-provenance", true);
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
    "후보의 신뢰도 지표·PAE·계면 접촉을 함께 확인하세요.";
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
  parts.push(I18N.term("후보의 신뢰도 지표·PAE·계면 접촉을 함께 확인하세요."));
  return I18N.concat(parts);
}
function resultChain(role) {
  const data = state.structure;
  const id = role === "target" ? data?.target_chain : data?.binder_chain;
  return (Array.isArray(data?.chains) ? data.chains : []).find(chain =>
    chain?.id === id && chain.role === role && Array.isArray(chain.residues));
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
  // Neither display owns the other's readiness. Handle plot failures now so a
  // pending 3D factory cannot leave an unhandled plot rejection behind.
  renderPae(data).catch(() => {
    if (state.structure !== data) return;
    const message = "PAE 차트를 표시하지 못했습니다. 아래 자료 설명을 확인하세요.";
    state.paeRenderError = {data, message};
    setPaeErrorStatus(message);
  });
  const chains = (Array.isArray(data.chains) ? data.chains : []).filter(chain => chain && typeof chain.id === "string");
  I18N.text($("structure-data-note"), localizedStructureDataNote(data));
  if (state.viewerData !== data) {
    disposeResultViewer();
    const request = state.viewerRequest;
    state.viewerData = data;
    I18N.text($("structure-note"), I18N.term("MolStar 구조·서열 준비 중"));
    const host = el("div", undefined, "molstar-host");
    $("structure-view").replaceChildren(host);
    const residueCount = chains.reduce((count, chain) => count + (Array.isArray(chain.residues) ? chain.residues.length : 0), 0);
    describeResultFigure("structure-view", "선택 후보 3D 구조", I18N.format("3D 모델 {0} · 기록된 체인 {1}개 · 잔기 메타데이터 {2}개 · 접촉 잔기쌍 {3}개 · 충돌 잔기쌍 {4}개. 원본 좌표·잔기 대응 표와 CSV를 함께 확인하세요. 미기록 메타데이터를 0접촉이나 비결합으로 해석하지 않습니다.", [data.candidate_id, chains.length, residueCount, data.contacts?.length ?? I18N.term("미기록"), data.clashes?.length ?? I18N.term("미기록")]), "#structure-details");
    state.viewerReady = NanoCoFoldResultViewer.create(host, data, location => {
      if (state.structure !== data) return;
      const original = location.mapping ? `${location.mapping.author_chain || location.mapping.source_chain}:${location.mapping.source_residue || "미관측"}` : null;
      I18N.text($("structure-note"), I18N.format("{0} · 구조 author 번호{1}", [I18N.term(location.label), original ? I18N.concat([I18N.term(" · 입력 원본 "), original]) : I18N.term(" · 원본 대응은 아래 표 확인")]));
      $("residue-a").value = location.label;
    }, phase => {
      if (request !== state.viewerRequest || state.structure !== data) return;
      const messages = {viewer: "3D 뷰어를 시작하고 있습니다.", coordinates: "3D 좌표를 읽고 있습니다.",
        layers: "체인과 표시 레이어를 준비하고 있습니다.", display: "3D 화면을 정리하고 있습니다."};
      if (messages[phase]) I18N.text($("structure-note"), I18N.term(messages[phase]));
    });
    let viewer;
    try { viewer = await state.viewerReady; }
    catch (error) {
      if (request !== state.viewerRequest) return;
      I18N.text($("structure-note"), I18N.concat([I18N.term("MolStar 표시 실패: "), error.message]));
      $("structure-view").replaceChildren(el("p", I18N.term("3D 표시를 준비하지 못했습니다. 원본 좌표·점수 파일에서 기록을 확인하세요."), "warning"));
      describeResultFigure("structure-view", "선택 후보 3D 구조", I18N.term("3D 표시를 준비하지 못했습니다. 원본 좌표·점수 파일에서 기록을 확인하세요."), "#report-provenance", true);
      return;
    }
    if (request !== state.viewerRequest || state.structure !== data) { viewer.dispose(); return; }
    state.viewer = viewer;
    syncResultChainControls();
    if ($("structure-source")) I18N.text($("structure-source"), I18N.format("현재 모델 {0} · 체인 {1} · {2}", [data.candidate_id, chains.length ? chains.map(c=>c.id).join(", ") : I18N.term("미기록"), I18N.term(coordinatePresentation(data).caption)]));
    if ($("save-native-structure")) $("save-native-structure").disabled = !coordinatePresentation(data).rawAvailable;
    if ($("save-pae")) $("save-pae").disabled = !data.pae;
  } else if (!state.viewer) {
    try { await state.viewerReady; } catch { return; }
    if (state.structure !== data || !state.viewer) return;
  }
  await state.viewer.layers(resultLayers());
  await state.viewer.display(resultDisplay());
  if (state.structure !== data) return;
  I18N.text($("structure-note"), state.viewer.metadataIncomplete ? I18N.term("체인 역할·잔기 정보 일부가 미기록입니다. 회색 좌표의 체인 역할과 잔기 대응은 미확인입니다.") : I18N.format("MolStar · {0} {1} · {2} {3} · 접촉 잔기쌍 {4} · 충돌 잔기쌍 {5}. 서열·PAE·아래 버튼에서 잔기를 선택하세요.", [state.result.mode === 'complex' ? I18N.term('기준 체인') : I18N.term('표적'), data.target_chain, state.result.mode === 'complex' ? I18N.term('파트너') : 'VHH', data.binder_chain, (data.contacts?.length ? data.contacts?.length : 0), (data.clashes?.length ? data.clashes?.length : 0)]));
  const residues = chains.flatMap(chain => (Array.isArray(chain.residues) ? chain.residues : [])
    .filter(residue => residue && typeof residue.label === "string")
    .map(residue => ({value: residue.label, label: `${residue.label} ${residue.name || ""}`})));
  for (const id of ["residue-a", "residue-b"]) {
    const saved = $(id).value;
    options($(id), [{value: "", label: "잔기 선택"}, ...residues]);
    if (residues.some(r => r.value === saved)) $(id).value = saved;
  }
  const reference = data.reference;
  const toggle = document.querySelector('[data-layer="reference"]');
  toggle.disabled = !reference?.available;
  I18N.text($("reference-note"), reference?.available ? I18N.format(reference.role === "comparison_only" ? "WT 참조 비교: 표적 Cα {0}개 · RMSD {1} Å. 예측 입력이나 선별 기준으로 사용하지 않았습니다." : "입력 좌표 중첩: 표적 Cα {0}개 · RMSD {1} Å. {2}", [reference.matched_ca, reference.rmsd.toFixed(2), I18N.term(reference.note || "")]) : I18N.format("입력 좌표 중첩 미평가: {0}", [I18N.term(reference?.reason || "보존된 참조 좌표 없음")]));
  const actions = $("structure-actions"); actions.replaceChildren();
  if (data.clashes?.length)
    actions.append(focusButton(`충돌 잔기쌍 · ${data.clashes.length}쌍 보기`, data.clashes.flatMap(p => [p.a, p.b])));
  if ((state.candidate.developability_details?.warnings || []).some(r => /cys/i.test(r)) && data.cysteines?.residues?.length)
    actions.append(focusButton("Cys 위험 사유 · 좌표 확인", data.cysteines.residues, data.cysteines.note));
  const cdrActions = $("cdr-actions"); cdrActions.replaceChildren(el("span", I18N.term(data.cdr?.note || "CDR 미평가"), "muted"));
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
  if (state.result.mode !== "complex") details.append(el("h3", I18N.term("VHH Cys SG–SG 거리")), el("p", I18N.term(data.cysteines?.note || "미평가")));
  for (const pair of data.cysteines?.pairs || []) {
    const text = I18N.concat([`${pair.a}:SG – ${pair.b}:SG · `, pair.distance == null ? I18N.term("SG 미관측") : pair.distance.toFixed(3) + " Å"]);
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
}
async function renderPae(data) {
  if (state.structure !== data) return;
  if (state.paeRenderError?.data !== data) state.paeRenderError = null;
  describeResultFigure("pae", "PAE · 예측 정렬 오차", data.pae
    ? I18N.format("PAE 행렬 {0} × {0} 잔기. 단위 Å. 행은 정렬 기준 잔기, 열은 평가 잔기입니다. 색상 눈금은 0–30 Å이며 원값은 PAE 다운로드에서 확인하세요.", [data.pae.labels?.length ?? I18N.term("미기록")])
    : I18N.term(data.pae_status === "removed_by_retention" ? "PAE · 용량 정리로 삭제됨" : "PAE 미평가"), "#report-provenance", !data.pae);
  if (window.Plotly) {
    let pae = data.pae;
    if (offline && pae?.values_ref) {
      try { pae.values = await offlinePaeValues(pae); data.pae_error = null; }
      catch (error) { data.pae_error = error.message; pae = null; }
      if (state.structure !== data) return;
    }
    if (!pae) describeResultFigure("pae", "PAE · 예측 정렬 오차", I18N.term(data.pae_error ? "PAE 배열을 표시하지 못했습니다. 원본 자료에서 기록을 확인하세요." : data.pae_status === "removed_by_retention" ? "PAE · 용량 정리로 삭제됨" : "PAE 미평가"), "#report-provenance", true);
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
        annotations: pae ? [] : [{ text: data.pae_error ? "PAE" : data.pae_status === "removed_by_retention" ? I18N.value(I18N.term("PAE · 용량 정리로 삭제됨")) : I18N.value(I18N.term("PAE 미평가")), showarrow: false }],
      },
      { responsive: true, displaylogo: false, displayModeBar: false },
    );
    if (state.structure !== data) return;
    state.paeRenderError = null;
    localizeEmptyPaeAnnotation();
    const resetPae = $("pae-reset");
    if (resetPae) resetPae.onclick = () => {
      if (state.structure !== data) return;
      return Plotly.relayout($("pae"), {"xaxis.autorange":true, "yaxis.autorange":"reversed"});
    };
    $("pae").removeAllListeners?.("plotly_click");
    $("pae").on("plotly_click", event => {
      if (state.structure !== data) return;
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
    if (format === "fasta" && data.summary.mode === "complex" && typeof data.sequences_fasta !== "string")
      throw Error("저장된 복합체 서열을 확인할 수 없습니다. 전달용 ZIP의 sequences.fasta 또는 원본 설정을 확인하세요.");
    const blob =
      format === "fasta"
        ? new Blob([data.sequences_fasta ?? state.rows.filter(r => r.sequence)
            .map(r => `>${r.candidate_id}\n${r.sequence}\n`).join("")],
            { type: "text/plain;charset=utf-8" })
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
/* DIAGNOSTIC_PRESENTATION_START */
function diagnosticCaption(name) {
  const labels = {
    gpu: "NVIDIA GPU", smoke: "시험 실행",
    "report.viewer_asset": "3D 뷰어", "report.delivery_assets": "보고서·다운로드 구성 요소",
  };
  if (Object.hasOwn(labels, name)) return I18N.term(labels[name]);
  const [engine, kind] = String(name || "").split(".");
  const checks = {env_imports: "실행 환경", jax_cuda: "GPU 계산", headless_plot: "그래프 저장",
    binary: "실행 프로그램", weights: "모델 가중치", msa: "MSA 설정",
    installation_identity: "설치 버전", wt_structure: "기준 VHH 구조", graph_cuda: "GPU 계산",
    framework: "프레임워크 구조", scaffold: "프레임워크 구조", blackwell: "GPU 호환성",
    followup_ready: "복합체 평가 준비", supported: "엔진 지원"};
  const engines = {colabfold: "ColabFold", boltz2: "Boltz-2", chai1: "Chai-1", opendde: "OpenDDE Preview",
    rfantibody: "RFantibody", boltzgen: "BoltzGen", antifold: "AntiFold", esm: "ESM"};
  return engines[engine] && checks[kind]
    ? I18N.concat([engines[engine], " · ", I18N.term(checks[kind])]) : String(name || "");
}
function diagnosticAction(check) {
  if (check.ok) return "확인 완료";
  if (!check.required) return "이 선택 기능 없이 분석할 수 있습니다.";
  const name = String(check.name || "");
  if (name.includes("wt_structure")) return "WT VHH 구조를 입력하거나 AntiFold를 끄세요.";
  if (name.includes("cuda") || name === "gpu" || name.endsWith(".blackwell"))
    return "NVIDIA GPU가 있는 계산 서버에서 드라이버·CUDA 환경을 확인하세요.";
  if (name.startsWith("report.")) return "프로그램을 업데이트한 뒤 환경 확인을 다시 실행하세요.";
  if (name.endsWith(".framework") || name.endsWith(".scaffold"))
    return "선택한 프레임워크 구조 파일을 확인하세요.";
  return "환경·설치에서 이 엔진의 설치 프로필을 설치한 뒤 다시 확인하세요.";
}
function renderDiagnostics(diagnosis) {
  if (!diagnosis) return;
  const output = $("diagnostics-output");
  output.replaceChildren(el("h3", I18N.term(Array.isArray(diagnosis.checks) && diagnosis.checks.length
    ? diagnosis.ok ? "실행 준비 통과" : "실행 준비 미충족" : "환경 확인 기록")));
  if (!Array.isArray(diagnosis.checks) || !diagnosis.checks.length) {
    output.append(el("p", I18N.term("환경 확인 세부 기록이 아직 없습니다. 작업·로그에서 진행 상태나 종료 사유를 확인하세요.")));
    return;
  }
  const grid = el("table", null, "diagnostic-table"), head = el("tr"), body = el("tbody");
  for (const title of ["항목", "구분", "준비 상태", "다음 단계"]) head.append(el("th", I18N.term(title)));
  const thead = el("thead"); thead.append(head); grid.append(thead, body);
  for (const check of diagnosis.checks) {
    const row = el("tr");
    for (const value of [diagnosticCaption(check.name), I18N.term(check.required ? "필수" : "선택"),
      I18N.term(check.ok ? "준비됨" : check.required ? "필수 확인" : "선택 기능"), I18N.term(diagnosticAction(check))])
      row.append(el("td", value));
    body.append(row);
  }
  const scroll = el("div", null, "scroll"); scroll.append(grid); output.append(scroll);
  const original = el("details"), raw = el("pre");
  original.id = "diagnostics-original";
  raw.textContent = JSON.stringify(diagnosis, null, 2);
  original.append(el("summary", I18N.term("기술 진단 원문")), raw); output.append(original);
}
/* DIAGNOSTIC_PRESENTATION_END */
function setupExplorer() {
  $("candidate-search").addEventListener("input", () => { state.catalogCandidateSearch = null; });
  $("recorded-eligible").onclick = () => {
    $("candidate-search").value = "";
    $("engine-filter").value = "all";
    $("hotspot-filter").checked = false;
    $("eligibility-basis").value = "recorded";
    $("eligibility").value = "true";
    renderCandidates();
  };
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
    if (!data?.pae) throw Error("이 모델의 PAE는 제공되지 않았습니다. 후보 상세에서 기록을 확인하세요.");
    const pae = data.pae.values_ref && !data.pae.values ? {...data.pae, array:window.NANOCOFOLD_OFFLINE?.pae_arrays?.[data.pae.values_ref]} : data.pae;
    saveBlob(new Blob([JSON.stringify(pae,null,2)],{type:"application/json"}),
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
    const residues = (Array.isArray(data?.chains) ? data.chains : [])
      .flatMap(chain => Array.isArray(chain?.residues) ? chain.residues : [])
      .filter(residue => residue && typeof residue.label === "string");
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
    for(const action of state.researcher && row.job?.researcher !== state.researcher ? [] : row.job?.can_resume?["resume"]:row.job&&['queued','running'].includes(row.job.state)?["cancel"]:[]){
      const button=el("button",action==="resume" ? I18N.term("항목 재개") : I18N.term("항목 취소"));
      button.disabled=batchBusy;
      // Capture identifiers before opening the dialog; polling may replace activeBatch.
      const batchId=batch.id, jobId=row.job_id, rowName=row.id;
      button.onclick=async()=>{
        if(batchBusy)return;
        const cancelTarget=action==="cancel"?JSON.parse(JSON.stringify({...row.job,id:jobId})):null;
        if(action==="cancel"&&!await confirmCancellation({name:rowName,id:jobId,pendingResume:row.job.state==="queued"&&(row.job.resume||row.job.phase==="resume_validation")}))return;
        await batchAction("항목 요청 처리 중",async()=>{
          if(cancelTarget&&!await verifyCancellationTarget(cancelTarget))return;
          await api("/api/jobs/"+jobId+"/"+action,cancelTarget?{expected_cancel:cancellationExpectation(cancelTarget)}:{additional_budget_seconds:36000},{timeoutMs:30000});
          if(activeBatch?.id===batchId)renderBatch(await api("/api/batches/"+batchId,undefined,{timeoutMs:15000}));
        });
      };
      status.append(button);
    }
    line.append(choice,el("td",row.id),el("td",(row.error ? row.error : I18N.term("검토 완료"))),status);
    const result=el("td");if(row.job?.result_available===true&&["complete","failed","partial_failure","cancelled","interrupted","budget_exhausted"].includes(row.job.state)){const partial=row.job.state!=="complete",link=el("a",I18N.term(partial?"부분 결과 열기":"결과 열기"));link.href="/?result="+encodeURIComponent(row.job_id);link.target="_blank";link.rel="noopener";result.append(link);if(partial)result.append(el("p",I18N.term("중단 전까지 저장된 부분 결과입니다. 완료·미완료 항목을 확인하세요.")));}else I18N.text(result, "—");line.append(result);body.append(line);
  }
  $("batch-rows").replaceChildren(target);
  const retryMissing = Boolean(batch.selected) && batch.rows.some(row => batch.selected.includes(row.id) && !row.job_id && !row.job && !row.error);
  $("batch-submit").disabled=(!!state.researcher && batch.researcher!==state.researcher)||batchBusy||(Boolean(batch.selected)&&!retryMissing)||!batchSelected.length||batch.rows.some(r=>batchSelected.includes(r.id)&&r.error);
  I18N.text($("batch-submit"), batch.selected ? I18N.term(retryMissing ? "미제출 항목 다시 제출" : "이미 제출된 분석") : I18N.concat([I18N.concat([I18N.term("3. 선택한 "), batchSelected.length]), I18N.term("개 분석 실행")]));
  I18N.text($("batch-next-step"), batch.selected && retryMissing ? I18N.term("일부 항목을 제출하지 못했습니다. 항목별 오류를 확인한 뒤 미제출 항목 다시 제출을 누르세요. 이미 등록된 작업은 같은 작업으로 연결됩니다.") : batch.selected ? I18N.term("4단계 · 계산을 제출했습니다. 완료된 행의 결과 열기를 누르세요. 실행 중 상태는 자동으로 갱신됩니다.") : batch.rows.some(row => row.error) ? I18N.term("2단계 · 검토 오류가 있습니다. 아래 항목별 오류를 확인하고 입력을 수정한 뒤 다시 검토하세요.") : I18N.term("3단계 · 입력 검토 완료, 계산 미제출입니다. 실행할 항목을 확인하고 선택한 분석 실행을 누르세요."));
  $("batch-cancel").disabled=(!!state.researcher && (batch.researcher!==state.researcher || batch.rows.some(row=>row.job && ["queued","running","cancelling"].includes(row.job.state) && row.job.researcher!==state.researcher)))||batchBusy||!batch.rows.some(r=>r.job&&['queued','running'].includes(r.job.state));
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
  $("batch-cancel").onclick=async()=>{
    if(batchBusy||!activeBatch)return;
    const batchId=activeBatch.id, batchName=activeBatch.name, generation=batchCancellationGeneration(activeBatch);
    const expectedCancel={batch_id:batchId,jobs:activeBatch.rows.filter(row=>row.job&&['queued','running','cancelling'].includes(row.job.state)).map(row=>cancellationExpectation({...row.job,id:row.job_id}))};
    if(!await confirmCancellation({name:batchName,id:batchId,batch:true}))return;
    await batchAction("미완료 작업 취소 요청 중",async()=>{
      const latest=await api("/api/batches/"+batchId,undefined,{timeoutMs:15000});
      if(latest.id!==batchId||batchCancellationGeneration(latest)!==generation){cancellationChanged();return;}
      const updated=await api("/api/batches/"+batchId+"/cancel",{expected_cancel:expectedCancel},{timeoutMs:30000});
      if(activeBatch?.id===batchId)renderBatch(updated);
    });
  };
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
  setupCandidateCatalog();
  for (const button of document.querySelectorAll("[data-view]"))
    button.onclick = () => view(button.dataset.view);
  for (const id of ["catalog-search", "catalog-owner"]) {
    $(id)?.addEventListener("keydown", event => { if (event.key === "Enter") $("catalog-refresh").click(); });
  }
  $("jobs-next-analysis").onclick = () => view("input");
  action("catalog-refresh", () => {state.catalogCursor = null; return Promise.all([refreshCatalog(),refreshLabUsage()]);});
  action("catalog-first", () => {state.catalogCursor = null; return refreshCatalog();});
  action("catalog-next", () => {state.catalogCursor = state.catalogNext; return refreshCatalog();});
  action("retention-preview", previewRetention);
  action("retention-apply", applyRetention);
  $("retention-consent").onchange = () => {$("retention-apply").disabled = !$("retention-consent").checked || !state.retentionPlan;};
  for (const id of ["mode", "input-kind", "mock"])
    $(id).addEventListener("change", fields);
  initAlternativeInputs();
  $("yaml-file").onchange = async () => {
    const revision = state.inputRevision;
    try {
      const parsed = await api("/api/configs/parse", {
        content: await $("yaml-file").files[0].text(),
      });
      if (revision !== state.inputRevision) throw Error("불러오는 동안 입력이 변경되었습니다. 현재 입력을 유지합니다.");
      applyAnalysisSettings(parsed.config);
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
          I18N.format("{0} · {1}", [p.positions.join(", "), I18N.term(p.reason || "추천 패치")]),
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
    resetMutationControls();
    $("analysis-preset").value = "small-four-v1";
    $("candidate-limit").value = "";
    $("design-count").value = ""; if ($("selection-limit")) $("selection-limit").value = "";
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
    if (state.input && state.readiness) notice("입력 확인 완료 · 분석 실행을 선택하세요.");
  });
  $("environment-mode").onchange = () => { state.environmentTarget = null; refreshEnvironmentStatus().catch(e => notice(e.message, true)); };
  action("diagnose-environment", submitDiagnostic);
  action("start-job", submitReviewedAnalysis);
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
    applyAnalysisSettings(state.result.config);
    $("job-name").value = (state.result.name || "analysis") + "-new";
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
        I18N.term("입력·엔진·seed·MSA·restraint가 같은 조건의 점수끼리 비교하세요."),
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
  $("seed").addEventListener("input", () => { $("seed").dataset.quantitySource = "direct"; });
  const session = await api("/api/session");
  state.researcher = session.researcher;
  state.canBrowseLab = Boolean(session.lab_admin || session.lab_shared);
  if (session.researcher) {
    $("lab-account").hidden = false;
    I18N.text(document.querySelector(".platform-brand span"), I18N.term("단백질 복합체·나노바디 분석 · 연구실 공유 작업 공간"));
    $("lab-user").textContent = session.researcher;
    $("catalog-owner-label").hidden = $("db-vhh-owner-label").hidden = !state.canBrowseLab;
    $("catalog-visibility").hidden=false;
    I18N.text($("catalog-visibility"),I18N.term(session.lab_admin?"관리자 · 연구실 전체 결과를 조회합니다.":session.lab_shared?"연구실 전체 결과를 조회할 수 있습니다.":"내가 제출한 분석 결과를 조회합니다."));
    if(session.lab_admin) {
      $("lab-sharing").hidden=false;$("share-lab-results").checked=session.lab_shared;
      action("apply-lab-sharing",async()=>{
        await api("/api/lab/sharing",{shared:$("share-lab-results").checked});
        I18N.text($("sharing-feedback"),I18N.term("열람 권한을 저장했습니다."));
      });
    }
    if(!state.canBrowseLab) {
      $("open-result").hidden=$("result-path").hidden=true;
    }
    $("lab-usage").hidden = false;
    $("lab-usage").ontoggle = () => refreshLabUsage().catch(e => notice(e.message, true));
    action("lab-usage-next", () => refreshLabUsage(true));
    action("lab-logout", async () => { await api("/api/lab/logout", {}); window.location.reload(); });
  }
  state.presets = session.presets || {};
  state.planningDefaults = session.planning_defaults || {};
  I18N.text($("platform-version"), session.version ? "v" + session.version : "");
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
  const requestedCandidate = new URLSearchParams(window.location.search).get("candidate");
  if (requested && requestedCandidate && state.currentView === "results") {
    const matches = new URLSearchParams(window.location.hash.slice(1)).get("matches") || "";
    await openExactCandidate(requestedCandidate, matches);
  }
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
  if (!rows.length)
    throw Error("이름과 표준 아미노산 서열을 확인하세요. X·DNA/RNA·빈 서열은 사용할 수 없습니다.");
  const names = new Set();
  for (const [index, row] of rows.entries()) {
    if (!row.name) throw Error(`FASTA ${index + 1}번째 후보: 이름이 없습니다. >이름을 입력하세요.`);
    if (!row.sequence) throw Error(`FASTA ${index + 1}번째 후보 (${row.name}): 서열이 비어 있습니다.`);
    const invalid = [...new Set(Array.from(row.sequence).filter(letter => !"ACDEFGHIKLMNPQRSTVWY".includes(letter)))].sort();
    if (invalid.length) throw Error(`FASTA ${index + 1}번째 후보 (${row.name}): 허용되지 않는 문자 ${invalid.slice(0, 10).join(", ")}. 표준 아미노산 20종만 입력하세요.`);
    const key = row.name.toLowerCase();
    if (names.has(key)) throw Error(`FASTA ${index + 1}번째 후보 (${row.name}): 이름이 중복됩니다.`);
    names.add(key);
  }
  return rows;
}
function noviceInputFasta(id) {
  try { return noviceFasta($(id).value); }
  catch (error) { error.inputField = $(id); throw error; }
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
  const entries = noviceInputFasta("complex-fasta");
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
  I18N.text(settingsCard.querySelector("h2"), I18N.term("실행 조건"));
  const intro = $("analysis-preset").nextElementSibling;
  I18N.text(intro, I18N.term("새 분석의 기본값은 Seed 42와 엔진별 모델 1개입니다. screen은 모든 입력 후보를 평가합니다. 복원한 설정은 그대로 유지합니다."));
  const advanced = el("details"); advanced.id="novice-advanced";advanced.append(el("summary",I18N.term("고급 설정 · 후보 수·Seed·기존 설정 가져오기")));
  while (intro.nextSibling) advanced.append(intro.nextSibling);settingsCard.append(advanced);
  settingsCard.insertBefore($('mature-library-settings'), advanced);
  const controls=el("fieldset");controls.append(el("legend",I18N.term("평가 엔진 · 동일 입력의 독립 결과")));
  for (const engine of state.starter.engines) {const label=el("label", null,"check"), box=el("input");box.type="checkbox";box.checked=true;box.value=engine;box.dataset.noviceEngine="";label.append(box,document.createTextNode(({colabfold:"ColabFold",boltz2:"Boltz-2",chai1:"Chai-1",opendde:"OpenDDE Preview"})[engine]||engine));controls.append(label);}
  const msa=el("select");msa.id="novice-msa"; for(const [v,label] of [["public_api","공용 MSA 서버 · 정확한 서열 전송 동의 필요"],["cache","검증된 MSA 캐시 재사용"],["local_mmseqs2","등록된 로컬 MSA 검색"]]) {const o=el("option",I18N.term(label));o.value=v;msa.append(o);}
  const ml=el("label",I18N.term("MSA 준비 방식"));ml.htmlFor=msa.id;
  const msaNote=el("p",null,"muted"); msaNote.id="msa-transfer-note";
  msa.setAttribute("aria-describedby",msaNote.id); settingsCard.insertBefore(ml,advanced); settingsCard.insertBefore(msa,advanced);
  settingsCard.insertBefore(msaNote,advanced);
  const consent=el("input");consent.id="novice-consent";consent.type="checkbox";const cl=el("label",null,"check");cl.append(consent,el("span", I18N.term("이 입력의 단백질 서열을 api.colabfold.com으로 전송하는 데 동의합니다.")));settingsCard.insertBefore(cl,advanced);
  const demoMsaNote=el("p",I18N.term("CPU 합성 데모는 MSA를 준비하거나 외부 서버로 서열을 전송하지 않습니다."),"muted");settingsCard.insertBefore(demoMsaNote,advanced);
  const plan=el("p","","next-step");plan.id="novice-model-plan";settingsCard.insertBefore(controls,advanced);settingsCard.insertBefore(plan,advanced);
  plan.setAttribute("role", "status"); plan.setAttribute("aria-live", "polite");
  const scale = el("fieldset"); scale.id = "analysis-scale";
  scale.append(el("legend", I18N.term("분석 규모")));
  const scaleNote = el("p", null, "muted"); scaleNote.id = "analysis-scale-note"; scale.append(scaleNote);
  const breakdown = el("div"); breakdown.id = "quantity-breakdown"; breakdown.setAttribute("role", "status"); breakdown.setAttribute("aria-live", "polite"); scale.append(breakdown);
  for (const id of ["design-count", "candidate-limit"]) {
    const help = el("p", null, "muted"); help.id = id + "-help";
    const current = el("p"); current.id = id + "-current"; current.setAttribute("aria-live", "polite");
    $(id).setAttribute("aria-describedby", id + "-current " + id + "-help");
    scale.append(document.querySelector('label[for="' + id + '"]'), $(id), current, help);
  }
  const selectionLabel = el("label", I18N.term("최종 선별 상한 · 여러 엔진 비교는 엔진별")); selectionLabel.htmlFor = "selection-limit";
  const selection = el("input"); selection.id = "selection-limit"; selection.type = "number"; selection.min = "1"; selection.step = "1"; selection.setAttribute("aria-describedby", "selection-limit-help");
  const selectionHelp = el("p", null, "muted"); selectionHelp.id = "selection-limit-help";
  const selectionCurrent = el("p"); selectionCurrent.id = "selection-limit-current";
  selection.setAttribute("aria-describedby", "selection-limit-current selection-limit-help");
  const conditions = el("details"), conditionData = el("pre"); conditionData.id = "quantity-conditions";
  conditions.append(el("summary", I18N.term("평가 엔진별 Seed·샘플 설정")), conditionData);
  scale.append(selectionLabel, selection, selectionCurrent, selectionHelp, conditions); settingsCard.insertBefore(scale, advanced);
  settingsCard.insertBefore(cl,advanced);
  const complex=el("div");complex.id="novice-complex-fields";complex.hidden=true;
  const label=el("label",I18N.term("복합체 단백질 FASTA · 위에서 아래 순서로 체인 배정"));label.htmlFor="complex-fasta";const fasta=el("textarea");fasta.id="complex-fasta";fasta.rows=8;fasta.spellcheck=false;
  const copies=el("div");copies.id="novice-complex-copies";complex.append(label,fasta,copies);inputCard.insertBefore(complex,$("fasta-fields"));
  const footer=el("div",null,"novice-footer"),prev=el("button",I18N.term("이전"),"secondary"),next=el("button",I18N.term("입력 확인 후 다음")),reason=el("p");prev.id="novice-previous";next.id="novice-next";reason.id="novice-blocker";reason.setAttribute("role","alert");footer.append(prev,next,reason);host.append(footer);
  let step=0,restoring=false,draftTimer,saveQueue=Promise.resolve();
  const sequenceHelp = new Map();
  for (const id of ["complex-fasta", "target-fasta", "vhh-fasta", "wt-fasta"]) {
    const field = $(id), help = el("p", null, "error field-error");
    help.id = id + "-validation"; help.hidden = true; help.setAttribute("role", "status");
    field.setAttribute("aria-describedby", [field.getAttribute("aria-describedby"), help.id].filter(Boolean).join(" "));
    field.after(help); sequenceHelp.set(id, help);
  }
  function refreshSequenceHelp() {
    const mode = $("mode").value, sequenceInput = $("input-kind").value === "fasta";
    const active = new Set(mode === "complex" ? (sequenceInput ? ["complex-fasta"] : []) : [
      ...(sequenceInput ? ["target-fasta"] : []), ...(mode === "screen" ? ["vhh-fasta"] : mode === "mature" ? ["wt-fasta"] : [])]);
    let blocked = false;
    for (const [id, help] of sequenceHelp) {
      const field = $(id); let message = "";
      if (active.has(id) && field.value.trim()) {
        try {
          const rows = noviceFasta(field.value);
          if (id === "target-fasta" && rows.length !== 1) throw Error("단일 분석은 표적 1개입니다. 여러 표적은 배치 분석을 사용하세요.");
          if (id === "wt-fasta" && rows.length !== 1) throw Error("WT VHH 1개를 입력하세요.");
        } catch (error) { message = error.message; }
      }
      help.hidden = !message; I18N.text(help, I18N.term(message));
      if (message) { field.setAttribute("aria-invalid", "true"); field.setAttribute("aria-errormessage", help.id); blocked = true; }
      else if (field.getAttribute("aria-errormessage") === help.id) { field.removeAttribute("aria-invalid"); field.removeAttribute("aria-errormessage"); }
    }
    next.disabled = step === 1 && blocked;
  }
  host.insertBefore(rail, task);
  const tasks=["목적을 고르세요. 필요한 입력과 결과를 확인하면 다음으로 이동합니다.","전체 서열·체인 순서·사본 수를 확인하세요. 오류 없는 필수 입력이면 다음으로 이동합니다.","계획 모델 수, 선택 엔진, MSA 전송 방식을 확인하세요.","입력 확인을 누르고 적용 조건을 읽은 뒤 분석 실행을 누르세요. 저장된 환경 확인 기록을 사용합니다."];
  function render(){
    host.querySelector(".setup-entry").hidden = step !== 0;
    [purposeCard,inputCard,settingsCard,reviewCard].forEach((n,i)=>n.hidden=i!==step);I18N.text(heading, I18N.format("{0}/4 · {1}", [step+1, I18N.term(state.starter.steps[step])]));I18N.text(task, I18N.term(tasks[step]));prev.hidden=step===0;next.hidden=step===0||step===3;I18N.text(next, step===1 ? I18N.term("입력 확인 후 다음") : I18N.term("조건 확인 후 다음"));
    [...rail.children].forEach((n,i)=>n.setAttribute("aria-current",i===step?"step":"false"));
    const isComplex=$("mode").value==="complex";
    complex.hidden=!isComplex||$("input-kind").value!=="fasta";$("fasta-fields").hidden=isComplex||$("input-kind").value!=="fasta";
    controls.hidden=$("analysis-preset").value!=="small-four-v1";$("mock").disabled=isComplex;demoControls.hidden=isComplex;
    const simulated=$("mock").checked&&!isComplex;
    ml.hidden=msa.hidden=simulated || controls.hidden;
    cl.hidden=msaNote.hidden=true;demoMsaNote.hidden=!simulated;
    try {const cfg = inputSettings(true);
      const needsConsent = usesPublicMsa(cfg);
      cl.hidden = simulated || !needsConsent;
      msaNote.hidden = simulated;
      const note = needsConsent ? "이 설정에서는 공용 MSA 검색을 위해 입력 서열을 전송합니다. 동의하려면 아래 확인란을 선택하세요."
        : !controls.hidden && msa.value === "cache" ? "검증된 캐시를 사용합니다. 해당 서열의 저장된 MSA가 필요하며 외부 전송 동의는 필요하지 않습니다."
        : !controls.hidden && msa.value === "local_mmseqs2" ? "서버에 등록된 데이터베이스를 검색합니다. 외부 전송 동의는 필요하지 않습니다."
        : "현재 설정에는 공용 MSA 서열 전송 동의가 필요하지 않습니다.";
      I18N.text(msaNote, I18N.term(note));
      renderAnalysisScale(cfg, $("mode").value); I18N.text(plan, I18N.term(noviceModelPlan(cfg,$("mode").value,$("mode").value==="screen"?noviceFasta($("vhh-fasta").value).length:0)));}catch(e){clearScalePreview();I18N.text(plan, I18N.term(e.message));}
    I18N.text($("review-input"), I18N.term("입력 확인"));I18N.text($("start-job"), I18N.term("분석 실행"));
    refreshSequenceHelp();
  }
  function jump(n){step=n;I18N.text(reason, "");render();heading.focus();}
  state.nextAnalysis = () => {
    consent.checked = false;
    invalidateInput();
    step = 0;
    I18N.text(reason, "");
    render();
  };
  state.showInputReview = () => jump(state.input ? 3 : 1);
  function validateInputs(){
    if($("mode").value==="complex") return noviceComplexProteins();
    if($("input-kind").value==="fasta") {if(noviceInputFasta("target-fasta").length!==1)throw Error("단일 분석은 표적 1개입니다. 여러 표적은 배치 분석을 사용하세요.");}
    else if(!state.analyzed||!$("acknowledge").checked)throw Error("구조를 불러오고 체인·준비 방식과 경고를 확인하세요.");
    if($("mode").value==="screen")noviceInputFasta("vhh-fasta");
    if($("mode").value==="mature"&&noviceInputFasta("wt-fasta").length!==1)throw Error("WT VHH 1개를 입력하세요.");
  }
  next.onclick=()=>{try {if(step===1)validateInputs();else inputSettings();jump(step+1);}catch(e){I18N.text(reason, I18N.term(e.message));const field=e.inputField||(step===2?consent:$("mode").value==="complex"?fasta:$("input-kind").value==="fasta"?$("target-fasta"):$("structure-file"));field.focus();field.setAttribute("aria-invalid","true");}};
  start.onclick=()=>{fields();jump(1);};prev.onclick=()=>jump(step-1);
  function copiesReview(){try{const old=[...copies.querySelectorAll("input")].map(i=>i.value),rows=noviceFasta(fasta.value);copies.replaceChildren(...rows.map((r,i)=>{const l=el("label",I18N.format("{0} · {1} · {2} aa · 사본 수", [String.fromCharCode(65+i), r.name, r.sequence.length])),n=el("input");n.id="complex-copies-"+i;n.type="number";n.min="1";n.max="25";n.value=old[i]||"1";l.append(n);return l;}));}catch{copies.replaceChildren();}}
  fasta.addEventListener("input",copiesReview);
  host.addEventListener("input",e=>{if(e.target!==consent)consent.checked=false;I18N.text(reason, "");render();if(restoring)return;clearTimeout(draftTimer);draftTimer=setTimeout(()=>{const fields={};for(const n of host.querySelectorAll("input[id],select[id],textarea[id]")){if(n.type!=="file"&&n.id!=="novice-consent")fields[n.id]=n.type==="checkbox"?n.checked:n.value;}fields.comparison_engines=[...controls.querySelectorAll('[data-novice-engine]:checked')].map(i=>i.value);saveQueue=saveQueue.catch(()=>{}).then(()=>api("/api/draft",{version:1,fields})).catch(e=>{I18N.text(reason, I18N.concat([I18N.term("초안 저장 실패: "), e.message]));});},500);});
  action("load-tutorial",async()=>{const revision=state.inputRevision;const example=await api("/api/tutorials/"+$("tutorial-mode").value),entry=example.payload.entries[0];if(revision!==state.inputRevision)throw Error("불러오는 동안 입력이 변경되었습니다. 현재 입력을 유지합니다.");$("mode").value=example.mode;$("input-kind").value="fasta";$("analysis-preset").value="small-four-v1";$("advanced").value="{}";resetMutationControls();$("mock").checked=false;$("candidate-limit").value="";$("design-count").value="";if($("selection-limit"))$("selection-limit").value="";$("seed").value="42";$("job-name").value=example.payload.name;$("target-fasta").value=entry.content||"";$("vhh-fasta").value=entry.vhh_fasta||"";$("wt-fasta").value=entry.wt_fasta||"";fasta.value=(entry.proteins||[]).map(p=>`>${p.name}\n${p.sequence}`).join("\n");consent.checked=false;invalidateInput();copiesReview();fields();jump(1);I18N.text($("tutorial-status"), I18N.term("공개 입력을 불러왔습니다. 아직 계산은 시작하지 않았습니다."));});
  render();const draftRevision=state.inputRevision;purposeChoices.querySelectorAll("button").forEach(button=>button.disabled=true);start.disabled=true;$("load-tutorial").disabled=true;try{const draft=await api("/api/draft");if(draftRevision!==state.inputRevision)throw Error("입력이 변경되어 저장된 초안을 덮어쓰지 않았습니다.");restoring=true;for(const [id,value]of Object.entries(draft.fields||{})){const n=$(id);if(n&&host.contains(n)&&n.type!=="file"){if(n.type==="checkbox")n.checked=value===true;else n.value=String(value);}}if(Object.hasOwn(draft.fields||{},'seed')&&$('seed').dataset)$('seed').dataset.quantitySource='restored';if(draft.fields?.comparison_engines)for(const n of controls.querySelectorAll('[data-novice-engine]'))n.checked=draft.fields.comparison_engines.includes(n.value);consent.checked=false;fields();copiesReview();if(Object.keys(draft.fields||{}).length)I18N.text(task, I18N.term("초안 입력을 복원했습니다. 구조 파일을 다시 선택하고 전송 동의와 입력을 확인하세요. 설치 환경 확인 기록은 재사용합니다."));}catch(e){I18N.text(reason, I18N.concat([I18N.term("초안 복원 실패: "), e.message]));}finally{restoring=false;purposeChoices.querySelectorAll("button").forEach(button=>button.disabled=false);start.disabled=false;$("load-tutorial").disabled=false;render();}
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
