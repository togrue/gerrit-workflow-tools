# Version 1 — actionable todos

Work **top to bottom** (simplest first). Each item lists **effort**, **spec status**, **steps**, and **done when**.

**Source:** [Version 1 Scope.md](Version%201%20Scope.md) · **Behavior specs:** [SPEC.md](SPEC.md) · [spec/commands/](spec/commands/)

| Effort | Meaning |
|--------|---------|
| **S** | Under ~1 hour (often docs or a few lines) |
| **M** | Half day or less |
| **L** | Multi-day / new command surface |

**Spec legend**

| Status | Meaning |
|--------|---------|
| ✅ | Enough to implement (see linked spec section) |
| 📝 | Spec gap — write/extend spec **before** coding |
| 🔶 | Partial — examples exist but missing flags, exit codes, or edge cases |

---

## Spec gaps summary (fix before or with implementation)

| Area | Gap | Where to write |
|------|-----|----------------|
| Onboarding | No copy-paste commit-msg hook steps for end users | Root [README.md](../README.md) + [docu/README.md](README.md) |
| `ger log -v` | What counts as “non-clean”; URL line rules vs `--url` | [spec/commands/log.md](spec/commands/log.md) |
| `ger log --unresolved-comments` | Flag behavior, output shape, `--json`, cost (N API calls?) | [spec/commands/log.md](spec/commands/log.md) |
| Comment chains | Shared model + API helper contract (used by show + log) | [architecture.md](architecture.md) + [spec/commands/show.md](spec/commands/show.md) |
| `ger show` Change-Id-only | Message source: Gerrit `commit` field vs “not available” | [spec/commands/show.md](spec/commands/show.md) |
| `ger fix` safety | Exit codes, exact error text, `--force` for conflict probe | [spec/commands/fix.md](spec/commands/fix.md) |
| `ger push --review` | Interactive step order vs `-i` / `--reviewers` | [spec/commands/push.md](spec/commands/push.md) |
| `ger assign` | Full command: flags, targets, `--dry-run`, branch move API | [spec/commands/assign.md](spec/commands/assign.md) |
| Stale docs | `push.md` still says confirmation UX is “partial” — shipped | [spec/commands/push.md](spec/commands/push.md) |


---

## 5. `ger show`: print `(no unresolved comments)` when clean (S) — ✅

**Why:** Small UX win; example already in [spec/commands/show.md](spec/commands/show.md).

**Steps:**

1. In `cli_show.py`, after fetching comments, if zero unresolved chains: print section header `Unresolved comments:` and indented `  (no unresolved comments)` (match spec example).
2. Unit test in `tests/test_show.py` (mock file map / empty unresolved).

**Done when:** `ger show` on a clean change shows the explicit empty state.

---

## 6. Spec: `ger fix` merged + conflict checks (S) — 📝

**Why:** [spec/commands/fix.md](spec/commands/fix.md) lists checks as “Open” with no behavior detail.

**Steps:**

1. **Merged:** If target resolves to a Gerrit change with `status == MERGED`, exit `1` (or `2` — pick one, document), message: suggest `ger show` / new change. Local SHA-only targets: skip Gerrit check.
2. **Conflict:** Define probe (e.g. `git merge-tree` / dry-run fixup rebase) and whether `--force` warns vs hard-fails.
3. Document exit codes and stderr wording in [fix.md](spec/commands/fix.md).

**Done when:** fix.md has testable acceptance criteria; no “verify before release” ambiguity.

---

## 7. `ger fix`: reject merged Gerrit target (S–M) — depends on §6

**Why:** Prevents fixup against already-integrated changes.

**Steps:**

1. After `resolve_gerrit_change` in `cli_fix.py`, read `status`; fail per §6 spec.
2. Tests: unit test with mocked change dict; optional integration seed merged change.

**Done when:** `ger fix I…` on merged change fails with documented message and exit code.

---

## 8. Implement `ger log -v` selective URLs (S–M) — depends on §4

**Why:** Closes scope log item after spec is clear.

**Steps:**

1. Implement policy from log.md § Verbose URL policy in `cli_log.py` (reuse `attention_reasons` / `determine_attention`).
2. Extend `tests/test_log.py` for verbose + clean vs attention row.

**Done when:** `-v` does not print URLs for clean commits; integration or unit tests lock behavior.

---

## 9. Spec: comment chain model + shared helper (M) — 📝

**Why:** Blocks show chain fix, show formatting, log `--unresolved-comments`, and consistent attention counts.

**Steps:**

1. In [architecture.md](architecture.md), add **Comment chains**:
   - Chain id / grouping rule (Gerrit `in_reply_to` / thread root)
   - **Resolved** iff last comment in chain has `unresolved: false`
   - `collect_unresolved_comment_chains(file_map) -> list[Chain]` (name TBD)
2. In [show.md](spec/commands/show.md), reference helper; move “current bug” to **Behavior (target)** once agreed.
3. Note impact on `comments_unresolved` count in log attention (integer = chain count, not raw comment count? — **decide in spec**).

**Done when:** One helper contract documented; show + log specs point to it.

---

## 10. `ger show`: chain-level unresolved detection (M) — depends on §9

**Why:** Scope + spec bug: per-comment `unresolved: true` is wrong for threads.

**Steps:**

1. Implement helper from §9; switch `cli_show` off `collect_unresolved_comments` (or make thin wrapper).
2. Port/update tests touching `collect_unresolved_comments` in `tests/test_show.py` / `gerrit_change_status` tests.
3. Align `determine_attention` / `comments_unresolved` if spec says chain count (may affect log summary — document in commit).

