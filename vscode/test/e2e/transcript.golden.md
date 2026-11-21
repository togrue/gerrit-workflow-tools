# What a user sees and gets, button by button

## On start

```
status bar | $(error) CI failed: 102
state  | ready
events | live
chains | CI failed on 102: test! temporary experiment
chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url.local]
chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     Extract command routing — 101 · V+1 CR+2 💬2 · in your stack  [ger.change.url.local]
chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
chains | To review — 1 chain
chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url]
stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
change | title: (nothing shown)
change | Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.
editor | (none)
```

## 1. Click the status bar item

**Expect:** The Change view opens on the change the status bar names (CI failed on 102).

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 102
+ change | CI failed — V-1 CR+2
+ change | Commit message — test! temporary experiment  (editable)
+ change | Showing — Local commit <sha> · Against its base commit
+ change | Files — 1 · +1 −0
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI
+ change |   Widget #55 — 2 findings
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
+ change |     FAILURE — generic · introduced here
- change | title: (nothing shown)
- change | Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.
```

## 2. Review chains › Mark All Activity as Seen

**Expect:** Every "● new activity" marker goes; nothing else changes.

```
status bar message: $(check) All activity marked as seen
--- on screen:
(no change)
```

## 3. Review chains › Sort Chains… › By last activity, oldest first

**Expect:** A picker offers the three orders, the current one marked; both sections say "oldest first" and list their chains that way.

```
picker: Sort review chains — "Applies to both sections"
    By chain state — current
    By last activity, newest first
    By last activity, oldest first
    → picked "By last activity, oldest first"
status bar message: $(check) Chains sorted by last activity, oldest first
--- on screen:
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments · oldest first
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
+ chains | To review — 1 chain · oldest first
- chains | To review — 1 chain
```

## 4. Review chains › Sort Chains… › By chain state

**Expect:** Back to the order that puts what needs you first; the sections no longer name an order.

```
picker: Sort review chains — "Applies to both sections"
    By chain state
    By last activity, newest first
    By last activity, oldest first — current
    → picked "By chain state"
status bar message: $(check) Chains sorted by chain state
--- on screen:
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments · oldest first
+ chains | To review — 1 chain
- chains | To review — 1 chain · oldest first
```

## 5. Review chains › Refresh from Gerrit

**Expect:** Both Review chains and Local stack show progress while they reload from Gerrit. With nothing new there, nothing changes.

```
progress (in view ger.overview): Refreshing from Gerrit
progress (in view ger.stack): Refreshing from Gerrit
status bar message: $(check) Refreshed from Gerrit
--- on screen:
(no change)
```

## 6. Click "Add retry to the uploader" under To review

**Expect:** The Change view shows change 900: what it needs from you, its files, its comments.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 900
+ change | Waiting for your review — V+1
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Showing — Patchset 1 (current) · Against its base commit
- change | title: Change 102
- change | CI failed — V-1 CR+2
- change | Commit message — test! temporary experiment  (editable)
- change | Showing — Local commit <sha> · Against its base commit
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 unresolved of 1
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change |   c.txt — added +1 −0
- change | Comments — none
- change | CI
- change |   Widget #55 — 2 findings
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
- change |     FAILURE — generic · introduced here
```

## 7. Inline Open in Gerrit on change 900

**Expect:** The browser opens change 900 on Gerrit.

```
status bar message: $(check) Opened 900 in the browser
browser opens: https://g.example/c/testproj/+/900
--- on screen:
(no change)
```

## 8. Right-click change 900 › Copy SHA

**Expect:** 900 is not in your stack: the SHA of its current patch set is on the clipboard; the status bar says so.

```
clipboard: 1 lines, starting "<sha>"
status bar message: $(check) Copied SHA <sha>…
--- on screen:
(no change)
```

## 9. Right-click change 900 › Copy Change-Id

**Expect:** Its Change-Id is on the clipboard; the status bar says so.

```
clipboard: 1 lines, starting "I0000000000000000000000000000000000000384"
status bar message: $(check) Copied Change-Id I00000000000…
--- on screen:
(no change)
```

## 10. Right-click change 900 › Hand to Agent

**Expect:** The comments and CI findings of 900 open as a preview, and a dialog offers to copy them (no agent command is set). It says truthfully that 900 is not checked out, so the agent can only read the brief; it does not promise amended commits.

```
progress (notification): Collecting comments and CI findings
dialog (info): The comments and CI findings of 900: Add retry to the uploader, as a brief for the agent.  [Copy to Clipboard] [Check Out to Work On] [Cancel]
    detail: This change is not checked out here, so the agent starts on your current branch and can only read the brief: its comments and CI findings. To have it amend the change, check it out to work on it first, then hand the commit over. Nothing is pushed or published.
    → pressed "Copy to Clipboard"
clipboard: 13 lines, starting "## <sha> — Add retry to the uploader"
notification (info): Copied. Paste it into your agent, or set `ger.agentCommand` to start the agent from here.
--- on screen:
+ editor | Preview ger-brief-<id>.md (webview)
- editor | (none)
```

## 11. Right-click change 900 › Hand to Agent › Check Out to Work On

**Expect:** The same dialog offers to check 900 out to work on instead; pressing it starts ger checkout in a terminal, as the agent could not change a change that is not checked out.

```
progress (notification): Collecting comments and CI findings
dialog (info): The comments and CI findings of 900: Add retry to the uploader, as a brief for the agent.  [Copy to Clipboard] [Check Out to Work On] [Cancel]
    detail: This change is not checked out here, so the agent starts on your current branch and can only read the brief: its comments and CI findings. To have it amend the change, check it out to work on it first, then hand the commit over. Nothing is pushed or published.
    → pressed "Check Out to Work On"
terminal "ger": runs `ger checkout change:900`
status bar message: $(terminal) ger checkout change:900 runs in the ger terminal: it may ask before it changes anything
--- on screen:
(no change)
```

