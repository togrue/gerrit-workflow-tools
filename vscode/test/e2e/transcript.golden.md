# What a user sees and gets, button by button

## On start

```
status bar | CI failed on 102: test! temporary experiment
chains | CI failed on 102: test! temporary experiment
chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url]
chains |     Cleanup after experiment — 103 · V-1 CR+2 · local  [ger.change.url]
chains |     test! temporary experiment — 102 · V-1 CR+2 · local  [ger.change.url]
chains |     Extract command routing — 101 · V+1 CR+2 💬2 · local  [ger.change.url]
chains |     Refactor parser init — 100 · V+1 CR+2 · local  [ger.change.url]
chains | To review — 1 chain
chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url]
stack  | Cleanup after experiment — HEAD · <sha> · V-1 CR+2 · held back  [ger.commit.url.down]
stack  | test! temporary experiment — <sha> · V-1 CR+2 · held back  [ger.commit.url.up.down]
stack  | Extract command routing — <sha> · V+1 CR+2 💬2  [ger.commit.url.up.down]
stack  | Refactor parser init — <sha> · V+1 CR+2  [ger.commit.url.up]
change | title: (nothing shown)
editor | (none)
```

## 1. Click the status bar item

**Expect:** The Change view opens on the change the status bar names (CI failed on 102).

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 102 — test! temporary experiment
+ change | CI failed — V-1 CR+2
+ change | Files — 1 · +1 −0
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI — failed — open to fetch the findings
+ change |   Widget #55 — 2 findings
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
+ change |     FAILURE — generic · introduced here
- change | title: (nothing shown)
```

## 2. Review chains › Mark All Activity as Seen

**Expect:** Every "● new activity" marker goes; nothing else changes.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 3. Review chains › Refresh from Gerrit

**Expect:** The views reload from Gerrit. With nothing new there, nothing changes.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 4. Click "Add retry to the uploader" under To review

**Expect:** The Change view shows change 900: what it needs from you, its files, its comments.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 900 — Add retry to the uploader
+ change | No reviewers, awaiting review — V+1
- change | title: Change 102 — test! temporary experiment
- change | CI failed — V-1 CR+2
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 unresolved of 1
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [ger.thread.open.free]
- change |   c.txt — added +1 −0
- change | Comments — none
- change | CI — failed — open to fetch the findings
- change |   Widget #55 — 2 findings
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
- change |     FAILURE — generic · introduced here
```

## 5. Inline Open in Gerrit on change 900

**Expect:** The browser opens change 900 on Gerrit.

```
browser opens: https://g.example/c/testproj/+/900
--- on screen:
(no change)
```

## 6. Right-click change 900 › Hand to Agent

**Expect:** As for your own commits: the comments and CI findings of 900 open as a preview, and a dialog offers to copy them (no agent command is set).

```
progress (notification): Collecting comments and CI findings
dialog (info): The comments and CI findings of 900: Add retry to the uploader, for the agent.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → pressed "Copy to Clipboard"
clipboard: 12 lines, starting "## <sha> — Add retry to the uploader"
notification (info): Copied. Paste it into your agent, or set `ger.agentCommand` to start the agent from here.
--- on screen:
+ editor | Preview ger-brief-<id>.md (webview)
- editor | (none)
```

## 7. Inline Check Out for Review on change 900

**Expect:** Progress while it fetches; then where change 900 is checked out, with a button to open that worktree in a new window.

```
progress (notification): Checking out 900: Add retry to the uploader
notification (info): Change 900 (patchset 1) is checked out, staged, on change-900 in <repo>-review.  [Open Review Worktree]
    → pressed "Open Review Worktree"
new VS Code window on: <repo>-review
--- on screen:
(no change)
```

## 8. Change › … › Leave Review Checkout

**Expect:** The review checkout of 900 is closed, and a notification says so.

```
notification (info): Left the review of change 900.
--- on screen:
(no change)
```

## 9. Command palette › ger: Leave Review Checkout, with none open

**Expect:** It says there is no review checkout to leave (the Change view menu no longer offers it).

```
notification (info): No review checkout is open here.
--- on screen:
(no change)
```

## 10. Click "Extract command routing" in Local stack

