// The CI findings of the change shown, handed to VS Code as diagnostics (ciDiagnostics.ts decides
// what each one says): source "ger CI", the build as the code, a link to its log. Set when the
// findings come; gone when the Change view leaves the change, so Problems lists only the change
// looked at, never one left behind.

import * as vscode from "vscode";

import { CiContent } from "./api";
import { CI_SOURCE, findingDiagnostics } from "./ciDiagnostics";

export class CiProblems implements vscode.Disposable {
  private readonly collection = vscode.languages.createDiagnosticCollection("ger-ci");

  /** The findings of `contents`, each at its place in the document `uriOf` names for its path. */
  show(contents: readonly CiContent[], introduced: ReadonlySet<string> | null, uriOf: (path: string) => vscode.Uri): void {
    const byFile = new Map<string, { uri: vscode.Uri; diagnostics: vscode.Diagnostic[] }>();
    for (const found of findingDiagnostics(contents, introduced)) {
      const uri = uriOf(found.path);
      // To the end of the line: VS Code fits the range to the line once the file is open.
      const range = new vscode.Range(found.line, found.column, found.line, Number.MAX_SAFE_INTEGER);
      const severity = found.severity === "warning" ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Error;
      const diagnostic = new vscode.Diagnostic(range, found.message, severity);
      diagnostic.source = CI_SOURCE;
      diagnostic.code = found.url ? { value: found.build, target: vscode.Uri.parse(found.url) } : found.build;
      const file = byFile.get(uri.toString()) ?? { uri, diagnostics: [] };
      file.diagnostics.push(diagnostic);
      byFile.set(uri.toString(), file);
    }
    this.collection.clear();
    this.collection.set([...byFile.values()].map(({ uri, diagnostics }) => [uri, diagnostics]));
  }

  /** The change was left: its findings go from the editor and from Problems. */
  clear(): void {
    this.collection.clear();
  }

  /** What is shown now, for the end-to-end transcript. */
  entries(): [vscode.Uri, readonly vscode.Diagnostic[]][] {
    const all: [vscode.Uri, readonly vscode.Diagnostic[]][] = [];
    this.collection.forEach((uri, diagnostics) => {
      all.push([uri, diagnostics]);
    });
    return all;
  }

  dispose(): void {
    this.collection.dispose();
  }
}