## 12. Inline Check Out to Work On on change 900

**Expect:** ger checkout change:900 runs in the ger terminal, where it can ask before it changes anything; the status bar says so. The views follow by themselves once the checkout moves HEAD.

```
terminal "ger": runs `ger checkout change:900`
status bar message: $(terminal) ger checkout change:900 runs in the ger terminal: it may ask before it changes anything
--- on screen:
(no change)
```

## 13. Right-click the chain "Cleanup after experiment" › Copy SHA

**Expect:** A chain row stands for its top change, 103, which is in your stack: its local commit is on the clipboard.

```
clipboard: 1 lines, starting "<sha>"
status bar message: $(check) Copied SHA <sha>…
--- on screen:
(no change)
```

## 14. Inline Edit Commit Here on change 103, a local commit in Review chains

**Expect:** Your own commit is edited where it is: ger edit runs in the terminal, and the status bar says what to do once it stops (not a review checkout, not a notification).

```
terminal "ger": runs `ger edit <sha>`
status bar message: $(terminal) ger edit runs in the ger terminal: once the rebase stops at "103: Cleanup after experiment", amend it, then git rebase --continue
--- on screen:
(no change)
```

## 15. Inline Review Change on change 900

**Expect:** Progress while it fetches; then where change 900 is checked out for review (the review worktree, as ger.reviewCheckout is not set), with a button to open that worktree in a new window.

```
progress (notification): Checking out 900: Add retry to the uploader
notification (info): Change 900 (patchset 1) is checked out for review on change-900 in <repo>-review, with its changes staged.  [Open Review Worktree]
    → pressed "Open Review Worktree"
new VS Code window on: <repo>-review
--- on screen:
(no change)
```

## 16. Change › … › Leave Review Checkout

**Expect:** The review checkout of 900 is closed, and a notification says so.

```
notification (info): Left the review of change 900.
--- on screen:
(no change)
```

## 17. Command palette › ger: Leave Review Checkout, with none open

**Expect:** It says there is no review checkout to leave (the Change view menu no longer offers it).

```
notification (info): No review checkout is open here.
--- on screen:
(no change)
```

## 18. Settings › ger.executable set to a program that is not installed

**Expect:** ger serve restarts with it and cannot start: the Gerrit views go empty and their welcome text says the program was not found, with Open Settings and Retry; the status bar shows a warning.

```
(nothing said or done outside the views)
--- on screen:
+ status bar | $(warning) Gerrit
+ state  | no-executable
+ events | (not said)
+ stack  | Your local stack shows here once ger runs. Review chains says what is missing and what to do next.
- status bar | $(error) CI failed: 102
- state  | ready
- events | live
- chains | CI failed on 102: test! temporary experiment
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
- chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url.local]
- chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 · in your stack  [ger.change.url.local]
- chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
- chains | To review — 1 chain
- chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | A change shows here once ger runs. Review chains says what is missing and what to do next.
- change | Waiting for your review — V+1
- change | Commit message — Add retry to the uploader  (read-only)
- change | Showing — Patchset 1 (current) · Against its base commit
- change | Files — 1 · +1 −0
- change |   uploader.txt — added +1 −0
- change | Comments — 1 unresolved of 1
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
```

## 19. Review chains › Retry, the program still missing

**Expect:** It says it is restarting ger serve; the program is still missing, so the views still say so.

```
progress (notification): Restarting ger serve
--- on screen:
(no change)
```

## 20. Review chains › Install ger

**Expect:** A terminal opens and installs the ger release with this extension's version, with uv (the suite needs uv, so it is there); nothing else changes until Retry.

```
terminal "ger": runs `uv tool install --force https://github.com/togrue/gerrit-workflow-tools/releases/download/v0.1.0/ger.tar.gz`
status bar message: $(terminal) Installing ger with uv in the ger terminal: Retry once it is done
--- on screen:
(no change)
```

## 21. Settings › ger.executable set back

**Expect:** ger serve starts again at once, without a reload, and the views show what they showed before.

```
(nothing said or done outside the views)
--- on screen:
+ status bar | $(error) CI failed: 102
+ state  | ready
+ events | live
+ chains | CI failed on 102: test! temporary experiment
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
+ chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url.local]
+ chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 · in your stack  [ger.change.url.local]
+ chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
+ chains | To review — 1 chain
+ chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url]
+ stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
- status bar | $(warning) Gerrit
- state  | no-executable
- events | (not said)
- stack  | Your local stack shows here once ger runs. Review chains says what is missing and what to do next.
+ change | Waiting for your review — V+1
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Showing — Patchset 1 (current) · Against its base commit
+ change | Files — 1 · +1 −0
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 unresolved of 1
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change | A change shows here once ger runs. Review chains says what is missing and what to do next.
```

## 22. Click "Extract command routing" in Local stack

**Expect:** The Change view is titled Change 101 and shows its commit message (editable: it is your commit), the local commit against its base, b.txt under Files with its added and removed lines, and both unresolved comments.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 101
+ change | Unresolved comments — V+1 CR+2 💬2
+ change | Commit message — Extract command routing  (editable)
+ change | Showing — Local commit <sha> · Against its base commit
- change | title: Change 900
- change | Waiting for your review — V+1
- change | Commit message — Add retry to the uploader  (read-only)
- change | Showing — Patchset 1 (current) · Against its base commit
+ change |   b.txt — added +1 −0
+ change | Comments — 2 unresolved of 3
+ change |   Please rename this. — b.txt:1 · Alice Reviewer  [open]
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
+ change |   Looks good overall. — whole change · Alice Reviewer  [resolved]
- change |   uploader.txt — added +1 −0
- change | Comments — 1 unresolved of 1
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
```

## 23. Change › Compare › Gerrit vs. local

**Expect:** The files list becomes what your commit changes compared with Gerrit's patchset: nothing, as the commit is the pushed one.

