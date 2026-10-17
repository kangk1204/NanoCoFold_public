# Personal platform HTTP contract

All `/api/` requests require `X-Platform-Token` (the initial HTML replaces `__TOKEN__`). `X-Wizard-Token` is accepted for the existing wizard. All hosts/origins must be loopback; JSON POST bodies are limited to 20 MiB. API errors use `{error}`; FastAPI validation errors use `{detail}`.

- `GET /api/session`: `{workspace,modes,poll_interval_ms:2000}`.
- `GET /api/inputs`: `{inputs:[{id,input_id,config,review,config_path}]}`.
- `POST /api/configs/parse`: `{content:YAML text}` -> `{config:normalized config}`; safe loader and nested mapping validation.
- `POST /api/inputs/load`: existing WizardSession payload `{pdb_id,assembly_id,model_index?:integer}` or `{filename,content,model_index?:integer}`. Browser defaults to model 1; direct inspection without explicit selection retains the single-model requirement. Response `{input_id,chains,warnings,blockers,source:{model_index,model_serial_number,model_count,...},...}`. Chains add `polymer_type,full_sequence,sequence_review:{source,full_length,observed_standard_length,observed_polymer_positions,missing_positions,unknown_positions,observed_to_full,modifications,polymer_components,conflicts,statement}`. Unknown/unrecorded metadata stays null or X, never inferred from a residue-number gap.
- `GET /api/inputs/{input_id}/structure`: token-protected normalized protein PDB for the current loaded input. Chain IDs and residue positions match the load/analyze responses. A replaced upload expires this handle (400); no arbitrary file paths or browser-side assembly expansion. Persistent reviewed inputs still use `GET /api/inputs/{id}`.
- `POST /api/inputs/analyze`: `{input_id,target,context:[]}`; response adds `preparation:{recommended:'coordinates|sequence_prediction',coordinates_available,sequence_confirmation_required,sequence_prediction_blockers}` to existing `{selection_id,target,retained_chains,residues,patches,pdb,warnings,blockers,...}`. Recommendations do not launch inference.
- `POST /api/inputs/review`: `{selection_id,hotspots:[integer],acknowledge:true,mode,mock?,vhh_fasta?:text,wt_fasta?:text,wt_structure?:{filename,content},config?:object}` -> persistent input object. Reviewed target/partner/residue mapping cannot be replaced by advanced config.
- The review endpoint optionally accepts `preparation:'coordinates|sequence_prediction'` (default coordinates), `provider:'boltz2|chai1'`, `prediction_sequences:FASTA text` (selected normalized chain IDs as names), `prediction_hotspots:'auto|reference'` (default auto). Sequence prediction accepts at most 2,000 target+partner residues and 25 chains. It rejects unknown standard sequences, conflicting known residues and ambiguous unknown-token expansions. Missing metadata requires a reviewed full sequence containing observed residues in order; ambiguous positional mappings stay empty. Auto hotspots ignore the reference `hotspots` array. Reference hotspots require unique observed-to-full mappings and are saved as `A:seqN`. This path snapshots the original file plus normalized reference PDB and their hashes in `input_review`, sets `target.pdb/fasta=null`, fixes full target/auxiliary sequences, structure provider, and disables truncation. Manifest schema 2 records reference chains, sequence origins and template spans; no inference occurs on review. Modified native components and new predicted residues are separately described in the complete mapping CSV by `source_observed,source_standard_observed,source_component,source_mapping_kind,coordinate_source`. Existing design-context blockers remain effective.
- `POST /api/inputs`: `{kind:'fasta',content,mode,name?,target_name?,mock?,provider?:'boltz2|chai1',hotspots?:'auto'|array,vhh_fasta?:text,wt_fasta?:text,config?:object}` -> persistent input object. `GET /api/inputs/{id}` retrieves it.
- `POST /api/jobs`: `{input_id?,config_path?,config?:object,mode?,name?,mock?,gpu?:'0',budget_seconds?}` -> job object.
- `POST /api/diagnostics`: same config selection -> queued job with `kind:'diagnostics'`; completion adds `diagnostics:{ok,checks,engines,evidence}`. `GET /api/diagnostics` returns `{jobs}`.
- `POST /api/setup`: `{profile:'core|complex|design|screen|mature|boltzgen|colabfold|opendde|all'}` -> queued job with `kind:'setup'`. `all` includes `antibody`, `boltz2`, `chai1`, `rfantibody`, `boltzgen`, `esm`, `colabfold` and `opendde`; separate installs of the last two are unnecessary after a successful `all` install.
- `GET /api/jobs?limit=100&before=&before_id=&active=false`: `{jobs,next_cursor,active_jobs?,active_next_cursor?}`. Each stream is bounded by `limit` (1–1,000). `jobs` is the ordinary chronological page; its cursor never skips displaced history. On the first ordinary page, `active_jobs` is a separate bounded preview of queued/running/cancelling jobs. Follow `active_next_cursor` with `active=true` to paginate that stream. The browser merges/deduplicates both previews and fetches the selected job independently. `GET /api/jobs/{id}`: job. Fields include `id,kind,state,name,mode,mock,run_dir,result_id,gpu,created,started,finished,elapsed_seconds,phase,progress:{completed,total},error,can_resume,history`. Lifecycle: `queued,running,cancelling,cancelled,interrupted,failed,partial_failure,budget_exhausted,complete`.
- `GET /api/jobs/{id}/logs?offset=0`: `{offset,next_offset,text,done}`; offsets are bytes. Poll every 2 seconds. `POST .../cancel` and `POST .../resume` return current job. Resuming validates fixed config/input snapshot hashes; it reuses only the CLI's validated completed steps. Exhausted jobs require resume `{additional_budget_seconds:number}` (positive, maximum 7 days); the budget becomes previous elapsed time plus the requested allowance, with the extension recorded in history and fixed configuration unchanged.
- `GET /api/results`: `{results:[summary]}`. `POST /api/results/open {path}` registers an existing workspace result read-only and returns summary.
- `GET /api/results/{id}`: summary `{id,name,path,mode,mock,total,scored_total,eligible_total,selected_total,failed_candidates,zero_eligible,complete,status,execution_state,platform_status,warnings,issues,config,state,conditions,readonly:true}`. Preserved final files do not override a current failed/cancelled/running execution. Worker/supervisor atomically saves `platform_status.json` terminal job provenance (source job, effective budget, resume history); it takes precedence only if its update is at least as recent as the current execution start. Older sidecars remain provenance with `applies:false`. Conditions include `seed,engine,msa,restraint,target`. Never combine scores across conditions.
- `GET .../candidates?search=&sort=composite_score&descending=true&eligible=true&offset=0&limit=100`: `{total,filtered_total,rows,columns,issues,metric_help}`. Omit `eligible` to include unassessed candidates. Maximum page 10,000. Rows preserve source CSV columns and add `candidate_id,evaluated,selected,exclusion_reasons,missing_metrics,structure_available`. Missing numerical measurements are `null`; unscored `final_eligible` is `null`. Added `threshold_checks:[{metric,value,operator,threshold,passed,threshold_source}]` only reports saved flags/thresholds, never recomputes eligibility. `cdr_details:{evaluated,missing_regions,configured_source,scheme,configured_ranges,recorded_ranges,mutation}` separates configured ranges from actual saved mature ranges. `developability_details:{warnings,unevaluated,severe_count,unevaluated_count,flags}` exposes saved assessment coverage and causes.
- `GET .../observations?candidate_id=...`: `{rows,issues}`. Each observation adds `candidate_id,observation_id,seed,msa,restraint,source_table?`.
- `GET .../structure?candidate_id=...&observation_id=...`: `{candidate_id,observation_id,content,format:'cif|pdb',chains:[{id,role,residues}],target_chain,binder_chain,hotspots:[labels],contacts,clashes,pae:{values,labels,unit:'Å'}|null,residue_mapping,issues}`. Contacts/clashes `{a,b,distance,kind:'target|context'}`. Structure chain/author residue labels are exact; the reviewed mapping is separately included.
- `GET .../artifacts`: `{artifacts:[{path,size,type}]}`. `GET .../download?path=...`: authenticated binary response; every path must remain inside the chosen run after symlink resolution.
- `POST .../export {format:'csv|xlsx|fasta|html|zip',filters?:{search,sort,descending,eligible,eligibility:'all|true|false|missing',selected_candidate_id,selected_observation_id,plot_x,plot_y}}`: binary attachment. Every export contains all candidates; XLSX/HTML/ZIP preserve typed exploration state and provenance, including exact `filtered_ids` for unassessed eligibility. UI-only selection/plot keys are never passed as candidate-query arguments. ZIP contains every run artifact and an offline HTML report. HTML seeks `templates/platform_offline.html`, replacing `__RESULT_DATA__`, `__PLOTLY_JS__`, `__3DMOL_JS__`. Result data `{summary,candidates,observations,structures,filters,provenance}`.

