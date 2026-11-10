# ger for VS Code

Your Gerrit review chains, your local stack, comments, diffs and CI findings — next to the code.

The extension has no Gerrit logic of its own. It starts `ger serve` in the workspace folder and
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
- **Reorder by drag and drop**, or with the arrows on a row. All or nothing: if a commit does
  not apply in its new place, the branch is left exactly as it was and you are told which commit.
- Push (`ger push` in a terminal, where it can ask you).

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
- **CI**: opened on demand (it downloads build logs). Findings this change **introduced** are
  on top with a flame; findings that already fail on its parent are dimmed. Click a finding to
  jump to its file and line.
- **Check out**: someone else's change opens as a review checkout (`ger review`); your own
  commit starts `ger edit` on it.
- **Hand to agent**: collects the unresolved comments and CI findings as Markdown
  (`ger show --ai`). Copied to the clipboard, or passed to the command in `ger.agentCommand`.

## Settings

| Setting | Default | |
|---------|---------|--|
| `ger.executable` | `ger` | How to run ger, e.g. `uv run ger`. |
| `ger.pollSeconds` | `120` | Background refresh. Also refreshes when the window regains focus. |
| `ger.agentCommand` | empty | Terminal command for *Hand to agent*; `{rev}` and `{brief}` are filled in. |

## Requirements

`ger` on the `PATH` (or `ger.executable` set), and a repository where `ger log` works:
`gerrit.webUrl`, credentials (`ger setup`) and an upstream for the branch.

## Build and run

```bash
cd vscode
npm install
npm run compile     # or: npm run watch
npm test            # unit tests of the pure parts (formatting, activity, reorder math)
```

Open the `vscode` folder in VS Code and press `F5` to start an Extension Development Host;
open your Gerrit repository in it. `npm run package` builds a `.vsix` to install elsewhere.

If the views stay empty, run **ger: Show Log**: it shows how `ger serve` was started and
anything it printed.

## Layout

| File | Role |
|------|------|
| `src/api.ts` | The API types and the HTTP client. No `vscode` import. |
| `src/server.ts` | Starts and stops `ger serve`. No `vscode` import. |
| `src/format.ts` | Words, ages, urgency, activity tracking. Pure, unit-tested. |
| `src/model.ts` | The shared state the views read; refresh; what you have seen. |
| `src/overviewView.ts`, `src/stackView.ts`, `src/changeView.ts` | The three views. |
| `src/comments.ts` | Editor comment threads, stepping, draft replies. |
| `src/extension.ts` | Wiring: commands, status bar, polling. |

When the API grows a field, add it to `src/api.ts` first: the types there are the extension's
copy of the contract.
