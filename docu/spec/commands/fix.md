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

`REF_OR_CHANGE` — a commit in the current stack, named by any changeish: git revision (a bare number is one), `change:<n>`, `refs/changes/…`, or Change-Id (`I…`).

### Change resolution

`REF_OR_CHANGE` is a **changeish** parsed by **`core/changeish.py`** — same grammar as `ger show` (git ref default; `change:<n>` for a Gerrit change number; triplet/URL for an exact pick). Full grammar: [change-and-commit-identifiers.md](../change-and-commit-identifiers.md).

It is then resolved by `resolve_stack_changeish` — the same path as [`ger edit`](edit.md),
[`ger reword`](edit.md) and [`ger rebase`](rebase.md) — **to a commit on the local stack**, and
never fetched ([ADR-0003](../../adr/0003-ger-fix-targets-the-local-stack.md)). A change that
exists on Gerrit but not on your stack exits `3`; rebase or cherry-pick it first. A plain git
ref outside the stack is rejected for the same reason: `git rebase --autosquash` over the stack
could not place the resulting `fixup!`.

A triplet or `change:<n>` naming a **sibling** on another branch (`proj~dev~I…` from a branch
pushing to `main`) still maps to the stack commit carrying that Change-Id, which is the only one
you could fix; a stderr note says that commit targets `main`, not `dev`.

| Input | Network |
|-------|---------|
| git ref, Change-Id, triplet | none — matched against the stack directly |
| `refs/changes/…` already in the repo | none — it is just a git ref |
| `change:<n>`, URL, `q:` query, unknown `refs/changes/…` | one Gerrit round trip to learn the Change-Id |

Resolution aside, whether Gerrit closed the target's change costs at most one more request, and none inside the trust window (Behavior 1).

Change-Id matching is case-insensitive, as the grammar accepts either case. Ambiguity means **several stack commits share one Change-Id** and exits `4` — a Change-Id appearing on several Gerrit branches is not ambiguous here, because Gerrit is not consulted. With `--json`, a `resolution` block appears only when Gerrit resolution actually ran ([§5](../change-and-commit-identifiers.md#5-machine-readable-resolution-for-automation)).

---

## Options

| Option | Description |
|--------|-------------|
| `-a`, `--all` | Stage all tracked modifications (`git commit -a`) |
| `--no-verify` | Pass `-n` to `git commit` (skip hooks) |
| `--json` | `{"fixup_sha", "resolution"?}` |
| `--ai`, `--format {human,ai}` | The text output, colour off ([Output formats](../../SPEC.md#output-formats)) |
| `--debug-log` | Standard helpers (`-v` is accepted, does nothing, and is not shown) |

Default: only **staged** changes are committed. On a TTY with an empty index, and without `--json` or `--ai`, prompts to stage tracked modifications (`y` / `n` / `d` for diff).

---

## Behavior

0. **Unresolved conflicts refuse.** When the index has unmerged paths (a rebase, merge or pick stopped on a conflict), `-a` or a plain commit would commit the conflict markers. `ger fix` exits `2` (`USAGE`) with `the index has unresolved conflicts, and a fixup would commit them;` and the shared next step (resolve, `git add`, `ger rebase --continue`, or `--skip` / `--abort`), before anything is resolved or staged.

   **A rebase stopped cleanly (an `edit` stop) does not refuse.** The fixup is committed at the stop, the rest of the rebase replays on top of it, and it folds into its target like any other fixup: [ADR-0003](../../adr/0003-ger-fix-targets-the-local-stack.md) makes `ger fix` a shortcut for a *later* autosquash, and nothing in an edit stop makes that later step wrong. The target resolves against the stack as rebased so far. The next-step line then starts with finishing the rebase (`ger rebase --continue`, which refuses over unstaged changes rather than letting git misreport them). To change the stopped commit itself, `git commit --amend` is the tool, not a fixup.
1. Resolve `REF_OR_CHANGE` to a commit SHA on the local stack (see Change resolution above). Never fetches.
   **A change Gerrit already closed refuses**, exit `2` (`USAGE`), with the same messages as [`ger edit`](edit.md#behavior): merged → `git fetch`, then `ger rebase`; abandoned → restore it on Gerrit, or `ger edit --drop` the commit. Git alone cannot tell, since a change submitted after your last `git fetch` is still on the local stack. Gerrit is asked at most once, for the push branch's change carrying the target's Change-Id (`ChangeApi.on_push_branch`), and not at all while the cache has it inside the trust window. When Gerrit cannot be asked (no `ger.webUrl`, no credentials, unreachable), the git-only check stands and the fixup is made: a debug log says why, no error.
2. If the index has no staged changes and `-a` was not passed:
   - **Interactive TTY** (human output) and there are unstaged modifications to tracked files: prompt `[y/n/d]` — `y` runs `git add -u` and continues; `d` prints the unstaged diff to stderr and re-prompts; `n` / empty / interrupt declines.
   - Otherwise (non-TTY, `--json` or `--ai`, declined, or nothing to stage): exit `2` (`USAGE`) with a hint to stage edits or use `-a`.
3. When the target is itself a fixup, aim at the stack commit it belongs to, so a fixup of a fixup never reads `fixup! fixup! …`.
4. Run `git commit --fixup=<sha>` (honours `--no-verify` and `-a`), then print the new commit's short SHA and subject (text mode), and on stderr the same advice `ger log` and `ger push` give for the commit: `next: squash it with `ger rebase --autosquash``.

---

## Exit codes

`0` when the command completed, non-zero when something went wrong ([exit-codes.md](../exit-codes.md)); the non-zero value is not part of the contract.

---

## See also

- [change-and-commit-identifiers.md](../change-and-commit-identifiers.md) — changeish grammar and resolution contract
- [`ger edit`](edit.md)
- [`ger sha`](sha-change-id.md)