No model inference is launched when opening/reading a result. HTTP server shutdown leaves detached jobs running. A restarted server reconciles persisted birth-stamped PIDs; orphaned running jobs become `interrupted` and require explicit resume. Known independent-session descendants are persisted and reaped after cancellation or forced worker death. Installation is serial and does not overlap inference; diagnostics share the configured GPU slot because readiness executes CUDA kernels.

Candidate search matches candidate IDs, sequences, exclusion reasons and error content. Internal JSON property names are excluded: searching `WT` does not match every row merely because mature candidates contain a `wt_aa` field. Browser, API, export provenance and offline search use the same fields; exports still preserve the complete candidate set.

Hotspot postfilter: candidate queries and export filters accept `hotspot_min` (null/off or a finite fraction 0–1) and `eligibility_basis` (`recorded`, the backward-compatible API default, or `quality`, the browser default). With a minimum enabled, missing/nonfinite contact rates and failed residue mappings are excluded. `quality_eligible` removes only an archived hotspot-only exclusion with all other required pass flags, CDR evaluation and recorded development threshold verified; incomplete evidence yields null. `final_eligible`, exclusion reasons, selection and source files remain unchanged. Summaries add `quality_eligible_total` while retaining `eligible_total` as the recorded count. Specificity campaigns retain their recorded policy. Browser and offline HTML preserve the same filter values; exports retain all candidates and save matching IDs separately. New general runs use `rank.require_hotspot=false`; explicit true retains the legacy hard gate.

