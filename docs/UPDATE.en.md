# Update an existing installation

Update in the original installation folder to keep your workspace, prediction engines and saved results. The current installer is **v0.4.8-install.8**. Its compute package reports **0.4.8rc12**.

## 1. Finish active jobs

In **Jobs & logs**, wait until no jobs are queued or running. If you choose to cancel one, wait for **Cancelled**. Then press **Ctrl+C** in the web server terminal. For a remote installation, do this on the server.

## 2. Get the source

Choose the method you originally used. Save any local source changes first.

**Git installation.** Run the commands below in the original source folder. The last command should print `v0.4.8-install.8`. For a shared installation, [set up the administrator and result access](shared-lab.en.md) after updating.

```bash
git fetch origin tag v0.4.8-install.8
git switch --detach v0.4.8-install.8
git describe --tags --exact-match
```

If your installation follows the `main` branch and you want to keep it there, use these commands instead.

```bash
git pull --ff-only
git rev-parse HEAD
```

Keep the tag or commit with your run notes. If Git reports an error, resolve it before continuing.

**ZIP installation.** Download the [v0.4.8-install.8 source ZIP](https://github.com/kangk1204/NanoCoFold_public/archive/refs/tags/v0.4.8-install.8.zip) and extract it into a **separate folder**. If your collaborator shared a specific revision, use **Code → Download ZIP** on that revision’s source page.

In the **original installation folder**, run the commands below. Replace the first path with the newly extracted folder containing `update.sh`. Keep the source link with your run notes.

```bash
update_source="/path/to/extracted/NanoCoFold_public-0.4.8-install.8"
rsync -a --exclude='.git/' --exclude='.nanocofold/' "$update_source/" ./
```

Continue once the copy succeeds. Keep the existing `.nanocofold/` and workspace. Do not add `--delete`.

## 3. Update and reopen the app

In the original folder, run the first command. Wait for **Update complete**, then run the second:

```bash
bash update.sh
bash start.sh --port 18765
```

Open the address printed after **NanoCoFold:** and reload the browser. To check the update without starting a job, choose **Evaluate nanobody variants → Load public example for this purpose → Review input & continue**. Under **Variant generation**, choose **Random multi-residue CDR substitutions**. You should see **CDR3 only** and **Custom CDR ranges**. If you still see the old interface, press **Ctrl+Shift+R** (**Cmd+Shift+R** on macOS).

Reopen a saved run via **Explore results → Select run**. To use the updated interface for an old report, export a new copy. If the updater cannot find the installation, return to the folder you used the first time. For a new computer, use the [installation instructions](../README.md#1-prepare-and-install).
