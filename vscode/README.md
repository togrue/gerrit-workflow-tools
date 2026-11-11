# ger for VS Code

Your Gerrit review chains, your local stack, comments, diffs and CI findings — next to the code.

The extension has no Gerrit logic of its own. It starts `ger serve` in the git checkout of your workspace folder and
shows what that says, so it always agrees with `ger log`, `ger show` and `ger inbox`. The API
contract is [docu/spec/commands/serve.md](../docu/spec/commands/serve.md).

## What you get

**Status bar** — the *headline*: the one change that most wants you (failed CI on your chain,
then unresolved comments, then a negative vote, else the review that has waited longest). A bell
with a count appears when chains had activity since you last looked. Click to open the change.

**Review chains** (activity bar → Gerrit)

- *Outgoing*: your open chains. *To review*: chains waiting on you.
- One row per chain, showing its top change, worst votes, unresolved comments and age. Expand
  it for the chain's changes. A `●` marks new activity.
- Changes that are commits of your local stack are marked `local` and open as local commits.

**Local stack**

- The commits above your upstream, newest first, each with patchset state, votes, unresolved
  comments, and whether `ger push` would hold it back.
- **Reorder by drag and drop**, or with the arrows on a row. A dropped commit takes the place of
  the one you drop it on, up or down; drop it under the list to make it the oldest. All or nothing: if a commit does
  not apply in its new place, nothing is touched and you are told which commit and which files.
- **Conflict preview**: where two neighbours cannot swap places, the arrow is replaced by a
  blocked marker; click it (or hover the row) to see which commit would conflict in which files.
- **Squash into the commit below** (keeping either message — never both, so there is always
  exactly one Change-Id) and **drop**, from a commit's context menu. Every rewrite offers
  **Undo** right after.
