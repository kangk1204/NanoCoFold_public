# NanoCoFold walkthrough

For your first experiment, follow the variant tutorial below. It follows one named analysis from installation through report delivery. The videos have English narration and selectable English and Korean captions.

## First experiment · ten EGFR nanobody variants

[Watch the experiment](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=en) · [Download the exercise inputs](https://github.com/kangk1204/NanoCoFold_public/releases/download/v0.4.8/NanoCoFold-EGFR-10-inputs.zip) · [Open the recorded result](https://github.com/kangk1204/NanoCoFold_public/releases/download/v0.4.8/NanoCoFold-EGFR-10-report.zip)

[Prepare the inputs (11:31)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=en&chapter=inputs) · [Read hotspot numbers (13:09)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=en&chapter=hotspots) · [Set the mutations (16:50)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=en&chapter=variants) · [Interpret results (20:38)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=en&chapter=results) · [Search the DB (25:07)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=en&chapter=database) · [Open the ZIP (26:08)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=first-run&lang=en&chapter=zip)

### Know your four input files

Extract the whole ZIP. A FASTA file stores a protein sequence as plain text. Open it in a text editor. The first line starts with `>` and names the sequence. Each letter on the next line represents one amino acid.

| File | Where to use it |
|---|---|
| `4KRL.cif` | The experimental structure shows where EGFR and 7D12 meet. Choose it in the **structure file** field. |
| `EGFR-target.fasta` | **Target EGFR**, the protein the nanobody binds. Copy both lines, starting at `>B`, into the **full target sequence** field. |
| `7D12-WT.fasta` | The **original nanobody (WT)**. Copy it into the separate **WT VHH sequence** field. |
| `hotspots.csv` | A list of **target positions close to the nanobody**. Open it in a spreadsheet and read `19, 40, 42` from the first column. You can keep this file open for reference. |

**How to read a position.** Count from the first letter of the target sequence as position one. The nineteenth letter, L, is residue 325 on author chain A in the deposited structure. In this exercise enter `19, 40, 42` in **hotspot positions** and leave **recommendation exclusions** empty. These three target positions stay fixed. The two substitutions are in **CDR3**, a loop near the end of the nanobody.

**For your own target.** If you have a structure, first match its residue numbers to your input sequence. With only a sequence, enter your target FASTA and use the binding sites suggested from its predicted structure.

### Follow the experiment

1. Download the public release on an Ubuntu computer where `nvidia-smi` shows your GPU. In the source folder run `bash install.sh --profile screen`. This profile supplies Boltz-2 and ANARCI for this random CDR3 experiment. In the web app you will choose the **variant** analysis mode. The recording adds `--skip-system-packages` because its Ubuntu system tools were already installed. Use the command above on a new computer and enter your sudo password if prompted. After installation completes, run `bash start.sh` and open **the address printed in the terminal**.
2. Keep the server terminal open. Download the **exercise inputs** above, open Downloads in Files, right-click the ZIP and choose **Extract Here**. To type the commands shown in the film, right-click an empty area in Downloads and choose **Open in Terminal**. Return to the NanoCoFold browser and choose **Evaluate variants** and name the analysis `EGFR 10 variants walkthrough`. Upload `4KRL.cif`. Select **Epidermal growth factor receptor** (chain B, author chain A) as the target. Leave the partner boxes empty and review the chains and residue exposure.
3. Select full-sequence structure prediction. Paste both lines of `EGFR-target.fasta`, including `>B`. Choose to map the selected binding sites to the full sequence. Enter `19, 40, 42` in the hotspot position field. Load `7D12-WT.fasta` in the WT VHH field. These three positions belong to the target. The CDR3 substitutions in the next step belong to the nanobody.
4. In Run settings choose **Legacy default · Apply prefilters**. Choose **Random multi-residue CDR substitutions** and **CDR3 only**. Request **10** variants, set both the minimum and maximum substitutions to **2**, and include WT. In Advanced settings choose **Boltz-2** as the complex prediction engine.
5. Review the input. Check for **214 target residues, seed 42, one Boltz-2 engine and 11 planned models (10 variants plus WT)**. Use **Go to environment check** to check this exact configuration and then run the analysis. In **Jobs & logs**, find the same name and watch the status and completed count.
6. Click **Open results** after completion. Read the model and selection counts first, then choose WT and one variant. Compare ipTM, ipSAE, interface pLDDT, PAE in ångströms, and contacts with the three selected target residues within the same engine. Binding and affinity require experimental measurements.
7. In **Results database**, search `EGFR 10 variants walkthrough` and click the analysis name in the first column. Open **02 Candidate & model counts** and check the recorded handoff status, errors and warnings. Download the report ZIP, extract **all** its files and open `report.html`.

The target and WT are the deposited human EGFR domain III construct and nanobody 7D12. Target positions 19, 40 and 42 correspond to author-chain A residues 325, 346 and 348. All three are within 5 Å of 7D12 in the crystal structure. [RCSB PDB 4KRL](https://www.rcsb.org/structure/4KRL)

**Check against the recorded result.** On an RTX 3060 the analysis took 17 minutes 48 seconds. All 11 structures finished, and every mutant had exactly two CDR3 substitutions. For example, `RND_000001` changes the nanobody’s A at position 98 to L and its E at position 110 to L. In that model the nanobody contacts all three target positions, but its interface confidence (ipSAE) is 0.015, compared with 0.055 for WT. All ten mutants fell below the ipSAE and ipTM thresholds, so zero were selected. Both proteins were predicted without a multiple sequence alignment (MSA) in this run. You can download the result ZIP and examine the structures and scores even when the handoff check shows the run is blocked for ordering.

[Introduction (0:28)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=intro&lang=en) · [Ubuntu installation overview (2:30)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=install&lang=en) · [Four-mode overview (5:47)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=walkthrough&lang=en)

### Installation video chapters

- 0:00　Get the public release
- 0:10　Check your GPU
- 0:21　Download the source
- 0:31　Open the release folder
- 0:45　Choose an installation profile
- 0:54　For a first protein complex
- 1:05　For all four analysis modes
- 1:15　Watch the installer run
- 1:26　Recognize the completion message
- 1:39　Finish a GPU installation
- 1:52　Start the web app
- 2:05　Open the printed address
- 2:17　Check the installed engines

### Four-mode overview chapters

- 0:00　Welcome
- 0:09　Install on Ubuntu
- 1:18　Predict a complex
- 2:20　Read the complex result
- 2:57　Design new nanobodies
- 3:42　Compare existing nanobodies
- 4:13　Evaluate variants
- 5:00　Search and share results

## 1. Install and start NanoCoFold

Use an Ubuntu x86_64 computer where `nvidia-smi` shows the NVIDIA GPU. The published requirements for the four-engine example include glibc 2.34 or later, driver 580 or later, 16 GB of GPU memory, 32 GB of system memory, 150 GiB of free SSD space, internet access and `sudo`. The commands below assume one GPU. For a multi-GPU computer, follow the GPU selection instructions in the README. [Installation requirements](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8-install.9/README.md#1-prepare-and-install)

For a first installation, open a terminal and run these commands.

```bash
git clone --branch v0.4.8-install.9 https://github.com/kangk1204/NanoCoFold_public.git
cd NanoCoFold_public
git describe --tags --exact-match
```

The last command prints `v0.4.8-install.9`. The film shows the original `v0.4.8` tag. If you already have an installation, follow the update instructions in its original folder. [Update an existing installation](https://github.com/kangk1204/NanoCoFold_public/blob/main/docs/UPDATE.en.md) · [Install from a ZIP without Git](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8-install.9/README.md#1-prepare-and-install)

Choose **one** installation command.

| What you want to run | Command |
|---|---|
| EGFR CDR3 variants with Boltz-2 | `bash install.sh --profile screen` |
| The first complex example with Boltz-2 | `bash install.sh --profile complex` |
| All four analysis modes and prediction engines | `bash install.sh --profile all` |

Wait for **[설치 완료]** (installation complete). Start the web server from the same folder.

```bash
bash start.sh --port 18765
```

Open the address printed after **NanoCoFold:** in a browser on the Ubuntu computer. Keep the terminal open. If the port is already in use, run `bash start.sh --port 0` and open the new address it prints. To work from another computer, follow the two-terminal instructions. [Connect to a remote server](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8-install.9/README.md#2-open-the-browser)

## 2. Try the four-mode overview examples

1. Select **Language → English**. For the Barnase–Barstar example, open **Input & run → Predict protein complexes → Load public example for this purpose**. Check for Barnase (110 amino acids) and Barstar (89 amino acids), one copy each.
2. Continue to Run settings. To reproduce the one-model plan, choose **Legacy default · Apply prefilters**, expand **Advanced settings**, and select **Boltz-2** as the complex prediction engine. Review the input and confirm seed 42, MSA empty and one planned model.
3. Click **Go to environment check** from that reviewed input. Run the check for the selected engine. When ready, click **Prepare analysis**, confirm the input, and click **Run analysis** once. A new installation with the complex profile has Boltz-2 and Chai-1. A four-engine environment check also requires ColabFold and OpenDDE.
4. In **Jobs & logs**, select the name you entered. Check the current stage and completed or failed model counts. When your analysis shows **Complete**, click **Open results**. A **diagnostics** entry records an environment check.

For your own data, put each protein in FASTA format. Write `>name` on the first line and the full amino acid sequence beneath it. Give each candidate a different name. Selecting public MSA sends the sequences to an external service, so check that you can share them first. [Example FASTA](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/examples/tutorial/vhh_library.fasta) · [Public inputs for all four modes](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/examples/tutorial/README.md)

| Analysis mode | Input | Result shown in the video |
|---|---|---|
| Predict protein complexes | One sequence and a copy count per chain | Boltz-2 completed one Barnase–Barstar model. Whole-complex ipTM was 0.078 and mean interface PAE was 24.84 Å. The model gives low confidence in the relative positions of the two chains. |
| Design new nanobodies | Target sequence or structure | Two generators produced two candidates for the IL-6 example. Four engines evaluated each candidate, giving eight completed models. No candidate passed final selection. |
| Compare existing nanobodies | Target and full VHH sequence for each candidate | Four engines evaluated one anti-IL-6 VHH. Its Boltz-2 ipSAE was 0.378. No candidate passed final selection. |
| Evaluate nanobody variants | Target and original VHH sequence (wild type, WT) | Four engines evaluated the WT and the N100R variant, giving eight completed models. Boltz-2 ipSAE was 0.378 for the WT and 0.013 for N100R. No candidate passed final selection. |

Select a model to inspect its structure, interface scores and PAE together. PAE estimates the error in the position of one residue relative to another, in ångströms. Compare a variant with its WT using the same engine and settings. **Completed models** counts predictions that produced output. **Final selection** counts candidates that passed the configured criteria. Binding and changes in affinity require experimental measurement.

## 3. Find the result again and share it

1. Analyses completed in the web app are registered in **Results database**. To add an earlier run, place its original run folder inside your workspace. Go to **Explore results**, enter that folder's path and click **Open existing result**.
2. In **Results database**, enter part of the analysis name or ID and click **Search**. Use the name you entered. The older overview searches for `public-complex`, a result in its filming workspace. Click the **analysis name in the first column** to open the report in a new tab. If an identical run reuses an earlier result, open it from **Jobs & logs → Open results**.
3. In **Explore results**, select **Shareable report ZIP** and export it. Send the whole ZIP. The recipient extracts the entire archive, then opens **`report.html`** in a browser. They can explore the structures and scores offline without installing NanoCoFold. [Read and share results](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8-install.9/README.md#5-read-and-share-results)

## What was recorded

The installation video uses a separate copy of the v0.4.8 tag on an Ubuntu computer with an RTX 5070 Ti. The terminal recording shows the core app installing, its completion message and the first browser connection. The completion of the full GPU installation is shown from a saved log. The long engine and model downloads are omitted. To run predictions, choose the GPU profile for your task in the table above.

The four-mode overview follows the public examples through data entry and result viewing. The Run button and progress were recorded in CPU practice mode for existing nanobodies. The completed complex result is a saved GPU run that took 56.7 seconds on an RTX 3080 Ti Laptop GPU. The design, screening and variant examples reopen saved GPU predictions from `0.4.8rc5` in the filmed public report interface. [Public release](https://github.com/kangk1204/NanoCoFold_public/releases/tag/v0.4.8)
