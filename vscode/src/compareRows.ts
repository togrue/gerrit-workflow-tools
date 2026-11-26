// What the Change view compares: the From and To pickers, the default mode and its toggle, and
// what the files compared are called. Pure: no `vscode`, no I/O; changeRows.ts puts it in the view.

import { Patchsets, ShownCommit } from "./api";

/** The newer side, the version looked at ("To"): your commit, Gerrit's current patchset, or patchset `n`. */
export type Version = "local" | "current" | number;
/** The older side, what it is compared with ("From"): its base commit, the patchset you last reviewed, Gerrit's current one, or patchset `n`. */
export type From = "base" | "reviewed" | "current" | number;
/** The default comparison, remembered across changes: what a change opened next starts on. */
export type Mode = "base" | "reviewed" | "gerrit";

export const MODES: Record<Mode, string> = {
  base: "Against its base commit",
  reviewed: "Since my last review",
  gerrit: "Gerrit vs. local",
};

/** The codicon the default-mode toggle wears for each mode. */
export const MODE_ICONS: Record<Mode, string> = { base: "git-commit", reviewed: "eye", gerrit: "cloud-upload" };

/** What a change allows: patchsets to compare once it is on Gerrit, your commit when it is in your stack. */
export interface Sides {
  pushed: boolean;
  local: boolean;
}

/** The default modes `sides` allows, in the toggle's order: Gerrit vs. local needs your commit and a patchset, Since my last review a patchset. */
export function modesFor(sides: Sides): Mode[] {
  return (["base", "reviewed", "gerrit"] as Mode[]).filter((mode) => mode === "base" || (sides.pushed && (mode === "reviewed" || sides.local)));
}

/**
 * From and To of `mode` for a change: base → your commit (else Gerrit's current patchset); your
 * last review → Gerrit's current patchset; Gerrit's current patchset → your commit. A mode the
 * change does not allow is its base comparison.
 */
export function modeSides(mode: Mode, sides: Sides): { from: From; to: Version } {
  const allowed = modesFor(sides).includes(mode) ? mode : "base";
  if (allowed === "reviewed") {
    return { from: "reviewed", to: "current" };
  }
  if (allowed === "gerrit") {
    return { from: "current", to: "local" };
  }
  return { from: "base", to: sides.local ? "local" : "current" };
}

/** A patchset number for `side`, once the patchsets are known: "current" and "reviewed" resolved. Else the side itself. */
export function resolveSide<T extends From | Version>(side: T, patchsets: Patchsets | undefined): T | number {
  if (side === "current" && patchsets?.current) {
    return patchsets.current;
  }
  if (side === "reviewed" && patchsets?.reviewed) {
    return patchsets.reviewed;
  }
  return side;
}

/**
 * A comparison as asked for, made one core can answer: From older than To. Picking To at or
 * below the patchset From names sets From back to the base commit; once the patchsets are
 * known, a From that names one at or above To does the same. Before they are known, From and
 * To naming the same patchset ("current" both) does too: nothing refits the pick later.
 */
export function fitSides(from: From, to: Version, patchsets: Patchsets | undefined): { from: From; to: Version } {
  const [older, newer] = [resolveSide(from, patchsets), resolveSide(to, patchsets)];
  const notOlder = older === newer || (typeof older === "number" && typeof newer === "number" && older >= newer);
  return notOlder ? { from: "base", to } : { from, to };
}

/** What the comparison is decided from: the change, its patchsets, and the sides and default mode chosen. */
export interface CompareState {
  /** A commit of your local stack. */
  local: boolean;
  shown: ShownCommit;
  patchsets: Patchsets | undefined;
  /** The newer side ("To"). */
  version: Version;
  /** The older side ("From"). */
  from: From;
  /** The default mode, remembered; From and To are its sides unless picked by hand. */
  mode: Mode;
}

/**
 * The From and To pickers, and the toggle of the default mode: `mode` is the default, `next` the
 * one a click makes it, `active` whether From and To are the default's (else picked by hand).
 */
export interface CompareVm {
  from: { options: Option[]; selected: string };
  to: { options: Option[]; selected: string };
  mode: { mode: Mode; icon: string; next: Mode; active: boolean; title: string };
}

export interface Option {
  value: string;
  label: string;
  disabled?: boolean;
}

/** `Patchset 3 (current, you reviewed it)`: a patchset as both pickers list it. */
function patchsetLabel(number: number, patchsets: Patchsets): string {
  const marks = [number === patchsets.current ? "current" : "", number === patchsets.reviewed ? "you reviewed it" : ""].filter(Boolean);
  return `Patchset ${number}${marks.length ? ` (${marks.join(", ")})` : ""}`;
}

