# Update an existing installation

The current release is **0.4.8rc10**. It updates the interface and guidance without rerunning saved analyses. Model weights and prediction engines do not need reinstallation.

## 1. Finish active jobs

In **Jobs & logs**, check that no job is queued or running. To stop a job, confirm **Cancel job** and wait for **Cancelled**. Then press **Ctrl+C** in the web server terminal. For a remote installation, do this on the server.

## 2. Get the new source

Use the original source folder. Keep `.nanocofold/` and the workspace in place. Save any source changes you made separately.

### Installed with Git

```bash
git pull --ff-only
```

If local changes or conflicts block the update, read the message. Keep your changes rather than forcing a reset.

### Installed from a ZIP

Download `NanoCoFold_public-0.4.8rc10.zip` from the [new release](https://github.com/kangk1204/NanoCoFold_public/releases/tag/v0.4.8rc10) and **extract it into a separate folder**. Run the following in the original installation folder. Replace the path on the first line with the folder you just extracted.

```bash
update_source="/path/to/extracted/NanoCoFold_public-0.4.8rc10"
rsync -a --exclude='.git/' --exclude='.nanocofold/' "$update_source/" ./
```

Do not add `--delete`. This copies the new source while retaining `.nanocofold/`, the workspace and results. If `rsync` is unavailable, ask the server administrator to copy the updated source.

## 3. Update the installed app and reopen it

In the same original source folder, run:

```bash
bash update.sh
bash start.sh --port 18765
```

Wait for **Update complete / 업데이트 완료** before starting the server. `git pull` alone does not update the installed program. `update.sh` updates the app at its saved installation location and does not run GPU analysis.

Reload the browser and check **v0.4.8rc10** at the bottom of the page. If the old interface remains, use **Ctrl+Shift+R** (**Cmd+Shift+R** on macOS). Reopen saved results through **Explore results → Select run**. Existing HTML files retain their original interface; export a new report to use the updated one.

## If the update stops

- Active job or web server: wait for it to finish, then repeat the command.
- Missing installation record: check that you are in the **original installation folder**, not the newly extracted source.
- New computer without an installation: follow the [installation guide](../README.md#1-prepare-and-install).
- Other failure: keep the message, resolve its cause and rerun `bash update.sh`. Keep the workspace and results.

This version distinguishes current generation, evaluation and selection counts and explains hotspot exclusions. Default computation sizes and scientific selection criteria are retained.
