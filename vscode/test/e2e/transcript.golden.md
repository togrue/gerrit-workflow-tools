# What a user sees and gets, button by button

## On start

```
status bar | $(error) CI failed on 102: test! temporary experiment
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
+ change | title: Change 102 — test! temporary experiment
+ change | CI failed — V-1 CR+2
+ change | Commit message — test! temporary experiment  (editable)
+ change | Showing — Local commit <sha> · Against its base commit
+ change | Files — 1 · +1 −0
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI — failed
+ change |   Widget #55 — 2 findings
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
+ change |     FAILURE — generic · introduced here
- change | title: (nothing shown)
- change | Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.
```

## 2. Review chains › Mark All Activity as Seen

**Expect:** Every "● new activity" marker goes; nothing else changes.

```
(nothing said or done outside the views)
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
--- on screen:
(no change)
```

## 6. Click "Add retry to the uploader" under To review

**Expect:** The Change view shows change 900: what it needs from you, its files, its comments.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 900 — Add retry to the uploader
+ change | No reviewers, awaiting review — V+1
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Showing — Patchset 1 (current) · Against its base commit
- change | title: Change 102 — test! temporary experiment
- change | CI failed — V-1 CR+2
- change | Commit message — test! temporary experiment  (editable)
- change | Showing — Local commit <sha> · Against its base commit
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 unresolved of 1
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change |   c.txt — added +1 −0
- change | Comments — none
- change | CI — failed
- change |   Widget #55 — 2 findings
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
- change |     FAILURE — generic · introduced here
```

## 7. Inline Open in Gerrit on change 900

**Expect:** The browser opens change 900 on Gerrit.

```
browser opens: https://g.example/c/testproj/+/900
--- on screen:
(no change)
```

## 8. Right-click change 900 › Hand to Agent

**Expect:** The comments and CI findings of 900 open as a preview, and a dialog offers to copy them (no agent command is set). It says truthfully that 900 is not checked out, so the agent can only read the brief; it does not promise amended commits.

```
progress (notification): Collecting comments and CI findings
dialog (info): The comments and CI findings of 900: Add retry to the uploader, as a brief for the agent.  [Copy to Clipboard] [Check Out to Work On] [Cancel]
    detail: This change is not checked out here, so the agent starts on your current branch and can only read the brief: its comments and CI findings. To have it amend the change, check it out to work on it first, then hand the commit over. Nothing is pushed or published.
    → pressed "Copy to Clipboard"
clipboard: 12 lines, starting "## <sha> — Add retry to the uploader"
notification (info): Copied. Paste it into your agent, or set `ger.agentCommand` to start the agent from here.
--- on screen:
+ editor | Preview ger-brief-<id>.md (webview)
- editor | (none)
```

## 9. Right-click change 900 › Hand to Agent › Check Out to Work On

**Expect:** The same dialog offers to check 900 out to work on instead; pressing it starts ger checkout in a terminal, as the agent could not change a change that is not checked out.

```
progress (notification): Collecting comments and CI findings
dialog (info): The comments and CI findings of 900: Add retry to the uploader, as a brief for the agent.  [Copy to Clipboard] [Check Out to Work On] [Cancel]
    detail: This change is not checked out here, so the agent starts on your current branch and can only read the brief: its comments and CI findings. To have it amend the change, check it out to work on it first, then hand the commit over. Nothing is pushed or published.
    → pressed "Check Out to Work On"
terminal "ger checkout": runs `ger checkout change:900`
status bar message: ger checkout change:900 runs in the terminal: it may ask before it changes anything
--- on screen:
(no change)
```

## 10. Inline Check Out to Work On on change 900

**Expect:** ger checkout change:900 runs in a new terminal, where it can ask before it changes anything; the status bar says so. The views follow by themselves once the checkout moves HEAD.

```
terminal "ger checkout": runs `ger checkout change:900`
status bar message: ger checkout change:900 runs in the terminal: it may ask before it changes anything
--- on screen:
(no change)
```

## 11. Inline Edit Commit Here on change 103, a local commit in Review chains

**Expect:** Your own commit is edited where it is: ger edit runs in the terminal, and the status bar says what to do once it stops (not a review checkout, not a notification).

```
terminal "ger edit": runs `ger edit <sha>`
status bar message: ger edit runs in the terminal: once the rebase stops at "103: Cleanup after experiment", amend it, then git rebase --continue
--- on screen:
(no change)
```

## 12. Inline Review Change on change 900

**Expect:** Progress while it fetches; then where change 900 is checked out for review (the review worktree, as ger.reviewCheckout is not set), with a button to open that worktree in a new window.

```
progress (notification): Checking out 900: Add retry to the uploader
notification (info): Change 900 (patchset 1) is checked out for review on change-900 in <repo>-review, with its changes staged.  [Open Review Worktree]
    → pressed "Open Review Worktree"
