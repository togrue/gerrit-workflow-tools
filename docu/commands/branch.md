# ger branch

**Status:** Implemented

Manage branch-local Gerrit metadata: target review branch and default reviewers. Settings are stored in `.git/config` under `branch.<name>.*` keys.

Configure an optional Gerrit target override and/or reviewers. If you rely on upstream inference for `ger push`, you can skip `--target` once `@{upstream}` tracks `gerrit.remote`.

---

## Usage

```
ger branch <subcommand> [options]
```

---

## Subcommands

### `show`

Print current branch's Gerrit metadata.

```bash
ger branch show
```

Example output (fields vary with config):

```
Branch configuration

  Branch          feature/my-work
  Target (override)  main
  Push mode       Gerrit (refs/for/…)
  Reviewers       alice,bob
```

With no override but an upstream on `origin`, **Inferred target** shows `origin/main → main` and **Push mode** is **Gerrit**. When upstream tracks another remote, **Push mode** is **plain git push**.

---

### `init`

Set branch-local Gerrit config (non-interactive).

```bash
ger branch init [--target <branch>] [--reviewers <list>]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--target BRANCH` | (optional) | Override Gerrit destination branch name (e.g. `main` or `dev`). If omitted, `ger push` uses upstream when it tracks `gerrit.remote`. |
| `--reviewers LIST` | (none) | Comma-separated list of Gerrit reviewer accounts |

If both `--target` and `--reviewers` are omitted, the command succeeds after printing a short hint (nothing is written to config).

---

### `set-target`

Update `gerritTarget` for the current branch.

```bash
ger branch set-target <branch>
```

---

### `set-reviewers`

Update `gerritReviewers` for the current branch.

```bash
ger branch set-reviewers alice,bob
```

---

## Global option

| Option | Description |
|--------|-------------|
| `--debug-log` | Log git commands and config writes to stderr. Repeat for more detail (git subprocesses and API bodies). |
| `-v`, `--verbose` | Reserved for richer command output in a future release (currently no effect). |

---

## Git config keys written

```ini
[branch "feature/my-work"]
    gerritTarget = main
    gerritReviewers = alice,bob
```

---

## Troubleshooting: gerritTarget missing locally

Merge-base and push logic need the target to resolve with `git rev-parse`—as a local branch or (common case) as `refs/remotes/<remote>/<branch>` after you **fetch** from `gerrit.remote` (default `origin`). Run `git fetch origin` or `git fetch origin <branch>` before `ger push` if the destination exists on the server but you have not fetched it yet.

Use the **short branch name** for `--target` / `gerritTarget` (e.g. `dev`). Do **not** create a local branch whose name looks like `origin/dev`; that pattern is for remote-tracking refs created by fetch, not a branch you should add manually under `refs/heads/`.

---

## See also

- [`ger push`](push.md) — uses the target, reviewers, and stop patterns from `gbranch` / config
