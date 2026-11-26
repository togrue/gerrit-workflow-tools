# The walk and its scorecard

`npm run walk` measures what a user's two loops cost, so one iteration of the extension can be
compared with the next: how many clicks and keys, how many times the eyes and hands move to
another view or tool, how long until something answers and until it settles, and how many
requests and subprocesses that took (the subprocesses are what make it slow on Windows).

```bash
cd vscode
npm run walk                                   # ~/ger-playground/repo, out/walk
GER_WALK_OUT=~/ger-playground/experimental-shots/12-scorecard npm run walk
```

It needs the playground (`~/ger-playground`: a Gerrit with the project `ger-playground`, its
chains and a clone configured as `pg-you`; see its README) and a VS Code
(`npm run e2e -- --get-vscode` gets one). It runs `ger` from this checkout (`uv run`).

## What it does

`run.ts` copies the checkout (`GER_WALK_REPO`, default `~/ger-playground/repo`; the copy is
the only one changed), checks out the chain of `GER_WALK_REWORK` (default `2178`) on the branch
`walk-rework`, and starts VS Code on it with
`GER_TRACE` set, so the extension, `ger serve` and every ger command in the terminal write one
trace. `suite.ts` then walks:

The walk takes the path the views lead a user along: wherever the Change view's **Next** button
names the step the walk is on, it presses Alt+Enter (and says so: `key alt+enter (Next: "…")`);
where Next names something else, the step is given the other way below, and a note says what
Next named. Each step's record keeps what Next named before and after it.

- **Review loop**: open the review queue (the Gerrit icon), open a chain (its row in To
  review; `GER_WALK_REVIEW` picks another), read the commit message (the bottom change's row),
  Review Change (the row's inline button; it opens all the files in one editor and the task
  header), see every file (Next while it names *Next file* or *Mark N files seen*; when two
  presses change nothing, the files' ticks in the Change view are clicked, as a user would;
  Open All Changes when Next names neither), go to a comment (Next comment, else the comment's
  row, or Alt+F9), write a draft comment (a click on the line, Ctrl+Alt+C, the text, Ctrl+Enter),
  Vote and Publish (Next, else the title button; +1, the message, Publish), next change (Next,
  else the notification's Next Change). Then, not measured, Leave Review.
- **Rework loop**: Work on Next (Local stack's button; `ger edit` runs in the terminal), the
  next comment twice (Next comment, else Alt+F9), apply the suggested edit (the Change view's
  list is an index: its row opens the thread in the editor, then Apply in the thread's title),
  Reply Done, save the file (its tab, Ctrl+S), the next stop (Next's *Work on next*,
  else Local stack's button; it refuses while the fix is saved and not amended, and offers no
  way on, so the walk does what a user must: Ctrl+` to the terminal, `git commit -a --amend
  --no-edit`, Enter, Work on Next again), and the push preview (Continue in the task header,
  which finishes the rebase in the terminal, then Push to Gerrit…; `ger push` shows what it would
  push; without the header, Push, its question's Continue, and Push again). Then, not measured,
  the push is answered with Ctrl+C and the rebase aborted.

Every step is given as a user gives it: clicks and keys through the window's DevTools protocol
(`cdp.ts`), into the workbench and into the frames of the webview views. When a step cannot find
what to click (a button renamed, a row not drawn), it runs the command instead, and the
scorecard says so (`input: command`, with the reason). A step's outcome is checked (the Change
view shows the change, the editor moved, the draft exists), and a step whose outcome does not
show in time is listed as over budget with the reason.

## What it writes

In `GER_WALK_OUT` (default `out/walk`):

- `trace.jsonl`: the trace ([Tracing](../../README.md#tracing)), with the walk's own records:
  `step-start`, `step` (its input, where it took the user, when its outcome showed), `note`.
- `steps/<n>-<loop>-<step>.png`: the window after each step, and `steps/notes.txt`.
- `tracebacks.txt`: every Python traceback from ger the run left (ger serve's stderr in the
  `ger` output channel's log, the terminal's text), whole, with its file; `none` when there was
  none.
- `scorecard.md`, `scorecard.json`: per step the user actions (clicks, keys, typed texts),
  context switches, time to the first response and to settled, requests, subprocesses (git run
  by `ger serve` and by ger commands in the terminal), Gerrit requests, and the Windows
  estimate; totals per loop; the steps over budget; the five slowest steps with their slowest
  requests and calls.

**Budgets.** A step within loaded data (*walk*: next comment, next file) shows something within
100 ms. A step that changes the change (*change*) settles within 1 s. A slower one (*slow*: the
rebase in the terminal, publishing) shows progress within 100 ms. **Windows** is estimated:
settled time plus each subprocess × `GER_WALK_PROCESS_MS` (default 80 ms), since process start
is what is slow there and the walk runs on Linux.

**Context switches** are counted from where each step takes the user (`where`: the Review
chains tree, the Change view, the editor, a picker, a dialog, a notification, the terminal):
each change of place is one, from where the step before left off.

## Comparing runs

```bash
node out/test/walk/compare.js "baseline=<dir>" "new UI=<dir>" "after fixes=<dir>" > comparison.md
```

puts scorecards side by side: per loop the inputs, context switches, first response, settled,
requests, git processes, Gerrit requests and steps over budget, each with its difference from the
first run; per step the same, matched by position, so a step the UI now leads to another way
lines up with the one it replaced (both titles are shown).

## Repeating it

The walk acts on Gerrit as the user of the checkout (`pg-you`): it votes +1 on the reviewed
change and publishes its draft comment there, and Reply Done saves drafts. Afterwards
`gerritReset.ts` undoes what it can, so the next run starts where this one did: the vote is
removed, the change put back in the user's attention set, drafts that did not exist before
the run are deleted (a draft that existed and was only changed stays), and Gerrit's reviewed flags
the walk set on the chain's files (a tick, Mark seen) are taken off again by the suite, which
read them before the first step. The published comment
cannot be deleted (only an administrator can): it is resolved with a reply, so the change does
not wait on its author, and each run leaves one resolved thread there. `GER_WALK_RESET=0` leaves
everything as the walk left it. Compare scorecards of runs against the same playground state.

The window is set up, not measured: the side bar widened, the Explorer in front, and
`ger.reviewCheckout` set to `branch` in the copy (a review worktree opens in a window of its
own, which the walk cannot follow). VS Code answers every modal dialog as cancelled while it
runs extension tests, and the walk is one: a modal question (Publish?, Finish the rebase first)
is asked as a notification with the same text and buttons instead, and its button clicked there:
the same one click.
