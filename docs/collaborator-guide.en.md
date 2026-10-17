# NanoCoFold collaborator guide

NanoCoFold designs nanobody candidates for a target protein and compares their predicted structures and sequence properties. Start with the public example to install the software and find the results. To view results someone sent you, go to [Opening ZIP files](#5-open-and-share-zip-files).

- [GitHub](https://github.com/kangk1204/NanoCoFold_public)
- [English README](../README.md) · [한국어 README](../README.ko.md)
- [Step-by-step screenshots](browser_quickstart.en.md#follow-the-screenshots)

## How does the platform work?

This is the basic workflow for designing new nanobodies. A backbone is the protein's three-dimensional framework; a sequence specifies the amino acids that form it.

**Target and scaffold → generate candidates → optional pre-assessment and CDR substitutions → predict complexes → assess and select → report**

1. **Prepare the input:** choose the target protein and a nanobody scaffold. Specify target residues if you have a preferred binding site.
2. **Generate candidates:** select RFantibody, BoltzGen, or both.

   | Design tool | Steps |
   |---|---|
   | RFantibody | Generate backbones with RFdiffusion → design sequences with ProteinMPNN → check structures with RF2 |
   | BoltzGen | Design structures → design compatible sequences by inverse folding → refold and analyze |

   Selecting both tools generates candidates with each tool, then brings them together for assessment.
3. **Predict complexes:** use the selected ColabFold, Boltz-2, Chai-1, or OpenDDE engines to predict candidate–target complexes. Choose engines and any staged screening workflow in the run settings.
4. **Assess and select:** review ipTM/ipSAE, pLDDT/PAE, hotspot contacts, atomic clashes, and sequence developability. Compare candidates within the same engine and conditions. Selection applies eligibility criteria and CDR3 diversity limits.
5. **Review the results:** work through the candidate table, 3D structures and PAE, exclusion and unevaluated-item reasons, then download sequences, scores, and reports. Use the final candidates to plan follow-up experiments.

Depending on the settings, you can add CDR substitution candidates or use ESM/AntiFold pre-assessment.

| Purpose | Starting point and result |
|---|---|
| Predict protein complexes | Predict a complex from the supplied proteins |
| Design new nanobodies | Generate and assess candidates from a target and scaffold |
| Compare existing nanobodies | Assess supplied VHH sequences against the same target |
| Evaluate nanobody variants | Generate CDR substitutions in an existing VHH and compare them with the parent |

## 1. Download the software

On GitHub, click **Code → Download ZIP** and extract the entire archive. Open the folder containing `README.md`, `install.sh`, and `start.sh`, then open a terminal inside that folder.

Install on Ubuntu x86_64 with an NVIDIA GPU. The first four-engine example requires at least 16 GB GPU memory, 32 GB RAM, and 150 GiB free SSD space. Check the operating system and driver requirements in the [README](../README.md#1-prepare-and-install). On Windows or macOS, connect to an installed server or open a report someone sent you.

## 2. Install

Run these commands one at a time. Wait for each to finish with `[설치 완료]` (installation complete) before starting the next. The first installation downloads engines and model files.

```bash
bash install.sh --profile complex
bash install.sh --profile colabfold
bash install.sh --profile opendde
```

For multiple GPUs, follow **Multiple GPUs** in the README to select your assigned GPU. With less than 16 GB GPU memory, ask your administrator to choose suitable engines and input sizes. For nanobody design and variant evaluation, follow the README's **all environments** installation instructions.

## 3. Open the interface

On the computer where you installed NanoCoFold, run this in the same folder:

```bash
bash start.sh --port 18765
```

Open the address printed after **NanoCoFold:** in the terminal. The default address for this command is [http://127.0.0.1:18765](http://127.0.0.1:18765). Check that **NanoCoFold** appears at the top. Use **Language** to select English or Korean. Keep the server terminal open while using the program.

For a remote server, keep both of these terminals open.

**Server terminal:**

```bash
bash start.sh --no-browser --port 18765
```

**Your computer's terminal:** replace `USER@SERVER` with the account and server address provided by your administrator.

```bash
ssh -o ExitOnForwardFailure=yes -L 18765:127.0.0.1:18765 USER@SERVER
```

Once connected, open [http://127.0.0.1:18765](http://127.0.0.1:18765) in your computer's browser. If you need a different port, follow the README's remote connection instructions.

## 4. Run the first analysis and find the results

1. Under **First time · Check your environment**, choose **Predict protein complexes**, then click **Check environment**.
2. When the check completes, click **Prepare analysis**.
3. Click **Load public example for this purpose**. This fills in Barnase (110 aa) and Barstar (89 aa), with one copy each.
4. Click **Review input & continue**. Check the small starter preset, four engines, and four planned models.
5. Consent to sending the example sequences to the public MSA service. Click **Review settings & continue → Review input → Run analysis** once.
6. In **Jobs & logs**, select the analysis you submitted and wait for completion. Then click **Open results**.

First check the completed, failed, and unevaluated model counts. Select a candidate/model row to view its scores, 3D structure, and PAE. You can reopen a saved analysis through **Explore results → Select run**.

`diagnostics · Complete` is the environment-check record. Continue with **Prepare analysis**. If the results button is out of view, scroll down in the job details.

After the public example, choose your research purpose and enter your own amino acid sequences.

## 5. Open and share ZIP files

**Extract the entire ZIP**, then open the following file in the extracted folder:

| File received | How to open it |
|---|---|
| Combined collaborator ZIP | `START_HERE.html` → **Open report** for a mode |
| Individual screen, mature, or design sample ZIP | `report.html` |
| Complex sample ZIP | Open `START_HERE.html` to read the mock-run refusal record |
| Shareable report ZIP downloaded after analysis | `report.html` |

The supplied samples are CPU/mock examples. Use **Language** at the top of the report to switch between English and Korean. Keep the extracted folder structure intact, including the files alongside the report. Saved reports also open in web browsers on Windows and macOS.

To share your own analysis, download **Shareable report ZIP** and send the entire archive. You can also save tables as CSV/XLSX, sequences as FASTA, and structures as coordinate files.