**Expect:** The Change view is titled Change 101 and shows b.txt under Files and both unresolved comments.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 101 — Extract command routing
+ change | Unresolved comments — V+1 CR+2 💬2
- change | title: Change 900 — Add retry to the uploader
- change | No reviewers, awaiting review — V+1
+ change |   b.txt — added +1 −0
+ change | Comments — 2 unresolved of 2
+ change |   Please rename this. — b.txt:1 · Alice Reviewer  [ger.thread.open.free]
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [ger.thread.open.free]
- change |   uploader.txt — added +1 −0
- change | Comments — 1 unresolved of 1
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [ger.thread.open.free]
```

## 11. Change › Next Unresolved Comment (Alt+F9)

**Expect:** b.txt opens at line 1, the first thread expands, the status bar says Comment 1 of 2.

```
status bar message: Comment 1 of 2
--- on screen:
+ editor | b.txt (file, line 1)
- editor | Preview ger-brief-<id>.md (webview)
```

## 12. Next Unresolved Comment again

**Expect:** The second thread: Comment 2 of 2.

```
status bar message: Comment 2 of 2
--- on screen:
(no change)
```

## 13. Next Unresolved Comment a third time

**Expect:** It wraps around to Comment 1 of 2.

```
status bar message: Comment 1 of 2
--- on screen:
(no change)
```

## 14. Change › Previous Unresolved Comment

**Expect:** Back to Comment 2 of 2.

```
status bar message: Comment 2 of 2
--- on screen:
(no change)
```

## 15. Click b.txt under Files

**Expect:** A diff opens: the parent on the left, the commit on the right.

```
(nothing said or done outside the views)
--- on screen:
+ editor | b.txt (<sha>) (diff, ger-file ↔ ger-file)
- editor | b.txt (file, line 1)
```

## 16. Inline Keep for Me on "Is this the right design?"

**Expect:** The thread is marked as kept for you; the agent will leave it alone.

```
(nothing said or done outside the views)
--- on screen:
+ change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [ger.thread.open.held]
- change |   Is this the right design? — b.txt:1 · Bob Architect  [ger.thread.open.free]
```

## 17. Local stack › Fix Stack with Agent…

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

## 18. Inline Let the Agent Handle It on "Is this the right design?"

**Expect:** The thread is no longer marked as kept for you.

```
(nothing said or done outside the views)
--- on screen:
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [ger.thread.open.free]
- change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [ger.thread.open.held]
```

## 19. Inline Reply (Draft)… on "Please rename this."

**Expect:** An input box asks for the reply; after Enter the draft shows under the thread and the status bar says it was saved.

```
input box: Reply to Alice Reviewer — b.txt:1
    prompt: Saved as a draft on Gerrit. Drafts are published with `ger push` or `ger review --vote`.
    placeholder: Please rename this.
    → typed "Renamed it." and pressed Enter
status bar message: $(check) Draft saved. Publish it with ger push.
--- on screen:
+ change |   Please rename this. — b.txt:1 · Alice Reviewer · 1 reply  [ger.thread.open.free]
+ change |     you — [draft] Renamed it.
- change |   Please rename this. — b.txt:1 · Alice Reviewer  [ger.thread.open.free]
```

## 20. Inline Reply (Draft)… on "Please rename this." again

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

## 21. Inline Reply Done and Resolve on "Is this the right design?"

**Expect:** A "Done" draft that resolves the thread is saved at once; the thread shows that your draft resolves it.

```
status bar message: $(check) Draft saved, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [ger.thread.open.free]
+ change |     you — [draft] Done
- change |   Is this the right design? — b.txt:1 · Bob Architect  [ger.thread.open.free]
```

## 22. Inline Reply Done and Resolve on "Please rename this.", which has your draft

**Expect:** Your draft stays as you wrote it and now resolves the thread.

```
status bar message: $(check) Draft updated, resolving the comment. Publish it with ger push.
--- on screen:
+ change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [ger.thread.open.free]
- change |   Please rename this. — b.txt:1 · Alice Reviewer · 1 reply  [ger.thread.open.free]
```

## 23. Type in the editor thread of "Please rename this." › Save Draft Reply

**Expect:** What you type is added to your draft reply there, after what it already says; the status bar says the draft was updated.

```
status bar message: $(check) Draft updated. Publish it with ger push.
--- on screen:
(no change)
```

## 24. "+" on line 1 of b.txt in the diff › Save Draft

**Expect:** The comment is saved as a draft and appears as a thread marked as your draft; it is not counted as unresolved feedback.

```
status bar message: $(check) Draft saved on Gerrit, visible only to you. Publish it with ger push.
--- on screen:
+ change | Comments — 2 unresolved of 2 · 1 of yours, not published
- change | Comments — 2 unresolved of 2
+ change |   Should this be configurable? — your draft · b.txt:1  [ger.mine]
```

## 25. "+" on line 1 of b.txt in the diff › Save Private Note

**Expect:** The note is saved in this repository only and appears marked as your private note; it is not counted as unresolved feedback.

```
status bar message: $(check) Private note saved here. It never leaves this repository.
--- on screen:
+ change | Comments — 2 unresolved of 2 · 2 of yours, not published
- change | Comments — 2 unresolved of 2 · 1 of yours, not published
+ change |   Remember to check the docs. — your private note · b.txt:1  [ger.mine]
```

## 26. Change › Edit Commit Here

**Expect:** ger edit runs in the terminal; you are told what to do once the rebase stops at 101, not that it already has.

```
terminal "ger edit": runs `ger edit <sha>`
notification (info): Started `ger edit` in the terminal. Once the rebase stops at "Extract command routing", amend it, then run `git rebase --continue`.
--- on screen:
(no change)
```

## 27. Change › Open in Gerrit

**Expect:** The browser opens change 101 on Gerrit.

```
browser opens: https://g.example/c/testproj/+/101
--- on screen:
(no change)
```

## 28. Click "test! temporary experiment" in Local stack

**Expect:** The Change view says CI failed; the CI group lists the findings, the ones this commit introduced first.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 102 — test! temporary experiment
+ change | CI failed — V-1 CR+2
- change | title: Change 101 — Extract command routing
- change | Unresolved comments — V+1 CR+2 💬2
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI — failed — open to fetch the findings
+ change |   Widget #55 — 2 findings
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
+ change |     FAILURE — generic · introduced here
- change |   b.txt — added +1 −0
- change | Comments — 2 unresolved of 2 · 2 of yours, not published
- change |   Please rename this. — your draft resolves it · b.txt:1 · Alice Reviewer · 1 reply  [ger.thread.open.free]
- change |     you — [draft] Renamed it to route().
- change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [ger.thread.open.free]
- change |     you — [draft] Done
- change |   Should this be configurable? — your draft · b.txt:1  [ger.mine]
- change |   Remember to check the docs. — your private note · b.txt:1  [ger.mine]
```

