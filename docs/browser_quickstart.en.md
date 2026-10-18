# Your first analysis in the browser

**English** · [한국어](browser_quickstart.md)

Check the environment once, then prepare and run your analysis. Follow the [README installation steps](../README.md#1-prepare-and-install) first. If your server is already installed, start at [Open the browser](../README.md#2-open-the-browser). Use the same source folder and workspace as the installation.

Start NanoCoFold as described in the README, then open the URL printed after `NanoCoFold:` (the documented port is `18765`). Start the server before opening the link. `127.0.0.1` points to your browser computer. If another app appears, keep it running and follow the README port/SSH instructions. For a remote server, keep both the server terminal and your SSH tunnel open. Select **Language → English** to match the screenshots. If **Preparing the interface.** appears, wait for the purpose buttons. If preparation fails, read the error and click **Reload interface**.

**Already looking at “diagnostics · Complete”?** Click **Prepare analysis**, then follow the **Purpose → Input → Settings → Review → Analysis** rows below. The environment check confirms installation readiness. If you already ran an analysis, use **Jobs & logs → select that analysis → Open results**; saved runs are also available under **Explore results → Select run**.

## Follow the workflow

| Stage | Action | What it means |
|---|---|---|
| Environment · once | Check environment once → select analysis → Check environment → Prepare analysis | Confirms installation readiness |
| Purpose / example | After Prepare analysis, load the public example directly. If purpose choices are shown, first choose Predict protein complexes. | Public barnase–barstar input is filled in |
| Input | Check full sequences, chain order and copies → Review input & continue | Required input is valid |
| Settings | Check engines, planned models and MSA → Review settings & continue | Public MSA needs consent for these sequences |
| Review | Review input → read the applied input and settings | Reuse a matching environment receipt |
| Analysis | Run analysis once → Jobs & logs | Prediction is submitted; inspect the analysis job |
| Results | Open results → select a candidate | Read status, structure, PAE and metrics together |

Input review checks your sequences and settings. **Run analysis** submits the calculation. The saved environment check (`diagnostics`) is reused for the same installation, GPU and engines. After changing inputs, review them and confirm MSA transmission consent again.

## Follow the screenshots

**0. Check the environment once.** If **Environment ready** is already shown, click **Prepare analysis** without checking again. Otherwise, click **Check environment once** → choose the analysis → **Check environment**. Stay on this page until it passes, then click **Prepare analysis**. The check is reused for the same installation, GPU and engines. Adding engines or changing the installation requires another check.

![Separate environment check, before it has been run](images/quickstart-rc9/en/environment.jpg)

**1. Confirm the purpose.** After **Prepare analysis**, **Prepare input** opens with the selected purpose already applied. Continue to step 2. If you started from the purpose-choice screen instead, click **Predict protein complexes · Enter input** as shown below.

![Choose protein complex prediction](images/quickstart-rc9/en/01-purpose.jpg)

**2. Load the public input.** Click **Load public example for this purpose**. The full sequences are filled in automatically. Check **Barnase: 110 aa**, **Barstar: 89 aa**, and **one copy of each**, then click **Review input & continue**.

![Check both full sequences, chain order and copy counts](images/quickstart-rc9/en/02-input.jpg)

**3. Check the settings.** Keep the small starter preset, **all four engines**, and **four planned models**. This preset uses seed **42**, **one model per engine**, **3 recycles**, and **200 diffusion steps**. Leave advanced settings unchanged for your first run. To use public MSA, give consent to send the current public sequences and click **Review settings & continue**. The screenshot shows consent **before checking the box**.

![Four engines, planned model count and MSA consent](images/quickstart-rc9/en/03-settings.jpg)

**4. Review before running.** Click **Review input**, read the applied inputs and settings, then click **Run analysis** once. A saved environment check is reused without leaving this page. If it is missing for these engines, click **Go to environment check**, complete the separate check, and click **Prepare analysis** to return to this input. The screenshot illustrates this missing-check state; no prediction was submitted. Input changes require another input review and fresh transmission consent.

![Review inputs and follow the explicit next action](images/quickstart-rc9/en/04-review.jpg)

**5. Read the result.** Follow **Jobs & logs → completion → Open results**. Check evaluated, failed and unevaluated counts first, then select a model. The screenshot shows four evaluated models from an earlier four-engine GPU run. **Not applicable** for VHH eligibility is normal for a general protein complex.

![Planned and evaluated model counts in an actual GPU result](images/quickstart-rc9/en/05-results.jpg)

**6. Explore and share.** Use **Fit entire structure**, chain selection and PAE cell selection. Read each model's metrics, structure and PAE together. Save the entire **Shareable report ZIP** for collaborators. Recipients extract the whole ZIP and open **report.html** without installing NanoCoFold or using a GPU.

![The selected actual model and its metrics](images/quickstart-rc9/en/06-structure-pae.jpg)

![The same model's PAE](images/quickstart-rc9/en/07-pae.jpg)

All screenshots show the **0.4.8rc9** interface from commit `26925c3`, captured on **18 October 2026**. The result screenshots use the current offline report viewer with the preserved rc5 GPU result; no prediction or scoring was rerun. Both languages show the same Chai-1 model and its 199 × 199 PAE matrix. See the [capture record and file hashes](images/quickstart-rc9/README.md).

## Settings by purpose

Use **Load public example for this purpose → review input → run settings → review** for each purpose. The examples use public IL-6 inputs. Seed is **42** and each prediction engine generates **one model** per evaluated sequence. Actual generation, selection and successful prediction counts can differ from the maximum plan.

| Purpose | Required input | Small starter settings |
|---|---|---|
| Predict protein complexes | Full sequences of the proteins to predict together | Four engines → four planned models |
| Design new nanobodies | Full target sequence or structure | RFantibody 1 + BoltzGen 1, evaluated by four engines → up to eight models |
| Compare existing nanobodies | Target + full VHH sequence for each candidate | Every input VHH × four engines |
| Evaluate nanobody variants | Target + full WT VHH sequence | Prepare up to three variants; evaluate up to two plus WT with four engines → up to twelve models |

The small starter preset disables design generator prefilter rejection and the mature ESM score threshold. It retains ESM scoring, variant limits and final quality criteria. AntiFold is off in the variant example. Enter the full VHH sequence, including its CDRs and framework.

These small counts apply to the loaded public example. The general **Researcher exploration** preset uses `design.num_designs=1000`, `screen.tier2_n=500` and `mature.prefilter.top_n=500`; automatic target truncation is off. These values set generation and evaluation limits; results show the actual successful model counts. Check the planned count before using your own input. In the local browser the time-budget field is initially blank (no limit); enter a limit if needed. Read the applied settings before submission.

<details>
<summary>Design new nanobodies: settings screenshot</summary>

![Two design generators and four evaluation engines](images/quickstart-rc9/en/settings-design.jpg)

</details>

<details>
<summary>Compare existing nanobodies: settings screenshot</summary>

![All input VHH candidates and four evaluation engines](images/quickstart-rc9/en/settings-screen.jpg)

</details>

<details>
<summary>Evaluate nanobody variants: settings screenshot</summary>

![Variant cap, WT control and maximum planned models](images/quickstart-rc9/en/settings-mature.jpg)

</details>

These web starter settings can differ from the preserved CLI conditions in `examples/tutorial/*_batch.json`. In particular, `library.size` does not cap DMS variants in the existing mature JSON. Use the web starter preset for the small plan shown here. The CLI DMS cap is `mature.library.max_variants`.

## Your own inputs and saved results

Choose either a structure file or a PDB ID; use **Change input method** to switch. Paste VHH/WT FASTA or import a file once. The populated FASTA is the actual input. Use **Load another file** or **Restore previous FASTA** to replace or undo an import. After restoring a draft, reselect structure files and reconfirm consent and input review. A matching installation check is reused. If you edit FASTA while a file is loading, the current input is retained and a notice is shown.

In results, select a candidate to inspect its structure. Use **Expand detailed metric columns** when needed. **Save selected display coordinates** saves the displayed coordinates; **Download original engine coordinates** is available only when the original is preserved and matches its recorded hash. Preparation and preselection have separate records from prediction failures. Unevaluated values remain missing. Check low-confidence predictions separately from failed calculations. The full original ZIP includes server paths/logs and is intended for personal archiving; share the report ZIP with collaborators.

## Read the report in six steps

| Section | What to check |
|---|---|
| 01 Inputs & settings | Are these the sequences and conditions you intended? |
| 02 Candidate & model counts | Separate completed, failed and unevaluated models. General complexes have no VHH eligibility filter. |
| 03 Compare candidates | Select a candidate/model and its prediction engine; read its recorded scores. |
| 04 Excluded / unevaluated | If selection is zero, check whether candidates were excluded by criteria or lacked the data needed for assessment. Use this information to plan the next analysis or experiment. |
| 05 Structure & PAE | Inspect the selected model's structure and residue-placement uncertainty together. |
| 06 Provenance & artifacts | Check recorded conditions and save the appropriate report/data files. |

Use section links and browser **Back / Forward** to return to earlier sections or views. Analysis continues during navigation. Keep the same selected candidate when comparing languages.

The steps above use the locally installed application's labels. An administrator-hosted Site can instead show **Jobs**, **Reports and provenance**, **Offline HTML**, and **Resume verified stages**. Use **Offline HTML** for the current-language report presentation. If resume is disabled, read the reason and contact the administrator. Older compute agents may need an update.

## Saved files and offline reports

Use the workspace shown in the browser. Each analysis is saved under `<workspace>/runs/<job_id>/`; the job identifier is in **Jobs & logs**. When report publication succeeds, start with `report.html` in that run folder. If it links to a `report_explorer-<hash>.html` file, keep that file beside the entry page. Older reports can use `report_explorer.html`. Keep the whole report folder or ZIP together; do not rename or delete a linked explorer.

The detailed worker log is `<workspace>/.nanocofold/jobs/<job_id>/worker.log`, beside the submitted `config.yaml` snapshot. The run folder preserves `run_config.yaml`, `run_state.json` and available engine outputs. An interrupted run can contain partial results. Check job status and completed model counts when opening its report.

Use **Language → English / 한국어** in the browser and in newly exported interactive reports. The two languages navigate the same candidates and recorded values. IDs, sequences, coordinates, numeric data, source settings and original execution logs stay as recorded. Older HTML and ZIP files retain their original interface. For the current bilingual interface, export a new report from the application and keep the older archive.

## Follow jobs, cancel and resume

Analysis continues when you change tabs, use Back, or switch language. Return to the same job to check its status and log before submitting another analysis.

In **Jobs & logs**, select the analysis job and open **Detailed execution log**. Environment-check and installation jobs contain setup records. If the browser disconnects, reopen the same workspace and select the existing job. Keep the server and compute host running.

To stop analysis, click **Cancel job**, check the target in the confirmation dialog, and confirm. Wait for **Cancelling → Cancelled** while resources are released. **Keep running** or Esc returns to the job. Saved results remain available.

For an interrupted, failed, or cancelled job, resolve the logged cause and use **Verify and resume** when available. NanoCoFold checks saved inputs, settings and previous process ownership before restarting. After **Time budget exhausted**, enter additional minutes when prompted. If resume is unavailable, read the displayed reason and log. Review partial results with their job status and completed model count.

## If you get stuck

- **Continue is unavailable:** read the input error and check full sequences, chain order and required files.
- **Run analysis is unavailable:** review the input, then use Go to environment check if a matching receipt is missing. Complete that separate check and click Prepare analysis. Install missing environments as directed.
- **A request is slow or the connection drops:** check the jobs list before submitting again.
- **Only part of a structure is visible:** fit the entire structure and check chain visibility.
- **MOCK or CPU demo is shown:** use these synthetic examples to practice the workflow.

Failed input-review files are preserved under `inputs/failed_reviews`. Review retention needs before cleaning them. Compare candidates within the same engine and conditions. Use the selected candidates for follow-up binding and KD experiments. See [result interpretation](RESULTS.md) (technical reference in Korean).
