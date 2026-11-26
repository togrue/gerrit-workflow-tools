// Files seen, as the Change view ticks them: Gerrit's per-file *reviewed* flag of the patchset
// looked at (`/reviewed`, ger serve 1.6), so a tick is there in Gerrit's web UI and after a reload
// too, and is drawn only once Gerrit took it (a refusal is said, never ticked); a version Gerrit
// does not have (your local commit) is kept here for the session. A file is
// seen once its diff, opened from the Change view (a click, Next file), was shown to its end, or by
// a click on its tick. The file alone in an editor (opened from a CI finding or a comment) is not its diff.
//
// The combined diff (Open All Changes) does not tick a file on its own: VS Code reports every file in
// it as shown whole, wherever it is scrolled. Read there, Next offers to mark them all seen (`markAll`),
// and so does the Files header.

import * as vscode from "vscode";

import { ApiError, GerApiError, GerClient } from "./api";
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

  /**
   * Tick `path` of `where`, or take the tick off. A patchset's tick is Gerrit's flag: it shows once
   * Gerrit has taken it, and a refusal leaves it as it was and is said (`refused`). A version only
   * here (your local commit) is ticked at once. Undefined when it was taken, else why not.
   */
  async mark(where: SeenWhere, path: string, seen = true): Promise<ApiError | undefined> {
    const set = this.set(where);
    if (set.has(path) === seen) {
      return undefined;
    }
    if (where.number && where.patchSet) {
      const failed = await this.send(() => this.client().then((client) => client.markReviewed(where.number!, where.patchSet!, path, seen)));
      if (failed) {
        this.refused(failed);
        return failed;
      }
    }
    if (seen) {
      set.add(path);
    } else {
      set.delete(path);
    }
    this.changed.fire();
    return undefined;
  }

  /** Tick every one of `paths` of `where` not ticked yet, on Gerrit in one request: as {@link mark}, all or none. */
  async markAll(where: SeenWhere, paths: readonly string[]): Promise<ApiError | undefined> {
    const set = this.set(where);
    const left = paths.filter((path) => !set.has(path));
    if (!left.length) {
      return undefined;
    }
    if (where.number && where.patchSet) {
      const failed = await this.send(() => this.client().then((client) => client.markAllReviewed(where.number!, where.patchSet!, left)));
      if (failed) {
        return failed;
      }
    }
    left.forEach((path) => set.add(path));
    this.changed.fire();
    return undefined;
  }

  /** Told when a tick Gerrit refused is left off; set by the Change view, which says it. */
  refused: (why: ApiError) => void = () => undefined;

  /** Run `request`; why it failed (as ger serve served it), or undefined. */
  private async send(request: () => Promise<unknown>): Promise<ApiError | undefined> {
    try {
      await request();
      return undefined;
    } catch (error) {
      return error instanceof GerApiError ? { code: error.code, message: error.message, reason: error.reason } : { code: "INTERNAL", message: error instanceof Error ? error.message : String(error) };
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

  /**
   * The right side of a file's own diff, scrolled to its last line: seen. Not inside the combined
   * diff, and not the file alone in an editor (a CI finding or a comment opens it at a line: that
   * is not reading the change, state 13).
   */
  private look(editor: vscode.TextEditor): void {
    const file = this.watched.has(editor.document.uri.toString()) ? this.fileOf(editor.document.uri) : undefined;
    const last = editor.visibleRanges.at(-1);
    if (!file || !last || !inOwnDiff(editor) || inCombinedDiff(editor) || this.sets.get(file.where.key)?.has(file.path)) {
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

/** Whether `editor` shows the right side of a diff in a tab of its own, not its file alone. */
function inOwnDiff(editor: vscode.TextEditor): boolean {
  const uri = editor.document.uri.toString();
  const groups = editor.viewColumn === undefined ? vscode.window.tabGroups.all : vscode.window.tabGroups.all.filter((group) => group.viewColumn === editor.viewColumn);
  return groups.some((group) => {
    const input = group.activeTab?.input;
    return input instanceof vscode.TabInputTextDiff && input.modified.toString() === uri;
  });
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
