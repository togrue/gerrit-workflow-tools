// The extension's settings (`ger.*`), read where they are used so a change applies at once.

import * as vscode from "vscode";

export function setting<T>(name: string, fallback: T): T {
  return vscode.workspace.getConfiguration("ger").get<T>(name, fallback);
}
