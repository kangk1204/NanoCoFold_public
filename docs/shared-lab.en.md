# Share one lab GPU from several desks

The **server** is the Ubuntu computer with NanoCoFold installed. Each **client** needs a browser and SSH access to that computer. Researchers initially see their own submissions. The administrator can review every result.

## On the server

1. [Update the existing installation](UPDATE.en.md). In the original source directory, initialize the administrator and create an account for each researcher. Enter each researcher’s password twice when prompted.
   ```bash
   python3 scripts/lab_account.py init-admin
   python3 scripts/lab_account.py add Song
   python3 scripts/lab_account.py add Kim
   ```
2. Start the web server once and keep the terminal open.
   ```bash
   bash start.sh --no-browser --port 18765
   ```

The server prints a one-time activation code. Sign in with **admin / admin** and enter the code and a new password (at least 10 characters). Sign in again with your new password to view all results. If you lose the code before activation, run `init-admin` again. If an existing researcher is named `admin`, verify the account owner on the server, then run `python3 scripts/lab_account.py promote-admin` to promote that account.

**To change your password later**, sign in and select **Change password** next to your account name. Enter your current password and the new one twice. Then sign in again on each device. Running analyses and saved results stay in place. Researchers can use the same steps for their own account.

Under **Results database → Administrator · Result access**, check **Allow all researchers to view all lab results** and choose **Save permissions**. Clear it and save to restore private access for subsequent requests and downloads. Previously downloaded files stay with their recipients. This controls NanoCoFold web access. The server operator manages SSH and filesystem permissions separately.

Creating the first account turns on lab sign-in for this workspace. Older results keep their original files and have no researcher assigned. Use `python3 scripts/lab_account.py list` to list accounts. If someone forgets their password, use `python3 scripts/lab_account.py password NAME` on the server to reset it or `disable NAME` to revoke access and keep the submission history. A disabled name stays reserved for that researcher. Back up the workspace and the server operator’s `~/.config/nanocofold/labs/` directory.

## At each desk

1. Open a terminal. Replace `USER@SERVER` with your SSH account and the server address.
   ```bash
   ssh -N -o ExitOnForwardFailure=yes -L 18765:127.0.0.1:18765 USER@SERVER
   ```
2. Leave that terminal open. Open **http://127.0.0.1:18765** and sign in with your NanoCoFold account.
3. Open **Environment & setup → Use one server from several desks** to see the connection instructions again. Submit your analysis. Opening **Input & run** after acceptance takes you to step **1/4**. You can reuse the previous sequences and review the input and run settings. Saved public MSA consent remains in effect for the next analysis. Switching tabs before submitting keeps your place.
4. In **Jobs & logs**, a queued job shows why it is waiting. It starts automatically when the GPU becomes available. You can cancel your own queued job.
5. In **Results database → Candidate sequences and structures**, search for a target or candidate ID. Filter by engine and recorded decision. To include practice runs, select **Include practice runs** and search again. Click a metric heading to sort, a candidate ID to see its sequence and structure, or the analysis name for the report. **Export all matches to Excel** includes every matching page. Open **Analyses → Lab usage history** to see submissions, including pending, cancelled and reused jobs.

After you click **Run analysis**, leave the tab open until the job appears. The first submission may take a few minutes while the server checks model files. Click only once. Once listed in **Jobs & logs**, the analysis continues if you close the browser.

## Sequence transfer and storage

- Under **Run settings**, choosing the public MSA service sends input sequences to the external service to search for related sequences. Consent is saved once per account and applies to later analyses, batches and page reloads. You can revoke it in **Environment & setup**. A personal installation without login saves consent for that installation. The verified cache option needs an existing alignment for the sequence; local search needs a configured database. Neither sends sequences to the public service. Boltz-2 follows the selected MSA settings.
- SQLite stores result summaries, file references and hashes. Eligible, identical requests can reference one saved result. Under **Explore results**, preview storage cleanup before confirming. Compact retention keeps metrics, sequences and final structures. Keep the full original if you need PAE, MSA or intermediate files for resuming. Cleanup is explicit.
- Before each real launch or resume, the server checks free memory and utilization on the selected GPU. Several analyses can run on the same GPU when capacity is available. Jobs wait if utilization is at least 90%, telemetry is unavailable, or an exclusive task is running. The queue checks again after about ten seconds. Waiting does not consume the run time budget.
- The default reservation is 6 GiB per analysis. The device keeps the larger of 10% or 1 GiB in reserve. On smaller GPUs, the job reservation is reduced to the remaining capacity. An active NanoCoFold job counts against available memory even before it loads its model. This is an admission estimate. Actual peak memory depends on the engine and sequence length. For analyses that typically use 10 GiB, for example, start the server with `NANOCOFOLD_GPU_JOB_MIB=10240 bash start.sh --no-browser --port 18765`.

In Results database, search for a candidate ID or a VHH sequence fragment (three or more consecutive amino acids). Add a researcher filter if needed. Open a matching candidate to see its sequence and predicted structure. Identical sequences are stored once in the search database.

## Shared execution policy

Administrators can view all results and set sharing permissions. Only the submitter can cancel or resume an analysis. New analyses reuse results only from the same researcher. Previously shared records remain available.

After five failed sign-in attempts for one account within a minute, wait before trying again. If GPU status cannot be read for 120 seconds, the job fails with instructions to check the GPU ID and driver, then resume. Jobs waiting for available memory continue checking automatically.

The GPU reservation is an operator-defined scheduling policy, not a prediction of peak memory. Installation and GPU checks run exclusively. A shared ticket on a separate local MSA search service has its own lifetime. Before stopping such a search, the server operator must check whether another request uses it.
