// The gallery's built-in scenarios: the states of the two webview views that matter for layout,
// each a snapshot (src/snapshot.ts). Every view model comes from the real `stackVm` / `changeVm`
// on what `ger serve` would answer (test/fixtures.ts), so a scenario cannot show what the
// extension would never draw.

import { createHash } from "node:crypto";

import { Chain, CommentChain, Member, MoveConflict, Overview, RebaseAction, RebaseStop, ReviewState, Section, ShownCommit, Stack, StackCommit } from "../../src/api";
import { ChangeState, ChangeVm, READ_ONLY, changeVm } from "../../src/changeRows";
import { changeScreen } from "../../src/changeScreen";
import { nextAction } from "../../src/nextAction";
import { changeProblem } from "../../src/problemRows";
import { SNAPSHOT_FORMAT, Snapshot } from "../../src/snapshot";
import { StackVm, stackVm } from "../../src/stackRows";
import { waitingFor } from "../../src/state";
import { Outcome, TaskVm, taskVm } from "../../src/taskRows";
import { chain, comment, commit, content, diff, finding, never, patchsets, shown, stack, state } from "../fixtures";

/** A snapshot, and what to press once it is drawn: sections and comments the page only shows opened. */
export interface Scenario extends Snapshot {
  /** CSS selectors: every element one matches is clicked, in turn, after the first render. */
  click?: string[];
}

function scenario(name: string, views: { stack?: StackVm; change?: ChangeVm; click?: string[] }): Scenario {
  return { format: SNAPSHOT_FORMAT, name, stack: views.stack ?? null, change: views.change ?? null, ...(views.click ? { click: views.click } : {}) };
}

// --- What ger serve would answer -----------------------------------------------------

const REVIEWERS = [
  { name: "Alice Arnold", cc: false },
  { name: "Bob Becker", cc: false },
  { name: "Carol Chen", cc: true },
];

/** A pushed commit that looks like one: a hex id, a change number, reviewers. */
function pushed(summary: string, extra: Partial<StackCommit> = {}): StackCommit {
  const sha = createHash("sha1").update(summary).digest("hex");
  const number = 48000 + (Number.parseInt(sha.slice(0, 4), 16) % 2000);
  return commit(summary, {
    sha,
    short_sha: sha.slice(0, 7),
    change_id: `I${sha}`,
    number,
    gerrit_url: `https://gerrit.example.org/c/widget/+/${number}`,
    attention_reasons: ["awaiting-review"],
    reviewers: REVIEWERS,
    ...extra,
  });
}

/** A commit Gerrit does not have yet. */
function unpushed(summary: string, extra: Partial<StackCommit> = {}): StackCommit {
  return pushed(summary, { pushed: false, patchset_status: "absent", number: null, gerrit_url: null, attention_reasons: ["not-pushed"], reviewers: [], ...extra });
}

/** The local stack of `commits`, oldest first, on a branch with a real name. */
function local(commits: StackCommit[], extra: Partial<Stack> = {}): Stack {
  return stack(commits, { branch: "routing", stack: { project: "widget", target_branch: "origin/main", push_branch: "main" }, ...extra });
}

const stackOf = (commits: StackCommit[], extra: Partial<Stack> = {}): StackVm => stackVm(local(commits, extra), undefined, never);

// --- Local stack ---------------------------------------------------------------------

function syncStates(): StackVm {
  return stackOf([
    pushed("Add the routing table", { patchset_status: "merged-same", change_status: "MERGED", code_review: 2, verified: 1, attention_reasons: [] }),
    pushed("Extract routing into its own module", { code_review: 2, verified: 1, submittable: true, attention_reasons: [] }),
    pushed("Route by longest matching prefix", { patchset_status: "newer", code_change: "rebased", code_review: 2, attention_reasons: ["ahead-of-gerrit"] }),
    pushed("Match routes case-insensitively", { patchset_status: "newer", code_change: "changed", last_pushed_votes: { verified: 1, code_review: 2 }, attention_reasons: ["ahead-of-gerrit"] }),
    pushed("Normalise trailing slashes", { patchset_status: "newer", code_change: "unknown", attention_reasons: ["ahead-of-gerrit"] }),
    pushed("Cache resolved routes", { patchset_status: "outdated", attention_reasons: ["outdated-patchset"] }),
    pushed("Evict stale routes", { patchset_status: "newer", diverged: true, attention_reasons: ["ahead-of-gerrit"] }),
    pushed("Log route misses", { patchset_status: "unknown", attention_reasons: [] }),
    unpushed("Document the routing table", { is_head: true }),
  ]);
}

/** The four comment states of commentState.ts, each with its share: to answer, your drafts, waiting, all resolved. */
function commentStates(): StackVm {
  return stackOf([
    pushed("All resolved: a full ring with a tick", { comments_unresolved: 0, comments_total: 4 }),
    pushed("Waiting on a reply: a thin ring, 1 of 3", { comments_unresolved: 1, comments_awaiting_reply: 1, comments_total: 3 }),
    pushed("Your drafts answer them: a dashed ring, 2 of 4", { comments_unresolved: 2, comments_drafted: 2, comments_total: 4, attention_reasons: ["drafts-to-publish"] }),
    pushed("To answer: a thick ring, 3 of 3 open", { comments_unresolved: 3, comments_total: 3, attention_reasons: ["unresolved-comments"] }),
  ]);
}