The API queue coordinates configured physical GPU indices across every workspace for the same Unix user with `~/.cache/nanocofold/gpu-<gpu>.lock`. A detached worker inherits the locked descriptor, so server shutdown/restart cannot release a live worker's slot. Busy jobs remain queued with `phase:waiting_for_gpu`. The lock inode is preserved; leading-zero GPU numbers are canonicalized. Direct manual CLI runs remain outside this API queue coordination.

## Structure exploration

Input review and FASTA creation accept `target_region:{start?:int,end?:int}` and `hotspot_exclusions:[{start:int,end:int,label?:str}]`. Bounds are inclusive 1-based positions in the observed standard sequence for coordinate preparation, and the reviewed full sequence for sequence prediction/FASTA. Empty region means the whole target. The crop changes the target construct; exclusions only mask automatic hotspot recommendations. Original references and numbering stay preserved. Whole-target masks, invalid bounds, and manual hotspots outside the crop/in an exclusion fail review. Frozen `input_review.target_region/hotspot_excluded_positions` and mapping `reference_chain,reference_position,source_full_position,hotspot_excluded` record these decisions. FASTA now also fixes original-sequence hash, applied sequence, provider and hotspot conditions in `input_review`.

Job `eta` is a nullable measured estimate with `scope:'complex_prediction',status,stage,engine,total,completed,sample_count,seconds_per_candidate,remaining_seconds,lower_seconds,upper_seconds,estimated_finish,basis,includes_postprocessing:false,note,range_kind:'heuristic_budget_range_not_confidence_interval'`. Unavailable fields are null. Stage-local `prediction_plan.json` plus atomically updated `prediction_progress.json` identify the actual selected pool and cache-validated completions for the current attempt. Duration samples come from successful tool footers, including Chai retries. Median of the last 10 samples estimates the current stage only; preparations, generation, postprocessing and queue time are excluded. Mock never supplies real estimates; terminal cancellation/failure hides stale estimates.

Structure responses add `chains[].residues[].ca/atom_ids`, minimum-distance clash `atom_a,atom_b,cutoff,distance_below_cutoff`, `cdr:{ranges:{CDR1|CDR2|CDR3:{start,end,labels}},source,note}`, `cysteines:{residues,missing_sg,pairs:[{a,b,distance,atom_a:'SG',atom_b:'SG'}],note}`, `reference:{available,content?,format?,matched_ca?,rmsd?,reference_sha256?,method?:'target_CA_rigid_fit',note?,reason?}`, and `target_region`. Distances are saved-coordinate geometry, not bonds or a reclassification of eligibility. CDR numbering must match the candidate/structure sequence; explicit saved user ranges are labeled separately. Native reference fitting requires exact unambiguous residue correspondence and at least 3 noncollinear Cα pairs. References are run-contained hash-verified copies or the exact hash-verified snapshot of the same workspace/platform job; YAML paths alone do not authorize external reads. Missing/corrupt references report unavailable without modifying old results.