```
(nothing said or done outside the views)
--- on screen:
+ change | Showing — Local commit <sha> · Gerrit vs. local
+ change | Gerrit vs. local — identical to patchset 1 — nothing to push
- change | Showing — Local commit <sha> · Against its base commit
- change | Files — 1 · +1 −0
- change |   b.txt — added +1 −0
```

## 24. Change › Show › Gerrit's current patchset

**Expect:** The view shows patchset 1 as Gerrit has it, against its base; its message is read-only.

```
(nothing said or done outside the views)
--- on screen:
+ change | Commit message — Extract command routing  (read-only)
+ change | Showing — Patchset 1 (current) · Against its base commit
+ change | Files — 1 · +1 −0
+ change |   b.txt — added +1 −0
- change | Commit message — Extract command routing  (editable)
- change | Showing — Local commit <sha> · Gerrit vs. local
- change | Gerrit vs. local — identical to patchset 1 — nothing to push
```

## 25. Change › Show › Local commit

**Expect:** Back on your commit: the message is editable again.

```
(nothing said or done outside the views)
--- on screen:
+ change | Commit message — Extract command routing  (editable)
+ change | Showing — Local commit <sha> · Against its base commit
- change | Commit message — Extract command routing  (read-only)
- change | Showing — Patchset 1 (current) · Against its base commit
```

## 26. Change › Next Unresolved Comment (Alt+F9)

**Expect:** b.txt opens at line 1, the first thread expands, the status bar says Comment 1 of 2.

```
status bar message: Comment 1 of 2
--- on screen:
+ editor | b.txt (file, line 1)
- editor | Preview ger-brief-<id>.md (webview)
```

## 27. Next Unresolved Comment again

**Expect:** The second thread: Comment 2 of 2.

```
status bar message: Comment 2 of 2
--- on screen:
(no change)
```

## 28. Next Unresolved Comment a third time

**Expect:** It wraps around to Comment 1 of 2.

```
status bar message: Comment 1 of 2
--- on screen:
(no change)
```

## 29. Change › Previous Unresolved Comment

**Expect:** Back to Comment 2 of 2.

```
status bar message: Comment 2 of 2
--- on screen:
(no change)
```

## 30. Click "Please rename this." in the Change view

**Expect:** b.txt opens at line 1 with that comment's thread expanded there, the other one closed.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 31. Click b.txt under Files

**Expect:** b.txt opens on its own: the commit adds it, so there is nothing to compare it with.

```
(nothing said or done outside the views)
--- on screen:
+ editor | b.txt (ger-file, line 1)
- editor | b.txt (file, line 1)
```

## 32. Inline Keep for Me on "Is this the right design?"

**Expect:** The thread is marked as kept for you; the agent will leave it alone.

```
status bar message: $(check) Kept for you: the agent leaves this comment alone
--- on screen:
+ change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [open, kept for you]
- change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
```

## 33. Local stack › Fix Stack with Agent…

**Expect:** The runbook opens as a preview; a dialog counts what the agent gets, without the comment kept for you; Copy to Clipboard copies it.

```
progress (notification): Collecting comments and CI findings
dialog (info): 2 commits: 1 comment and 2 CI findings for the agent. 1 comment kept for you.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → pressed "Copy to Clipboard"
clipboard: 73 lines, starting "# Address the review feedback on this stack"
notification (info): Copied. Paste it into your agent, or set `ger.agentCommand` to start the agent from here.
--- on screen:
+ editor | Preview ger-runbook-<id>.md (webview)
- editor | b.txt (ger-file, line 1)
```

## 34. Inline Let the Agent Handle It on "Is this the right design?"

**Expect:** The thread is no longer marked as kept for you.

```
status bar message: $(check) Left to the agent
--- on screen:
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
- change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [open, kept for you]
```

## 35. Inline Reply as Draft… on "Please rename this."

**Expect:** An input box asks for the reply; after Enter the draft shows under the thread and the status bar says it was saved.

```
input box: Reply to Alice Reviewer — b.txt:1
    prompt: Saved as a draft on Gerrit. Drafts are published with `ger push` or `ger review --vote`.
    placeholder: Please rename this.
    → typed "Renamed it." and pressed Enter
status bar message: $(check) Draft saved. Publish it with ger push.
--- on screen:
+ change |   Please rename this. — b.txt:1 · Alice Reviewer · 1 reply  [open]
+ change |     you — [draft] Renamed it.
- change |   Please rename this. — b.txt:1 · Alice Reviewer  [open]
```

## 36. Inline Reply as Draft… on "Please rename this." again

**Expect:** The box opens with your draft in it, to edit; saving updates that draft, nothing you wrote is lost unseen.

```
input box: Edit your draft reply — b.txt:1
    prompt: Saved as a draft on Gerrit. Drafts are published with `ger push` or `ger review --vote`.
    placeholder: Renamed it.
    already filled in: "Renamed it."
    → changed it to "Renamed it to route()." and pressed Enter
status bar message: $(check) Draft updated. Publish it with ger push.
--- on screen:
+ change |     you — [draft] Renamed it to route().
- change |     you — [draft] Renamed it.
```

## 37. Inline Reply Done and Resolve as Draft on "Is this the right design?"

**Expect:** A "Done" draft that resolves the thread is saved at once; the thread shows that your draft resolves it.

```
status bar message: $(check) Draft saved, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [open]
+ change |     you — [draft] Done
- change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
```

## 38. Inline Reply Done and Resolve as Draft on "Please rename this.", which has your draft

**Expect:** Your draft stays as you wrote it and now resolves the thread.

```
status bar message: $(check) Draft updated, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |   Please rename this. — b.txt:1 · Alice Reviewer · 1 reply  [open]
```

## 39. Expand "Looks good overall." (about the whole change) in the Change view, type a reply, Resolved left ticked › Save

