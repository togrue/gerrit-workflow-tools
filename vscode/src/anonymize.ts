// Takes everything that comes from the repository or from Gerrit out of what the views show, for a
// report that may leave a secret project (src/report.ts). Default-deny: SHAPES says for every field
// of the view models what happens to it, and a field it does not name is replaced and its key
// renamed, so a field added next month is safe before anyone decides about it here.
//
// Kept: booleans, counts, and strings that are ours: one of a fixed set (icons, tones, states), or a
// sentence made of our words and counts only. Replaced: all other text, by filler words with the
// same length and line breaks (not a transformation of the original: nothing can be guessed back
// from it); paths, by made-up segments, as many and roughly as long; people, SHAs, Change-Ids,
// change numbers, line numbers, URLs and ids, by unrelated ones of the same shape. Within one report
// the same original gets the same replacement, from a table that lives only as long as the call.
//
// Pure: no `vscode` import, unit-tested (test/anonymize.test.ts).

import { randomInt } from "node:crypto";

import { commentPhrases } from "./commentState";
import { DISCARD, LOADING, NOTHING_FROM_GERRIT, NOTHING_SHOWN, NOT_FILES, NO_CI_LINK, READ_ONLY, REVIEW_UPDATE, STOPPED_WORDS, UNKNOWN_WORDS, YOURS } from "./changeRows";
import { MODES, MODE_ICONS, SIDE_WORDS } from "./compareRows";
import { Names, Random } from "./fakes";
import { ATTENTION_ICONS, FILE_STATES, REASON_WORDS } from "./format";
import { SECTION_TITLES, TREE_WORDS, allRowContexts } from "./rows";
import { MARKER_WORDS, MERGE_WORDS, REBASE_ACTIONS, REBASE_WORDS, REVIEW_FROZEN, STACK_GERRIT_WORDS, SYNC_ICONS, SYNC_WORDS } from "./stackRows";
import { PROBLEM_ACTIONS, PROBLEM_WORDS } from "./problemRows";
import { NOT_KNOWN, NOT_LISTED } from "./taskRows";

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

// --- What happens to a field -----------------------------------------------------------

/** What becomes of one value; `parent` is the object it is in (for an item of a list, the object the list is in). */
type Leaf = (value: unknown, names: Names, parent: Json) => unknown;
/** A field's treatment: a leaf, an object's fields by name, or a list of one shape. */
type Shape = Leaf | { readonly [key: string]: Shape } | readonly [Shape];

/** A value nothing was decided about (or of a type its field should not have): nothing of it is kept but its outline. */
function deny(value: unknown, names: Names): unknown {
  if (typeof value === "string") {
    return names.text(value);
  }
  if (typeof value === "number") {
    return Number.isInteger(value) && value >= 0 ? Number(names.number(String(value))) : 0;
  }
  if (typeof value === "boolean") {
    return false;
  }
  if (Array.isArray(value)) {
    return value.map((item) => deny(item, names));
  }
  if (isObject(value)) {
    // A key may be data too (a map by path): the keys go as well.
    return Object.fromEntries(Object.values(value).map((item, index) => [`unclassified${index + 1}`, deny(item, names)]));
  }
  return null;
}

/** A leaf for strings; any other type under it is denied. */
function string(replace: (text: string, names: Names, parent: Json) => string): Leaf {
  return (value, names, parent) => (typeof value === "string" ? replace(value, names, parent) : deny(value, names));
}

