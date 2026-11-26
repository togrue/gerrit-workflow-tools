# Extension vision

Where the VS Code extension should go next, and what must be true of core for it to get there.
Written 2026-11-24 from the open issues, the [target architecture](../architecture.md#target-architecture),
the [commit-chain roadmap](commit-chain-roadmap.md), a hands-on walk through one review and rework
cycle, and a look at what comparable tools do. It extrapolates one step past what the issues
already decide, and says where it does.

## The yardstick

From issue 137: *human effort is the expensive part: every saved click or minute counts. Every
switch between changes, files, views or tools costs more than the click itself.* A feature is
judged by the context switches it removes, not by what it adds.

## What it should feel like

**For an author.** The side bar is the work list and the editor is where the work happens. The
**local stack** says in icons what each commit needs and what the next push will cost. Editing a
commit, staging, rewording, continuing a rebase and pushing never leave the Gerrit views or the
editor. The terminal appears only for a command that asks a question no view can ask.

**For a reviewer.** A chain opens from *To review*, the diff shows what is new since the last
review, one key walks to the next thing that needs an answer, the answer is written in place, and
the review ends with a vote and the drafts published from the same place. The browser is not
needed to finish a review.

## Principles

1. **Count context switches.** Every action is reachable from where its state is shown.
2. **One key for the next thing that needs me**: across unresolved comment chains, CI findings
   and the members of a chain.
3. **The editor is where work happens; the side bar is a list.** Comments, diffs and the actions
   on them stay in one place.
4. **Icons at a glance, words in the tooltip.** Fewer, shorter details per row, and no line that
   only repeats another.
5. **Undo instead of confirm.** A dialog is kept for what leaves the machine: a push, a vote, a
   published comment.
6. **Say it before the button is pressed.** Every state a command would refuse is visible in the
   view first, in ger's own words.
7. **Every capability is served by core and has a command.** The extension holds no Gerrit logic;
   what it can do, `ger` can do, and the tooltip can name the command.
8. **Every action answers.** After a click the user sees what happened and what is next, as each
   command now ends with its outcome and a `next:` line.
9. **The agent prepares, the human decides and sends.**
10. **A limit shows only in the uncommon case.** What git, the editor or Gerrit cannot do sits
    behind one fallback, so the common case stays simple. When the user does hit a limit, they
    are told what happened, what state things are in now and what their options are. Nobody is
    left guessing the state.

## The questions every screen answers

The standard an extension change is reviewed against. Agreed on 2026-11-26.

1. Does the screen say which mode I am in and how to get out?
2. Is there exactly one obvious next step, with its name on it?
3. Can I see how much is left?
4. Is every state shown by more than colour?
5. Does every action answer within a moment, and say what happened?
6. Can I undo it, and if not, was I asked first?
7. If I am interrupted now, do I come back to the same place?
8. When a limit is hit, am I told the state and my options?

Three cautions that come with them:

- A mode hides what is outside it. One line stays for "something else needs you", without
  pulling the user out.
- A result may be shown before it is confirmed only for what stays on the machine, such as a
  draft. A push or a vote reads "sending" until Gerrit has answered.
- One primary action must not hide the others: the alternatives are listed in words beside it.

## Roadmap

### Near: decided in the issues

| Item | Issues | Needs from core |
|------|--------|-----------------|
| One selection model and one look for the three views; Review chains as a webview | 97, 42, 70 | nothing new |
| Change view with room: an editor tab, From / To patchset pickers | 64, 66 | nothing new |
| Stage and Continue during a rebase; the commit message while editing a commit | 132, 131, 43 | the changes since the stop, staging by path, reword at an edit stop |
| Comment box and navigation with fewer keystrokes | 133, 134, 46 | one draft per chain, changing a draft's type |
| Reviewers at a glance, collapsed views remembered, fewer notes | 130, 129, 135 | nothing new |
| CI: manual reload, links to parsed logs | 136 | a refresh of CI content |

### Next: the consequence of those decisions

| Item | Why it follows | Needs from core |
|------|----------------|-----------------|
| **Push from the views**: the preview (what each commit becomes, which votes a code change drops, the drafts that go along) and one confirmation | The author loop still ends in a terminal prompt. The preview data and the readiness verdict already live in core | the push plan served; the command names the head it previewed ([ADR-0008](../adr/0008-push-confirm-loop-stays-in-the-command.md) stands) |
| **One Next walk**: one key through unresolved chains, CI findings and unreviewed files of the open change, then the next member of the chain | Principle 2; today there are three: the comment walk, Work on Next and Review Next Change | the walk order served, as the attention walk is for `ger edit` |
| **Combined diff of a chain** in one editor tab, with all its comments | 64 and 44 together | a chain-wide diff and comment listing |
| **Undo for every rewrite**, and with it Drop, Squash and Skip without a question | Principle 5 depends on it | the operation log of the commit-chain roadmap |
| **Show the command**: each action's tooltip names the `ger` line it runs | Teaches the command line and makes the view trustworthy | nothing new |

### Later: named only, beyond what the issues state

Agent-suggested review order (137), delegation of comments to an agent in the views (114),
absorb and split from the stack view, a sync button, single-key bindings in the views,
publishing to the marketplaces and a dev container (87, 88, 48). Each needs its own decision.

## The two loops, as walked on 2026-11-25

Both loops were walked on a live Gerrit in a real VS Code; what was cheap was fixed, and this is
what is left. The proposals below are not decided.

### Reviewing a chain

Today: *To review* (your turn only) → Review Change → the window opens on the change with all its
files in one editor → comment in place, with the language server working → Suggest Edit → Vote and
Publish → Next Change.

| Open | Proposal |
|------|----------|
| Nothing says which file matters or in which order to read (137) | One agent call per patchset, cached by commit: three to five summary bullets, a risk line with its reason, and a **file order with one line per file**. Shown above the commit message; the Files list and the combined diff follow it. Hunks are not reordered across files: a hunk away from its file is harder to judge, and "have I seen everything" loses its answer |
| "Have I seen every file" | Gerrit's per-file *reviewed* flag, set when a file's diff was scrolled through; `3 / 4 files seen` in the Change view |
| The Change view is a third of the side bar (64) | While a change is reviewed or reworked, the Change view takes the side bar and the two lists fold to their headline |
| A suggested edit is text in a fence | Kept as text (it is what Gerrit's own UI reads); the agent's findings arrive as draft comments with suggested edits the reviewer accepts or discards |

### Reworking a reviewed chain

Today: Work on Next → the commit's first comment to answer, at its line → fix, or Apply the
suggested edit → Reply Done → next comment → Work on Next → Push, which publishes the replies.
The terminal is needed to amend and to run tests.

**Edit each commit (A) or fix from the top (B).** Both stay, and the tool says which one fits a
given fix. B is right for small fixes in lines no later commit touches; A for anything else. The
line between them is one check that core has: does this change fold cleanly into its commit, and do
the commits above it still replay. `ger fix` refuses a fixup that fails it and names `ger edit`.

| Open | Proposal |
|------|----------|
| Amend at an edit stop is a terminal command | Continue amends what changed since the stop (132) |
| B needs a commit id typed per fix | **Absorb**: edit at the top, one command; each hunk goes to the commit that last touched its lines when the fold check passes, the rest are listed with the reason. No target to name, no fixup rows |
| B has no place that shows the whole chain (44) | A chain overview in the editor area: one collapsible section per change that needs attention, its hunks with the comment chains and CI findings at their lines. Per hunk two actions: *Edit Commit Here* (A) and *Fix into this commit* (B, behind the fold check). Editing happens in the working file, never inside a rendered old hunk |
| A comment whose fix belongs in a new commit on top | **Fix on top** as a reply, instead of comments that float in a local overlay: a draft "Fixed in {change}" on the original comment, a trailer on the new commit naming the comment, and `ger push` fills in the new change and resolves the chain. At the top of the chain the commit lists what it addresses. Gerrit holds the truth and nothing local can drift across machines, reorders or squashes |
| Tests are run by hand at every stop | A `ger.testCommand` the views run at a stop, with its result on the row |
| Nothing says a rewritten commit still compiles | CI does the final check; a local check only has to be cheaper than the CI round it saves. A per-repository check command, **recommended, never forced**, and only where a break is likely: after a fix was folded into a commit below the top, after a resolved conflict, after an applied suggested edit. Not before every Continue and not before every push. ger remembers the result by tree, so the same content is not checked twice. The push preview names commits whose content was rewritten and never checked, as a note |

## What was looked at elsewhere

Rated against the yardstick; the full notes are not kept here.

**Worth taking**

- An operation log with one-step undo (Jujutsu, git-branchless, Sapling, lazygit): the
  precondition for removing confirmations.
- Absorb (Sapling, git-absorb): the fastest way to address comments spread over a chain.
- Sync that drops merged commits and skips a conflicting stack instead of stopping (Graphite,
  git-branchless).
- A queue split by whose move it is (Gerrit's *Your turn* and attention set, Graphite's inbox).
- Rebase-only changes left out of a patchset comparison (Gerrit).
- Gerrit's words for states users already know: *Done*, *Ack*, *Not current*, *Submittable*.
- The command behind every button (Sapling's ISL).

**Not for this audience**

- Virtual branches (GitButler): state is no longer visible at a glance.
- Generated descriptions and automatic conflict resolution: output that must be reviewed again.
- An editor or a prompt per item (`repo upload`, guided traversals): a step per item.
- Gerrit's web edit mode and server-side *apply fix*: the local commit goes stale.
- A confirmation on every action: the opposite of undo first.
