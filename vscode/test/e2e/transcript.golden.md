# What a user sees and gets, button by button

## On start

```
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
state  | ready
events | live
chains | 2 unresolved comments on 101: Extract command routing
chains | To review — 1 chain
chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
change | title: (nothing shown)
change | Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.
editor | (none)
```

## 1. Click the status bar item

**Expect:** The Change view opens on the change the status bar names (the comments on 101: the oldest change that needs an edit, below the failed CI on 102).

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 101
+ change | Unresolved comments — V+1 CR+2 💬2 open
+ change | [Next comment] — 2 comments left to answer here, 2 not gone to yet (Alt+Enter)
+ change | Commit message — Extract command routing  (editable)
+ change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
+ change | Files — 1 · +1 −0
+ change |   b.txt — added +1 −0
+ change | Comments — 2 to answer
+ change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer  [open]
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
+ change |   Looks good overall. — whole change · Alice Reviewer  [resolved]
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
    By chain state — current · What needs you most comes first
    By last activity, newest first
    By last activity, oldest first
    → picked "By last activity, oldest first"
status bar message: $(check) Chains sorted by last activity, oldest first
--- on screen:
+ chains | To review — 1 chain · oldest first
- chains | To review — 1 chain
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments · oldest first
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
```

## 4. Review chains › Sort Chains… › By chain state

**Expect:** Back to the order that puts what needs you first; the sections no longer name an order.

```
picker: Sort review chains — "Applies to both sections"
    By chain state · What needs you most comes first
    By last activity, newest first
    By last activity, oldest first — current
    → picked "By chain state"
status bar message: $(check) Chains sorted by chain state
--- on screen:
+ chains | To review — 1 chain
- chains | To review — 1 chain · oldest first
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments · oldest first
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

**Expect:** The Change view shows change 900: what it needs from you, its files, its comments. Not yours to reword, its message is plain text, its Change-Id under it.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 900
+ change | Waiting for your review — V+1
+ change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
- change | title: Change 101
- change | Unresolved comments — V+1 CR+2 💬2 open
- change | [Next comment] — 2 comments left to answer here, 2 not gone to yet (Alt+Enter)
- change | Commit message — Extract command routing  (editable)
- change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 to read
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change |   b.txt — added +1 −0
- change | Comments — 2 to answer
- change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer  [open]
- change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
- change |   Looks good overall. — whole change · Alice Reviewer  [resolved]
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
clipboard: 16 lines, starting "## <sha> — Add retry to the uploader"
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
status bar message: $(terminal) ger edit runs in the ger terminal: once the rebase stops at "103: Cleanup after experiment", amend it, then ger rebase --continue
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
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
- state  | ready
- events | live
- chains | 2 unresolved comments on 101: Extract command routing
- chains | To review — 1 chain
- chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
- chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
- chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
- chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | A change shows here once ger runs. Review chains says what is missing and what to do next.
- change | Waiting for your review — V+1
- change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
- change | Commit message — Add retry to the uploader  (read-only)
- change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
- change | Files — 1 · +1 −0
- change |   uploader.txt — added +1 −0
- change | Comments — 1 to read
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
```

## 19. Review chains › Retry, the program still missing

**Expect:** It says it is restarting ger serve; the program is still missing, so the views still say so.

```
progress (notification): Restarting ger serve
notification (warning): Still not working: there is no program "ger-not-installed": install ger, or set ger.executable to how to run it (its full path works too)
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
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
+ state  | ready
+ events | live
+ chains | 2 unresolved comments on 101: Extract command routing
+ chains | To review — 1 chain
+ chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
+ chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
+ chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
+ chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
+ stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
- status bar | $(warning) Gerrit
- state  | no-executable
- events | (not said)
- stack  | Your local stack shows here once ger runs. Review chains says what is missing and what to do next.
+ change | Waiting for your review — V+1
+ change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
+ change | Files — 1 · +1 −0
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 to read
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change | A change shows here once ger runs. Review chains says what is missing and what to do next.
```

## 22. In a terminal, ger.webUrl becomes http://gerrit.example; then Review chains › Refresh from Gerrit

**Expect:** ger will not send credentials over plain http. Review chains' welcome text says that is why, and names the two ways out (the https:// address, or ger.allowInsecureHttp for a test Gerrit) with Run ger setup and Retry; it does not claim the address is missing. Local stack and Change say ger's own words, with Run ger setup; so does the notification, as you asked for the refresh.

```
progress (in view ger.overview): Refreshing from Gerrit
progress (in view ger.stack): Refreshing from Gerrit
notification (warning): Refresh failed: ger.webUrl must use https:// (got 'http://gerrit.example'): credentials go with every request. For a local test Gerrit on plain http, opt in with `git config ger.allowInsecureHttp true`
--- on screen:
+ status bar | $(warning) Gerrit
+ state  | setup
+ chains | (welcome text for setup: insecure-http)
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
- state  | ready
+ stack  | Your local stack shows here once ger can ask Gerrit. ger says: ger.webUrl must use https:// (got 'http://gerrit.example'): credentials go with every request. For a local test Gerrit on plain http, opt in with `git config ger.allowInsecureHttp true`
- chains | 2 unresolved comments on 101: Extract command routing
- chains | To review — 1 chain
- chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
- chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
- chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
- chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | A change shows here once ger can ask Gerrit. ger says: ger.webUrl must use https:// (got 'http://gerrit.example'): credentials go with every request. For a local test Gerrit on plain http, opt in with `git config ger.allowInsecureHttp true`
- change | Waiting for your review — V+1
- change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
- change | Commit message — Add retry to the uploader  (read-only)
- change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
- change | Files — 1 · +1 −0
- change |   uploader.txt — added +1 −0
- change | Comments — 1 to read
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
```

## 23. Review chains › Retry, nothing fixed

**Expect:** It restarts ger serve, and says what it found: the same refusal, in a notification.

```
progress (notification): Restarting ger serve
notification (warning): Still not working: ger.webUrl must use https:// (got 'http://gerrit.example'): credentials go with every request. For a local test Gerrit on plain http, opt in with `git config ger.allowInsecureHttp true`
--- on screen:
+ events | (not said)
- events | live
```

## 24. In a terminal, ger.webUrl is the https:// address again; then Review chains › Retry

**Expect:** ger serve starts, and the views show what they showed before.

```
progress (notification): Restarting ger serve
status bar message: $(check) Refreshed from Gerrit
--- on screen:
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
+ state  | ready
+ events | live
+ chains | 2 unresolved comments on 101: Extract command routing
+ chains | To review — 1 chain
+ chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
+ chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
+ chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
+ chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
+ stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
- status bar | $(warning) Gerrit
- state  | setup
- chains | (welcome text for setup: insecure-http)
- events | (not said)
- stack  | Your local stack shows here once ger can ask Gerrit. ger says: ger.webUrl must use https:// (got 'http://gerrit.example'): credentials go with every request. For a local test Gerrit on plain http, opt in with `git config ger.allowInsecureHttp true`
+ change | Waiting for your review — V+1
+ change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
+ change | Files — 1 · +1 −0
+ change |   uploader.txt — added +1 −0
+ change | Comments — 1 to read
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change | A change shows here once ger can ask Gerrit. ger says: ger.webUrl must use https:// (got 'http://gerrit.example'): credentials go with every request. For a local test Gerrit on plain http, opt in with `git config ger.allowInsecureHttp true`
```

## 25. Click "Extract command routing" in Local stack

**Expect:** The Change view is titled Change 101 and shows its commit message (editable: it is your commit), the local commit against its base, b.txt under Files with its added and removed lines, and both unresolved comments.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 101
+ change | Unresolved comments — V+1 CR+2 💬2 open
+ change | [Next comment] — 2 comments left to answer here, 2 not gone to yet (Alt+Enter)
+ change | Commit message — Extract command routing  (editable)
+ change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
- change | title: Change 900
- change | Waiting for your review — V+1
- change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
- change | Commit message — Add retry to the uploader  (read-only)
- change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
+ change |   b.txt — added +1 −0
+ change | Comments — 2 to answer
+ change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer  [open]
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
+ change |   Looks good overall. — whole change · Alice Reviewer  [resolved]
- change |   uploader.txt — added +1 −0
- change | Comments — 1 to read
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
```

## 26. Change › From › Patchset 1 (current)

**Expect:** From Gerrit's current patchset to your commit: the files list becomes what your commit changes compared with it — nothing, as the commit is the pushed one. The mode toggle goes dim: picked by hand.

```
(nothing said or done outside the views)
--- on screen:
+ change | Comparing — Patchset 1 (current) → Local commit <sha> · default: Against its base commit (picked by hand)
+ change | Gerrit vs. local — identical to patchset 1 — nothing to push
- change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
- change | Files — 1 · +1 −0
- change |   b.txt — added +1 −0
```

## 27. Change › To › Patchset 1 (current)

**Expect:** To at From's patchset: From goes back to the base commit. The view shows patchset 1 as Gerrit has it, against its base; its message is read-only.

```
(nothing said or done outside the views)
--- on screen:
+ change | Commit message — Extract command routing  (read-only)
+ change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit (picked by hand)
+ change | Files — 1 · +1 −0
+ change |   b.txt — added +1 −0
- change | Commit message — Extract command routing  (editable)
- change | Comparing — Patchset 1 (current) → Local commit <sha> · default: Against its base commit (picked by hand)
- change | Gerrit vs. local — identical to patchset 1 — nothing to push
```

## 28. Change › To › Local commit

**Expect:** Back on your commit: the message is editable again.

```
(nothing said or done outside the views)
--- on screen:
+ change | Commit message — Extract command routing  (editable)
+ change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
- change | Commit message — Extract command routing  (read-only)
- change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit (picked by hand)
```

## 29. Change › default mode toggle (to Gerrit vs. local)

**Expect:** The default becomes Gerrit vs. local and the change shown follows it: Gerrit's current patchset against your commit, the toggle lit.

```
(nothing said or done outside the views)
--- on screen:
+ change | Comparing — Patchset 1 (current) → Local commit <sha> · default: Gerrit vs. local
+ change | Gerrit vs. local — identical to patchset 1 — nothing to push
- change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
- change | Files — 1 · +1 −0
- change |   b.txt — added +1 −0
```

## 30. Change › From › Base commit

**Expect:** Picked by hand: the change's files against its base, the default still Gerrit vs. local.

```
(nothing said or done outside the views)
--- on screen:
+ change | Comparing — Base commit → Local commit <sha> · default: Gerrit vs. local (picked by hand)
+ change | Files — 1 · +1 −0
+ change |   b.txt — added +1 −0
- change | Comparing — Patchset 1 (current) → Local commit <sha> · default: Gerrit vs. local
- change | Gerrit vs. local — identical to patchset 1 — nothing to push
```

## 31. Click "Extract command routing" in Local stack again

**Expect:** Opened anew, the pick by hand is gone: the change starts on the remembered default, Gerrit vs. local.

```
(nothing said or done outside the views)
--- on screen:
+ change | Comparing — Patchset 1 (current) → Local commit <sha> · default: Gerrit vs. local
+ change | Gerrit vs. local — identical to patchset 1 — nothing to push
- change | Comparing — Base commit → Local commit <sha> · default: Gerrit vs. local (picked by hand)
- change | Files — 1 · +1 −0
- change |   b.txt — added +1 −0
```

## 32. Change › default mode toggle (Against its base commit)

**Expect:** The default is the base comparison again: base commit → your commit.

```
(nothing said or done outside the views)
--- on screen:
+ change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
+ change | Files — 1 · +1 −0
+ change |   b.txt — added +1 −0
- change | Comparing — Patchset 1 (current) → Local commit <sha> · default: Gerrit vs. local
- change | Gerrit vs. local — identical to patchset 1 — nothing to push
```

## 33. Change › Next Unresolved Comment (Alt+F9)

**Expect:** b.txt opens at line 1, the first thread expands, the status bar says Comment 1 of 2.

```
status bar message: Comment 1 of 2 to answer
--- on screen:
+ change | [Next comment] — 2 comments left to answer here, 1 not gone to yet (Alt+Enter)
- change | [Next comment] — 2 comments left to answer here, 2 not gone to yet (Alt+Enter)
+ editor | b.txt (file, line 1)
- editor | Preview ger-brief-<id>.md (webview)
```

## 34. Next Unresolved Comment again

**Expect:** The second thread: Comment 2 of 2.

```
status bar message: Comment 2 of 2 to answer
--- on screen:
+ change | [Next comment] — 2 comments left to answer here, gone to, not answered yet (Alt+Enter)
- change | [Next comment] — 2 comments left to answer here, 1 not gone to yet (Alt+Enter)
```