/** What each picker says while the patchsets are not known yet, for the sides that name one. */
export const SIDE_WORDS = {
  base: "Base commit",
  current: "Gerrit's current patchset",
  /** Gerrit's current patchset by its number, as the commit's status names it: the list need not have come. */
  currentNumbered: (number: number) => `Gerrit's patchset ${number} (current)`,
  reviewed: "Patchset you last reviewed",
  notReviewed: "Patchset you last reviewed (none)",
};

/** Gerrit's current patchset before the list has come: by its number when the commit's status says it. */
function currentLabel(shown: ShownCommit): string {
  return shown.current_patch_set ? SIDE_WORDS.currentNumbered(shown.current_patch_set) : SIDE_WORDS.current;
}

/** The patchsets newest first: both pickers list the versions as a timeline, the newest on top. */
function newestFirst(patchsets: Patchsets): number[] {
  return patchsets.patchsets.map((patchset) => patchset.number).reverse();
}

/** The To picker: your commit (in your stack), then every patchset on Gerrit. */
function toPicker(state: CompareState): { options: Option[]; selected: string } {
  const { shown, patchsets } = state;
  const options: Option[] = [];
  if (state.local) {
    options.push({ value: "local", label: `Local commit ${shown.short_sha}` });
  }
  if (shown.pushed && patchsets) {
    options.push(...newestFirst(patchsets).map((number) => ({ value: String(number), label: patchsetLabel(number, patchsets) })));
  } else if (shown.pushed) {
    options.push({ value: "current", label: currentLabel(shown) });
  }
  const wanted = String(resolveSide(state.version, patchsets));
  return { options, selected: options.some((option) => option.value === wanted) ? wanted : (options[0]?.value ?? "") };
}

/** The From picker: every patchset on Gerrit older than To, then the base commit. */
function fromPicker(state: CompareState): { options: Option[]; selected: string } {
  const { shown, patchsets } = state;
  const to = resolveSide(state.version, patchsets);
  const options: Option[] = [];
  if (shown.pushed && patchsets) {
    options.push(...newestFirst(patchsets).map((number) => ({ value: String(number), label: patchsetLabel(number, patchsets), disabled: typeof to === "number" && number >= to })));
    if (state.from === "reviewed" && patchsets.reviewed === null) {
      options.push({ value: "reviewed", label: SIDE_WORDS.notReviewed, disabled: true });
    }
  } else if (shown.pushed) {
    options.push({ value: "current", label: currentLabel(shown), disabled: to === "current" }, { value: "reviewed", label: SIDE_WORDS.reviewed });
  }
  options.push({ value: "base", label: SIDE_WORDS.base });
  const wanted = String(resolveSide(state.from, patchsets));
  return { options, selected: options.some((option) => option.value === wanted) ? wanted : "base" };
}

/** Whether From and To are the default mode's sides, as far as the patchsets known can say. */
export function atDefault(state: Omit<CompareState, "shown"> & { pushed: boolean }): boolean {
  const own = modeSides(state.mode, { pushed: state.pushed, local: state.local });
  const same = (a: From | Version, b: From | Version) => resolveSide(a, state.patchsets) === resolveSide(b, state.patchsets);
  return same(own.from, state.from) && same(own.to, state.version);
}

/** The toggle of the default mode: which it is, the next a click makes it, whether From and To are its sides. */
function modeToggle(state: CompareState): CompareVm["mode"] {
  const allowed = modesFor({ pushed: state.shown.pushed, local: state.local });
  const next = allowed[(allowed.indexOf(state.mode) + 1) % allowed.length] ?? "base";
  const active = atDefault({ ...state, pushed: state.shown.pushed });
  const here = !allowed.includes(state.mode) ? " — not possible for this change" : active ? "" : " — From and To picked by hand for this change";
  return { mode: state.mode, icon: MODE_ICONS[state.mode], next, active, title: `Default comparison: ${MODES[state.mode]}${here}. Click for ${MODES[next]}.` };
}

/** What the files compared are called: by what From and To are, whether picked by hand or by the default mode. */
export function deltaTitle(state: CompareState): "Gerrit vs. local" | "Since your review" | "Compared" {
  const from = resolveSide(state.from, state.patchsets);
  if (state.version === "local" && (state.from === "current" || from === state.patchsets?.current)) {
    return "Gerrit vs. local";
  }
  return state.from === "reviewed" || from === state.patchsets?.reviewed ? "Since your review" : "Compared";
}

/** The pickers and the toggle, as the Change view draws them. */
export function compareVm(state: CompareState): CompareVm {
  return { from: fromPicker(state), to: toPicker(state), mode: modeToggle(state) };
}