## 29. Right-click it › Hand to Agent › Cancel

**Expect:** A runbook for this one commit and a dialog to start or copy it; Cancel does nothing more.

```
progress (notification): Collecting comments and CI findings
dialog (info): 1 commit: 0 comments and 2 CI findings for the agent.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → cancelled
--- on screen:
(no change)
```

## 30. Local stack › Push to Gerrit…

**Expect:** ger push runs in a new terminal of its own (never typed into one that is busy), which shows what it will push and asks first.

```
terminal "ger push": runs `ger push`
--- on screen:
(no change)
```

## 31. Inline ↑ on "Refactor parser init"

**Expect:** It swaps places with the commit above it; a notification offers Undo. Every commit keeps its votes: none changed its code, so Gerrit keeps them on the push.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → dismissed
--- on screen:
+ stack  | Cleanup after experiment — HEAD · <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.down]
+ stack  | test! temporary experiment — <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.up.down]
+ stack  | Refactor parser init — <sha> · V+1 CR+2 · ahead of Gerrit  [ger.commit.url.up.down]
+ stack  | Extract command routing — <sha> · V+1 CR+2 💬2 · ahead of Gerrit  [ger.commit.url.up]
- stack  | Cleanup after experiment — HEAD · <sha> · V-1 CR+2 · held back  [ger.commit.url.down]
- stack  | test! temporary experiment — <sha> · V-1 CR+2 · held back  [ger.commit.url.up.down]
- stack  | Extract command routing — <sha> · V+1 CR+2 💬2  [ger.commit.url.up.down]
- stack  | Refactor parser init — <sha> · V+1 CR+2  [ger.commit.url.up]
+ change | Local changes not pushed, CI failed — V-1 CR+2
+ change | This commit is ahead of Gerrit — only rebased: the votes stay when you push
- change | CI failed — V-1 CR+2
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6
+ change |     FAILURE — generic
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
- change |     FAILURE — generic · introduced here
```

## 32. Inline ↓ on "Refactor parser init", then Undo

**Expect:** It swaps back with the commit below; Undo puts the stack back as it was before this move.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## Meanwhile: you commit a rework of d.txt in the terminal

```
+ stack  | Rework d.txt — HEAD · <sha> · not on Gerrit · held back  [ger.commit.downx]
+ stack  | Cleanup after experiment — <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.upx.down]
- stack  | Cleanup after experiment — HEAD · <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.down]
```

## 33. Inline ⊘ (Moving Earlier Would Conflict) on "Rework d.txt"

**Expect:** It says which commit it would conflict with, and that nothing changed.

```
notification (info): "Rework d.txt" cannot change places with "Cleanup after experiment": "Rework d.txt" would conflict in d.txt. Nothing was changed.
--- on screen:
(no change)
```

## 34. Drag "Rework d.txt" onto "Cleanup after experiment"

**Expect:** Refused before anything is touched, saying why; the stack stays as it was.

```
notification (info): "Rework d.txt" cannot change places with "Cleanup after experiment": "Rework d.txt" would conflict in d.txt. Nothing was changed.
--- on screen:
(no change)
```

## 35. Drag "Cleanup after experiment" onto "Extract command routing"

**Expect:** It takes that commit's place, and the commits between move up one; Undo is offered.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → dismissed
--- on screen:
+ stack  | Rework d.txt — HEAD · <sha> · not on Gerrit · held back  [ger.commit.down]
- stack  | Rework d.txt — HEAD · <sha> · not on Gerrit · held back  [ger.commit.downx]
- stack  | Cleanup after experiment — <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.upx.down]
+ stack  | Extract command routing — <sha> · V+1 CR+2 💬2 · ahead of Gerrit  [ger.commit.url.up.down]
+ stack  | Cleanup after experiment — <sha> · V-1 CR+2 · ahead of Gerrit  [ger.commit.url.up]
- stack  | Extract command routing — <sha> · V+1 CR+2 💬2 · ahead of Gerrit  [ger.commit.url.up]
```