**Expect:** The comment has no line in a file, so it is answered in the view: the reply is saved as a draft, as a reply in an editor thread is, and shows under it; the status bar says it was saved, the comment staying resolved.

```
status bar message: $(check) Draft saved, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Looks good overall. — whole change · Alice Reviewer · 1 reply  [resolved]
+ change |     you — [draft] Added a test for the parser.
- change |   Looks good overall. — whole change · Alice Reviewer  [resolved]
```

## 40. Click "Please rename this." in the Change view again

**Expect:** b.txt comes back in front of the runbook with that thread expanded: Alice's comment and its suggested fix, your draft reply under it, the reply box, and the Resolved toggle on in its header.

```
(nothing said or done outside the views)
--- on screen:
+ editor | b.txt (file, line 1)
- editor | Preview ger-runbook-<id>.md (webview)
```

## 41. Type in the editor thread of "Please rename this.", whose Resolved toggle is on (your draft resolves it) › Save

**Expect:** What you type is added to your draft reply there, after what it already says, and it still resolves the comment; the status bar says the draft was updated.

```
status bar message: $(check) Draft updated, resolving the comment. Publish it with ger push.
--- on screen:
(no change)
```

## 42. Click the Resolved toggle in the header of the editor thread of "Please rename this."

**Expect:** Your draft there is saved again at once, now leaving the comment unresolved; the status bar says so.

```
status bar message: $(check) Draft updated, leaving the comment unresolved. Publish it with ger push.
--- on screen:
+ change |   Please rename this. — b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [open]
```

## 43. Click the Resolved toggle in the header of the editor thread of "Please rename this." again

**Expect:** Your draft resolves the comment again, as before.

```
status bar message: $(check) Draft updated, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |   Please rename this. — b.txt:1 · Alice Reviewer · 1 reply  [open]
```

## 44. "+" on line 1 of b.txt in the diff › Save Draft

**Expect:** The comment is saved as a draft and appears as a thread marked as your draft; it is not counted as unresolved feedback.

```
status bar message: $(check) Draft saved on Gerrit, visible only to you. Publish it with ger push.
--- on screen:
+ change | Comments — 2 unresolved of 3 · 1 of yours, not published
- change | Comments — 2 unresolved of 3
+ change |   Should this be configurable? — your draft · b.txt:1  [yours]
```

## 45. "+" on line 1 of b.txt in the diff › Save Private Note

**Expect:** The note is saved in this repository only and appears marked as your private note; it is not counted as unresolved feedback.

```
status bar message: $(check) Private note saved here. It never leaves this repository.
--- on screen:
+ change | Comments — 2 unresolved of 3 · 2 of yours, not published
- change | Comments — 2 unresolved of 3 · 1 of yours, not published
+ change |   Remember to check the docs. — your private note · b.txt:1  [yours]
```

## 46. Inline Edit… on your comment "Should this be configurable?" in the Change view

**Expect:** b.txt opens at its line and the comment turns into a text box in its thread, with Save and Cancel; nothing is saved yet.

```
(nothing said or done outside the views)
--- on screen:
+ editor | b.txt (file)
- editor | b.txt (file, line 1)
```

## 47. Change the text in that box › Save

**Expect:** Your comment says the new text, in the thread and in the Change view; the status bar says it was updated. It is still yours and not published.

```
status bar message: $(check) Draft updated.
--- on screen:
+ change |   Should this be a setting? — your draft · b.txt:1  [yours]
- change |   Should this be configurable? — your draft · b.txt:1  [yours]
+ editor | b.txt (file, line 1)
- editor | b.txt (file)
```

## 48. Pencil on your private note in its editor thread, change the text › Cancel

**Expect:** The note says what it said before; nothing is saved and nothing is said.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 49. Inline Delete Your Draft Reply… on "Is this the right design?" › Cancel

**Expect:** A dialog shows the draft reply and asks before deleting it; Cancel leaves it.

```
dialog (warning): Delete your draft reply on b.txt:1?  [Delete] [Cancel]
    detail: Done
    → cancelled
--- on screen:
(no change)
```

## 50. Trash on your private note in its editor thread › Delete

**Expect:** A dialog shows the note and asks; Delete removes it from the thread and from the Change view, and the status bar says so.

```
dialog (warning): Delete your private note on b.txt:1?  [Delete] [Cancel]
    detail: Remember to check the docs.
    → pressed "Delete"
status bar message: $(check) Private note deleted.
--- on screen:
+ change | Comments — 2 unresolved of 3 · 1 of yours, not published
- change | Comments — 2 unresolved of 3 · 2 of yours, not published
- change |   Remember to check the docs. — your private note · b.txt:1  [yours]
```

## 51. Command palette › ger: Add Comment, with your own b.txt in the editor

**Expect:** The file in the editor is yours as it is now, not change 101 as shown (its menu does not offer Add Comment): it says where a comment on 101 goes, and opens no box.

```
notification (info): These are not the lines of "Extract command routing", the change shown. Open one of its files from the Change view (the right-hand side of a diff) and add the comment there.
--- on screen:
(no change)
```

## 52. Open b.txt of the commit, cursor on line 1 › Add Comment (Ctrl+Alt+C), type › Save

**Expect:** A comment box opens on that line with the same Save buttons as the "+" in the gutter; saving it adds your comment to the change.

```
status bar message: $(check) Draft saved on Gerrit, visible only to you. Publish it with ger push.
--- on screen:
+ change | Comments — 2 unresolved of 3 · 2 of yours, not published
- change | Comments — 2 unresolved of 3 · 1 of yours, not published
+ change |   Added from the keyboard. — your draft · b.txt:1  [yours]
+ editor | b.txt (ger-file, line 1)
- editor | b.txt (file, line 1)
```

## 53. Add Comment again › Cancel

**Expect:** The box closes; nothing is saved.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 54. Inline Comment on This File… on b.txt under Files

