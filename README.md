# Gerrit workflow tools (local)

**Alpha:** early project; behavior and UX will change.

## What this is

**`ger`** is a small CLI for **Gerrit stacked reviews**: multiple local commits, each with its own Change-Id, pushed as a chain of dependent changes. It helps you see review state next to your commits (comments, votes, CI) and push the right slice of your stack (ready boundary, target branch, reviewers) without relying only on the web UI.

If you use Gerrit with single-commit changes only, or you already have a workflow you like, you may not need this.

![Example `ger log` output: stack commits with patchset, verification, code-review, comments, and attention hints](docu/screenshots/ger_log_output.png)

## You might want this if

- You work with **multi-commit stacks** on Gerrit and want a **compact view of the chain** vs what is on the server (`ger log`, `ger show`).
- You want **branch-local Gerrit settings** (target branch, reviewers via `branch.*` git config) and **push** commands that understand your stack (`ger push`).
- You **reorder or edit commits in the middle of a stack** and want helpers built for that workflow (`ger edit`, `ger fix`, `ger rebase`).
- You **pick up a change** from another machine or a colleague, or a new patchset of it: `ger checkout 1234` puts it on a branch tracking its target branch, or updates the copy you already have.
- You **review other people's chains** from your editor: find them (`ger inbox`), check one out in a review worktree (`ger review`), comment, and vote (`ger comment`, `ger review --vote`).
- You **work through review comments and CI failures** on your own stack, or hand that to an agent (`ger show`, `ger ci`, `ger reply`, private notes with `ger comment --note`).
- You want all of that in **VS Code** too: the extension in [`vscode/`](vscode/README.md) does it through `ger serve`.

**Documentation:** [Getting started](docu/Getting-Started.md) · [Workflows, step by step](docu/flows.md) · [Reading `ger log`](docu/Reading-ger-log.md) · [Full index](docu/README.md)

### Change-Id vs Gerrit change identity

Each commit carries a footer **Change-Id** (`I…`), but Gerrit's unique key for a review is the **triplet** `project~branch~Change-Id` (plus the numeric change number for URLs). The same Change-Id can exist on multiple branches; `ger` resolves a bare Change-Id using your stack context (`gerrit.project` and `branch.*.gerritTarget` or upstream) and never silently picks the wrong branch. Use `ger resolve <changeish> --json` to inspect how an input was classified. Full rules, ambiguity handling, and JSON output: [docu/spec/change-and-commit-identifiers.md](docu/spec/change-and-commit-identifiers.md).

## Install

One command, no clone. It needs git, Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) (or pipx):

```bash
uv tool install https://github.com/togrue/gerrit-workflow-tools/releases/latest/download/ger.tar.gz
# or:  pipx install https://github.com/togrue/gerrit-workflow-tools/releases/latest/download/ger.tar.gz
```

Then, once per machine:

```bash
ger setup             # asks for your Gerrit address and credentials
ger vscode install    # optional: the VS Code extension that came with this ger
```

To update, run the install command again with `--force`, then `ger vscode install` again.

**Starting from VS Code instead:** install [`ger-vscode.vsix`](https://github.com/togrue/gerrit-workflow-tools/releases/latest/download/ger-vscode.vsix) (*Extensions: Install from VSIX…*). Its *Review chains* view offers **Install ger** when ger is missing, and **Run ger setup** after that.

If `ger` is not found afterwards, `uv tool update-shell` (or `pipx ensurepath`) puts its directory on `PATH`. Bash completion, the Change-Id hook, branch settings, and verification: **[Getting started](docu/Getting-Started.md)**.

<details>
<summary>Other ways to install</summary>

- The development version: `uv tool install git+https://github.com/togrue/gerrit-workflow-tools`. It carries no extension; build that with `npm run package` in `vscode/`.
- From a clone: `uv tool install .` or `pipx install .`
- A release built locally: `python scripts/build_release.py` writes the same files a release publishes to `dist/release/`.

</details>

<details>
<summary><strong>Development</strong></summary>

Contributors use [uv](https://docs.astral.sh/uv/) (`uv sync`, `uv run pytest`). This is **not** required to install and run **`ger`** as an end user.

### VS Code

`ger serve` is a local HTTP service over the same core; the extension in [`vscode/`](vscode/README.md)
uses it to show your review chains, the local stack, comments, diffs and CI findings in the editor,
and to reorder the stack by drag and drop. API: [docu/spec/commands/serve.md](docu/spec/commands/serve.md).

### Testing

Quick start: `uv sync` then `uv run pytest -q` (unit only; integration tests are opt-in). The extension has its own: `npm test` and `npm run e2e` in `vscode/` (the e2e run drives the real `ger serve` over a fake Gerrit).

### Integration tests (optional)

End-to-end tests against a real Gerrit in Docker are under [tests/integration/README.md](tests/integration/README.md). Default `pytest` **skips** them (`--ignore=tests/integration` in `pyproject.toml`). Install deps with `uv sync --group integration` and run `uv run --group integration pytest tests/integration` (or `scripts/run_integration_tests.sh`).

</details>
