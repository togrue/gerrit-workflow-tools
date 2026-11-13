# ger for VS Code

Your Gerrit review chains, your local stack, comments, diffs and CI findings — next to the code.

The extension has no Gerrit logic of its own. It starts `ger serve` in the git checkout of your workspace folder and
shows what that says, so it always agrees with `ger log`, `ger show` and `ger inbox`. The API
contract is [docu/spec/commands/serve.md](../docu/spec/commands/serve.md).

## What you get

In the editor, places are called *Gerrit* (the activity bar, the status bar item); commands are listed in the palette under `ger:`, because they run the `ger` tool.

**Status bar** — the *headline*: the one change that most wants you (failed CI on your chain,
then unresolved comments, then a negative vote, else the review that has waited longest). A bell
with a count appears when chains had activity since you last looked. Click to open the change.
When a refresh fails, whether it ran in the background or you pressed *Refresh* (Gerrit unreachable, `ger serve` stopped
and cannot be restarted), the views keep the last answer and the item carries a warning mark; its tooltip says why
it is out of date, and *Review chains* is labelled so. The next refresh that works clears the mark. While `ger` cannot run at all (not found, not set up, folder not trusted) the
views clear and say why, and the timer stops trying: it would only start `ger serve` to fail again, once per poll.
The log says so once; regaining focus, *Retry* and a changed `ger.executable` still try.

**Review chains** (activity bar → Gerrit)

- *Outgoing*: your open chains. *To review*: chains waiting on you.
- One row per chain, showing its top change, worst votes, unresolved comments and age. Expand
  it for the chain's changes. A `●` marks new activity.
- Changes that are commits of your local stack are marked *in your stack* and open as local commits.
- **Sort Chains…** (title bar): *By chain state* — the order `ger inbox` gives, what needs you
  first — or *By last activity*, newest or oldest first. It applies to both sections and is
  remembered per workspace; the sections name an order other than by state.

**Local stack**

The commits above your upstream, newest first. One row per commit:

- A **cloud** in front says how the commit relates to Gerrit, by colour: the text colour when it
  is the same as on Gerrit, **yellow** when you changed it since the last push, **red** when
  Gerrit has a newer patchset than this commit (outdated), **blue** when both happened
  (diverged: you changed it, and Gerrit's current patchset came from somewhere else). A dimmed
  upload cloud is a commit Gerrit does not have yet; a check, one that is merged.
- On the right, as icons: **unresolved comments** (with their number), the **review vote** and the
  **CI vote**. Dimmed, they are the last pushed patchset's: the commit's code changed since, and
  Gerrit may not keep them. Hover any of them for the words.
- `HEAD`, the short commit id, and *held back* where `ger push` would stop at the ready boundary.
- Click a row, or walk the list with **↑ / ↓**: the *Change* view follows, with the commit
  message, while the keyboard stays in the list. `Enter` (or a double click) moves to the Change view.
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
  Change-Id), **Drop**, **Copy Change-Id** and **Copy Commit Hash** (of every selected row, one
  per line). Every rewrite offers **Undo** right after.
- **During a rebase** (a `ger edit`, a conflict) the view shows the branch as it was before it,
  with a **marker** on the commit the rebase stopped at; commits still to be replayed are
  **dimmed**. Stopped on a conflict, the marked commit is dimmed too: it is not applied yet.
  Nothing can be moved until the rebase is finished or aborted.
- **A merge commit** in the stack is one row, with a merge icon and marked *merge*, however
  many commits it brings in: those are not part of the stack (as on Gerrit) and are never read.
  `git rebase` cannot replay a merge, so the newest merge and the commits below it stay where
  they are — the view says so when you try to move, squash or drop one; `ger rebase` does it by
  hand. The commits above it reorder, squash and drop as usual. Rewording works everywhere.