function badgeKinds(): StackVm {
  return stackOf([
    pushed("Nothing to say yet"),
    pushed("Unresolved comments only", { comments_unresolved: 3, attention_reasons: ["unresolved-comments"] }),
    pushed("Approved and verified", { code_review: 2, verified: 1, submittable: true, attention_reasons: [] }),
    pushed("A positive vote, CI still running", { code_review: 1 }),
    pushed("A negative vote", { code_review: -1, attention_reasons: ["review-issues"] }),
    pushed("Vetoed, CI failed, many comments", { code_review: -2, verified: -1, ci_failures: ["Verified"], comments_unresolved: 12, attention_reasons: ["ci-failed", "unresolved-comments", "review-issues"] }),
    pushed("CI failed, nobody voted", { ci_failures: ["widget-verify", "widget-lint"], attention_reasons: ["ci-failed"] }),
    pushed("Votes of the last pushed patchset", { patchset_status: "newer", code_change: "changed", last_pushed_votes: { verified: -1, code_review: 2 }, comments_unresolved: 1, attention_reasons: ["ahead-of-gerrit"] }),
    pushed("Nobody to review it", { reviewers: [], attention_reasons: ["no-reviewers"], is_head: true }),
  ]);
}

function rowKinds(): StackVm {
  return stackOf([
    pushed("Add the routing table", { code_review: 2, verified: 1, pinned: true }),
    pushed("Merge branch 'release-2.4' into routing", { merge: true, pinned: true, patchset_status: "absent", pushed: false, number: null, gerrit_url: null, attention_reasons: ["not-pushed"] }),
    pushed("Extract routing into its own module", { code_review: 1 }),
    pushed("Drop the first-match router", { abandoned: true, change_status: "ABANDONED", attention_reasons: ["abandoned"] }),
    pushed("Cache resolved routes", { held_back: true, work_in_progress: true, attention_reasons: ["wip"] }),
    unpushed("Evict stale routes", { held_back: true }),
    unpushed("Try a radix tree instead", { held_back: true, is_head: true, change_id: null, attention_reasons: ["missing-change-id"] }),
  ]);
}

/** What core allows at each stop (`RebaseProgress.actions`). */
const WAYS_ON: Record<RebaseStop, RebaseAction[]> = {
  edit: ["continue", "abort"],
  split: ["continue", "abort"],
  paused: ["continue", "abort"],
  conflict: ["abort", "skip"],
  resolved: ["continue", "abort", "skip"],
};

function rebasing(stop: RebaseStop): StackVm {
  const applied = stop === "edit";
  return stackOf(
    [
      pushed("Add the routing table", { rebase: "done", code_review: 2, verified: 1 }),
      pushed("Extract routing into its own module", { rebase: "done", is_head: !applied, comments_unresolved: 2, attention_reasons: ["unresolved-comments"] }),
      pushed("Route by longest matching prefix", { rebase: "current", is_head: applied, ...(applied ? { patchset_status: "newer", attention_reasons: ["ahead-of-gerrit"] } : {}) }),
      pushed("Cache resolved routes", { rebase: "todo", code_review: -1, attention_reasons: ["review-issues"] }),
      unpushed("Evict stale routes", { rebase: "todo" }),
    ],
    {
      rebase_in_progress: true,
      rebase: { branch: "routing", onto: null, orig_head: null, stop, applied, current: null, unmerged: stop === "conflict" ? ["src/routes.ts"] : [], as_before: false, actions: WAYS_ON[stop], refused: {} },
    },
  );
}

function longSubjects(): StackVm {
  return stackOf([
    pushed("Route requests by the longest matching prefix instead of the first one registered, keeping the registration order only as the tie-break", {
      code_review: -1,
      verified: -1,
      ci_failures: ["Verified"],
      comments_unresolved: 4,
      attention_reasons: ["ci-failed", "unresolved-comments", "review-issues"],
      pinned: true,
    }),
    pushed("WIP", { held_back: true, work_in_progress: true, attention_reasons: ["wip"], pinned: true }),
    pushed("Merge remote-tracking branch 'origin/release-2.4-long-lived-stabilisation' into routing", { merge: true, held_back: true, pinned: true }),
    pushed("AVeryLongSubjectWithoutASingleBlankInItThatCannotWrapAnywhereAtAllSoItHasToBeCutOff", { held_back: true, code_review: 2, verified: 1 }),
    unpushed("Cache resolved routes per worker process and evict them when the routing table is reloaded from disk", { held_back: true, is_head: true }),
  ]);
}

function swapConflict(): StackVm {
  const commits = [pushed("Extract routing into its own module"), pushed("Route by longest matching prefix"), unpushed("Cache resolved routes", { is_head: true })];
  const [lower, upper] = [commits[0].sha, commits[1].sha];
  const conflict: MoveConflict = { sha: upper, summary: commits[1].summary, files: ["src/widget/routing/prefix_tree.py", "tests/test_routing.py"] };
  return stackVm(local(commits), undefined, (a, b) => (a === lower && b === upper ? conflict : undefined));
}

const NO_UPSTREAM = { error: { code: "GIT", message: "fatal: no upstream configured for branch 'change-48211'" }, needs_upstream: true };

