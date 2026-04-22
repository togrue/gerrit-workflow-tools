# Migration plan: upstream-first push target + optional override

This document describes how to move from **required** `branch.<name>.gerritTarget` for `ger push` to **upstream-inferred** Gerrit push targets, with **`gerritTarget` as an optional override**, and **plain `git push` semantics** when the branch does not track the Gerrit remote.

## 1. Target behavior (summary)

| Situation | Expected behavior |
|-----------|-------------------|
| `@{upstream}` exists and its **remote name** equals `gerrit.remote` (default `origin`) | Infer Gerrit destination branch name from the upstream ref (e.g. `origin/dev` → `dev`). Push refspec uses `refs/for/<branch>` after `refs_for_push_branch_name` normalization. Merge-base / restack “onto remote” use the same logical target (see §3). |
| `@{upstream}` exists but tracks a **non-Gerrit** remote | **Vanilla push (B1):** run **`git push` with no extra arguments** (same as the user invoking plain Git—honors `push.default` and upstream). No `refs/for/...`, no Gerrit-only pre-checks (see §1.1). |
| User needs submit target ≠ inferred from upstream | Set **`branch.<name>.gerritTarget`** (override). It **wins** for push destination and for merge-base / remote-tip resolution when present (consistent with today’s precedence in `resolve_local_base_ref`). |
| No upstream and no `gerritTarget` | **A1:** Clear error for **`ger push`** and for **`resolve_local_base_ref`** / stack / merge-base—**no** silent fallback to local `main` / `master`. Actionable hints: `git branch -u`, `ger branch init`, or set override. |

### 1.1 Resolved design choices (voted)

| Choice | Decision |
|--------|----------|
| **A1** | **Single rule, no default branch guess:** If there is no `gerritTarget` and no usable `@{upstream}`, commands that need a base or a Gerrit destination **error**. Remove the current **`resolve_local_base_ref`** fallback to local **`main` / `master`**. |
| **B1** | **Vanilla `ger push` = plain Git:** Invoke **`git push`** with **no additional arguments**. Skip Gerrit-only steps: `refs/for/`, reviewers, ready/Change-Id pipeline, `gerrit.push.remotePolicy` / rebase-on-remote checks (see §5). |
| **C1** | **Detached `HEAD`:** **`ger push` errors** with a short hint to checkout a branch (or rely on a future explicit branch flag if added later). |
| **D1** | **`ger branch init` without `--target`:** Write **no** `branch.<name>.gerritTarget`; behavior relies on upstream + `gerrit.remote` inference. |

## 2. Current behavior (baseline)

- **`branch.<name>.gerritTarget`** is stored via `ger branch init --target` / `ger branch set-target` (`config.set_branch_config`).
- **`ger push`** (`cli_push.py`) **requires** `branch_gerrit_target`; otherwise raises (`No Gerrit target: run ger branch init`).
- **`refs_for_push_branch_name`** normalizes `origin/main` → `main` when the prefix matches `gerrit.remote`.
- **`resolve_local_base_ref`** order **today**: `gerritTarget` → `@{upstream}` → **`main` / `master`** (merge-base for stack, log, etc.). **After migration (A1):** drop the **`main` / `master`** tail—match the “no upstream, no override” error row in §1.
- **`resolve_rebase_onto_remote_ref`** prefers `gerritTarget` then remote-tracking candidates; else upstream-shaped candidates; else (today) `gerrit.remote`/main/master. **After migration:** align with the same effective destination as push / `resolve_local_base_ref`—no orphan `main`/`master` guesses when upstream and override are both absent.

No code path today switches `ger push` to vanilla Git based on remote type.

## 3. Centralize “effective Gerrit branch name” and “push mode”

Introduce one internal API (names illustrative) in `config.py` (or a small dedicated module if it grows):

1. **`resolve_upstream_parsed(cwd, branch?)`**  
   Returns `None` or `(remote_name, branch_segment)` from `git rev-parse --abbrev-ref @{upstream}` (handle `remote/with/slashes` by splitting on first `/` only—same idea as existing `resolve_local_base_ref`).

2. **`effective_gerrit_destination_branch(cwd, branch?)`**  
   - If `branch_gerrit_target` is set → use it (override).  
   - Else if upstream exists and `upstream.remote == gerrit_remote()` → infer branch segment from upstream (e.g. `dev` from `origin/dev`).  
   - Else → no Gerrit destination from config/upstream alone.

3. **`ger_push_mode(cwd, branch?)`** (or inline the rule)  
   - **Gerrit push**: override set **or** (upstream set **and** upstream remote == `gerrit.remote`).  
   - **Vanilla push**: upstream set **and** upstream remote != `gerrit.remote`.  
   - **Error**: no upstream, no override—**no** `main`/`master` fallback (**A1**).

Reuse **`refs_for_push_branch_name(cwd, target)`** for the final `refs/for/<name>` segment whenever mode is Gerrit.