new VS Code window on: <repo>-review
--- on screen:
(no change)
```

## 13. Change › … › Leave Review Checkout

**Expect:** The review checkout of 900 is closed, and a notification says so.

```
notification (info): Left the review of change 900.
--- on screen:
(no change)
```

## 14. Command palette › ger: Leave Review Checkout, with none open

**Expect:** It says there is no review checkout to leave (the Change view menu no longer offers it).

```
notification (info): No review checkout is open here.
--- on screen:
(no change)
```

## 15. Settings › ger.executable set to a program that is not installed

**Expect:** ger serve restarts with it and cannot start: the Gerrit views go empty and their welcome text says the program was not found, with Open Settings and Retry; the status bar shows a warning.

```
(nothing said or done outside the views)
--- on screen:
+ status bar | $(warning) Gerrit
+ state  | no-executable
+ events | (not said)
+ stack  | Your local stack shows here once ger runs. Review chains says what is missing and what to do next.
- status bar | $(error) CI failed on 102: test! temporary experiment
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
- change | No reviewers, awaiting review — V+1
- change | Commit message — Add retry to the uploader  (read-only)
- change | Showing — Patchset 1 (current) · Against its base commit
- change | Files — 1 · +1 −0
- change |   uploader.txt — added +1 −0
- change | Comments — 1 unresolved of 1
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
```

## 16. Review chains › Retry, the program still missing

**Expect:** It says it is restarting ger serve; the program is still missing, so the views still say so.

```
progress (notification): Restarting ger serve
--- on screen:
(no change)
```

## 17. Review chains › Install ger

**Expect:** A terminal opens and installs the ger release with this extension's version, with uv (the suite needs uv, so it is there); nothing else changes until Retry.

```
terminal "install ger": runs `uv tool install --force https://github.com/togrue/gerrit-workflow-tools/releases/download/v0.1.0/ger.tar.gz`
--- on screen:
(no change)
```

## 18. Settings › ger.executable set back

**Expect:** ger serve starts again at once, without a reload, and the views show what they showed before.

```
(nothing said or done outside the views)
--- on screen:
+ status bar | $(error) CI failed on 102: test! temporary experiment
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
+ change | No reviewers, awaiting review — V+1
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Showing — Patchset 1 (current) · Against its base commit
+ change | Files — 1 · +1 −0
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 unresolved of 1
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change | A change shows here once ger runs. Review chains says what is missing and what to do next.
```

## 19. Click "Extract command routing" in Local stack

**Expect:** The Change view is titled Change 101 and shows its commit message (editable: it is your commit), the local commit against its base, b.txt under Files with its added and removed lines, and both unresolved comments.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 101 — Extract command routing
+ change | Unresolved comments — V+1 CR+2 💬2
+ change | Commit message — Extract command routing  (editable)
+ change | Showing — Local commit <sha> · Against its base commit
- change | title: Change 900 — Add retry to the uploader
- change | No reviewers, awaiting review — V+1
- change | Commit message — Add retry to the uploader  (read-only)
- change | Showing — Patchset 1 (current) · Against its base commit
+ change |   b.txt — added +1 −0
+ change | Comments — 2 unresolved of 2
+ change |   Please rename this. — b.txt:1 · Alice Reviewer  [open]
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
- change |   uploader.txt — added +1 −0
- change | Comments — 1 unresolved of 1
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
```

## 20. Change › Compare › Gerrit vs. local

**Expect:** The files list becomes what your commit changes compared with Gerrit's patchset: nothing, as the commit is the pushed one.

```
(nothing said or done outside the views)
--- on screen:
+ change | Showing — Local commit <sha> · Gerrit vs. local
+ change | Gerrit vs. local: patchset 1 → local — 0 · +0 −0
- change | Showing — Local commit <sha> · Against its base commit
- change | Files — 1 · +1 −0
- change |   b.txt — added +1 −0
```