Export exploration additionally preserves `result_layers:{target|binder|context|hotspots|contacts|clashes|cdr|reference:bool}`, `viewer_display:{sequence:bool,residues:bool}` (both default false), `selected_residues:[label]` and `distance_selection:{a:label,b:label}`. These affect display only. Offline HTML embeds local MolStar JS/CSS and the same result adapter as the browser. No result inference or rescoring occurs in viewer endpoints.


## Browser workflow additions

The environment check is separate from input review: `POST /api/environment/check` queues diagnostics for the selected mode/settings; `POST /api/environment/status` reads the matching saved check without launching work. `GET /api/starter-presets` and `GET /api/capabilities` describe available purpose settings and installed capabilities, not successful prediction evidence. Draft GET/POST stores browser input fields; file handles and sequence-transmission consent still require explicit review.

Batch review stores validated per-item inputs and a review hash without inference. Submit accepts that exact review hash, selected item IDs, and an optional per-item time budget; stable submission links deduplicate retries. Cancel reaches already registered children after concurrent submission finishes. Batch row checkpoints are bounded; a lost checkpoint is recovered from durable child submission links without creating new jobs during GET.

`GET /api/result-catalog` pages saved result records. Result retention GET previews a policy and POST applies a confirmed policy; it does not rerun prediction or recompute old eligibility. `GET /api/results/{identifier}/observations` returns recorded model observations. All these endpoints use the same token, loopback, path containment and request-size boundaries described above.

## Complete route inventory

This inventory uses the route variable names in app.py. Detailed payload rules above still apply; an identifier is not an arbitrary filesystem path.

| Method | Route | Handler |
|---|---|---|
| POST | `/api/inputs/complex` | `complex_input` |
| GET | `/api/starter-presets` | `starter_presets` |
| GET | `/api/capabilities` | `capabilities` |
| GET | `/api/draft` | `draft_read` |
| POST | `/api/draft` | `draft_save` |
| GET | `/api/batches` | `batch_list` |
| POST | `/api/batches/review` | `batch_review` |
| GET | `/api/batches/{identifier}` | `batch_get` |
| POST | `/api/batches/{identifier}/submit` | `batch_submit` |
| POST | `/api/batches/{identifier}/cancel` | `batch_cancel` |
| GET | `/api/session` | `session` |
| GET | `/api/inputs` | `input_list` |
| GET | `/api/tutorials/{mode}` | `public_tutorial` |
| POST | `/api/configs/parse` | `parse_config` |
| POST | `/api/inputs` | `input_create` |
| POST | `/api/inputs/load` | `input_load` |
| POST | `/api/inputs/analyze` | `input_analyze` |
| POST | `/api/inputs/review` | `input_review` |
| GET | `/api/inputs/{identifier}/structure` | `input_structure` |
| GET | `/api/inputs/{identifier}` | `input_get` |
| GET | `/api/jobs` | `job_list` |
| POST | `/api/jobs` | `job_create` |
| GET | `/api/jobs/{identifier}` | `job_get` |
| GET | `/api/jobs/{identifier}/logs` | `job_logs` |
| POST | `/api/jobs/{identifier}/cancel` | `job_cancel` |
| POST | `/api/jobs/{identifier}/resume` | `job_resume` |
| POST | `/api/diagnostics` | `diagnostics` |
| POST | `/api/environment/check` | `environment_check` |
| POST | `/api/environment/status` | `environment_status` |
| GET | `/api/diagnostics` | `diagnostic_list` |
| POST | `/api/setup` | `setup` |
| GET | `/api/results` | `results_list` |
| GET | `/api/result-catalog` | `result_catalog` |
| POST | `/api/results/open` | `results_open` |
| GET | `/api/results/{identifier}` | `result_summary` |
| GET | `/api/results/{identifier}/retention` | `retention_plan` |
| POST | `/api/results/{identifier}/retention` | `retention_apply` |
| GET | `/api/results/{identifier}/candidates` | `candidates` |
| GET | `/api/results/{identifier}/observations` | `observations` |
| GET | `/api/results/{identifier}/structure` | `structure` |
| GET | `/api/results/{identifier}/artifacts` | `artifacts` |
| GET | `/api/results/{identifier}/download` | `download` |
| POST | `/api/results/{identifier}/export` | `export` |
