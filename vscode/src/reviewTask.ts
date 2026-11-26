// The task header while you review a chain (a staged review here): the chain's changes oldest
// first, each one's vote and comments, what is left, and the way out. Pure: no `vscode`, no I/O.
// taskRows.ts has what both modes share and picks the mode.

import { Member, Overview, Stack } from "./api";
import { commentTurns } from "./commentState";
import { plural } from "./format";
import { reviewFocus } from "./reviewFocus";
import { TaskMark, TaskOptions, TaskStep, TaskVm, VIEWS, commentMark, counted, failedCi, folded } from "./taskRows";

/** What the header says of a closed change of the chain, as Gerrit's words for it. */
const CLOSED_WORDS: Record<"MERGED" | "ABANDONED", string> = { ABANDONED: "abandoned", MERGED: "merged" };

/** What the header says of a change reviewed on top of an abandoned one: its code is this review's base. */
export const ON_ABANDONED = (number: number, subject: string) => `builds on ${number} "${subject}", which is abandoned: its code is the base this change is reviewed on`;

/** What the header says of a change of the chain under review that the inbox does not list now. */
export const NOT_LISTED = "not in your inbox now";
/** What it says of the chain's changes while the inbox cannot be read. */
export const NOT_KNOWN = "unknown: Gerrit could not be asked";

function voteMark(member: Member): TaskMark | undefined {
  const review = member.your_votes?.["Code-Review"];
  if (review) {
    return { icon: review > 0 ? "thumbsup" : "thumbsdown", text: `you voted ${review > 0 ? "+" : ""}${review}`, tone: review > 0 ? "good" : "bad" };
  }
  if (member.not_ready === NOT_KNOWN) {
    return undefined; // said once, in what is left
  }
  if (member.not_ready === NOT_LISTED) {
    return { icon: "question", text: member.not_ready, tone: "dim" };
  }
  if (member.not_ready) {
    return { icon: "circle-slash", text: `not ready: ${member.not_ready}`, tone: "dim" };
  }
  return member.your_turn ? { icon: "circle-large-outline", text: "to review" } : undefined;
}

/** A change of the chain under review the inbox does not list: what is known of it is its number. */
function unlisted(number: number, subject?: string, unknown = false): Member {
  const member: Member = { number, change_id: "", subject: subject ?? `Change ${number}`, verified: null, code_review: null, comments_unresolved: 0, attention_reasons: [], severity: "ok", url: null, local_sha: null, sha: null };
  return { ...member, not_ready: unknown ? NOT_KNOWN : NOT_LISTED };
}

