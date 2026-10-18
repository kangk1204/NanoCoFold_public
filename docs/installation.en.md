# Installation and updates

**English** · [한국어](installation.md)

Use the public source commit or tag shared with you and its own README. GitHub's **Latest release** can be older than the current `main` source. Keep the exact commit or archive URL with your installation record; the package version alone does not identify a source revision.

## Prepare the computer

- Ubuntu x86_64 with glibc 2.34 or newer.
- A working NVIDIA driver, version 580 or newer. Some pinned engine environments use CUDA 13. The installer does not install or change the driver.
- For the first four-engine example: at least 16 GB GPU memory, 32 GB system RAM and 150 GiB free SSD space. Larger inputs and libraries need additional resources.
- Internet access and `sudo` for system packages. Run the installer as your ordinary user, not as root.

Check `nvidia-smi` before installing. Windows and macOS users normally connect to an installed Ubuntu server or open an extracted report ZIP.

The installer prepares its pinned `uv`, Python, scientific packages and selected model files. Conda, Docker, a separate CUDA Toolkit and Node.js are not prerequisites for this installation. Public-MSA examples do not need a large local MSA database. Public MSA requests send input sequences to an external service only after the requested consent; installation does not send research sequences.

## Install the first complex example

Extract the whole source ZIP, or use your Git checkout. Open a terminal in the folder containing `README.md`, `install.sh` and `start.sh`. Run these commands in order, waiting for `[설치 완료]` (installation complete) after each:

```bash
bash install.sh --profile complex
bash install.sh --profile colabfold
bash install.sh --profile opendde
```

`complex` prepares Boltz-2 and Chai-1; the other profiles prepare ColabFold and OpenDDE. The environments, models and results use `.nanocofold/workspace` by default. Packages and downloaded model files are checked against pinned versions and SHA256 records. A successful installation checks environment imports, required assets and basic CUDA operations; run the browser environment check and the public analysis next.

A single GPU is selected automatically. With multiple GPUs, find your assigned GPU's full UUID using `nvidia-smi -L` and add the same `--gpu GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` to **every** install command. Do not delete GPU lock files.

To choose another data disk, add the same `--workspace /your/data/NanoCoFold-workspace` to **every** install command. `start.sh` reuses the location and GPU recorded in `.nanocofold/install-location.json`.

## Choose environments for other purposes

For all supported purposes, run `bash install.sh --profile all`. Running `bash install.sh` without options also selects `all`.

| Profile | Environments prepared, in addition to the core application |
|---|---|
| `core` | None; CPU interface and mock workflows, without real prediction models |
| `complex` | Boltz-2, Chai-1 |
| `colabfold` | ColabFold |
| `opendde` | OpenDDE |
| `screen` | Antibody tools, Boltz-2, Chai-1 |
| `design` | Antibody tools, Boltz-2, Chai-1, RFantibody, BoltzGen |
| `mature` | Antibody tools, Boltz-2, Chai-1, ESM-2/AntiFold |
| `boltzgen` | Antibody tools, BoltzGen |
| `all` | All the environments above |

Check that every engine selected in the browser is installed. A purpose profile does not install every optional comparison engine. ESM-2 and AntiFold share an environment.

## Start the interface

In the same source folder on the installed Ubuntu computer:

```bash
bash start.sh --port 18765
```

Open the address printed after `NanoCoFold:`. Keep the terminal and computer running. If that port is occupied, use `bash start.sh --port 0` and open the newly printed port.

For a remote server, run `bash start.sh --no-browser --port 18765` there. In another terminal **on your own computer**, connect using your account and server address:

```bash
ssh -o ExitOnForwardFailure=yes -L 18765:127.0.0.1:18765 USER@SERVER
```

Keep both terminals open, then visit [http://127.0.0.1:18765](http://127.0.0.1:18765) on your computer. For port conflicts, custom server ports and the first analysis, follow the [README](../README.md#2-open-the-browser).

## Diagnose or retry installation

After an interrupted or failed installation, rerun the **same command with the same options** in the same source folder. Verified environments and downloads can be reused. Damaged or unexpected files are preserved and reported; inspect the error before changing them.

The workspace records attempts under `.platform/install-attempts/`, engine logs under `.platform/install-*.log`, and final profile status in `.platform/setup-<profile>.json` and `.platform/offline-readiness-<profile>.json`. Use those records and the terminal error when requesting help. Remove private sequences, paths and credentials from material posted in public issues.

`bash install.sh --check-host` checks requirements without installing or running analysis. On a bare Ubuntu system, missing Python or build tools can prevent this check from completing. The normal installer prepares system packages; `--skip-system-packages` is only for administrators who have already prepared them.

If `nvidia-smi` fails, resolve the driver problem with the administrator first. `Failed to initialize NVML: Driver/library version mismatch` can occur when the loaded kernel driver and installed libraries differ. A driver update may require an administrator-coordinated reboot after active work finishes. Repeating Python installation does not repair this condition.

## Update an existing installation

Wait until all queued/running jobs finish, then stop the web server with Ctrl+C. In the original source folder of a Git installation:

```bash
git pull --ff-only
bash update.sh
bash start.sh --port 18765
```

For a source ZIP, preserve local changes, replace the source with the intended new version, and run only the last two commands. Keep `.nanocofold/`, the workspace, inputs and results. For a checkout pinned to a specific commit, select the agreed new revision before `bash update.sh` instead of pulling a moving branch.

`update.sh` reuses the saved workspace and GPU, updates the application, and rejects active jobs or a running web server. It does not start predictions or reinstall the engines. Retry `bash update.sh` after interruption; startup remains blocked until the update completes. Reload the browser afterward and export reports again if you need the updated report interface. Follow the release notes for changes to engine environments.

Check the installed version with `.nanocofold/workspace/.venv-dev/bin/nanocofold --version`, replacing the workspace path if customized. Running `python -m nanocofold` from the source folder can instead import the checkout. See [update details](UPGRADING.md), [handoff records](collaborator-guide.en.md#6-record-the-handoff) and [validation history](VALIDATION.md).