function stackScenarios(): Scenario[] {
  return [
    scenario("Stack: every sync state", { stack: syncStates() }),
    scenario("Stack: every badge", { stack: badgeKinds() }),
    scenario("Stack: every comment state, a ring and a shape each", { stack: commentStates() }),
    scenario("Stack: HEAD, held back, a merge, abandoned", { stack: rowKinds() }),
    scenario("Stack: a rebase in progress", { stack: rebasing("edit") }),
    scenario("Stack: a rebase stopped on a conflict", { stack: rebasing("conflict") }),
    scenario("Stack: a rebase with its conflicts resolved", { stack: rebasing("resolved") }),
    scenario("Stack: long subjects", { stack: longSubjects() }),
    scenario("Stack: neighbours that cannot swap (hover the rows)", { stack: swapConflict() }),
    scenario("Stack: no upstream, one to track", {
      stack: stackVm(NO_UPSTREAM, { branch: "change-48211", upstream: null, suggested: "origin/release-2.4", why: "the remote branch nearest to it", candidates: ["origin/release-2.4", "origin/main"] }, never),
    }),
    scenario("Stack: no upstream, none to suggest", { stack: stackVm(NO_UPSTREAM, { branch: "change-48211", upstream: null, suggested: null, why: null, candidates: [] }, never) }),
    scenario("Stack: it could not be read", { stack: stackVm({ error: { code: "GIT", message: "fatal: bad revision 'origin/main..HEAD'\nthe upstream branch is gone" } }, undefined, never) }),
    scenario("Stack: no local commits", { stack: stackOf([]) }),
    scenario("Stack: a rebase aborted, what it kept said", {
      stack: stackVm(local([pushed("Extract routing into its own module"), unpushed("Cache resolved routes", { is_head: true })]), undefined, never, () => 0, false, {
        text: "rebase aborted: routing is at 9b1e0d4 Cache resolved routes, as before the rebase",
        ok: true,
        more: ["kept: your changes to src/widget/routing/prefix_tree.py, not amended, in stash@{0}; `git stash apply` brings them back"],
      }),
    }),
    scenario("Stack: a push Gerrit refused, said in the view", {
      stack: stackVm(local([unpushed("Cache resolved routes", { is_head: true })]), undefined, never, () => 0, false, {
        text: "Gerrit rejected the push: branch release-9.9 not found",
        ok: false,
        more: ["next: the branch is not on Gerrit: set the upstream to one it has (`git branch --set-upstream-to=origin/main`)"],
      }),
    }),
    scenario("Stack: commits push gives a Change-Id", {
      stack: stackOf([pushed("Extract routing into its own module"), unpushed("Cache resolved routes", { change_id: null, adds_change_id: true, attention_reasons: ["missing-change-id"] }), unpushed("Evict stale routes", { is_head: true, change_id: null, adds_change_id: true, attention_reasons: ["missing-change-id"] })]),
    }),
    scenario("Stack: a detached HEAD", { stack: stackOf([pushed("Extract routing into its own module", { is_head: true })], { branch: null }) }),
    scenario("Stack: the upstream is gone", {
      stack: stackVm(
        { error: { code: "CONFIG", message: "The upstream origin/release-9.9 of branch 'routing' is gone: it is configured, but no such remote-tracking branch is there (deleted on the remote, or pruned)." }, needs_upstream: true },
        { branch: "routing", upstream: null, gone: "origin/release-9.9", suggested: "origin/main", why: "nearest: 1 ahead, 0 behind", candidates: ["origin/main"] },
        never,
      ),
    }),
  ];
}

// --- Change --------------------------------------------------------------------------

const SUBJECT = "Route requests by longest matching prefix";

const MESSAGE = `${SUBJECT}

The router took the first prefix that was registered, so /api/v2/users
went to the /api handler. Match the longest prefix instead, and keep the
registration order only as the tie-break.

Bug: WIDGET-1482
Change-Id: I${createHash("sha1").update(SUBJECT).digest("hex")}`;

const FILES = [
  { path: "Makefile", status: "M", old_path: null, insertions: 1, deletions: 1 },
  { path: "docs/images/routing-tree.png", status: "A", old_path: null, insertions: null, deletions: null },
  { path: "src/widget/first_match.py", status: "D", old_path: null, insertions: 0, deletions: 312 },
  { path: "src/widget/routing/__init__.py", status: "M", old_path: null, insertions: 2, deletions: 1 },
  { path: "src/widget/routing/internal/compat/legacy_first_match_router_adapter.py", status: "R", old_path: "src/widget/router_adapter.py", insertions: 12, deletions: 1203 },
  { path: "src/widget/routing/prefix_tree.py", status: "M", old_path: null, insertions: 184, deletions: 37 },
  { path: "tests/integration/routing/test_longest_prefix_matching_with_overlapping_registrations.py", status: "A", old_path: null, insertions: 2468, deletions: 0 },
];

/** A message as people write them: hard-wrapped at 72, a list, an indented example, trailers. */
const LONG_MESSAGE = `calc: raise DivisionByZero from divide()

divide() let Python's ZeroDivisionError escape, so callers could not
tell a calc error from a bug in their own code. Introduce calc.errors
with a CalcError base class and DivisionByZero, which still subclasses
ZeroDivisionError so existing \`except ZeroDivisionError\` keeps working.

What changes for callers:
- divide(1, 0) raises DivisionByZero, with the operands in the message
  so the traceback says which call it was.
- every error calc raises is a CalcError: one except clause for all.

Before and after:

    >>> divide(1, 0)
    calc.errors.DivisionByZero: cannot divide 1 by zero

Bug: CALC-12
Signed-off-by: Alice Author <alice@example.org>`;

const theChange = (extra: Partial<ShownCommit> = {}): ShownCommit => shown({ ...pushed(SUBJECT), ...extra });
/** As /diff serves it: the box's text without the Change-Id footer, which comes beside it. */
const [MESSAGE_TEXT, CHANGE_ID] = [MESSAGE.slice(0, MESSAGE.lastIndexOf("\n")), MESSAGE.slice(MESSAGE.lastIndexOf(" ") + 1)];
const itsDiff = (extra: Parameters<typeof diff>[0] = {}) =>
  diff({ message: MESSAGE, message_text: MESSAGE_TEXT, change_id: CHANGE_ID, author: "Dana Dietrich <dana@example.org>", files: FILES, ...extra });
