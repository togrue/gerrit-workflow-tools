// The gallery's built-in scenarios: the states of the two webview views that matter for layout,
// each a snapshot (src/snapshot.ts). Every view model comes from the real `stackVm` / `changeVm`
// on what `ger serve` would answer (test/fixtures.ts), so a scenario cannot show what the
// extension would never draw.

import { createHash } from "node:crypto";

import { CommentChain, MoveConflict, ShownCommit, Stack, StackCommit } from "../../src/api";
import { ChangeState, ChangeVm, changeVm } from "../../src/changeRows";
import { SNAPSHOT_FORMAT, Snapshot } from "../../src/snapshot";
import { StackVm, stackVm } from "../../src/stackRows";
import { waitingFor } from "../../src/state";
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
    pushed("Route by longest matching prefix", { patchset_status: "newer", attention_reasons: ["ahead-of-gerrit"] }),
    pushed("Cache resolved routes", { patchset_status: "outdated", attention_reasons: ["outdated-patchset"] }),
    pushed("Evict stale routes", { patchset_status: "newer", diverged: true, attention_reasons: ["ahead-of-gerrit"] }),
    pushed("Log route misses", { patchset_status: "unknown", attention_reasons: [] }),
    unpushed("Document the routing table", { is_head: true }),
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
    pushed("Votes of the last pushed patchset", { patchset_status: "newer", last_pushed_votes: { verified: -1, code_review: 2 }, comments_unresolved: 1, attention_reasons: ["ahead-of-gerrit"] }),
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

