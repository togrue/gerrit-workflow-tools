// Files seen, as the Change view ticks them: Gerrit's per-file *reviewed* flag of the patchset
// looked at (`/reviewed`, ger serve 1.6), so a tick is there in Gerrit's web UI and after a reload
// too; a version Gerrit does not have (your local commit) is kept here for the session. A file is
// seen once its diff, opened from the Change view (a click, Next file), was shown to its end, or by
// a click on its tick.
//
// The combined diff (Open All Changes) does not count: VS Code reports every file in it as shown
// whole, wherever it is scrolled, so only its ticks mark them there.

import * as vscode from "vscode";

import { GerClient } from "./api";
import { SeenWhere } from "./changeRows";

export class FilesSeen implements vscode.Disposable {
  private readonly sets = new Map<string, Set<string>>();
  /** Asked of Gerrit already, or not to be asked (no patchset, or a ger serve before 1.6). */
  private readonly asked = new Set<string>();
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChange = this.changed.event;
  private readonly subscriptions: vscode.Disposable[] = [];
  /** The documents whose diff the Change view opened on their own: only they are looked at. */
  private readonly watched = new Set<string>();

  /**
   * `fileOf` names the file of the version looked at that `uri` shows on its right side (your file
   * when the working files hold it), and where its ticks are kept; undefined for anything else.
   */
  constructor(
    private readonly client: () => Promise<GerClient>,
    private readonly fileOf: (uri: vscode.Uri) => { where: SeenWhere; path: string } | undefined,
  ) {
    const look = (editor: vscode.TextEditor) => this.look(editor);
    // A file that fits on the screen never scrolls: look again once a new editor has been laid out.
    // Not at once: the tabs say what an editor is part of only once it is laid out.
    this.subscriptions.push(
      vscode.window.onDidChangeTextEditorVisibleRanges((event) => look(event.textEditor)),
      vscode.window.onDidChangeVisibleTextEditors((editors) => setTimeout(() => editors.forEach(look), 400)),
    );
  }

  /** The diff of the document `uri` was opened on its own: shown to its end, it is seen. */
  watch(uri: vscode.Uri): void {
    this.watched.add(uri.toString());
  }

  /** The files seen of `where`; the first time, Gerrit is asked once and `onDidChange` fires with its answer. */
  seen(where: SeenWhere): ReadonlySet<string> {
    const set = this.set(where);
    if (!this.asked.has(where.key)) {
      this.asked.add(where.key);
      void this.load(where, set);
    }
    return set;
  }

  /** Tick `path` of `where`, or take the tick off: here at once, on Gerrit behind it. */
  async mark(where: SeenWhere, path: string, seen = true): Promise<void> {
    const set = this.set(where);
    if (set.has(path) === seen) {
      return;
    }
    if (seen) {
      set.add(path);
    } else {
      set.delete(path);
    }
    this.changed.fire();
    if (where.number && where.patchSet) {
      await (await this.client()).markReviewed(where.number, where.patchSet, path, seen).catch(() => undefined);
    }
  }

  private set(where: SeenWhere): Set<string> {
    const set = this.sets.get(where.key) ?? new Set<string>();
    this.sets.set(where.key, set);
    return set;
  }

  private async load(where: SeenWhere, set: Set<string>): Promise<void> {
    if (!where.number || !where.patchSet) {
      return;
    }
    try {
      const reviewed = await (await this.client()).reviewedFiles(where.number, where.patchSet);
      const before = set.size;
      reviewed.forEach((path) => set.add(path));
      if (set.size !== before) {
        this.changed.fire();
      }
    } catch {
      // A ger serve before API 1.6, or Gerrit said no: the ticks are kept here for the session.
    }
  }

  /** An editor showing a file of the version looked at, scrolled to its last line: seen. Not inside the combined diff. */
  private look(editor: vscode.TextEditor): void {
    const file = this.watched.has(editor.document.uri.toString()) ? this.fileOf(editor.document.uri) : undefined;
    const last = editor.visibleRanges.at(-1);
    if (!file || !last || inCombinedDiff(editor) || this.sets.get(file.where.key)?.has(file.path)) {
      return;
    }
    if (last.end.line >= editor.document.lineCount - 1) {
      void this.mark(file.where, file.path);
    }
  }

  dispose(): void {
    this.subscriptions.forEach((subscription) => subscription.dispose());
    this.changed.dispose();
  }
}

/**
 * Whether `editor` is one file of a combined diff (a tab whose input lists `textDiffs`). An editor
 * with no column of its own is inside another: a side of a diff, or a file of a combined diff.
 */
function inCombinedDiff(editor: vscode.TextEditor): boolean {
  const combined = (group: vscode.TabGroup | undefined) => Boolean((group?.activeTab?.input as { textDiffs?: unknown } | undefined)?.textDiffs);
  if (editor.viewColumn === undefined) {
    return vscode.window.tabGroups.all.some(combined);
  }
  return combined(vscode.window.tabGroups.all.find((g) => g.viewColumn === editor.viewColumn));
}
