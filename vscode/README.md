# ger for VS Code

Your Gerrit review chains, your local stack, comments, diffs and CI findings — next to the code.

The extension has no Gerrit logic of its own. It starts `ger serve` in the git checkout of your workspace folder and
shows what that says, so it always agrees with `ger log`, `ger show` and `ger inbox`. The API
contract is [docu/spec/commands/serve.md](../docu/spec/commands/serve.md).

## What you get

In the editor, places are called *Gerrit* (the activity bar, the status bar item); commands are listed in the palette under `ger:`, because they run the `ger` tool.

**Status bar** — the *headline*: the one change that most wants you (failed CI on your chain,
then unresolved comments, then a negative vote, else the review that has waited longest).
Failed CI counts only on the chain of your local stack, and the item then just says
*CI failed:* and the change number; the tooltip has the change's subject. A bell
with a count appears when chains had activity since you last looked. Click to open the change.
While a rebase is underway (a `ger edit`, a conflict, also one you started in a terminal), the item
shows that instead, as ger serve's headline does (and so does the first row of *Review chains*): *Rebase stopped at 101*,
*Rebase stopped to split 101*, *Conflict in 103*, *Conflict resolved in 103*, *Rebase paused*, *Rebase stopped* when the
commit it stopped at was reset away, or *Rebase underway* when it cannot tell why it stopped — the change number when the
commit is on Gerrit, else its subject in quotes. A conflict is shown in the error colour, another stop as a warning. The
index is watched while a rebase is stopped, so a `git add` that resolves the last conflict shows at once. The tooltip says
what to do next (amend, resolve and `git add`, `git rebase --continue` or `--abort`); a click
opens *Local stack*. When the rebase ends, the item goes back to the headline.
When a refresh fails, whether it ran in the background or you pressed *Refresh* (Gerrit unreachable, `ger serve` stopped
and cannot be restarted), the views keep the last answer and the item carries a warning mark; its tooltip says why
it is out of date, and *Review chains* is labelled so. The next refresh that works clears the mark. While `ger` cannot run at all (not found, not set up, folder not trusted) the
views clear and say why, and the timer stops trying: it would only start `ger serve` to fail again, once per poll.
The log says so once; regaining focus, *Retry* and a changed `ger.executable` still try.

**Review chains** (activity bar → Gerrit)

- *Outgoing*: your open chains. *To review*: chains waiting on you.
- One row per chain, showing its top change, worst votes, unresolved comments and age. Expand
  it for the chain's changes. A `●` marks new activity.
- The icon in front is the most urgent thing about the chain or change: an error for failed CI or
  a broken change (abandoned, merged drift, invalid or missing Change-Id), a comment bubble for
  unresolved comments (the ones the Change view lists as open), a warning for a negative vote, a
  newer patchset on Gerrit or a merge whose equivalence is unknown, a clock while it waits, a
  check when nothing does. A *To review* chain with nothing wrong shows an eye. The status bar
  uses the same icons for the headline.
- Changes that are commits of your local stack are marked *in your stack* and open as local commits.
- Right-click a change (or a chain, for its top change) › **Copy Change-Id** or **Copy SHA**: the
  local commit for a change in your stack, else the commit of its current patch set.
- **Sort Chains…** (title bar): *By chain state* — the order `ger inbox` gives, what needs you
  first — or *By last activity*, newest or oldest first. It applies to both sections and is
  remembered per workspace; the sections name an order other than by state.

**Local stack**

The commits above your upstream, newest first. One row per commit:

