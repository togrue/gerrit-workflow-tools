# Gerrit workflow tools (local)

**Alpha:** early project; behavior and UX will change.

## What this is

**`ger`** is a small CLI for **Gerrit stacked reviews**: multiple local commits, each with its own Change-Id, pushed as a chain of dependent changes. It helps you see review state next to your commits (comments, votes, CI) and push the right slice of your stack (ready boundary, target branch, reviewers) without relying only on the web UI.

If you use Gerrit with single-commit changes only, or you already have a workflow you like, you may not need this.

## You might want this if

- You work with **multi-commit stacks** on Gerrit and want a **compact view of the chain** vs what is on the server (`ger log`, `ger show`).
- You want **branch-local Gerrit settings** (target branch, reviewers via `branch.*` git config) and **push** commands that understand your stack (`ger push`).
- You **reorder or edit commits in the middle of a stack** and want helpers built for that workflow (`ger edit`, `ger sha` / `ger change-id`).

**Documentation** (specification, commands, configuration): [docu/SPEC.md](docu/SPEC.md) · [docu/README.md](docu/README.md).

## Install

Python installs a single CLI: **`ger`**. After install, run **`ger`** subcommands from any repository without activating a virtual environment.

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
|----------|--------------|
| Linux / macOS | `~/.local/bin` |
| Windows (pip `--user`) | `%APPDATA%\Python\Python3x\Scripts` or `Python3x\Scripts` under your user profile (see `python -m site --user-site` and parent `Scripts`) |
| Windows (Git Bash) | same as above; add the `Scripts` folder that contains `ger.exe` |

Verify:

```bash
ger push --help
# or:  ger --help
```

### Configure Gerrit defaults

Before daily use, review the supported Git config keys in [docu/Configuration.md](docu/Configuration.md), especially:

- `gerrit.webUrl`
- default reviewers (`branch.<name>.gerritReviewers`)
- ready-boundary stop patterns (`gerrit.stopPattern`)

### Bash completion (optional)

See [docu/Completion.md](docu/Completion.md): `ger bash-completion` prints the `source` line; `ger bash-completion --install` adds it to `~/.bashrc`.

### First-time setup: Change-Id hook

Gerrit Change-Ids are added automatically by the `commit-msg` hook. Install this once per clone:

```bash
# Uses gerrit.webUrl from your git config
# Example: git config --global gerrit.webUrl https://gerrit.example.com
HOOK_URL="$(git config --get gerrit.webUrl)/tools/hooks/commit-msg"

curl -sfL -o .git/hooks/commit-msg "$HOOK_URL" \
  || wget -q -O .git/hooks/commit-msg "$HOOK_URL"

chmod +x .git/hooks/commit-msg
```

If `gerrit.webUrl` is not set yet, configure it first (see [docu/Configuration.md](docu/Configuration.md)).

After installing the hook, run:

```bash
ger change-id --check-duplicates
```

## Development

Contributors use [uv](https://docs.astral.sh/uv/) (`uv sync`, `uv run pytest`). This is **not** required to install and run **`ger`** as an end user. See [docu/Howto_Test.md](docu/Howto_Test.md).

### Testing

See [docu/Howto_Test.md](docu/Howto_Test.md). Quick start: `uv run pytest -q` (unit only).

### Integration tests (optional)

End-to-end tests against a real Gerrit in Docker are under [tests/integration/README.md](tests/integration/README.md). Default `pytest` **skips** them (`--ignore=tests/integration` in `pyproject.toml`). Install deps with `uv sync --group integration` and run `python scripts/run_integration.py` or `pytest tests/integration`.
