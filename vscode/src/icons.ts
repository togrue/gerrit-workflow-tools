import * as vscode from "vscode";

import { Severity, attentionIcon } from "./format";

const COLORS: Record<Severity, string | undefined> = {
  error: "list.errorForeground",
  warning: "list.warningForeground",
  pending: undefined,
  ok: "testing.iconPassed",
};

/** The icon of a change or chain with these attention reasons (`attentionIcon`), coloured by urgency. */
export function attentionThemeIcon(reasons: readonly string[]): vscode.ThemeIcon {
  const { icon: id, severity } = attentionIcon(reasons);
  return icon(id, COLORS[severity]);
}

export function icon(id: string, color?: string): vscode.ThemeIcon {
  return new vscode.ThemeIcon(id, color ? new vscode.ThemeColor(color) : undefined);
}
