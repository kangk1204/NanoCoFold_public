/* Shared by the result explorer, printable report and hosted site. Display only. */
(() => {
  "use strict";
  if (typeof document === "undefined" || globalThis.NanoMetricGuide) return;
  const guide = {
    ipsae: ["↑ 계면 배치 신뢰도", "↑ Interface confidence", "0–1. 예측 오차(PAE)를 이용해 두 체인의 상대 배치가 얼마나 일관적인지 나타냅니다. 높을수록 신뢰도가 높습니다.", "On a 0–1 scale, higher values indicate more confidence in how the two chains fit together, based on predicted alignment errors."],
    iptm: ["↑ 상대 배치 신뢰도", "↑ Placement confidence", "0–1. 높을수록 모델이 체인 사이의 상대 배치를 더 확신합니다. 체인 쌍 점수인지 전체 복합체 점수인지는 지표 이름과 적용 범위에서 확인하세요.", "On a 0–1 scale, higher values mean greater confidence in the relative positions of the chains. The label and recorded scope identify a chain pair or the whole complex."],
    interface_plddt: ["↑ 접촉 부위의 구조 신뢰도", "↑ Local confidence at the interface", "0–100. 서로 접촉하는 부위에서 잔기 구조의 평균 신뢰도입니다. 두 체인이 올바르게 결합했는지는 ipSAE와 PAE도 함께 보세요.", "Mean confidence in the local structure of contacting residues, from 0 to 100. To assess how the chains fit together, also check ipSAE and PAE."],
    interface_pae: ["↓ 상대 위치의 불확실성", "↓ Uncertainty in relative position", "서로 접촉하는 잔기 쌍의 예측 위치 오차를 양방향으로 평균한 값입니다. 단위는 Å이며, 낮을수록 상대 위치가 더 확실합니다.", "Mean predicted alignment error across contacting residue pairs in both directions, in ångströms (Å). Lower values indicate more certain relative positions."],
    ptm: ["↑ 전체 구조의 신뢰도", "↑ Overall fold confidence", "0–1. 모델이 전체 복합체의 접힘을 얼마나 확신하는지 나타냅니다. 높더라도 결합 부위의 배치는 별도로 확인하세요.", "On a 0–1 scale, a higher score indicates more confidence in the overall fold. Check the interface separately."],
    pdockq: ["↑ 예측한 계면의 품질", "↑ Estimated interface quality", "접촉 수와 접촉 부위의 구조 신뢰도를 이용한 점수입니다. 같은 방법에서 높은 값은 더 그럴듯한 계면을 뜻합니다. 결합 세기를 재는 값은 아닙니다.", "Uses contacts and local confidence to estimate interface quality. Higher values from the same method favor a more plausible interface; the score does not measure binding strength."],
    pdockq2: ["↑ 두 방향 중 낮은 쪽의 품질", "↑ Quality of the lower-scoring side", "두 체인 방향 각각에서 접촉 부위 신뢰도와 PAE로 품질을 추정하고 더 낮은 점수를 표시합니다. 같은 방법에서 높을수록 좋습니다.", "Interface quality is estimated for each side from local confidence and PAE. This is the lower of the two scores. Higher is better when comparing the same method."],
    pdockq2_target: ["↑ 이 체인 쪽의 계면 품질", "↑ Interface quality on this side", "표적 쪽 접촉 잔기의 신뢰도와 이 방향의 PAE로 추정합니다. 일반 복합체에서는 기준 체인 쪽 점수입니다.", "Estimated from confidence in contacting residues on the target and PAE in that direction. In a general complex, this is the reference-chain side."],
    pdockq2_binder: ["↑ 이 체인 쪽의 계면 품질", "↑ Interface quality on this side", "VHH 쪽 접촉 잔기의 신뢰도와 이 방향의 PAE로 추정합니다. 일반 복합체에서는 상대 체인 쪽 점수입니다.", "Estimated from confidence in contacting residues on the VHH and PAE in that direction. In a general complex, this is the partner-chain side."],
    interface_pae_ab: ["↓ 이 방향의 예측 오차", "↓ Error in this direction", "표적을 행, VHH를 열로 읽은 접촉 잔기 쌍의 평균 PAE(Å)입니다. 일반 복합체에서는 기준 체인이 행입니다. 반대 방향의 값과 다를 수 있습니다.", "Mean PAE (Å) for contacting residues, with the target on rows and the VHH on columns. For a general complex the reference chain is on rows. The reverse direction can differ."],
    interface_pae_ba: ["↓ 반대 방향의 예측 오차", "↓ Error in the reverse direction", "VHH를 행, 표적을 열로 읽은 접촉 잔기 쌍의 평균 PAE(Å)입니다. 일반 복합체에서는 상대 체인이 행입니다.", "Mean PAE (Å) for contacting residues, with the VHH on rows and the target on columns. For a general complex the partner chain is on rows."],
    interface_pae_median: ["↓ 대표적인 예측 오차", "↓ Typical predicted error", "접촉 잔기 쌍의 양방향 PAE를 정렬했을 때 가운데 값(Å)입니다. 낮을수록 전형적인 접촉 쌍의 위치가 더 확실합니다.", "The middle value of the contact-pair PAE values from both directions, in Å. Lower values mean less uncertainty for a typical pair."],
    interface_pae_p90: ["↓ 오차가 큰 접촉까지 확인", "↓ Uncertainty among less certain contacts", "접촉 잔기 쌍의 양방향 PAE 중 약 90%가 이 값(Å) 이하입니다. 평균이 낮아도 일부 접촉의 위치가 불확실한지 볼 수 있습니다.", "About 90% of contact-pair PAE values from both directions are at or below this value (Å). It can reveal uncertain contacts even when the mean is low."],
    target_plddt: ["↑ 표적 자체의 구조 신뢰도", "↑ Local confidence in the target", "표적 잔기의 국소 구조 신뢰도를 평균한 값(0–100)입니다. 일반 복합체에서는 기준 체인에 해당합니다.", "Mean local confidence across target residues (0–100), or across the reference chain in a general complex."],
    binder_plddt: ["↑ 상대 체인 자체의 구조 신뢰도", "↑ Local confidence in the partner", "VHH 잔기의 국소 구조 신뢰도를 평균한 값(0–100)입니다. 일반 복합체에서는 상대 체인에 해당합니다.", "Mean local confidence across VHH residues (0–100), or across the partner chain in a general complex."],
    complex_plddt: ["↑ 복합체 전체의 국소 구조 신뢰도", "↑ Mean local confidence across the complex", "모든 체인 잔기의 국소 구조 신뢰도를 평균한 값(0–100)입니다. 이 평균이 높아도 두 체인의 상대 배치가 맞는지는 별도입니다.", "Mean local confidence across residues in all chains (0–100). A high average does not establish that the chains are positioned correctly."],
    hotspot_contact_frac: ["↑ 지정 잔기와 더 많이 접촉", "↑ More designated residues contacted", "지정한 잔기 중 예측 구조에서 접촉 거리 기준을 충족한 비율입니다. 1은 모두, 0은 하나도 접촉하지 않았다는 뜻입니다. 결합 확률은 아닙니다.", "Fraction of designated residues within the contact distance in the prediction. 1 means all were contacted; 0 means none were. This is not a binding probability."],
    n_clashes: ["↓ 적을수록 충돌이 적음", "↓ Fewer potential clashes", "서로 너무 가까운 무거운 원자쌍의 수입니다. 0은 이 거리 기준에서 충돌이 발견되지 않았다는 뜻입니다.", "Counts heavy-atom pairs that are too close. Zero means that no clashes were detected using this distance cutoff."],
    dev_severe: ["↓ 선별 시 검토할 항목이 적음", "↓ Fewer sequence flags used in selection", "실행 당시 선별 기준에서 확인이 필요한 서열 특징의 수입니다. 허용 개수를 넘으면 구조 점수가 높아도 자동 선별을 보류할 수 있습니다.", "Counts sequence features flagged by the recorded selection policy. Exceeding the allowed count can put selection on hold even when structure scores are high."],
    dev_flags: ["↓ 표시된 서열 특징이 적음", "↓ Fewer flagged sequence features", "서열 검사에서 표시된 전체 항목 수입니다. 모두 같은 심각도를 뜻하지 않습니다. 선별에 쓰인 수는 ‘서열 검토 항목 수’에서 확인하세요.", "Total number of flags in the sequence screen. They need not be equally severe; the separate selection flag count shows which ones were used for screening."],
    n_hbonds: ["거리 기준 개수 · 구조와 함께 해석", "Count · inspect the geometry", "두 원자가 모두 질소(N) 또는 산소(O)이고 서로 가까운 쌍의 수입니다. 결합 각도는 검사하지 않으므로 수소결합 개수로 읽거나 많을수록 좋다고 판단하지 않습니다.", "Counts close atom pairs where each atom is either nitrogen or oxygen. Angles are not tested, so this is not a count of confirmed hydrogen bonds. A larger count is not necessarily better."],
    n_salt_bridges: ["거리 기준 개수 · 구조와 함께 해석", "Count · inspect the geometry", "반대 전하를 띨 수 있는 곁사슬 원자들이 가까이 있는 쌍의 수입니다. 많다고 결합이 더 강하다는 뜻은 아닙니다.", "Counts nearby atom pairs on oppositely charged side chains. A larger count alone does not imply stronger binding."],
    esm_score: ["↑ 같은 방식에서 서열 적합도가 높음", "↑ More favored by the sequence model", "ESM이 이 서열이나 치환을 얼마나 선호하는지 나타냅니다. 같은 계산 방식 안에서 비교하세요. 친화도를 예측한 점수는 아닙니다.", "Indicates how much the ESM model favors a sequence or substitution. Compare scores from the same scoring method; they are not affinity estimates."],
    composite_score: ["↑ 같은 비교 집단 안에서 높은 점수", "↑ Higher within the same comparison group", "여러 지표를 가중해 합한 상대 점수입니다. 같은 엔진과 후보 집합에서 비교하세요. 점수가 높아도 자동 선별 기준은 따로 적용됩니다.", "A weighted relative score. Compare within the same engine and candidate set. A high score does not override the separate selection criteria."],
    n_hotspots_contacted: ["접촉한 지정 잔기 수", "Designated residues contacted", "지정 잔기 중 접촉 거리 안에 든 수입니다. 지정한 총 잔기 수와 함께 읽으세요.", "Counts designated residues within the contact distance. Read it against the total number designated."],
    pose_supporting_engines: ["같은 자세를 지지한 엔진 수", "Engines supporting the same pose", "같은 후보를 비슷한 자세로 예측한 서로 다른 엔진 수입니다. 독립 실험의 횟수는 아닙니다.", "Counts distinct engines that predicted a similar pose for this candidate, not independent experiments."],
    design_binder_rmsd_A: ["↓ 두 예측 자세가 더 비슷함", "↓ Greater agreement between poses", "표적을 겹쳤을 때 설계 구조와 재예측 구조 사이의 VHH 위치 차이(Å)입니다. 낮을수록 두 예측이 비슷합니다.", "VHH Cα displacement (Å) between design and reprediction after aligning the target. Smaller values indicate closer agreement between the two models."],
    review_rank: ["작은 번호부터 검토", "Review smaller ranks first", "같은 실행 조건에서 검토할 순서입니다. 동률이 있을 수 있으며 1위라도 검토 보류일 수 있습니다.", "Review order within the same run conditions. Ties are possible, and rank 1 can still be on hold."],
  };
  const lookup = key => {
    const base = String(key).replace(/^(?:colabfold|boltz)_(?=iptm$|ipsae$)/, "");
    return Object.hasOwn(guide, base) ? guide[base] : undefined;
  };
  let trigger = null, popup = null, pinned = false, delay = 0;
  const selector = "button[data-ncf-metric]";
  function decorate(host, key, missing = false, sort = false) {
    if (!host || !lookup(key) || host.querySelector(selector)) return;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.ncfMetric = key;
    if (missing) button.dataset.ncfMissing = "true";
    if (sort) {
      button.className = "ncf-metric-help ncf-help-only";
      const label = host.querySelector("button")?.textContent || key;
      if (globalThis.I18N) I18N.attribute(button, "aria-label", I18N.concat([I18N.bilingual("지표 설명: ", "Explain metric: "), I18N.term(host.dataset.ncfCaption || key)]));
      else button.setAttribute("aria-label", label);
    } else {
      button.className = "ncf-metric-help";
      button.append(...host.childNodes);
    }
    host.append(button);
  }
  function close() {
    clearTimeout(delay);
    if (trigger) trigger.removeAttribute("aria-describedby");
    trigger = null; pinned = false;
    if (popup) popup.hidden = true;
  }
  function place() {
    if (!trigger || !popup) return;
    const b = trigger.getBoundingClientRect(), p = popup.getBoundingClientRect();
    if (!trigger.isConnected || !b.height || b.bottom < 0 || b.top > innerHeight || b.right < 0 || b.left > innerWidth) { close(); return; }
    popup.style.left = Math.max(8, Math.min(b.left, innerWidth - p.width - 8)) + "px";
    // Touch and keyboard users can always reach the control again to close it.
    const y = b.bottom + 5;
    popup.style.top = Math.max(6, Math.min(y + p.height + 8 <= innerHeight ? y : b.top - p.height - 5, innerHeight - p.height - 6)) + "px";
  }
  function draw() {
    if (!trigger) return;
    const g = lookup(trigger.dataset.ncfMetric);
    const en = document.documentElement.lang !== "ko";
    const title = document.createElement("strong"), detail = document.createElement("span");
    title.textContent = g[en ? 1 : 0]; detail.textContent = g[en ? 3 : 2];
    popup.replaceChildren(title, detail);
    if (trigger.dataset.ncfMissing === "true") {
      const note = document.createElement("span"); note.className = "ncf-missing";
      note.textContent = en ? "Not assessed: no value was recorded for this model. This is not zero." : "미평가: 이 모델에 기록된 값이 없습니다. 0점과 다릅니다.";
      popup.append(note);
    }
    place();
  }
  function show(button, pin = false) {
    if (!button || !lookup(button.dataset.ncfMetric)) return;
    clearTimeout(delay);
    if (trigger !== button) { close(); trigger = button; }
    if (pin) pinned = true;
    if (!popup) {
      popup = document.createElement("div"); popup.id = "ncf-metric-tooltip"; popup.setAttribute("role", "tooltip");
      document.body.append(popup);
    }
    popup.hidden = false; trigger.setAttribute("aria-describedby", popup.id);
    draw();
  }
  const at = el => el?.closest?.(selector);
  const hoverAt = el => at(el) || el?.closest?.("th")?.querySelector(".ncf-help-only");
  const leave = () => { clearTimeout(delay); if (!pinned) delay = setTimeout(close, 180); };
  document.addEventListener("pointerover", event => {
    if (event.pointerType === "touch") return;
    if (popup?.contains(event.target)) { clearTimeout(delay); return; }
    const b = hoverAt(event.target);
    if (b && (!pinned || b === trigger) && b !== hoverAt(event.relatedTarget)) show(b);
  });
  document.addEventListener("pointerout", event => {
    if (event.pointerType === "touch" || !trigger) return;
    if (hoverAt(event.relatedTarget) !== trigger && !popup?.contains(event.relatedTarget) &&
        (hoverAt(event.target) === trigger || popup?.contains(event.target)) && document.activeElement !== trigger) leave();
  });
  document.addEventListener("focusin", event => { const b = at(event.target); if (b) show(b); else if (trigger) close(); });
  document.addEventListener("focusout", event => { if (at(event.target) === trigger && trigger) close(); });
  document.addEventListener("click", event => {
    const b = at(event.target);
    if (b) { if (pinned && b === trigger) close(); else show(b, true); }
    else if (!popup?.contains(event.target)) close();
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && trigger) { close(); event.preventDefault(); } });
  // Scrollable tables or panels may move an anchor independently of the window.
  document.addEventListener("scroll", place, true);
  window.addEventListener("resize", place);
  new MutationObserver(draw).observe(document.documentElement, {attributes:true, attributeFilter:["lang"]});
  const style = document.createElement("style");
  style.textContent = `
:is(body,#view-results) button.ncf-metric-help{display:inline-block;min-height:28px;max-width:100%;margin:0;padding:1px 2px;border:0;border-radius:3px;background:transparent;color:inherit;font:inherit;text-align:start;line-height:1.35;white-space:normal;cursor:help}
:is(body,#view-results) button.ncf-metric-help:hover,:is(body,#view-results) button.ncf-metric-help:focus-visible{text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px;color:#075f62;filter:none}
button.ncf-metric-help:focus-visible{outline:2px solid #176b68;outline-offset:2px}
.ncf-metric-help::after{content:"?";display:inline-grid;place-items:center;vertical-align:.09em;border:1px solid currentColor;border-radius:50%;min-width:1.12em;height:1.12em;margin-inline-start:.4em;font:600 .74em/1 system-ui,sans-serif;color:#286663}
:is(body,#view-results) button.ncf-help-only{width:28px;min-width:28px;margin-inline-start:2px;text-align:center;vertical-align:middle}
.ncf-help-only::after{margin:0}
#ncf-metric-tooltip{position:fixed;z-index:2147483000;box-sizing:border-box;width:max-content;max-width:min(21rem,calc(100vw - 16px));padding:.7rem .9rem;border:1px solid #95b5af;border-radius:6px;background:#fff;color:#172e2d;box-shadow:0 3px 14px #173c392e;font:normal 15px/1.5 system-ui,sans-serif;white-space:normal;overflow-wrap:anywhere}
#ncf-metric-tooltip[hidden]{display:none}
#ncf-metric-tooltip strong,#ncf-metric-tooltip span{display:block}
#ncf-metric-tooltip strong{color:#115a55;margin-bottom:.25rem}
#ncf-metric-tooltip .ncf-missing{margin-top:.4rem;border-top:1px solid #dbe6e3;padding-top:.35rem}
@media (pointer:coarse){:is(body,#view-results) button.ncf-metric-help{min-height:36px}:is(body,#view-results) button.ncf-help-only{min-width:36px}}
@media print{.ncf-metric-help::after,.ncf-help-only,#ncf-metric-tooltip{display:none!important}}
`;
  document.head.append(style);
  globalThis.NanoMetricGuide = {decorate, has:key => Boolean(lookup(key))};
})();
