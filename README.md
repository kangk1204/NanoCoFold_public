# NanoCoFold

**English** · [한국어](README.ko.md)

**Build: 0.4.8rc9**

Predict protein complexes, design new nanobodies, and compare existing candidates or variants. Explore candidate tables, 3D structures, PAE, and shareable HTML reports.

**Start with the public example below.** It loads the sequences and a small starter preset.

| Your computer | Use NanoCoFold this way |
|---|---|
| Ubuntu x86_64 with NVIDIA GPU | Install and compute here, after checking the requirements below. |
| Windows or macOS | Connect to an installed server in your browser, or open an extracted report ZIP. |
| Windows with WSL | Ask your server administrator to check WSL, GPU, and driver compatibility before installation. |

| What do you want to do? | Start here |
|---|---|
| Install on a new Ubuntu GPU computer | [1. Prepare and install](#1-prepare-and-install) |
| Use a computer/server where NanoCoFold is already installed | [2. Open the browser](#2-open-the-browser), then [3. Try the example](#3-try-the-first-public-example) |
| Read a report someone sent you | Extract the **whole Shareable report ZIP** and double-click **report.html**. No installation or GPU is needed. |

**Environment check → prepare input → review settings → run once → Open results.**

**After the environment check, click Prepare analysis.** `diagnostics · Complete` is the environment-check record. Start the analysis in [section 3](#3-try-the-first-public-example).

[Collaborator guide: workflow, installation, and results](docs/collaborator-guide.en.md)

[English screenshots, step by step](docs/browser_quickstart.en.md#follow-the-screenshots) · [Find and share results](#5-read-and-share-results)

## 1. Prepare and install

Requirements for the four-engine example:

| Item | Requirement |
|---|---|
| Operating system | Ubuntu x86_64, glibc 2.34 or later |
| GPU | NVIDIA GPU, driver 580 or later |
| GPU memory | At least 16 GB |
| System memory | At least 32 GB RAM |
| Storage | At least 150 GiB free SSD space |
| Other | Internet access and `sudo` |

Check the GPU and its memory with `nvidia-smi`. Longer proteins and larger libraries need additional resources.

For GPUs with less than 16 GB of memory, ask your server administrator to select suitable engines and input sizes, then run the environment check for that configuration.

The installer prepares the Python environments, engines, and models automatically. Your server administrator installs the NVIDIA driver beforehand.

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

The first installation downloads engines and model files. Follow the terminal log until installation completes. If interrupted, rerun the same command in the same folder to continue from the saved downloads and installation records.

For installation errors and retry instructions, see the terminal message and [installation troubleshooting](docs/installation.md).

For nanobody design, candidate comparison, and variant evaluation, install all environments with the command below. `bash install.sh` without options also selects `all`.

```bash
bash install.sh --profile all
```

On a multi-GPU host, add the same `--gpu` UUID to this optional installation. The [validation record](docs/VALIDATION.md) distinguishes installation, software checks, and actual prediction evidence.

For `Driver/library version mismatch`, ask the server administrator to check the driver. Retry installation when `nvidia-smi` works. See [installation troubleshooting](docs/installation.md).

</details>

## 2. Open the browser

On the **Ubuntu computer where you installed NanoCoFold**, run this in the **same source folder**. For a remote server, use the two-terminal instructions below:

```bash
bash start.sh --port 18765
```

Open the **URL printed after `NanoCoFold:`** in the terminal. The command above uses [http://127.0.0.1:18765](http://127.0.0.1:18765). Check that the page says **NanoCoFold** and keep the server terminal open while using it. `127.0.0.1` refers to your current PC; use the instructions below for a remote server.

**Server running:** the terminal shows a `NanoCoFold:` address and waits. Use the app in your browser and keep this terminal open.

**If another application uses the same port**, run the command below to select a free port, then open the new URL printed in the terminal.

```bash
bash start.sh --port 0
```

When using `--port 0` on a remote server, set up SSH forwarding with the server port printed in the terminal.

Select an analysis purpose when the interface is ready. For a loading error, click **Reload interface**. The interface opens in English; use **Language → 한국어** to switch. The browser saves your language choice.

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

Keep both terminals open and visit the forwarded address in your PC browser. Enter inputs and view results on the PC; computation runs on the Ubuntu server. If the server uses a different port, update the right-hand port in the SSH command.

The default public workspace is `.nanocofold/workspace` under the source folder. Use `--workspace` to choose another location; `start.sh` reuses the saved location.

If local port 18765 is occupied, use the command below and open **http://127.0.0.1:18766**. Replace the final `18765` if the server uses another port.

```bash
ssh -o ExitOnForwardFailure=yes -L 18766:127.0.0.1:18765 USER@SERVER
```

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

Reload the browser after updating. The workspace, models, inputs, and results are retained. For a source ZIP, save local changes, copy the new source into the original folder, then run `bash update.sh` and the start command. Keep `.nanocofold/` in place. Export a report again to use the updated interface.

If an update is interrupted, rerun `bash update.sh`. Finish all jobs and close the web server before updating.

</details>

<details>
<summary>Check the installed version</summary>

`bash start.sh` uses the installed program in the saved workspace. To check its version from the source folder:

```bash
.nanocofold/workspace/.venv-dev/bin/nanocofold --version
```

For a custom workspace, use the `workspace` path in `.nanocofold/install-location.json`. To update an older installation, finish all jobs, stop the web server, and follow **Update an existing installation** above.

**For source development**, check the checkout version and module path with:

```bash
source .nanocofold/workspace/.venv-dev/bin/activate
python -m nanocofold --version
python -c "import nanocofold; print(nanocofold.__file__)"
```

For a custom workspace, use its saved path when activating the environment. Development launches use the source checkout and the workspace given by `--workspace`. For catalog paths, use `pwd -P` to find the workspace's physical path.

</details>

## 3. Try the first public example

Use the **Predict protein complexes** public example and follow the table in order.

| Step | What to click or check | Ready to continue when… |
|---|---|---|
| A · Environment, once | **Check environment once** → select **Predict protein complexes** → **Check environment** | **Environment ready** appears on this page. Click **Prepare analysis**. |
| B · Example | **Load public example for this purpose** | Barnase **110 aa** and Barstar **89 aa** appear, with **one copy of each**. |
| C · Input | Check both sequences, chain order and copies → **Review input & continue** | **Run settings** opens. |
| D · Settings | **Small starter preset**, **four engines**, **four planned models**. For public MSA, consent to send these public sequences to **api.colabfold.com** → **Review settings & continue** | **Review & run** opens. |
| E · Final review | **Review input** → check the input and settings summary | **Run analysis** is enabled. |
| F · Analysis | Click **Run analysis** once → **Jobs & logs** | Select the submitted analysis job and wait for completion. |
| G · Result | On the completed analysis, click **Open results** | A candidate/model table appears. Select a row to see its scores, 3D structure and PAE. |

**Check that analysis is running:** in **Jobs & logs**, select the job you just submitted. Its **Running** status, current stage, model count, and elapsed time update automatically. A single model can take time, so the stage and count may stay the same for a while. Open **Detailed execution log** for details. When the job is **Complete**, click **Open results**. If you used browser Back, return to **Jobs & logs** and reopen the same job; analysis continues. See [job controls](#6-follow-jobs-cancel-and-resume).

**Prepare analysis** opens **Prepare input** for the selected purpose. Load the public example there. From the purpose-selection screen, choose **Predict protein complexes · Enter input**.

If prompted for an environment check, follow **Go to environment check → Environment ready → Prepare analysis**. The saved check is reused while the installation, GPU, and selected engines remain the same.

The example uses seed **42** and **one model per engine**, for **four planned models**. Check completed, failed, and unevaluated counts in the results. Review the input and MSA consent again after changing sequences.

<details>
<summary>Step-by-step settings and English screenshots</summary>

Use the settings and English screenshots below. Check completed, failed, and unevaluated model counts in the results. Capture versions are listed in the [capture record](docs/images/quickstart-flow/README.md).

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

Use the starter settings for the first run. In screenshot 3, confirm transmission consent for the public example before continuing. Screenshot 4 shows a pending environment check; complete it to enable the run button.

![Step 3: four engines and public MSA settings](docs/images/quickstart-flow/en/03-settings.jpg)

![Step 4: review inputs and see the next action](docs/images/quickstart-flow/en/04-review.jpg)

</details>

<details>
<summary>Screenshot 5: inspect an actual saved GPU result</summary>

This rc5 Barnase–Barstar example shows four evaluated models and their engine-specific scores.

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

Use **amino-acid FASTA**: a `>name` line followed by the sequence. For nanobodies, provide the **full VHH sequence**. Give entries distinct names that do not start with `=`, `+`, `-`, or `@`. Compare prepared substitution or insertion variants in `screen`.

Enter a structure by **file upload** or **PDB ID**, and use **Change input method** to switch. Paste VHH / WT FASTA or import a file. **Load another file** replaces it; **Restore previous FASTA** restores it. After loading a draft, reselect structure files and review the input and transmission consent.

For structure files, check chains, residue numbering, and missing residues. Public MSA sends sequences to an external server. Choose an MSA method that follows your institution's policy for research sequences.

<details>
<summary>Optional: practice variant evaluation in the terminal without a GPU</summary>

In the source folder, activate the environment with `source .nanocofold/workspace/.venv-dev/bin/activate`, then run the command below. It uses public FASTAs to generate **at most 3 DMS variants** and evaluate **at most 2 variant candidates**. `--mock` generates practice structures and CDR annotations.

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

The terminal shows elapsed time and the current stage every **10 seconds**. The terminal prints the actual candidate count before prediction, then **candidate n/total** while calculating scores. When it finishes, open the printed **report.html** path. Keep the linked report files together when moving it.

For a short practice run, use `max_variants=3` and `top_n=2` as shown above. The mature research default evaluates up to **200** variant candidates. For research, remove `--mock` and use your sequences with checked CDR annotations.

</details>

## 5. Read and share results

**To open your result:** **Jobs & logs → select your analysis job → Open results**. On a small screen, scroll below the job details to find its actions. You can also use **Explore results → Select run** to reopen a saved analysis in the same workspace.

| What you see | What to do next |
|---|---|
| **diagnostics / Environment check · Complete** | Click **Prepare analysis** and follow [section 3](#3-try-the-first-public-example). |
| Analysis **Complete** with **Open results** | Open it, check successful/failed/unevaluated counts, then select a model. |
| Failed, cancelled, interrupted or budget exhausted | Check **Detailed execution log** for the cause, then continue with [section 6](#6-follow-jobs-cancel-and-resume). |
| No **Open results** button or no saved run listed | Select the analysis in the workspace used for that run, then check its status and log. |

Then read in this order: **status and counts → candidate/model row → scores, structure and PAE → download**. X/Y selectors compare metrics for the selected view. [Chart example](docs/images/quickstart-flow/en/charts.jpg) · [Report navigation](docs/images/quickstart-flow/en/report-nav.jpg).

- **Fit entire structure** shows the whole complex. **Select target / Select nanobody**, or **Select reference chain / Select partner**, highlights and focuses the relevant chain. **Clear selection** removes highlighting.
- Click a PAE cell to select and focus two residues in the structure viewer. PAE shows uncertainty in their predicted relative positions.
- Save CSV / XLSX tables, FASTA, coordinates, and the **report ZIP**. For Excel, use the exported CSV (UTF-8 BOM) or XLSX; run-folder CSVs are UTF-8 machine records. **Save selected display coordinates** saves the coordinates shown in the viewer. **Download original engine coordinates** is available when the original file is preserved and matches its recorded hash. Check the provenance notice for the relationship between original and display coordinates.

The **Full original ZIP · For local archiving** includes server paths and execution logs and is for personal recovery / archiving. Send collaborators the entire **Shareable report ZIP**. Recipients extract the whole ZIP and open **`report.html`**. Saved results can be read on macOS, Windows, or other PCs without a GPU or program installation. Newly generated interactive reports also include the English / Korean selector.

### Read the report in six steps

| Section | What to check |
|---|---|
| 01 Inputs & settings | Are these the sequences and conditions you intended? |
| 02 Candidate & model counts | Check completed, failed, and unevaluated model counts. For general complexes, review chain-pair scores. |
| 03 Compare candidates | Select a candidate/model and its prediction engine; read its recorded scores. |
| 04 Excluded / unevaluated | If selection is zero, check the reasons for exclusions and unevaluated candidates. |
| 05 Structure & PAE | Inspect the selected model's structure and residue-placement uncertainty together. |
| 06 Provenance & artifacts | Check recorded conditions and save the appropriate report/data files. |

Use section links and browser **Back / Forward** to move through the report while analysis continues. Keep the same candidate selected when comparing languages.

An administrator-hosted Site may use the labels **Jobs**, **Reports and provenance**, **Offline HTML**, and **Resume verified stages**. Download reports with **Offline HTML**. If resume is disabled, read the displayed reason and contact the administrator.

<details>
<summary>File locations, older reports and PyMOL (optional)</summary>

### Find the saved files

Each analysis is saved under `<workspace>/runs/<job_id>/`; find the job ID in **Jobs & logs**. Open `report.html` in that folder. Keep linked `report_explorer-<hash>.html` files beside it; older reports may use `report_explorer.html`. Move or archive the whole report folder together.

The worker log is `<workspace>/.nanocofold/jobs/<job_id>/worker.log`, beside the submitted `config.yaml`. The run folder stores `run_config.yaml`, `run_state.json`, and engine outputs. Check progress and model counts in the job view.

Use **Language → English / 한국어** in the browser and newly exported interactive reports. Older reports retain their original interface. For the current bilingual interface, export a new report from the application and keep the older archive.

If PyMOL materials are provided, extract the **whole folder** and open `.pml` through PyMOL **File → Open**. Keep the relative folder layout of scripts and referenced coordinates. PyMOL materials are separate from the general report ZIP. See [viewing in PyMOL](docs/RESULTS.md#pymol에서-보기).

</details>

Select candidates by reviewing engine-specific scores, ipTM, interface pLDDT, interface PAE, and structures together. Use WT controls for variant comparisons, then test selected candidates for binding, KD, and expression. See [metrics and result interpretation](docs/RESULTS.md).


[Score cohorts, comparison conditions, and statistical interpretation](docs/STATISTICAL_INTERPRETATION.md).

## 6. Follow jobs, cancel and resume

| Situation | What to do |
|---|---|
| Changed tabs, used Back, switched language or closed the browser | Analysis continues. Reopen the existing job in the same workspace. Keep the server and compute host running. |
| Progress seems stuck or the connection dropped | Check **Detailed execution log** and the connection, then reopen the existing job. |
| Want to stop a job | Click **Cancel job**, check the target in the dialog, then confirm. Wait for **Cancelling → Cancelled**. **Keep running** or Esc closes the dialog without requesting cancellation. |
| A failed, interrupted or cancelled job offers **Verify and resume** | Resolve the logged cause, then use that action on the **same job**. Saved inputs/configuration and process ownership are checked before restarting. |
| **Time budget exhausted** | Enter additional minutes when prompted, then **Verify and resume**. |
| Resume is unavailable | Read the displayed reason and log, then contact the administrator. |

While cancelling, NanoCoFold stops the worker and its tools and releases their resources. Saved results remain available; unfinished models may be recalculated when resumed. In a batch, confirm cancellation of one item or all unfinished jobs.

Continue interrupted jobs with **Verify and resume**. Saved results remain available. Review partial results together with job status and completed model counts.

[Changelog](CHANGELOG.md) · [Validation record](docs/VALIDATION.md) · [External engine and model terms](LICENSE_MANIFEST.md)

No separate public license is granted for the project itself. External libraries, engines, and models are subject to their respective terms. Linked technical documents and original execution logs may be in Korean. See [language support](docs/LANGUAGE_SUPPORT.md).
