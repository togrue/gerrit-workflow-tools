// Local stack's conflict preview (`GET /stack/moves`): which neighbours cannot swap places without
// a conflict, for the stack as it is now. No `vscode` import.

import { GerClient, MoveConflict, Stack } from "./api";
import { blockedSwaps, swapKey } from "./format";

export class MovePreview {
  /** Neighbours that cannot swap places, for the stack whose head is `head`. */
  private blocked = new Map<string, MoveConflict>();
  private head: string | null | undefined;

  constructor(
    private readonly client: () => Promise<GerClient>,
    private readonly stack: () => Stack | undefined,
    private readonly changed: () => void,
  ) {}

  /** Ask for the stack as it is now. Best effort: without it every move is offered. */
  async load(): Promise<void> {
    const head = this.stack()?.head;
    if (head === this.head) {
      return;
    }
    if (this.stack()?.staged_review) {
      // Somebody else's chain under review: nothing of it is reordered here (core refuses), so nothing to ask.
      this.blocked = new Map();
      this.head = head;
      return;
    }
    try {
      const moves = head ? await (await this.client()).moves() : undefined;
      if (this.stack()?.head !== head) {
        return; // the stack moved on meanwhile: the next load asks again
      }
      this.blocked = blockedSwaps(moves);
      this.head = head;
    } catch {
      this.blocked = new Map();
    }
    this.changed();
  }

  /** Why the stack commits `lower` and `upper` (neighbours) cannot swap places, if they cannot. */
  conflict(lower: string | null, upper: string | null): MoveConflict | undefined {
    return lower && upper && this.stack()?.head === this.head ? this.blocked.get(swapKey(lower, upper)) : undefined;
  }
}
