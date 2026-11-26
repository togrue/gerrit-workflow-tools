// The next change of a review, asked while you read the one before it: its commit and files, so
// Review Next Change draws it at once and opens its diff from git while the checkout runs. One
// request per change, at a moment ger serve is idle; kept only for the patchset it was asked for.

import { GerClient, Member, ShownCommit } from "./api";

export interface Ahead {
  shown: ShownCommit;
  files: NonNullable<ShownCommit["files"]>;
}

export class ReviewAhead {
  /** By change number: the patchset's commit it was asked for, and the answer on its way. */
  private readonly asked = new Map<number, { sha: string | null; answer: Promise<Ahead | undefined> }>();

  constructor(private readonly client: () => Promise<GerClient>) {}

  /** Ask for `member` once: its commit with comments and files, as the Change view's first load would. */
  fetch(member: Member): void {
    const known = this.asked.get(member.number);
    if (known && known.sha === member.sha) {
      return;
    }
    const answer = this.client()
      .then((client) => client.show([`change:${member.number}`], { comments: "all", context: 3, files: true }))
      .then(([shown]) => (shown?.files && (!member.sha || shown.sha === member.sha) ? { shown, files: shown.files } : undefined))
      .catch(() => undefined);
    this.asked.set(member.number, { sha: member.sha, answer });
  }

  /** What was asked for `member`, if it is still that patchset; undefined when it was not asked or failed. */
  async take(member: Member): Promise<Ahead | undefined> {
    const known = this.asked.get(member.number);
    if (!known || known.sha !== member.sha) {
      return undefined;
    }
    return known.answer;
  }

  /** The review ended or moved to another chain: nothing asked is kept. */
  forget(): void {
    this.asked.clear();
  }
}