## 35. Next Unresolved Comment a third time

**Expect:** It wraps around to Comment 1 of 2.

```
status bar message: Comment 1 of 2 to answer
--- on screen:
(no change)
```

## 36. Change › Previous Unresolved Comment

**Expect:** Back to Comment 2 of 2.

```
status bar message: Comment 2 of 2 to answer
--- on screen:
(no change)
```

## 37. Click "Please rename this." in the Change view

**Expect:** b.txt opens at line 1 with that comment's thread expanded there, the other one closed.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 38. Click b.txt under Files

**Expect:** b.txt opens on its own: the commit adds it, so there is nothing to compare it with.

```
(nothing said or done outside the views)
--- on screen:
+ editor | b.txt (ger-file, line 1)
- editor | b.txt (file, line 1)
```

## 39. Inline Keep for Me on "Is this the right design?"

**Expect:** The thread is marked as kept for you; the agent will leave it alone.

```
status bar message: $(check) Kept for you: the agent leaves this comment alone
--- on screen:
+ change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [open, kept for you]
- change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
```

## 40. Local stack › Fix Stack with Agent…

**Expect:** The runbook opens as a preview; a dialog counts what the agent gets, without the comment kept for you; Copy to Clipboard copies it.

```
progress (notification): Collecting comments and CI findings
dialog (info): 2 commits: 1 comment and 1 CI finding for the agent. 1 comment kept for you.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → pressed "Copy to Clipboard"
clipboard: 73 lines, starting "# Address the review feedback on this stack"
notification (info): Copied. Paste it into your agent, or set `ger.agentCommand` to start the agent from here.
--- on screen:
+ editor | Preview ger-runbook-<id>.md (webview)
- editor | b.txt (ger-file, line 1)
```

## 41. Inline Let the Agent Handle It on "Is this the right design?"

**Expect:** The thread is no longer marked as kept for you.

```
status bar message: $(check) Left to the agent
--- on screen:
+ change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
- change |   Is this the right design? — kept for you · b.txt:1 · Bob Architect  [open, kept for you]
```

## 42. Inline Reply as Draft… on "Please rename this."

**Expect:** An input box asks for the reply; after Enter the draft shows under the thread and the status bar says it was saved.

```
reply box: Reply to Alice Reviewer — b.txt:1
    prompt: Saved as a draft, the comment left unresolved; visible only to you: your next push publishes it.
    Resolved toggle: No
    placeholder: Please rename this.
    → typed "Renamed it." and pressed Enter
notification (info): Draft saved, visible only to you: your next push publishes it.  [Publish Drafts…]
    → dismissed
--- on screen:
+ status bar | $(comment-unresolved) 1 unresolved comment on 101: Extract command routing
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
+ chains | 1 unresolved comment on 101: Extract command routing
- chains | 2 unresolved comments on 101: Extract command routing
+ chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open ✎1 · active <age> ago  [ger.chain.url.local]
- chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open ✎1 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
+ change | Unresolved comments, drafts to publish with the push — V+1 CR+2 💬2 open ✎1
+ change | [Next comment] — 1 comment left to answer here, gone to, not answered yet (Alt+Enter)
- change | Unresolved comments — V+1 CR+2 💬2 open
- change | [Next comment] — 2 comments left to answer here, gone to, not answered yet (Alt+Enter)
+ change | Comments — 1 to answer · 1 answered by your drafts (push publishes them)
+ change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
+ change |     you — [draft] Renamed it.
- change | Comments — 2 to answer
- change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer  [open]
```

## 43. Next Unresolved Comment (Alt+F9) after the reply

**Expect:** Your draft answers "Please rename this.", so the walk leaves it out and goes on where it stood, not back at 1: Comment 1 of 1 to answer, "Is this the right design?".

```
status bar message: Comment 1 of 1 to answer
--- on screen:
+ editor | b.txt (file, line 1)
- editor | Preview ger-runbook-<id>.md (webview)
```

## 44. Inline Reply as Draft… on "Please rename this." again

**Expect:** The box opens with your draft in it, to edit; saving updates that draft, nothing you wrote is lost unseen.

```
reply box: Edit your draft reply — b.txt:1
    prompt: Saved as a draft, the comment left unresolved; visible only to you: your next push publishes it.
    Resolved toggle: No
    placeholder: Renamed it.
    already filled in: "Renamed it."
    → changed it to "Renamed it to route()." and pressed Enter
status bar message: $(check) Draft updated, visible only to you: your next push publishes it.
--- on screen:
+ change |     you — [draft] Renamed it to route().
- change |     you — [draft] Renamed it.
```

## 45. Inline Reply Done and Resolve as Draft on "Is this the right design?"

**Expect:** A "Done" draft that resolves the thread is saved at once; the thread shows that your draft resolves it.

```
status bar message: $(check) Draft saved, resolving the comment, visible only to you: your next push publishes it.
--- on screen:
+ status bar | $(error) CI failed: 102
- status bar | $(comment-unresolved) 1 unresolved comment on 101: Extract command routing
+ chains | CI failed on 102: test! temporary experiment
- chains | 1 unresolved comment on 101: Extract command routing
+ chains | Outgoing — 1 chain · 1 with failed CI
+ chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open ✎2 · active <age> ago  [ger.chain.url.local]
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
- chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open ✎1 · active <age> ago  [ger.chain.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open ✎2 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open ✎1 · in your stack  [ger.change.url.local]
+ change | Drafts to publish with the push — V+1 CR+2 💬2 open ✎2
+ change | [Work on next] — Work on Next (Alt+Enter)
- change | Unresolved comments, drafts to publish with the push — V+1 CR+2 💬2 open ✎1
- change | [Next comment] — 1 comment left to answer here, gone to, not answered yet (Alt+Enter)
+ change | Comments — 2 answered by your drafts (push publishes them)
- change | Comments — 1 to answer · 1 answered by your drafts (push publishes them)
+ change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [open]
+ change |     you — [draft] Done
- change |   Is this the right design? — b.txt:1 · Bob Architect  [open]
```

## 46. Next Unresolved Comment (Alt+F9) once both are answered

**Expect:** Nothing is left to answer: the status bar says so, and that 2 are answered by your drafts, which your next push publishes.

```
status bar message: $(pass) No comments left to answer on this change · 2 answered by your drafts (your next push publishes them)
--- on screen:
(no change)
```

## 47. Inline Reply Done and Resolve as Draft on "Please rename this.", which has your draft

**Expect:** Your draft stays as you wrote it and now resolves the thread.

```
status bar message: $(check) Draft updated, resolving the comment, visible only to you: your next push publishes it.
--- on screen:
+ change |   Please rename this. — your draft resolves it · suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
```

## 48. Reply as Draft… on "Looks good overall." (about the whole change) in the Change view: type a reply, Resolved left on › Enter

**Expect:** The comment has no line in a file, so its row answers it: the reply box opens at the top of the window (the side bar has no box), the reply is saved as a draft, as a reply in an editor thread is, and shows under it; the status bar says it was saved, the comment staying resolved.

```
reply box: Reply to Alice Reviewer — /PATCHSET_LEVEL
    prompt: Saved as a draft that resolves the comment; visible only to you: your next push publishes it.
    Resolved toggle: Yes
    placeholder: Looks good overall.
    → typed "Added a test for the parser." and pressed Enter
status bar message: $(check) Draft saved, resolving the comment, visible only to you: your next push publishes it.
--- on screen:
+ change |   Looks good overall. — whole change · Alice Reviewer · 1 reply  [resolved]
+ change |     you — [draft] Added a test for the parser.
- change |   Looks good overall. — whole change · Alice Reviewer  [resolved]
```

## 49. Click "Please rename this." in the Change view again

**Expect:** b.txt is in front (the walk brought it back before the runbook) with that thread expanded: Alice's comment and its suggested fix, your draft reply under it, the reply box, and the Resolved toggle on in its header.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 50. Type in the editor thread of "Please rename this.", whose Resolved toggle is on (your draft resolves it) › Save

**Expect:** What you type is added to your draft reply there, after what it already says, and it still resolves the comment; the status bar says the draft was updated.

```
status bar message: $(check) Draft updated, resolving the comment, visible only to you: your next push publishes it.
--- on screen:
(no change)
```

## 51. Click the Resolved toggle in the header of the editor thread of "Please rename this."

**Expect:** Your draft there is saved again at once, now leaving the comment unresolved; the status bar says so.

```
status bar message: $(check) Draft updated, leaving the comment unresolved, visible only to you: your next push publishes it.
--- on screen:
+ change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |   Please rename this. — your draft resolves it · suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
```

## 52. Click the Resolved toggle in the header of the editor thread of "Please rename this." again

**Expect:** Your draft resolves the comment again, as before.

```
status bar message: $(check) Draft updated, resolving the comment, visible only to you: your next push publishes it.
--- on screen:
+ change |   Please rename this. — your draft resolves it · suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |   Please rename this. — suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
```

## 53. "+" on line 1 of b.txt in the diff › Save Draft

**Expect:** The comment is saved as a draft and appears as a thread marked as your draft; it is not counted as unresolved feedback.

```
status bar message: $(check) Draft saved on Gerrit, visible only to you: your next push publishes it.
--- on screen:
+ change | Comments — 2 answered by your drafts (push publishes them) · 1 of yours, not published
- change | Comments — 2 answered by your drafts (push publishes them)
+ change |   Should this be configurable? — your draft · b.txt:1  [yours]
```

## 54. "+" on line 1 of b.txt in the diff › Save Private Note

**Expect:** The note is saved in this repository only and appears marked as your private note; it is not counted as unresolved feedback.

```
status bar message: $(check) Private note saved here. It never leaves this repository.
--- on screen:
+ change | Comments — 2 answered by your drafts (push publishes them) · 2 of yours, not published
- change | Comments — 2 answered by your drafts (push publishes them) · 1 of yours, not published
+ change |   Remember to check the docs. — your private note · b.txt:1  [yours]
```

## 55. Inline Edit… on your comment "Should this be configurable?" in the Change view

**Expect:** b.txt opens at its line and the comment turns into a text box in its thread, with Save and Cancel; nothing is saved yet.

```
(nothing said or done outside the views)
--- on screen:
+ editor | b.txt (file)
- editor | b.txt (file, line 1)
```

## 56. Change the text in that box › Save

**Expect:** Your comment says the new text, in the thread and in the Change view; the status bar says it was updated. It is still yours and not published.

```
status bar message: $(check) Draft updated.
--- on screen:
+ change |   Should this be a setting? — your draft · b.txt:1  [yours]
- change |   Should this be configurable? — your draft · b.txt:1  [yours]
+ editor | b.txt (file, line 1)
- editor | b.txt (file)
```

## 57. Pencil on your private note in its editor thread, change the text › Cancel

**Expect:** The note says what it said before; nothing is saved and nothing is said.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 58. Inline Delete Your Draft Reply… on "Is this the right design?" › Cancel

**Expect:** A dialog shows the draft reply and asks before deleting it; Cancel leaves it.

```
dialog (warning): Delete your draft reply on b.txt:1?  [Delete] [Cancel]
    detail: Done
    → cancelled
--- on screen:
(no change)
```

## 59. Trash on your private note in its editor thread › Delete

**Expect:** A dialog shows the note and asks; Delete removes it from the thread and from the Change view, and the status bar says so.

```
dialog (warning): Delete your private note on b.txt:1?  [Delete] [Cancel]
    detail: Remember to check the docs.
    → pressed "Delete"
status bar message: $(check) Private note deleted.
--- on screen:
+ change | Comments — 2 answered by your drafts (push publishes them) · 1 of yours, not published
- change | Comments — 2 answered by your drafts (push publishes them) · 2 of yours, not published
- change |   Remember to check the docs. — your private note · b.txt:1  [yours]
```

## 60. Command palette › ger: Add Comment, with your own b.txt in the editor

**Expect:** The file in the editor is yours as it is now, not change 101 as shown (its menu does not offer Add Comment): it says where a comment on 101 goes, and opens no box.

```
notification (info): These are not the lines of "Extract command routing", the change shown. Open one of its files from the Change view (the right-hand side of a diff) and add the comment there.
--- on screen:
(no change)
```

## 61. Open b.txt of the commit, cursor on line 1 › Add Comment (Ctrl+Alt+C), type › Save

**Expect:** A comment box opens on that line with the same Save buttons as the "+" in the gutter; saving it adds your comment to the change.

```
status bar message: $(check) Draft saved on Gerrit, visible only to you: your next push publishes it.
--- on screen:
+ change | Comments — 2 answered by your drafts (push publishes them) · 2 of yours, not published
- change | Comments — 2 answered by your drafts (push publishes them) · 1 of yours, not published
+ change |   Added from the keyboard. — your draft · b.txt:1  [yours]
+ editor | b.txt (ger-file, line 1)
- editor | b.txt (file, line 1)
```

