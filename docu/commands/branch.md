# ger branch

**Status:** Implemented

Manage branch-local Gerrit metadata: target review branch, default reviewers, and push mode. Settings are stored in `.git/config` under `branch.<name>.*` keys.

Run `ger branch init --target <branch>` once per local branch before using `ger push`.

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

Output:
```
Branch: feature/my-work
Target branch: main
Reviewers: alice,bob
Push mode: ready
```

---

### `init`

Set branch-local Gerrit config (non-interactive).

```bash
ger branch init --target <branch> [--reviewers <list>] [--push-mode <mode>]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--target BRANCH` | (required) | Gerrit target review branch |
| `--reviewers LIST` | (none) | Comma-separated list of Gerrit reviewer accounts |
| `--push-mode MODE` | `ready` | Push mode: `ready` or `all` |

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

### `set-push-mode`

Update `gerritPushMode` for the current branch.

```bash
ger branch set-push-mode ready
```

---

## Global option

| Option | Description |
|--------|-------------|
| `-v`, `--verbose` | Log git commands and config writes to stderr |

---

## Git config keys written

```ini
[branch "feature/my-work"]
    gerritTarget = main
    gerritReviewers = alice,bob
    gerritPushMode = ready
```

---

## See also

- [`ger push`](push.md) — uses the target, push mode, and stop patterns from `gbranch` / config