const FIVE = { ...patchsets, number: 48211, current: 5, reviewed: 2, patchsets: [1, 2, 3, 4, 5].map((number) => ({ number, commit: String(number).repeat(40), created: null })) };

/** The Change view of a commit of your stack with everything arrived, unless `extra` says otherwise. */
const change = (extra: Partial<ChangeState> = {}): ChangeVm => changeVm(state({ shown: theChange(), base: itsDiff(), patchsets: FIVE, ...extra }));

const REMOTE = "Only a commit of your local stack can be reworded here.";
const since = { patch_set: 2, current_patch_set: 5, to_patch_set: null, up_to_date: false, identical: false, rebased: false };

const TREE = "src/widget/routing/prefix_tree.py";
const on = (line: number | null, path = TREE) => ({ path, line });

function chains(): CommentChain[] {
  const whole = on(null, "/PATCHSET_LEVEL");
  const init = on(7, "src/widget/routing/__init__.py");
  const fix = {
    fix_id: "f1",
    description: "Export the tree under its public name",
    in_message: false,
    replacements: [{ path: init.path, range: { start_line: 7, start_character: 0, end_line: 8, end_character: 0 }, replacement: 'from .prefix_tree import PrefixTree\n\n__all__ = ["PrefixTree", "route"]\n', location: `${init.path}:7` }],
  };
  // What ger serve writes out below the comment (render/suggested_fixes.py).
  const exported = "Export it here, or callers have to reach into the module.";
  const fixMarkdown = `**Suggested fix:** ${fix.description}\n\`${init.path}:7\` becomes:\n\`\`\`\nfrom .prefix_tree import PrefixTree\n\n__all__ = ["PrefixTree", "route"]\n\`\`\``;
  const fixText = `suggested fix: ${fix.description}\n  ${init.path}:7 becomes:\n  + from .prefix_tree import PrefixTree\n  + \n  + __all__ = ["PrefixTree", "route"]`;
  return [
    chain("c1", [comment("Should this be a node per path segment rather than per character? With one per character the tree gets very deep for long URLs.", { ...on(42) }), comment("Per segment would also make the wildcard match simpler.", { ...on(42), author: "Bob Becker" })], on(42)),
    chain("c2", [comment("What happens when two handlers register the very same prefix?", { ...on(88), author: "Bob Becker" })], { ...on(88), held: true }),
    chain("c3", [comment("Typo: registraton", { ...on(101) }), comment("Done", { ...on(101), state: "draft", author: undefined, comment_id: "d1" })], { ...on(101), resolved_after_publish: true }),
    chain(
      "c4",
      [
        comment(
          "Thanks for taking this on. Two things before I can approve it.\n\nFirst, the commit removes first_match.py but the plugin loader still imports it behind a feature flag; with the flag set the service no longer starts. Please either keep a shim or remove the flag in the same change.\n\nSecond, the integration test registers its prefixes in sorted order, so it would also pass with the old first-match behaviour for half of its cases. Shuffle them.",
          { ...whole, author: "Carol Chen" },
        ),
        comment("Good catch on the flag, I will keep a shim.", { ...whole, state: "draft", author: undefined, comment_id: "d2" }),
      ],
      whole,
    ),
    chain("c5", [comment(exported, { ...init, suggested_edits: [fix], text: `${exported}\n\n${fixText}`, markdown: `${exported}\n\n${fixMarkdown}` })], init),
    chain("c6", [comment("Nit: blank line."), comment("Done", { author: "Dana Dietrich" })], { ...on(12), resolved: true, resolved_after_publish: true }),
    chain("c7", [comment("Measure this against the old router before pushing.", { ...on(140), state: "note", author: undefined, comment_id: "n1" })], on(140)),
    chain("c8", [comment("Explain here why registration order is the tie-break.", { ...on(63), state: "local-draft", author: undefined, comment_id: "l1" })], on(63)),
  ];
}

const commented = () => change({ shown: theChange({ comments_unresolved: 5, code_review: -1, attention_reasons: ["unresolved-comments", "review-issues"] }), chains: chains() });

const FAILING = { ci_failures: ["Verified"], verified: -1, attention_reasons: ["ci-failed"], ci_links: [{ label: "widget-verify #5512", url: "https://ci.example.org/job/widget-verify/5512/", source: "message" }] };

/** The findings of one failed build: two this change introduced, one it inherited, and warnings behind them. */
function findings() {
  return [
    finding("unused variable 'node'", "warning", "w1", { tool: "pylint", path: TREE, line: 51 }),
    finding("test_longest_prefix_wins: AssertionError: expected handler 'users', got 'api'", "error", "e1", { tool: "pytest", path: "tests/integration/routing/test_longest_prefix_matching_with_overlapping_registrations.py", line: 212 }),
    finding("line too long (131 > 120 characters)", "warning", "w2", { tool: "flake8", path: TREE, line: 97 }),
    finding("ModuleNotFoundError: No module named 'widget.first_match'", "error", "e2", { tool: "pytest", path: "src/widget/plugins/loader.py", line: 14 }),
    finding("test_reload_keeps_connections: timed out after 60 s", "error", "e3", { tool: "pytest", path: null, line: null }),
    finding("missing docstring", "warning", "w3", { tool: "pylint", path: "src/widget/routing/__init__.py", line: 1 }),
  ];
}