export function reviewTask(overview: Overview | undefined, stack: Stack, options: TaskOptions): TaskVm {
  const review = stack.staged_review!;
  // The inbox's chain of the change under review, else of another change of its chain (the inbox lost the one reviewed).
  const others = (options.chain ?? []).filter((number) => number !== review.number);
  const focus = reviewFocus(overview, stack) ?? others.map((number) => reviewFocus(overview, { ...stack, staged_review: { ...review, number } })).find(Boolean);
  const listed = focus?.chain.members ?? [];
  // A change of the chain the inbox lists in a chain of its own (its base is an older patchset of the one below).
  const inbox = (overview?.sections ?? []).flatMap((section) => section.chains.flatMap((chain) => chain.members));
  // The chain as core has it at the reviewed patchset: members the inbox lost are kept, in their place,
  // and closed ones too when core says them (API 1.10), marked in words and counted as no work.
  const closed = new Map((options.members ?? []).filter((member) => member.status !== "NEW").map((member) => [member.number, member.status as "MERGED" | "ABANDONED"]));
  const subject = (number: number) => (number === review.number ? review.subject : (options.members?.find((member) => member.number === number)?.subject ?? options.subjects?.get(number)));
  const numbers = closed.size ? options.members!.map((member) => member.number) : (options.chain ?? []);
  const known: Member[] = numbers.length > 1
    ? numbers.map((number) => (listed.find((member) => member.number === number) ?? inbox.find((member) => member.number === number)) ?? unlisted(number, subject(number), options.inboxUnknown))
    : listed;
  // Core's chain holds open changes only: the change under review, closed meanwhile, is still the one reviewed.
  const members = known.length && !known.some((member) => member.number === review.number) ? [unlisted(review.number, review.subject, options.inboxUnknown), ...known] : known;
  const comments = (member: Member) => counted(member, String(member.number), options);
  const at = members.findIndex((member) => member.number === review.number);
  const steps: TaskStep[] = members.map((member, index) => {
    const current = index === at;
    const status = closed.get(member.number);
    // Closed: Gerrit takes no vote or comment there, so what it had is no work; the word says why.
    const marks = [
      current ? { icon: "eye", text: "reviewing", tone: "info" as const } : undefined,
      status ? { icon: status === "MERGED" ? "git-merge" : "circle-slash", text: CLOSED_WORDS[status], tone: "dim" as const } : voteMark(member),
      status ? undefined : commentMark(comments(member)),
      !status && failedCi(member) ? { icon: "error", text: "CI failed", tone: "bad" as const } : undefined,
    ].filter((mark): mark is TaskMark => Boolean(mark));
    return { key: String(member.number), position: index + 1, title: member.subject, current, marks, tooltip: `${member.number}: ${member.subject}\n${marks.map((m) => m.text).join(" · ") || "nothing to say"}` };
  });
  if (!steps.length) {
    // Not in your inbox (any more): the change alone.
    steps.push({ key: String(review.number), position: 1, title: review.subject, current: true, marks: [{ icon: "eye", text: "reviewing", tone: "info" }], tooltip: `${review.number}: ${review.subject}` });
  }
  // What is left is counted on the open changes alone: a closed one takes no vote and no answer.
  const open = members.filter((member) => !closed.has(member.number));
  const voted = open.filter((member) => member.your_votes?.["Code-Review"]).length;
  const toVote = open.filter((member) => !member.your_votes?.["Code-Review"] && (!member.not_ready || member.not_ready === NOT_LISTED || member.not_ready === NOT_KNOWN)).length;
  const notReady = open.filter((member) => !member.your_votes?.["Code-Review"] && member.not_ready && member.not_ready !== NOT_LISTED && member.not_ready !== NOT_KNOWN).length;
  const gone = open.filter((member) => member.not_ready === NOT_LISTED).length;
  const toAnswer = open.reduce((sum, member) => sum + commentTurns(comments(member)).left, 0);
  const shut = members.length - open.length;
  const below = at > 0 ? members[at - 1] : undefined;
  const notice = below && closed.get(below.number) === "ABANDONED" ? ON_ABANDONED(below.number, below.subject) : undefined;
  const left = [
    options.inboxUnknown
      ? `what is left is ${NOT_KNOWN}`
      : open.length
        ? toVote
          ? `${toVote} of ${open.length} to vote on`
          : `you voted on ${voted} of ${open.length}`
        : members.length
          ? "nothing: Gerrit takes no vote on a closed change"
          : "its chain is not in your inbox",
    notReady ? `${notReady} not ready for review` : "",
    gone ? `${gone} ${NOT_LISTED}` : "",
    toAnswer ? `${plural(toAnswer, "comment")} to answer` : "",
    shut ? `${shut} closed` : "",
  ].filter(Boolean);
  const owner = focus?.chain.owner.name;
  const back = review.return_to;
  return {
    mode: "review",
    heading: owner ? `Reviewing ${owner}'s chain` : `Reviewing ${review.number}`,
    title: focus?.chain.top.subject ?? review.subject,
    position: `change ${at >= 0 ? at + 1 : 1} of ${Math.max(members.length, 1)}`,
    left: left.join(" · "),
    steps,
    views: options.all ? VIEWS.only : VIEWS.all,
    actions: [
      { label: "Leave review", detail: back ? `back to ${back}; the review's branches go` : "end the review; its branches go", command: "ger.reviewDone" },
    ],
    busy: options.busy,
    ...(options.outcome ? { outcome: options.outcome } : {}),
    ...(notice ? { notice } : {}),
    ...folded(steps, "review"),
  };
}
