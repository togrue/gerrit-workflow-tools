// Shared by the pages of the webview views (stack.js, change.js): building DOM, icons, and
// talking to the extension. The pages hold no Gerrit or git logic: they draw the view model the
// extension sends (`{type: "render", vm}`) and say what the user did.

(function () {
  const vscode = acquireVsCodeApi();

  /** An element: `attrs` are attributes, `on…` listeners, and `class`; children are nodes or text, null skipped. */
  function h(tag, attrs, ...children) {
    const element = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) {
        continue;
      }
      if (name.startsWith("on") && typeof value === "function") {
        element.addEventListener(name.slice(2), value);
      } else if (name === "class") {
        element.className = value;
      } else {
        element.setAttribute(name, value === true ? "" : String(value));
      }
    }
    for (const child of children.flat(Infinity)) {
      if (child !== undefined && child !== null && child !== false && child !== "") {
        element.append(child);
      }
    }
    return element;
  }

  /** A codicon; `tone` colours it (good, bad, warn, dim). */
  function icon(id, tone, title) {
    return h("span", { class: `codicon codicon-${id}${tone ? ` tone-${tone}` : ""}`, title, "aria-hidden": title ? undefined : "true" });
  }

  function post(type, fields) {
    vscode.postMessage({ type, ...(fields || {}) });
  }

  /** The panel shown instead of content: a sentence, and maybe one button. */
  function panel(text, link) {
    return h(
      "div",
      { class: "panel" },
      h("p", {}, text),
      link ? h("button", { class: "button", onclick: () => post("command", { command: link.command }) }, link.label) : null,
    );
  }

  /** Call `draw(vm)` for every view model the extension sends, and tell it the page is listening. */
  function listen(draw, other) {
    window.addEventListener("message", (event) => {
      const message = event.data;
      if (message && message.type === "render") {
        draw(message.vm);
      } else if (message && other) {
        other(message);
      }
    });
    post("ready");
  }

  window.ger = {
    h,
    icon,
    post,
    panel,
    listen,
    getState: () => vscode.getState() || {},
    setState: (state) => vscode.setState(state),
  };
})();