function ciLoaded(introduced: Set<string> | null): ChangeVm {
  const build = (name: string, extra: object = {}) => ({ url: `https://ci.example.org/job/${name.replace(" #", "/")}/console`, build: name, ...extra });
  return change({
    shown: theChange(FAILING),
    ci: {
      state: "loaded",
      contents: [content(introduced !== null, findings(), build("widget-verify #5512", { truncated: true })), content(false, [], build("widget-lint #981", { error: "HTTP 403: the build log needs a login" }))],
      introduced,
    },
  });
}

function changeScenarios(): Scenario[] {
  const voted = { code_review: 2, verified: 1, submittable: true, attention_reasons: [] };
  const fromRow = { commit: true, base: true, delta: false, ready: false };
  return [
    scenario("Change: a commit of your stack, its message editable", { change: change({ shown: theChange(voted) }) }),
    scenario("Change: somebody's change under review in its own window", { change: changeScreen(change({ shown: theChange({ own: false }), local: false, elsewhere: "/home/dana/widget-review" }), { next: nextAction({ stack: local([]), shown: theChange({ own: false }), chains: [], local: false, reviewing: false, visited: new Set(), files: [], seenFiles: new Set(), reviewElsewhere: "/home/dana/widget-review" }) }) }),
    scenario("Change: somebody's change, uncommitted changes here in the way of a review", { change: changeScreen(change({ shown: theChange({ own: false }), local: false, blocked: ["README.md"] }), { next: nextAction({ stack: local([], { review_blocked: { reason: "uncommitted-changes", message: "", files: ["README.md"] } }), shown: theChange({ own: false }), chains: [], local: false, reviewing: false, visited: new Set(), files: [], seenFiles: new Set() }) }) }),
    scenario("Change: not on Gerrit yet", { change: change({ shown: shown(unpushed(SUBJECT)), patchsets: undefined }) }),
    scenario("Change: ahead of Gerrit, compared with it", {
      change: change({ shown: theChange({ patchset_status: "newer", code_change: "changed", last_pushed_votes: { verified: 1, code_review: 2 }, attention_reasons: ["ahead-of-gerrit"] }), from: "current", mode: "gerrit", delta: itsDiff({ files: FILES.slice(3, 6), since: { ...since, patch_set: 5 } }) }),
    }),
    scenario("Change: two patchsets picked by hand", {
      change: change({ from: 3, version: 5, readOnly: "This is a patchset on Gerrit: choose the local commit to edit its message.", delta: itsDiff({ files: FILES.slice(2, 5), since: { ...since, patch_set: 3, to_patch_set: 5 } }) }),
    }),
    scenario("Change: your commit since your last review, the default mode", {
      change: change({ from: "reviewed", version: "current", mode: "reviewed", readOnly: "This is a patchset on Gerrit: choose the local commit to edit its message.", delta: itsDiff({ files: FILES.slice(4, 6), since: { ...since, to_patch_set: 5 } }) }),
    }),
    scenario("Change: Gerrit has a newer patchset", { change: change({ shown: theChange({ patchset_status: "outdated", attention_reasons: ["outdated-patchset"] }) }) }),
    scenario("Change: someone's change, new since your review", {
      change: change({ local: false, shown: theChange({ local_commit: false, code_review: 1 }), version: "current", readOnly: REMOTE, delta: itsDiff({ files: FILES.slice(4, 6), since: { ...since, to_patch_set: 5 } }) }),
    }),
    scenario("Change: someone's change, a long message to read", {
      change: change({ local: false, shown: theChange({ local_commit: false }), version: "current", readOnly: REMOTE, base: itsDiff({ message: LONG_MESSAGE, message_text: LONG_MESSAGE }) }),
    }),
    scenario("Change: someone's change since your review, rebased", {
      change: change({ local: false, shown: theChange({ local_commit: false }), version: "current", from: "reviewed", mode: "reviewed", readOnly: REMOTE, delta: itsDiff({ files: FILES.slice(4, 6), since: { ...since, to_patch_set: 5, rebased: true } }) }),
    }),
    scenario("Change: comments as they arrive", { change: commented() }),
    scenario("Change: comments opened in full", { change: commented(), click: [".thread .twist"] }),
    scenario("Change: CI failed, findings not fetched", { change: change({ shown: theChange(FAILING) }) }),
    scenario("Change: CI findings being fetched", { change: change({ shown: theChange(FAILING), ci: { state: "loading" } }) }),
    scenario("Change: CI findings, introduced and inherited", { change: ciLoaded(new Set(["e1", "e2", "w1", "w2"])), click: [".finding[aria-expanded]"] }),
    scenario("Change: CI findings, their origin unknown", { change: ciLoaded(null) }),
    scenario("Change: CI findings could not be fetched", { change: change({ shown: theChange(FAILING), ci: { state: "error", error: "Gerrit unreachable: connection refused (gerrit.example.org:443)" } }) }),
    scenario("Change: CI failed without a link to its log", { change: change({ shown: theChange({ ...FAILING, ci_links: [] }), ci: { state: "loaded", contents: [], introduced: null } }) }),
    scenario("Change: a rebase stopped here on a conflict", {
      change: change({
        shown: theChange({ patchset_status: "newer", last_pushed_votes: { verified: 1, code_review: 2 }, attention_reasons: ["ahead-of-gerrit"] }),
        readOnly: READ_ONLY.rebase,
        stopped: { stop: "conflict", unmerged: [TREE, "tests/integration/routing/test_longest_prefix_matching_with_overlapping_registrations.py"], actions: ["abort", "skip"] },
      }),
    }),
    scenario("Change: a rebase stopped here, its conflicts resolved", {
      change: change({ shown: theChange(FAILING), readOnly: READ_ONLY.rebase, stopped: { stop: "resolved", unmerged: [], actions: ["continue", "abort", "skip"] } }),
    }),
    scenario("Change: loading, drawn from the clicked stack row", { change: change({ shown: theChange({ attention_reasons: [] }), base: undefined, patchsets: undefined, loading: fromRow }) }),
    scenario("Change: loading someone's change", {
      change: change({ local: false, shown: theChange({ local_commit: false, comments_unresolved: 2, attention_reasons: ["unresolved-comments"] }), base: undefined, patchsets: undefined, version: "current", readOnly: REMOTE, loading: fromRow }),
    }),
    scenario("Change: it could not be shown", { change: { problem: changeProblem("ger show failed: the commit 3f9a2c1 is not in this repository (was it dropped by a rebase?)", true) } }),
  ];
}

