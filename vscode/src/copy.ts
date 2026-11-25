// Copying what names a change, in the forms of format.ts's COPY_FORMATS: to the clipboard, and
// the "l" menu that picks the form with one more letter, as Gerrit's web UI does.

import { CopyFormat, copyItems, copyText } from "./format";
import { ChangeTarget } from "./model";
import { feedback, ui } from "./ui";

/** Copy `format` of `targets`, and say so in the status bar. Nothing when none of them has it. */
export async function copyAs(targets: ChangeTarget[], format: CopyFormat): Promise<void> {
  const values = copyText(targets, format);
  if (!values.length) {
    return;
  }
  await ui().copy(values.join("\n"));
  const noun = format.label;
  feedback(values.length === 1 ? (format.id ? `Copied ${noun} ${values[0].slice(0, 12)}…` : `Copied ${noun}`) : `Copied ${values.length} ${noun}s`);
}

/** The "l" menu: pick a form with its letter (or the arrows and Enter), and it is copied. */
export async function copyMenu(targets: ChangeTarget[]): Promise<void> {
  const items = copyItems(targets);
  if (!items.length) {
    return;
  }
  const title = targets.length === 1 ? `Copy ${targets[0].number ?? targets[0].title}` : `Copy ${targets.length} changes`;
  const picked = await ui().pick(items, { title, placeHolder: "Press a letter to copy that form" });
  if (picked) {
    await copyAs(targets, picked.format);
  }
}
