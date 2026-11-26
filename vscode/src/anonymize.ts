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
import { DISCARD, LOADING, MODES, NOTHING_SHOWN, NOT_FILES, NO_CI_LINK, READ_ONLY, REVIEW_UPDATE, STOPPED_WORDS, YOURS } from "./changeRows";
import { Names, Random } from "./fakes";
import { ATTENTION_ICONS, FILE_STATES, REASON_WORDS } from "./format";
import { SECTION_TITLES, TREE_WORDS, allRowContexts } from "./rows";
import { MARKER_WORDS, MERGE_WORDS, REBASE_ACTIONS, REBASE_WORDS, REVIEW_FROZEN, SYNC_ICONS, SYNC_WORDS } from "./stackRows";

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
  ...["beaker", "debug-continue", "edit", "eye", "flame", "git-branch", "history", "list-selection", "loading", "note", "person", "person-add", "target", "thumbsdown", "thumbsup"],
];
const ICON = ours(ICONS);
const TONE = ours(["good", "bad", "warn", "info", "plain", "dim"]);

const COUNT = "\\d{1,6}";
const AGE = "\\d{1,5}[mhd]";
const PATCHSET = "\\d{1,4}";
const NOTE = "( on the last pushed patchset; Gerrit may not keep it for this code)?";
/** `V+1 CR-2 💬3`, `last pushed V+1`, `no votes` (format.ts, votes and commitVotes). */
const VOTES = new RegExp(`(last pushed )?(no votes|(V|CR)[+-]?\\d|💬${COUNT})( ((V|CR)[+-]?\\d|💬${COUNT}))*`, "u");

/** `CI failed, unresolved comments`: attention reasons in our words (format.ts, reasonWords), as a sentence or not. */
const reasons = (text: string) => text.split(", ").every((word) => REASON_WORDS.some((known) => known.toLowerCase() === word.toLowerCase()));

/** What a view says in place of its content (state.ts, waitingFor). */
const WAITING: Shape = {
  text: ours(["Starting ger serve…", /(Your local stack|A change) shows here once ger runs\. Review chains says what is missing and what to do next\./]),
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
  text: ours([new RegExp(`([+-]?${COUNT})?`), new RegExp(`${COUNT}/${COUNT}`)]),
  title: TOOLTIP,
  stale: KEEP,
};

/** A row's key is its Change-Id or its commit: it becomes what that field becomes. */
const ROW_KEY = string((text, names, row) => (text === row.changeId ? names.changeId(text) : names.key(text)));

/** A banner's words, or a line about the upstream ("<branch> has no upstream…", "Track <branch>", an error of git's: filler). */
const INFO_TEXT = ours([REBASE_WORDS.conflict, REBASE_WORDS.resolved, REBASE_WORDS.split, REBASE_WORDS.progress, "No local commits", "Choose another upstream branch…", "Choose an upstream branch…"]);

/** A rebase's buttons: Continue, Abort, Skip. */
const ACTIONS: Shape = [
  {
    label: ours(Object.values(REBASE_ACTIONS).map((action) => action.label)),
    title: ours(Object.values(REBASE_ACTIONS).map((action) => action.title)),
    command: { id: ours(Object.values(REBASE_ACTIONS).map((action) => action.command)), args: [TEXT] },
    primary: KEEP,
  },
];

/** A conflicted file's row (`conflictRow`), under Local stack's banner and in the Change view's Conflicts. */
const CONFLICT_ROW = {
  // It says its path: it becomes what its `conflict` becomes.
  text: string((text, names, info) => String((text === info.conflict ? PATH : INFO_TEXT)(text, names, info))),
  detail: ours([REBASE_WORDS.ready, REBASE_WORDS.onTop, REBASE_WORDS.asBefore, REBASE_WORDS.conflicted]),
  icon: ICON,
  tone: TONE,
  tooltip: TOOLTIP,
  conflict: PATH,
};