/** Kept as it is: a boolean, or a number that counts (comments, lines added, rows that can move). Never a string. */
const KEEP: Leaf = (value, names) => (typeof value === "boolean" || typeof value === "number" ? value : deny(value, names));
const TEXT = string((text, names) => names.text(text));
const PERSON = string((text, names) => names.person(text));
const SHA = string((text, names) => names.hex(text));
/** A short commit id, or `→ 2168` / `→ 1a2b3c4` (the commit a fixup folds into). */
const FIXUP_OR_SHA = string((text, names) => {
  const target = /^→ (.*)$/.exec(text)?.[1];
  return target === undefined ? names.hex(text) : `→ ${/^\d+$/.test(target) ? names.number(target) : names.hex(target)}`;
});
const CHANGE_ID = string((text, names) => names.changeId(text));
const KEY = string((text, names) => names.key(text));
/** The Change-Id beside the commit message: none stays none. */
const FOOTER_CHANGE_ID = string((text, names) => (text ? names.changeId(text) : ""));
const URL = string((text, names) => names.url(text));
const ID = string((text, names) => names.id(text));
const PATH = string((text, names) => (NOT_FILES.includes(text) ? text : names.path(text)));
/** A line in a file: where something is, not how many. */
const LINE: Leaf = (value, names) => (typeof value === "number" && Number.isInteger(value) && value > 0 ? Number(names.number(String(value))) : deny(value, names));

type Ours = string | RegExp | ((text: string) => boolean);

/**
 * A string of ours is kept: one of the fixed `known` strings, or one a pattern of our own words
 * and counts matches from start to end. Whatever else stands there goes to `otherwise`.
 */
function ours(known: readonly Ours[], otherwise: Leaf = TEXT): Leaf {
  const tests = known.map((one) => {
    if (typeof one === "string") {
      return (text: string) => text === one;
    }
    if (typeof one === "function") {
      return one;
    }
    const whole = new RegExp(`^(?:${one.source})$`, one.flags);
    return (text: string) => whole.test(text);
  });
  return (value, names, parent) => (typeof value === "string" && tests.some((test) => test(value)) ? value : otherwise(value, names, parent));
}

/** `leaf` for each part of a string that `separator` divides: lines, or what stands between ` · `. */
function each(separator: string, leaf: Leaf): Leaf {
  return (value, names, parent) =>
    typeof value === "string"
      ? value
          .split(separator)
          .map((part) => String(leaf(part, names, parent)))
          .join(separator)
      : deny(value, names);
}

/** Our words around something of theirs: `pattern` (start to end) catches theirs, `rebuild` writes the string anew around what it became. */
function around(pattern: RegExp, rebuild: (names: Names, ...theirs: string[]) => string, otherwise: Leaf = TEXT): Leaf {
  const whole = new RegExp(`^(?:${pattern.source})$`, pattern.flags);
  return (value, names, parent) => {
    const match = typeof value === "string" ? whole.exec(value) : null;
    return match ? rebuild(names, ...match.slice(1)) : otherwise(value, names, parent);
  };
}

// --- Our own words ---------------------------------------------------------------------

/** Every codicon a view model or a row of Review chains names. */
export const ICONS = [
  ...Object.values(SYNC_ICONS),
  ...ATTENTION_ICONS,
  ...["beaker", "check", "circle-large-outline", "circle-slash", "comment", "debug-continue", "debug-stackframe", "edit", "error", "eye", "flame", "git-branch", "git-commit", "history", "list-selection", "loading", "note", "multiple-windows", "person", "person-add", "question", "sync", "target", "thumbsdown", "thumbsup", "warning"],
  ...Object.values(MODE_ICONS),
];
const ICON = ours(ICONS);
const TONE = ours(["good", "bad", "warn", "info", "plain", "dim"]);

const COUNT = "\\d{1,6}";
const AGE = "\\d{1,5}[mhd]";
const PATCHSET = "\\d{1,4}";
const NOTE = "( on the last pushed patchset; Gerrit may not keep it for this code)?";
/** `V+1 CR-2 💬3 open ✎1`, `last pushed V+1`, `no votes` (format.ts, votes and commitVotes). */
const MARK = `(V|CR)[+-]?\\d|💬(${COUNT} open|all resolved)|✎${COUNT}`;
const VOTES = new RegExp(`(last pushed )?(no votes|${MARK})( (${MARK}))*`, "u");

/** `CI failed, unresolved comments`: attention reasons in our words (format.ts, reasonWords), as a sentence or not. */
const reasons = (text: string) => text.split(", ").every((word) => REASON_WORDS.some((known) => known.toLowerCase() === word.toLowerCase()));