**Expect:** An input box asks for a comment about the whole file; after Enter it is saved like any new comment and shows under Comments as yours.

```
input box: Comment on b.txt
    prompt: About the whole file. Saved as a draft on Gerrit, visible only to you until it is published.
    → typed "This file needs a header." and pressed Enter
status bar message: $(check) Draft saved on Gerrit, visible only to you. Publish it with ger push.
--- on screen:
+ change | Comments — 2 unresolved of 3 · 3 of yours, not published
+ change |   This file needs a header. — your draft · b.txt  [yours]
- change | Comments — 2 unresolved of 3 · 2 of yours, not published
```

## 55. Change › Edit Commit Here

**Expect:** ger edit runs in the terminal; you are told what to do once the rebase stops at 101, not that it already has.

```
terminal "ger": runs `ger edit <sha>`
status bar message: $(terminal) ger edit runs in the ger terminal: once the rebase stops at "Extract command routing", amend it, then git rebase --continue
--- on screen:
(no change)
```

## 56. Change › Open in Gerrit

**Expect:** The browser opens change 101 on Gerrit.

```
status bar message: $(check) Opened 101 in the browser
browser opens: https://g.example/c/testproj/+/101
--- on screen:
(no change)
```

## 57. Click "test! temporary experiment" in Local stack

**Expect:** The Change view says CI failed; the CI group lists the findings, the ones this commit introduced first.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 102
+ change | CI failed — V-1 CR+2
+ change | Commit message — test! temporary experiment  (editable)
- change | title: Change 101
- change | Unresolved comments — V+1 CR+2 💬2
- change | Commit message — Extract command routing  (editable)
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI
+ change |   Widget #55 — 2 findings
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
+ change |     FAILURE — generic · introduced here
- change |   b.txt — added +1 −0
- change | Comments — 2 unresolved of 3 · 3 of yours, not published
- change |   This file needs a header. — your draft · b.txt  [yours]
- change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |     you — [draft] Renamed it to route().
- change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [open]
- change |     you — [draft] Done
- change |   Should this be a setting? — your draft · b.txt:1  [yours]
- change |   Added from the keyboard. — your draft · b.txt:1  [yours]
- change |   Looks good overall. — whole change · Alice Reviewer · 1 reply  [resolved]
- change |     you — [draft] Added a test for the parser.
```

## 58. Right-click it › Hand to Agent › Cancel

**Expect:** A runbook for this one commit and a dialog to start or copy it; Cancel does nothing more.

```
progress (notification): Collecting comments and CI findings
dialog (info): 1 commit: 0 comments and 2 CI findings for the agent.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → cancelled
--- on screen:
+ editor | Preview ger-runbook-<id>.md (webview)
- editor | b.txt (ger-file, line 1)
```

## 59. Local stack › Push to Gerrit…

**Expect:** ger push runs in the ger terminal (a new one when a command still runs there), which shows what it will push and asks first.

```
terminal "ger": runs `ger push`
status bar message: $(terminal) ger push runs in the ger terminal: it shows what it will push and asks first
--- on screen:
(no change)
```

## 60. Right-click "Refactor parser init" › Copy SHA

**Expect:** The commit's full id is on the clipboard; the status bar says so.

```
clipboard: 1 lines, starting "<sha>"
status bar message: $(check) Copied SHA <sha>…
--- on screen:
(no change)
```

## 61. Select two rows, right-click › Copy Change-Id

**Expect:** Both Change-Ids are on the clipboard, one per line, newest first as shown.

```
clipboard: 2 lines, starting "I2222222222222222222222222222222222222222"
status bar message: $(check) Copied 2 Change-Ids
--- on screen:
(no change)
```

## 62. Drag "Refactor parser init" to the line above "Extract command routing"

**Expect:** It swaps places with the commit above it; a notification offers Undo. Every commit keeps its votes: none changed its code, so Gerrit keeps them on the push.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → dismissed
--- on screen:
+ stack  | Cleanup after experiment — HEAD · <sha> · held back · changed here since the last push  [review +2, ci bad]
+ stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [review +2, ci bad]
+ stack  | Refactor parser init — <sha> · changed here since the last push  [review +2, ci good]
+ stack  | Extract command routing — <sha> · changed here since the last push  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | Local changes not pushed, CI failed — V-1 CR+2
+ change | This commit is ahead of Gerrit — only rebased: the votes stay when you push
- change | CI failed — V-1 CR+2
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6
+ change |     FAILURE — generic
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
- change |     FAILURE — generic · introduced here
```

## 63. Drag "Refactor parser init" to the bottom, then Undo

**Expect:** It swaps back with the commit below; Undo puts the stack back as it was before this move.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## 64. Select "Cleanup after experiment" and "Extract command routing", drag them to the bottom, then Undo

**Expect:** Both land under the last row, together and in the order they had; Undo puts the stack back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## Meanwhile: you edit a.txt and do not commit it

```
```

## 65. Drag "Extract command routing" up one row, with uncommitted changes › Cancel

**Expect:** A dialog asks whether to stash the uncommitted changes and continue; Cancel leaves the stack and the edit as they were.

```
progress (in view ger.stack): Rewriting the stack
dialog (warning): You have uncommitted changes. Stash them and continue?  [Stash and Continue] [Cancel]
    detail: They are stashed for the rewrite and put back right after it, also when the rewrite is refused.
    → cancelled
--- on screen:
(no change)
```

## 66. The same drag › Stash and Continue, then Undo

**Expect:** The commits swap, and the edit to a.txt is back in the worktree afterwards; Undo puts the stack back and keeps the edit.