const STACK: Shape = {
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
      notes: [ours(["HEAD", "under review", "merge", "code changed", "held back", "abandoned", new RegExp(`${COUNT} fixups? pending`)], FIXUP_OR_SHA)],
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

/** `Local commit 1a2b3c4`, `Patchset 3 (current)`: which version is shown. A patchset's number says how many there were, as a count does. */
const VERSION_LABEL = around(/Local commit ([0-9a-f]+)/, (names, sha) => `Local commit ${names.hex(sha)}`, ours([new RegExp(`Patchset ${PATCHSET}( \\((current|you reviewed it|current, you reviewed it)\\))?`), "Gerrit's current patchset"]));
const VERSION = ours([new RegExp(`local|current|${PATCHSET}`)]);
const MODE = ours(Object.keys(MODES));

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
  waiting: WAITING,
  /** `Loading <subject>…` has the subject in it: filler. */
  empty: ours([NOTHING_SHOWN]),
  /** An error as git or Gerrit worded it. */
  problem: TEXT,
  key: KEY,
  loading: { message: KEEP, readOnly: READ_ONLY_WORDS, plain: KEEP },
  head: {
    icon: ICON,
    tone: TONE,
    text: ours(
      [reasons, "Ready to submit", "Loading…", "Nothing to do", STOPPED_WORDS.resolved, new RegExp(STOPPED_WORDS.conflicts(0).replace("0", COUNT))],
      around(/Conflict in (.+) — resolve it, then Continue/, (names, file) => STOPPED_WORDS.conflict(names.path(file))),
    ),
    detail: ours([VOTES, "not on Gerrit"]),
    badges: [BADGE],
    actions: ACTIONS,
  },
  conflicts: { summary: ours([new RegExp(`${COUNT} files?`)]), rows: [CONFLICT_ROW] },
  notes: [
    {
      icon: ICON,
      tone: TONE,
      text: ours(["This commit is ahead of Gerrit", "Gerrit has a newer patchset than this commit", "Gerrit has a newer patchset than the one checked out"]),
      detail: ours([...Object.values(SYNC_WORDS), "comments and CI below are about Gerrit's version"]),
      /** Update to patchset N, in a staged review behind Gerrit. */
      actions: [
        {
          label: ours([new RegExp(`Update to patchset ${PATCHSET}`), "Update to the newest patchset"]),
          title: ours([REVIEW_UPDATE.title]),
          command: { id: ours([REVIEW_UPDATE.command]), args: [TEXT] },
          primary: KEEP,
        },
      ],
    },
  ],
  message: { key: KEY, text: TEXT, changeId: FOOTER_CHANGE_ID, author: PERSON, readOnly: READ_ONLY_WORDS, plain: KEEP },
  versions: { options: [{ value: VERSION, label: VERSION_LABEL, disabled: KEEP }], selected: VERSION },
  modes: { options: [{ value: MODE, label: ours([...Object.values(MODES), `${MODES.reviewed} (you have not reviewed it)`]), disabled: KEEP }], selected: MODE },
  hint: {
    text: ours([new RegExp(`Nothing new since your review of patchset ${PATCHSET}`), new RegExp(`Since your review: patchset ${PATCHSET} → ${PATCHSET} · ${COUNT} files?( · rebased)?`)]),
    show: KEEP,
  },
  files: {
    title: ours(["Files", STOPPED_WORDS.files, "Gerrit vs. local", "Since your review", new RegExp(`(Gerrit vs\\. local|Since your review): patchset ${PATCHSET} → (patchset ${PATCHSET}|local)( \\(rebased\\))?`)]),
    summary: ours([new RegExp(`${COUNT} files?`), LOADING, "could not be compared", "you have not reviewed this change yet", "the commit is not available here", new RegExp(`nothing new: you reviewed patchset ${PATCHSET}`)]),
    /** Lines added and removed, in all. */
    added: KEEP,
    deleted: KEEP,
    note: ours(["", LOADING, "could not be compared", "you have not reviewed this change yet", "the commit is not available here", new RegExp(`nothing new: you reviewed patchset ${PATCHSET}`)]),
    tooltip: ours([
      "What the change does: its files against the parent commit.",
      STOPPED_WORDS.filesTooltip,
      "The two versions sit on different parents. Only files the change touches are listed; inside them, edits that came in from underneath show too.",
      "What your commit changes compared with the patchset Gerrit has: what a push would upload on top.",
      "What changed after the patchset you last voted or commented on.",
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
      },
    ],
  },
  comments: {
    summary: each(
      " · ",
      ours([
        LOADING,
        "none",
        new RegExp(`${COUNT} unresolved of ${COUNT}`),
        new RegExp(`${COUNT} to answer`),
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