/** What a view says in place of its content (state.ts, waitingFor). */
const WAITING: Shape = {
  text: ours(["Starting ger serve…", "Asking Gerrit…", /^Asking Gerrit… If it does not answer within \d{1,3} seconds, the views say so\.$/, /(Your local stack|A change) shows here once ger runs\. Review chains says what is missing and what to do next\./]),
  link: { label: ours(["Show Review Chains"]), command: ours(["ger.overview.focus"]) },
};

const READ_ONLY_WORDS = ours(["", ...Object.values(READ_ONLY)]);
const ORIGIN = ["introduced here", "also fails on the parent"];

/** One line of a tooltip: kept where it is a sentence of ours, filler where it has their words in it. */
const TOOLTIP_LINE = ours([
  ...Object.values(SYNC_WORDS),
  ...Object.values(MARKER_WORDS),
  MERGE_WORDS,
  ...ORIGIN,
  reasons,
  "no open issues",
  "Still to be replayed by the rebase.",
  "Held back by the ready boundary: ger push will not send it.",
  "It stays where it is: git rebase cannot replay a merge, so the newest merge and what is below it are not moved, folded or dropped.",
  REBASE_WORDS.tooltip,
  REBASE_WORDS.resolve,
  STOPPED_WORDS.notAmendedTooltip,
  STOPPED_WORDS.stagedTooltip,
  "The log was cut: findings may be incomplete.",
  new RegExp(`${COUNT} unresolved comments?`),
  new RegExp(`(${commentPhrases(COUNT).join("|")})( · (${commentPhrases(COUNT).join("|")}))*`),
  "No reviewers assigned: nobody is asked to review it",
  new RegExp(`(Code-Review|Verified) [+-]\\d${NOTE}`),
  new RegExp(`CI failed${NOTE}`),
]);
const TOOLTIP = each("\n", TOOLTIP_LINE);

const BADGE: Shape = {
  kind: ours(["comments", "review", "ci"]),
  icon: ICON,
  tone: TONE,
  /** A count of comments, or a vote. */
  text: ours([new RegExp(`([+-]?${COUNT})?`)]),
  title: TOOLTIP,
  stale: KEEP,
  /** The comment ring: a share and a shape, no words of anybody's. */
  ring: { share: KEEP, shape: ours(["solid", "dashed", "thin", "tick"]) },
};

/** A row's key is its Change-Id or its commit: it becomes what that field becomes. */
const ROW_KEY = string((text, names, row) => (text === row.changeId ? names.changeId(text) : names.key(text)));

/** A banner's words, or a line about the upstream ("<branch> has no upstream…", "Track <branch>", an error of git's: filler). */
const INFO_TEXT = ours([PROBLEM_WORDS.stack, STACK_GERRIT_WORDS.asking, STACK_GERRIT_WORDS.unknown, REBASE_WORDS.conflict, REBASE_WORDS.resolved, REBASE_WORDS.split, REBASE_WORDS.progress, "No local commits", "Choose another upstream branch…", "Choose an upstream branch…"]);

/** A rebase's buttons: Continue, Abort, Skip; and the ways on from an error state (problemRows.ts). */
const ACTIONS: Shape = [
  {
    label: ours([...Object.values(REBASE_ACTIONS), ...Object.values(PROBLEM_ACTIONS)].map((action) => action.label)),
    title: ours([...Object.values(REBASE_ACTIONS), ...Object.values(PROBLEM_ACTIONS)].map((action) => action.title)),
    command: { id: ours([...Object.values(REBASE_ACTIONS).map((action) => action.command), ...Object.values(PROBLEM_ACTIONS).map((action) => action.command.id)]), args: [TEXT] },
    primary: KEEP,
  },
];