- Push (`ger push` in a new terminal of its own, where it can ask you).
- **No upstream** (typical after checking a change out with Gerrit's download commands; `ger
  checkout` and *Check Out to Work On* set it): the view says so and offers **Track origin/…** in one click — the branch it was created from, else
  the remote branch nearest to it — or **Choose another upstream branch…**. Later changes:
  *Set Upstream Branch…* in the view's `…` menu.

**Change** — select any change or commit

- What it needs, in words, with its votes.
- **Commit message**: the whole message. For a commit of your local stack it is a text box:
  edit it and press **Apply** (`Ctrl+Enter`); *Revert* goes back to the commit's message. Only
  the message is rewritten — uncommitted changes stay where they are, nothing can conflict —
  and a message that lost its Change-Id footer keeps the commit's. **Undo** is offered right
  after. What you typed survives a refresh and switching views until you apply or revert it.
- **Show**: which version of the change you look at. It always starts on your **local commit**
  when the change is in your stack; the list also has every patchset on Gerrit, the current one
  and the one you reviewed marked.
- **Compare**: *Against its base commit* (what the change does), *Since my last review* (from
  the newest patchset you voted or commented on), or *Gerrit vs. local* (Gerrit's current
  patchset against your commit: what a push would add). On someone else's change you reviewed
  before, a line says what is new since, with **Show** to switch to it.
- **Files**: added and removed lines per file, in the colours git gives them. Click for the
  diff of what is compared. The commit you are standing on diffs against the real file, so you
  can fix things in the diff editor.
- **Comments**: unresolved first. Click to jump to the line; *Next / Previous Unresolved Comment* (the arrows in the view's title bar) step
  through the unresolved ones, across files. `Alt+F9` / `Shift+Alt+F9` do it too while the Change view or a
  diff of the change has focus; anywhere else they stay VS Code's own next / previous comment thread, which only
  walks the open file. For a local commit they also appear as comment threads in the
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
- **Review Change** (eye icon, changes that are not in your stack): `ger review`
  fetches the change into the review worktree and offers to open that worktree in a new window; your
  own stack is not touched. When the git config key `ger.reviewCheckout` says `branch`, it switches
  this checkout to a `change-<n>` branch instead, the message says so, and *Leave Review Checkout*
  switches back. For reading someone's work.
- **Check Out to Work On** (download icon, changes that are not in your stack): `ger checkout` in
  a new terminal puts the change on a `change-<n>` branch here that tracks its target branch, so
  the change is your local stack and *Push to Gerrit* uploads a new patchset. It may ask before it
  changes anything (update a branch you already have to the newest patchset?), which is why it runs
  in a terminal. The views follow by themselves once it has switched branches (see *Your repository* below).
  For changing a change of yours that exists only on Gerrit, or taking over a colleague's.
- **Edit Commit Here** (pencil icon, your local stack commits, also where they appear in *Review
  chains*): `ger edit` runs in this checkout, in a new terminal: an interactive rebase that stops
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

These are VS Code settings. How ger itself behaves (`ger.reviewCheckout`, `gerrit.webUrl` and
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
`gerrit.webUrl`, credentials (`ger setup`) and an upstream for the branch.

The folder can be the checkout, a folder inside it, or a worktree. The extension starts with the editor (after startup has finished), so the status bar item is there for a sub-folder of a checkout too; without a git checkout it starts nothing. With several workspace
folders, the first one in a git checkout is used; the log and the status bar tooltip name it.

When something is missing, the *Review chains* view says what and offers the next step:

| The view says | Next step |
|---------------|-----------|
| Open a folder with a Gerrit checkout / none of the folders is in a git checkout | **Open Folder** (a folder you add later starts ger without a reload) |
| The program that runs ger was not found | **Install ger** (in a terminal, with uv or pipx), then **Retry**; or **Open Settings** on `ger.executable` when it is installed somewhere else |
| This repository is not set up for Gerrit yet | **Run ger setup** (in a terminal), then **Retry** |
| `ger serve` stopped or failed | **Retry**, or **Show Log** for why |

*Local stack* and *Change* say the same in a line and point to *Review chains*; their buttons in the view title bars appear once ger is running.

A branch without an upstream still shows your review chains; *Local stack* offers to track one.

## Build and run

```bash
cd vscode
npm install
npm run compile     # or: npm run watch
npm test            # unit tests of the pure parts (formatting, activity, reorder math, the view models of the two webview views) and of package.json against the code (menus, row contexts, commands)
npm run e2e         # the extension inside a real VS Code, against a fixture repo and a fake Gerrit
```

`npm run e2e` starts your installed VS Code (or `VSCODE_EXE`) with a throwaway profile, opens
a generated four-commit repository, and has the extension start the real `ger serve` over the
test suite's fake Gerrit (`test/e2e/fake_ger.py`). Your git config and ger's cache and config
are kept out. Needs `uv`.

*Local stack* and *Change* are webview views: `src/stackRows.ts` and `src/changeRows.ts` build
what they show as plain data, the pages in `media/` (`stack.js`, `change.js`, `views.css`) only
draw it and send back what you did. They use VS Code's icon font, which `npm install` copies
from `@vscode/codicons` into `media/` (a webview cannot reach the editor's own copy). The font is
Microsoft's [Codicons](https://github.com/microsoft/vscode-codicons), licensed CC BY 4.0.

The suite presses every button in turn. For the two webview views it calls what their pages
send (a drop at a line, a button on a comment) and prints their view models as text; how the
pages look and feel is not covered by it. Everything the extension says or does outside its
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
open your Gerrit repository in it. `npm run package` builds a `.vsix` to install elsewhere; a release (`python scripts/build_release.py` in the repository root) builds it into ger, for `ger vscode install`.

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
| `src/rows.ts` | The `contextValue` of every tree row, which the menus in `package.json` match with `viewItem =~ /…/`. Pinned against them by `test/package.test.ts`. |
| `src/comments.ts` | Editor comment threads, stepping, draft replies. |
| `src/statusBar.ts` | The status bar item: the headline and the activity bell. |
| `src/follow.ts` | The Change view following its commit through rewrites and amends. |
| `src/review.ts` | Review Change, Check Out to Work On, Leave Review Checkout, Edit Commit Here. |
| `src/upstream.ts` | Giving the branch an upstream. |
| `src/agent.ts` | Handing a runbook to a coding agent. |
| `src/config.ts`, `src/launch.ts`, `src/commandLine.ts` | The `ger.*` settings; how ger is started, as a process or in a terminal. |
| `src/workspace.ts`, `src/state.ts` | Which checkout is served; the state the views' welcome texts follow (`ger.state`). |
| `src/ui.ts` | Everything said to the user or done outside the views; the e2e suite scripts it. |
| `src/poller.ts` | Background refresh from Gerrit: on a timer while focused, and on regaining focus. |
| `src/gitWatch.ts`, `src/repoWatcher.ts` | The views following local git: what to watch (pure, unit-tested), the debounce, the watcher. |
| `src/extension.ts` | Wiring: views and commands. |

When the API grows a field, add it to `src/api.ts` first: the types there are the extension's
copy of the contract.