// --- Both views ----------------------------------------------------------------------

const NO_ANSWER = { code: "GERRIT", message: "Gerrit at 10.255.255.1:8081 does not answer (waited 10 s)", reason: "no-answer" };
const REFUSED = { code: "GERRIT", message: "Gerrit at gerrit.example.org rejected the credentials (HTTP 401); check ger.user and ger.token, or run `ger setup`", reason: "credentials" };

/** Gerrit could not be asked: Local stack says it once, the Change view says unknown with the ways on, and Next names the fix. */
function gerritAway(away: typeof NO_ANSWER): { stack: StackVm; change: ChangeVm } {
  const unknown = { patchset_status: "unknown" as const, pushed: false, attention_reasons: ["gerrit-unknown"] };
  const stack = local([pushed("Add the prefix tree", unknown), pushed("Route by longest prefix", { ...unknown, is_head: true })], { gerrit_unavailable: away });
  const shownCommit = theChange({ ...unknown, own: false, gerrit_unavailable: away });
  const next = nextAction({ stack, shown: shownCommit, chains: [], local: false, reviewing: false, visited: new Set(), files: [], seenFiles: new Set() });
  return { stack: stackVm(stack, undefined, never), change: changeScreen(change({ shown: shownCommit, local: false }), { next }) };
}

function bothScenarios(): Scenario[] {
  const nothing = "Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.";
  // As the two views say it while there is no overview (stackView.ts, changeView.ts).
  const noStack = stackVm(undefined, undefined, never);
  return [
    scenario("Both: a stack and its selected commit", { stack: syncStates(), change: commented() }),
    scenario("Both: ger serve is starting", { stack: { ...noStack, waiting: waitingFor("starting", "Your local stack") }, change: { waiting: waitingFor("starting", "A change") } }),
    scenario("Both: Gerrit is being asked; Local stack from git meanwhile", { stack: stackVm(local([pushed("Add the prefix tree", { patchset_status: "unknown", pushed: false, attention_reasons: ["gerrit-unknown"] })], { local_only: true }), undefined, never), change: { waiting: waitingFor("asking", "A change", undefined, undefined, 10) } }),
    scenario("Both: Gerrit does not answer", gerritAway(NO_ANSWER)),
    scenario("Both: Gerrit refuses the credentials", gerritAway(REFUSED)),
    scenario("Both: ger is not set up", { stack: { ...noStack, waiting: waitingFor("setup", "Your local stack") }, change: { waiting: waitingFor("setup", "A change") } }),
    scenario("Both: nothing to show", { stack: noStack, change: { empty: nothing } }),
  ];
}

// --- The task header (taskRows.ts) ----------------------------------------------------

/** Where the review stands: the change reviewed, your votes so far, what Gerrit's attention set says. */
interface ReviewAt {
  all?: boolean;
  busy?: string;
  /** The change under review (48211 unless said). */
  at?: number;
  /** Changes you voted on already, by number. */
  voted?: number[];
}

/** A chain of Alice's to review: four changes, one of them reviewed here, each in another state. */
function reviewChain(options: ReviewAt = {}): { overview: Overview; stack: Stack; members: Member[] } {
  const at = options.at ?? 48211;
  const voted = new Set(options.voted ?? []);
  const member = (number: number, subject: string, extra: Partial<Member> = {}): Member => ({
    number,
    change_id: `I${number}`,
    subject,
    verified: 1,
    code_review: null,
    comments_unresolved: 0,
    attention_reasons: [],
    severity: "ok",
    url: null,
    local_sha: null,
    sha: null,
    ...extra,
    ...(voted.has(number) ? { your_votes: { "Code-Review": 1 }, your_turn: false } : {}),
  });
  const members = [
    member(48210, "Add the routing table", { your_votes: { "Code-Review": 1 }, comments_unresolved: 1, comments_awaiting_reply: 1, comments_total: 3 }),
    member(48211, SUBJECT, { your_turn: true, comments_unresolved: 3, comments_to_answer: 2, comments_drafted: 1, comments_total: 5 }),
    member(48212, "Cache resolved routes", { your_turn: true, attention_reasons: ["ci-failed"], verified: -1 }),
    member(48213, "Evict stale routes when the table changes", { your_turn: true }),
  ];
  const chain: Chain = {
    key: "widget~main~I48213",
    top: { number: 48213, change_id: "I48213", subject: members[3].subject, url: null },
    project: "widget",
    branch: "main",
    owner: { name: "Alice Arnold", email: null },
    depth: 4,
    wait_age_seconds: 0,
    unreviewed_age_seconds: 0,
    last_activity: null,
    verified: -1,
    code_review: null,
    comments_unresolved: 4,
    attention_reasons: ["ci-failed"],
    severity: "error",
    partial_chain: false,
    members,
  };
  const subject = members.find((m) => m.number === at)?.subject ?? SUBJECT;
  const review: ReviewState = { number: at, patch_set: 5, commit: "5".repeat(40), subject, staged: true, mode: "branch", worktree: "/src/widget", branch: "change-48211", return_to: "routing" };
  const overview: Overview = { host: "gerrit.example.org", sections: [{ name: "to-review", query: "", summary: {}, chains: [chain] } as Section], local: local([]) };
  return { overview, stack: local([], { staged_review: review }), members };
}

