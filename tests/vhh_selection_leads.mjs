import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const ui = readFileSync(new URL("../nanocofold/templates/platform.js", import.meta.url), "utf8");
const node = () => ({replaceChildren(){}, append(){}});
const ctx = { $:node, el:node, I18N:{term:x=>x}, fmt:String, recordedEvaluationError:r=>r.error };
vm.createContext(ctx);
vm.runInContext(ui.slice(ui.indexOf("function renderSelectionLeads("),ui.indexOf("async function openExactCandidate(")),ctx);
const base = {candidate_id:"vh1", sequence:"ACDF", predictor_engine:"boltz2",recorded_engine:"boltz2",
  selected:true, selection_rank:1, evaluated:true, final_eligible:true};
const ids = (rows, summary={}) => Array.from(ctx.renderSelectionLeads({mode:"screen", complete:true, ...summary},rows), r=>r.candidate_id);

test("lead is a uniquely recorded eligible first pick within its own engine",()=>{
  assert.deepEqual(ids([base]),["vh1"]);
  for (const patch of [{selection_rank:2},{selection_rank:undefined},{selected:false},{evaluated:false},{error:"failed"},
    {final_eligible:false},{is_parent_control:true},{source_sequence_id:"WT"},{sequence:null},{recorded_engine:null},
    {dev_assessment_policy:"sequence_liability_screen_v2",dev_assessment_status:"review_required"}]) {
    assert.deepEqual(ids([{...base,...patch}]),[]);
  }
  for (const summary of [{mock:true},{complete:false},{mode:"complex"}]) assert.deepEqual(ids([base],summary),[]);
  const chai={...base,candidate_id:"vh2",predictor_engine:"chai1",recorded_engine:"chai1"};
  assert.deepEqual(ids([base,chai]),[]);
  assert.deepEqual(ids([{...base,ranking_scope:"boltz2"},{...chai,ranking_scope:"chai1"}]),["vh1","vh2"]);
  assert.deepEqual(ids([base,{...base,candidate_id:"duplicate-rank"}]),[]);
});
