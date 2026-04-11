# git gbranch

**Status:** Implemented

Manage branch-local Gerrit metadata: target review branch, default reviewers, and push mode. Settings are stored in `.git/config` under `branch.<name>.*` keys.

Run `git gbranch init --target <branch>` once per local branch before using `git gpush`.

---

## Usage

```
git gbranch <subcommand> [options]
```

---

## Subcommands

### `show`

Print current branch's Gerrit metadata.

```bash
git gbranch show
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
git gbranch init --target <branch> [--reviewers <list>] [--push-mode <mode>]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--target BRANCH` | (required) | Gerrit target review branch |
| `--reviewers LIST` | (none) | Comma-separated list of reviewer accounts |
| `--push-mode MODE` | `ready` | Push mode: `ready` or `all` |

---

### `set-target`

Update `gerritTarget` for the current branch.

```bash
git gbranch set-target <branch>
```

---

### `set-reviewers`

Update `gerritReviewers` for the current branch.

```bash
git gbranch set-reviewers alice,bob
```

---

### `set-push-mode`

Update `gerritPushMode` for the current branch.

```bash
git gbranch set-push-mode ready
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

- [`git gpush`](gpush.md) — uses the target and push mode set here
- [`git gready`](gready.md) — reads `gerritPushMode` and stop patterns