## 62. Add Comment again › Cancel

**Expect:** The box closes; nothing is saved.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## 63. Inline Comment on This File… on b.txt under Files

**Expect:** An input box asks for a comment about the whole file; after Enter it is saved like any new comment and shows under Comments as yours.

```
input box: Comment on b.txt
    prompt: About the whole file. Saved as a draft on Gerrit, visible only to you until it is published.
    → typed "This file needs a header." and pressed Enter
status bar message: $(check) Draft saved on Gerrit, visible only to you: your next push publishes it.
--- on screen:
+ change | Comments — 2 answered by your drafts (push publishes them) · 3 of yours, not published
+ change |   This file needs a header. — your draft · b.txt  [yours]
- change | Comments — 2 answered by your drafts (push publishes them) · 2 of yours, not published
```

## 64. Change › Edit Commit Here

**Expect:** ger edit runs in the terminal; you are told what to do once the rebase stops at 101, not that it already has.

```
terminal "ger": runs `ger edit <sha>`
status bar message: $(terminal) ger edit runs in the ger terminal: once the rebase stops at "Extract command routing", amend it, then ger rebase --continue
--- on screen:
(no change)
```

## 65. Change › Open in Gerrit

**Expect:** The browser opens change 101 on Gerrit.

```
status bar message: $(check) Opened 101 in the browser
browser opens: https://g.example/c/testproj/+/101
--- on screen:
(no change)
```

## 66. Click "test! temporary experiment" in Local stack

**Expect:** The Change view says CI failed; the CI group lists the findings, the ones this commit introduced first.

```
(nothing said or done outside the views)
--- on screen:
+ change | title: Change 102
+ change | CI failed — V-1 CR+2
- change | title: Change 101
- change | Drafts to publish with the push — V+1 CR+2 💬2 open ✎2
+ change | Commit message — test! temporary experiment  (editable)
- change | Commit message — Extract command routing  (editable)
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI
+ change |   Widget #55 — 1 finding
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
- change |   b.txt — added +1 −0
- change | Comments — 2 answered by your drafts (push publishes them) · 3 of yours, not published
- change |   This file needs a header. — your draft · b.txt  [yours]
- change |   Please rename this. — your draft resolves it · suggested edit · b.txt:1 · Alice Reviewer · 1 reply  [open]
- change |     you — [draft] Renamed it to route().
- change |   Is this the right design? — your draft resolves it · b.txt:1 · Bob Architect · 1 reply  [open]
- change |     you — [draft] Done
- change |   Should this be a setting? — your draft · b.txt:1  [yours]
- change |   Added from the keyboard. — your draft · b.txt:1  [yours]
- change |   Looks good overall. — whole change · Alice Reviewer · 1 reply  [resolved]
- change |     you — [draft] Added a test for the parser.
```

## 67. Right-click it › Hand to Agent › Cancel

**Expect:** A runbook for this one commit and a dialog to start or copy it; Cancel does nothing more.

```
progress (notification): Collecting comments and CI findings
dialog (info): 1 commit: 0 comments and 1 CI finding for the agent.  [Copy to Clipboard] [Cancel]
    detail: The agent amends commits and writes draft replies. Nothing is pushed or published: you review what it did and push it yourself.
    → cancelled
--- on screen:
+ editor | Preview ger-runbook-<id>.md (webview)
- editor | b.txt (ger-file, line 1)
```

## 68. Local stack › Push to Gerrit…

**Expect:** ger push runs in the ger terminal (a new one when a command still runs there), which shows what it will push and asks first.

```
terminal "ger": runs `ger push`
status bar message: $(terminal) ger push runs in the ger terminal: it shows what it will push and asks first
--- on screen:
(no change)
```

## 69. Right-click "Refactor parser init" › Copy SHA

**Expect:** The commit's full id is on the clipboard; the status bar says so.

```
clipboard: 1 lines, starting "<sha>"
status bar message: $(check) Copied SHA <sha>…
--- on screen:
(no change)
```

## 70. Select two rows, right-click › Copy Change-Id

**Expect:** Both Change-Ids are on the clipboard, one per line, newest first as shown.

```
clipboard: 2 lines, starting "I2222222222222222222222222222222222222222"
status bar message: $(check) Copied 2 Change-Ids
--- on screen:
(no change)
```

## 71. Drag "Refactor parser init" to the line above "Extract command routing"

**Expect:** It swaps places with the commit above it; a notification offers Undo. Every commit keeps its votes: none changed its code, so Gerrit keeps them on the push.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → dismissed
--- on screen:
+ stack  | Cleanup after experiment — HEAD · <sha> · held back · only rebased since the last push  [review +2, ci bad]
+ stack  | test! temporary experiment — <sha> · held back · only rebased since the last push  [review +2, ci bad]
+ stack  | Refactor parser init — <sha> · only rebased since the last push  [review +2, ci good]
+ stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | Local changes not pushed, CI failed — V-1 CR+2
- change | CI failed — V-1 CR+2
+ change | This commit is ahead of Gerrit — only rebased since the last push: the same code, by patch-id (whitespace and the message aside), so Gerrit usually keeps the votes when you push
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · introduced here
```

## 72. Drag "Refactor parser init" to the bottom, then Undo

**Expect:** It swaps back with the commit below; Undo puts the stack back as it was before this move.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## 73. Select "Cleanup after experiment" and "Extract command routing", drag them to the bottom, then Undo

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

## 74. Drag "Extract command routing" up one row, with uncommitted changes › Cancel

**Expect:** A dialog asks whether to stash the uncommitted changes and continue; Cancel leaves the stack and the edit as they were.

```
progress (in view ger.stack): Rewriting the stack
dialog (warning): You have uncommitted changes. Stash them and continue?  [Stash and Continue] [Cancel]
    detail: They are stashed for the rewrite and put back right after it, also when the rewrite is refused.
    → cancelled
--- on screen:
(no change)
```

## 75. The same drag › Stash and Continue, then Undo

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

## 76. Change › edit the commit message of "test! temporary experiment" › Apply, then Undo

**Expect:** The box holds the message without its Change-Id. The commit gets the new message and keeps its Change-Id, though the text typed named another; the commits above get new ids, nothing else changes. Undo puts the old message back.

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
+ stack  | Cleanup after experiment — <sha> · held back · only rebased since the last push  [review +2, ci bad]
- stack  | Cleanup after experiment — HEAD · <sha> · held back · only rebased since the last push  [review +2, ci bad]
```

## 77. Drag "Rework d.txt" to the line under "Cleanup after experiment"

**Expect:** One place down: the conflict preview already knows the two cannot swap, so it says at once which commit would conflict, and that nothing changed.

```
notification (info): "Rework d.txt" cannot change places with "Cleanup after experiment": "Rework d.txt" would conflict in d.txt. Nothing was changed.
--- on screen:
(no change)
```

## 78. Drag "Rework d.txt" to the bottom

**Expect:** Refused before anything is touched, saying which commit does not apply in its new place; the stack stays as it was.

```
progress (in view ger.stack): Rewriting the stack
notification (warning): "Rework d.txt" (<sha>) does not apply in its new place (conflict in d.txt); the stack was left as it was
--- on screen:
(no change)
```

## 79. Drag "Cleanup after experiment" to the line under "Extract command routing"

**Expect:** It lands there, and the commits between move up one; Undo is offered.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Stack reordered. Push to update Gerrit.  [Undo]
    → dismissed
--- on screen:
- stack  | Cleanup after experiment — <sha> · held back · only rebased since the last push  [review +2, ci bad]
+ stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci bad]
```

## 80. Right-click "Rework d.txt" › Squash into Commit Below, then Undo

**Expect:** It folds into the commit below, keeping that commit's message; Undo brings it back.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "test! temporary experiment". Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## 81. Right-click "Rework d.txt" › Squash into Commit Below, Keeping This Message, then Undo

**Expect:** Folds, keeping this commit's message, with a note that the Gerrit change of the one below stays open; Undo brings both back, and the Change view shows again what it showed.

```
progress (in view ger.stack): Rewriting the stack
notification (info): Squashed into "Rework d.txt". The Gerrit change of "test! temporary experiment" stays open: abandon it there. Push to update Gerrit.  [Undo]
    → pressed "Undo"
status bar message: $(check) Undone: the stack is as it was
--- on screen:
(no change)
```

## 82. Right-click "Rework d.txt" › Drop Commit… › Cancel

**Expect:** A dialog asks to confirm; Cancel leaves everything as it was.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → cancelled
--- on screen:
(no change)
```

## 83. Right-click "Rework d.txt" › Drop Commit… › Drop, then Undo

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

## 84. Right-click "Rework d.txt" › Drop Commit… › Drop

**Expect:** The commit is gone from the stack and from the checkout; nothing to push, as Gerrit never had it.

```
dialog (warning): Drop "Rework d.txt" from the stack?  [Drop] [Cancel]
    detail: It was never pushed: after the drop it exists only in the reflog. You can undo the drop right after.
    → pressed "Drop"
progress (in view ger.stack): Rewriting the stack
notification (info): Dropped "Rework d.txt".  [Undo]
    → dismissed
--- on screen:
+ stack  | test! temporary experiment — HEAD · <sha> · held back · only rebased since the last push  [review +2, ci bad]
- stack  | Rework d.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · held back · only rebased since the last push  [review +2, ci bad]
```

## Meanwhile: you change the code of the top commit and amend it

**Expect:** Its votes now show as the last pushed patchset's (Gerrit may not keep them for this code); the Change view follows the commit and says so.

```
+ stack  | test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | test! temporary experiment — HEAD · <sha> · held back · only rebased since the last push  [review +2, ci bad]
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
- change | Local changes not pushed, CI failed — V-1 CR+2
+ change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | This commit is ahead of Gerrit — only rebased since the last push: the same code, by patch-id (whitespace and the message aside), so Gerrit usually keeps the votes when you push
+ change |   Widget #55 — from patchset 1, before your local changes · 1 finding
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
- change |   Widget #55 — 1 finding
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6
```

## 85. Local stack › … › Set Upstream Branch… › Escape

**Expect:** A picker lists the branches it could track, the current one marked; Escape changes nothing.

```
picker: Upstream for feature — "The local stack is the commits above it; ger push goes to its branch on Gerrit"
    origin/main — current · $(star-full) suggested: nearest: 4 ahead, 0 behind
    origin/feature
    → escaped
--- on screen:
(no change)
```

## 86. Review chains › … › Report Issue… › Copy and Open GitHub Issue

**Expect:** The report opens first, as an untitled JSON document to read: what the three views show, with names, paths, text and ids replaced. A notification says so, and that nothing has left the machine. Copy and Open GitHub Issue puts the report on the clipboard and opens a new issue that has only the versions in it and says to paste the report.

