# Gerrit workflow tools (local)

**Alpha:** this project is early; behavior and UX will be refined.

## What this is

**`ger`** is a small command-line tool for **Gerrit stacked reviews**: multiple local commits, each with its own Change-Id, pushed as a chain of dependent changes. It helps you **see review state next to your commits** (comments, votes, CI) and **push the right slice** of your stack (ready boundary, target branch, reviewers) without living only in the web UI.

If you use Gerrit with single-commit changes only, or you already have a workflow you like, you may not need this.

## You might want this if

- You work with **multi-commit stacks** on Gerrit and want a **compact view of the chain** vs what is on the server (`ger log`, `ger show`, `ger comments`).
- You want **branch-local Gerrit settings** (target branch, reviewers) and **push** commands that understand your stack (`ger branch`, `ger push`).
- You **reorder or edit commits in the middle of a stack** and want helpers built for that workflow (`ger edit`, `ger sha` / `ger cid`).

Full command list, configuration, and setup: [docu/README.md](docu/README.md). For narrative “why” and scenarios, see [Gerrit-Workflow-Scenarios.md](Gerrit-Workflow-Scenarios.md).

## Install

Python installs a single CLI: **`ger`**. After install, run **`ger push`**, **`ger log`**, **`ger branch`**, and the other subcommands from any repository without activating a virtual environment.

### User environment (recommended)

Install once; the generated launcher embeds the interpreter used at install time. **Runtime does not use `uv`**—only the install step may.

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
| Windows (Git Bash) | same as above; add the `Scripts` folder that contains `ger.exe` |

Verify:

```bash
ger push --help
# or:  ger --help
```

### Bash completion (optional)

Optional bash tab completion is described in [docu/Completion.md](docu/Completion.md): `ger bash-completion` prints the `source` line; `ger bash-completion --install` adds it to `~/.bashrc`.

## Development (this repository)

Use [uv](https://docs.astral.sh/uv/) for a local venv and optional lockfile; this is **not** required for end users.

```bash
uv venv
uv sync
uv run pytest
```

Editable install inside the venv: `uv sync` already links the package. For ad-hoc runs without activating: `uv run ger`, `uv run pytest`.

## Design

See [docu/README.md](docu/README.md) for the full documentation index and command reference.

## Gerrit HTTP

Set **`gerrit.webUrl`** in git config to your Gerrit HTTPS base (scheme + host, optional port); it is required for commands that call the Gerrit REST API (`ger log`, `ger show`, `ger push --show-attributes`, …). API authentication uses **`gerrit.user`** with **`gerrit.password`** or **`gerrit.token`**. Details: [gcomments section](Gerrit-Workflow-Scenarios.md) in the design doc.
