# Workflows

The three workflows `ger` exists for, step by step: the command that does each step, the
VS Code command that does it, and the tests that cover it. A **gap** is a step nothing does
yet. This is where to look first when you ask "can I do X?", and the place a new feature
adds its row.

**The rule:** anything the extension can do, the CLI can do too. The CLI may do more. Agents
mostly use the CLI, so a feature that exists only in the extension does not exist for them.
[Extension command parity](#extension-command-parity) lists every extension command with its
CLI equivalent, and `tests/test_parity.py` keeps that table in step with
`vscode/package.json` and the registered commands.

Status words: **ships**, **partial** (see the linked spec), **gap** (nothing does it yet).

## Seeing state

| Step | CLI | Extension | Tests | Status |
|------|-----|-----------|-------|--------|
| Your local stack against Gerrit: votes, comments, CI, what needs attention | `ger log` | *Local stack* view | `tests/test_log.py`, `tests/test_annotated_stack.py` | ships |
| Chains waiting on your review | `ger inbox` | *Review chains* view, *To review* | `tests/test_inbox.py` | ships |
| Your own open chains across all branches | — | — | — | **gap**: `ger log` covers the working branch only; inbox's *waiting on others* section is [planned](spec/commands/inbox.md#sections) |
| Why CI failed: findings from the CI logs | `ger ci`, `ger ci --stack`, `ger show --ci` | *CI* group in the *Change* view | `tests/test_ci.py`, `tests/test_ci_findings.py` | ships |
| What is new since you last looked | — | *Mark All Activity as Seen* (kept in VS Code only) | — | **gap**: the [inbox watermark](spec/commands/inbox.md#inbox-watermark) is planned |

## Review flow: someone else's chain

| Step | CLI | Extension | Tests | Status |
|------|-----|-----------|-------|--------|
| Open the chain | `ger inbox`, `ger show CHANGEISH` | click a chain, *Show Change* | `tests/test_inbox.py`, `tests/test_show.py` | ships |
| Check out one commit, in the review worktree | `ger review CHANGEISH` | *Check Out for Review (Review Worktree)* | `tests/test_review.py` | ships |
| Walk up and down the chain | `ger review --next`, `ger review --prev` | click another chain member | `tests/test_review.py`, `tests/test_review_chain.py` | ships |
| Read the commit's diff in your own tools | `ger review --stage` (the commit's changes staged, so every git view shows them) | *Files* group, *Open Diff* | `tests/test_review.py` | ships |
| What changed since the patchset you reviewed | — | *Since your review* group (`GET /diff?since=reviewed`) | `tests/test_serve.py` | **gap** in the CLI |
| Read the comment chains | `ger show --comments all` | editor comment threads, *Next Unresolved Comment* | `tests/test_show.py`, `tests/test_show_comments_render.py` | ships |
| Comment on a line or range | `ger comment PATH:LINE -m MSG` | "+" in the editor gutter, *Save Draft* | `tests/test_comment.py` | ships |
| Reply to a chain | `ger reply CHAIN -m MSG`, `--done` | *Reply (Draft)…*, *Reply Done and Resolve* | `tests/test_reply.py` | ships |
| Send comments and vote | `ger review --vote CR+1 -m MSG` | runs in the terminal | `tests/test_review.py` | ships |
| Leave the review | `ger review --done` | *Leave Review Checkout* | `tests/test_review.py` | ships |
| An agent reviews: at most Code-Review+1, and says what it would really give | `GER_NO_PUBLISH=1` stops publishing altogether | — | — | **gap**: no agent vote policy in core |

## Rework flow: your own chain

| Step | CLI | Extension | Tests | Status |
|------|-----|-----------|-------|--------|
| Find the commits that need attention | `ger log`, `ger show --first-attention` | *Local stack* view, *Open the Most Important Change* | `tests/test_log.py`, `tests/test_attention_agreement.py` | ships |
| Edit a commit: stop a rebase at it | `ger edit REV`, `ger edit --first-attention-commit`, `ger edit -n` | *Edit Commit Here* | `tests/test_edit.py`, `tests/test_edit_attention.py` | ships |
| See the CI failures of this commit | `ger ci REV`, `ger show --ci REV` | *CI* group in the *Change* view | `tests/test_ci.py` | ships |
| Diff the local commit against the patchset CI checked | — | — | — | **gap** |
| Step to the next issue, comments and CI findings alike | `ger show -n` (next commit with attention) | *Next Unresolved Comment* (comments only) | `tests/test_attention_cursor.py` | partial: no single walk over comments and findings |
| Reply to comments | `ger reply CHAIN -m MSG` | *Reply (Draft)…*, editor threads | `tests/test_reply.py` | ships |
| Add a private comment | `ger comment --note`, `ger comment --local` | *Save Private Note*, *Save Local Draft* | `tests/test_comment.py`, `tests/test_local_comments.py` | ships |
| Remove a private comment | `ger comment --discard` | — | `tests/test_comment.py` | partial: CLI only |
| Fold a fix into a stack commit | `ger fix CHANGEISH` | — | `tests/test_fix.py` | ships |
| Reorder, squash, drop | `ger rebase` (interactive), `ger edit --drop REV` | move, drag and drop, *Squash*, *Drop*, with a conflict preview and *Undo* | `tests/test_serve.py` | partial: no non-interactive CLI, no CLI preview or undo ([roadmap](plans/commit-chain-roadmap.md)) |
| Push | `ger push` | *Push to Gerrit…* (runs in the terminal) | `tests/test_push.py` | ships |
| Hand the stack to an agent | — | *Fix Stack with Agent…* (`GET /agent/runbook`) | `tests/test_serve.py` | **gap** in the CLI |
| Keep a comment chain for yourself, away from the agent | — | *Keep for Me*, *Let the Agent Handle It* | `tests/test_serve.py` | **gap** in the CLI |

## Local review flow

The rework flow with the comments kept private: an agent walks the stack and writes notes,
a human adds notes, an agent fixes them.

| Step | CLI | Extension | Tests | Status |
|------|-----|-----------|-------|--------|
| Write private notes on stack commits | `ger comment --note` | *Save Private Note* | `tests/test_local_comments.py` | ships |
| Notes follow their lines through rebases | re-anchoring in `ger show` | same | `tests/test_anchor_mapping.py` | ships |
| Notes follow their commit through a squash or reorder | — | — | — | **gap**: [orphans](spec/commands/comment.md#later-slices) are planned |
| Turn a local draft into a Gerrit draft | — | — | — | **gap**: [uploading](spec/commands/comment.md#later-slices) is planned |

## Extension command parity

Every command in `vscode/package.json`, with how to do the same from the command line.
*UI only* is for what has no meaning outside an editor (moving the cursor, opening a view).
A **gap** names the work that would close it.

| Extension command | CLI | Note |
|-------------------|-----|------|
| `ger.refresh` | `ger log`, `ger inbox` | every command reads Gerrit within the trust window |
| `ger.showLog` | UI only | the service's output channel |
| `ger.openHeadline` | `ger inbox`, `ger show --first-attention` | |
| `ger.markSeen` | **gap** | inbox watermark |
| `ger.openChange` | `ger show CHANGEISH` | |
| `ger.openInGerrit` | `ger show CHANGEISH` | prints the Gerrit URL |
| `ger.checkout` | `ger review CHANGEISH` | |
| `ger.stack.edit` | `ger edit REV` | |
| `ger.reviewDone` | `ger review --done` | |
| `ger.push` | `ger push` | |
| `ger.stack.moveUp` | **gap** | `ger rebase` reorders interactively only |
| `ger.stack.moveDown` | **gap** | `ger rebase` reorders interactively only |
| `ger.stack.moveUpBlocked` | **gap** | no conflict preview on the CLI |
| `ger.stack.moveDownBlocked` | **gap** | no conflict preview on the CLI |
| `ger.stack.squash` | **gap** | `ger rebase` squashes interactively only |
| `ger.stack.squashTakeMessage` | **gap** | `ger rebase` squashes interactively only |
| `ger.stack.drop` | `ger edit --drop REV` | |
| `ger.openDiff` | UI only | `ger review --stage` shows the diff to git's own tools |
| `ger.openLocation` | UI only | |
| `ger.nextComment` | UI only | `ger show` lists every unresolved chain |
| `ger.prevComment` | UI only | |
| `ger.reply` | `ger reply CHAIN -m MSG` | |
| `ger.replyDone` | `ger reply CHAIN --done` | |
| `ger.thread.reply` | `ger reply CHAIN -m MSG` | |
| `ger.thread.resolve` | `ger reply CHAIN -m MSG --resolve` | |
| `ger.comment.saveDraft` | `ger comment PATH:LINE -m MSG` | |
| `ger.comment.saveLocalDraft` | `ger comment --local PATH:LINE -m MSG` | |
| `ger.comment.saveNote` | `ger comment --note PATH:LINE -m MSG` | |
| `ger.setUpstream` | `ger log` | prompts for a missing upstream on a terminal |
| `ger.agentBrief` | `ger show --ai --comments unresolved --ci --context REV` | for a change of someone else; on a stack commit it uses the runbook |
| `ger.agentWalk` | **gap** | agent runbook, `GET /agent/runbook` only |
| `ger.hold` | **gap** | holds, `POST /comments/hold` only |
| `ger.release` | **gap** | holds, `POST /comments/hold` only |
