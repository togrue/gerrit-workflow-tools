# ger for VS Code

Your Gerrit review chains, your local stack, comments, diffs and CI findings — next to the code.

The extension has no Gerrit logic of its own. It starts `ger serve` in the git checkout of your workspace folder and
shows what that says, so it always agrees with `ger log`, `ger show` and `ger inbox`. The API
contract is [docu/spec/commands/serve.md](../docu/spec/commands/serve.md).

## What you get

In the editor, places are called *Gerrit* (the activity bar, the status bar item); commands are listed in the palette under `ger:`, because they run the `ger` tool.

**Status bar** — the *headline*: the one change that most wants you (failed CI on your chain;
then unresolved comments on your chains or the review whose turn is yours, whichever has waited
longer; then a negative vote).
Failed CI counts only on the chain of your local stack, and the item then just says
*CI failed:* and the change number; the tooltip has the change's subject. A bell
with a count appears when chains had activity since you last looked. Click to open the change.
While a rebase is underway (a `ger edit`, a conflict, also one you started in a terminal), the item
shows that instead, as ger serve's headline does (and so does the first row of *Review chains*; both read it from the
local stack, so it goes from both with the rebase): *Rebase stopped at 101*,
*Rebase stopped to split 101*, *Conflict in 103*, *Conflict resolved in 103*, *Rebase paused*, *Rebase stopped* when the
commit it stopped at was reset away, or *Rebase underway* when it cannot tell why it stopped — the change number when the
commit is on Gerrit, else its subject in quotes. A conflict is shown in the error colour, another stop as a warning. The
index is watched while a rebase is stopped, so a `git add` that resolves the last conflict shows at once. The tooltip says
what to do next (amend, resolve and `git add`, `git rebase --continue` or `--abort`); a click
opens *Local stack*. When the rebase ends, the item goes back to the headline.
**When Gerrit cannot be asked** (it refuses the credentials, does not answer, or is not there),
nothing is said as done or as none. Connecting waits at most 10 seconds (`/health`'s
`gerrit_wait`): meanwhile the views say *Asking Gerrit…*, not *Starting ger serve…*, and Local
stack draws what git alone knows at once (`/stack?local=1`). Then *Review chains* says Gerrit's
reason with the ways on as rows (*Check Credentials (ger setup)…* for refused credentials, else
*Retry*, *Check the Gerrit Address (ger setup)…*), *Show Log*, and that local work goes on; Local
stack says once *Gerrit state unknown: …* with *Retry*; the Change view says the change's state,
votes and comments are unknown, once (never *not on Gerrit* or *none*), keeps the task header and its
exit, and Next names the fix instead of the vote. The status bar item wears an error icon, never
the check; Refresh answers *Not refreshed from Gerrit: …* in the status bar, not in a toast that
would offer the views' ways on a second time; a file marked seen is
ticked only once Gerrit took it (a refusal is said, once per reason); a vote, a draft, an edit or
a delete that did not reach Gerrit is said as not done, with *Check Credentials* / *Retry* where
that is the fix. While the views cannot follow Gerrit's event stream, the status bar's tooltip
says when they last read from Gerrit, beside how they keep up (every `ger.pollSeconds`, and when
the window gets the focus again).
If `ger serve` dies under the views, it is started again at once, quietly; a restart that fails
leaves the last answer, and *Review chains* says *Out of date: …* with *Restart ger serve* and *Show Log*.

When a refresh fails, whether it ran in the background or you pressed *Refresh* (Gerrit unreachable, `ger serve` stopped
and cannot be restarted), the views keep the last answer and the item carries a warning mark; its tooltip says why
it is out of date, and *Review chains* is labelled so. The next refresh that works clears the mark. While `ger` cannot run at all (not found, not set up, folder not trusted) the
views clear and say why, and the timer stops trying: it would only start `ger serve` to fail again, once per poll.
The log says so once; regaining focus, *Retry* and a changed `ger.executable` still try.

**Review chains** (activity bar → Gerrit)

- *To review* first: the chains whose turn is yours, as Gerrit's attention set says (a new
  patchset, a reply to you, or not reviewed yet). The chains you reviewed wait on their author:
  they sit in the collapsed *Waiting on the author* group below, with a clock, and come back up
  when it is your turn again. The activity bar's badge counts the chains whose turn is yours.
  *Outgoing*: your open chains.
- One row per chain, showing its top change, the worst vote cast of each label and what is missing
  (*V+1 CR+1 💬5 open · V missing on 2, CR on 1*; *no votes* only when no change of it has any),
  unresolved comments and age. Expand
  it for the chain's changes: all of them, also those not ready for review yet (greyed out, with
  why: *not verified yet*, *WIP*). A `●` marks new activity.
- The icon in front is the most urgent thing about the chain or change: an error for failed CI or
  a broken change (abandoned, merged drift, invalid or missing Change-Id), a comment bubble for
  unresolved comments (the ones the Change view lists as open), a warning for a negative vote, a
  newer patchset on Gerrit or a merge whose equivalence is unknown, a clock while it waits, a
  check when nothing does. A *To review* chain with nothing wrong shows an eye while it is your turn, a clock while it waits on its author. The status bar
  uses the same icons for the headline.
- Changes that are commits of your local stack are marked *in your stack* and open as local commits.
- **Comments at a glance**: a change's row says how many comment chains are open, *💬2 open*
  (never *2/5*, which reads as "2 of 5 done"; the tooltip says *2 of 5 comment chains open*), and
  is coloured by where they stand: **yellow** while one waits for your answer, **blue** while drafts of yours wait to be
  published (once nothing is left to answer), plain when they wait on the other side or are all
  resolved. The badge at the right says how many are left to answer, or ✎ for drafts. **Hover** the
  row for the comments themselves: who wrote what where and in which state, each with **Go there**
  (the file at the comment, its thread open), **Reply Done** and, on your own commit's suggested
  edit, **Apply**. The hover reads the comments once (or takes them from the Change view when it
  shows that change); the row's colour needs nothing beyond what the list already has.
- **While you review a chain** (a staged review here) the view steps aside, and so does *Local
  stack*: the **task header** at the top of the Change view says what *Review chains* said about
  the chain, and the Change view has the side bar. While you rework your own (a rebase underway)
  both step aside too, and the header lists your chain's commits. **All chains** (in the header) brings
  them back for the rest of that task; the eye in this view's title bar hides them again. When the
  task ends they are back by themselves. The change a staged review holds is marked *reviewing*
  here when you see it.
- Right-click a change (or a chain, for its top change) › **Copy Change-Id** or **Copy SHA**: the
  local commit for a change in your stack, else the commit of its current patch set.
- **Sort Chains…** (title bar): *By chain state* — the order `ger inbox` gives, what needs you
  first — or *By last activity*, newest or oldest first. It applies to both sections and is
  remembered per workspace; the sections name an order other than by state.

**Local stack**

The commits above your upstream, newest first. One row per commit:

- **In a staged review** (`ger review` checked somebody's change out here: the review worktree, or
  this checkout with `ger.reviewCheckout` = `branch`), the rows are their chain, not your work: the
  header says *reviewing 2155 ← [2156]*, as `ger review` prints the chain, and the change under
  review is marked so (HEAD sits at its parent only to stage it). Nothing moves, and the rows offer
  review actions only: Open All Changes, Vote and Publish…, Open in Gerrit, the copies. Push, Fix
  Stack with Agent, Edit Commit Here, squash, drop and rewording are not offered, as core refuses
  them there (`ger review --unstage` to work on the change, or *Leave Review Checkout*).

