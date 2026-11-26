// Vote and Publish: a reviewer's review goes out from the editor. Core says what would go out
// and which votes there are (`GET /vote`); the editor asks (the vote, a message, then once: publish?);
// core sends exactly that (`POST /vote`), the same as `ger review --vote`.

import * as vscode from "vscode";

import { GerApiError, VoteOption, VotePlan } from "./api";
import { ChangeTarget, Model, describeError } from "./model";
import { failureWays, servedError } from "./problemRows";
import { WayOn, offerWaysOn, ui } from "./ui";
import { VoteItem, confirmVote, defaultVoteItem, sentVote, voteItems, votePlaceholder, voteTitle } from "./voteText";

export interface VoteRequest {
  /** Every open change of the target's review chain, not only the target. */
  chain?: boolean;
  /** Your drafts without a vote: no picker. */
  draftsOnly?: boolean;
  /** Draw what was sent: the views refresh, the change shown reloads. */
  after?: () => Promise<void>;
  /** Once it is sent: the way on to offer on the notification (the next change of a staged review), if any. */
  next?: () => Promise<{ label: string; run: () => Promise<void> } | undefined>;
}

/** Ask for the vote (or none) and a message, confirm once what goes out, and publish it. */
export async function voteAndPublish(model: Model, target: ChangeTarget | undefined, request: VoteRequest = {}): Promise<void> {
  if (!target) {
    return;
  }
  if (target.number === undefined) {
    void ui().message("info", `"${target.title}" is not on Gerrit yet: there is nothing to vote on or publish.`, {}, []);
    return;
  }
  const number = target.number;
  const chain = Boolean(request.chain);
  let plan: VotePlan;
  try {
    const client = await model.client();
    plan = await ui().progress(vscode.ProgressLocation.Window, "Reading your drafts", () => client.votePlan(number, chain));
  } catch (error) {
    void offerWaysOn(error instanceof GerApiError && error.reason === "closed" ? "warning" : "error", `Cannot vote: ${describeError(error)}`, waysOn(error));
    return;
  }
  let option: VoteOption | undefined;
  if (!request.draftsOnly) {
    const items = voteItems(plan);
    const picked = await ui().pick<VoteItem>(items, { title: voteTitle(plan), placeHolder: votePlaceholder(plan), active: defaultVoteItem(plan, items), matchOnDescription: true });
    if (!picked) {
      return;
    }
    option = picked.option;
  } else if (!plan.drafts) {
    void ui().message("info", `You have no drafts on ${number} to publish.`, {}, []);
    return;
  }
  const message = await ui().input({
    title: `Message on ${chain ? "each change" : `change ${number}`} (optional)`,
    prompt: "Posted with it as the change message. Leave it empty for none; Escape cancels.",
    placeHolder: option && option.value > 0 ? "Looks good to me" : undefined,
  });
  if (message === undefined) {
    return;
  }
  const confirm = confirmVote(plan, option, message.trim());
  if ((await ui().message("info", confirm.text, { modal: true, detail: confirm.detail }, ["Publish"])) !== "Publish") {
    return;
  }
  const patchSets = Object.fromEntries(plan.changes.map((change) => [String(change.number), change.patch_set ?? 0]));
  const votes = option ? { [option.label]: option.value } : {};
  try {
    const client = await model.client();
    // "Publishing" until Gerrit answered and the views have read it back: the answer, Next and the
    // Comments section then change at the same moment (W11), never "Published" beside "Vote and publish".
    const sent = await ui().progress(vscode.ProgressLocation.Notification, `Publishing on ${number}`, async () => {
      const answer = await client.vote(number, chain, votes, message.trim() || undefined, patchSets);
      await request.after?.().catch(() => undefined);
      return answer;
    });
    const next = await request.next?.().catch(() => undefined);
    void ui()
      .message("info", sentVote(sent, option), {}, next ? [next.label] : [])
      .then((picked) => (picked && next ? next.run() : undefined));
    return;
  } catch (error) {
    // The changes moved since they were shown: nothing went out. Look again, and decide on what is there now.
    if (error instanceof GerApiError && error.code === "REFUSED") {
      if ((await ui().message("warning", `Nothing was published: ${error.message}`, {}, ["Look Again"])) === "Look Again") {
        await voteAndPublish(model, target, request);
      }
      return;
    }
    // Nothing went out: said as a failure, with what can be done about it.
    void offerWaysOn("error", `Not published: ${describeError(error)}`, waysOn(error));
  }
  await request.after?.();
}

/** Leave Review, while a staged review sits here: the way on from a change nothing can be voted on any more. */
const LEAVE: WayOn = { label: "Leave Review", command: { id: "ger.reviewDone", args: [] } };

/** What a refused or failed vote offers: Gerrit away, its fixes; a closed change, leaving its review; else the log. */
export function waysOn(error: unknown): WayOn[] {
  return servedError(error)?.reason === "closed" ? [LEAVE] : failureWays(error);
}