```
progress (in view ger.stack): Rewriting the stack
dialog (warning): You have uncommitted changes. Stash them and continue?  [Stash and Continue] [Cancel]
    detail: They are stashed for the rewrite and put back right after it, also when the rewrite is refused.
    → pressed "Stash and Continue"
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## Meanwhile: you take the edit to a.txt back

```
```

## 67. Change › edit the commit message of "test! temporary experiment" › Apply, then Undo

**Expect:** The commit gets the new message and keeps its Change-Id, though the text typed had none; the commits above get new ids, nothing else changes. Undo puts the old message back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Commit message of "test! temporary experiment" changed. Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## Meanwhile: you commit a rework of d.txt in the terminal

```
+ stack  | Rework d.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | Cleanup after experiment — <sha> · held back · changed here since the last push  [review +2, ci bad]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · changed here since the last push  [review +2, ci bad]
```

## 68. Drag "Rework d.txt" to the line under "Cleanup after experiment"

**Expect:** One place down: the conflict preview already knows the two cannot swap, so it says at once which commit would conflict, and that nothing changed.

```
notification (info): "Rework d.txt" cannot change places with "Cleanup after experiment": "Rework d.txt" would conflict in d.txt. Nothing was changed.
--- on screen:
(no change)
```

## 69. Drag "Rework d.txt" to the bottom

**Expect:** Refused before anything is touched, saying which commit does not apply in its new place; the stack stays as it was.

```
progress (in view ger.stack): Rewriting the stack
notification (warning): "Rework d.txt" (<sha>) does not apply in its new place (conflict in d.txt); the stack was left as it was
--- on screen:
(no change)
```

## 70. Drag "Cleanup after experiment" to the line under "Extract command routing"

**Expect:** It lands there, and the commits between move up one; Undo is offered.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → dismissed
--- on screen:
- stack  | Cleanup after experiment — <sha> · held back · changed here since the last push  [review +2, ci bad]
+ stack  | Cleanup after experiment — <sha> · changed here since the last push  [review +2, ci bad]
```

## 71. Right-click "Rework d.txt" › Squash into Commit Below, then Undo

**Expect:** It folds into the commit below, keeping that commit's message; Undo brings it back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "test! temporary experiment". Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## 72. Right-click "Rework d.txt" › Squash into Commit Below, Keeping This Message, then Undo

**Expect:** Folds, keeping this commit's message, with a note that the Gerrit change of the one below stays open; Undo brings both back, and the Change view shows again what it showed.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "Rework d.txt". The Gerrit change of "test! temporary experiment" stays open: abandon it there. Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## 73. Right-click "Rework d.txt" › Drop Commit… › Cancel

**Expect:** A dialog asks to confirm; Cancel leaves everything as it was.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → cancelled
--- on screen:
(no change)
```

## 74. Right-click "Rework d.txt" › Drop Commit… › Drop, then Undo

**Expect:** The commit goes; Undo brings it back. It was never pushed, so there is nothing to push.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → pressed "Drop"
progress (in view ger.stack): Rewriting the stack
notification (info): Dropped "Rework d.txt".  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## 75. Right-click "Rework d.txt" › Drop Commit… › Drop

**Expect:** The commit is gone from the stack and from the checkout; nothing to push, as Gerrit never had it.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → pressed "Drop"
progress (in view ger.stack): Rewriting the stack
notification (info): Dropped "Rework d.txt".  [Undo]
    → dismissed
--- on screen:
+ stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [review +2, ci bad]
- stack  | Rework d.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [review +2, ci bad]
```

## Meanwhile: you change the code of the top commit and amend it

**Expect:** Its votes now show as the last pushed patchset's (Gerrit may not keep them for this code); the Change view follows the commit and says so.

```
+ stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [review +2, ci bad]
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
+ change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | Local changes not pushed, CI failed — V-1 CR+2
- change | This commit is ahead of Gerrit — only rebased: the votes stay when you push
- change | CI
- change |   Widget #55 — 2 findings
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6
- change |     FAILURE — generic
```

## 76. Local stack › … › Set Upstream Branch… › Escape

**Expect:** A picker lists the branches it could track, the current one marked; Escape changes nothing.

```
picker: Upstream for feature — "The local stack is the commits above it; ger push goes to its branch on Gerrit"
    origin/main — current · $(star-full) suggested: nearest: 4 ahead, 0 behind
    origin/feature
    → escaped
--- on screen:
(no change)
```

## 77. Review chains › … › Report Issue… › Copy and Open GitHub Issue

**Expect:** The report opens first, as an untitled JSON document to read: what the three views show, with names, paths, text and ids replaced. A notification says so, and that nothing has left the machine. Copy and Open GitHub Issue puts the report on the clipboard and opens a new issue that has only the versions in it and says to paste the report.

```
untitled json document opens: 347 lines, starting
    {
      "about": "What the ger views showed, for a bug report. Names, paths, text and ids are replaced by made-up ones of the same shape; states, counts and the extension's own words are kept. No log, no settings, no folder names.",
notification (info): This is the report, and all that would be shared: names, paths, text and ids in it are replaced. Nothing has left this machine.  [Copy and Open GitHub Issue] [Save to File…]
    → pressed "Copy and Open GitHub Issue"
clipboard: 347 lines, starting "{"
browser opens: https://github.com/togrue/gerrit-workflow-tools/issues/new?labels=vscode&body=<!-- What happened, and what did you expect%3F -->


<details><summary>Report (names, paths, text and ids replaced)</summary>

```json
PASTE THE REPORT HERE: it is on your clipboard
```

</details>

---
ger extension <version> · VS Code <version> · <os>
status bar message: $(check) Report copied: paste it into the issue that opens in the browser
--- on screen:
(no change)
```

## 78. Command palette › ger: Report Issue… › Save to File…

**Expect:** The report opens again to read; Save to File… asks where, suggesting ger-report.json, and the status bar says where it was saved. Nothing is copied and no browser opens.