- The **comment badge** on the right (here, in the Change view and in the task header) draws the
  state so it cannot be misread: the number is how many chains are **open**, a **ring** around it
  is filled by their share of all chains, and the ring's shape says the state besides its colour:
  **dashed** and blue while drafts of yours wait to be published (they lead: what is left to answer
  besides is in the tooltip and in the task header's *1 to answer · your drafts*), **thick** and
  yellow while something waits for your answer, **thin** while they wait on the other side, a full
  ring with a **tick** once all are resolved. The tooltip says it in words (*3 of 5 comment chains
  open · 1 to answer · 2 answered by your drafts*). Drafts are known from core's count of those
  that answer an open chain, and for the change the Change view shows, all of them: a draft that
  answers nothing on a commit not shown is not seen until it is shown (asking Gerrit for drafts on
  every load would cost a request a warm `ger log` does not make). Hover it
  a moment for the comments, with *Go there*, *Reply Done* and *Apply*. From the keyboard: the
  list walks its rows with the arrow keys (each row read out with its notes, Gerrit's state and its
  badges' words); **Tab** then reaches the focused row's badges, one stop named by their words, and
  **Enter** there shows the comments. The Change view's head badges are one stop too, and every
  button, row and step of both views shows the focus ring.
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
  **CI vote**. Grey and ending in **?** (*+2?*), they are the last pushed patchset's: the commit's
  code changed since, and Gerrit may not keep them. Hover any of them for the words; the review vote's tooltip names the
  reviewers (and who is CC'd). A pushed change with nobody to review it shows a **person +** icon
  where the review vote goes.
- `HEAD`, the short commit id, *code changed* (see above), and *held back* where `ger push` would
  stop at the ready boundary. The boundary itself is a dashed line, *ready boundary*, under the
  oldest commit held back (Push sends what is below it), and one quiet line above the rows says
  how much of the stack is where, in `ger log`'s summary words: *6 on Gerrit · 1 to upload · 2 held
  back · 1 failed CI*. A `fixup!` commit (`ger fix`) names the commit it folds into, *→ 2168*
  (its change number, else its short id), and that commit says *1 fixup pending* until
  `ger rebase --autosquash` folds it in. They stay in commit order: a row's place is where the commit is.
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
  **Continue** once nothing is conflicted (the one to press: the primary button), **Abort**, and **Skip** where it stopped on a conflict
  (`ger rebase --continue`, `--abort`, `--skip`, in the *ger* terminal, also in the palette while a
  rebase is underway, Skip only where it is offered). Skip asks first, and says what it throws
  away: the commit and the conflict resolution done so far, staged or not; Abort brings back the
  branch as it was before the rebase, not the resolution. **Abort asks first where it throws away
  what no stash keeps** — the commits made in this rebase that the branch before it does not have
  (an amend, a resolved conflict, a split: by patch id and message, so a commit only replayed is
  not one), named with their ids, and a resolved conflict (`GET /rebase/refusal?action=abort`'s
  `discards`). Beside Abort, **Show What Would Go** opens those commits' files in one editor each
  and aborts nothing; Abort asks again. A fix saved and not amended is no reason to ask: `ger rebase --abort` puts it in a
  stash before git resets the tree, and says so (*kept: your changes to calc/ops.py, not amended,
  in stash@{0}; `git stash apply` brings them back*). Where nothing is lost it does not ask. At an edit stop, or once you split a
  commit there, there is nothing to skip: the commit is already in, and `git rebase --skip` would
  only throw away what is not committed yet, so `ger rebase --skip` refuses there too. Run from
  the palette where it is not offered, a way on says why not in `ger rebase`'s own words
  (`rebase.refused`).
- **The Change view follows the rebase.** Each time it stops somewhere new — an edit stop, a
  conflict, also from a `git rebase --continue` in a terminal — the Change view shows the commit it
  stopped at, whatever was clicked before (the keyboard stays where it is). While it stays at that
  stop (you amend, resolve, or click another row) the view is yours. A review follows the same way:
  started, moved along its chain or updated anywhere (the views, `ger review` in a shell, a restart),
  the Change view shows the change it holds; once you leave it, the change's page on Gerrit. Only a
  commit of your own stack that left it is said to be *not in the local stack any more*, and it is
  shown again once it is back.
- **Stopped on a conflict**, the commit it stopped at has a red conflict icon and is not dimmed:
  it is what wants you. The files still conflicted are listed under the banner; click one to
  open it with its conflict markers in the text editor, where VS Code offers *Accept Current*,
  *Accept Incoming*, *Accept Both* on each conflict and *Resolve in Merge Editor*. It does not open
  the merge editor at once: that starts with its result dirty (*No Changes Accepted*), and saving it
  would write its own result over a resolution made meanwhile somewhere else. Once they are resolved and added, the banner says it is
  ready to continue: Continue appears, and is the one to press. The index is watched while a rebase
  is stopped, so a `git add` in the terminal shows within a second; Continue from the palette looks
  again before it says no. Nothing can be moved until the rebase is finished or aborted.
- **Continue saves first, asking.** `ger rebase --continue` sees only the files as they are on
  disk, so before it runs, any file of the repository with edits only in an editor (the diff of
  the commit you stand on has your file on its right side) is named in a dialog. **Save All**
  saves them; saved edits are not part of the rebase yet, so core's check before every "go on"
  (`GET /rebase/refusal`) then refuses, and a warning says what they need (`git add` them at a
  conflict, amend them in at an edit stop, or `git stash` them), with nothing run in the terminal.
  So does Continue whenever files of the repository are changed and not amended or added. At an
  edit stop that is said before the press: core serves the files changed and not staged with the
  stop (`rebase.uncommitted`), Continue is drawn disabled with why, and the Change view of the
  commit lists them under **Changed, not amended** (a click opens one). Files staged there go into
  the commit on Continue without a word from git: core serves them too (`rebase.staged`), the
  Change view lists them as **Staged, go into this commit** (both kinds: *Changed at this stop*),
  and Continue says *amend the 2 staged files into this commit, then …*. **Continue without saving** runs it with the edits left in the
  editor: git rewrites the files under them, and VS Code says so when you save. Cancel runs
  nothing. Nothing is saved unasked.
- **Work on Next** (the step-over icon in *Local stack*'s and the Change view's title bars, the
  palette): the attention walk of `ger edit -n`, in the *ger* terminal. During a rebase it continues
  it and stops at the next commit tip-ward that needs edits (comments to answer, a failed build, a
  negative vote); with no rebase it starts the walk (`ger edit`: where the walk stood, else the oldest
  commit that needs edits). Once it has stopped, the Change view shows that commit and the keyboard is
  on its first comment to answer (*Next Unresolved Comment*), else on its first CI finding. With no
  commit left that needs edits it says so and offers **Push to Gerrit…**; so does a Continue that
  finishes the rebase, with **Work on Next** too while commits still need edits. Before anything runs,
  core's own check is asked (`GET /rebase/refusal`), as it is for **Continue** and **Skip**: when it
  would refuse — files changed at the stop and not amended in, a conflict still open — a warning
  says why in ger's words, and nothing runs in the terminal, where it would go unseen.
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
  Gerrit's patchsets, compared with each other, never go stale. In a staged review, the diffs
  with your file on the right are of the change the review holds: once it moves on (to the next
  change of the chain, or a newer patchset) they are stale too.
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
- **How the last step ended** (Continue, Abort, Skip, Work on Next's `ger edit -n`, Push, run in the
  terminal) is said in ger's words, kept until the next action: in the task header while one is
  shown, else as Local stack's first row (*rebase aborted: s14 is at d66fc18 …, as before the
  rebase*, with what was kept or is gone after it; *push cancelled: nothing sent*; *Gerrit rejected
  the push: branch no-such-branch not found*, with **Choose Upstream…** where ger's next step is
  another upstream), and once in the status bar. It comes from core's **step outcome** (`GET /stack`'s
  `last_outcome`), which each of those commands leaves in the git dir; the view looks again as soon
  as the command in the terminal ends (VS Code's shell integration) or the file changes. It lives
  until the next action (Edit Commit Here, Work on Next, a rebase step, Push) or until what it says
  is no longer true, whichever is first: once `HEAD` moves on, a rebase begins or ends, or the
  changes it kept in a stash are back in the work tree (`git stash apply`), it is history and goes.
- **The title bar offers Work on Next and Push only where they would do something** (P6): Work on
  Next while a commit needs edits and nothing blocks the rebase underway (no conflict, no fix
  left unamended), Push while a push would send something (new code, a commit not on Gerrit,
  drafts) and nothing blocks it (a rebase, `git am`, no upstream). An empty stack shows neither;
  Work on Next on a stack with nothing to edit no longer offers a Push that would refuse.
- **A commit without a Change-Id** on the branch checked out reads *push adds a Change-Id*, not
  *held back*: `ger push` gives it one before its preview and sends it as a new change (and takes
  the ids back when the push is not sent). Core's `adds_change_id`; the ready boundary is judged
  as the push does.
- **A detached HEAD** says *Detached HEAD: commits made here belong to no branch*, with **Create
  Branch Here…** (VS Code's *Git: Create Branch…*).
- Push (`ger push` in the *ger* terminal, where it can ask you). During a rebase, when `ger push`
  would refuse (the branch still points at the commits from before it), Push says *Finish the
  rebase first*, in `ger push`'s own words, and offers the ways on instead (Abort, and Continue
  once nothing is conflicted); during a
  `git am` it says to finish that. Every ger command the extension
  starts runs in that one terminal, shown when it starts; closed or exited, the next command opens it
  again. When a command still runs there (VS Code 1.93+ can tell), a new *ger* terminal takes over.
- **No upstream** (typical after checking a change out with Gerrit's download commands; `ger
  checkout` and *Check Out to Work On* set it), or **one configured and gone** (deleted on the
  remote, or pruned: *The upstream origin/no-such-branch of s19 is gone*, core's `gone`): the view says so and offers **Track origin/…** in one click — the branch it was created from, else
  the remote branch nearest to it — or **Choose another upstream branch…**. Later changes:
  *Set Upstream Branch…* in the view's `…` menu.
- **The stack could not be read** (git refused, an upstream branch that is gone): the view says so
  in ger's words, git's message under it as it came, and offers **Reload**, **Choose an Upstream
  Branch…** and **Show Log**. Which of them helps is not known (core does not say what kind of
  failure it was), so none is drawn as the one to press.

**Change** — select any change or commit

- The title bar names the change number. The subject is not repeated there: the commit message
  says it.
- **When the change cannot be shown** (git or Gerrit refused, the commit is gone after a rebase),
  the view says so in ger's words with their message under it, and offers **Show Again** (the one
  to press: *Show the Change Again* in the palette), **Show Local Stack** for a commit of yours (pick
  it as it is now) or **Show Review Chains** for somebody's change, and **Show Log**. Every error
  state of the two views is built in `src/problemRows.ts`, so none says what went wrong and offers
  nothing. Under **CI**, findings that could not be fetched offer **Try Again**; a failed build
  with no log to read offers **Open in Gerrit**, where the change's messages say what CI reported;
  a log that could not be read opens in the browser from its row.
- What it needs, in words, with its votes.
- **The commit a rebase stopped at on a conflict** says so instead: *Conflict in d.txt — resolve it,
  then Continue* (several files: how many), with the buttons of Local stack's banner: the same ways
  on, Abort and Skip while files are still conflicted. **Conflicts**, above the commit message (in a
  rework right under the task header and Next), lists the files still conflicted; a click opens one
  with its conflict markers, as under the banner. While the task header is shown, the head does not
  say the conflict again (the header's Continue says why it waits, Conflicts lists the files, Next
  opens the first): it says what Gerrit has for you on the change, or *Nothing from Gerrit to answer*. Files is the commit
  as it was before the rebase, and says so: the rebase has not applied it yet. What a push of it
  would do (*ahead of Gerrit*, whether the votes stay) is not said until it has: that is not known
  yet. What Gerrit has still is: that it has a newer patchset, and under CI the failed builds of the
  last pushed patchset and their logs; only why there is no log to read is left out. Once the
  conflicts are resolved and added, it says *Conflicts resolved — Continue*, and Continue is the one
  to press. A rebase through `git rebase --apply` keeps no todo, so which commit it stopped on
  cannot be told: no Change view says it, and the banner lists the conflicts. Nor does it where
  core cannot tell why it stopped (a resolution committed by hand as another commit, with another
  subject and no Change-Id): Local stack marks the commit it stopped at as stopped, not conflicted.
- **Next** (the primary button under the change's state, `Alt+Enter`): the one thing to do now,
  named on it: *Update to patchset N*, *Next comment*, *Next file*, *Vote and publish*, *Next change*,
  *Leave review* while you review; *Resolve src/routes.ts* (the first file still conflicted, then
  the next), *Continue*, *Next comment*, *Work on next*, *Push* on your own commit; *Review change* on somebody's change you only look at. Which one, when, is
  the table under [Next: what it names, and when](#next-what-it-names-and-when). It asks nothing of
  `ger serve` to decide; every other action stays where it was, in the title bar and its `…` menu.
- **Commit message**: the whole message, its `Change-Id:` trailer shown read-only on a quiet line
  under it (select it to copy). For a commit of your local stack it is a text box without the
  Change-Id: edit it and press **Apply** (`Ctrl+Enter`); *Revert* goes back to the commit's
  message. The commit's Change-Id goes back on as the last trailer; one typed into the box is
  ignored. Other trailers (`Signed-off-by:`, …) stay in the box. Only the message is rewritten —
  uncommitted changes stay where they are, nothing can conflict. **Undo** is offered right after.
  What you typed survives a refresh and switching views until you apply or revert it. Someone
  else's change (also one checked out for review), or a patchset on Gerrit, cannot be reworded
  here: its message is plain text, set to read in a narrow side bar: the subject in bold;
  paragraphs and list items hard-wrapped at 72 joined again, so they wrap once, where the view
  does; indented and fenced blocks kept line for line in the editor font (they scroll sideways when
  wide); the trailers (`Bug:`, `Signed-off-by:`) small and dim, as the Change-Id is.
- **From / To**: what is compared. In a task (reviewing, reworking), while they are the default
  mode's sides, they are one line that says it (*Base commit → Local commit 464bd89*) with
  **Change** to open the pickers; picked by hand, they stay open. Reworking, the commit message box
  is two lines high until it has the keyboard: the comments and files you came for come up. *To* is the version you look at: your **local commit** (when
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
  switch to it. When nothing is (you reviewed the current patchset, or no file differs), there is
  no such line.
- **Files**: added and removed lines per file, in the colours git gives them, and a **tick** per
  file: a filled check once it is **seen**, an empty circle until then. A file is seen when its
  diff, or the file, was shown to its last line, or when you click its tick (a click again takes
  it off). The ticks are Gerrit's own per-file *reviewed* flag of the patchset looked at, the one
  its web UI shows, so they are there again after a reload or on another machine; on a version
  Gerrit does not have (your local commit) they are kept for the session. On somebody's change the
  header says how far you are: *2 of 4 files seen*, with **Mark all seen** beside it. Inside the
  combined diff (*Open All Changes*) VS Code reports every file as shown whole wherever it is
  scrolled, so it ticks none on its own: while it is the editor in front, Next reads *Mark N files
  seen* and one press ticks them all (one request to ger serve; Gerrit takes one per file). Else
  *Next file* opens the first file not seen on its own, where reading it to the end ticks it. The
  command line does the same: `ger review --seen PATH…`, `--unseen PATH…`, `--seen --all`.
  Click a row for the diff of what is compared; an added or deleted file opens on its own. What your working files
  hold diffs against the real file: the commit you are standing on, so you can fix things in the
  diff editor; and in a **staged review** (`ger review`, in the review worktree or in place), the
  change under review, at the patchset checked out, against its base or since your last review.
  There the comment threads are drawn where you write them, VS Code's Comments panel lists them,
  and go to definition and references work as in any file. The right side is your file, so it can
  be edited: an edit shows as an unstaged change in Source Control, and `ger review` refuses to
  move on (`--next`, `--done`) until it is put back, saying how. Another patchset, a commit below
  HEAD, or a file the change deletes is shown as it is in the commit, read-only.
- **Open All Changes** (the two-pages icon in the title bar, on a change row of *Review chains*,
  in Local stack's context menu, the palette): every file of what is compared in one editor,
  VS Code's multi-diff editor, in the Files list's order, top to bottom. Each file has the sides
  its own diff has, so in a staged review comments and go to definition work there too. A
  click on a change still only shows it here: walking the stack with the arrow keys, or reading a
  message, opens no editor. A review that moves on (*Review Next Change*) closes the combined diff
  of the change it left, as its right side is your file. VS Code before 1.86 has no multi-diff
  editor: the first file's diff opens instead.
- **Comments**: unresolved first. On your own change the header says what is left for you:
  *2 to answer · 3 answered by your drafts (push publishes them) · 1 waiting on a reply* — a comment
  your draft answers is still unresolved on Gerrit until the draft is published, but it is no longer
  yours to answer; on a change you review, how many are unresolved. The list is an **index**: click one to open its file at the comment, its editor thread expanded and what it was aimed at highlighted (the characters of a range comment, else its lines) until you move on. Reading, replying, resolving and applying happen in that thread; the row only keeps *Keep for Me* for the agent. The chevron shows a chain whole: every comment in full. A chain about the whole change (no line in a file) has no thread to go to: it opens that way on a click, and its row's buttons reply (*Reply as Draft…*, in a box at the top of the window), reply *Done*, edit or delete your draft. The extension never opens VS Code's *Comments* panel (`comments.openView` defaults to `never` with it installed). *Next / Previous Unresolved Comment* (command palette) step
  through what is left for you to answer, across files: not a comment your draft already answers,
  nor one whose last word is your published reply (it waits on a reply). The status bar counts what
  is left, *Comment 2 of 3 to answer*; saving a reply does not send the walk back to the first: it
  goes on from where it stood, by comment, not by position. With nothing left, it says so, and how
  many your drafts answer. One about the whole change (no line in a file) is shown in the Change view,
  its text whole and the keyboard on its row. On a change you review (not yours), it walks every
  open comment and your own drafts, local drafts and notes too, so you can read again what you are
  about to publish. `Alt+F9` / `Shift+Alt+F9` do it too while the Change view or a
  diff of the change has focus; anywhere else they stay VS Code's own next / previous comment thread, which only
  walks the open file. For a local commit they also appear as comment threads in the
  editor, in your file: drawn where the commented lines are in it now, mapped by `ger serve` from the
  version each comment was written on (a comment on an earlier commit of the stack, seen at the tip,
  sits on its line there, not on the line number it had). A comment whose lines have changed since
  is drawn where they were, its thread saying *outdated: line 11 has changed since*, and its row in
  the Change view *outdated: changed since*; one that moved says *line 13 in your file*. Reply, or reply "Done" and resolve — saved as **drafts** on Gerrit, published later:
  on your own commit by your next `ger push`, anywhere else by *Vote and Publish…*. A reply box says whom it answers (*Reply to Bob…*: the last who
  spoke in the chain). VS Code has one prompt for all of an extension's threads, so in an editor
  thread the name is said only where it is sure: the thread opened from the Change view or the walk,
  or the one the cursor is in; else the one person every thread of the change shown answers; else
  just *Reply…*. A name from the change shown before never stays (it named Bob under a thread of
  Alice's alone). A thread opened by clicking its gutter mark, the cursor elsewhere, keeps the name
  of where the cursor is. It has **Save**, **Done** (what you typed, or "Done",
  saved as your draft resolving the comment) and **Cancel**, and Gerrit's **Resolved**
  checkbox: in an editor thread a toggle in the thread's header
  (VS Code empties the reply box on any button beside it), its label saying what Save will do;
  in the *Reply as Draft…* box (a row's reply button) a toggle in the box's title bar, the line
  under the box saying what Enter saves, and `Ctrl+Shift+Enter` saves and resolves at once. It
  starts where the comment stands, and with your draft there already it changes that draft at once.
  The box of a draft, local draft or note of yours you edit in an editor thread has **Discard**: it deletes it, asking first, as
  the trash does; Save is bottom right, as on Gerrit. In a thread, Esc in the reply box steps back
  to its comments; Esc again (or Esc in the editor) closes it. A reviewer's suggested edit shows in
  the editor thread as **Suggested edit**, old → new as a small diff: a ```` ```suggestion ````
  block of the comment's text in its place, a Gerrit fix suggestion below the text with where it
  applies (the old lines where `ger serve` has them: on your own commits; elsewhere the new lines
  alone). The Change view's row says *suggested edit*. On your own commit, **Apply Suggested Edit to
  Your File** (the lightbulb in the thread's title, and *Apply* in a row's hover) writes it into your file where
  the lines are now (see *For a local commit* above), unsaved edits included, and offers **Reply
  Done**. It refuses, saying so, when those lines no longer say what the suggestion replaces, or
  ger cannot tell where they are: then apply it by hand. Nothing is saved or committed: undo it with
  `Ctrl+Z`, amend it in (or `ger fix`) once you are done.
- **Suggest Edit** (the editor's context menu, the palette): on the selected lines (the cursor's
  line without a selection) of the change's diff, a comment box opens holding them in a
  ```` ```suggestion ```` block, as Gerrit's own *Suggest edit* writes it: change them into what
  they should say, add a sentence above it, **Save**. It is a new comment as below (a draft on
  Gerrit where Gerrit has the commit), Gerrit's web UI offers to apply it too, and Vote and
  Publish lists it *(with a suggested edit)*.
- **New comments**: hover a line (or select lines) in the change's diff — or in your file, when
  your working files hold the shown commit (see *Files*) — and click **+**. Save it as:
  - **Draft**, when Gerrit has this very commit: on Gerrit, visible only to you until published.
    The first draft on a change says what publishes it, with a button that does it now: on your own
    commit your next push (**Publish Drafts…**), on anyone else's **Vote and Publish…**;
  - **Local draft**, when Gerrit does not (never pushed, or amended since): kept here, in
    `refs/ger/comments`, for when the commit is pushed;
  - **Private note**, always: kept here and never uploaded.

  The comment goes on the version you are looking at: with another patchset chosen under
  **To**, it is a draft on that patchset, on the lines its diff shows.
- **CI**: opened on demand (it downloads build logs: this change's and its parent's, not the rest
  of its chain). Findings this change **introduced** are
  on top with a flame; findings that already fail on its parent are dimmed. A finding's message
  wraps to three lines (a JUnit assertion goes on in its detail; the tooltip has it whole), where
  and whence under it, and opening the section brings its first rows into view. Every finding with
  a place in a file is also an editor problem of the change shown: a marker at its line, a row in
  **Problems**, the whole message on hover, source *ger CI*, the build as the code (a link to its
  log); they go when the Change view leaves the change. Nothing in it knows a language: a
  compiler's error, a failed test and a linter's warning are a path, a line and words. Click a
  finding to jump to its file and line; that opens the file, not its diff, so the file is not
  ticked seen.
- **Vote and Publish…** (check icon in the Change view's title bar for someone else's change, and
  on a *To review* row, where it takes the row's whole chain): `ger review --vote`. A picker offers
  the votes Gerrit lets you give there, each with what the project says it means, opening on
  Code-Review +1, and your drafts without a vote. Then an optional message, then one dialog listing
  everything that goes out: the vote, each draft (`path:line` and its first words), the message,
  on which changes, and the changes of a chain that do not take the vote. A chain vote never
  replaces a vote you gave: a change that carries yours keeps it (*2156 …: keeps your
  Code-Review -1*) and still gets your drafts; Vote and Publish on that change alone replaces it. Publish sends it; a
  notification says what went out and the views refresh, so the chain leaves *To review* when
  Gerrit says so. On the change a staged review holds, the notification offers the way on: **Next
  Change** while the chain goes on above it, **Leave Review** at its top. Should the change get a new patchset meanwhile, nothing is sent and **Look
  Again** starts over on what is there now. **Publish Drafts…** (the Change view's `…` menu, the
  palette) sends your drafts without a vote: `ger reply --publish`.
- **Review Change** (eye icon, changes that are not in your stack): `ger review`
  fetches the change into the review worktree and offers to open that worktree in a new window; your
  own stack is not touched. The new window opens on the review: the Gerrit view, the change under
  review shown, and all its files in one editor (the same whenever a window opens on a staged
  review with no editor open yet; with editors open, only the change is shown). When the git config
  key `ger.reviewCheckout` says `branch`, it switches this checkout to a `change-<n>` branch instead,
  the message says so, the change is shown with all its files, and *Leave Review Checkout* switches
  back. For reading someone's work. While its review is open in the worktree's own window, this
  window says so under the change's state and Next is **Go to Review Window**. In `branch` mode,
  uncommitted changes to tracked files stand in its way: the Change view says so before the press
  (core's `review_blocked`), and the refusal offers **Review in a Worktree Instead** and **Commit
  or Stash Them…** (Source Control) as buttons; nothing is changed. **Leave Review** closes the
  review's combined diffs and says where the checkout is now (*back on main*, or the review worktree
  free) and what is next: the headline, with **Open It**. A comment written on an earlier patchset
  says so (*written on patchset 1, line 5*), drawn where its lines are now when ger can map them,
  else marked outdated; going to a resolved comment draws its thread (read only) at its line; and
  going to a comment while the review's combined diff is in front opens the file in a tab of its own
  beside it. When the inbox no longer lists a change of the chain under review (a new patchset
  dropped its Verified vote), the task header keeps it, marked *not in your inbox now*.
- **Review Next Change in Chain** (arrow in the Change view's title bar, `Ctrl+Alt+PageDown`) and
  **Review Previous Change in Chain** (the `…` menu, `Ctrl+Alt+PageUp`), while a staged review sits
  in this checkout: `ger review --next` / `--prev`. The review moves to the change above (below) in
  the review chain, at its current patchset; the Change view shows it, its files open in one editor
  (*Open All Changes*), and the combined diff of the change it left closes. At the top of the chain it
  says so and offers what `ger review` suggests there: **Vote on the Chain…** and **Leave Review**.
  With edits of yours in the way, nothing moves, and it says what to do in `ger review`'s words.
  It answers at once: while you read a change, the one above it is asked for ahead (one request,
  while ger serve is idle), so *Review Next Change* draws it straight away, its message, files and
  comments in the Change view and its diff in one editor read from git, titled *checking out…*.
  The Change view says *Checking out 2156…* in place of Next until the checkout is
  done (once: the header does not say it again); then the diff opens again on your files, where Go to Definition and edits work, and the
  *checking out…* tab closes. In between, comments and the diff are there to read; language
  features are not. Without an answer asked ahead (*Previous*, a jump), it waits for the checkout,
  as before. Only the local stack is read again afterwards: nothing moved on Gerrit.
- **Update to patchset N**: when the author has pushed since you checked their change out, the
  Change view of the change under review says *Gerrit has a newer patchset than the one checked
  out*, with this button (also *Update the Review to the Newest Patchset* in the palette). It runs
  `ger review` again on the change, in the same place. Whenever a staged review shows a change you
  reviewed at an earlier patchset (after the update, *Review Next Change*, the review window
  opening), it starts on **Since my last review**, and so does the combined diff; *Show* and
  *Compare* switch back as usual. The line *Since your review: patchset 1 → 2* is said for the
  change under review as for anyone's change.
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
- A negative vote stops the walk, but it is yours to answer: a commit with nothing else is listed
  as left for a human, with the vote (`Code-Review -1 from Bob`).
- CI findings a commit only inherits from the one below are not handed out twice.
- Afterwards the stack view shows the amended commits; you review, then `ger push` sends the
  patchsets and publishes the draft replies.

### The task header

While you review a chain (a staged review here) or rework your own (a rebase underway), one block
at the top of the side bar says which task you are in, and the lists that are not about it step
aside (their `when` clauses read `ger.task` and `ger.taskAll`) so the Change view has the room:

- **Reviewing** (at the top of the Change view; *Review chains* and *Local stack* step aside):
  *Reviewing Alice's chain · change 2 of 4*, then **Left**: how many changes are still to vote on,
  how many are not ready for review, how many comments are left to read (for the change shown, the
  Change view's own count of its comments, so both say one number). Then the chain in reading
  order, oldest first, one row each: its subject and its state in an icon **and** a word —
  *reviewing* (an eye, the row barred and bold), *you voted +1* (a thumb), *to review* (an empty
  circle), *not ready: WIP*, the comment ring with *2 to answer* / *your drafts* / *waits on a
  reply*, *CI failed*. A click on a row goes there: the change reviewed is shown, the one above or
  below is *Review Next / Previous Change*, another is *Review Change*. Under it **Leave review**,
  with where it goes back to.
- **Reworking** (at the top of the Change view too; *Review chains* and *Local stack* step aside,
  so the comments you answer have the room): *Reworking your chain · change 2 of 5*, **Left** over
  the whole chain (*3 comments to answer · CI failed on 1 commit · 2 commits need edits*: CI is
  counted by commit, as the rows show it, since the findings are known only once a log is read),
  the chain oldest first, each commit *replayed*, *stopped here* (barred and bold) or *to come*
  (dimmed), with its comment ring, *needs edits* and *CI failed*; a click shows that commit. Then
  **Continue** (*commit this stop as it is, then replay the 3 commits above*) and **Abort** (*put
  the branch back as it was before the rebase*), **Skip** at a conflict, each with what it does
  beside it, or why it does not run now in core's words: a button refused now is drawn disabled
  (dimmed, it does nothing), *not now: …* beside it, also while the step before it still runs.
  Continue is the primary button once it is the next step (the conflicts resolved); otherwise Next,
  under the header, is. *not now* is one line (*resolve calc/ops.py first*, *calc/ops.py changed, not
  amended: amend or stash it first*); core's whole refusal, with the commands, is its tooltip.
  **Left** counts what the rebase itself still needs first: *1 conflict to resolve*, *calc/ops.py
  not amended*, then the comments, CI and edits, then *2 commits to replay*; *nothing left to answer
  or fix* only when none of these is left. They are shown only here: the Change
  view's head of a commit stopped on a conflict says what Gerrit has without them, no note says the
  commit is *ahead of Gerrit* (every commit a rebase replays is: the push says what it sends), and the
  message box says only why it cannot be edited now, and *Local stack*,
  when *All chains* shows it, leaves out its rebase banner (the conflicted files stay there). They
  run in the ger terminal; once the step is sent (its question, if it has one, answered: nothing
  reads as under way while Skip's or Abort's question is open, and a question cancelled leaves no
  trace) until its step outcome is served or the stack moves on (another stop, or the rebase over)
  the header says *Continuing the rebase in the ger terminal…*, its buttons wait, and Next is not
  offered (nor `Alt+Enter`), so nothing starts over the step that runs. A step refused before it
  runs (core's check, the files to save) says why in the header at once, *⚠ ger rebase --continue
  did not run: …*, and nothing is under way. Then the header says how the step ended, until the
  next action.
- **All chains** (top right of the header) brings the lists back for the rest of the task; there
  it reads **Only this task**. Something else that needs you stays on the status bar item, which
  opens it, the lists coming back when it is in one of them.

In a narrow side bar (under 240 px; the gallery draws the task scenarios at 170 px too) the header
keeps the mode, the next step and the way out on screen, with nothing wider than the view: a step
is one line, its states as icons (their words in the tooltip, and read out by a screen reader); a
chain longer than five folds to the steps around the current one, the others one line each before
and after (*⋯ 2 replayed*, *⋯ 3 to come*; *2 earlier*, *3 later* while reviewing), which a click
opens; the buttons sit in one row, what each does in its tooltip, and a *not now* reason stays under
them. Wider, a button's words go beside it where they fit and under it where not.

It is not a view of its own: VS Code gives a view that appears the room the others leave, and
restores the Change view's stored height, so a separate header took two thirds of the side bar.

### Next: what it names, and when

The first row that holds names the button; `src/nextAction.ts` is this table, and
`test/next.test.ts` checks each row. *To answer* is an open chain somebody started on
Gerrit whose last published word is not yours and that no draft of yours answers.

| You are | # | When | Next |
|---|---|---|---|
| anywhere but your own commit | 0 | Gerrit could not be asked about the change shown (credentials refused, no answer, unreachable) | *Check credentials* (`ger setup`) or *Ask Gerrit again*: never the vote |
| reviewing (a staged review holds the change shown) | 1 | the change is abandoned or merged | *Next change*, or *Leave review* at the top of the chain (Vote and Publish is not offered: Gerrit takes nothing there) |
| | 1 | Gerrit has a newer patchset than the one checked out | *Update to patchset N* (N as `/show` says it, even when the patchsets asked before are older) |
| | 2 | a chain to answer you have not gone to (its title: *N of M to answer not gone to yet*) | *Next comment* |
| | 3 | files of the comparison shown are not seen, and its combined diff is the editor in front (where they were read) | *Mark N files seen* (all of them, one request) |
| | 4 | a file of the comparison shown is not seen (its tick: shown to its end, or clicked) | *Next file* (the first not seen) |
| | 5 | drafts of yours wait, or Gerrit's attention set holds you (or ger cannot tell) | *Vote and publish* |
| | 6 | the chain goes on above the change (or ger cannot tell) | *Next change* |
| | 7 | the top of the chain | *Leave review* |
| on your own commit, no staged review | 1 | a rebase stopped on a conflict, files still conflicted | *Resolve src/routes.ts*: the first file still conflicted, opened with its markers; once it is resolved and staged (`git add`, or **+** in Source Control: the extension does not stage), the next |
| | 2 | stopped on a conflict, none left | *Continue* |
| | 3 | a chain left to answer here, gone to or not (a reply typed into its box but not saved does not answer it) | *Next comment* |
| | 4 | a commit above the stop needs edits, or (no rebase) any commit does | *Work on next* |
| | 5 | a rebase underway, nothing above the stop needs edits | *Continue*: the task header's own button, by its name (one action, one name) |
| | – | at an edit stop, a file changed and not amended (core's `uncommitted`) | none: Continue would refuse; the stop lists it under *Changed, not amended*, and amending is the terminal's (`git commit --amend -a`) until issue 132 |
| | 6 | something to send (new code, a commit not on Gerrit, drafts of yours) and the push is not refused | *Push* |
| looking at somebody's change on Gerrit | 1 | it is under review in the review worktree's own window | *Go to Review Window* |
| | 2 | otherwise | *Review change* (its title says when uncommitted changes here would stand in the way: core's `review_blocked`) |

Your own published comments waiting on the author never make *Next comment*: a change you voted
on that waits on its author offers *Next change*.

**One primary button, and it is the next step.** A screen draws at most one filled (primary)
button, and it is the one this table names: Next. Where the next step is a button the screen
already has (*Continue* in the task header, *Update to patchset N* under the change's state), that
button is the primary one and Next is not drawn a second time; every other button is drawn plain,
its alternatives listed beside it in words. While the commit message has edits not applied, *Apply*
is the next step and Next is drawn plain until they are applied or reverted. Local stack's banner
draws *Continue* primary wherever core allows it. `src/changeScreen.ts` puts the Change view
together by this rule; `test/screen.test.ts` draws every gallery scenario and fails on a view with
two primary buttons.

## Settings

| Setting | Default | |
|---------|---------|--|
| `ger.executable` | `ger` | How to run ger: a program on the `PATH`, a path to it, or a command line such as `uv run ger` (quote a path with blanks). |
| `ger.agentCommand` | empty | Terminal command that starts your agent; `{brief}` is the runbook file, `{rev}` the commit, e.g. `claude "Follow the runbook in {brief}"`. Empty: the runbook is copied to the clipboard. |
| `ger.pollSeconds` | `120` | Background refresh from Gerrit while the window has focus; a change applies at once. It also refreshes when the window regains focus. While Gerrit's event stream is live (below) it slows to every 15 minutes (or this, when longer), a safety net. |
| `ger.notify` | `true` | Notify when a background refresh finds something new to be the most important change. Not for what you just did: the status bar shows that. |
| `ger.closeDiffsOnRebase` | `false` | Close a diff tab opened from the Change view once it is stale: its commit left the stack (rewritten by a rebase, in the views or in a terminal, an amend or a reorder; dropped; or merged past the upstream), or, with your file on its right, HEAD moved off it. Off: the tab stays open and its title says *stale* (`b.txt (6827cbfe) — stale`). A tab that may hold unsaved edits is never closed. |
| `ger.trace` | empty | A file to append one JSON line per user action to: how fast the views answered it ([Tracing](#tracing)). Empty: off. `GER_TRACE` in the editor's environment does the same and wins. |

**Old setting names.** When the repository still uses git config keys under their old names
(`gerrit.project` for `ger.project`), every ger command warns once, and so does the extension,
once per session, with **Run ger config --migrate**, which renames them all in the *ger* terminal.

**Gerrit's event stream.** When `ger serve` can follow Gerrit's event stream (`ssh … gerrit
**Your repository.** The views follow it as the built-in Git view does: a commit, a checkout, a
`ger edit` and its `git rebase --continue` (anything that moves `HEAD`, a branch or a rebase) or a fetch that moves the upstream, refresh *Local stack*,
the *in your stack* markers and the Change view's buttons about a second after the last change, also when you
work in the integrated terminal. This does not make Gerrit refetch anything (answers still fresh are reused), and says nothing
in a notification, as you did it yourself. While a rebase is stopped or a review is checked out here, the work tree is followed
too: a file you save, or one changed elsewhere (a terminal, an agent), and the index (`git add`) bring
the stop's *changed, not amended* and *staged* files, and Continue's words, within a moment, one
`GET /stack` for each burst of changes (VS Code's own save and file events; git is not polled). A
refused Continue or Work on Next reads the stack again too, so the buttons under the refusal agree with it.

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
| This repository is not set up for Gerrit yet (no `ger.webUrl`) | **Run ger setup** (in a terminal), then **Retry** |
| ger refuses `ger.webUrl`: it is a plain http:// address | the https:// address (**Run ger setup**), or `git config ger.allowInsecureHttp true` for a test Gerrit; then **Retry** |
| ger cannot ask Gerrit yet: a setting is missing or wrong | **Run ger setup**, then **Retry** |
| ger and this extension do not speak the same version of the `ger serve` API (the line above says which versions, and which to update) | **Install the Matching ger** (the release with the extension's version), then **Retry**; when ger is the newer one, updating the extension fixes it too |
| `ger serve` stopped or failed | **Retry**, or **Show Log** for why |

**Retry** says what it found when it still cannot start: a notification with ger's reason.

While a setting is wanting, *Local stack* and *Change* say ger's own words for it, with **Run ger setup**.
Otherwise *Local stack* and *Change* say the same in a line and point to *Review chains*; their buttons in the view title bars appear once ger is running. For an API version they cannot use they say which versions instead, and offer **Update ger** (or, when ger is newer, **Install Matching ger**) themselves.

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
npm run gallery     # the two webview views in a browser page: every layout state, dark and light, three widths
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

A line that says what state things are in or what went wrong (a view's status headline, Local
stack's banner and its other lines, why CI has nothing to read, why a log could not be read) is
drawn whole: it wraps in a narrow side bar and is never cut off. Ordinary rows (a subject, a file
name, a comment's first line) stay one line and may end in "…". A section's counts stay beside its
title; when both do not fit, the counts move under it, at the right.

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
`media/` as they ship, each in an iframe 200, 300 and 450 pixels wide (the task scenarios 170 too), in VS Code's Dark Modern and Light
Modern colours. What stands in for VS Code is in `test/gallery/`: `theme.ts` has the `--vscode-*`
variables and a webview's default styles (a variable the pages use and it does not list fails
`npm test`), and `frame.js` is `acquireVsCodeApi()`: it answers the page's `ready` with the view
model, and what the page posts (a click, a drop, the context a right-click would hand to the menu)
is listed under the frame.

- The scenarios (`test/gallery/scenarios.ts`) call `stackVm()` and `changeVm()` on what `ger serve`
  would answer, built from the fixtures the unit tests use (`test/fixtures.ts`); the task scenarios
  put the Change view together as the window does (`changeScreen()` with `nextAction()`), so Next and
  the one primary button are what a user sees. A new state of a view gets a scenario there.
- `test/contrast.test.ts` measures every state colour of the pages without a browser: the `--ger-*`
  tokens of `views.css` resolved per theme kind with `theme.ts`'s values of Dark and Light Modern
  and both high-contrast themes, against the side bar, a hovered and a selected row (WCAG: 4.5:1
  for words, 3:1 for an icon that carries a state and for the focus ring). A rule that colours a
  state without a token fails it.
- `npm run gallery -- report.json` adds snapshot files as scenarios, in front of the built-in
  ones. A snapshot is `{ "format": 1, "name": "…", "stack": <StackVm or null>, "change": <ChangeVm or null> }`,
  other keys ignored (`src/snapshot.ts`). The report of **ger: Report Issue…** is one: this is how a
  reported layout is looked at.
- `npm run gallery -- --png <dir>` also writes a picture of each frame,
  `<scenario>.<view>.<theme>.<width>.png`, at twice the size. It runs an installed Edge, Chrome or
  Chromium headless (`GER_GALLERY_BROWSER` names another one), once per picture: all of them take
  a few minutes, so `--only <words>` keeps the scenarios with those words in their name (several: `--only "conflict|ci findings"`). This is
  how a coding agent looks at a layout change it made. It also lists every frame that would scroll
  sideways, by how much and the first element that sticks out: a side bar never should.

Open the `vscode` folder in VS Code and press `F5` to start an Extension Development Host;
open your Gerrit repository in it. `npm run package` builds a `.vsix` to install elsewhere; a release (`python scripts/build_release.py` in the repository root) builds it into ger, for `ger vscode install`.

**ger: Show Log** shows which repository is used, how `ger serve` was started, and anything it
printed.

## Tracing

To see how fast the extension answers, set `ger.trace` to a file (or start VS Code with
`GER_TRACE=<file>`). Each line of it is one JSON record; off, nothing is measured. Changing the
setting applies at once to commands, clicks and keys in the two webview views, and requests;
the Review chains tree and the prompts are measured from the next start of the window.

- **`action`**: one per user action, a command (a key binding, a menu, a tree row) or a click or
  key the page of a webview view sends (`stack.command ger.openChange`, `change.thread`). `t` is
  when it began (ms since the epoch; for a page, when the page saw it), `first_ms` how long until
  something showed in answer and `first` what: `paint:stack` / `paint:change` (the page painted the
  new state: it says so after the next frame), `editor:…`, `cursor:…`, `tab`, `terminal:…`,
  `tree` (VS Code asked for the Review chains rows), `ui:pick` / `ui:status` / `ui:progress` / …
  (a prompt, a status message, a progress). `settled_ms` is when the last of it was done: no
  request in flight and nothing waiting to be painted for 250 ms. With it, the totals of its
  requests: `requests`, `request_ms`, `serve_ms`, `git` / `git_ms` (git subprocesses `ger serve`
  ran), `gerrit` / `gerrit_ms` (Gerrit requests), `paints`. `inner` lists the commands it ran
  (a webview button runs one). An action that waits on you (a picker, a dialog) settles while it
  waits; your answer starts the next record, `<name> › pick` (`via: "answer"`). One still busy
  after a minute is written with `timeout: true`.
- **`request`**: one per request to `ger serve`: `action` (the action's `id`, null for a
  background refresh), `rid`, `method`, `path`, `status`, `ms` as the extension saw it.
- **`serve`**: what `ger serve` said that request cost, joined by `rid` and `action`: its
  `X-Ger-Trace` ([serve.md](../docu/spec/commands/serve.md#tracing-a-request)): run and queue
  time, git and Gerrit calls, the slowest of them.
- **`cli`**: a ger command run in a terminal (Work on Next's `ger edit -n`, `ger push`) while
  `GER_TRACE` is set in its environment, as it is in the integrated terminal when VS Code was
  started with it: `argv`, `exit`, `ms` and its git and Gerrit calls, the same shape
  ([SPEC.md](../docu/SPEC.md)). It has no action id: it joins by time.

**The walk and its scorecard.** `npm run walk` drives a real VS Code through the review loop
and the rework loop on a copy of the playground checkout, with clicks and keys as a user gives
them and tracing on, and writes the trace, a picture per step and a scorecard (`scorecard.md`,
`scorecard.json`): per step the user actions (clicks, keys, typed texts), the context switches,
the time to the first response and to settled, the requests and subprocesses, a Windows
estimate, the totals per loop and the steps over budget. See
[test/walk/README.md](test/walk/README.md).

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
| `src/comments.ts` | Editor comment threads, stepping, draft replies, Suggest Edit and Apply. |
| `src/suggestions.ts` | Suggested edits as a thread shows them (old → new), and where Apply writes one, or why not. Pure, unit-tested. |
| `src/statusBar.ts` | The status bar item: the headline (a rebase underway, when core says so) and the activity bell. |
| `src/shownChange.ts`, `src/follow.ts` | Which change the Change view shows and why, the one owner of it: the change the review here holds (however the review moved: the views, a shell, a restart), the commit a rebase stopped at, your selection followed through rewrites and amends, said when it left the stack and shown again when it is back (the rule pure, unit-tested); applying it on every change of the views' state. |
| `src/diffTabs.ts`, `src/staleDiffs.ts` | `ger-file:` documents and the diff tabs of them: opening one, knowing it again, marking it stale or closing it once its right side is no longer the commit its title names, unmarking it once it is again (the rule is pure, unit-tested); your files' repository paths; saving before a rebase continues. |
| `src/compareRows.ts` | What the Change view compares: the From and To pickers, the default mode and its toggle, what the files compared are called. Pure, unit-tested. |
| `src/problemRows.ts` | An error state of the two webview views: a sentence in ger's words, git's or Gerrit's under it, the options as buttons. Pure, unit-tested. |
| `src/changeLoad.ts` | Which change the Change view shows and what of it has come: drawn at once from the clicked row, filled in answer by answer, a stale answer dropped. Pure, unit-tested. |
| `src/review.ts` | Review Change, Check Out to Work On, Leave Review Checkout, Edit Commit Here; moving a review along its chain. |
| `src/reviewWalk.ts`, `src/reviewAhead.ts` | The review walk as the views do it: the change under review shown with all its files; the next one asked ahead and drawn from it before the checkout, its diff from git, then your files. |
| `src/task.ts`, `src/taskRows.ts` | The task you are in (a review, a rework): its header (pure, unit-tested), and the context keys the lists step aside by. |
| `src/changeScreen.ts` | The Change view as a screen: the change, the task header and Next put together, one primary button that is the next step. Pure, unit-tested. |
| `src/nextAction.ts` | Next: the one thing to do now on the change shown. Pure, the rules are the table above, unit-tested row by row. |
| `src/stackSummary.ts` | Local stack at a glance: `ger log`'s summary line, and the row the ready boundary is drawn under. Pure, unit-tested. |
| `src/ciDiagnostics.ts`, `src/ciProblems.ts` | The CI findings of the change shown as editor problems: what each says (pure, unit-tested), and the diagnostic collection that holds them while the change is shown. |
| `src/commentState.ts` | A change's comments at a glance: the state, the open count, the share for the ring, the words. Pure, unit-tested. |
| `src/walk.ts` | Work on Next: the attention walk (`ger edit -n`) from the views, and what follows once it has stopped or the rebase is finished. |
| `src/vote.ts`, `src/voteText.ts` | Vote and Publish, Publish Drafts: the questions, around `GET /vote` and `POST /vote`; their words (pure, unit-tested). |
| `src/upstream.ts` | Giving the branch an upstream. |
| `src/agent.ts` | Handing a runbook to a coding agent. |
| `src/config.ts`, `src/launch.ts`, `src/commandLine.ts` | The `ger.*` settings; how ger is started, as a process or in a terminal; installing ger, and the warning when its version is not the extension's. |
| `src/workspace.ts`, `src/state.ts` | Which checkout is served; the state the views' welcome texts follow (`ger.state`). |
| `src/ui.ts` | Everything said to the user or done outside the views; the e2e suite scripts it. |
| `src/poller.ts` | Background refresh from Gerrit: on a timer while focused, and on regaining focus. |
| `src/gitWatch.ts`, `src/repoWatcher.ts` | The views following local git: what to watch (pure, unit-tested), the debounce, the watcher. |
| `src/extension.ts` | Wiring: views and commands. |
| `src/trace.ts`, `src/tracing.ts` | Tracing (`ger.trace`): the record of each user action and the requests it caused (no `vscode` import, unit-tested); switching it on and hooking it to what VS Code shows. |
| `src/snapshot.ts` | The snapshot file: the view models of the two webview views at one moment. Read by the gallery. No `vscode` import, unit-tested. |
| `src/anonymize.ts`, `src/fakes.ts` | What becomes of every field of the view models in a report: kept, or replaced (default-deny); and the made-up words, names, paths and ids that replace. No `vscode` import, unit-tested. |
| `src/report.ts` | The report of an issue: the anonymized views and the versions, as a snapshot file; the link to a new issue. No `vscode` import, unit-tested. |
| `src/reportIssue.ts` | ger: Report Issue…: shows the report, then copies it for a GitHub issue or saves it. |
| `test/fixtures.ts` | API-shaped commits, diffs, comment chains and CI logs: shared by the unit tests and the gallery's scenarios. |
| `test/walk/` | The walk (`npm run walk`): the review and rework loops in a real VS Code against the playground Gerrit, real input through the DevTools protocol, and the scorecard of its trace. Not in the `.vsix`. |
| `test/gallery/` | The webview gallery (`npm run gallery`): scenarios, the stand-in for VS Code (theme, `acquireVsCodeApi()`), the page around the frames, pictures. Not in the `.vsix`. |

When the API grows a field, add it to `src/api.ts` first: the types there are the extension's
copy of the contract.
