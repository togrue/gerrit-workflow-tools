# Gerrit workflow tools (local)

Python CLIs installed as **`git-gstack`**, **`git-gready`**, **`git-gchangeid-check`**, **`git-gbranch`**, **`git-gpush`**, and **`git-gedit`**. Git dispatches `git <name>` by running an executable `git-<name>` on your `PATH`, so after install you can run **`git gstack`** from any repository without a virtual environment or `uv`.

## Install into your user environment (recommended)

Install once; the generated launchers embed the interpreter used at install time. **Runtime does not use `uv`**—only the install step may.

**From a clone of this repo** (editable or regular install):

```bash
# pip (user site-packages + user Scripts/bin)
pip install --user .

# or uv as an installer only (same outcome: scripts on PATH)
uv pip install --user .
```

**Or isolated tool env** ([pipx](https://pypa.github.io/pipx/)):

```bash
pipx install .
# or from directory:  pipx install /path/to/workflow-optimization
```

Then ensure your **user binary directory** is on `PATH`:

| Platform | Typical path |
|----------|----------------|
| Linux / macOS | `~/.local/bin` |
| Windows (pip `--user`) | `%APPDATA%\Python\Python3x\Scripts` or `Python3x\Scripts` under your user profile (see `python -m site --user-site` and parent `Scripts`) |
| Windows (Git Bash) | same as above; add the `Scripts` folder that contains `git-gstack.exe` |

Verify:

```bash
git gstack --help
# or:  git-gstack --help
```

## Development (this repository)

Use [uv](https://docs.astral.sh/uv/) for a local venv and optional lockfile; this is **not** required for end users.

```bash
uv venv
uv sync
uv run pytest
```

Editable install inside the venv: `uv sync` already links the package. For ad-hoc runs without activating: `uv run git-gstack`, `uv run pytest`.

## Design

See [Gerrit-Workflow-Tools.md](Gerrit-Workflow-Tools.md).

## Gerrit HTTP (`git gcomments`)

Set **`gerrit.webUrl`** in git config to your Gerrit HTTPS base (scheme + host, optional port); it is required for `git gcomments`. API authentication uses **`gerrit.user`** with **`gerrit.password`** or **`gerrit.token`**. Details: [gcomments section](Gerrit-Workflow-Tools.md#git-gcomments) in the design doc.
