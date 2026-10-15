# Your first analysis in the browser

**English** · [한국어](browser_quickstart.md)

This guide shows the four-step **0.4.8rc5** interface. Follow the [README installation steps](../README.md#1-prepare-and-install) first. If your server is already installed, start at [Open the browser](../README.md#2-open-the-browser). Use the same source folder and workspace as the installation.

Open `http://127.0.0.1:8765`. For a remote server, keep both the server terminal and your SSH tunnel open. Select **Language → English** to match the screenshots. If **Preparing the interface.** appears, wait for the purpose buttons. If preparation fails, read the error and click **Reload interface**.

## Follow the screenshots

**1. Choose a purpose.** Start with **Predict protein complexes · Enter input**.

![Choose protein complex prediction](images/quickstart-rc5/en/01-purpose.jpg)

**2. Load the public input.** Click **Load public example for this purpose**. The full sequences are filled in automatically. Check **Barnase: 110 aa**, **Barstar: 89 aa**, and **one copy of each**, then click **Review input & continue**.

![Check both full sequences, chain order and copy counts](images/quickstart-rc5/en/02-input.jpg)

**3. Check the settings.** Keep the small starter preset, **all four engines**, and **four planned models**. This preset uses seed **42**, **one model per engine**, **3 recycles**, and **200 diffusion steps**. Leave advanced settings unchanged for your first run. To use public MSA, give consent to send the current public sequences and click **Review settings & continue**. The screenshot shows consent **before checking the box**.

![Four engines, planned model count and MSA consent](images/quickstart-rc5/en/03-settings.jpg)

**4. Review before running.** Click **Review input & check environment**. Read the applied input/settings summary and confirm that the required environments pass. Then click the enabled **Run analysis** control **once**. The screenshot is **before checks**; it does not show installation success or approval to submit. Review alone does not submit prediction. If you change inputs or settings, review and confirm consent again.

![Review input and check the environment before submitting](images/quickstart-rc5/en/04-review.jpg)

**5. Read the result.** Follow **Jobs & logs → completion → Open results**. Check evaluated, failed and unevaluated counts first, then select a model. Four planned models do not guarantee four successful predictions. This screenshot shows the previously verified four-engine GPU run. **Not applicable** for VHH eligibility is normal for a general protein complex.

![Planned and evaluated model counts in an actual GPU result](images/quickstart-rc5/en/05-results.jpg)

**6. Explore and share.** Use **Fit entire structure**, chain selection and PAE cell selection. Read each model's metrics, structure and PAE together. Save the entire **Shareable report ZIP** for collaborators. Recipients extract the whole ZIP and open **report.html** without installing NanoCoFold or using a GPU.

![The selected actual model and its metrics](images/quickstart-rc5/en/06-structure-pae.jpg)

![The same model's PAE](images/quickstart-rc5/en/07-pae.jpg)

Input/settings screenshots were captured from the actual local rc5 interface without submitting another calculation. Result screenshots use the preserved actual GPU result, opened read-only. [Capture scope and provenance](images/quickstart-rc5/en/README.md)

## Settings by purpose

Use **Load public example for this purpose → review input → run settings → review** for each purpose. The examples use public IL-6 inputs. Seed is **42** and each prediction engine generates **one model** per evaluated sequence. Actual generation, selection and successful prediction counts can differ from the maximum plan.

| Purpose | Required input | Small starter settings |
|---|---|---|
| Predict protein complexes | Full sequences of the proteins to predict together | Four engines → four planned models |
| Design new nanobodies | Full target sequence or structure | RFantibody 1 + BoltzGen 1, evaluated by four engines → up to eight models |
| Compare existing nanobodies | Target + full VHH sequence for each candidate | Every input VHH × four engines |
| Evaluate nanobody variants | Target + full WT VHH sequence | Prepare up to three variants; evaluate up to two plus WT with four engines → up to twelve models |

The small starter preset disables design generator prefilter rejection and the mature ESM score threshold. It retains ESM scoring, variant limits and final quality criteria. AntiFold is off in the variant example. A full VHH sequence is required; a CDR3 fragment alone is insufficient.

<details>
<summary>Design new nanobodies: settings screenshot</summary>

![Two design generators and four evaluation engines](images/quickstart-rc5/en/settings-design.jpg)

</details>

<details>
<summary>Compare existing nanobodies: settings screenshot</summary>

![All input VHH candidates and four evaluation engines](images/quickstart-rc5/en/settings-screen.jpg)

</details>

<details>
<summary>Evaluate nanobody variants: settings screenshot</summary>

![Variant cap, WT control and maximum planned models](images/quickstart-rc5/en/settings-mature.jpg)

</details>

These web starter settings can differ from the preserved CLI conditions in `examples/tutorial/*_batch.json`. In particular, `library.size` does not cap DMS variants in the existing mature JSON. Use the web starter preset for the small plan shown here. The CLI DMS cap is `mature.library.max_variants`.

## Your own inputs and saved results

Choose either a structure file or a PDB ID; use **Change input method** to switch. Paste VHH/WT FASTA or import a file once. The populated FASTA is the actual input. Use **Load another file** or **Restore previous FASTA** to replace or undo an import. After restoring a draft, reselect structure files and reconfirm consent, review and environment checks.

In results, select a candidate to inspect its structure. Use **Show detailed metric columns** when needed. **Save selected display coordinates** saves the displayed coordinates; **Download original engine coordinates** is available only when the original is preserved and matches its recorded hash. Unevaluated is not zero, and low confidence is different from execution failure. The full original ZIP includes server paths/logs and is intended for personal archiving; share the report ZIP with collaborators.

## If you get stuck

- **Continue is unavailable:** read the input error and check full sequences, chain order and required files.
- **Run analysis is unavailable:** review the input and required environment checks; install missing environments as directed.
- **A request is slow or the connection drops:** check the jobs list before submitting again.
- **Only part of a structure is visible:** fit the entire structure and check chain visibility.
- **MOCK or CPU demo is shown:** these are synthetic workflow examples, not actual GPU predictions.

Failed input-review files are preserved under `inputs/failed_reviews`. Review retention needs before cleaning them. Prediction completion, quality eligibility and experimental binding are different outcomes. Do not combine engine scores into a binding probability or KD. See [result interpretation](RESULTS.md) (technical reference in Korean).
