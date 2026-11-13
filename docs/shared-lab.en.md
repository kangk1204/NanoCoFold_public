# Share one lab GPU from several desks

The **server** is the Ubuntu computer with NanoCoFold installed. Each **client** needs a browser and SSH access to that computer. The lab workspace shares results among its members.

## On the server

1. [Update the existing installation](UPDATE.en.md). In the original source directory, create an account for each researcher. Enter each password twice when prompted.
   ```bash
   python3 scripts/lab_account.py add Song
   python3 scripts/lab_account.py add Kim
   ```
2. Start the web server once and keep the terminal open.
   ```bash
   bash start.sh --no-browser --port 18765
   ```

Creating the first account turns on lab sign-in for this workspace. Older results keep their original files and have no researcher assigned. Use `python3 scripts/lab_account.py list` to list accounts. Use `password NAME` to change a password or `disable NAME` to revoke access and keep the submission history. Back up the workspace and the server operator’s `~/.config/nanocofold/labs/` directory.

## At each desk

1. Open a terminal. Replace `USER@SERVER` with your SSH account and the server address.
   ```bash
   ssh -N -o ExitOnForwardFailure=yes -L 18765:127.0.0.1:18765 USER@SERVER
   ```
2. Leave that terminal open. Open **http://127.0.0.1:18765** and sign in with your NanoCoFold account.
3. Submit your analysis. Opening **Input & run** after acceptance takes you to step **1/4**. You can reuse the previous sequences. Review the input and give fresh consent if using the public MSA service. Switching tabs before submitting keeps your place.
4. In **Jobs & logs**, a queued job shows why it is waiting. It starts automatically when the GPU becomes available. You can cancel your own queued job.
5. In **Results database**, enter an exact account name under **Researcher** and choose **Search**. Leave the field empty for all lab results. Click a name to open the report, or the storage column heading to find the largest runs. Expand **Lab usage history** for submissions, including pending, cancelled and reused jobs. Each researcher has a separate input draft.

## Sequence transfer and storage

- Under **Run settings**, choosing the public MSA service sends input sequences to the external service to search for related sequences and requires consent. The verified cache option needs an existing alignment for the sequence. Local search needs a configured database. Boltz-2 follows the selected MSA settings.
- SQLite stores result summaries, file references and hashes. Eligible, identical requests can reference one saved result. Under **Explore results**, preview storage cleanup before confirming. Compact retention keeps metrics, sequences and final structures. Keep the full original if you need PAE, MSA or intermediate files for resuming. Cleanup is explicit.
- Before starting a real analysis, the queue checks the selected GPU. Admission requires at least 80% free device memory, utilization at or below 20%, and no compute-only or MPS process. Graphics and mixed graphics processes count toward the memory and utilization checks. When busy or unreadable, the queue checks again in about ten seconds. Waiting does not consume the run budget. External launches do not participate in the platform lock, and peak memory usage depends on the analysis.
