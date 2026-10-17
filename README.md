# NanoCoFold

**English** · [한국어](README.ko.md)

**Build: 0.4.8rc8**

Predict protein complexes, design new nanobodies, and compare existing candidates or variants. Explore candidate tables, 3D structures, PAE, and shareable HTML reports.

**First time? Use the public example below.** It fills in the sequences and small-run settings for you. Leave advanced settings unchanged.

| What do you want to do? | Start here |
|---|---|
| Install on a new Ubuntu GPU computer | [1. Prepare and install](#1-prepare-and-install) |
| Use a computer/server where NanoCoFold is already installed | [2. Open the browser](#2-open-the-browser), then [3. Try the example](#3-try-the-first-public-example) |
| Read a report someone sent you | Extract the **whole Shareable report ZIP** and double-click **report.html**. No installation or GPU is needed. |

**Environment check → prepare input → review settings → run once → Open results.**

**Only seeing “diagnostics · Complete”?** That is an installation check. It does not predict a protein structure. Click **Prepare analysis**, then follow [section 3](#3-try-the-first-public-example).

[English screenshots, step by step](docs/browser_quickstart.en.md#follow-the-screenshots) · [Find and share results](#5-read-and-share-results)

## 1. Prepare and install

The compute host needs **Ubuntu x86_64**, glibc 2.34 or later, an NVIDIA GPU, and driver 580 or later. `nvidia-smi` must show the GPU correctly. For the four-engine example below, prepare at least 16 GB GPU memory, 32 GB RAM, and 150 GiB of free SSD space. Longer proteins and larger libraries may need more resources. Internet access and `sudo` are also required. Ask your server administrator to check these requirements if needed. The GPU model name alone does not establish enough memory for every engine.

You do not need to install Python packages, the CUDA Toolkit, Conda, or Docker separately. The installer prepares isolated Python environments, engines, and models. It does not change the NVIDIA driver.

1. On this GitHub page, click **Code → Download ZIP**, then **extract the entire archive**. If you received a source ZIP, extract it instead. If you cloned the repository, use that checkout.
2. Open **`NanoCoFold_public`** after a Git clone, or **`NanoCoFold_public-main`** after GitHub **Download ZIP**. Tagged or separately packaged source archives may have a different folder name.
3. Open a terminal inside that folder. You should see `README.md`, `install.sh`, and `start.sh`.
4. To prepare all four engines for the first complex example, run these **three commands in order, in the same folder**. Wait for each command to finish with **`[설치 완료]`** (installation complete) before starting the next.

If there is more than one GPU, read **Multiple GPUs** below before running these commands.

```bash
bash install.sh --profile complex
bash install.sh --profile colabfold
bash install.sh --profile opendde
```

<details>
<summary>Multiple GPUs, extra purposes and installation troubleshooting</summary>

`complex` prepares Boltz-2 and Chai-1; the other commands prepare ColabFold and OpenDDE. A single GPU is selected automatically. If the host has multiple GPUs, find the full UUID with `nvidia-smi -L` and use **the same UUID in all three commands**.

```bash
bash install.sh --profile complex --gpu GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
bash install.sh --profile colabfold --gpu GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
bash install.sh --profile opendde --gpu GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

The first installation downloads several engines and large model files and may take time. Follow the stage and download logs until **`[설치 완료]`** appears. Installation alone does not start analysis or transmit input sequences. Resume an interrupted download by running the same command again. Existing files, partial downloads, and failure records are preserved; completion requires file hashes and environment checks to pass.

The initial uv bootstrap has a waiting deadline. If it times out or you interrupt it with Ctrl-C, follow the retry instructions and rerun the same command in the same folder. This deadline is not a limit on the entire engine and model installation.

To prepare environments for new nanobody design, existing candidate comparison, and variant evaluation, optionally run the following. `bash install.sh` without options also uses `all`. This is not required for the first complex example.

```bash
bash install.sh --profile all
```

On a multi-GPU host, add the same `--gpu` UUID to this optional installation. The [validation record](docs/VALIDATION.md) distinguishes installation, software checks, and actual prediction evidence.

If an error occurs, stop before continuing. `Driver/library version mismatch` means the NVIDIA driver and libraries do not match. Ask the administrator whether a reboot is needed, and retry only after `nvidia-smi` works. Reinstalling Python does not fix this error. See [installation troubleshooting](docs/installation.md).

</details>

## 2. Open the browser

On the **Ubuntu computer where you installed NanoCoFold**, run this in the **same source folder**. For a remote server, use the two-terminal instructions below:

```bash
bash start.sh --port 18765
```

**Start NanoCoFold first, then open the URL printed after `NanoCoFold:` in that terminal.** With the command above, it is [http://127.0.0.1:18765](http://127.0.0.1:18765). Check that the page says **NanoCoFold**. A README link does not start the program: `127.0.0.1` refers to the computer running your browser. This terminal runs the web server; keep it open.

**SkinPipe or another app opens?** Keep that app running. Use the NanoCoFold command above on a free port; if 18765 is occupied, run the following and open the new URL it prints:

```bash
bash start.sh --port 0
```

For a remote server, open the SSH instructions below before using the link. `--port 0` selects a free **server** port; use the corresponding forwarding command printed by NanoCoFold.

If **Preparing the interface.** appears, wait for the purpose buttons. If preparation fails, read the error and click **Reload interface**. The interface starts in English. Use **Language → 한국어** in the header to switch; the browser remembers your choice. Switching languages preserves inputs and analysis state. Original identifiers, sequences, numerical results, logs, and configuration records are kept as recorded.

<details>
<summary>Using a remote Ubuntu server from your own PC</summary>

**Terminal 1 · On the installed Ubuntu server:**

```bash
bash start.sh --no-browser --port 18765
```

**Terminal 2 · On your own PC:** replace `USER@SERVER` with your server account and address.

```bash
ssh -o ExitOnForwardFailure=yes -L 18765:127.0.0.1:18765 USER@SERVER
```

Keep both terminals open. If the SSH command reports an occupied local port, it has not connected NanoCoFold; do not open that occupied address. For example, forward local 18766 to server 18765 and open `http://127.0.0.1:18766`. If the server printed a different port, use that server port on the right of `-L`. Then open the forwarded address in your PC browser. Inputs and results are viewed on the PC; computation uses the GPU of the Ubuntu server where you installed NanoCoFold. The public installer normally stores the workspace in `.nanocofold/workspace` under the source folder; `--workspace` can choose another location. `start.sh` reuses the saved installation location.

</details>

<details>
<summary>Update an existing installation (after jobs finish)</summary>

### Update an existing installation

When no job is queued or running, stop the web server with **Ctrl+C** in its terminal. In the original source folder, run:

```bash
git pull --ff-only
bash update.sh
bash start.sh --port 18765
```

This updates the installed application while preserving the saved workspace, GPU selection, models, inputs and results. It does not run predictions or reinstall GPU engines. Reload the browser afterward. `git pull` alone does not update the installed application. For a source ZIP, use its complete updated contents in the original source folder, then run the last two commands. Existing exported HTML reports keep their captured appearance; export a new report to use the updated layout.

If the update fails or is interrupted, run `bash update.sh` again. Starting the application is blocked until the update finishes successfully. An open web server or active job also blocks the update; close the server after its jobs finish, then retry.

If the local port is occupied, this example uses another local port. Run it and open **http://127.0.0.1:18766**. If NanoCoFold printed a different server port, replace the last `18765` with that number.

```bash
ssh -o ExitOnForwardFailure=yes -L 18766:127.0.0.1:18765 USER@SERVER
```

</details>

## 3. Try the first public example

Start with **Predict protein complexes**. Follow one row at a time; each row tells you when to continue.

| Step | What to click or check | Ready to continue when… |
|---|---|---|
| A · Environment, once | **Check environment once** → select **Predict protein complexes** → **Check environment** | **Environment ready** appears on this page. Click **Prepare analysis**. |
| B · Example | **Load public example for this purpose** | Barnase **110 aa** and Barstar **89 aa** appear, with **one copy of each**. |
| C · Input | Check both sequences, chain order and copies → **Review input & continue** | **Run settings** opens. |
| D · Settings | **Small starter preset**, **four engines**, **four planned models**. For public MSA, consent to send these public sequences to **api.colabfold.com** → **Review settings & continue** | **Review & run** opens. |
| E · Final review | **Review input** → read the applied input and settings | **Run analysis** is enabled. Reviewing does not start computation. |
| F · Analysis | Click **Run analysis** **once** → **Jobs & logs** | Select your **analysis** job and wait for its final status. An environment-check job is a different job. |
| G · Result | On the completed analysis, click **Open results** | A candidate/model table appears. Select a row to see its scores, 3D structure and PAE. |

**After Prepare analysis, the purpose selected in the environment check is already applied.** If **Prepare input** is open, load the example directly; you do not need to return to the purpose screen. If you start from the purpose choices instead, choose **Predict protein complexes · Enter input**. If **Environment ready** is already shown, reuse it instead of checking again.

If **Run analysis** is disabled because the environment check is missing: **Go to environment check** → complete it → **Prepare analysis**. Your input is kept. Reuse the check while the installation, GPU and selected engine configuration stay the same.

The example uses seed **42** and **one model per engine**. Four planned models do not guarantee four successful models; check completed, failed and unevaluated counts. Loading the example does not submit analysis or grant sequence-transmission consent. Review changed inputs and confirm their consent again.

<details>
<summary>Step-by-step settings and English screenshots</summary>

For the first example, check these values. **Four planned models do not guarantee four successful predictions**; inspect the actual counts afterward. The screenshots are saved examples; select **Language → English** to match their labels. Exact capture versions are in the [capture record](docs/images/quickstart-flow/README.md).

| Step | Key settings to check | Screenshot |
|---|---|---|
| Environment · once | Select analysis → Check environment → Prepare analysis | [0](docs/images/quickstart-flow/en/environment.jpg) |
| Purpose | Predict protein complexes | [1](docs/images/quickstart-flow/en/01-purpose.jpg) |
| Input | Barnase 110 aa + Barstar 89 aa, one copy of each | [2](docs/images/quickstart-flow/en/02-input.jpg) |
| Settings | Small starter preset, four engines, seed 42, one model per engine → four planned models | [3](docs/images/quickstart-flow/en/03-settings.jpg) |
| Review & run | Current-sequence consent → Review input → reuse environment check → Run analysis once | [4 · a check is needed](docs/images/quickstart-flow/en/04-review.jpg) |
| Results | Completed, failed and unevaluated counts → select a model → structure and PAE | [5](docs/images/quickstart-rc5/en/05-results.jpg) · [structure](docs/images/quickstart-rc5/en/06-structure-pae.jpg) · [PAE](docs/images/quickstart-rc5/en/07-pae.jpg) |

<details>
<summary>Screenshots 1–2: choose a purpose and check the public input</summary>

![Step 1: choose protein complex prediction](docs/images/quickstart-flow/en/01-purpose.jpg)

![Step 2: check both full sequences and copy counts](docs/images/quickstart-flow/en/02-input.jpg)

</details>

<details>
<summary>Screenshots 3–4: check settings and find the review button</summary>

Leave advanced settings unchanged. Screenshot 3 shows the transmission consent box unchecked. Confirm consent for the current public example before continuing. Screenshot 4 shows a valid input with **no environment check saved**. Complete the separate check once; the run control is then enabled. These captures did not submit predictions.

![Step 3: four engines and public MSA settings](docs/images/quickstart-flow/en/03-settings.jpg)

![Step 4: review inputs and see the next action](docs/images/quickstart-flow/en/04-review.jpg)

</details>

<details>
<summary>Screenshot 5: inspect an actual saved GPU result</summary>

This is the previously verified rc5 barnase–barstar run with four actual engine models. General complexes do not use VHH eligibility. Capturing the input and settings screens did not submit another calculation.

![Step 5: compare planned and evaluated model counts](docs/images/quickstart-rc5/en/05-results.jpg)

</details>

</details>

[Step-by-step buttons and result interpretation](docs/browser_quickstart.en.md) · [Example sequence sources and JSON](examples/tutorial/README.md) · [Analysis workflow and selection logic](docs/ANALYSIS_WORKFLOW.md)

## 4. Use your own input

| Purpose | Required input | Results to inspect |
|---|---|---|
| Predict protein complexes (`complex`) | Full sequences of the proteins to predict together | Chain-pair metrics, complex structure, PAE |
| Design new nanobodies (`design`) | Full target sequence or structure | New VHH sequences, engine-specific structures, candidate table |
| Compare existing nanobodies (`screen`) | Target + full VHH sequence for each candidate | Predictions and scores per candidate and engine |
| Evaluate nanobody variants (`mature`) | Target + full original VHH sequence | WT, CDR substitutions, comparisons with WT from the same engine |

[Settings screenshots for design, screening, and variant evaluation](docs/browser_quickstart.en.md#settings-by-purpose) are included in the same guide.

Use amino-acid sequences. FASTA is a text format with a `>name` line followed by the sequence. A VHH is the full nanobody variable domain; a CDR3 fragment alone is insufficient. DNA/RNA and FASTQ are unsupported. Give entries distinct names that do not start with `=`, `+`, `-`, or `@`. Prepared substitution or insertion variants can be compared in `screen` as **full VHH sequences**.

Prepare an input using one method. Selecting a structure file disables the PDB ID alternative; entering a PDB ID disables the file alternative. Use **Change input method** to switch. Paste VHH / WT FASTA or import a file once. The populated FASTA is the actual input; no duplicate file is required. Use **Load another file** to replace it or **Restore previous FASTA** to undo the import. After restoring a draft, reselect structure files and reconfirm transmission consent and input review. A matching installation check is reused.

For structure files, review chains, residue mappings, missing residues, and the preparation method. Public MSA transmits sequences externally. For private research inputs, check your institution's policy and choose a suitable MSA method. Consent for a public example does not carry over to your own sequences.

<details>
<summary>Optional: practice variant evaluation in the terminal without a GPU</summary>

In the NanoCoFold environment prepared by the [installation steps above](#1-prepare-and-install), activate it with `source .nanocofold/workspace/.venv-dev/bin/activate`, open this source folder in the terminal and copy the command below. It uses the included public FASTAs, explicitly samples at most **3 DMS variants**, and evaluates at most **2 variant candidates** after prefiltering. `--mock` generates synthetic structures and synthetic CDR annotations for practicing the workflow. These are not research predictions or orderable candidates.

If you installed with a custom `--workspace`, use that workspace path in place of `.nanocofold/workspace` in the activation command. The saved path is in `.nanocofold/install-location.json`.

```bash
nanocofold run --mode mature --mock \
  --wt-fasta examples/tutorial/wt_vhh.fasta \
  --set target.fasta=examples/tutorial/target.fasta \
  --set mature.cdr.source=config \
  --set mature.library.max_variants=3 \
  --set mature.prefilter.top_n=2 \
  --set run.name=mature_mock_small
```

The terminal prints the actual candidate count before prediction, then **candidate n/total** while calculating scores. When it finishes, open the printed **report.html** path. Keep the linked report files together when moving it.

`--mock` alone keeps your chosen library and candidate limits; it does not automatically shrink a research run. The research default can send up to **200** mature candidates to evaluation. Use the two explicit limits above for a short practice run. For research, remove `--mock` and use reviewed inputs and validated CDR annotations; do not reuse synthetic CDR positions.

</details>

## 5. Read and share results

**To open your result:** **Jobs & logs → select your analysis job → Open results**. On a small screen, scroll below the job details to find its actions. You can also use **Explore results → Select run** to reopen a saved analysis in the same workspace.

| What you see | What to do next |
|---|---|
| **diagnostics / Environment check · Complete** | Click **Prepare analysis** and follow [section 3](#3-try-the-first-public-example). No prediction has run yet. |
| Analysis **Complete** with **Open results** | Open it, check successful/failed/unevaluated counts, then select a model. |
| Failed, cancelled, interrupted or budget exhausted | Read **Detailed execution log** and [section 6](#6-follow-jobs-cancel-and-resume). Any saved partial result is not proof of completion. |
| No **Open results** button or no saved run listed | Confirm the same workspace and the selected **analysis** job. Read its status and log; do not submit it again just to find results. |

Then read in this order: **status and counts → candidate/model row → scores, structure and PAE → download**. X/Y selectors compare metrics for the selected view. [Chart example](docs/images/quickstart-flow/en/charts.jpg) · [Report navigation](docs/images/quickstart-flow/en/report-nav.jpg).

- **Fit entire structure** shows the whole complex. **Select target / Select nanobody**, or **Select reference chain / Select partner**, highlights and focuses the relevant chain. **Clear selection** removes highlighting.
- Clicking a PAE cell selects and focuses two residues and moves to the structure viewer. PAE describes uncertainty in predicted relative residue placement; it is not a binding measurement.
- Save CSV / XLSX tables, FASTA, coordinates, and the **report ZIP**. For Excel, use the exported CSV (UTF-8 BOM) or XLSX; run-folder CSVs are UTF-8 machine records. **Save selected display coordinates** saves the coordinates shown in the viewer. **Download original engine coordinates** is available when the original file is preserved and matches its recorded hash. Check the provenance notice for the relationship between original and display coordinates.

The **Full original ZIP · For local archiving** includes server paths and execution logs and is for personal recovery / archiving. Send collaborators the entire **Shareable report ZIP**. Recipients extract the whole ZIP and open **`report.html`**. Saved results can be read on macOS, Windows, or other PCs without a GPU or program installation. Newly generated interactive reports also include the English / Korean selector.

<details>
<summary>File locations, older reports and PyMOL (optional)</summary>

### Find the saved files

Use the workspace shown in the browser. Each analysis is saved under `<workspace>/runs/<job_id>/`; the job identifier is in **Jobs & logs**. When report publication succeeds, start with `report.html` in that run folder. If it links to a `report_explorer-<hash>.html` file, keep that file beside the entry page. Older reports can use `report_explorer.html`. Keep the whole report folder or ZIP together; do not rename or delete a linked explorer.

The detailed worker log is `<workspace>/.nanocofold/jobs/<job_id>/worker.log`, beside the submitted `config.yaml` snapshot. The run folder preserves `run_config.yaml`, `run_state.json` and available engine outputs. An interrupted run can contain partial results; an existing HTML file alone does not prove that the current analysis completed. Read its status and counts.

Use **Language → English / 한국어** in the browser and in newly exported interactive reports. The two languages navigate the same candidates and recorded values. IDs, sequences, coordinates, numeric data, source settings and original execution logs stay as recorded. Existing HTML snapshots keep their original renderer; export a new report to use updated language support.

If PyMOL materials are provided, extract the **whole folder** and open `.pml` through PyMOL **File → Open**. Keep the relative folder layout of scripts and referenced coordinates. PyMOL materials are separate from the general report ZIP. See [viewing in PyMOL](docs/RESULTS.md#pymol에서-보기).

</details>

Prediction completion, selection eligibility, and experimentally confirmed binding are distinct. Unevaluated is not zero; WT is a control. Scores from different engines are not summed into one ranking. Review ipTM, interface pLDDT, interface PAE, and structures together. Binding, KD, and expression require experimental validation. See [metrics and result interpretation](docs/RESULTS.md).


[Score cohorts, comparison conditions, and statistical interpretation](docs/STATISTICAL_INTERPRETATION.md).

## 6. Follow jobs, cancel and resume

| Situation | What to do |
|---|---|
| Changed tabs, used Back, switched language or closed the browser | This does not cancel the analysis. Reopen the same workspace and inspect the existing job. Keep the server and compute host running. |
| Progress seems stuck or the connection dropped | Check **Detailed execution log** and the connection. The last displayed status can be stale; do not submit a duplicate. |
| Want to stop a job | **Cancel job** → wait for **Cancelling → Cancelled**. A cancellation request alone does not mean cleanup has finished. |
| A failed, interrupted or cancelled job offers **Verify and resume** | Resolve the logged cause, then use that action on the **same job**. Saved inputs/configuration and process ownership are checked before restarting. |
| **Time budget exhausted** | Enter additional minutes when prompted, then **Verify and resume**. |
| Resume is unavailable | Read the stated reason and the log. A hosted workspace may require a compute-agent update; contact its administrator. Do not create a new run just to bypass this check. |

Saved work and results are retained; an interrupted job does not restart automatically. Existing results open without running inference. Partial results must be read as partial.

[Changelog](CHANGELOG.md) · [Validation record](docs/VALIDATION.md) · [External engine and model terms](LICENSE_MANIFEST.md)

No separate public license is granted for the project itself. External libraries, engines, and models are subject to their respective terms. Linked technical documents and original execution logs may be in Korean. See [language support](docs/LANGUAGE_SUPPORT.md).
