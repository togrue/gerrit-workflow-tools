// The words of Vote and Publish: the picker's votes, the one confirmation and what was sent.
// What goes out, what each vote means and which changes take it are core's (`GET /vote`); this
// only words them. No `vscode` import: unit-tested.

import { VoteOption, VotePlan, VoteSent } from "./api";
import { plural } from "./format";

/** A row of the vote picker: a vote, or (no `option`) your drafts without one. */
export interface VoteItem {
  label: string;
  description?: string;
  detail?: string;
  option?: VoteOption;
}

/** `+1`, `0`, `-2`: as Gerrit writes a vote's value. */
export function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

/** `Code-Review +1`. */
export function voteName(option: VoteOption): string {
  return `${option.label} ${signed(option.value)}`;
}

/** Which changes the vote is on: `change 900`, or `100, 101, 102` for a chain. */
export function changesNamed(numbers: number[]): string {
  return numbers.length === 1 ? `change ${numbers[0]}` : numbers.join(", ");
}

/** What the picker is about: the change, or how many changes of the chain. */
export function voteTitle(plan: VotePlan): string {
  const [first] = plan.changes;
  return plan.changes.length === 1 ? `Vote on ${first.number} "${first.subject}"` : `Vote on the chain: ${plan.changes.length} changes, ${changesNamed(plan.changes.map((c) => c.number))}`;
}

/** The line under the title: what goes out besides the vote. */
export function votePlaceholder(plan: VotePlan): string {
  return plan.drafts ? `${plural(plan.drafts, "draft")} of yours ${plan.drafts === 1 ? "goes" : "go"} out with it` : "You have no drafts here: only the vote and a message go out";
}

const ICONS: Record<string, string> = { up: "$(thumbsup)", down: "$(thumbsdown)", none: "$(circle-large-outline)" };

/**
 * The votes on offer, as core lists them (Code-Review first, highest first), with what each means;
 * for a chain, a vote some changes do not take says which do. Then your drafts without a vote,
 * when you have drafts there.
 */
export function voteItems(plan: VotePlan): VoteItem[] {
  const all = plan.changes.length;
  const items: VoteItem[] = plan.options.map((option) => ({
    label: `${ICONS[option.value > 0 ? "up" : option.value < 0 ? "down" : "none"]} ${voteName(option)}`,
    description: option.meaning || undefined,
    detail: option.takes.length < all ? `only on ${changesNamed(option.takes)}: the other changes do not take it` : undefined,
    option,
  }));
  if (plan.drafts) {
    items.push({ label: "$(comment) Publish drafts only, no vote", description: plural(plan.drafts, "draft") });
  }
  return items;
}

/**
 * What the picker opens on: Code-Review +1, the usual reviewer's vote. On your own change (every
 * change of the plan yours) your drafts without a vote, when you have some.
 */
export function defaultVoteItem(plan: VotePlan, items: VoteItem[]): VoteItem | undefined {
  const drafts = items.find((item) => !item.option);
  if (drafts && plan.changes.every((change) => change.own)) {
    return drafts;
  }
  return items.find((item) => item.option?.label === "Code-Review" && item.option.value === 1) ?? items[0];
}

/** The changes a choice goes to: those that take the vote (every change, without one). */
export function takers(plan: VotePlan, option: VoteOption | undefined): number[] {
  return option ? option.takes : plan.changes.map((change) => change.number);
}

/** The one question, a modal one (it cannot be taken back): what goes out, on which changes, draft by draft. */
export function confirmVote(plan: VotePlan, option: VoteOption | undefined, message: string): { text: string; detail: string } {
  const on = takers(plan, option);
  const changes = plan.changes.filter((change) => on.includes(change.number));
  const drafts = changes.reduce((sum, change) => sum + change.drafts.length, 0);
  const what = [option ? voteName(option) : "no vote", drafts ? plural(drafts, "draft") : "no drafts", message ? `message "${message}"` : ""].filter(Boolean).join(" · ");
  const lines = [what];
  // Each change with its drafts; a single change without any needs no line of its own.
  for (const change of changes.length > 1 || drafts ? changes : []) {
    lines.push("", `${change.number} ${change.subject}${change.drafts.length ? "" : ": no drafts"}`);
    lines.push(...change.drafts.map((draft) => `  ${draft.location}  "${draft.text}"${draft.transition ? ` (${draft.transition})` : ""}`));
  }
  const skipped = plan.changes.filter((change) => !on.includes(change.number));
  if (skipped.length) {
    lines.push("", `Skipped (it does not take ${option ? voteName(option) : "it"}): ${changesNamed(skipped.map((change) => change.number))}`);
  }
  lines.push("", "Everyone on the change sees it at once; it cannot be taken back.");
  return { text: `Publish on ${changesNamed(on)}?`, detail: lines.join("\n") };
}

/** What a notification says once it went out. */
export function sentVote(sent: VoteSent, option: VoteOption | undefined): string {
  const drafts = sent.changes.reduce((sum, change) => sum + change.drafts, 0);
  const what = [option ? voteName(option) : "", drafts || !option ? plural(drafts, "draft") : ""].filter(Boolean).join(" and ");
  const skipped = sent.skipped.length ? ` Skipped ${changesNamed(sent.skipped.map((change) => change.number))}.` : "";
  return `Published ${what} on ${changesNamed(sent.changes.map((change) => change.number))}.${skipped}`;
}