- An **icon** in front says how the commit relates to Gerrit: a **full circle** when it is the
  same as on Gerrit; a **yellow arrow up** in a circle when it is only rebased since the last push
  (the same code by patch-id, whitespace and message aside: Gerrit usually keeps the votes); a **yellow diff** square when its code changed since (push it, and
  reviewers have to look again: Gerrit may not keep the votes), noted *code changed* beside the
  short id; a plain **yellow arrow up** when it changed and ger cannot tell which (either side
  is a merge, a root or an empty commit); a **red arrow down** when Gerrit has a newer patchset than this commit
  (outdated); **blue arrows both ways** when both happened (diverged: you changed it, and
  Gerrit's current patchset came from somewhere else). A dimmed **empty circle** is a commit
  Gerrit does not have yet; a green check, one that is merged. Hover it for the words.
- On the right, as icons: **unresolved comments** (with their number), the **review vote** and the
  **CI vote**. Dimmed, they are the last pushed patchset's: the commit's code changed since, and
  Gerrit may not keep them. Hover any of them for the words; the review vote's tooltip names the
  reviewers (and who is CC'd). A pushed change with nobody to review it shows a **person +** icon
  where the review vote goes.
- `HEAD`, the short commit id, *code changed* (see above), and *held back* where `ger push` would
  stop at the ready boundary.
- Click a row, or walk the list with **↑ / ↓**: the *Change* view follows, with the commit
  message, while the keyboard stays in the list. It shows what the row knows at once (what the
  change needs, its votes) and fills in the message, files and comments as they arrive. `Enter` (or a double click) moves to the Change view.
- **Select several** with `Ctrl`/`Cmd`+click, `Shift`+click or `Shift`+↑/↓ (`Ctrl+A`: all).
- **Reorder by drag and drop.** While you drag, a **line between two rows** shows where the
  commits will land; selected commits move together and keep their order. All or nothing: if a
  commit does not apply in its new place, nothing is touched and you are told which commit and
  which files. One place up or down, the **conflict preview** already knows and says so at once
  (hover a row to see which neighbour it cannot swap with).
- **Uncommitted changes** do not stop a rewrite: you are asked whether to stash them; they are
  put back right after, also when the rewrite is refused. If they no longer fit (the file they
  edit went with a dropped commit), they stay in `git stash` and you are told.
- Context menu: **Edit Commit Here**, **Open in Gerrit**, **Hand to Agent…**, **Squash into the
  commit below** (keeping either message — never both, so there is always exactly one
  Change-Id), **Drop**, **Copy Change-Id** and **Copy SHA** (of every selected row, one
  per line). Every rewrite offers **Undo** right after.
- **During a rebase** (a `ger edit`, a conflict) the view shows the stack as the rebase has it
  so far, as `ger log` does: the commits it replayed, and the one it stopped at once applied,
  as they are now (an amend shows at once, with its new id and HEAD); a **marker** on the
  commit it stopped at; the commits still to be replayed **dimmed**, as they were. The header
  names the branch being rebased (`feature (rebasing) → origin/main`). The banner above the
  stack says why it stopped and has the ways on `ger serve` allows from there (`rebase.actions`):
  **Continue** once nothing is conflicted, **Abort**, and **Skip** where it stopped on a conflict
  (`ger rebase --continue`, `--abort`, `--skip`, in the *ger* terminal, also in the palette while a
  rebase is underway, Skip only where it is offered). Skip asks first, and says what it throws
  away: the commit and the conflict resolution done so far, staged or not; Abort brings back the
  branch as it was before the rebase, not the resolution. At an edit stop, or once you split a
  commit there, there is nothing to skip: the commit is already in, and `git rebase --skip` would
  only throw away what is not committed yet, so `ger rebase --skip` refuses there too. Run from
  the palette where it is not offered, a way on says why not in `ger rebase`'s own words
  (`rebase.refused`).
- **Stopped on a conflict**, the commit it stopped at has a red conflict icon and is not dimmed:
  it is what wants you. The files still conflicted are listed under the banner; click one to
  resolve it in VS Code's merge editor (the file itself, with its conflict markers, where the
  built-in Git extension cannot open one). Once they are resolved and added, the banner says it is
  ready to continue: Continue appears, and is the one to press. The index is watched while a rebase
  is stopped, so a `git add` in the terminal shows within a second; Continue from the palette looks
  again before it says no. Nothing can be moved until the rebase is finished or aborted.
- **Continue saves first, asking.** `ger rebase --continue` sees only the files as they are on
  disk, so before it runs, any file of the repository with edits only in an editor (the diff of
  the commit you stand on has your file on its right side) is named in a dialog. **Save All**
  saves them and runs `ger rebase --continue`; saved edits are not part of the rebase yet, so it
  then refuses and says what they need (`git add` them at a conflict, amend them in at an edit
  stop, or `git stash` them). **Continue without saving** runs it with the edits left in the
  editor: git rewrites the files under them, and VS Code says so when you save. Cancel runs
  nothing. Nothing is saved unasked.
- **Diff tabs of commits that left the stack.** A diff opened from the Change view of a local
  commit in your stack goes on showing that commit once it is no longer in the stack: rewritten
  (amended, replayed by the rebase, reordered), dropped, or merged past the upstream. Its title
  then says it is *stale*: `b.txt (6827cbfe) — stale`. Tabs of commits still in the stack (still
  to be replayed, or left as they were) stay as they are. A diff with your file on its right (it
  opened on the commit you stood on) is true only while that commit is in the stack and HEAD too:
  after a commit on top, or a rebase that stops below it, your file is not what the title names,
  and the tab is stale too, as it is once the commit is merged past the upstream with HEAD still on
  it. With `ger.closeDiffsOnRebase` on, stale tabs are closed instead, unless they may hold
  unsaved edits: one that has them, or has your file on its right while VS Code has not read that
  file in yet, is marked; and the tabs VS Code restores at startup are only marked on the first
  look, and closed on the next. Only the tabs of the branch checked out are judged: another branch
  checked out (`ger checkout`, a review checkout, `git switch`) leaves them as they are, and a
  stale tab whose commit is back (the branch switched back, the rebase aborted) loses its mark.
  Gerrit's patchsets, compared with each other, never go stale.
- **The upstream moved on** (after a `git fetch`, `origin/main` has commits your branch has not):
  a row above the stack says so, *origin/main has 1 new commit*, with **Rebase onto
  origin/main**, which runs core's one command for it in the *ger* terminal (it shows the todo
  first): `ger rebase origin/main`, or `ger rebase origin/main --drop-merged-equivalent` once a
  commit is merged on Gerrit with the same code. Before you press it, the row says what the
  rebase would meet, previewed by `ger serve` without touching your worktree (`GET /stack`'s
  `upstream`), on the commits the rebase replays (not those already upstream, which it drops, as
  the tooltip says): *no conflicts*, or the first commit it would stop on, with a warning icon:
  one that would not apply and where (*"Refactor parser init" would conflict in a.txt*), or one
  that would become empty because its change is already upstream (you drop it with Skip). With
  a merge in the stack, or a git older than 2.40, it cannot tell, and the tooltip says why in
  `ger serve`'s words. With no local commits, the row below says the branch is behind it. During a rebase there is no such row: the banner leads, and the row is back
  once the rebase is finished or aborted. The status bar does not show it: an upstream that
  moved on is not urgent, and the status bar is the change that most wants you. `ger log` ends
  with the same advice as a `next:` line.