**Done when:** Resolved thread with unresolved-looking middle replies does not list as open.

---

## 11. `ger show`: reformatted comment chain output (M) — depends on §10 — 🔶

**Why:** Scope formatting; example in [show.md](spec/commands/show.md).

**Steps:**

1. Render: chain URL once at top; per comment: author, relative time, `PATCHSET_LEVEL` or `path:line` prefix.
2. Reuse or add small formatter in `render/` if needed.
3. Update golden tests / `test_show.py`.

**Done when:** Output matches spec example structure (manual spot-check on integration optional).

---

## 12. Spec: `ger show` message when only Change-Id (S) — 📝

**Why:** Scope: always show commit message; today skipped with no local commit.

**Steps:**

1. Decide source: Gerrit change `subject` + `commit` message body from detail API vs error if unavailable.
2. Document in [show.md](spec/commands/show.md): ordering vs local `git show`, dim markers, exit codes unchanged.

**Done when:** Implementer knows exact API fields and fallback when change not in local stack.

---

## 13. `ger show`: Change-Id-only commit message (M) — depends on §12

**Steps:**

1. In `resolve_show_commit_row` / `cli_show`, fetch and print message per spec when no local SHA.
2. Test with mocked Gerrit detail (no local commit).

**Done when:** `ger show I…` prints message block without local branch containing that commit.

---

## 14. Spec: `ger log --unresolved-comments` (S) — 📝

**Why:** New flag; no option row in log.md yet.

**Steps:**

1. Add option to [log.md](spec/commands/log.md):
   - Text mode: after each **non-clean** line or only lines with comment attention? (**recommend:** only rows with unresolved chains)
   - Inline body: reuse show chain formatter (§11)
   - `--json`: new field e.g. `unresolved_chains` vs inline only in text
   - Performance: one comments API per change vs batch — document
2. Add completion entry note for [bash-completion](spec/commands/bash-completion.md) when implemented.

**Done when:** Flag documented with examples; JSON shape chosen.

---

## 15. `ger log --unresolved-comments` (M) — depends on §9, §11, §14

**Steps:**

1. Add argparse flag; fetch comments for relevant commits only.
2. Print inline chains using shared formatter.
3. Tests in `tests/test_log.py`.

**Done when:** `ger log --unresolved-comments` matches spec; no duplicate formatting logic vs show.

---

## 16. Spec: `ger push --review` interactive shortcut (S) — 📝

**Why:** Scope item; [push.md](spec/commands/push.md) only says “not implemented”.

**Steps:**

1. Document: skips which prompts (ready list? rebase check? attribute preview?) — lands user on reviewer step only.
2. TTY requirements, non-TTY error (mirror existing `--yes` rules).
3. Relation to `-i` and `--reviewers` / `--reviewer-strategy`.

**Done when:** push.md has step list a tester can walk through manually.

---

## 17. `ger push --review` (M) — depends on §16

**Steps:**

1. Add `--review` flag; branch interactive flow in `cli_push.py` / `push_input_prompt.py`.
2. Tests in `tests/test_push.py` (TTY mocked).

**Done when:** `ger push --review` reaches reviewer assignment without earlier confirmations (per spec).

---

## 18. Spec: `ger fix` conflict probe + `--force` (M) — 📝

**Why:** Second fix safety check; needs §6 exit-code family.

**Steps:**

1. Define algorithm (e.g. attempt `git commit --fixup` with `--dry-run` if available, or merge-tree).
2. `--force` behavior in [fix.md](spec/commands/fix.md).

**Done when:** fix.md describes deterministic pass/fail scenarios.

---

## 19. `ger fix`: conflict check (M) — depends on §6, §18

**Steps:**

1. Implement probe before `git commit --fixup`.
2. Unit tests with contrived index state / fixture repo.

**Done when:** Conflicting staged fixup fails (or warns with `--force`) per spec.

---

## 20. Spec: `ger assign` full command (M) — 📝

**Why:** [assign.md](spec/commands/assign.md) is outline-only; largest product gap.

**Steps:**

1. Expand [assign.md](spec/commands/assign.md):
   - CLI flags: `--reviewers`, `--topic`, `--wip`, `--private`, `--target-branch`, `--dry-run`
   - Target grammar: `A..B`, `I…`, `@stack` (default range)
   - Exit codes; REST endpoints (reuse push reviewer strategies?)
   - **Move branch:** Gerrit API + constraints (open changes only?)
2. Add registry row timing in [SPEC.md](SPEC.md) when ready to implement.

**Done when:** assign.md is implementable without reading push.py for every behavior.

---

## 21. `ger assign` MVP (L) — depends on §20

**Why:** New command; absorb `ger move` recovery.

**Suggested slice order (each can be a PR):**

1. `ger assign --reviewers` on one Change-Id / SHA (`--dry-run`)
2. Topic / WIP / private
3. `--target-branch` move
4. Stack range + bash completion

**Done when:** `cli_ger.py` registers `assign`; integration test mutates a seeded change.

---

## Maintenance (ongoing, not ordered)

- [ ] When closing any todo above, update [Version 1 Scope.md](Version%201%20Scope.md) and the command spec **V1 scope delta** section.
- [ ] Keep [SPEC.md](SPEC.md) command registry status in sync (`Planned` → `Implemented`).

---

## Suggested first sprint (simplest, high leverage)

1. **§1** push.md hygiene
2. **§2–3** onboarding docs
3. **§5** show empty unresolved line
4. **§4 + §8** log `-v` spec then implement
5. **§6–7** fix merged spec + implement

Defer **§20–21** (`ger assign`) until comment-chain core (**§9–11**) is stable — log inline comments and show share the same model.