## 36. Right-click "Rework d.txt" › Squash into Commit Below, then Undo

**Expect:** It folds into the commit below, keeping that commit's message; Undo brings it back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "test! temporary experiment". Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## 37. Right-click "Rework d.txt" › Squash into Commit Below, Keeping This Message, then Undo

**Expect:** Folds, keeping this commit's message, with a note that the Gerrit change of the one below stays open; Undo brings both back, and the Change view shows again what it showed.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "Rework d.txt". The Gerrit change of "test! temporary experiment" stays open: abandon it there. Push to update Gerrit.  [Undo]
    → pressed "Undo"
--- on screen:
(no change)
```

## 38. Right-click "Rework d.txt" › Drop Commit… › Cancel

**Expect:** A dialog asks to confirm; Cancel leaves everything as it was.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → cancelled
--- on screen:
(no change)
```

## 39. Right-click "Rework d.txt" › Drop Commit… › Drop, then Undo

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

## 40. Right-click "Rework d.txt" › Drop Commit… › Drop

**Expect:** The commit is gone from the stack and from the checkout; nothing to push, as Gerrit never had it.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → pressed "Drop"
progress (in view ger.stack): Rewriting the stack
notification (info): Dropped "Rework d.txt".  [Undo]
    → dismissed
--- on screen:
+ stack  | test! temporary experiment — HEAD · <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.down]
- stack  | Rework d.txt — HEAD · <sha> · not on Gerrit · held back  [ger.commit.down]
- stack  | test! temporary experiment — <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.up.down]
```

## Meanwhile: you change the code of the top commit and amend it

**Expect:** Its votes now show as the last pushed patchset's (Gerrit may not keep them for this code); the Change view follows the commit and says so.

```
+ stack  | test! temporary experiment — HEAD · <sha> · last pushed V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.down]
- stack  | test! temporary experiment — HEAD · <sha> · V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.down]
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
+ change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | Local changes not pushed, CI failed — V-1 CR+2
- change | This commit is ahead of Gerrit — only rebased: the votes stay when you push
- change | CI — failed — open to fetch the findings
- change |   Widget #55 — 2 findings
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6
- change |     FAILURE — generic
```

## 41. Local stack › … › Set Upstream Branch… › Escape

**Expect:** A picker lists the branches it could track, the current one marked; Escape changes nothing.

```
picker: Upstream for feature — "The local stack is the commits above it; ger push goes to its branch on Gerrit"
    origin/main — current · $(star-full) suggested: nearest: 4 ahead, 0 behind
    origin/feature
    → escaped
--- on screen:
(no change)
```

## At the end

```
status bar | CI failed on 102: test! temporary experiment
chains | CI failed on 102: test! temporary experiment
chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 · active <age> ago  [ger.chain.url]
chains |     Cleanup after experiment — 103 · V-1 CR+2 · local  [ger.change.url]
chains |     test! temporary experiment — 102 · V-1 CR+2 · local  [ger.change.url]
chains |     Extract command routing — 101 · V+1 CR+2 💬2 · local  [ger.change.url]
chains |     Refactor parser init — 100 · V+1 CR+2 · local  [ger.change.url]
chains | To review — 1 chain
chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url]
stack  | test! temporary experiment — HEAD · <sha> · last pushed V-1 CR+2 · ahead of Gerrit · held back  [ger.commit.url.down]
stack  | Refactor parser init — <sha> · V+1 CR+2 · ahead of Gerrit  [ger.commit.url.up.down]
stack  | Extract command routing — <sha> · V+1 CR+2 💬2 · ahead of Gerrit  [ger.commit.url.up.down]
stack  | Cleanup after experiment — <sha> · V-1 CR+2 · ahead of Gerrit  [ger.commit.url.up]
change | title: Change 102 — test! temporary experiment
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | This commit is ahead of Gerrit — its code changed: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Files — 1 · +1 −0
change |   c.txt — added +1 −0
change | Comments — none
editor | Preview ger-runbook-<id>.md (webview)
```