function rebasing(applied: boolean): StackVm {
  return stackOf(
    [
      pushed("Add the routing table", { rebase: "done", code_review: 2, verified: 1 }),
      pushed("Extract routing into its own module", { rebase: "done", comments_unresolved: 2, attention_reasons: ["unresolved-comments"] }),
      pushed("Route by longest matching prefix", { rebase: "current", is_head: true, patchset_status: "newer", attention_reasons: ["ahead-of-gerrit"] }),
      pushed("Cache resolved routes", { rebase: "todo", code_review: -1, attention_reasons: ["review-issues"] }),
      unpushed("Evict stale routes", { rebase: "todo" }),
    ],
    { rebase_in_progress: true, rebase: { applied } },
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
    scenario("Stack: HEAD, held back, a merge, abandoned", { stack: rowKinds() }),
    scenario("Stack: a rebase in progress", { stack: rebasing(true) }),
    scenario("Stack: a rebase stopped on a conflict", { stack: rebasing(false) }),
    scenario("Stack: long subjects", { stack: longSubjects() }),
    scenario("Stack: neighbours that cannot swap (hover the rows)", { stack: swapConflict() }),
    scenario("Stack: no upstream, one to track", {
      stack: stackVm(NO_UPSTREAM, { branch: "change-48211", upstream: null, suggested: "origin/release-2.4", why: "the remote branch nearest to it", candidates: ["origin/release-2.4", "origin/main"] }, never),
    }),
    scenario("Stack: no upstream, none to suggest", { stack: stackVm(NO_UPSTREAM, { branch: "change-48211", upstream: null, suggested: null, why: null, candidates: [] }, never) }),
    scenario("Stack: it could not be read", { stack: stackVm({ error: { code: "GIT", message: "fatal: bad revision 'origin/main..HEAD'\nthe upstream branch is gone" } }, undefined, never) }),
    scenario("Stack: no local commits", { stack: stackOf([]) }),
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

const theChange = (extra: Partial<ShownCommit> = {}): ShownCommit => shown({ ...pushed(SUBJECT), ...extra });
const itsDiff = (extra: Parameters<typeof diff>[0] = {}) => diff({ message: MESSAGE, author: "Dana Dietrich <dana@example.org>", files: FILES, ...extra });
const FIVE = { ...patchsets, number: 48211, current: 5, reviewed: 2, patchsets: [1, 2, 3, 4, 5].map((number) => ({ number, commit: String(number).repeat(40), created: null })) };

/** The Change view of a commit of your stack with everything arrived, unless `extra` says otherwise. */
const change = (extra: Partial<ChangeState> = {}): ChangeVm => changeVm(state({ shown: theChange(), base: itsDiff(), patchsets: FIVE, ...extra }));

const REMOTE = "Only a commit of your local stack can be reworded here.";
const since = { patch_set: 2, current_patch_set: 5, to_patch_set: null, up_to_date: false, rebased: false };

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
    scenario("Change: not on Gerrit yet", { change: change({ shown: shown(unpushed(SUBJECT)), patchsets: undefined }) }),
    scenario("Change: ahead of Gerrit, compared with it", {
      change: change({ shown: theChange({ patchset_status: "newer", last_pushed_votes: { verified: 1, code_review: 2 }, attention_reasons: ["ahead-of-gerrit"] }), mode: "gerrit", delta: itsDiff({ files: FILES.slice(3, 6), since: { ...since, patch_set: 5 } }) }),
    }),
    scenario("Change: Gerrit has a newer patchset", { change: change({ shown: theChange({ patchset_status: "outdated", attention_reasons: ["outdated-patchset"] }) }) }),
    scenario("Change: someone's change, new since your review", {
      change: change({ local: false, shown: theChange({ local_commit: false, code_review: 1 }), version: "current", readOnly: REMOTE, delta: itsDiff({ files: FILES.slice(4, 6), since: { ...since, to_patch_set: 5 } }) }),
    }),
    scenario("Change: someone's change since your review, rebased", {
      change: change({ local: false, shown: theChange({ local_commit: false }), version: "current", mode: "reviewed", readOnly: REMOTE, delta: itsDiff({ files: FILES.slice(4, 6), since: { ...since, to_patch_set: 5, rebased: true } }) }),
    }),
    scenario("Change: comments as they arrive", { change: commented() }),
    scenario("Change: comments opened in full", { change: commented(), click: [".thread .twist"] }),
    scenario("Change: CI failed, findings not fetched", { change: change({ shown: theChange(FAILING) }) }),
    scenario("Change: CI findings being fetched", { change: change({ shown: theChange(FAILING), ci: { state: "loading" } }) }),
    scenario("Change: CI findings, introduced and inherited", { change: ciLoaded(new Set(["e1", "e2", "w1", "w2"])), click: [".finding[aria-expanded]"] }),
    scenario("Change: CI findings, their origin unknown", { change: ciLoaded(null) }),
    scenario("Change: CI findings could not be fetched", { change: change({ shown: theChange(FAILING), ci: { state: "error", error: "Gerrit unreachable: connection refused (gerrit.example.org:443)" } }) }),
    scenario("Change: CI failed without a link to its log", { change: change({ shown: theChange({ ...FAILING, ci_links: [] }), ci: { state: "loaded", contents: [], introduced: null } }) }),
    scenario("Change: loading, drawn from the clicked stack row", { change: change({ shown: theChange({ attention_reasons: [] }), base: undefined, patchsets: undefined, loading: fromRow }) }),
    scenario("Change: loading someone's change", {
      change: change({ local: false, shown: theChange({ local_commit: false, comments_unresolved: 2, attention_reasons: ["unresolved-comments"] }), base: undefined, patchsets: undefined, version: "current", readOnly: REMOTE, loading: fromRow }),
    }),
    scenario("Change: it could not be shown", { change: { problem: "ger show failed: the commit 3f9a2c1 is not in this repository (was it dropped by a rebase?)" } }),
  ];
}

// --- Both views ----------------------------------------------------------------------

function bothScenarios(): Scenario[] {
  const nothing = "Select a change in Review chains or a commit in Local stack to see its commit message, files, comments and CI findings.";
  // As the two views say it while there is no overview (stackView.ts, changeView.ts).
  const noStack = stackVm(undefined, undefined, never);
  return [
    scenario("Both: a stack and its selected commit", { stack: syncStates(), change: commented() }),
    scenario("Both: ger serve is starting", { stack: { ...noStack, waiting: waitingFor("starting", "Your local stack") }, change: { waiting: waitingFor("starting", "A change") } }),
    scenario("Both: ger is not set up", { stack: { ...noStack, waiting: waitingFor("setup", "Your local stack") }, change: { waiting: waitingFor("setup", "A change") } }),
    scenario("Both: nothing to show", { stack: noStack, change: { empty: nothing } }),
  ];
}

/** Every built-in scenario, in the order the gallery shows them. */
export function scenarios(): Scenario[] {
  return [...stackScenarios(), ...changeScenarios(), ...bothScenarios()];
}
