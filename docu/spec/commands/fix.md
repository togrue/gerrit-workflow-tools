# `ger fix`

| | |
|--|--|
| **Status** | Implemented |
| **Module** | `src/gerrit_workflow_tools/cli_fix.py` |
| **Requires** | Git; Gerrit lookup only when the target is addressed by number ([ADR-0003](../../adr/0003-ger-fix-targets-the-local-stack.md)) |

Create a **fixup** commit: `git commit --fixup=<target>`.

---

## Usage

```
ger fix [options] REF_OR_CHANGE
```

`REF_OR_CHANGE` — commit-ish, `refs/changes/…`, numeric change id, or Change-Id (`I…`).

### Change resolution

`REF_OR_CHANGE` is a **changeish** parsed by **`core/changeish.py`** — same grammar as `ger show` (git ref default; `change:<n>` for a Gerrit change number; triplet/URL for an exact pick). Full grammar: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

It is then resolved **to a commit on the local stack**, and never fetched ([ADR-0003](../../adr/0003-ger-fix-targets-the-local-stack.md)). A change that exists on Gerrit but not on your stack exits `3`; rebase or cherry-pick it first.

| Input | Network |
|-------|---------|
| git ref, Change-Id, triplet | none — matched against the stack directly |
| `refs/changes/…` already in the repo | none — it is just a git ref |
| `change:<n>`, URL, `q:` query, unknown `refs/changes/…` | one Gerrit round trip to learn the Change-Id |

Change-Id matching is case-insensitive, as the grammar accepts either case. Ambiguity means **several stack commits share one Change-Id** and exits `4` — a Change-Id appearing on several Gerrit branches is not ambiguous here, because Gerrit is not consulted. With `--json`, a `resolution` block appears only when Gerrit resolution actually ran ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)).

---

## Options

| Option | Description |
|--------|-------------|
| `-a`, `--all` | Stage all tracked modifications (`git commit -a`) |
| `--no-verify` | Pass `-n` to `git commit` (skip hooks) |
| `--debug-log`, `-v` | Standard helpers |

Default: only **staged** changes are committed. On a TTY with an empty index, prompts to stage tracked modifications (`y` / `n` / `d` for diff).

---

## Behavior

1. Resolve `REF_OR_CHANGE` to a commit SHA on the local stack (see Change resolution above). Never fetches.
2. If the index has no staged changes and `-a` was not passed:
   - **Interactive TTY** and there are unstaged modifications to tracked files: prompt `[y/n/d]` — `y` runs `git add -u` and continues; `d` prints the unstaged diff to stderr and re-prompts; `n` / empty / interrupt declines.
   - Otherwise (non-TTY, declined, or nothing to stage): exit `1` with a hint to stage edits or use `-a`.
3. Run `git commit --fixup=<sha>` (honours `--no-verify` and `-a`).

---

## Exit codes

Semantic codes come from the shared `ExitCode` table ([exit-codes.md](../exit-codes.md)); `3` is **not** a Gerrit API error, as this table previously claimed.

| Code | Meaning |
|------|---------|
| `0` | Fixup commit created successfully |
| `1` | Fixup rejected (no staged changes) |
| `2` | Usage error (bad arguments) |
| `3` | `NOT_FOUND` — no commit on the local stack matches |
| `4` | `AMBIGUOUS` — several stack commits share the Change-Id |
| `5` | `GERRIT` — Gerrit answered badly, or not at all |
| `6` | `CONFIG` — required git configuration missing |
| `7` | `GIT` — a git command failed |

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger edit`](edit.md)
- [`ger sha`](sha-change-id.md)
