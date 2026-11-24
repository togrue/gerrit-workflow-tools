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
| **Whose turn it is** on every row: a chain whose last word is yours waits on the other side | The headline and the bell should point at what needs you, not at what you answered | attention that tells *your turn* from *their turn*; Gerrit's attention set where the server has it |
| **One Next walk**: one key through unresolved chains, CI findings and unreviewed files of the open change, then the next member of the chain | Principle 2; today each has its own command | the walk order served, as the attention walk is for `ger edit` |
| **Combined diff of a chain** in one editor tab, with all its comments | 64 and 44 together | a chain-wide diff and comment listing |
| **Undo for every rewrite**, and with it Drop, Squash and Skip without a question | Principle 5 depends on it | the operation log of the commit-chain roadmap |
| **Apply a suggested edit** from a comment chain into the commit it belongs to | The author retypes what the reviewer already wrote | applying a suggestion as a fixup |
| **Show the command**: each action's tooltip names the `ger` line it runs | Teaches the command line and makes the view trustworthy | nothing new |

### Later: named only, beyond what the issues state

Agent-suggested review order (137), delegation of comments to an agent in the views (114),
absorb and split from the stack view, a sync button, single-key bindings in the views,
publishing to the marketplaces and a dev container (87, 88, 48). Each needs its own decision.

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
