// Files as they are in a commit (`ger-file:` documents), which the extension's diffs show.

import * as vscode from "vscode";

export const FILE_SCHEME = "ger-file";

/** A file as it is in a commit: `ger-file:/<path>?<commit>`. */
export function fileUri(rev: string, file: string): vscode.Uri {
  return vscode.Uri.from({ scheme: FILE_SCHEME, path: `/${file}`, query: rev });
}
