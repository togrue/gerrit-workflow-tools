// A view drawn in a webview: the extension hands it plain data (a view model), the page's script
// (media/<name>.js) draws it and sends back what the user did. No Gerrit or git logic lives in
// the page: it cannot do anything but ask.

import * as crypto from "node:crypto";
import * as vscode from "vscode";

export interface Message {
  type: string;
  [field: string]: unknown;
}

function page(webview: vscode.Webview, media: vscode.Uri, name: string): string {
  const nonce = crypto.randomBytes(16).toString("base64");
  const uri = (file: string) => webview.asWebviewUri(vscode.Uri.joinPath(media, file)).toString();
  const csp = `default-src 'none'; style-src ${webview.cspSource}; font-src ${webview.cspSource}; script-src 'nonce-${nonce}'`;
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<link rel="stylesheet" href="${uri("codicon.css")}">
<link rel="stylesheet" href="${uri("views.css")}">
</head>
<body class="${name}">
<div id="root"></div>
<script nonce="${nonce}" src="${uri("common.js")}"></script>
<script nonce="${nonce}" src="${uri(`${name}.js`)}"></script>
</body>
</html>`;
}

export abstract class WebView<Vm> implements vscode.WebviewViewProvider {
  protected view: vscode.WebviewView | undefined;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly name: "stack" | "change",
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    const media = vscode.Uri.joinPath(this.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    view.webview.onDidReceiveMessage((message: Message) => {
      // The page says when it can listen: what was rendered before that is rendered again.
      if (message?.type === "ready") {
        this.render();
      } else if (typeof message?.type === "string") {
        void this.receive(message);
      }
    });
    view.onDidDispose(() => {
      this.view = undefined;
    });
    view.webview.html = page(view.webview, media, this.name);
  }

  /** What the page draws. */
  abstract vm(): Vm;
  /** Something the user did in the page. */
  protected abstract receive(message: Message): unknown;
  /** The view's title and description, which VS Code draws outside the page. */
  protected decorate(_view: vscode.WebviewView): void {}

  render(): void {
    if (this.view) {
      this.decorate(this.view);
      this.post({ type: "render", vm: this.vm() });
    }
  }

  protected post(message: Message): void {
    void this.view?.webview.postMessage(message);
  }

  /** Bring the view on screen; with `preserveFocus`, the keyboard stays where it is. */
  show(preserveFocus: boolean): void {
    this.view?.show(preserveFocus);
  }
}

/** A webview view that only says something: for when the extension has nothing to show in it. */
export function staticView(text: string): vscode.WebviewViewProvider {
  const escaped = text.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c] ?? c);
  return {
    resolveWebviewView(view) {
      view.webview.html = `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'"></head><body><p>${escaped}</p></body></html>`;
    },
  };
}