function reviewTask(options: ReviewAt = {}): TaskVm | undefined {
  const { overview, stack } = reviewChain(options);
  return taskVm(overview, stack, { all: options.all ?? false, busy: options.busy });
}

/** What Next is given in a review: as the Change view gives it (changeView.ts `next`). */
interface Reading {
  chains?: CommentChain[];
  visited?: Set<string>;
  seen?: Set<string>;
  combinedActive?: boolean;
  /** Gerrit has a newer patchset than the one checked out. */
  behind?: boolean;
  /** The change was abandoned while under review. */
  abandoned?: boolean;
}

/**
 * The Change view of somebody's change under review, as the window draws it: the change, the task
 * header above it, and Next under that (changeScreen.ts), Next decided by nextAction on the same state.
 */
function reviewScreen(at: ReviewAt = {}, reading: Reading = {}, busy?: string): ChangeVm {
  const { stack, members } = reviewChain(at);
  const number = at.at ?? 48211;
  const index = members.findIndex((m) => m.number === number);
  const member = members[index];
  const seen = reading.seen ?? new Set<string>();
  const shownCommit = theChange({ own: false, number, ...(reading.behind ? { patchset_status: "outdated" as const, attention_reasons: ["outdated-patchset"] } : {}), ...(reading.abandoned ? { change_status: "ABANDONED", attention_reasons: ["abandoned"] } : {}) });
  const chainsShown = reading.chains ?? [];
  const vm = change({ shown: shownCommit, seen, chains: chainsShown, reviewing: { behind: Boolean(reading.behind), newest: reading.behind ? 6 : 5 }, patchset: 5, visited: reading.visited });
  const next = nextAction({
    stack,
    shown: shownCommit,
    chains: chainsShown,
    local: true,
    reviewing: true,
    visited: reading.visited ?? new Set(),
    files: FILES.map((file) => file.path),
    seenFiles: seen,
    combinedActive: reading.combinedActive,
    yourTurn: member?.your_turn,
    above: index < members.length - 1,
    newest: reading.behind ? 6 : 5,
  });
  return changeScreen(vm, { task: reviewTask({ ...at, busy }), next, busy });
}

/** Your stack, its commits oldest first, with the rebase stopped at the third. */
function reworkStack(stop: RebaseStop, nothingLeft = false, uncommitted: string[] = [], staged: string[] = []): Stack {
  const applied = stop === "edit";
  const edits = !nothingLeft;
  const commits = [
    pushed("Add the routing table", { rebase: "done", code_review: 2, verified: 1 }),
    pushed("Extract routing into its own module", { rebase: "done", ...(edits ? { comments_unresolved: 2, comments_total: 3, attention_reasons: ["unresolved-comments"], needs_edit: true } : { comments_total: 3 }) }),
    pushed("Route by longest matching prefix", { rebase: "current", is_head: applied, ...(edits ? { comments_unresolved: 1, comments_total: 1, needs_edit: true } : {}) }),
    pushed("Cache resolved routes", { rebase: "todo", ...(edits ? { ci_failures: ["Verified"], verified: -1, needs_edit: true } : {}) }),
    unpushed("Evict stale routes", { rebase: "todo" }),
  ];
  const notAmended = `you have unstaged changes to ${uncommitted.join(", ")}; amend them in (\`git commit --amend -a\`) or \`git stash\` them, then \`ger rebase --continue\``;
  const refused = stop === "conflict" ? { continue: "the index has unresolved conflicts; resolve the conflicts in src/routes.ts, src/cache.ts, `git add` them, then `ger rebase --continue` to finish" } : uncommitted.length ? { continue: notAmended } : {};
  // Two files: the head then says how many, not a path (a path is theirs, and is replaced in a report).
  const unmerged = stop === "conflict" ? ["src/routes.ts", "src/cache.ts"] : [];
  const actions = uncommitted.length ? WAYS_ON[stop].filter((way) => way !== "continue") : WAYS_ON[stop];
  return local(commits, { rebase_in_progress: true, rebase: { branch: "routing", onto: null, orig_head: "f".repeat(40), stop, applied, current: null, unmerged, uncommitted, staged, as_before: false, actions, refused } });
}

/**
 * Your stack, a rebase stopped at its third commit: the task header at the top of the Change view,
 * which shows the commit stopped at (Local stack steps aside; shown with All chains, without its
 * banner), Next under it, as the window draws them (changeScreen.ts).
 */
function reworking(stop: RebaseStop, options: { busy?: string; nothingLeft?: boolean; chains?: CommentChain[]; uncommitted?: string[]; staged?: string[]; outcome?: Outcome } = {}): { stack: StackVm; change: ChangeVm } {
  const stack = reworkStack(stop, options.nothingLeft, options.uncommitted, options.staged);
  const commit = stack.commits[2];
  const task = taskVm(undefined, stack, { all: false, busy: options.busy, ...(options.outcome ? { outcome: options.outcome } : {}) });
  const stopped = stop === "conflict" || stop === "resolved" ? { stop, unmerged: stack.rebase?.unmerged ?? [], actions: WAYS_ON[stop] } : undefined;
  const shownCommit = theChange({ ...commit, own: true });
  const chainsShown = options.chains ?? [];
  const vm = change({ shown: shownCommit, chains: chainsShown, readOnly: READ_ONLY.rebase, stopped, task: "rework", notAmended: options.uncommitted ?? [], staged: options.staged ?? [] });
  const next = nextAction({ stack, shown: shownCommit, chains: chainsShown, local: true, reviewing: false, visited: new Set(), files: FILES.map((file) => file.path), seenFiles: new Set() });
  // Continue running in the terminal is the header's to say (task.ts busyUntilMoved), as in the window.
  return { stack: stackVm(stack, undefined, never, () => 0, true), change: changeScreen(vm, { task, next }) };
}