## 21. Change › Show › Gerrit's current patchset

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
- change | Gerrit vs. local: patchset 1 → local — 0 · +0 −0
```

## 22. Change › Show › Local commit

**Expect:** Back on your commit: the message is editable again.

```
(nothing said or done outside the views)
--- on screen:
+ change | Commit message — Extract command routing  (editable)
+ change | Showing — Local commit <sha> · Against its base commit
- change | Commit message — Extract command routing  (read-only)
- change | Showing — Patchset 1 (current) · Against its base commit
```

## 23. Change › Next Unresolved Comment (Alt+F9)

**Expect:** b.txt opens at line 1, the first thread expands, the status bar says Comment 1 of 2.

```
status bar message: Comment 1 of 2
--- on screen:
+ editor | b.txt (file, line 1)
- editor | Preview ger-brief-<id>.md (webview)
```

## 24. Next Unresolved Comment again

**Expect:** The second thread: Comment 2 of 2.

```
status bar message: Comment 2 of 2
--- on screen:
(no change)
```

## 25. Next Unresolved Comment a third time

**Expect:** It wraps around to Comment 1 of 2.

```
status bar message: Comment 1 of 2
--- on screen:
(no change)
```

## 26. Change › Previous Unresolved Comment

**Expect:** Back to Comment 2 of 2.

```
status bar message: Comment 2 of 2
--- on screen:
(no change)
```

## 27. Click b.txt under Files

**Expect:** A diff opens: the parent on the left, the commit on the right.

```
(nothing said or done outside the views)
--- on screen:
+ editor | b.txt (<sha>) (diff, ger-file ↔ ger-file)
- editor | b.txt (file, line 1)
```

## 28. Inline Keep for Me on "Is this the right design?"

**Expect:** The thread is marked as kept for you; the agent will leave it alone.

```
(nothing said or done outside the views)
--- on screen:
+ change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [open, kept for you]
- change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
```

## 29. Local stack › Fix Stack with Agent…

**Expect:** The runbook opens as a preview; a dialog counts what the agent gets, without the comment kept for you; Copy to Clipboard copies it.

```
progress (notification): Collecting comments and CI findings
dialog (info): 2 commits: 1 comment and 2 CI findings for the agent. 1 comment kept for you.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → pressed "Copy to Clipboard"
clipboard: 67 lines, starting "# Address the review feedback on this stack"
notification (info): Copied. Paste it into your agent, or set `ger.agentCommand` to start the agent from here.
--- on screen:
+ editor | Preview ger-runbook-<id>.md (webview)
- editor | b.txt (<sha>) (diff, ger-file ↔ ger-file)
```

## 30. Inline Let the Agent Handle It on "Is this the right design?"

**Expect:** The thread is no longer marked as kept for you.

```
(nothing said or done outside the views)
--- on screen:
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
- change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [open, kept for you]
```

## 31. Inline Reply as Draft… on "Please rename this."

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

## 32. Inline Reply as Draft… on "Please rename this." again

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

## 33. Inline Reply Done and Resolve as Draft on "Is this the right design?"

**Expect:** A "Done" draft that resolves the thread is saved at once; the thread shows that your draft resolves it.

```
status bar message: $(check) Draft saved, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [open]
+ change |     you — [draft] Done
- change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
```

## 34. Inline Reply Done and Resolve as Draft on "Please rename this.", which has your draft

**Expect:** Your draft stays as you wrote it and now resolves the thread.

```
status bar message: $(check) Draft updated, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |   Please rename this. — b.txt:1 · Alice Reviewer · 1 reply  [open]
```

## 35. Type in the editor thread of "Please rename this." › Save Draft Reply

**Expect:** What you type is added to your draft reply there, after what it already says; the status bar says the draft was updated.

```
status bar message: $(check) Draft updated. Publish it with ger push.
--- on screen:
(no change)
```

## 36. "+" on line 1 of b.txt in the diff › Save Draft

**Expect:** The comment is saved as a draft and appears as a thread marked as your draft; it is not counted as unresolved feedback.

```
status bar message: $(check) Draft saved on Gerrit, visible only to you. Publish it with ger push.
--- on screen:
+ change | Comments — 2 unresolved of 2 · 1 of yours, not published
- change | Comments — 2 unresolved of 2
+ change |   Should this be configurable? — your draft · b.txt:1  [yours]
```

## 37. "+" on line 1 of b.txt in the diff › Save Private Note

**Expect:** The note is saved in this repository only and appears marked as your private note; it is not counted as unresolved feedback.

```
status bar message: $(check) Private note saved here. It never leaves this repository.
--- on screen:
+ change | Comments — 2 unresolved of 2 · 2 of yours, not published
- change | Comments — 2 unresolved of 2 · 1 of yours, not published
+ change |   Remember to check the docs. — your private note · b.txt:1  [yours]
```

## 38. Change › Edit Commit Here

**Expect:** ger edit runs in the terminal; you are told what to do once the rebase stops at 101, not that it already has.

```
terminal "ger edit": runs `ger edit <sha>`
status bar message: ger edit runs in the terminal: once the rebase stops at "Extract command routing", amend it, then git rebase --continue
--- on screen:
(no change)
```

## 39. Change › Open in Gerrit

**Expect:** The browser opens change 101 on Gerrit.

```
browser opens: https://g.example/c/testproj/+/101
--- on screen:
(no change)
```

## 40. Click "test! temporary experiment" in Local stack

**Expect:** The Change view says CI failed; the CI group lists the findings, the ones this commit introduced first.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 102 — test! temporary experiment
+ change | CI failed — V-1 CR+2
+ change | Commit message — test! temporary experiment  (editable)
- change | title: Change 101 — Extract command routing
- change | Unresolved comments — V+1 CR+2 💬2
- change | Commit message — Extract command routing  (editable)
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI — failed
+ change |   Widget #55 — 2 findings
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
+ change |     FAILURE — generic · introduced here
- change |   b.txt — added +1 −0
- change | Comments — 2 unresolved of 2 · 2 of yours, not published
- change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |     you — [draft] Renamed it to route().
- change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [open]
- change |     you — [draft] Done
- change |   Should this be configurable? — your draft · b.txt:1  [yours]
- change |   Remember to check the docs. — your private note · b.txt:1  [yours]
```

