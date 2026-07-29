# Gerrit workflow tools (local)

**Alpha:** early project; behavior and UX will change.

## What this is

**`ger`** is a small CLI for **Gerrit stacked reviews**: multiple local commits, each with its own Change-Id, pushed as a chain of dependent changes. It helps you see review state next to your commits (comments, votes, CI) and push the right slice of your stack (ready boundary, target branch, reviewers) without relying only on the web UI.

If you use Gerrit with single-commit changes only, or you already have a workflow you like, you may not need this.

![Example `ger log` output: stack commits with patchset, verification, code-review, comments, and attention hints](.screenshots\ger_log_output.png)

## You might want this if

- You work with **multi-commit stacks** on Gerrit and want a **compact view of the chain** vs what is on the server (`ger log`, `ger show`).
- You want **branch-local Gerrit settings** (target branch, reviewers via `branch.*` git config) and **push** commands that understand your stack (`ger push`).
- You **reorder or edit commits in the middle of a stack** and want helpers built for that workflow (`ger edit`, `ger sha` / `ger change-id`).

**Documentation:** [Getting started](docu/Getting-Started.md) · [Reading `ger log`](docu/Reading-ger-log.md) · [Full index](docu/README.md)

### Change-Id vs Gerrit change identity

Each commit carries a footer **Change-Id** (`I…`), but Gerrit's unique key for a review is the **triplet** `project~branch~Change-Id` (plus the numeric change number for URLs). The same Change-Id can exist on multiple branches; `ger` resolves a bare Change-Id using your stack context (`gerrit.project` and `branch.*.gerritTarget` or upstream) and never silently picks the wrong branch. Use `ger resolve <changeish> --json` to inspect how an input was classified. Full rules, ambiguity handling, and JSON output: [docu/spec/change-and-commit-identifiers.md](docu/spec/change-and-commit-identifiers.md).

## Install

From a clone of this repo:

```bash
pip install --user .
# or:  pipx install .
```

Ensure the install directory is on `PATH`, then configure Gerrit:

```bash
ger setup
ger --help
```

For PATH details, bash completion, the Change-Id hook, branch settings, and verification: **[Getting started](docu/Getting-Started.md)**.

<details>
<summary><strong>Development</strong></summary>

Contributors use [uv](https://docs.astral.sh/uv/) (`uv sync`, `uv run pytest`). This is **not** required to install and run **`ger`** as an end user.

### Testing

Quick start: `uv sync` then `uv run pytest -q` (unit only; integration tests are opt-in).

### Integration tests (optional)

End-to-end tests against a real Gerrit in Docker are under [tests/integration/README.md](tests/integration/README.md). Default `pytest` **skips** them (`--ignore=tests/integration` in `pyproject.toml`). Install deps with `uv sync --group integration` and run `python scripts/run_integration.py` or `pytest tests/integration`.

</details>