```
untitled json document opens: 347 lines, starting
    {
      "about": "What the ger views showed, for a bug report. Names, paths, text and ids are replaced by made-up ones of the same shape; states, counts and the extension's own words are kept. No log, no settings, no folder names.",
notification (info): This is the report, and all that would be shared: names, paths, text and ids in it are replaced. Nothing has left this machine.  [Copy and Open GitHub Issue] [Save to File…]
    → pressed "Save to File…"
save dialog: suggests ger-report.json
    → saved 347 lines as ~/ger-report.json
status bar message: $(check) Report saved to ~/ger-report.json
--- on screen:
(no change)
```

## 79. Command palette › ger: Report Issue… › close the report without saving › Copy and Open GitHub Issue

**Expect:** The user may have taken things out of the report before closing it, so nothing is copied, not the original either, and no browser opens; a notification says nothing was shared and to run Report Issue… again.

```
untitled json document opens: 347 lines, starting
    {
      "about": "What the ger views showed, for a bug report. Names, paths, text and ids are replaced by made-up ones of the same shape; states, counts and the extension's own words are kept. No log, no settings, no folder names.",
    → closed without saving
notification (info): This is the report, and all that would be shared: names, paths, text and ids in it are replaced. Nothing has left this machine.  [Copy and Open GitHub Issue] [Save to File…]
    → pressed "Copy and Open GitHub Issue"
notification (info): The report was closed without saving it, so nothing was shared. Run ger: Report Issue… again for a new one.
--- on screen:
(no change)
```

## Meanwhile: a background refresh finds ger serve gone and cannot start it again

**Expect:** Nothing is said and the views keep what they showed; the status bar item gets a warning mark, its tooltip says the answer is out of date, and Review chains is labelled so.

```
+ status bar | $(error) CI failed: 102 $(warning)
- status bar | $(error) CI failed: 102
+ events | (not said)
- events | live
```

## Meanwhile: you press Refresh while ger serve is still gone

**Expect:** The views keep the last answer, marked out of date; a notification says why it is still out of date, and nothing is cleared.

```
progress (notification): Restarting ger serve
notification (warning): Still out of date: fake ger: down for this test
```

## Meanwhile: ger serve can start again, and the next background refresh works

**Expect:** The warning mark goes and the views show what Gerrit says now (the fake Gerrit starts afresh from the repository, so the amended top commit is no longer ahead of it); its event stream is followed again.

```
+ status bar | $(error) CI failed: 102
- status bar | $(error) CI failed: 102 $(warning)
+ events | live
+ chains | CI failed on 102: Refactor parser init
- events | (not said)
- chains | CI failed on 102: test! temporary experiment
+ chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url.local]
+ chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url.local]
- chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
+ stack  | test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
- stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · changed here since the last push  [review +2, ci good]
- stack  | Extract command routing — <sha> · changed here since the last push  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · changed here since the last push  [review +2, ci bad]
```

## Meanwhile: a reviewer comments on change 900 on Gerrit, and its event stream says so

**Expect:** With no click and no poll, Review chains shows the new unresolved comment on 900 within a few seconds; nothing is said.

```
+ status bar | $(error) CI failed: 102  $(bell-dot) 1
- status bar | $(error) CI failed: 102
+ chains | To review — 1 chain · 1 with comments
+ chains |   Add retry to the uploader — ● new activity · alice · 1 change · V+1 💬2 · waiting <age> · active <age> ago  [ger.change.url]
- chains | To review — 1 chain
- chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url]
```

## Meanwhile: you commit in the terminal, with no click and no focus change

**Expect:** Within a second or two Local stack shows the new commit on top (not yet on Gerrit); nothing is said, as you did it yourself.

```
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
```

## 80. Click "test! temporary experiment" in Local stack