```
untitled json document opens: 394 lines, starting
    {
      "about": "What the ger views showed, for a bug report. Names, paths, text and ids are replaced by made-up ones of the same shape; states, counts and the extension's own words are kept. No log, no settings, no folder names.",
notification (info): This is the report, and all that would be shared: names, paths, text and ids in it are replaced. Nothing has left this machine.  [Copy and Open GitHub Issue] [Save to File…]
    → pressed "Copy and Open GitHub Issue"
clipboard: 394 lines, starting "{"
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

## 87. Command palette › ger: Report Issue… › Save to File…

**Expect:** The report opens again to read; Save to File… asks where, suggesting ger-report.json, and the status bar says where it was saved. Nothing is copied and no browser opens.

```
untitled json document opens: 394 lines, starting
    {
      "about": "What the ger views showed, for a bug report. Names, paths, text and ids are replaced by made-up ones of the same shape; states, counts and the extension's own words are kept. No log, no settings, no folder names.",
notification (info): This is the report, and all that would be shared: names, paths, text and ids in it are replaced. Nothing has left this machine.  [Copy and Open GitHub Issue] [Save to File…]
    → pressed "Save to File…"
save dialog: suggests ger-report.json
    → saved 394 lines as ~/ger-report.json
status bar message: $(check) Report saved to ~/ger-report.json
--- on screen:
(no change)
```

## 88. Command palette › ger: Report Issue… › close the report without saving › Copy and Open GitHub Issue

**Expect:** The user may have taken things out of the report before closing it, so nothing is copied, not the original either, and no browser opens; a notification says nothing was shared and to run Report Issue… again.

```
untitled json document opens: 394 lines, starting
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
notification (info): 2 unresolved comments on 101: Extract command routing  [Open]
    → dismissed
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
- status bar | $(error) CI failed: 102 $(warning)
+ events | live
+ chains | 2 unresolved comments on 101: Extract command routing
- events | (not said)
- chains | CI failed on 102: test! temporary experiment
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
+ chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
+ chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
+ chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
+ stack  | test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
- chains | Outgoing — 1 chain · 1 with failed CI
- chains |   Cleanup after experiment — 4 changes · V-1 CR+2 💬2 open ✎2 · active <age> ago  [ger.chain.url.local]
- chains |     Cleanup after experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     test! temporary experiment — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open ✎2 · in your stack  [ger.change.url.local]
- chains |     Refactor parser init — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
- stack  | test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · only rebased since the last push  [review +2, ci good]
- stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci bad]
```

## Meanwhile: ger serve restarts as a ger whose API is 2.0, while the extension needs 1.1

**Expect:** The views refuse it rather than break on it: Local stack and the Change view say ger speaks API 2.0 and the extension needs 1.1, and that the extension is what to update, with a button to install the ger that matches it; the status bar shows a warning.

```
+ status bar | $(warning) Gerrit
+ state  | incompatible
+ events | (not said)
+ stack  | ger serve speaks API 2.0, the extension needs 1.4: update the extension, or install the ger release that matches it.
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
- state  | ready
- events | live
- chains | 2 unresolved comments on 101: Extract command routing
- chains | To review — 1 chain
- chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
- chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
- chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
- chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
- chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
- stack  | test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | ger serve speaks API 2.0, the extension needs 1.4: update the extension, or install the ger release that matches it.
- change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
- change | [Work on next] — Work on Next (Alt+Enter)
- change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | Commit message — test! temporary experiment  (editable)
- change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
- change | Files — 1 · +1 −0
- change |   c.txt — added +1 −0
- change | Comments — none
- change | CI
- change |   Widget #55 — from patchset 1, before your local changes · 1 finding
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
```

## Meanwhile: ger is back at the matching release, and you press Retry

**Expect:** ger serve starts again, its API fits, and the views show the stack and the chains as before.

```
progress (notification): Restarting ger serve
status bar message: $(check) Refreshed from Gerrit
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
+ state  | ready
+ events | live
+ chains | 2 unresolved comments on 101: Extract command routing
+ chains | To review — 1 chain
+ chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
+ chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
+ chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
+ chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
+ chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
+ stack  | test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
+ change | title: Change 103
+ change | CI failed — V-1 CR+2
+ change | [Work on next] — Work on Next (Alt+Enter)
+ change | Commit message — test! temporary experiment  (editable)
+ change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
+ change | Files — 1 · +1 −0
+ change |   c.txt — added +1 −0
+ change | Comments — none
+ change | CI
+ change |   Widget #55 — 1 finding
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
- status bar | $(warning) Gerrit
- state  | incompatible
- events | (not said)
- stack  | ger serve speaks API 2.0, the extension needs 1.4: update the extension, or install the ger release that matches it.
- change | title: Change 102
- change | ger serve speaks API 2.0, the extension needs 1.4: update the extension, or install the ger release that matches it.
```

## Meanwhile: a reviewer comments on change 900 on Gerrit, and its event stream says so

**Expect:** With no click and no poll, Review chains shows the new unresolved comment on 900 within a few seconds; nothing is said.

```
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
+ chains | To review — 1 chain · 1 with comments
+ chains |   Add retry to the uploader — ● new activity · alice · 1 change · V+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
- chains | To review — 1 chain
- chains |   Add retry to the uploader — alice · 1 change · V+1 · waiting <age> · active <age> ago  [ger.change.url.toreview]
```

## Meanwhile: you commit in the terminal, with no click and no focus change

**Expect:** Within a second or two Local stack shows the new commit on top (not yet on Gerrit); nothing is said, as you did it yourself.

```
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
```

## 89. Click "test! temporary experiment" in Local stack

**Expect:** The Change view shows change 103 as it is now: the commit is the same as on Gerrit. It should have shown that already after ger serve restarted (it kept Change 102's heading and "ahead of Gerrit", no issue yet); this click makes the rebase steps start from a fresh view.

```
(nothing said or done outside the views)
--- on screen:
(no change)
```

## Meanwhile: you start ger edit's rebase on "test! temporary experiment", and it stops there

**Expect:** Local stack says a rebase is underway, with Continue and Abort, and marks that commit as where it stopped, with HEAD on it; the commits below are done, "Add e.txt" is dimmed, still to be replayed. The header says the branch is being rebased. The Change view of 103 says its message cannot be edited during the rebase. The status bar says the rebase stopped at 103.

```
+ status bar | $(warning) Rebase stopped at 103  $(bell-dot) 1
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
+ chains | Rebase stopped at 103: test! temporary experiment
- chains | 2 unresolved comments on 101: Extract command routing
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
+ change | ▌ Left: 2 comments to answer · CI failed on 2 commits · 3 commits need edits · 1 commit to replay
+ change | ▌   1. Cleanup after experiment — replayed
+ change | ▌   2. Extract command routing — replayed · 2 to answer
+ change | ▌   3. Refactor parser init — replayed · CI failed
+ change | ▌ ▸ 4. test! temporary experiment — stopped here · needs edits · CI failed
+ change | ▌   5. Add e.txt — to come
+ change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
+ change | ▌ [Abort] put the branch back as it was before the rebase
+ change | Commit message — test! temporary experiment  (read-only)
- change | [Work on next] — Work on Next (Alt+Enter)
- change | Commit message — test! temporary experiment  (editable)
--- shown now:
status bar | $(warning) Rebase stopped at 103  $(bell-dot) 1
stack header | (view not drawn)
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
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 2 comments to answer · CI failed on 2 commits · 3 commits need edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed · CI failed
change | ▌ ▸ 4. test! temporary experiment — stopped here · needs edits · CI failed
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
change | ▌ [Abort] put the branch back as it was before the rebase
change | CI failed — V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   c.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
change | (the commit shown is HEAD)
```

## 90. you add a line to c.txt, do not amend it, and press Local stack › Work on Next

**Expect:** Nothing runs in the terminal: a warning says Work on Next did not continue the rebase, in ger's words: c.txt has unstaged changes, amend them in or stash them. The views stay as they were.

```
notification (warning): Work on Next did not continue the rebase: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
+ change | ▌ Left: c.txt not amended · 2 comments to answer · CI failed on 2 commits · 3 commits need edits · 1 commit to replay
+ change | ▌ ⚠ Work on Next did not continue the rebase: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
- change | ▌ Left: 2 comments to answer · CI failed on 2 commits · 3 commits need edits · 1 commit to replay
+ change | ▌ [Continue] (not now: c.txt changed, not amended: amend or stash it first)
- change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
+ change | Changed, not amended — 1 file
+ change |   ⚠ c.txt — changed, not amended
--- shown now:
status bar | $(warning) Rebase stopped at 103  $(bell-dot) 1
stack header | (view not drawn)
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
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: c.txt not amended · 2 comments to answer · CI failed on 2 commits · 3 commits need edits · 1 commit to replay
change | ▌ ⚠ Work on Next did not continue the rebase: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed · CI failed
change | ▌ ▸ 4. test! temporary experiment — stopped here · needs edits · CI failed
change | ▌   5. Add e.txt — to come
change | ▌ [Continue] (not now: c.txt changed, not amended: amend or stash it first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | CI failed — V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Changed, not amended — 1 file
change |   ⚠ c.txt — changed, not amended
change | Files — 1 · +1 −0
change |   c.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
change | (the commit shown is HEAD)
```

## 91. Local stack › Continue, c.txt still not amended

**Expect:** The same: nothing runs, and a warning says why ger rebase --continue did not run.

```
notification (warning): ger rebase --continue did not run: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
+ change | ▌ ⚠ ger rebase --continue did not run: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
- change | ▌ ⚠ Work on Next did not continue the rebase: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
--- shown now:
status bar | $(warning) Rebase stopped at 103  $(bell-dot) 1
stack header | (view not drawn)
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
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: c.txt not amended · 2 comments to answer · CI failed on 2 commits · 3 commits need edits · 1 commit to replay
change | ▌ ⚠ ger rebase --continue did not run: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed · CI failed
change | ▌ ▸ 4. test! temporary experiment — stopped here · needs edits · CI failed
change | ▌   5. Add e.txt — to come
change | ▌ [Continue] (not now: c.txt changed, not amended: amend or stash it first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | CI failed — V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Changed, not amended — 1 file
change |   ⚠ c.txt — changed, not amended
change | Files — 1 · +1 −0
change |   c.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
change | (the commit shown is HEAD)
```

## Meanwhile: you add a line to c.txt and amend the stopped commit

**Expect:** The stopped commit shows what it is now: a new SHA, HEAD, its code changed since the last push. The Change view follows it to the amended commit: c.txt with +2 lines.

```
+ stack  | ▶ test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | ▶ test! temporary experiment — HEAD · <sha> · held back · the same as on Gerrit  [review +2, ci bad]
+ change | ▌ Left: 2 comments to answer · CI failed on 1 commit · 2 commits need edits · 1 commit to replay
- change | ▌ Left: c.txt not amended · 2 comments to answer · CI failed on 2 commits · 3 commits need edits · 1 commit to replay
- change | ▌ ⚠ ger rebase --continue did not run: You have unstaged changes to c.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
+ change | ▌ ▸ 4. test! temporary experiment — stopped here
- change | ▌ ▸ 4. test! temporary experiment — stopped here · needs edits · CI failed
+ change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
- change | ▌ [Continue] (not now: c.txt changed, not amended: amend or stash it first)
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
- change | CI failed — V-1 CR+2
+ change | Files — 1 · +2 −0
+ change |   c.txt — added +2 −0
- change | Changed, not amended — 1 file
- change |   ⚠ c.txt — changed, not amended
- change | Files — 1 · +1 −0
- change |   c.txt — added +1 −0
+ change |   Widget #55 — from patchset 1, before your local changes · 1 finding
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
- change |   Widget #55 — 1 finding
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 · also fails on the parent
--- shown now:
status bar | $(warning) Rebase stopped at 103  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 2 comments to answer · CI failed on 1 commit · 2 commits need edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed · CI failed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
change | ▌ [Abort] put the branch back as it was before the rebase
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is HEAD)
```

## 92. Local stack › Continue (the rebase stopped at the amended commit)

**Expect:** ger rebase --continue runs in the ger terminal, and the status bar says so; once it has run, the task header and the marks go: the branch is back, the amended commit with its code changed since the last push and "Add e.txt" on top of it; the header names the branch; the Change view shows the amended commit, its message editable again.

```
terminal "ger": runs `ger rebase --continue`
status bar message: $(terminal) ger rebase --continue runs in the ger terminal: the views follow once the rebase moves on
notification (info): The rebase is finished. 2 commits of your stack still need edits.  [Work on Next]
    → dismissed
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
- status bar | $(warning) Rebase stopped at 103  $(bell-dot) 1
+ chains | 2 unresolved comments on 101: Extract command routing
- chains | Rebase stopped at 103: test! temporary experiment
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | ▶ test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
- change | ▌ Left: 2 comments to answer · CI failed on 1 commit · 2 commits need edits · 1 commit to replay
- change | ▌   1. Cleanup after experiment — replayed
- change | ▌   2. Extract command routing — replayed · 2 to answer
- change | ▌   3. Refactor parser init — replayed · CI failed
- change | ▌ ▸ 4. test! temporary experiment — stopped here
- change | ▌   5. Add e.txt — to come
- change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
- change | ▌ [Abort] put the branch back as it was before the rebase
+ change | [Work on next] — Work on Next (Alt+Enter)
+ change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
+ change | Commit message — test! temporary experiment  (editable)
- change | Commit message — test! temporary experiment  (read-only)
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
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
change | [Work on next] — Work on Next (Alt+Enter)
change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is below HEAD)
```

## Meanwhile: you stop at "Refactor parser init" the same way, and amend it to also add e.txt

**Expect:** Local stack marks the rebase stopped at that commit, which shows its new code (its code changed since the last push) and HEAD; the two above are dimmed, still to be replayed. The status bar says the rebase stopped at 102.

```
+ status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
+ chains | Rebase stopped at 102: Refactor parser init
- chains | 2 unresolved comments on 101: Extract command routing
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
+ stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · the same as on Gerrit  [review +2, ci bad]
+ change | title: Change 102
+ change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
+ change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
+ change | ▌   1. Cleanup after experiment — replayed
+ change | ▌   2. Extract command routing — replayed · 2 to answer
+ change | ▌ ▸ 3. Refactor parser init — stopped here
+ change | ▌   4. test! temporary experiment — to come
+ change | ▌   5. Add e.txt — to come
+ change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
+ change | ▌ [Abort] put the branch back as it was before the rebase
- change | title: Change 103
+ change | Commit message — Refactor parser init  (read-only)
- change | [Work on next] — Work on Next (Alt+Enter)
- change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | Commit message — test! temporary experiment  (editable)
+ change | Files — 2 · +2 −0
+ change |   a.txt — added +1 −0
+ change |   e.txt — added +1 −0
- change | Files — 1 · +2 −0
- change |   c.txt — added +2 −0
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 102
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
change | ▌ [Abort] put the branch back as it was before the rebase
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | Commit message — Refactor parser init  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 2 · +2 −0
change |   a.txt — added +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is HEAD)
```

## Meanwhile: you run git rebase --continue, and "Add e.txt" conflicts in e.txt

**Expect:** Local stack's task header says Reworking your chain, the change it stopped at and what is left, Continue (not now, in core's words), Abort and Skip with what each does, and lists e.txt under it; "Add e.txt" has a red conflict icon and is not dimmed; the commits below are done, showing what they are now. The status bar says Conflict in "Add e.txt".

```
+ status bar | $(error) Conflict in "Add e.txt"  $(bell-dot) 1
- status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
+ chains | Conflict in "Add e.txt"
- chains | Rebase stopped at 102: Refactor parser init
+ stack  |   ⚠ e.txt — conflicted
+ stack  | ⚠ Add e.txt — <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
- stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ change | title: Change
+ change | ▌ Reworking your chain: Add e.txt — change 5 of 5  [All chains]
+ change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits
- change | title: Change 102
- change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
- change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
+ change | ▌   3. Refactor parser init — replayed
+ change | ▌   4. test! temporary experiment — replayed
+ change | ▌ ▸ 5. Add e.txt — stopped here
+ change | ▌ [Continue] (not now: resolve e.txt first)
- change | ▌ ▸ 3. Refactor parser init — stopped here
- change | ▌   4. test! temporary experiment — to come
- change | ▌   5. Add e.txt — to come
- change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
+ change | ▌ [Skip] leave this commit out and go on
+ change | No Change-Id — not on Gerrit
+ change | [Resolve e.txt] — it is still conflicted: open e.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
+ change | Commit message — Add e.txt  (read-only)
- change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
- change | Commit message — Refactor parser init  (read-only)
+ change | Conflicts — 1 file
+ change |   ⚠ e.txt — conflicted
+ change | Files, as before the rebase — 1 · +1 −0
- change | Files — 2 · +2 −0
- change |   a.txt — added +1 −0
- change | CI
- change |   Widget #55 — from patchset 1, before your local changes · 1 finding
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
--- shown now:
status bar | $(error) Conflict in "Add e.txt"  $(bell-dot) 1
stack header | (view not drawn)
stack  |   ⚠ e.txt — conflicted
stack  | ⚠ Add e.txt — <sha> · held back · not on Gerrit
stack  | test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change
change | ▌ Reworking your chain: Add e.txt — change 5 of 5  [All chains]
change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌   4. test! temporary experiment — replayed
change | ▌ ▸ 5. Add e.txt — stopped here
change | ▌ [Continue] (not now: resolve e.txt first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | No Change-Id — not on Gerrit
change | [Resolve e.txt] — it is still conflicted: open e.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
change | Commit message — Add e.txt  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Conflicts — 1 file
change |   ⚠ e.txt — conflicted
change | Files, as before the rebase — 1 · +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | (the commit shown is only on the branch as it was before)
```

## 93. Local stack › click e.txt under the task header

**Expect:** e.txt opens with its conflict markers in the text editor (VS Code offers Accept Current / Incoming / Both there, and Resolve in Merge Editor), not in the merge editor; nothing else changes.

```
+ editor | e.txt (file, line 1)
- editor | Preview ger-runbook-<id>.md (webview)
--- shown now:
status bar | $(error) Conflict in "Add e.txt"  $(bell-dot) 1
stack header | (view not drawn)
stack  |   ⚠ e.txt — conflicted
stack  | ⚠ Add e.txt — <sha> · held back · not on Gerrit
stack  | test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change
change | ▌ Reworking your chain: Add e.txt — change 5 of 5  [All chains]
change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌   4. test! temporary experiment — replayed
change | ▌ ▸ 5. Add e.txt — stopped here
change | ▌ [Continue] (not now: resolve e.txt first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | No Change-Id — not on Gerrit
change | [Resolve e.txt] — it is still conflicted: open e.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
change | Commit message — Add e.txt  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Conflicts — 1 file
change |   ⚠ e.txt — conflicted
change | Files, as before the rebase — 1 · +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | (the commit shown is only on the branch as it was before)
```

## Meanwhile: you resolve e.txt and git add it

**Expect:** Nothing is conflicted any more: e.txt leaves the list and the conflict icon goes; the rebase is still stopped at "Add e.txt", ready to Continue, which now appears as the prominent button.

```
+ status bar | $(warning) Conflict resolved in "Add e.txt"  $(bell-dot) 1
- status bar | $(error) Conflict in "Add e.txt"  $(bell-dot) 1
+ chains | Conflict resolved in "Add e.txt"
- chains | Conflict in "Add e.txt"
+ stack  | ▶ Add e.txt — <sha> · held back · not on Gerrit
- stack  |   ⚠ e.txt — conflicted
- stack  | ⚠ Add e.txt — <sha> · held back · not on Gerrit
+ change | ▌ Left: 2 comments to answer · 1 commit needs edits
- change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits
+ change | ▌ [**Continue**] commit this stop as it is and finish the rebase
- change | ▌ [Continue] (not now: resolve e.txt first)
- change | [Resolve e.txt] — it is still conflicted: open e.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
- change | Conflicts — 1 file
- change |   ⚠ e.txt — conflicted
+ editor | Preview ger-runbook-<id>.md (webview)
- editor | e.txt (file, line 1)
--- shown now:
status bar | $(warning) Conflict resolved in "Add e.txt"  $(bell-dot) 1
stack header | (view not drawn)
stack  | ▶ Add e.txt — <sha> · held back · not on Gerrit
stack  | test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is HEAD)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change
change | ▌ Reworking your chain: Add e.txt — change 5 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌   4. test! temporary experiment — replayed
change | ▌ ▸ 5. Add e.txt — stopped here
change | ▌ [**Continue**] commit this stop as it is and finish the rebase
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | No Change-Id — not on Gerrit
change | Commit message — Add e.txt  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files, as before the rebase — 1 · +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | (the commit shown is only on the branch as it was before)
```

## 94. Local stack › Continue (the conflict resolved)

**Expect:** ger rebase --continue runs in the ger terminal; once it has run, the task header and the marks go; Local stack shows the branch with "Refactor parser init" changed here since the last push and "Add e.txt" on top.

```
terminal "ger": runs `ger rebase --continue`
status bar message: $(terminal) ger rebase --continue runs in the ger terminal: the views follow once the rebase moves on
notification (info): The rebase is finished. 1 commit of your stack still needs edits.  [Work on Next] [Push to Gerrit…]
    → dismissed
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
- status bar | $(warning) Conflict resolved in "Add e.txt"  $(bell-dot) 1
+ chains | 2 unresolved comments on 101: Extract command routing
- chains | Conflict resolved in "Add e.txt"
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | ▶ Add e.txt — <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — HEAD · <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- change | ▌ Reworking your chain: Add e.txt — change 5 of 5  [All chains]
- change | ▌ Left: 2 comments to answer · 1 commit needs edits
- change | ▌   1. Cleanup after experiment — replayed
- change | ▌   2. Extract command routing — replayed · 2 to answer
- change | ▌   3. Refactor parser init — replayed
- change | ▌   4. test! temporary experiment — replayed
- change | ▌ ▸ 5. Add e.txt — stopped here
- change | ▌ [**Continue**] commit this stop as it is and finish the rebase
- change | ▌ [Abort] put the branch back as it was before the rebase
- change | ▌ [Skip] leave this commit out and go on
+ change | [Work on next] — Work on Next (Alt+Enter)
+ change | Commit message — Add e.txt  (editable)
- change | Commit message — Add e.txt  (read-only)
+ change | Files — 1 · +1 −0
+ change |   e.txt — modified +1 −0
- change | Files, as before the rebase — 1 · +1 −0
- change |   e.txt — added +1 −0
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change
change | No Change-Id — not on Gerrit
change | [Work on next] — Work on Next (Alt+Enter)
change | Commit message — Add e.txt  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   e.txt — modified +1 −0
change | Comments — none
change | (the commit shown is HEAD)
```

## Meanwhile: somebody adds c.txt on main, and you git fetch, so origin/main moves on

**Expect:** Local stack says origin/main has 1 new commit, offers Rebase onto origin/main, and warns that "test! temporary experiment" would conflict in c.txt; the commits themselves are unchanged. The status bar does not change: an upstream that moved on is not urgent.

```
+ stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change
change | No Change-Id — not on Gerrit
change | [Work on next] — Work on Next (Alt+Enter)
change | Commit message — Add e.txt  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   e.txt — modified +1 −0
change | Comments — none
change | (the commit shown is HEAD)
```

## 95. Local stack › Rebase onto origin/main ("test! temporary experiment" conflicts in c.txt, as the row said)

**Expect:** ger rebase origin/main runs in the ger terminal, and the status bar says so; once it has run, the upstream row goes and Local stack says the rebase stopped on a conflict in c.txt, with Abort and Skip; "test! temporary experiment" has a red conflict icon, not dimmed; the three below are replayed onto the new origin/main and show their new SHAs; "Add e.txt" is dimmed. The Change view of 103 says "Conflict in c.txt" and lists it under Conflicts, without the vote explanation. The status bar says Conflict in 103.

```
terminal "ger": runs `ger rebase origin/main`
status bar message: $(terminal) ger rebase origin/main runs in the ger terminal: it shows the todo, then replays the stack onto origin/main
+ status bar | $(error) Conflict in 103  $(bell-dot) 1
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
+ chains | Conflict in 103: test! temporary experiment
- chains | 2 unresolved comments on 101: Extract command routing
+ stack  |   ⚠ c.txt — conflicted
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | ⚠ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
+ change | title: Change 103
+ change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
+ change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits · 1 commit to replay
+ change | ▌   1. Cleanup after experiment — replayed
+ change | ▌   2. Extract command routing — replayed · 2 to answer
+ change | ▌   3. Refactor parser init — replayed
+ change | ▌ ▸ 4. test! temporary experiment — stopped here
+ change | ▌   5. Add e.txt — to come
+ change | ▌ [Continue] (not now: resolve c.txt first)
+ change | ▌ [Abort] put the branch back as it was before the rebase
+ change | ▌ [Skip] leave this commit out and go on
+ change | Awaiting review — last pushed V-1 CR+2
+ change | [Resolve c.txt] — it is still conflicted: open c.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
+ change | Commit message — test! temporary experiment  (read-only)
- stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
- change | title: Change
- change | No Change-Id — not on Gerrit
- change | [Work on next] — Work on Next (Alt+Enter)
- change | Commit message — Add e.txt  (editable)
+ change | Conflicts — 1 file
+ change |   ⚠ c.txt — conflicted
+ change | Files, as before the rebase — 1 · +2 −0
+ change |   c.txt — added +2 −0
- change | Files — 1 · +1 −0
- change |   e.txt — modified +1 −0
+ change | CI
+ change |   Widget #55 — from patchset 1, before your local changes · 1 finding
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
--- shown now:
status bar | $(error) Conflict in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  |   ⚠ c.txt — conflicted
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ⚠ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [Continue] (not now: resolve c.txt first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | [Resolve c.txt] — it is still conflicted: open c.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Conflicts — 1 file
change |   ⚠ c.txt — conflicted
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
```

## 96. Local stack › Skip › Cancel

**Expect:** A dialog asks before "test! temporary experiment" is left out: the commit and the conflict resolution so far would be thrown away, Abort brings back the branch as before the rebase but not the resolution, and its change stays open on Gerrit. Cancelled, nothing runs and nothing changes.

```
dialog (warning): Skip "test! temporary experiment"?  [Skip] [Cancel]
    detail: The rebase goes on without it: the commit and the conflict resolution done so far, staged or not, are thrown away, and once the rebase is finished the branch no longer has this commit. Abort instead brings back the branch as it was before the rebase, but not the resolution. Its change stays open on Gerrit until you abandon it there.
    → cancelled
(no change)
--- shown now:
status bar | $(error) Conflict in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  |   ⚠ c.txt — conflicted
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ⚠ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [Continue] (not now: resolve c.txt first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | [Resolve c.txt] — it is still conflicted: open c.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Conflicts — 1 file
change |   ⚠ c.txt — conflicted
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
```

## 97. Local stack › Push to Gerrit… (during the rebase) › Abort

**Expect:** No ger push: a dialog says to finish the rebase first, why (in ger push's own words), and offers only Abort: Continue is not a way on while c.txt is conflicted. Abort runs ger rebase --abort in the ger terminal; once it has run, everything is as before the rebase: the branch, its commits, the header; Local stack still says origin/main has 1 new commit.

```
dialog (warning): Finish the rebase first  [Abort] [Cancel]
    detail: A rebase is in progress, and 'feature' still points at the commits from before it; finish it (`ger rebase --continue`) or `ger rebase --abort`, then push.
    → pressed "Abort"
terminal "ger": runs `ger rebase --abort`
status bar message: $(terminal) ger rebase --abort runs in the ger terminal: the branch goes back to where it was before the rebase
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
- status bar | $(error) Conflict in 103  $(bell-dot) 1
+ chains | 2 unresolved comments on 101: Extract command routing
- chains | Conflict in 103: test! temporary experiment
+ stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
- stack  |   ⚠ c.txt — conflicted
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | ⚠ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
+ change | [Work on next] — Work on Next (Alt+Enter)
+ change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
+ change | Commit message — test! temporary experiment  (editable)
- change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
- change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits · 1 commit to replay
- change | ▌   1. Cleanup after experiment — replayed
- change | ▌   2. Extract command routing — replayed · 2 to answer
- change | ▌   3. Refactor parser init — replayed
- change | ▌ ▸ 4. test! temporary experiment — stopped here
- change | ▌   5. Add e.txt — to come
- change | ▌ [Continue] (not now: resolve c.txt first)
- change | ▌ [Abort] put the branch back as it was before the rebase
- change | ▌ [Skip] leave this commit out and go on
- change | Awaiting review — last pushed V-1 CR+2
- change | [Resolve c.txt] — it is still conflicted: open c.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
- change | Commit message — test! temporary experiment  (read-only)
+ change | Files — 1 · +2 −0
- change | Conflicts — 1 file
- change |   ⚠ c.txt — conflicted
- change | Files, as before the rebase — 1 · +2 −0
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | [Work on next] — Work on Next (Alt+Enter)
change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is below HEAD)
```

## Meanwhile: you stop at "Refactor parser init" with ger edit's rebase

**Expect:** Local stack marks the rebase stopped at "Refactor parser init", HEAD on it; the two above are dimmed, still to be replayed.

```
+ status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
+ chains | Rebase stopped at 102: Refactor parser init
- chains | 2 unresolved comments on 101: Extract command routing
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
+ stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ change | title: Change 102
+ change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
+ change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
+ change | ▌   1. Cleanup after experiment — replayed
+ change | ▌   2. Extract command routing — replayed · 2 to answer
+ change | ▌ ▸ 3. Refactor parser init — stopped here
+ change | ▌   4. test! temporary experiment — to come
+ change | ▌   5. Add e.txt — to come
+ change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
+ change | ▌ [Abort] put the branch back as it was before the rebase
- change | title: Change 103
+ change | Commit message — Refactor parser init  (read-only)
- change | [Work on next] — Work on Next (Alt+Enter)
- change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | Commit message — test! temporary experiment  (editable)
+ change | Files — 2 · +2 −0
+ change |   a.txt — added +1 −0
+ change |   e.txt — added +1 −0
- change | Files — 1 · +2 −0
- change |   c.txt — added +2 −0
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 102
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
change | ▌ [Abort] put the branch back as it was before the rebase
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | Commit message — Refactor parser init  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 2 · +2 −0
change |   a.txt — added +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is HEAD)
```

## 98. Local stack › click "Add e.txt" (still to be replayed)

**Expect:** The Change view shows "Add e.txt" as it was: it changes e.txt.

```
+ change | title: Change
- change | title: Change 102
+ change | No Change-Id — not on Gerrit
+ change | Commit message — Add e.txt  (read-only)
- change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
- change | Commit message — Refactor parser init  (read-only)
+ change | Files — 1 · +1 −0
+ change |   e.txt — modified +1 −0
- change | Files — 2 · +2 −0
- change |   a.txt — added +1 −0
- change |   e.txt — added +1 −0
- change | CI
- change |   Widget #55 — from patchset 1, before your local changes · 1 finding
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
change | ▌ [Abort] put the branch back as it was before the rebase
change | No Change-Id — not on Gerrit
change | Commit message — Add e.txt  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   e.txt — modified +1 −0
change | Comments — none
change | (the commit shown is only on the branch as it was before)
```

## 99. Change › click e.txt under Files

**Expect:** The diff of e.txt in "Add e.txt" opens, its title naming the commit; both sides are that commit's, as it is not the one you stand on. You keep it open (double-click its tab), so the next file opened does not take its place.

```
+ editor | e.txt (<sha>) (diff, ger-file ↔ ger-file)
- editor | Preview ger-runbook-<id>.md (webview)
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
change | ▌ [Abort] put the branch back as it was before the rebase
change | No Change-Id — not on Gerrit
change | Commit message — Add e.txt  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   e.txt — modified +1 −0
change | Comments — none
change | (the commit shown is only on the branch as it was before)
diff tab | e.txt (<sha>) (ger-file on the right, active)
```

## 100. Local stack › click "Refactor parser init" (where it stopped)

**Expect:** The Change view shows the commit you stand on; the diff tab of "Add e.txt" stays as it is.

```
+ change | title: Change 102
- change | title: Change
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
+ change | Commit message — Refactor parser init  (read-only)
- change | No Change-Id — not on Gerrit
- change | Commit message — Add e.txt  (read-only)
+ change | Files — 2 · +2 −0
+ change |   a.txt — added +1 −0
+ change |   e.txt — added +1 −0
- change | Files — 1 · +1 −0
- change |   e.txt — modified +1 −0
+ change | CI
+ change |   Widget #55 — from patchset 1, before your local changes · 1 finding
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 102
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
change | ▌ [Abort] put the branch back as it was before the rebase
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | Commit message — Refactor parser init  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 2 · +2 −0
change |   a.txt — added +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is HEAD)
diff tab | e.txt (<sha>) (ger-file on the right, active)
```

## 101. Change › click e.txt under Files

**Expect:** e.txt opens on its own (the commit adds it), as your file: you stand on this commit.

```
+ editor | e.txt (file, line 1)
- editor | e.txt (<sha>) (diff, ger-file ↔ ger-file)
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 102
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
change | ▌ [Abort] put the branch back as it was before the rebase
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | Commit message — Refactor parser init  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 2 · +2 −0
change |   a.txt — added +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is HEAD)
diff tab | e.txt (<sha>) (ger-file on the right)
```

## Meanwhile: you type a line at the top of e.txt, and do not save it

**Expect:** e.txt has unsaved edits; nothing else changes.

```
+ editor | e.txt (file, line 2)
- editor | e.txt (file, line 1)
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 102
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
change | ▌ [Abort] put the branch back as it was before the rebase
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | Commit message — Refactor parser init  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 2 · +2 −0
change |   a.txt — added +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is HEAD)
diff tab | e.txt (<sha>) (ger-file on the right)
```

## 102. Local stack › Continue (e.txt not saved) › Save All

**Expect:** Before anything runs, a dialog names e.txt, says ger rebase --continue sees only the files on disk and then says what saved edits still need, and offers Save All, Continue without saving (git would rewrite e.txt under the edits) and Cancel. Save All pressed, e.txt is saved; then ger's own check refuses before anything runs, and a warning says so in its words: e.txt is changed and not committed, amend it in or stash it. Nothing runs in the terminal; the rebase stays where it stopped.

```
dialog (warning): Save e.txt first? ger rebase --continue then says what it still needs.  [Save All] [Continue without saving] [Cancel]
    detail: e.txt has edits that are not saved, and ger rebase --continue sees only the files on disk. Continue without saving: the edits stay in the editor, git rewrites the files under them, and VS Code says so when you save.
    → pressed "Save All"