**Consistency rule:** `resolve_local_base_ref` and `resolve_rebase_onto_remote_ref` should use the **same** effective destination as push when override is set; when only upstream applies, merge-base already largely matches—ensure **`resolve_rebase_onto_remote_ref`** does not require `gerritTarget` when upstream points at `gerrit.remote` (today it often works via upstream branch in candidates; verify after refactor).

## 4. File-level change checklist

| Area | Action |
|------|--------|
| `config.py` | Add resolution helpers (§3). Optionally rename docstrings: `gerritTarget` → “optional override for Gerrit destination branch.” Adjust `resolve_rebase_onto_remote_ref` / error messages so inference from upstream + `gerrit.remote` is first-class. |
| `cli_push.py` | Replace mandatory `branch_gerrit_target` with effective resolution + mode. **Vanilla (B1):** `git push` with **no extra args**. **Gerrit mode:** current pipeline (ready, Change-Id, reviewers, `refs/for/`, policy). Reject detached HEAD (**C1**). |
| `cli_branch.py` | **`ger branch init`**: make `--target` **optional**; if omitted, **do not set** `gerritTarget` (**D1**). **`ger branch show`**: show **inferred** target vs **override** (e.g. “Target (override): …” / “Inferred from upstream: origin/dev → dev”). |
| `set_branch_config` / docs | Keep `gerritTarget` key unchanged for compatibility; clarify semantics as **override only**. |
| `head_is_linear_on_remote_gerrit_target` | Ensure “remote tip” used for linearity checks matches effective Gerrit destination when inference is used (uses `resolve_rebase_onto_remote_ref`—update that function in lockstep). |

## 5. Edge cases to specify in code and tests

- **Upstream remote name** must be compared to **`gerrit.remote`**, not hard-coded `origin`.
- **`gerritTarget` as `origin/dev`**: existing normalization in `refs_for_push_branch_name` must still apply.
- **No upstream, override set**: Gerrit push should still work (override supplies destination).
- **No upstream, no override (A1):** error for `ger push`, `resolve_local_base_ref`, and any command that depended on the old `main`/`master` fallback—update tests and user-facing messages.
- **Vanilla push (B1):** **skip** `gerrit.push.remotePolicy`, `head_is_linear_on_remote_gerrit_target`, Gerrit HTTP, reviewers, and `refs/for/`; subprocess is **`git push`** only.
- **Detached HEAD (C1):** error before mode resolution (or immediately after detecting `HEAD`).
- **`ger branch init` without `--target` (D1):** no `gerritTarget` key written; user must have upstream (or set override later) for Gerrit push.

## 6. Tests

| Suite | Updates |
|-------|---------|
| `tests/test_config.py` | Inferred destination from `@{upstream}` when remote matches `gerrit.remote`; override wins; **A1:** no base when neither upstream nor override (expect error, not `main`/`master`); `resolve_rebase_onto_remote_ref` aligned with upstream when `gerritTarget` unset. |
| `tests/test_push.py` | Gerrit refspec `refs/for/...` when upstream is Gerrit remote; **B1:** non-Gerrit upstream → invoked command is **`git push`** only (no extra args); override overrides inference; **C1:** detached HEAD → error. |
| `tests/conftest.py` / fixtures | Branches that relied only on `gerritTarget` may get upstream set in fixture **or** keep explicit `gerritTarget` where testing override behavior. |
| Integration / CLI tests | `ger branch show` output; `ger branch init` without `--target` when upstream exists. |

Run full `pytest` after changes.

## 7. Documentation

Update at least:

- `docu/Configuration.md` — `gerritTarget` optional override; upstream + `gerrit.remote` default.
- `docu/README.md` — “required for push” → new rules.
- `docu/commands/push.md`, `docu/commands/branch.md` — prerequisites, vanilla vs Gerrit push.
- Root `README.md` if it duplicates branch setup.

## 8. Suggested implementation order

1. Add **`effective_gerrit_destination_branch`** + **`ger_push_mode`** (or equivalent) with unit tests only.  
2. Refactor **`resolve_rebase_onto_remote_ref`** (and if needed **`resolve_local_base_ref`**) to share the same inference—avoid drift.  
3. Change **`cli_push.py`** to use resolution + mode; add push tests.  
4. Update **`ger branch`** UX (`init` optional target, `show`).  
5. Documentation sweep; manual smoke: Gerrit push + fork remote push.

## 9. Backward compatibility

- Repos with **existing `branch.*.gerritTarget`** behave as today: override applies first.  
- No config key rename required for v1 of this migration.  
- Users who only ran `ger branch init --target` can later clear `gerritTarget` and rely on upstream if they want a single source of truth.

**Breaking change (A1):** Workflows that relied on **no upstream and no `gerritTarget`** but had a local **`main`/`master`** branch for stack / merge-base will need to **`git branch -u`** (or set `gerritTarget`) instead of relying on implicit default-branch resolution.

---

*End of migration plan.*
