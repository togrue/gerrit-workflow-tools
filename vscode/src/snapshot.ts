// A snapshot: what the two webview views drew at one moment, as the view models their pages were
// sent. The gallery (test/gallery) draws one per scenario, and a snapshot written from a running
// extension replays there. One format for both, so it has one owner. No `vscode` import: unit-tested.

import { ChangeVm } from "./changeRows";
import { StackVm } from "./stackRows";

export const SNAPSHOT_FORMAT = 1;

export interface Snapshot {
  format: typeof SNAPSHOT_FORMAT;
  /** A few words that say what it shows. */
  name: string;
  /** What *Local stack* drew; null when the snapshot is not about it. */
  stack: StackVm | null;
  /** What *Change* drew; null when the snapshot is not about it. */
  change: ChangeVm | null;
}

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

/** A view's part of the file: an object, or null (also when the key is missing). */
function view(file: Record<string, unknown>, key: "stack" | "change"): Record<string, unknown> | null {
  const value = file[key] ?? null;
  if (value !== null && !isObject(value)) {
    throw new Error(`"${key}" must be a view model (an object) or null`);
  }
  return value;
}

/**
 * The snapshot in `text` (the content of a snapshot file). Keys it does not know are left out:
 * whoever writes one may say more (versions, when it was taken). Throws an Error that says what
 * is wrong with a file that is not a snapshot.
 */
export function readSnapshot(text: string): Snapshot {
  let file: unknown;
  try {
    file = JSON.parse(text);
  } catch (error) {
    throw new Error(`not JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!isObject(file)) {
    throw new Error("a snapshot is a JSON object");
  }
  if (file.format !== SNAPSHOT_FORMAT) {
    throw new Error(`"format" is ${JSON.stringify(file.format)}: this reads format ${SNAPSHOT_FORMAT}`);
  }
  if (typeof file.name !== "string" || !file.name.trim()) {
    throw new Error('"name" must say what the snapshot shows');
  }
  const [stack, change] = [view(file, "stack"), view(file, "change")];
  // The Local stack page walks these two lists unless it only says why there is nothing to show.
  if (stack && !stack.waiting && !(Array.isArray(stack.rows) && Array.isArray(stack.info))) {
    throw new Error('"stack" needs its "rows" and "info" lists');
  }
  return { format: SNAPSHOT_FORMAT, name: file.name.trim(), stack: stack as StackVm | null, change: change as ChangeVm | null };
}