**Expect:** The Change view shows change 103 as it is now: the commit is the same as on Gerrit. It should have shown that already after ger serve restarted (it kept Change 102's heading and "ahead of Gerrit", no issue yet); this click makes the rebase steps start from a fresh view.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 103
+ change | CI failed — V-1 CR+2
- change | title: Change 102
- change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
- change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
+ change | CI
+ change |   Widget #55 — 2 findings
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
+ change |     FAILURE — generic · also fails on the parent
```

## Meanwhile: you start ger edit's rebase on "test! temporary experiment", and it stops there

**Expect:** Local stack says a rebase is underway, with Continue and Abort, and marks that commit as where it stopped, with HEAD on it; the commits below are done, "Add e.txt" is dimmed, still to be replayed. The header says the branch is being rebased. The Change view of 103 says its message cannot be edited during the rebase. The status bar says the rebase stopped at 103.

**Today:** the banner has no buttons (#56); the status bar still shows the headline (#62).

```
+ stack  | A rebase is in progress
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ change | Commit message — test! temporary experiment  (read-only)
- change | Commit message — test! temporary experiment  (editable)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature (rebasing) → origin/main
stack  | A rebase is in progress
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | CI failed — V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +1 −0
change |   c.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — 2 findings
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
change |     FAILURE — generic · also fails on the parent
change | (the commit shown is HEAD)
```

## Meanwhile: you add a line to c.txt and amend the stopped commit

**Expect:** The stopped commit shows what it is now: a new SHA, HEAD, changed here since the last push. The Change view follows it to the amended commit: c.txt with +2 lines.

```
+ stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
+ change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | CI failed — V-1 CR+2
+ change | Files — 1 · +2 −0
+ change |   c.txt — added +2 −0
- change | Files — 1 · +1 −0
- change |   c.txt — added +1 −0
- change | CI
- change |   Widget #55 — 2 findings
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
- change |     FAILURE — generic · also fails on the parent
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature (rebasing) → origin/main
stack  | A rebase is in progress
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (read-only)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is HEAD)
```

## Meanwhile: you run git rebase --continue, and the rebase finishes

**Expect:** The banner and the marks go: the branch is back, the amended commit changed here since the last push and "Add e.txt" on top of it; the header names the branch; the Change view shows the amended commit, its message editable again.

```
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | A rebase is in progress
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
+ change | Commit message — test! temporary experiment  (editable)
- change | Commit message — test! temporary experiment  (read-only)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature → origin/main
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is below HEAD)
```

## Meanwhile: you stop at "Refactor parser init" the same way, and amend it to also add e.txt

**Expect:** Local stack marks the rebase stopped at that commit, which shows its new code (changed here since the last push) and HEAD; the two above are dimmed, still to be replayed. The status bar says the rebase stopped at 102.

**Today:** no Continue or Abort (#56); the status bar still shows the headline (#62).

```
+ stack  | A rebase is in progress
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]  (dim)
+ stack  | ▶ Refactor parser init — HEAD · <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
+ change | Commit message — test! temporary experiment  (read-only)
- change | Commit message — test! temporary experiment  (editable)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature (rebasing) → origin/main
stack  | A rebase is in progress
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (read-only)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is only on the branch as it was before)
```

## Meanwhile: you run git rebase --continue, and "Add e.txt" conflicts in e.txt

**Expect:** Local stack says the rebase stopped on a conflict, lists e.txt under the banner, with Continue, Abort and Skip; "Add e.txt" has a red conflict icon and is not dimmed; the commits below are done, showing what they are now. The status bar says Conflict in "Add e.txt".

**Today:** no conflicted file and no buttons (#56); the conflicting row is dimmed and only tagged (#58); the status bar still shows the headline (#62).

```
+ stack  | A rebase stopped on a conflict
+ stack  | ▶! Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | A rebase is in progress
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]  (dim)
- stack  | ▶ Refactor parser init — HEAD · <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature (rebasing) → origin/main
stack  | A rebase stopped on a conflict
stack  | ▶! Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (read-only)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is HEAD)
```

## Meanwhile: you resolve e.txt and git add it

**Expect:** Nothing is conflicted any more: e.txt leaves the list and the conflict icon goes; the rebase is still stopped at "Add e.txt", ready to Continue.

**Today:** no list of conflicted files for e.txt to leave, and no Continue (#56).

```
+ stack  | A rebase stopped with its conflicts resolved — ready to continue
+ stack  | ▶ Add e.txt — <sha> · held back · not on Gerrit
- stack  | A rebase stopped on a conflict
- stack  | ▶! Add e.txt — <sha> · held back · not on Gerrit  (dim)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature (rebasing) → origin/main
stack  | A rebase stopped with its conflicts resolved — ready to continue
stack  | ▶ Add e.txt — <sha> · held back · not on Gerrit
stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (read-only)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is HEAD)
```

## Meanwhile: you run git rebase --continue, and the rebase finishes

**Expect:** The banner and the marks go; Local stack shows the branch with "Refactor parser init" changed here since the last push and "Add e.txt" on top.

```
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | A rebase stopped with its conflicts resolved — ready to continue
- stack  | ▶ Add e.txt — <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — HEAD · <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
+ change | Commit message — test! temporary experiment  (editable)
- change | Commit message — test! temporary experiment  (read-only)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature → origin/main
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is below HEAD)
```

## Meanwhile: somebody adds c.txt on main, and you git fetch, so origin/main moves on

**Expect:** Local stack says origin/main has 1 new commit, offers Rebase onto origin/main, and warns that "test! temporary experiment" would conflict in c.txt; the commits themselves are unchanged.

**Today:** nothing at all changes (#60).

```
(no change)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature → origin/main
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is below HEAD)
```

## Meanwhile: you rebase onto origin/main, and "test! temporary experiment" conflicts in c.txt

**Expect:** Local stack says the rebase stopped on a conflict in c.txt, with Continue, Abort and Skip; "test! temporary experiment" has a red conflict icon, not dimmed; the three below are replayed onto the new origin/main and show their new SHAs; "Add e.txt" is dimmed. The Change view of 103 says "Conflict in c.txt" and lists it under Conflicts, without the vote explanation. The status bar says Conflict in 103.

**Today:** no conflicted file and no buttons (#56); the conflicting row is dimmed (#58); the Change view still explains votes and shows the commit as it was (#59); the status bar still shows the headline (#62).

```
+ stack  | A rebase stopped on a conflict
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | ▶! test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]  (dim)
+ stack  | Refactor parser init — HEAD · <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Extract command routing — <sha> · changed here since the last push  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · changed here since the last push  [review +2, ci good]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | Commit message — test! temporary experiment  (read-only)
- change | Commit message — test! temporary experiment  (editable)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature (rebasing) → origin/main
stack  | A rebase stopped on a conflict
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶! test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | Refactor parser init — HEAD · <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · changed here since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · changed here since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (read-only)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is only on the branch as it was before)
```

## Meanwhile: you run git rebase --abort

**Expect:** Everything is as before the rebase: the branch, its commits, the header; Local stack still says origin/main has 1 new commit.

**Today:** back as before, but nothing says origin/main moved on (#60).

```
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
- stack  | A rebase stopped on a conflict
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | ▶! test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]  (dim)
- stack  | Refactor parser init — HEAD · <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Extract command routing — <sha> · changed here since the last push  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · changed here since the last push  [review +2, ci good]
+ change | Commit message — test! temporary experiment  (editable)
- change | Commit message — test! temporary experiment  (read-only)
--- shown now:
status bar | $(error) CI failed: 102  $(bell-dot) 1
stack header | feature → origin/main
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | (the commit shown is below HEAD)
```

## At the end

```
status bar | $(error) CI failed: 102  $(bell-dot) 1
state  | ready
events | live
chains | CI failed on 102: Refactor parser init
chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url.local]
chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     Extract command routing — 101 · V+1 CR+2 💬2 · in your stack  [ger.change.url.local]
chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
chains | To review — 1 chain · 1 with comments
chains |   Add retry to the uploader — ● new activity · alice · 1 change · V+1 💬2 · waiting <age> · active <age> ago  [ger.change.url]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · held back · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · changed here since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
editor | Preview ger-runbook-<id>.md (webview)
```