## 41. Right-click it › Hand to Agent › Cancel

**Expect:** A runbook for this one commit and a dialog to start or copy it; Cancel does nothing more.

```
progress (notification): Collecting comments and CI findings
dialog (info): 1 commit: 0 comments and 2 CI findings for the agent.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → cancelled
--- on screen:
(no change)
```

## 42. Local stack › Push to Gerrit…

**Expect:** ger push runs in a new terminal of its own (never typed into one that is busy), which shows what it will push and asks first.

```
terminal "ger push": runs `ger push`
--- on screen:
(no change)
```

## 43. Right-click "Refactor parser init" › Copy Commit Hash

**Expect:** The commit's full id is on the clipboard; the status bar says so.

```
clipboard: 1 lines, starting "<sha>"
status bar message: $(check) Copied the commit hash
--- on screen:
(no change)
```

## 44. Select two rows, right-click › Copy Change-Id

**Expect:** Both Change-Ids are on the clipboard, one per line, newest first as shown.

```
clipboard: 2 lines, starting "I2222222222222222222222222222222222222222"
status bar message: $(check) Copied 2 Change-Ids
--- on screen:
(no change)
```

## 45. Drag "Refactor parser init" to the line above "Extract command routing"

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

## 46. Drag "Refactor parser init" to the bottom, then Undo

**Expect:** It swaps back with the commit below; Undo puts the stack back as it was before this move.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## 47. Select "Cleanup after experiment" and "Extract command routing", drag them to the bottom, then Undo

**Expect:** Both land under the last row, together and in the order they had; Undo puts the stack back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## Meanwhile: you edit a.txt and do not commit it

```
```

## 48. Drag "Extract command routing" up one row, with uncommitted changes › Cancel

**Expect:** A dialog asks whether to stash the uncommitted changes and continue; Cancel leaves the stack and the edit as they were.

```
progress (in view ger.stack): Rewriting the stack
dialog (warning): You have uncommitted changes. Stash them and continue?  [Stash and Continue] [Cancel]
    detail: They are stashed for the rewrite and put back right after it, also when the rewrite is refused.
    → cancelled
--- on screen:
(no change)
```

## 49. The same drag › Stash and Continue, then Undo

**Expect:** The commits swap, and the edit to a.txt is back in the worktree afterwards; Undo puts the stack back and keeps the edit.