- Push (`ger push` in a new terminal of its own, where it can ask you).
- **No upstream** (typical after checking a change out from Gerrit's download commands): the
  view says so and offers **Track origin/…** in one click — the branch it was created from, else
  the remote branch nearest to it — or **Choose another upstream branch…**. Later changes:
  *Set Upstream Branch…* in the view's `…` menu.

**Change** — select any change or commit

- What it needs, in words, with its votes.
- **Since your review**: on someone else's change you already reviewed, the files that changed
  between the patchset you voted or commented on and the current one — click for that diff.
- **Files**: click for a diff against the parent. The commit you are standing on diffs against
  the real file, so you can fix things in the diff editor.
- **Comments**: unresolved first. Click to jump to the line; `Alt+F9` / `Shift+Alt+F9` step
  through the unresolved ones. For a local commit they also appear as comment threads in the
  editor. Reply, or reply "Done" and resolve — saved as **drafts** on Gerrit, published later by
  `ger push` or `ger review --vote`.
- **New comments**: hover a line (or select lines) in the change's diff — or in your file, when
  the shown commit is HEAD — and click **+**. Save it as:
  - **Draft**, when Gerrit has this very commit: on Gerrit, visible only to you until published;
  - **Local draft**, when Gerrit does not (never pushed, or amended since): kept here, in
    `refs/ger/comments`, for when the commit is pushed;
  - **Private note**, always: kept here and never uploaded.
- **CI**: opened on demand (it downloads build logs). Findings this change **introduced** are
  on top with a flame; findings that already fail on its parent are dimmed. Click a finding to
  jump to its file and line.
- **Check Out for Review** (pull-request icon, changes on Gerrit): `ger review` fetches the
  change into the review worktree (or a `change-<n>` branch here, when the git config key
  `ger.reviewCheckout` says `branch`) and offers to open that worktree in a new window. Your
  own stack is not touched.
- **Edit Commit Here** (pencil icon, your local stack commits): `ger edit` runs in this checkout,
  in a new terminal: an interactive rebase that stops at the commit, so you amend it in place and
  `git rebase --continue`. No worktree, nothing fetched.
- **Hand to agent**: for one commit, the same runbook as below, narrowed to it.

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
| `ger.pollSeconds` | `120` | Background refresh while the window has focus; a change applies at once. It also refreshes when the window regains focus. While Gerrit's event stream is live (below) it slows to every 15 minutes (or this, when longer), a safety net. |
| `ger.notify` | `true` | Notify when a background refresh finds something new to be the most important change. Not for what you just did: the status bar shows that. |

**Gerrit's event stream.** When `ger serve` can follow Gerrit's event stream (`ssh … gerrit
stream-events`: it needs ssh, a key Gerrit accepts and the *Stream Events* capability), the
views refresh a second after review activity happens — a new patchset, comment, vote, reviewer,
merge — instead of at the next poll, and the poll drops to a 15-minute safety net. When it
cannot (HTTP only, no key, no capability, the connection dropped), nothing changes: the views
poll every `ger.pollSeconds` as before, and no notification says so. The status bar tooltip
says which of the two is keeping the views current, and why; the log (**Show Log**) has one line
per change. Configure it with the git keys `ger.eventStream` (`auto`, `on`, `off`) and
`ger.eventStreamHost` / `Port` / `User`:
[docu/Configuration.md](../docu/Configuration.md#ger-serve--gereventstream).

These are VS Code settings. How ger itself behaves (`ger.reviewCheckout`, `gerrit.webUrl` and
the rest) is git configuration, the same for the extension and the command line: see
[docu/Configuration.md](../docu/Configuration.md).

**Workspace trust.** In Restricted Mode the extension starts nothing: ger runs git and the
program in `ger.executable` in the repository, and a repository you do not trust could make
either run its own code. `ger.executable` and `ger.agentCommand` are read only from your user
settings there, never from the repository's `.vscode/settings.json`. Trust the folder and the
views fill.

## Requirements

`ger` on the `PATH` (or `ger.executable` set), and a repository where `ger log` works:
`gerrit.webUrl`, credentials (`ger setup`) and an upstream for the branch.

The folder can be the checkout, a folder inside it, or a worktree. With several workspace
folders, the first one in a git checkout is used; the log and the status bar tooltip name it.

When something is missing, the *Review chains* view says what and offers the next step:

| The view says | Next step |
|---------------|-----------|
| Open a folder with a Gerrit checkout / none of the folders is in a git checkout | **Open Folder** |
| The program that runs ger was not found | **Open Settings** on `ger.executable`, then **Retry** |
| This repository is not set up for Gerrit yet | **Run ger setup** (in a terminal), then **Retry** |
| `ger serve` stopped or failed | **Retry**, or **Show Log** for why |

*Local stack* and *Change* say the same in a line and point to *Review chains*; their buttons in the view title bars appear once ger is running.

A branch without an upstream still shows your review chains; *Local stack* offers to track one.

## Build and run

```bash
cd vscode
npm install
npm run compile     # or: npm run watch
npm test            # unit tests of the pure parts (formatting, activity, reorder math)
npm run e2e         # the extension inside a real VS Code, against a fixture repo and a fake Gerrit
```

`npm run e2e` starts your installed VS Code (or `VSCODE_EXE`) with a throwaway profile, opens
a generated four-commit repository, and has the extension start the real `ger serve` over the
test suite's fake Gerrit (`test/e2e/fake_ger.py`). Your git config and ger's cache and config
are kept out. Needs `uv`.

The suite presses every button in turn. Everything the extension says or does outside its
views (notifications, dialogs, pickers, input boxes, the status bar, terminals, the clipboard,
the browser) goes through one seam, `src/ui.ts`, which the suite replaces with a scripted user
(`test/e2e/scriptedUi.ts`) that answers each prompt and writes it down. For each press,
`test/e2e/transcript.golden.md` holds what a user expects, what the extension said and did,
and how the views changed. An **Expect** line says what a user would expect from that press;
where one behaviour is clearly what users expect, the extension does that, and only open
design questions go to a decision. A run whose transcript differs fails and prints the diff: judge each
changed step against its **Expect** line, then accept it with `npm run e2e -- --update`. Set
`GER_E2E_SCREENS=<file>` to also get the full screen before and after every press.

Open the `vscode` folder in VS Code and press `F5` to start an Extension Development Host;
open your Gerrit repository in it. `npm run package` builds a `.vsix` to install elsewhere.

**ger: Show Log** shows which repository is used, how `ger serve` was started, and anything it
printed.

## Layout

| File | Role |
|------|------|
| `src/api.ts` | The API types and the HTTP client. No `vscode` import. |
| `src/server.ts` | Starts and stops `ger serve`. No `vscode` import. |
| `src/format.ts` | Words, ages, urgency, activity tracking. Pure, unit-tested. |
| `src/model.ts` | The shared state the views read; refresh; what you have seen. |
| `src/overviewView.ts`, `src/stackView.ts`, `src/changeView.ts` | The three views. |
| `src/comments.ts` | Editor comment threads, stepping, draft replies. |
| `src/statusBar.ts` | The status bar item: the headline and the activity bell. |
| `src/follow.ts` | The Change view following its commit through rewrites and amends. |
| `src/review.ts` | Check Out for Review, Leave Review Checkout, Edit Commit Here. |
| `src/upstream.ts` | Giving the branch an upstream. |
| `src/agent.ts` | Handing a runbook to a coding agent. |
| `src/config.ts`, `src/launch.ts`, `src/commandLine.ts` | The `ger.*` settings; how ger is started, as a process or in a terminal. |
| `src/workspace.ts`, `src/state.ts` | Which checkout is served; the state the views' welcome texts follow (`ger.state`). |
| `src/ui.ts` | Everything said to the user or done outside the views; the e2e suite scripts it. |
| `src/poller.ts` | Background refresh: on a timer while focused, and on regaining focus. |
| `src/extension.ts` | Wiring: views and commands. |

When the API grows a field, add it to `src/api.ts` first: the types there are the extension's
copy of the contract.
