import * as vscode from "vscode";

import { Attended, Severity } from "./api";
import { attentionIcon } from "./format";

const COLORS: Record<Severity, string | undefined> = {
  error: "list.errorForeground",
  warning: "list.warningForeground",
  pending: undefined,
  ok: "testing.iconPassed",
};

/** The icon of a change or chain with its attention reasons (`attentionIcon`), coloured by their severity. */
export function attentionThemeIcon(item: Readonly<Attended>): vscode.ThemeIcon {
  const { icon: id, severity } = attentionIcon(item);
  return icon(id, COLORS[severity]);
}

export function icon(id: string, color?: string): vscode.ThemeIcon {
  return new vscode.ThemeIcon(id, color ? new vscode.ThemeColor(color) : undefined);
}