```
progress (in view ger.stack): Rewriting the stack
dialog (warning): You have uncommitted changes. Stash them and continue?  [Stash and Continue] [Cancel]
    detail: They are stashed for the rewrite and put back right after it, also when the rewrite is refused.
    → pressed "Stash and Continue"
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## Meanwhile: you take the edit to a.txt back

```
```

## 50. Change › edit the commit message of "test! temporary experiment" › Apply, then Undo

**Expect:** The commit gets the new message and keeps its Change-Id, though the text typed had none; the commits above get new ids, nothing else changes. Undo puts the old message back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Commit message of "test! temporary experiment" changed. Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## Meanwhile: you commit a rework of d.txt in the terminal

```
+ stack  | Rework d.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | Cleanup after experiment — <sha> · held back · changed here since the last push  [review +2, ci bad]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · changed here since the last push  [review +2, ci bad]
```

## 51. Drag "Rework d.txt" to the line under "Cleanup after experiment"

**Expect:** One place down: the conflict preview already knows the two cannot swap, so it says at once which commit would conflict, and that nothing changed.

```
notification (info): "Rework d.txt" cannot change places with "Cleanup after experiment": "Rework d.txt" would conflict in d.txt. Nothing was changed.
--- on screen:
(no change)
```

## 52. Drag "Rework d.txt" to the bottom

**Expect:** Refused before anything is touched, saying which commit does not apply in its new place; the stack stays as it was.

```
progress (in view ger.stack): Rewriting the stack
notification (warning): "Rework d.txt" (<sha>) does not apply in its new place (conflict in d.txt); the stack was left as it was
--- on screen:
(no change)
```

## 53. Drag "Cleanup after experiment" to the line under "Extract command routing"

**Expect:** It lands there, and the commits between move up one; Undo is offered.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → dismissed
--- on screen:
- stack  | Cleanup after experiment — <sha> · held back · changed here since the last push  [review +2, ci bad]
+ stack  | Cleanup after experiment — <sha> · changed here since the last push  [review +2, ci bad]
```

## 54. Right-click "Rework d.txt" › Squash into Commit Below, then Undo

**Expect:** It folds into the commit below, keeping that commit's message; Undo brings it back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "test! temporary experiment". Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## 55. Right-click "Rework d.txt" › Squash into Commit Below, Keeping This Message, then Undo

**Expect:** Folds, keeping this commit's message, with a note that the Gerrit change of the one below stays open; Undo brings both back, and the Change view shows again what it showed.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "Rework d.txt". The Gerrit change of "test! temporary experiment" stays open: abandon it there. Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## 56. Right-click "Rework d.txt" › Drop Commit… › Cancel

**Expect:** A dialog asks to confirm; Cancel leaves everything as it was.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → cancelled
--- on screen:
(no change)
```

## 57. Right-click "Rework d.txt" › Drop Commit… › Drop, then Undo

**Expect:** The commit goes; Undo brings it back. It was never pushed, so there is nothing to push.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → pressed "Drop"
progress (in view ger.stack): Rewriting the stack
notification (info): Dropped "Rework d.txt".  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## 58. Right-click "Rework d.txt" › Drop Commit… › Drop

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
- change | CI — failed
- change |   Widget #55 — 2 findings
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6
- change |     FAILURE — generic
```

## 59. Local stack › … › Set Upstream Branch… › Escape

**Expect:** A picker lists the branches it could track, the current one marked; Escape changes nothing.

```
picker: Upstream for feature — "The local stack is the commits above it; ger push goes to its branch on Gerrit"
    origin/main — current · $(star-full) suggested: nearest: 4 ahead, 0 behind
    origin/feature
    → escaped
--- on screen:
(no change)
```

## Meanwhile: a background refresh finds ger serve gone and cannot start it again

**Expect:** Nothing is said and the views keep what they showed; the status bar item gets a warning mark, its tooltip says the answer is out of date, and Review chains is labelled so.

```
+ status bar | $(error) CI failed on 102: test! temporary experiment $(warning)
- status bar | $(error) CI failed on 102: test! temporary experiment
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
+ status bar | $(error) CI failed on 102: Refactor parser init
- status bar | $(error) CI failed on 102: test! temporary experiment $(warning)
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
+ status bar | $(error) CI failed on 102: Refactor parser init  $(bell-dot) 1
- status bar | $(error) CI failed on 102: Refactor parser init
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

## At the end

```
status bar | $(error) CI failed on 102: Refactor parser init  $(bell-dot) 1
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
stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
change | title: Change 102 — test! temporary experiment
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Showing — Local commit <sha> · Against its base commit
change | Files — 1 · +1 −0
change |   c.txt — added +1 −0
change | Comments — none
editor | Preview ger-runbook-<id>.md (webview)
```
