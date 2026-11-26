// Local stack's conflict preview (`GET /stack/moves`): which neighbours cannot swap places without
// a conflict, for the stack as it is now. No `vscode` import.

import { GerClient, MoveConflict, Stack } from "./api";
import { blockedSwaps, swapKey } from "./format";

/** What the preview is asked for: the head, or that nothing moves (frozen: a staged review, a rebase underway). */
function previewKey(stack: Stack | undefined): string {
  return stack?.staged_review || stack?.rebase_in_progress ? `frozen ${stack.head}` : String(stack?.head ?? "");
}

export class MovePreview {
  /** Neighbours that cannot swap places, for the stack `key` names. */
  private blocked = new Map<string, MoveConflict>();
  private key: string | undefined;
  /** The key of the ask under way: a second load meanwhile (two stacks set in a row) does not ask again. */
  private asking: string | undefined;

  constructor(
    private readonly client: () => Promise<GerClient>,
    private readonly stack: () => Stack | undefined,
    private readonly changed: () => void,
  ) {}

  /** Ask for the stack as it is now, once per head. Best effort: without it every move is offered. */
  async load(): Promise<void> {
    const key = previewKey(this.stack());
    if (key === this.key || key === this.asking) {
      return;
    }
    if (key.startsWith("frozen")) {
      // Somebody else's chain under review, or a rebase underway: nothing is reordered now (core refuses), so nothing to ask.
      this.blocked = new Map();
      this.key = key;
      return;
    }
    this.asking = key;
    try {
      const moves = key ? await (await this.client()).moves() : undefined;
      if (previewKey(this.stack()) !== key) {
        return; // the stack moved on meanwhile: the next load asks again
      }
      this.blocked = blockedSwaps(moves);
      this.key = key;
    } catch {
      this.blocked = new Map();
    } finally {
      this.asking = this.asking === key ? undefined : this.asking;
    }
    this.changed();
  }

  /** Why the stack commits `lower` and `upper` (neighbours) cannot swap places, if they cannot. */
  conflict(lower: string | null, upper: string | null): MoveConflict | undefined {
    return lower && upper && previewKey(this.stack()) === this.key ? this.blocked.get(swapKey(lower, upper)) : undefined;
  }
}
