// Vote and Publish: a reviewer's review goes out from the editor. Core says what would go out
// and which votes there are (`GET /vote`); the editor asks (the vote, a message, then once: publish?);
// core sends exactly that (`POST /vote`), the same as `ger review --vote`.

import * as vscode from "vscode";

import { GerApiError, VoteOption, VotePlan } from "./api";
import { ChangeTarget, Model, describeError } from "./model";
import { ui } from "./ui";
import { VoteItem, confirmVote, defaultVoteItem, sentVote, voteItems, votePlaceholder, voteTitle } from "./voteText";

export interface VoteRequest {
  /** Every open change of the target's review chain, not only the target. */
  chain?: boolean;
  /** Your drafts without a vote: no picker. */
  draftsOnly?: boolean;
  /** Draw what was sent: the views refresh, the change shown reloads. */
  after?: () => Promise<void>;
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
    void ui().message("error", `Cannot vote: ${describeError(error)}`, {}, []);
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
    const sent = await ui().progress(vscode.ProgressLocation.Notification, `Publishing on ${number}`, () => client.vote(number, chain, votes, message.trim() || undefined, patchSets));
    void ui().message("info", sentVote(sent, option), {}, []);
  } catch (error) {
    // The changes moved since they were shown: nothing went out. Look again, and decide on what is there now.
    if (error instanceof GerApiError && error.code === "REFUSED") {
      if ((await ui().message("warning", `Nothing was published: ${error.message}`, {}, ["Look Again"])) === "Look Again") {
        await voteAndPublish(model, target, request);
      }
      return;
    }
    void ui().message("error", `Not published: ${describeError(error)}`, {}, []);
  }
  await request.after?.();
}
