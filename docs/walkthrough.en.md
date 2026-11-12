# NanoCoFold walkthrough

For a first installation, open the installation video and follow the commands below. The videos have English narration and selectable English and Korean captions.

[Introduction (0:28)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=intro&lang=en) · [Ubuntu installation (2:30)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=install&lang=en) · [Full walkthrough (5:47)](https://kangk1204.github.io/NanoCoFold_public/watch/?v=walkthrough&lang=en)

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

### Full walkthrough chapters

- 0:00　Welcome
- 0:09　Install on Ubuntu
- 1:18　Predict a complex
- 2:20　Read the complex result
- 2:57　Design new nanobodies
- 3:42　Compare existing nanobodies
- 4:13　Evaluate variants
- 5:00　Search and share results

## 1. Install and start NanoCoFold

Use an Ubuntu x86_64 computer where `nvidia-smi` shows the NVIDIA GPU. The published requirements for the four-engine example include glibc 2.34 or later, driver 580 or later, 16 GB of GPU memory, 32 GB of system memory, 150 GiB of free SSD space, internet access and `sudo`. The commands below assume one GPU. For a multi-GPU computer, follow the GPU selection instructions in the README. [Installation requirements](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/README.md#1-prepare-and-install)

For a first installation, open a terminal and run these commands.

```bash
git clone --branch v0.4.8 https://github.com/kangk1204/NanoCoFold_public.git
cd NanoCoFold_public
git describe --tags --exact-match
```

The last command prints `v0.4.8`. If you already have an installation, follow the update instructions in its original folder. [Update an existing installation](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/docs/UPDATE.en.md) · [Install from a ZIP without Git](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/README.md#1-prepare-and-install)

Choose **one** installation command.

| What you want to run | Command |
|---|---|
| The first complex example with Boltz-2 | `bash install.sh --profile complex` |
| All four analysis modes and prediction engines | `bash install.sh --profile all` |

Wait for **[설치 완료]** (installation complete). Start the web server from the same folder.

```bash
bash start.sh --port 18765
```

Open the address printed after **NanoCoFold:** in a browser on the Ubuntu computer. Keep the terminal open. If the port is already in use, run `bash start.sh --port 0` and open the new address it prints. To work from another computer, follow the two-terminal instructions. [Connect to a remote server](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/README.md#2-open-the-browser)

## 2. Prepare input and run the first example

1. Select **Language → English**. Open **Environment & setup → Check environment once**, select **Predict protein complexes** and check the environment. When **Environment ready** appears, click **Prepare analysis**.
2. Choose **Load public example for this purpose**. Check that Barnase (110 amino acids) and Barstar (89 amino acids) each have one copy. Click **Review input & continue**.
3. To reproduce the setup shown in the video, select only **Boltz-2**, use no MSA (multiple sequence alignment) and keep seed **42**. Choose **Review settings & continue → Review input**. Check that the plan shows **one model**, then click **Run analysis** once.
4. Open **Jobs & logs** and select the analysis you submitted. Follow the current stage and completed or failed model counts. A completed **diagnostics** entry is the environment check. When the analysis job shows **Complete**, click **Open results**.

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
2. In **Results database**, enter part of the analysis name or ID and click **Search**. The video searches for `public-complex`. Click a matching row to reopen its report in a new tab.
3. In **Explore results**, select **Shareable report ZIP** and export it. Send the whole ZIP. The recipient extracts the entire archive, then opens **`report.html`** in a browser. They can explore the structures and scores offline without installing NanoCoFold. [Read and share results](https://github.com/kangk1204/NanoCoFold_public/blob/v0.4.8/README.md#5-read-and-share-results)

## What was recorded

The installation video uses a separate copy of the v0.4.8 tag on an Ubuntu computer with an RTX 5070 Ti. The terminal recording shows the core app installing, its completion message and the first browser connection. The completion of the full GPU installation is shown from a saved log. The long engine and model downloads are omitted. To run predictions, choose the GPU profile for your task in the table above.

The full walkthrough follows the public examples through data entry and result viewing. The Run button and progress were recorded in CPU practice mode for existing nanobodies. The completed complex result is a saved GPU run that took 56.7 seconds on an RTX 3080 Ti Laptop GPU. The design, screening and variant examples reopen saved GPU predictions from `0.4.8rc5` in the filmed public report interface. [Public release](https://github.com/kangk1204/NanoCoFold_public/releases/tag/v0.4.8)