/** A conflicted file's row (`conflictRow`), under Local stack's banner and in the Change view's Conflicts. */
const CONFLICT_ROW = {
  // It says its path: it becomes what its `conflict` becomes.
  text: string((text, names, info) => String((text === info.conflict ? PATH : INFO_TEXT)(text, names, info))),
  detail: ours([REBASE_WORDS.ready, REBASE_WORDS.onTop, REBASE_WORDS.asBefore, REBASE_WORDS.conflicted, STOPPED_WORDS.notAmended, STOPPED_WORDS.staged]),
  icon: ICON,
  tone: TONE,
  tooltip: TOOLTIP,
  conflict: PATH,
  /** An error of git's, under the sentence that says what state the view is in: filler. */
  raw: TEXT,
};

/** A change number or a commit id: what it became. */
const CHANGE_OR_SHA = string((text, names) => (/^\d+$/.test(text) ? names.number(text) : names.hex(text)));
const CHECKING_OUT = around(/Checking out (\d+)…(.*)/, (names, number, rest) => `Checking out ${names.number(number)}…${rest}`);

/** The task header (taskRows.ts): our words around their names, subjects and numbers. */
const TASK: Shape = {
  mode: ours(["review", "rework"]),
  heading: ours([/Reworking your chain/], around(/(Reviewing|Reworking your chain on) (.+?)('s chain)?/, (names, verb, who, tail) => `${verb} ${tail ? names.person(who) : names.text(who)}${tail ?? ""}`)),
  title: TEXT,
  position: ours([new RegExp(`change ${COUNT} of ${COUNT}`), new RegExp(`between changes, ${COUNT} commits? to come`)]),
  left: each(
    " · ",
    ours([
      new RegExp(`${COUNT} of ${COUNT} to vote on`),
      new RegExp(`you voted on ${COUNT} of ${COUNT}`),
      new RegExp(`${COUNT} not ready for review`),
      new RegExp(`${COUNT} comments? to answer`),
      new RegExp(`${COUNT} ${NOT_LISTED}`),
      `what is left is ${NOT_KNOWN}`,
      new RegExp(`CI failed on ${COUNT} commits?`),
      new RegExp(`${COUNT} commits? needs? edits`),
      "its chain is not in your inbox",
      "nothing left to answer or fix",
      new RegExp(`${COUNT} conflicts? to resolve`),
      new RegExp(`${COUNT} commits? to replay`),
    ], around(/(.+) not amended/, (names, files) => `${names.text(files)} not amended`)),
  ),
  steps: [
    {
      key: CHANGE_OR_SHA,
      position: KEEP,
      title: TEXT,
      current: KEEP,
      phase: ours(["done", "current", "todo"]),
      marks: [
        {
          icon: ICON,
          text: ours([NOT_LISTED, NOT_KNOWN, "reviewing", "to review", "CI failed", "your drafts", "waits on a reply", "replayed", "stopped here", "to come", "needs edits", new RegExp(`you voted [+-]${COUNT}`), new RegExp(`${COUNT} to answer`)], around(/not ready: (.+)/, (names, why) => `not ready: ${names.text(why)}`)),
          tone: TONE,
          ring: { share: KEEP, shape: ours(["solid", "dashed", "thin", "tick"]) },
        },
      ],
      /** The subject and the marks: filler. */
      tooltip: TEXT,
    },
  ],
  actions: [
    {
      label: ours(["Leave review", "Continue", "Abort", "Skip"]),
      detail: ours([/commit this stop as it is.*/, /once the .* resolved/, "the step before it is still running", "put the branch back as it was before the rebase", "leave this commit out and go on", "end the review; its branches go"], around(/back to (.+); the review's branches go/, (names, branch) => `back to ${names.text(branch)}; the review's branches go`)),
      command: ours(["ger.reviewDone", "ger.rebase.continue", "ger.rebase.abort", "ger.rebase.skip"]),
      /** Core's words about this repository: filler. */
      notNow: TEXT,
      tooltip: TEXT,
      primary: KEEP,
    },
  ],
  views: { label: ours(["All chains", "Only this task"]), detail: ours([/keep going; show the inbox and Local stack too/, /hide the inbox and Local stack again/]), command: ours(["ger.showAllChains", "ger.focusReview"]) },
  busy: ours([/(Continuing the rebase|Aborting the rebase|Skipping the commit) in the ger terminal…/], CHECKING_OUT),
  /** ger's closing words about this repository (commits, branches, files): filler. */
  outcome: { text: TEXT, ok: KEEP, more: [TEXT] },
};

const STACK: Shape = {
  task: TASK,
  waiting: WAITING,
  info: [
    {
      ...CONFLICT_ROW,
      command: { id: ours(["ger.setUpstream"]), args: [TEXT] },
      actions: ACTIONS,
    },
  ],
  rows: [
    {
      key: ROW_KEY,
      sha: SHA,
      shortSha: SHA,
      summary: TEXT,
      changeId: CHANGE_ID,
      url: URL,
      sync: ours(Object.keys(SYNC_ICONS)),
      syncIcon: ICON,
      syncTitle: ours(Object.values(SYNC_WORDS)),
      marker: ours(["rebase", "conflict"]),
      dim: KEEP,
      merge: KEEP,
      pinned: KEEP,
      /** Our words, and the short commit id. */
      /** Our words, the short commit id, and the commit a fixup folds into (its change number or short id). */
      notes: [ours(["HEAD", "under review", "merge", "code changed", "held back", "push adds a Change-Id", "abandoned", new RegExp(`${COUNT} fixups? pending`)], FIXUP_OR_SHA)],
      badges: [BADGE],
      tooltip: TOOLTIP,
      squash: KEEP,
    },
  ],
  frozen: ours(["A rebase is in progress: finish it (Continue) or abort it (Abort) before you move commits.", REVIEW_FROZEN]),
  /** How many rows can move. */
  movable: KEEP,
  /** A staged review: whether the rows are somebody else's. */
  review: KEEP,
};

/** `Local commit 1a2b3c4`, `Patchset 3 (current)`, `Base commit`: a side From or To names. A patchset's number says how many there were, as a count does. */
const SIDE_LABEL = around(/Local commit ([0-9a-f]+)/, (names, sha) => `Local commit ${names.hex(sha)}`, ours([new RegExp(`Patchset ${PATCHSET}( \\((current|you reviewed it|current, you reviewed it)\\))?`), ...Object.values(SIDE_WORDS).filter((words) => typeof words === "string"), new RegExp(`Gerrit's patchset ${PATCHSET} \\(current\\)`)]));
const SIDE = ours([new RegExp(`base|local|current|reviewed|${PATCHSET}`)]);
const PICKER: Shape = { options: [{ value: SIDE, label: SIDE_LABEL, disabled: KEEP }], selected: SIDE };
const MODE = ours(Object.keys(MODES));
const MODE_WORDS = `(${Object.values(MODES).join("|")})`;

/** A file's name and folder as its row shows them: those of what its path became. */
const FILE_NAME = string((text, names, row) => (typeof row.path === "string" && row.path.split("/").at(-1) === text ? (names.path(row.path).split("/").at(-1) ?? "") : names.fileName(text)));
const FILE_DIR = string((text, names, row) => (typeof row.path === "string" && row.path.split("/").slice(0, -1).join("/") === text ? names.path(row.path).split("/").slice(0, -1).join("/") : names.path(text)));

/** A file's name, with the line when there is one: where a comment is. */
const WHERE = around(/(.*?)(?::(\d+))?/, (names, file, line) => `${names.fileName(file)}${line ? `:${names.number(line)}` : ""}`);
/** Under a comment: our words, then where it is, then who wrote it. */
const THREAD_NOTE = ours([...Object.values(YOURS), "kept for you", "your draft resolves it", "waits on a reply", "whole change", "outdated: changed since", "suggested edit"], (value, names, row) => (value === row.author ? PERSON(value, names, row) : WHERE(value, names, row)));
const WHO = ours(["you", "?", ""], PERSON);

/** Under a CI finding: the tool that found it (theirs), our words, and the file and line it stands in. */
const FINDING_NOTE = ours(["warning", ...ORIGIN], (value, names, row) => {
  if (typeof value === "string" && typeof row.path === "string" && (value === row.path || value === `${row.path}:${String(row.line)}`)) {
    return `${names.path(row.path)}${value === row.path ? "" : `:${names.number(String(row.line))}`}`;
  }
  return TEXT(value, names, row);
});
const FINDING: Shape = { icon: ICON, tone: TONE, text: TEXT, notes: [FINDING_NOTE], tooltip: TOOLTIP, path: PATH, line: LINE };

const CHANGE: Shape = {
  task: TASK,
  busy: CHECKING_OUT,
  waiting: WAITING,
  /** `Loading <subject>…` has the subject in it: filler. */
  empty: ours([NOTHING_SHOWN]),
  /** Our sentence, the error as git or Gerrit worded it (filler), the ways on. */
  problem: { text: ours([PROBLEM_WORDS.change]), raw: TEXT, actions: ACTIONS },
  key: KEY,
  loading: { message: KEEP, readOnly: READ_ONLY_WORDS, plain: KEEP },
  head: {
    icon: ICON,
    tone: TONE,
    text: ours(
      [reasons, "Ready to submit", "Loading…", "Nothing to do", NOTHING_FROM_GERRIT, STOPPED_WORDS.resolved, new RegExp(STOPPED_WORDS.conflicts(0).replace("0", COUNT))],
      around(/Conflict in (.+) — resolve it, then Continue/, (names, file) => STOPPED_WORDS.conflict(names.path(file))),
    ),
    detail: ours([VOTES, "not on Gerrit"]),
    badges: [BADGE],
    actions: ACTIONS,
  },
  conflicts: { title: ours([STOPPED_WORDS.notAmendedTitle, STOPPED_WORDS.stagedTitle, STOPPED_WORDS.bothTitle]), summary: ours([new RegExp(`${COUNT} files?`)]), rows: [CONFLICT_ROW] },
  /** Next (nextAction.ts): our names for it, a conflicted file's path made up; why, with subjects and paths in it: filler. */
  next: {
    label: ours(
      [
        "Next comment",
        "Next file",
        new RegExp(`Mark ${COUNT} files? seen`),
        "Vote and publish",
        "Next change",
        "Leave review",
        new RegExp(`Update to patchset ${PATCHSET}`),
        "Update to the newest patchset",
        "Continue",
        "Work on next",
        "Finish the rebase",
        "Push",
        "Review change",
      ],
      around(/Resolve (.+)/, (names, file) => `Resolve ${names.path(file)}`),
    ),
    title: TEXT,
  },
  notes: [
    {
      icon: ICON,
      tone: TONE,
      text: ours(
        [
          ...["This commit is ahead of Gerrit", "Gerrit has a newer patchset than this commit", "Gerrit has a newer patchset than the one checked out"],
          ...["This change is abandoned: Gerrit takes no vote and no comment on it", "This change is merged: Gerrit takes no vote and no comment on it", UNKNOWN_WORDS.note, "Under review in its own window", "Uncommitted changes here"],
        ],
      ),
      raw: TEXT,
      detail: ours(
        [
          ...Object.values(SYNC_WORDS),
          "comments and CI below are about Gerrit's version",
          "a review in this checkout needs them committed or stashed; Review Change offers the review worktree instead",
          "your draft stays a draft: publishable once the change is restored, or delete it",
          new RegExp(`your ${COUNT} drafts stay drafts: publishable once the change is restored, or delete them`),
          "the review goes on there: Go to Review Window",
          "",
        ],
      ),
      /** Update to patchset N, in a staged review behind Gerrit. */
      actions: [
        {
          label: ours([new RegExp(`Update to patchset ${PATCHSET}`), "Update to the newest patchset", ...Object.values(PROBLEM_ACTIONS).map((action) => action.label)]),
          title: ours([REVIEW_UPDATE.title, ...Object.values(PROBLEM_ACTIONS).map((action) => action.title)]),
          command: { id: ours([REVIEW_UPDATE.command, ...Object.values(PROBLEM_ACTIONS).map((action) => action.command.id)]), args: [TEXT] },
          primary: KEEP,
        },
      ],
    },
  ],
  message: { key: KEY, text: TEXT, changeId: FOOTER_CHANGE_ID, author: PERSON, readOnly: READ_ONLY_WORDS, plain: KEEP },
  compare: {
    from: PICKER,
    to: PICKER,
    mode: {
      mode: MODE,
      icon: ICON,
      next: MODE,
      active: KEEP,
      title: ours([new RegExp(`Default comparison: ${MODE_WORDS}( — (not possible for this change|From and To picked by hand for this change))?\\. Click for ${MODE_WORDS}\\.`)]),
    },
  },
  hint: {
    text: ours([new RegExp(`Since your review: patchset ${PATCHSET} → ${PATCHSET} · ${COUNT} files?( · rebased)?`)]),
    show: KEEP,
  },
  files: {
    title: ours(["Files", STOPPED_WORDS.files, "Gerrit vs. local", "Since your review", "Compared", new RegExp(`(Gerrit vs\\. local|Since your review|Compared): patchset ${PATCHSET} → (patchset ${PATCHSET}|local)( \\(rebased\\))?`)]),
    summary: ours([
      new RegExp(`(${COUNT} of )?${COUNT} files?( seen)?`),
      LOADING,
      "could not be compared",
      "you have not reviewed this change yet",
      "the commit is not available here",
      new RegExp(`nothing new: you reviewed patchset ${PATCHSET}`),
      new RegExp(`identical to patchset ${PATCHSET}( — nothing to push)?`),
    ]),
    markAll: ours(["Mark all seen"]),
    /** Lines added and removed, in all. */
    added: KEEP,
    deleted: KEEP,
    note: ours([
      "",
      LOADING,
      "could not be compared",
      "you have not reviewed this change yet",
      "the commit is not available here",
      new RegExp(`nothing new: you reviewed patchset ${PATCHSET}`),
      new RegExp(`identical to patchset ${PATCHSET}( — nothing to push)?`),
    ]),
    tooltip: ours([
      "What the change does: its files against the parent commit.",
      STOPPED_WORDS.filesTooltip,
      "The two versions sit on different parents. Only files the change touches are listed; inside them, edits that came in from underneath show too.",
      "What your commit changes compared with the patchset Gerrit has: what a push would upload on top.",
      "What changed after the patchset you last voted or commented on.",
      "What changed from the version under From to the one under To.",
    ]),
    rows: [
      {
        path: PATH,
        name: FILE_NAME,
        dir: FILE_DIR,
        /** Git's status in a word; a letter this does not know is passed on as it came. */
        state: ours(Object.values(FILE_STATES)),
        /** Lines added and removed in this file. */
        added: KEEP,
        deleted: KEEP,
        /** The path, or `old → new` for a rename. */
        tooltip: each(" → ", PATH),
        seen: KEEP,
      },
    ],
  },
  comments: {
    summary: each(
      " · ",
      ours([
        LOADING,
        "none",
        UNKNOWN_WORDS.comments,
        new RegExp(`${COUNT} to answer(, ${COUNT} gone to)?`),
        new RegExp(`${COUNT} answered by your drafts \\((push publishes them|Vote and Publish sends them)\\)`),
        new RegExp(`${COUNT} waiting on a reply`),
        new RegExp(`all ${COUNT} resolved`),
        new RegExp(`${COUNT} of yours, not published`),
      ]),
    ),
    open: KEEP,
    threads: [
      {
        icon: ICON,
        tone: TONE,
        id: ID,
        text: TEXT,
        body: TEXT,
        author: WHO,
        file: KEEP,
        notes: [THREAD_NOTE],
        /** Who wrote what, comment by comment. */
        tooltip: TEXT,
        reply: KEEP,
        open: KEEP,
        waiting: KEEP,
        held: KEEP,
        resolved: KEEP,
        resolving: KEEP,
        draft: KEEP,
        edit: KEEP,
        discard: ours(["", "Delete Your Draft Reply…", ...Object.values(DISCARD)]),
        apply: KEEP,
        replies: [{ author: WHO, state: ours(["", "draft", "local-draft", "note"]), text: TEXT }],
      },
    ],
  },
  ci: {
    /** The names of the builds that failed. */
    summary: TEXT,
    state: ours(["closed", "loading", "error", "loaded"]),
    error: TEXT,
    noLinks: ours(["", NO_CI_LINK]),
    links: [
      {
        icon: ICON,
        tone: TONE,
        /** The build's name. */
        text: TEXT,
        /** `could not be read: <why>` has the CI server's words in it: filler. */
        detail: ours([new RegExp(`${COUNT} findings?( · ${COUNT} warnings?)?`)]),
        url: URL,
        /** The build's URL, and whether its log was cut. */
        tooltip: each("\n", ours(["The log was cut: findings may be incomplete."], URL)),
        findings: [FINDING],
        warnings: [FINDING],
      },
    ],
  },
};

/** One part of a Review chains row's description: ours, else a change number or the owner's name. */
const CHAIN_NOTE = ours(
  [
    "● new activity",
    "in your stack",
    "newest first",
    "oldest first",
    VOTES,
    new RegExp(`${COUNT} (changes?|chains?|with failed CI|with comments)`),
    // How long it waits: a duration, as the tree shows it.
    new RegExp(`waiting ${AGE}|active ${AGE} ago`),
  ],
  string((text, names) => (/^\d+$/.test(text) ? names.number(text) : names.person(text))),
);

/** A row of Review chains (report.ts, TreeRow): its label is a subject, or the headline with one in it. */
const TREE_ROW: { [key: string]: Shape } = {
  label: ours([...Object.values(SECTION_TITLES), ...Object.values(TREE_WORDS)]),
  description: each(" · ", CHAIN_NOTE),
  icon: ICON,
  contextValue: ours(allRowContexts()),
};
TREE_ROW.children = [TREE_ROW];

const SHAPES = { stack: STACK, change: CHANGE, chains: [TREE_ROW] as Shape };

// --- The walk --------------------------------------------------------------------------

/** What the views showed, with nothing of the repository or of Gerrit left in it. */
export interface Anonymized {
  stack: unknown;
  change: unknown;
  chains: unknown;
  /**
   * Fields SHAPES does not name, as `change.files.rows[].mode`: they were replaced. For the test
   * that makes a new field a decision; not for a report, as a key may be data.
   */
  unclassified: string[];
}

/**
 * `views` (the view models of Local stack and Change, the rows of Review chains; null for one that
 * is not there) as a report may carry them. One call is one report: the same original becomes the
 * same replacement within it, and something else in the next.
 */
export function anonymize(views: { stack: unknown; change: unknown; chains: unknown }, random: Random = randomInt): Anonymized {
  const names = new Names(random);
  const unclassified = new Set<string>();
  const walk = (value: unknown, shape: Shape, path: string, parent: Json): unknown => {
    if (value === null || value === undefined) {
      return value ?? null;
    }
    if (typeof shape === "function") {
      return shape(value, names, parent);
    }
    if (Array.isArray(shape)) {
      return Array.isArray(value) ? value.map((item) => walk(item, shape[0], `${path}[]`, parent)) : deny(value, names);
    }
    if (!isObject(value)) {
      return deny(value, names);
    }
    const fields = shape as { readonly [key: string]: Shape };
    const out: Json = {};
    let unknown = 0;
    for (const [key, item] of Object.entries(value)) {
      if (item === undefined) {
        continue; // JSON has no undefined: the key would not be in the file either
      }
      if (Object.hasOwn(fields, key)) {
        out[key] = walk(item, fields[key], `${path}.${key}`, value);
      } else {
        unclassified.add(`${path}.${key}`);
        out[`unclassified${++unknown}`] = deny(item, names);
      }
    }
    return out;
  };
  return {
    stack: walk(views.stack, SHAPES.stack, "stack", {}),
    change: walk(views.change, SHAPES.change, "change", {}),
    chains: walk(views.chains, SHAPES.chains, "chains", {}),
    unclassified: [...unclassified].sort(),
  };
}
