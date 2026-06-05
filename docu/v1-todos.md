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
| `ger log -v` | What counts as “non-clean”; URL line rules vs `--url` | [spec/commands/log.md](spec/commands/log.md) |
| `ger log --unresolved-comments` | Flag behavior, output shape, `--json`, cost (N API calls?) | [spec/commands/log.md](spec/commands/log.md) |
| Comment chains (docs) | `architecture.md` section missing; show.md still points at wrong module | [architecture.md](architecture.md) + [spec/commands/show.md](spec/commands/show.md) |
| `ger show` Change-Id-only | Message source: Gerrit `commit` field vs “not available” | [spec/commands/show.md](spec/commands/show.md) |
| `ger fix` safety | Exit codes, exact error text, `--force` for conflict probe | [spec/commands/fix.md](spec/commands/fix.md) |
| `ger push --review` | Interactive step order vs `-i` / `--reviewers` | [spec/commands/push.md](spec/commands/push.md) |
| `ger assign` | Full command: flags, targets, `--dry-run`, branch move API | [spec/commands/assign.md](spec/commands/assign.md) |

---

## 6. Spec: `ger fix` merged + conflict checks (S) — 📝

**Why:** [spec/commands/fix.md](spec/commands/fix.md) has no behavior detail for merged-target or conflict probes.

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

## 8. Implement `ger log -v` selective URLs (S–M) — depends on log.md spec

**Why:** Closes scope log item: verbose mode still prints URLs for every row.

**Steps:**

1. Document policy in [log.md](spec/commands/log.md) (reuse `attention_reasons` / `determine_attention`).
2. Implement in `cli_log.py`: skip URL + extra detail lines for clean commits when `-v` is set.
3. Extend `tests/test_log.py` for verbose + clean vs attention row.

**Done when:** `-v` does not print URLs for clean commits; unit tests lock behavior.

---

## 9. Spec: comment chain model in architecture (S) — 📝

**Why:** Implementation lives in `core/comment_chains.py`; docs still lag.

**Steps:**

1. In [architecture.md](architecture.md), add **Comment chains** (grouping, resolution rule, helper names).
2. Fix module reference in [show.md](spec/commands/show.md) (`comment_chains.py`, not `gerrit_change_status.py`).
3. Confirm `comments_unresolved` = chain count (already used by `count_unresolved_in_file_map`).

**Done when:** architecture.md + show.md match shipped code.

---

## 11. `ger show`: comment chain formatting polish (S) — 🔶 — depends on §9

**Why:** Per-comment timestamp and location prefix still missing from show output.

**Steps:**

1. Per comment: relative timestamp (e.g. “3 days ago”) alongside author.
2. Per comment: `PATCHSET_LEVEL` or `path:line` prefix (not only at chain header).
3. Reuse or add formatter in `render/`; update `tests/test_show.py`.

**Done when:** Output matches spec example structure.

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
   - Text mode: only rows with unresolved chains (recommended)
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

**Why:** Scope item; [push.md](spec/commands/push.md) has no `--review` behavior yet.

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

- [ ] When closing any todo above, update [Version 1 Scope.md](Version%201%20Scope.md). Do not add roadmap or “planned/deferred” sections to user-facing docs (`docu/SPEC.md`, `docu/spec/commands/*` for shipped commands, `docu/README.md`, root `README.md`).
- [ ] Keep [SPEC.md](SPEC.md) command registry limited to commands registered in `cli_ger.py`.

---

## Suggested next sprint

1. **§9** — architecture.md comment chains (doc-only, unblocks log flag spec)
2. **§6–7** — fix merged spec + implement
3. **§8** — log `-v` selective URLs
4. **§11** — show comment timestamp / location prefix polish

Defer **§20–21** (`ger assign`) until comment-chain formatting is stable — log inline comments and show share the same formatter.