notification (warning): ger rebase --continue did not run: You have unstaged changes to e.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.
status bar message: $(warning) refused: you have unstaged changes to e.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`
ger terminal | error: you have unstaged changes to e.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`
ger terminal | (exit 2)
+ change | ▌ Left: e.txt not amended · 2 comments to answer · 1 commit needs edits · 2 commits to replay
+ change | ▌ ⚠ refused: you have unstaged changes to e.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`
- change | ▌ Left: 2 comments to answer · 1 commit needs edits · 2 commits to replay
+ change | ▌ [Continue] (not now: e.txt changed, not amended: amend or stash it first)
- change | ▌ [**Continue**] commit this stop as it is, then replay the 2 commits above
+ change | Changed, not amended — 1 file
+ change |   ⚠ e.txt — changed, not amended
--- shown now:
status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 102
change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
change | ▌ Left: e.txt not amended · 2 comments to answer · 1 commit needs edits · 2 commits to replay
change | ▌ ⚠ refused: you have unstaged changes to e.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌ ▸ 3. Refactor parser init — stopped here
change | ▌   4. test! temporary experiment — to come
change | ▌   5. Add e.txt — to come
change | ▌ [Continue] (not now: e.txt changed, not amended: amend or stash it first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | Commit message — Refactor parser init  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Changed, not amended — 1 file
change |   ⚠ e.txt — changed, not amended
change | Files — 2 · +2 −0
change |   a.txt — added +1 −0
change |   e.txt — added +1 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is HEAD)
diff tab | e.txt (<sha>) (ger-file on the right)
```

