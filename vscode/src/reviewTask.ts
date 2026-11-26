// The task header while you review a chain (a staged review here): the chain's changes oldest
// first, each one's vote and comments, what is left, and the way out. Pure: no `vscode`, no I/O.
// taskRows.ts has what both modes share and picks the mode.

import { Member, Overview, Stack } from "./api";
import { commentTurns } from "./commentState";
import { plural } from "./format";
import { reviewFocus } from "./reviewFocus";
import { TaskMark, TaskOptions, TaskStep, TaskVm, VIEWS, commentMark, counted, failedCi, folded } from "./taskRows";

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
  // The chain as core has it at the reviewed patchset: members the inbox lost are kept, in their place.
  const known: Member[] = (options.chain ?? []).length > 1
    ? options.chain!.map((number) => (listed.find((member) => member.number === number) ?? inbox.find((member) => member.number === number)) ?? unlisted(number, number === review.number ? review.subject : options.subjects?.get(number), options.inboxUnknown))
    : listed;
  // Core's chain holds open changes only: the change under review, closed meanwhile, is still the one reviewed.
  const members = known.length && !known.some((member) => member.number === review.number) ? [unlisted(review.number, review.subject, options.inboxUnknown), ...known] : known;
  const comments = (member: Member) => counted(member, String(member.number), options);
  const at = members.findIndex((member) => member.number === review.number);
  const steps: TaskStep[] = members.map((member, index) => {
    const current = index === at;
    const marks = [
      current ? { icon: "eye", text: "reviewing", tone: "info" as const } : undefined,
      voteMark(member),
      commentMark(comments(member)),
      failedCi(member) ? { icon: "error", text: "CI failed", tone: "bad" as const } : undefined,
    ].filter((mark): mark is TaskMark => Boolean(mark));
    return { key: String(member.number), position: index + 1, title: member.subject, current, marks, tooltip: `${member.number}: ${member.subject}\n${marks.map((m) => m.text).join(" · ") || "nothing to say"}` };
  });
  if (!steps.length) {
    // Not in your inbox (any more): the change alone.
    steps.push({ key: String(review.number), position: 1, title: review.subject, current: true, marks: [{ icon: "eye", text: "reviewing", tone: "info" }], tooltip: `${review.number}: ${review.subject}` });
  }
  const voted = members.filter((member) => member.your_votes?.["Code-Review"]).length;
  const toVote = members.filter((member) => !member.your_votes?.["Code-Review"] && (!member.not_ready || member.not_ready === NOT_LISTED || member.not_ready === NOT_KNOWN)).length;
  const notReady = members.filter((member) => !member.your_votes?.["Code-Review"] && member.not_ready && member.not_ready !== NOT_LISTED && member.not_ready !== NOT_KNOWN).length;
  const gone = members.filter((member) => member.not_ready === NOT_LISTED).length;
  const toAnswer = members.reduce((sum, member) => sum + commentTurns(comments(member)).left, 0);
  const left = [
    options.inboxUnknown
      ? `what is left is ${NOT_KNOWN}`
      : members.length
        ? toVote
          ? `${toVote} of ${members.length} to vote on`
          : `you voted on ${voted} of ${members.length}`
        : "its chain is not in your inbox",
    notReady ? `${notReady} not ready for review` : "",
    gone ? `${gone} ${NOT_LISTED}` : "",
    toAnswer ? `${plural(toAnswer, "comment")} to answer` : "",
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
    ...folded(steps, "review"),
  };
}
