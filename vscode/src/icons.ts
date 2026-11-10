import * as vscode from "vscode";

import { Severity } from "./format";

const ICONS: Record<Severity, [string, string | undefined]> = {
  error: ["error", "list.errorForeground"],
  warning: ["comment-unresolved", "list.warningForeground"],
  pending: ["clock", undefined],
  ok: ["pass", "testing.iconPassed"],
};

export function severityIcon(severity: Severity): vscode.ThemeIcon {
  const [id, color] = ICONS[severity];
  return new vscode.ThemeIcon(id, color ? new vscode.ThemeColor(color) : undefined);
}

export function icon(id: string, color?: string): vscode.ThemeIcon {
  return new vscode.ThemeIcon(id, color ? new vscode.ThemeColor(color) : undefined);
}