## Meanwhile: you amend "Refactor parser init" with e.txt, and git rebase --continue

**Expect:** The rebase finishes: "Refactor parser init" and the two above it have new SHAs. The diff tab of "Add e.txt" stays open, as it still shows that commit as it was, and its title says it is stale.

```
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
- status bar | $(warning) Rebase stopped at 102  $(bell-dot) 1
+ chains | 2 unresolved comments on 101: Extract command routing
- chains | Rebase stopped at 102: Refactor parser init
+ stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]  (dim)
- stack  | ▶ Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- change | ▌ Reworking your chain: Add e.txt — change 3 of 5  [All chains]
- change | ▌ Left: e.txt not amended · 2 comments to answer · 1 commit needs edits · 2 commits to replay
- change | ▌ ⚠ refused: you have unstaged changes to e.txt; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`
- change | ▌   1. Cleanup after experiment — replayed
- change | ▌   2. Extract command routing — replayed · 2 to answer
- change | ▌ ▸ 3. Refactor parser init — stopped here
- change | ▌   4. test! temporary experiment — to come
- change | ▌   5. Add e.txt — to come
- change | ▌ [Continue] (not now: e.txt changed, not amended: amend or stash it first)
- change | ▌ [Abort] put the branch back as it was before the rebase
+ change | [Work on next] — Work on Next (Alt+Enter)
+ change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
+ change | Commit message — Refactor parser init  (editable)
- change | Commit message — Refactor parser init  (read-only)
+ change | Files — 2 · +3 −0
- change | Changed, not amended — 1 file
- change |   ⚠ e.txt — changed, not amended
- change | Files — 2 · +2 −0
+ change |   e.txt — added +2 −0
- change |   e.txt — added +1 −0
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 102
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | [Work on next] — Work on Next (Alt+Enter)
change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — Refactor parser init  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 2 · +3 −0
change |   a.txt — added +1 −0
change |   e.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is below HEAD)
diff tab | e.txt (<sha>) — stale (ger-file on the right)
```

## 103. Settings › ger.closeDiffsOnRebase: on

**Expect:** The stale diff tab of "Add e.txt" closes.

```
(no change)
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 102
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | [Work on next] — Work on Next (Alt+Enter)
change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — Refactor parser init  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 2 · +3 −0
change |   a.txt — added +1 −0
change |   e.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is below HEAD)
```

## 104. Local stack › click "Add e.txt"

**Expect:** The Change view shows "Add e.txt", now HEAD.

```
+ change | title: Change
+ change | No Change-Id — not on Gerrit
- change | title: Change 102
- change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
+ change | Commit message — Add e.txt  (editable)
- change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | Commit message — Refactor parser init  (editable)
+ change | Files — 1 · +1 −0
+ change |   e.txt — modified +1 −0
- change | Files — 2 · +3 −0
- change |   a.txt — added +1 −0
- change |   e.txt — added +2 −0
- change | CI
- change |   Widget #55 — from patchset 1, before your local changes · 1 finding
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change
change | No Change-Id — not on Gerrit
change | [Work on next] — Work on Next (Alt+Enter)
change | Commit message — Add e.txt  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   e.txt — modified +1 −0
change | Comments — none
change | (the commit shown is HEAD)
```

## 105. Change › click e.txt under Files

**Expect:** The diff of e.txt in "Add e.txt" opens with your file on its right: you stand on this commit.

```
+ editor | e.txt (<sha>) (diff, ger-file ↔ file)
- editor | e.txt (file, line 2)
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change
change | No Change-Id — not on Gerrit
change | [Work on next] — Work on Next (Alt+Enter)
change | Commit message — Add e.txt  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   e.txt — modified +1 −0
change | Comments — none
change | (the commit shown is HEAD)
diff tab | e.txt (<sha>) (file on the right, active)
```

## Meanwhile: you type a line at the top of e.txt in that diff, and do not save it

**Expect:** The diff tab of "Add e.txt" has unsaved edits; nothing else changes.

```
(no change)
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change
change | No Change-Id — not on Gerrit
change | [Work on next] — Work on Next (Alt+Enter)
change | Commit message — Add e.txt  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   e.txt — modified +1 −0
change | Comments — none
change | (the commit shown is HEAD)
diff tab | e.txt (<sha>) (file on the right, unsaved, active)
```

## Meanwhile: you rebase onto origin/main, and "test! temporary experiment" conflicts in c.txt

**Expect:** Local stack says the rebase stopped on a conflict in c.txt. "Add e.txt" is still to be replayed, but its diff tab has your file on the right, which is now the worktree of the stopped rebase, not that commit: its title says it is stale. It is not closed, though ger.closeDiffsOnRebase is on: it holds unsaved edits, which are kept.

**Today:** the Change view still explains votes and shows the commit as it was (#59).

```
+ status bar | $(error) Conflict in 103  $(bell-dot) 1
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
+ chains | Conflict in 103: test! temporary experiment
- chains | 2 unresolved comments on 101: Extract command routing
+ stack  |   ⚠ c.txt — conflicted
+ stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
+ stack  | ⚠ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
+ change | title: Change 103
+ change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
+ change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits · 1 commit to replay
+ change | ▌   1. Cleanup after experiment — replayed
+ change | ▌   2. Extract command routing — replayed · 2 to answer
+ change | ▌   3. Refactor parser init — replayed
+ change | ▌ ▸ 4. test! temporary experiment — stopped here
+ change | ▌   5. Add e.txt — to come
+ change | ▌ [Continue] (not now: resolve c.txt first)
+ change | ▌ [Abort] put the branch back as it was before the rebase
+ change | ▌ [Skip] leave this commit out and go on
+ change | Awaiting review — last pushed V-1 CR+2
+ change | [Resolve c.txt] — it is still conflicted: open c.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
+ change | Commit message — test! temporary experiment  (read-only)
- stack  | origin/main has 1 new commit — "test! temporary experiment" would conflict in c.txt  [Rebase onto origin/main]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Extract command routing — <sha> · the same as on Gerrit  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · the same as on Gerrit  [review +2, ci good]
- change | title: Change
- change | No Change-Id — not on Gerrit
- change | [Work on next] — Work on Next (Alt+Enter)
- change | Commit message — Add e.txt  (editable)
+ change | Conflicts — 1 file
+ change |   ⚠ c.txt — conflicted
+ change | Files, as before the rebase — 1 · +2 −0
+ change |   c.txt — added +2 −0
- change | Files — 1 · +1 −0
- change |   e.txt — modified +1 −0
+ change | CI
+ change |   Widget #55 — from patchset 1, before your local changes · 1 finding
+ change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
+ editor | e.txt (<sha>) — stale (diff, ger-file ↔ file)
- editor | e.txt (<sha>) (diff, ger-file ↔ file)
--- shown now:
status bar | $(error) Conflict in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  |   ⚠ c.txt — conflicted
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ⚠ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [Continue] (not now: resolve c.txt first)
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | [Resolve c.txt] — it is still conflicted: open c.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Conflicts — 1 file
change |   ⚠ c.txt — conflicted
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
diff tab | e.txt (<sha>) — stale (file on the right, unsaved, active)
```

## Meanwhile: you resolve c.txt and git add it

**Expect:** Nothing is conflicted any more; the rebase is ready to Continue.

```
+ status bar | $(warning) Conflict resolved in 103  $(bell-dot) 1
- status bar | $(error) Conflict in 103  $(bell-dot) 1
+ chains | Conflict resolved in 103: test! temporary experiment
- chains | Conflict in 103: test! temporary experiment
- stack  |   ⚠ c.txt — conflicted
+ stack  | ▶ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | ⚠ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ change | ▌ Left: 2 comments to answer · 1 commit needs edits · 1 commit to replay
- change | ▌ Left: 1 conflict to resolve · 2 comments to answer · 1 commit needs edits · 1 commit to replay
+ change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
- change | ▌ [Continue] (not now: resolve c.txt first)
- change | [Resolve c.txt] — it is still conflicted: open c.txt with its conflict markers, resolve them, then stage it (git add) so the rebase can go on (Alt+Enter)
- change | Conflicts — 1 file
- change |   ⚠ c.txt — conflicted
--- shown now:
status bar | $(warning) Conflict resolved in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
diff tab | e.txt (<sha>) — stale (file on the right, unsaved, active)
```

## Meanwhile: you create notes.txt, a file git does not track, and an extension edits it without saving

**Expect:** Nothing in the views changes: notes.txt is no part of the stack. VS Code opens a tab of it behind the others, as it does for every file with unsaved edits.

```
(no change)
--- shown now:
status bar | $(warning) Conflict resolved in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
diff tab | e.txt (<sha>) — stale (file on the right, unsaved, active)
```

## 106. Local stack › Continue (e.txt and notes.txt not saved) › Cancel

**Expect:** A dialog names e.txt, whose edits are only in the stale diff tab, and notes.txt, though git does not track it (asking once too often loses nothing), and offers Save All, Continue without saving and Cancel. Cancelled, nothing is saved and nothing runs: the rebase stays stopped, the tab stale and unsaved.

```
dialog (warning): Save 2 files first? ger rebase --continue then says what they still need.  [Save All] [Continue without saving] [Cancel]
    detail: e.txt and notes.txt have edits that are not saved, and ger rebase --continue sees only the files on disk. Continue without saving: the edits stay in the editor, git rewrites the files under them, and VS Code says so when you save.
    → cancelled