- **A merge commit** in the stack is one row, with a merge icon and marked *merge*, however
  many commits it brings in: those are not part of the stack (as on Gerrit) and are never read.
  `git rebase` cannot replay a merge, so the newest merge and the commits below it stay where
  they are — the view says so when you try to move, squash or drop one; `ger rebase` does it by
  hand. The commits above it reorder, squash and drop as usual. Rewording works everywhere.
- Push (`ger push` in the *ger* terminal, where it can ask you). During a rebase, when `ger push`
  would refuse (the branch still points at the commits from before it), Push says *Finish the
  rebase first*, in `ger push`'s own words, and offers the ways on instead (Abort, and Continue
  once nothing is conflicted); during a
  `git am` it says to finish that. Every ger command the extension
  starts runs in that one terminal, shown when it starts; closed or exited, the next command opens it
  again. When a command still runs there (VS Code 1.93+ can tell), a new *ger* terminal takes over.
- **No upstream** (typical after checking a change out with Gerrit's download commands; `ger
  checkout` and *Check Out to Work On* set it): the view says so and offers **Track origin/…** in one click — the branch it was created from, else
  the remote branch nearest to it — or **Choose another upstream branch…**. Later changes:
  *Set Upstream Branch…* in the view's `…` menu.

**Change** — select any change or commit

- The title bar names the change number. The subject is not repeated there: the commit message
  says it.
- What it needs, in words, with its votes.
- **The commit a rebase stopped at on a conflict** says so instead: *Conflict in d.txt — resolve it,
  then Continue* (several files: how many), with the buttons of Local stack's banner: the same ways
  on, Abort and Skip while files are still conflicted. **Conflicts**, above Files, lists the files
  still conflicted; a click opens one in the merge editor, as under the banner. Files is the commit
  as it was before the rebase, and says so: the rebase has not applied it yet. What a push of it
  would do (*ahead of Gerrit*, whether the votes stay) is not said until it has: that is not known
  yet. What Gerrit has still is: that it has a newer patchset, and under CI the failed builds of the
  last pushed patchset and their logs; only why there is no log to read is left out. Once the
  conflicts are resolved and added, it says *Conflicts resolved — Continue*, and Continue is the one
  to press. A rebase through `git rebase --apply` keeps no todo, so which commit it stopped on
  cannot be told: no Change view says it, and the banner lists the conflicts. Nor does it where
  core cannot tell why it stopped (a resolution committed by hand as another commit, with another
  subject and no Change-Id): Local stack marks the commit it stopped at as stopped, not conflicted.
- **Commit message**: the whole message, its `Change-Id:` trailer shown read-only on a quiet line
  under it (select it to copy). For a commit of your local stack it is a text box without the
  Change-Id: edit it and press **Apply** (`Ctrl+Enter`); *Revert* goes back to the commit's
  message. The commit's Change-Id goes back on as the last trailer; one typed into the box is
  ignored. Other trailers (`Signed-off-by:`, …) stay in the box. Only the message is rewritten —
  uncommitted changes stay where they are, nothing can conflict. **Undo** is offered right after.
  What you typed survives a refresh and switching views until you apply or revert it. Someone
  else's change, or a patchset on Gerrit, cannot be reworded here: its message is plain text.
- **From / To**: what is compared. *To* is the version you look at: your **local commit** (when
  the change is in your stack) or any patchset on Gerrit. *From* is what it is compared with:
  the **base commit** (what the change does) or any patchset older than *To*. Both lists run
  newest first and mark the current patchset and the one you reviewed. Picking *To* at or below
  *From*'s patchset sets *From* back to the base commit.
- **Default mode** (the icon beside the pickers): a click moves to the next mode —
  *Against its base commit* (base → your commit, or Gerrit's current patchset),
  *Since my last review* (the newest patchset you voted or commented on → the current one), or
  *Gerrit vs. local* (the current patchset → your commit: what a push would add; when your commit
  is that patchset, it says there is nothing to push). A mode the change cannot have is skipped.
  The default is remembered and every change you open starts on it; picking *From* or *To* by
  hand overrides it for that change only, until you open it again, and the icon dims meanwhile.
  On someone else's change you reviewed before, a line says what is new since, with **Show** to
  switch to it.
- **Files**: added and removed lines per file, in the colours git gives them. Click for the
  diff of what is compared; an added or deleted file opens on its own. The commit you are
  standing on diffs against the real file, so you can fix things in the diff editor.
- **Comments**: unresolved first. Click one to open its file at the line, its thread expanded there. The chevron shows a chain whole: every comment in full; one about the whole change (no line in a file) opens that way on a click and has a box to reply right there. *Next / Previous Unresolved Comment* (command palette) step
  through the unresolved ones, across files. `Alt+F9` / `Shift+Alt+F9` do it too while the Change view or a
  diff of the change has focus; anywhere else they stay VS Code's own next / previous comment thread, which only
  walks the open file. For a local commit they also appear as comment threads in the
  editor. Reply, or reply "Done" and resolve — saved as **drafts** on Gerrit, published later by
  `ger push` or `ger review --vote`. A reply box has **Save** and **Cancel**, and Gerrit's **Resolved**
  checkbox: by the box in the Change view; in an editor thread a toggle in the thread's header
  (VS Code empties the reply box on any button beside it), its label saying what Save will do. It
  starts where the comment stands, and with your draft there already it changes that draft at once.
  With your draft reply there, the Change view's box also has **Discard**, and so has the box of a
  draft, local draft or note of yours you edit in an editor thread: it deletes it, asking first, as
  the trash does; Save is bottom right, as on Gerrit. In a thread, Esc in the reply box steps back
  to its comments; Esc again (or Esc in the editor) closes it. A reviewer's suggested edit shows in
  the comment: a ```` ```suggestion ```` block of its text as a code block, a Gerrit fix suggestion
  as **Suggested fix**, where it applies and the new text. It is shown, not applied.
- **New comments**: hover a line (or select lines) in the change's diff — or in your file, when
  the shown commit is HEAD — and click **+**. Save it as:
  - **Draft**, when Gerrit has this very commit: on Gerrit, visible only to you until published;
  - **Local draft**, when Gerrit does not (never pushed, or amended since): kept here, in
    `refs/ger/comments`, for when the commit is pushed;
  - **Private note**, always: kept here and never uploaded.

  The comment goes on the version you are looking at: with another patchset chosen under
  **To**, it is a draft on that patchset, on the lines its diff shows.
- **CI**: opened on demand (it downloads build logs: this change's and its parent's, not the rest
  of its chain). Findings this change **introduced** are
  on top with a flame; findings that already fail on its parent are dimmed. Click a finding to
  jump to its file and line.
- **Review Change** (eye icon, changes that are not in your stack): `ger review`
  fetches the change into the review worktree and offers to open that worktree in a new window; your
  own stack is not touched. When the git config key `ger.reviewCheckout` says `branch`, it switches
  this checkout to a `change-<n>` branch instead, the message says so, and *Leave Review Checkout*
  switches back. For reading someone's work.
- **Check Out to Work On** (download icon, changes that are not in your stack): `ger checkout` in
  the *ger* terminal puts the change on a `change-<n>` branch here that tracks its target branch, so
  the change is your local stack and *Push to Gerrit* uploads a new patchset. It may ask before it
  changes anything (update a branch you already have to the newest patchset?), which is why it runs
  in a terminal. The views follow by themselves once it has switched branches (see *Your repository* below).
  For changing a change of yours that exists only on Gerrit, or taking over a colleague's.
- **Edit Commit Here** (pencil icon, your local stack commits, also where they appear in *Review
  chains*): `ger edit` runs in this checkout, in the *ger* terminal: an interactive rebase that stops
  at the commit, so you amend it in place and `git rebase --continue`. No worktree, nothing fetched.
- **Hand to Agent**: for one commit, the same runbook as below, narrowed to it. For a change that
  is not in your checkout it is a brief (the change's comments and CI findings) the agent can read
  but not act on, since the change is not checked out; the dialog says so and offers *Check Out to
  Work On* instead.

**Fix Stack with Agent** (robot icon on *Local stack*)

One run for the whole attention walk. The service writes a runbook: for each commit that needs
edits, the comments to address and the CI findings to fix there, plus the rules — amend in
place, reply "Done" as a draft, never push or publish. You see the runbook and a count before
anything starts; then it goes to the command in `ger.agentCommand`, in a terminal where
publishing is blocked (`GER_NO_PUBLISH`). Without that setting it is copied to the clipboard.

- A comment that needs your decision, not a fix: **Keep for Me** on it (person icon). The
  runbook lists it as left for a human, and a commit with nothing else is skipped.
- CI findings a commit only inherits from the one below are not handed out twice.
- Afterwards the stack view shows the amended commits; you review, then `ger push` sends the
  patchsets and publishes the draft replies.

## Settings

| Setting | Default | |
|---------|---------|--|
| `ger.executable` | `ger` | How to run ger: a program on the `PATH`, a path to it, or a command line such as `uv run ger` (quote a path with blanks). |
| `ger.agentCommand` | empty | Terminal command that starts your agent; `{brief}` is the runbook file, `{rev}` the commit, e.g. `claude "Follow the runbook in {brief}"`. Empty: the runbook is copied to the clipboard. |
| `ger.pollSeconds` | `120` | Background refresh from Gerrit while the window has focus; a change applies at once. It also refreshes when the window regains focus. While Gerrit's event stream is live (below) it slows to every 15 minutes (or this, when longer), a safety net. |
| `ger.notify` | `true` | Notify when a background refresh finds something new to be the most important change. Not for what you just did: the status bar shows that. |
| `ger.closeDiffsOnRebase` | `false` | Close a diff tab opened from the Change view once it is stale: its commit left the stack (rewritten by a rebase, in the views or in a terminal, an amend or a reorder; dropped; or merged past the upstream), or, with your file on its right, HEAD moved off it. Off: the tab stays open and its title says *stale* (`b.txt (6827cbfe) — stale`). A tab that may hold unsaved edits is never closed. |
| `ger.debugLog` | `false` | Start `ger serve` with `--debug-log`: **Show Log** then has, for every request of the views, how long it took and the git and Gerrit calls it made, about once a minute how many calls that minute had, and Gerrit's response bodies. Verbose; for finding out what the views cost. A change restarts `ger serve`. |

**Gerrit's event stream.** When `ger serve` can follow Gerrit's event stream (`ssh … gerrit
**Your repository.** The views follow it as the built-in Git view does: a commit, a checkout, a
`ger edit` and its `git rebase --continue` (anything that moves `HEAD`, a branch or a rebase) or a fetch that moves the upstream, refresh *Local stack*,
the *in your stack* markers and the Change view's buttons about a second after the last change, also when you
work in the integrated terminal. This does not make Gerrit refetch anything (answers still fresh are reused), and says nothing
in a notification, as you did it yourself.

stream-events`: it needs ssh, a key Gerrit accepts and the *Stream Events* capability), the
views refresh a second after review activity happens — a new patchset, comment, vote, reviewer,
merge — instead of at the next poll, and the poll drops to a 15-minute safety net. When it
cannot (HTTP only, no key, no capability, the connection dropped), nothing changes: the views
poll every `ger.pollSeconds` as before, and no notification says so. The status bar tooltip
says which of the two is keeping the views current, and why; the log (**Show Log**) has one line
per change. Configure it with the git keys `ger.eventStream` (`auto`, `on`, `off`) and
`ger.eventStreamHost` / `Port` / `User`:
[docu/Configuration.md](../docu/Configuration.md#ger-serve--gereventstream).

These are VS Code settings. How ger itself behaves (`ger.reviewCheckout`, `ger.webUrl` and
the rest) is git configuration, the same for the extension and the command line: see
[docu/Configuration.md](../docu/Configuration.md).

**Workspace trust.** In Restricted Mode the extension starts nothing: ger runs git and the
program in `ger.executable` in the repository, and a repository you do not trust could make
either run its own code. `ger.executable` and `ger.agentCommand` are read only from your user
settings there, never from the repository's `.vscode/settings.json`. Trust the folder and the
views fill.

## Install

With ger already installed, one command installs the extension that came with it (and updates it after you update ger):

```bash
ger vscode install    # --editor cursor (or code-insiders, codium) for another editor
```

Without ger: install [`ger-vscode.vsix`](https://github.com/togrue/gerrit-workflow-tools/releases/latest/download/ger-vscode.vsix) with *Extensions: Install from VSIX…*, open your repository, and press **Install ger** in the *Review chains* view. That installs the ger release with the extension's version in a terminal, using uv or pipx; **Retry** then starts it, also when your editor's `PATH` is from before the install.

## Requirements

`ger` on the `PATH` (or `ger.executable` set), and a repository where `ger log` works:
`ger.webUrl`, credentials (`ger setup`) and an upstream for the branch.

The folder can be the checkout, a folder inside it, or a worktree. The extension starts with the editor (after startup has finished), so the status bar item is there for a sub-folder of a checkout too; without a git checkout it starts nothing. With several workspace
folders, the first one in a git checkout is used; the log and the status bar tooltip name it.

When something is missing, the *Review chains* view says what and offers the next step:

| The view says | Next step |
|---------------|-----------|
| Open a folder with a Gerrit checkout / none of the folders is in a git checkout | **Open Folder** (a folder you add later starts ger without a reload) |
| The program that runs ger was not found | **Install ger** (in a terminal, with uv or pipx), then **Retry**; or **Open Settings** on `ger.executable` when it is installed somewhere else |
| This repository is not set up for Gerrit yet | **Run ger setup** (in a terminal), then **Retry** |
| ger and this extension do not speak the same version of the `ger serve` API (the line above says which versions, and which to update) | **Install the Matching ger** (the release with the extension's version), then **Retry**; when ger is the newer one, updating the extension fixes it too |
| `ger serve` stopped or failed | **Retry**, or **Show Log** for why |

*Local stack* and *Change* say the same in a line and point to *Review chains*; their buttons in the view title bars appear once ger is running. For an API version they cannot use they say which versions instead, and offer **Update ger** (or, when ger is newer, **Install Matching ger**) themselves.

When the API fits but ger's version differs from the extension's, everything works and a warning says so, once a session: with **Update ger** when ger is older, or a suggestion to update the extension when ger is newer, and **Don't Show Again** for that pair of versions. Dev and local builds are not compared. The log records each comparison and what you chose.

A branch without an upstream still shows your review chains; *Local stack* offers to track one.

## Reporting an issue

**ger: Report Issue…** (command palette, in every state, also when ger does not start; and the `…` menu of
*Review chains*) takes a report of what the views show at that moment, for when something looks or behaves
wrong. The extension runs in projects that are secret, so the report is **anonymized** before you see it,
and nothing leaves your machine without you:

1. The report opens as an untitled JSON document. Read it: it is all that would be shared. Edit it if you
   want something else out. What you take out stays out: if you save the document somewhere before you
   choose, what is shared is what you saved; if you close it without saving, nothing is shared.
2. Then choose. **Copy and Open GitHub Issue** copies the document as it stands and opens a new issue on
   the project's GitHub; the link carries only the label and the versions line, and the issue says where to
   paste the report. **Save to File…** writes it where you say. Closing the notification does nothing.

What a report contains:

- The rows of *Review chains* and the view models of *Local stack* and *Change*: the same rows, icons,
  states and badges, with
  - **kept**: booleans; counts (unresolved comments, lines added and removed, files, chains, votes, the
    number of a patchset, ages such as `waiting 3d`); icons, tones and states; and sentences that are the
    extension's own words, such as *the same as on Gerrit* or *Since your review: patchset 2 → 5*;
  - **replaced**: every other text. Subjects, commit messages, comments, CI findings and tool output,
    errors of git and Gerrit, branch and remote names become filler words with the same length and line
    breaks. Names and e-mail addresses become made-up names as long. Paths become made-up segments, as
    many and roughly as long, without the file's name or extension. Commit ids, Change-Ids, change numbers
    and line numbers become unrelated ones of the same shape; URLs become `https://example.invalid/<n>`.
    A sentence of the extension with such a thing in it (*Track origin/…*, a tooltip naming the reviewers)
    is replaced whole.
  Within one report the same commit, person, file or change number is the same everywhere; the next report
  uses other ones. Nothing is hashed or masked: the replacements are made up, not computed from the original.
- The extension's version, VS Code's version, the operating system and its release, the state of the
  extension (`ready`, `setup`, `no-executable`, …), where Gerrit's event stream is (`live`, `off`, …) and
  whether the views are out of date.

What it does not contain: the log (**Show Log**), any setting (`ger.executable` and `ger.agentCommand` may
hold paths), the names of the workspace folders or of the repository, Gerrit's host, your name, the time.

What remains to be read from a report is the shape of your work: how many commits, files, comments and
patchsets there are, how long each text and name is, how many lines each file gained and lost, the votes,
and how long chains have waited. If that is too much for your project, edit the document before you share
it, or share nothing.

Every field of the view models is classified in `src/anonymize.ts`. A field it does not name is replaced,
key and all, and `npm test` fails until someone decides about it, so a field added later is safe before
anyone remembers the report.

A report is a snapshot file: `npm run gallery -- ger-report.json` draws it (see below), and it can become a
test fixture.

## Third-party notices

The extension carries [Codicons](https://github.com/microsoft/vscode-codicons), VS Code's icon
font, © Microsoft Corporation, unchanged:

- the font (`media/codicon.ttf`) is licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); its text is in `media/codicon-LICENSE.txt`;
- its stylesheet (`media/codicon.css`) is licensed under the MIT License; its text is in
  `media/codicon-LICENSE-CODE.txt`.

Both files are in the `.vsix`, and so in every ger release that carries the extension.

## Build and run

```bash
cd vscode
npm install
npm run compile     # or: npm run watch
npm test            # unit tests of the pure parts (formatting, activity, reorder math, the view models of the two webview views) and of package.json against the code (menus, row contexts, commands)
npm run e2e         # the extension inside a real VS Code, against a fixture repo and a fake Gerrit
npm run gallery     # the two webview views in a browser page: every layout state, dark and light, two widths
```

`npm run e2e` starts your installed VS Code (or `VSCODE_EXE`) with a throwaway profile, opens
a generated four-commit repository, and has the extension start the real `ger serve` over the
test suite's fake Gerrit (`test/e2e/fake_ger.py`). Your git config and ger's cache and config
are kept out. Needs `uv`.

*Local stack* and *Change* are webview views: `src/stackRows.ts` and `src/changeRows.ts` build
what they show as plain data, the pages in `media/` (`stack.js`, `change.js`, `views.css`) only
draw it and send back what you did. They use VS Code's icon font, which `npm install` copies
from `@vscode/codicons` into `media/` (a webview cannot reach the editor's own copy), together
with its two licences ([Third-party notices](#third-party-notices)).

The suite presses every button in turn. For the two webview views it calls what their pages
send (a drop at a line, a button on a comment) and prints their view models as text; how the
pages look and feel is not covered by it. Everything the extension says or does outside its
views (notifications, dialogs, pickers, input boxes, the status bar, terminals, the clipboard,
the browser, untitled documents, files it saves) goes through one seam, `src/ui.ts`, which the suite replaces with a scripted user
(`test/e2e/scriptedUi.ts`) that answers each prompt and writes it down. For each press,
`test/e2e/transcript.golden.md` holds what a user expects, what the extension said and did,
and how the views changed. An **Expect** line says what a user would expect from that press;
where one behaviour is clearly what users expect, the extension does that, and only open
design questions go to a decision. A run whose transcript differs fails and prints the diff: judge each
changed step against its **Expect** line, then accept it with `npm run e2e -- --update`. Set
`GER_E2E_SCREENS=<file>` to also get the full screen before and after every press.

**Pictures of the real thing.** `GER_E2E_PNG=<dir> npm run e2e` also writes a picture of the VS Code
window on start and after every press, `<step>-<button>.png` (1400 × 900, the Gerrit side bar 420
pixels wide, no chat panel). This is how a coding agent looks at the native tree view, the webview
views in place, the status bar and the editor together; the gallery is quicker for the webview
views alone. The run's transcript is written beside them, `transcript.md`. It is off by default: it
makes the run about 20 seconds slower. The pictures are taken
through the window's DevTools protocol (`test/e2e/picture.ts`), with no dependency. On Linux with
no X or Wayland display (a coding agent's shell, a server), VS Code runs headless, pictures or not.
Without an installed VS Code, `npm run e2e -- --get-vscode` downloads the latest stable one for
Linux into `~/.cache/ger-vscode-test`, where the run finds it; `GER_E2E_VSCODE_VERSION=<x.y.z>`
downloads that version instead, as CI does for pull requests. `GER_E2E_TIMEOUT_SECONDS` (default
300) is how long VS Code may take for the suite.

**The gallery** is for how the two webview views look, without starting VS Code. `npm run gallery`
writes `out/gallery/index.html` and prints its path: open it in a browser. It runs the pages of
`media/` as they ship, each in an iframe 300 and 450 pixels wide, in VS Code's Dark Modern and Light
Modern colours. What stands in for VS Code is in `test/gallery/`: `theme.ts` has the `--vscode-*`
variables and a webview's default styles (a variable the pages use and it does not list fails
`npm test`), and `frame.js` is `acquireVsCodeApi()`: it answers the page's `ready` with the view
model, and what the page posts (a click, a drop, the context a right-click would hand to the menu)
is listed under the frame.

- The scenarios (`test/gallery/scenarios.ts`) call `stackVm()` and `changeVm()` on what `ger serve`
  would answer, built from the fixtures the unit tests use (`test/fixtures.ts`). A new state of a
  view gets a scenario there.
- `npm run gallery -- report.json` adds snapshot files as scenarios, in front of the built-in
  ones. A snapshot is `{ "format": 1, "name": "…", "stack": <StackVm or null>, "change": <ChangeVm or null> }`,
  other keys ignored (`src/snapshot.ts`). The report of **ger: Report Issue…** is one: this is how a
  reported layout is looked at.
- `npm run gallery -- --png <dir>` also writes a picture of each frame,
  `<scenario>.<view>.<theme>.<width>.png`, at twice the size. It runs an installed Edge, Chrome or
  Chromium headless (`GER_GALLERY_BROWSER` names another one), once per picture: all of them take
  a few minutes, so `--only <words>` keeps the scenarios with those words in their name. This is
  how a coding agent looks at a layout change it made.

Open the `vscode` folder in VS Code and press `F5` to start an Extension Development Host;
open your Gerrit repository in it. `npm run package` builds a `.vsix` to install elsewhere; a release (`python scripts/build_release.py` in the repository root) builds it into ger, for `ger vscode install`.

**ger: Show Log** shows which repository is used, how `ger serve` was started, and anything it
printed.

## Layout

| File | Role |
|------|------|
| `src/api.ts` | The API types and the HTTP client. No `vscode` import. |
| `src/server.ts` | Starts and stops `ger serve`, and refuses one whose API version does not fit. No `vscode` import. |
| `src/compat.ts` | The API version the extension needs (`REQUIRED_API`), and the rule for whether a served API and a package version fit. No `vscode` import, unit-tested. |
| `src/format.ts` | Words, ages, urgency, activity tracking. Pure, unit-tested. |
| `src/model.ts` | The shared state the views read; refresh; what you have seen. |
| `src/overviewView.ts`, `src/stackView.ts`, `src/changeView.ts` | The three views. |
| `src/rows.ts` | The `contextValue` of every tree row, which the menus in `package.json` match with `viewItem =~ /…/`. Pinned against them by `test/package.test.ts`. |
| `src/comments.ts` | Editor comment threads, stepping, draft replies. |
| `src/statusBar.ts` | The status bar item: the headline (a rebase underway, when core says so) and the activity bell. |
| `src/follow.ts` | The Change view following its commit through rewrites and amends. |
| `src/diffTabs.ts`, `src/staleDiffs.ts` | `ger-file:` documents and the diff tabs of them: opening one, knowing it again, marking it stale or closing it once its right side is no longer the commit its title names, unmarking it once it is again (the rule is pure, unit-tested); your files' repository paths; saving before a rebase continues. |
| `src/changeLoad.ts` | Which change the Change view shows and what of it has come: drawn at once from the clicked row, filled in answer by answer, a stale answer dropped. Pure, unit-tested. |
| `src/review.ts` | Review Change, Check Out to Work On, Leave Review Checkout, Edit Commit Here. |
| `src/upstream.ts` | Giving the branch an upstream. |
| `src/agent.ts` | Handing a runbook to a coding agent. |
| `src/config.ts`, `src/launch.ts`, `src/commandLine.ts` | The `ger.*` settings; how ger is started, as a process or in a terminal; installing ger, and the warning when its version is not the extension's. |
| `src/workspace.ts`, `src/state.ts` | Which checkout is served; the state the views' welcome texts follow (`ger.state`). |
| `src/ui.ts` | Everything said to the user or done outside the views; the e2e suite scripts it. |
| `src/poller.ts` | Background refresh from Gerrit: on a timer while focused, and on regaining focus. |
| `src/gitWatch.ts`, `src/repoWatcher.ts` | The views following local git: what to watch (pure, unit-tested), the debounce, the watcher. |
| `src/extension.ts` | Wiring: views and commands. |
| `src/snapshot.ts` | The snapshot file: the view models of the two webview views at one moment. Read by the gallery. No `vscode` import, unit-tested. |
| `src/anonymize.ts`, `src/fakes.ts` | What becomes of every field of the view models in a report: kept, or replaced (default-deny); and the made-up words, names, paths and ids that replace. No `vscode` import, unit-tested. |
| `src/report.ts` | The report of an issue: the anonymized views and the versions, as a snapshot file; the link to a new issue. No `vscode` import, unit-tested. |
| `src/reportIssue.ts` | ger: Report Issue…: shows the report, then copies it for a GitHub issue or saves it. |
| `test/fixtures.ts` | API-shaped commits, diffs, comment chains and CI logs: shared by the unit tests and the gallery's scenarios. |
| `test/gallery/` | The webview gallery (`npm run gallery`): scenarios, the stand-in for VS Code (theme, `acquireVsCodeApi()`), the page around the frames, pictures. Not in the `.vsix`. |

When the API grows a field, add it to `src/api.ts` first: the types there are the extension's
copy of the contract.
