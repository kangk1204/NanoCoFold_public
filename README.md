# NanoCoFold

**English** · [한국어](README.ko.md)

**Build: 0.4.8rc5**

Predict protein complexes, design new nanobodies, and compare existing candidates or variants. Explore candidate tables, 3D structures, PAE, and shareable HTML reports.

**For your first run, follow sections 1 → 2 → 3.** The public example fills in the sequences and settings; leave advanced settings unchanged. A [large-image walkthrough](docs/browser_quickstart.en.md#follow-the-screenshots) is also available in English.

**If your server is already installed, start at section 2.** Use the same source folder and workspace as the installation. The source under `source/` in a delivery bundle and a saved `report.html` are different entry points. To read existing results, extract the report ZIP and open `report.html`; no installation is needed.

## 1. Prepare and install

The compute host needs **Ubuntu x86_64**, glibc 2.34 or later, an NVIDIA GPU, and driver 580 or later. `nvidia-smi` must show the GPU correctly. For small examples, prepare at least 16 GB GPU memory, 32 GB RAM, and 150 GiB of free SSD space. Longer proteins and larger libraries may need more resources. Internet access and `sudo` are also required.

You do not need to install Python packages, the CUDA Toolkit, Conda, or Docker separately. The installer prepares isolated Python environments, engines, and models. It does not change the NVIDIA driver.

1. If you received a ZIP, **extract the entire archive**. If you cloned the repository, use the existing checkout.
2. Open **`NanoCoFold_public`** after a Git clone, or **`NanoCoFold_public-main`** after GitHub **Download ZIP**. Tagged or separately packaged source archives may have a different folder name.
3. Open a terminal inside that folder. You should see `README.md`, `install.sh`, and `start.sh`.
4. To prepare all four engines for the first complex example, run these **three commands in order, in the same folder**. Wait for each command to finish with **`[설치 완료]`** (installation complete) before starting the next.

```bash
bash install.sh --profile complex
bash install.sh --profile colabfold
bash install.sh --profile opendde
```

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

## 2. Open the browser

Run this in the **same folder** after installation:

```bash
bash start.sh
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). This terminal runs the web server, so it is normal not to see a new command prompt. Keep it open while using the platform.

If **Preparing the interface.** appears, wait for the purpose buttons. If preparation fails, read the error and click **Reload interface**. The interface starts in English. Use **Language → 한국어** in the header to switch; the browser remembers your choice. Switching languages preserves inputs and analysis state. Original identifiers, sequences, numerical results, logs, and configuration records are kept as recorded.

For a remote Ubuntu server, run `bash start.sh --no-browser` on the server. In a **separate terminal on your own PC**, run the command below and keep both terminals open. Replace `USER@SERVER` with your server account and address.

```bash
ssh -L 8765:127.0.0.1:8765 USER@SERVER
```

Then open the address above in your PC browser. Inputs and results are viewed on the PC; computation uses the GPU of the Ubuntu server where you installed NanoCoFold.

## 3. Try the first public example

Start with **Predict protein complexes**. You do not need to create sequences or a settings file.

1. Click **Predict protein complexes · Enter input** on the first screen.
2. Click **Load public example for this purpose**. The barnase–barstar sequences and small-run settings are filled in.
3. In **Prepare input**, check both protein names, full sequences, chain order, and copy counts. Click **Review input & continue**.
4. In **Run settings**, check selected engines, the maximum planned model count, and the MSA method. If using the public MSA server, explicitly check consent to send the current sequences to `api.colabfold.com`. Click **Review settings & continue**.
5. In **Review & run**, click **Review input & check environment**. Read the input / settings summary and environment check results. **Run analysis** is enabled after the required checks pass. Computation has not been submitted yet.
6. Click **Run analysis** once. Follow progress in **Jobs & logs**. When finished, click **Open results**.

For the first example, check these values. **Four planned models do not guarantee four successful predictions**; inspect the actual counts afterward. The screenshots show the current English interface; select **Language → English** to match them.

| Step | Key settings to check | Screenshot |
|---|---|---|
| Purpose | Predict protein complexes | [1](docs/images/quickstart-rc5/en/01-purpose.jpg) |
| Input | Barnase 110 aa + Barstar 89 aa, one copy of each | [2](docs/images/quickstart-rc5/en/02-input.jpg) |
| Settings | Small starter preset, four engines, seed 42, one model per engine → four planned models | [3](docs/images/quickstart-rc5/en/03-settings.jpg) |
| Review & run | Consent for the current sequences → input/environment checks pass → run once | [4 · before checks](docs/images/quickstart-rc5/en/04-review.jpg) |
| Results | Completed, failed and unevaluated counts → select a model → structure and PAE | [5](docs/images/quickstart-rc5/en/05-results.jpg) · [structure](docs/images/quickstart-rc5/en/06-structure-pae.jpg) · [PAE](docs/images/quickstart-rc5/en/07-pae.jpg) |

<details>
<summary>Screenshots 1–2: choose a purpose and check the public input</summary>

![Step 1: choose protein complex prediction](docs/images/quickstart-rc5/en/01-purpose.jpg)

![Step 2: check both full sequences and copy counts](docs/images/quickstart-rc5/en/02-input.jpg)

</details>

<details>
<summary>Screenshots 3–4: check settings and find the review button</summary>

Leave advanced settings unchanged. Screenshot 3 shows the transmission consent box unchecked. Confirm consent for the current public example before continuing. Screenshot 4 is **before environment checks**; the actual run control becomes available after checks pass.

![Step 3: four engines and public MSA settings](docs/images/quickstart-rc5/en/03-settings.jpg)

![Step 4: review inputs and check the environment](docs/images/quickstart-rc5/en/04-review.jpg)

</details>

<details>
<summary>Screenshot 5: inspect an actual saved GPU result</summary>

This is the previously verified rc5 barnase–barstar run with four actual engine models. General complexes do not use VHH eligibility. Capturing the input and settings screens did not submit another calculation.

![Step 5: compare planned and evaluated model counts](docs/images/quickstart-rc5/en/05-results.jpg)

</details>

After changing inputs or settings, confirm review, environment checks, and transmission consent again. Advanced settings are available in the relevant step; use defaults for the first example. If a request is slow or the connection drops, check the jobs list for an existing submission before clicking again.

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

Prepare an input using one method. Selecting a structure file disables the PDB ID alternative; entering a PDB ID disables the file alternative. Use **Change input method** to switch. Paste VHH / WT FASTA or import a file once. The populated FASTA is the actual input; no duplicate file is required. Use **Load another file** to replace it or **Restore previous FASTA** to undo the import. After restoring a draft, reselect structure files and reconfirm transmission consent, input review, and environment checks.

For structure files, review chains, residue mappings, missing residues, and the preparation method. Public MSA transmits sequences externally. For private research inputs, check your institution's policy and choose a suitable MSA method. Consent for a public example does not carry over to your own sequences.

## 5. Read and share results

First inspect **run status and counts of successful, failed, and unevaluated models**. Click a candidate row to open its structure and PAE.

- **Fit entire structure** shows the whole complex. **Select target / Select nanobody**, or **Select reference chain / Select partner**, highlights and focuses the relevant chain. **Clear selection** removes highlighting.
- Clicking a PAE cell selects and focuses two residues and moves to the structure viewer. PAE describes uncertainty in predicted relative residue placement; it is not a binding measurement.
- Save CSV / XLSX tables, FASTA, coordinates, and the **report ZIP**. **Save selected display coordinates** saves the coordinates shown in the viewer. **Download original engine coordinates** is available when the original file is preserved and matches its recorded hash. Check the provenance notice for the relationship between original and display coordinates.

The **Full original ZIP · For local archiving** includes server paths and execution logs and is for personal recovery / archiving. Send collaborators the entire **Shareable report ZIP**. Recipients extract the whole ZIP and open **`report.html`**. Saved results can be read on macOS, Windows, or other PCs without a GPU or program installation. Newly generated interactive reports also include the English / Korean selector.

If PyMOL materials are provided, extract the **whole folder** and open `.pml` through PyMOL **File → Open**. Keep the relative folder layout of scripts and referenced coordinates. PyMOL materials are separate from the general report ZIP. See [viewing in PyMOL](docs/RESULTS.md#pymol에서-보기).

Prediction completion, selection eligibility, and experimentally confirmed binding are distinct. Unevaluated is not zero; WT is a control. Scores from different engines are not summed into one ranking. Review ipTM, interface pLDDT, interface PAE, and structures together. Binding, KD, and expression require experimental validation. See [metrics and result interpretation](docs/RESULTS.md).

[Changelog](CHANGELOG.md) · [Validation record](docs/VALIDATION.md) · [External engine and model terms](LICENSE_MANIFEST.md)

No separate public license is granted for the project itself. External libraries, engines, and models are subject to their respective terms. Linked technical documents and original execution logs may be in Korean. See [language support](docs/LANGUAGE_SUPPORT.md).