(no change)
--- shown now:
status bar | $(warning) Conflict resolved in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
diff tab | e.txt (<sha>) — stale (file on the right, unsaved, active)
```

## Meanwhile: you save notes.txt, close its tab and delete it

**Expect:** Nothing changes.

```
(no change)
--- shown now:
status bar | $(warning) Conflict resolved in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
diff tab | e.txt (<sha>) — stale (file on the right, unsaved, active)
```

## Meanwhile: you revert e.txt in the stale diff tab (File › Revert File)

**Expect:** The stale diff tab of "Add e.txt" has no unsaved edits any more, so with ger.closeDiffsOnRebase on it closes once the views refresh; the editor of e.txt opened earlier is in front again.

```
+ editor | e.txt (file, line 2)
- editor | e.txt (<sha>) — stale (diff, ger-file ↔ file)
--- shown now:
status bar | $(warning) Conflict resolved in 103  $(bell-dot) 1
stack header | (view not drawn)
stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
stack  | ▶ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is HEAD)
stack  | (test! temporary experiment is only on the branch as it was before)
stack  | (Add e.txt is only on the branch as it was before)
change | title: Change 103
change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
change | ▌ Left: 2 comments to answer · 1 commit needs edits · 1 commit to replay
change | ▌   1. Cleanup after experiment — replayed
change | ▌   2. Extract command routing — replayed · 2 to answer
change | ▌   3. Refactor parser init — replayed
change | ▌ ▸ 4. test! temporary experiment — stopped here
change | ▌   5. Add e.txt — to come
change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
change | ▌ [Abort] put the branch back as it was before the rebase
change | ▌ [Skip] leave this commit out and go on
change | Awaiting review — last pushed V-1 CR+2
change | Commit message — test! temporary experiment  (read-only)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files, as before the rebase — 1 · +2 −0
change |   c.txt — added +2 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is only on the branch as it was before)
```

## 107. Local stack › Continue (ger.closeDiffsOnRebase on)

**Expect:** Nothing is unsaved, so nothing is asked: ger rebase --continue runs in the ger terminal; once it has run, the rebase is finished, "Add e.txt" is replayed onto origin/main with a new SHA. The Change view follows "Add e.txt" to its new commit.

**Today:** the Change view loses "Add e.txt" and shows nothing: it follows a rewritten commit by its Change-Id, and this one has none (no issue yet).

```
terminal "ger": runs `ger rebase --continue`
status bar message: $(terminal) ger rebase --continue runs in the ger terminal: the views follow once the rebase moves on
notification (info): The rebase is finished. 1 commit of your stack still needs edits.  [Work on Next] [Push to Gerrit…]
    → dismissed
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
- status bar | $(warning) Conflict resolved in 103  $(bell-dot) 1
+ chains | 2 unresolved comments on 101: Extract command routing
- chains | Conflict resolved in 103: test! temporary experiment
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Add e.txt — <sha> · held back · not on Gerrit  (dim)
- stack  | ▶ test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — HEAD · <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
+ change | [Work on next] — Work on Next (Alt+Enter)
+ change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
+ change | Commit message — test! temporary experiment  (editable)
- change | ▌ Reworking your chain: Add e.txt — change 4 of 5  [All chains]
- change | ▌ Left: 2 comments to answer · 1 commit needs edits · 1 commit to replay
- change | ▌   1. Cleanup after experiment — replayed
- change | ▌   2. Extract command routing — replayed · 2 to answer
- change | ▌   3. Refactor parser init — replayed
- change | ▌ ▸ 4. test! temporary experiment — stopped here
- change | ▌   5. Add e.txt — to come
- change | ▌ [**Continue**] commit this stop as it is, then replay the 1 commit above
- change | ▌ [Abort] put the branch back as it was before the rebase
- change | ▌ [Skip] leave this commit out and go on
- change | Awaiting review — last pushed V-1 CR+2
- change | Commit message — test! temporary experiment  (read-only)
+ change | Files — 1 · +1 −0
+ change |   c.txt — modified +1 −0
- change | Files, as before the rebase — 1 · +2 −0
- change |   c.txt — added +2 −0
--- shown now:
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
stack header | feature → origin/main
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
stack  | (Cleanup after experiment is below HEAD)
stack  | (Extract command routing is below HEAD)
stack  | (Refactor parser init is below HEAD)
stack  | (test! temporary experiment is below HEAD)
stack  | (Add e.txt is HEAD)
change | title: Change 103
change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
change | [Work on next] — Work on Next (Alt+Enter)
change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
change | Commit message — test! temporary experiment  (editable)
change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
change | Files — 1 · +1 −0
change |   c.txt — modified +1 −0
change | Comments — none
change | CI
change |   Widget #55 — from patchset 1, before your local changes · 1 finding
change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
change | (the commit shown is below HEAD)
```

## 108. Click "Add retry to the uploader" under To review

**Expect:** The Change view shows change 900, Alice's; its title bar offers Vote and Publish.

```
(nothing said or done outside the views)
--- on screen:
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing  $(bell-dot) 1
+ chains |   Add retry to the uploader — alice · 1 change · V+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
- chains |   Add retry to the uploader — ● new activity · alice · 1 change · V+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
+ change | title: Change 900
+ change | Waiting for your review, unresolved comments — V+1 💬2 open
+ change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
+ change | Commit message — Add retry to the uploader  (read-only)
+ change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
- change | title: Change 103
- change | Local changes not pushed, awaiting review — last pushed V-1 CR+2
- change | [Work on next] — Work on Next (Alt+Enter)
- change | This commit is ahead of Gerrit — its code changed since the last push: the votes are the last pushed patchset's, and Gerrit may not keep them when you push
- change | Commit message — test! temporary experiment  (editable)
- change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
+ change |   uploader.txt — added +1 −0
+ change | Comments — 2 to read
+ change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
+ change |   And cap the delay. — uploader.txt:1 · Bob Architect  [open]
- change |   c.txt — modified +1 −0
- change | Comments — none
- change | CI
- change |   Widget #55 — from patchset 1, before your local changes · 1 finding
- change |     tests/test_calc.py::test_divide: ZeroDivisionError: division by zero — pytest · demo/calc.py:6 (line 6 on patchset 1, changed since)
```

## 109. "+" on line 1 of uploader.txt in the diff › Save Draft

**Expect:** The draft is saved on Gerrit and shows as yours in the Change view. Only you see it until it is published, and as it is not your change, ger push would not publish it: the notification offers Vote and Publish.

```
notification (info): Draft saved on Gerrit, visible only to you: Vote and Publish sends it.  [Vote and Publish…]
    → dismissed