function taskScenarios(): Scenario[] {
  const all = new Set(FILES.map((file) => file.path));
  const some = new Set(FILES.slice(0, 2).map((file) => file.path));
  const open = chain("q1", [comment("Should this be a node per path segment rather than per character?", { ...on(42), author: "Bob Becker" })], { ...on(42), turn: "yours" });
  const answer = chain("a1", [comment("Rename this to longest_prefix: first-match is gone.", { ...on(12), author: "Bob Becker" })], { ...on(12), turn: "yours" });
  const draft = chain("d1", [comment("Nit: name the constant.", { ...on(30), state: "draft", comment_id: "d1", author: "you" })], on(30));
  const older = chain("p1", [comment("Who still uses LEGACY_FACTOR? If nobody, drop it.", { ...on(5), author: "Bob Becker", patch_set: 1 })], { ...on(5), turn: "yours" });
  return [
    scenario("Task: reviewing a chain, the header on the Change view", { change: reviewScreen({}, { chains: [open], seen: some }) }),
    scenario("Task: reviewing, Next is Update to patchset 6", { change: reviewScreen({}, { chains: [open], seen: some, behind: true }) }),
    scenario("Task: reviewing, Next is Mark 5 files seen", { change: reviewScreen({}, { seen: some, combinedActive: true }) }),
    scenario("Task: reviewing, Next is Next file", { change: reviewScreen({}, { seen: some }) }),
    scenario("Task: reviewing, Next is Vote and publish", { change: reviewScreen({}, { seen: all }) }),
    scenario("Task: reviewing, Next is Next change", { change: reviewScreen({ voted: [48211] }, { seen: all }) }),
    scenario("Task: reviewing, the last change of the chain after the vote", { change: reviewScreen({ at: 48213, voted: [48211, 48212, 48213] }, { seen: all }) }),
    scenario("Task: reviewing an abandoned change, a draft stranded on it", { change: reviewScreen({}, { seen: all, abandoned: true, chains: [draft] }) }),
    scenario("Task: reviewing, a comment written on an earlier patchset", { change: reviewScreen({}, { chains: [older], seen: some }) }),
    scenario("Task: reviewing, all chains shown again", { change: reviewScreen({ all: true }, { seen: some }) }),
    scenario("Task: reviewing, the next change checking out", { change: reviewScreen({}, { seen: all }, "Checking out 48212… Go to Definition and edits in your files once it is checked out") }),
    scenario("Task: reworking, stopped to edit", { change: reworking("edit").change }),
    scenario("Task: reworking, stopped to edit, a comment to answer", { change: reworking("edit", { chains: [answer] }).change }),
    scenario("Task: reworking, stopped on a conflict", { change: reworking("conflict").change }),
    scenario("Task: reworking, the conflicts resolved", { change: reworking("resolved").change }),
    scenario("Task: reworking, Continue running in the terminal", { change: reworking("edit", { busy: "Continuing the rebase in the ger terminal…" }).change }),
    scenario("Task: reworking, nothing left", { change: reworking("edit", { nothingLeft: true }).change }),
    scenario("Task: reworking, all chains shown: Local stack without its banner", { stack: reworking("edit").stack }),
    scenario("Task: reworking, a fix saved and not amended at the stop", { change: reworking("edit", { nothingLeft: true, uncommitted: ["src/widget/routing/prefix_tree.py"] }).change }),
    scenario("Task: reworking, staged files Continue amends in, listed before it", {
      change: reworking("edit", { nothingLeft: true, uncommitted: ["src/widget/routing/prefix_tree.py"], staged: ["src/widget/routing/__init__.py", "tests/test_routing.py"] }).change,
    }),
    scenario("Task: reworking, only staged files at the stop", { change: reworking("edit", { nothingLeft: true, staged: ["src/widget/routing/__init__.py"] }).change }),
    scenario("Task: reworking, Continue refused, said in the header", {
      change: reworking("edit", { nothingLeft: true, uncommitted: ["src/widget/routing/prefix_tree.py"], outcome: { text: "ger rebase --continue did not run: you have unstaged changes to src/widget/routing/prefix_tree.py; amend them in (`git commit --amend -a`) or `git stash` them, then `ger rebase --continue`.", ok: false, more: [] } }).change,
    }),
    scenario("Task: reworking, a skip done, its outcome said", {
      change: reworking("edit", { nothingLeft: true, outcome: { text: "stopped at 3f9a2c1 Route by longest matching prefix", ok: true, more: ["dropped 9b1e0d4 Cache resolved routes; its change 48213 stays open on Gerrit", "to get it back: once the rebase is finished, git cherry-pick 9b1e0d4 (or `ger rebase --abort` now)"] } }).change,
    }),
  ];
}

/** Every built-in scenario, in the order the gallery shows them. */
export function scenarios(): Scenario[] {
  return [...taskScenarios(), ...stackScenarios(), ...changeScenarios(), ...bothScenarios()];
}