--- on screen:
+ change | Comments — 2 to read · 1 of yours, not published
- change | Comments — 2 to read
+ change |   Cap the retries at 5? — your draft · uploader.txt:1  [yours]
```

## 110. Change › Vote and Publish… › Escape

**Expect:** A picker offers the votes Gerrit lets you give on 900, each with what the project says it means, opening on Code-Review +1, and your draft without a vote. Escape sends nothing.

```
progress (notification): Reading your drafts
picker: Vote on 900 "Add retry to the uploader" — "1 draft of yours goes out with it"
    $(thumbsup) Code-Review +2 — Looks good to me, approved
    $(thumbsup) Code-Review +1 — Looks good to me, but someone else must approve  (opens here)
    $(circle-large-outline) Code-Review 0 — No score
    $(thumbsdown) Code-Review -1 — I would prefer this is not submitted as is
    $(thumbsdown) Code-Review -2 — This shall not be submitted
    $(comment) Publish drafts only, no vote — 1 draft
    → escaped
--- on screen:
(no change)
```

## 111. Change › Vote and Publish… › Code-Review +1 › type "Thanks!" › Publish

**Expect:** After the vote, an optional message; then one dialog lists what goes out (Code-Review +1, the draft at uploader.txt:1, the message, on change 900) and says it cannot be taken back. Publish sends it; a notification says what was published, the draft shows as published in the Change view and the vote on 900's row.

```
progress (notification): Reading your drafts
picker: Vote on 900 "Add retry to the uploader" — "1 draft of yours goes out with it"
    $(thumbsup) Code-Review +2 — Looks good to me, approved
    $(thumbsup) Code-Review +1 — Looks good to me, but someone else must approve  (opens here)
    $(circle-large-outline) Code-Review 0 — No score
    $(thumbsdown) Code-Review -1 — I would prefer this is not submitted as is
    $(thumbsdown) Code-Review -2 — This shall not be submitted
    $(comment) Publish drafts only, no vote — 1 draft
    → picked "$(thumbsup) Code-Review +1"
input box: Message on change 900 (optional)
    prompt: Posted with it as the change message. Leave it empty for none; Escape cancels.
    placeholder: Looks good to me
    → typed "Thanks!" and pressed Enter
dialog (info): Publish on change 900?  [Publish] [Cancel]
    detail: Code-Review +1 · 1 draft · message "Thanks!"

900 Add retry to the uploader
  uploader.txt:1  "Cap the retries at 5?"

Everyone on the change sees it at once; it cannot be taken back.
    → pressed "Publish"
progress (notification): Publishing on 900
notification (info): Published Code-Review +1 and 1 draft on change 900.
--- on screen:
+ chains |   Add retry to the uploader — alice · 1 change · V+1 CR+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
- chains |   Add retry to the uploader — alice · 1 change · V+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
+ change | Waiting for your review, unresolved comments — V+1 CR+1 💬2 open
- change | Waiting for your review, unresolved comments — V+1 💬2 open
+ change | Comments — 3 to read
- change | Comments — 2 to read · 1 of yours, not published
+ change |   Cap the retries at 5? — uploader.txt:1  [open]
- change |   Cap the retries at 5? — your draft · uploader.txt:1  [yours]
```

## 112. Inline Vote and Publish… on the To review row of 900 › Code-Review +2 › no message › Cancel

**Expect:** The row's button votes on its chain (here only 900): the same picker and message, and the dialog. Cancelled, nothing is sent.

```
progress (notification): Reading your drafts
picker: Vote on 900 "Add retry to the uploader" — "You have no drafts here: only the vote and a message go out"
    $(thumbsup) Code-Review +2
    $(thumbsup) Code-Review +1  (opens here)
    $(circle-large-outline) Code-Review 0
    $(thumbsdown) Code-Review -1
    $(thumbsdown) Code-Review -2
    → picked "$(thumbsup) Code-Review +2"
input box: Message on each change (optional)
    prompt: Posted with it as the change message. Leave it empty for none; Escape cancels.
    placeholder: Looks good to me
    → typed "" and pressed Enter
dialog (info): Publish on change 900?  [Publish] [Cancel]
    detail: Code-Review +2 · no drafts

Everyone on the change sees it at once; it cannot be taken back.
    → cancelled
--- on screen:
(no change)
```

## 113. Command palette › ger: Publish Drafts…, with no drafts left on 900

**Expect:** It says there are no drafts of yours on 900 to publish, and asks nothing.

```
progress (notification): Reading your drafts
notification (info): You have no drafts on 900 to publish.
--- on screen:
(no change)
```

## Meanwhile: in a terminal, git config ger.reviewCheckout branch

```
```

## 114. Inline Review Change on change 900, ger.reviewCheckout being branch

**Expect:** This checkout switches to change-900 with 900's changes staged, and the notification says so. The task header at the top of the Change view says Reviewing alice's chain, change 1 of 1, what is left, 900 marked reviewing, and Leave review; Review chains and Local stack step aside (Local stack, were it shown, lists 900's chain: its row is marked under review, not HEAD, with no Rebase onto origin/main). The Change view shows 900, and all its files open in one editor (here one, added by the change).

```
progress (notification): Checking out 900: Add retry to the uploader
notification (info): Change 900 (patchset 1) is checked out for review: this checkout was switched to change-900, with its changes staged. Leave Review Checkout switches back.
--- on screen:
+ status bar | $(eye) alice has waited <age> for your review of 900: Add retry to t…
- status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
+ chains | alice has waited <age> for your review of 900: Add retry to the uploader
- chains | 2 unresolved comments on 101: Extract command routing
+ chains |   Add retry to the uploader — alice · 1 change · V+1 CR+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.local.toreview]
- chains |   Add retry to the uploader — alice · 1 change · V+1 CR+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
+ chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url]
+ chains |     test! temporary experiment — 103 · V-1 CR+2  [ger.change.url]
+ chains |     Refactor parser init — 102 · V-1 CR+2  [ger.change.url]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open  [ger.change.url]
+ chains |     Cleanup after experiment — 100 · V+1 CR+2  [ger.change.url]
+ stack  | Add retry to the uploader — under review · <sha> · the same as on Gerrit  [comments 2, review +1, ci good]
- chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
- chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
- chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
- stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
- stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
- stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
- stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
+ change | ▌ Reviewing alice's chain: Add retry to the uploader — change 1 of 1  [All chains]
+ change | ▌ Left: you voted on 1 of 1 · 3 comments to read
+ change | ▌ ▸ 1. Add retry to the uploader — reviewing · you voted +1 · 3 to answer
+ change | ▌ [Leave review] back to feature; the review's branches go
+ change | [Next comment] — 3 open comments not looked at yet (Alt+Enter)
- change | [Review change] — check it out for review, staged, with all its files (Alt+Enter)
+ change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
- change | Comparing — Base commit → Patchset 1 (current) · default: Against its base commit
+ editor | Change 900 (<sha>) (1 file) (webview)
- editor | e.txt (file, line 2)
```

## 115. Change › Files › uploader.txt

**Expect:** Your file opens, not the commit's copy: the working files hold the change, so comments are drawn where they are written and language features work.

```
(nothing said or done outside the views)
--- on screen:
+ editor | uploader.txt (file, line 1)
- editor | Change 900 (<sha>) (1 file) (webview)
```

## 116. "+" on line 1 of uploader.txt, your file › Save Draft

**Expect:** The draft is saved on Gerrit as on the commit's lines, and listed as yours in the Change view.

```
status bar message: $(check) Draft saved on Gerrit, visible only to you: Vote and Publish sends it.
--- on screen:
+ change | Comments — 3 to read · 1 of yours, not published
- change | Comments — 3 to read
+ change |   Is 3 enough on a slow link? — your draft · uploader.txt:1  [yours]
```

## 117. Next Unresolved Comment (Alt+F9)

**Expect:** 900 is not yours: the walk takes your draft too, so it counts four (the three open comments and yours), in the Change view's order.

```
status bar message: Comment 1 of 4
--- on screen:
+ change | [Next comment] — 2 open comments not looked at yet (Alt+Enter)
- change | [Next comment] — 3 open comments not looked at yet (Alt+Enter)
```

## 118. Change › Review Next Change in Chain

**Expect:** 900 is the only change of its chain: it says so and offers Vote on the Chain… and Leave Review. Nothing moves.

```
progress (notification): Checking out the next change of 900's chain
notification (info): Change 900 has no review chain to walk.  [Vote on the Chain…] [Leave Review]
    → dismissed
--- on screen:
(no change)
```

## 119. Task header › All chains

**Expect:** The header now offers Only this task; Review chains and Local stack are back for the rest of this review.

```
(nothing said or done outside the views)
--- on screen:
+ change | ▌ Reviewing alice's chain: Add retry to the uploader — change 1 of 1  [Only this task]
- change | ▌ Reviewing alice's chain: Add retry to the uploader — change 1 of 1  [All chains]
```

## 120. Change › … › Leave Review Checkout

**Expect:** Back on the branch it came from; the views are your stack again, and the task header is gone.

```
notification (info): Left the review of change 900.
--- on screen:
+ status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
- status bar | $(eye) alice has waited <age> for your review of 900: Add retry to t…
+ chains | 2 unresolved comments on 101: Extract command routing
- chains | alice has waited <age> for your review of 900: Add retry to the uploader
+ chains |   Add retry to the uploader — alice · 1 change · V+1 CR+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
- chains |   Add retry to the uploader — alice · 1 change · V+1 CR+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.local.toreview]
+ chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
+ chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
+ chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
+ chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
+ stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
+ stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
+ stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
+ stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
+ change | title: (nothing shown)
+ change | "Add retry to the uploader" is not in the local stack any more (a checkout, a drop or a squash). Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.
- chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url]
- chains |     test! temporary experiment — 103 · V-1 CR+2  [ger.change.url]
- chains |     Refactor parser init — 102 · V-1 CR+2  [ger.change.url]
- chains |     Extract command routing — 101 · V+1 CR+2 💬2 open  [ger.change.url]
- chains |     Cleanup after experiment — 100 · V+1 CR+2  [ger.change.url]
- stack  | Add retry to the uploader — under review · <sha> · the same as on Gerrit  [comments 2, review +1, ci good]
- change | title: Change 900
- change | ▌ Reviewing alice's chain: Add retry to the uploader — change 1 of 1  [Only this task]
- change | ▌ Left: you voted on 1 of 1 · 3 comments to read
- change | ▌ ▸ 1. Add retry to the uploader — reviewing · you voted +1 · 3 to answer
- change | ▌ [Leave review] back to feature; the review's branches go
- change | Waiting for your review, unresolved comments — V+1 CR+1 💬2 open
- change | [Next comment] — 2 open comments not looked at yet (Alt+Enter)
- change | Commit message — Add retry to the uploader  (read-only)
- change | Comparing — Base commit → Local commit <sha> · default: Against its base commit
- change | Files — 1 · +1 −0
- change |   uploader.txt — added +1 −0
- change | Comments — 3 to read · 1 of yours, not published
- change |   Should it back off between retries? — uploader.txt:1 · Bob Architect  [open]
- change |   And cap the delay. — uploader.txt:1 · Bob Architect  [open]
- change |   Cap the retries at 5? — uploader.txt:1  [open]
- change |   Is 3 enough on a slow link? — your draft · uploader.txt:1  [yours]
```

## At the end

```
status bar | $(comment-unresolved) 2 unresolved comments on 101: Extract command routing
state  | ready
events | live
chains | 2 unresolved comments on 101: Extract command routing
chains | To review — 1 chain · 1 with comments
chains |   Add retry to the uploader — alice · 1 change · V+1 CR+1 💬2 open · waiting <age> · active <age> ago  [ger.change.url.toreview]
chains | Outgoing — 1 chain · 1 with failed CI · 1 with comments
chains |   test! temporary experiment — 4 changes · V-1 CR+2 💬2 open · active <age> ago  [ger.chain.url.local]
chains |     test! temporary experiment — 103 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     Refactor parser init — 102 · V-1 CR+2 · in your stack  [ger.change.url.local]
chains |     Extract command routing — 101 · V+1 CR+2 💬2 open · in your stack  [ger.change.url.local]
chains |     Cleanup after experiment — 100 · V+1 CR+2 · in your stack  [ger.change.url.local]
stack  | Add e.txt — HEAD · <sha> · held back · not on Gerrit
stack  | test! temporary experiment — <sha> · code changed · held back · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Refactor parser init — <sha> · code changed · its code changed since the last push  [last pushed review +2, last pushed ci bad]
stack  | Extract command routing — <sha> · only rebased since the last push  [comments 2, review +2, ci good]
stack  | Cleanup after experiment — <sha> · only rebased since the last push  [review +2, ci good]
change | title: (nothing shown)
change | "Add retry to the uploader" is not in the local stack any more (a checkout, a drop or a squash). Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.
editor | uploader.txt (file, line 1)
```
