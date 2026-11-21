// Shared by the pages of the webview views (stack.js, change.js): building DOM, icons, talking to
// the extension, and the selection of a list of rows. The pages hold no Gerrit or git logic: they
// draw the view model the extension sends (`{type: "render", vm}`) and say what the user did.

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

  /**
   * A vote or comment count as the Local stack shows it (a `Badge` of src/stackRows.ts). With no
   * `item`, an empty slot of `kind`, so columns of badges line up.
   */
  function badge(item, kind = item && item.kind) {
    const cls = `badge badge-${kind}`;
    if (!item) {
      return h("span", { class: cls });
    }
    return h("span", { class: `${cls} tone-${item.tone}${item.stale ? " stale" : ""}`, title: item.title }, icon(item.icon), item.text);
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

  /** Buttons that run commands (a `StackAction` of src/stackRows.ts): the one to press now drawn as the primary button. */
  function actions(list) {
    const buttons = list.map((action) =>
      h(
        "button",
        { class: `button${action.primary ? "" : " secondary"}`, title: action.title, onclick: () => post("command", { command: action.command.id, args: action.command.args }) },
        action.label,
      ),
    );
    return h("div", { class: "info-buttons" }, buttons);
  }

  /**
   * An info row (a `StackInfo` of src/stackRows.ts): Local stack's banner and its lines, a
   * conflicted file in either view. A conflicted file opens to resolve it; its buttons go under it.
   */
  function info(row) {
    const run = row.conflict
      ? () => post("conflict", { path: row.conflict })
      : row.command
        ? () => post("command", { command: row.command.id, args: row.command.args })
        : undefined;
    const keys = run ? (event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), run()) : undefined;
    const element = h(
      "div",
      { class: `info${run ? " clickable" : ""}${row.conflict ? " conflict" : ""}`, title: row.tooltip, role: run ? "button" : undefined, tabindex: run ? "0" : undefined, onclick: run, onkeydown: keys },
      row.icon ? icon(row.icon, row.tone) : h("span", { class: "codicon" }),
      h("span", { class: "label" }, row.text),
      h("span", { class: "detail" }, row.detail || ""),
    );
    return row.actions && row.actions.length ? [element, actions(row.actions)] : element;
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

  /**
   * The selection of a list of rows, as Local stack has it: one or several rows, the focused one,
   * walking with the arrow keys, and what a row hands VS Code's context menu. `rows()` gives the
   * rows as shown, each with a `key` that survives a redraw. `open(row, keepFocus)` shows a row;
   * `context(row, rows)` is its `data-vscode-context`, `rows` being what a command on it acts on.
   * The page draws the list; `wire(list)` makes its `.row` elements (with `data-key`) answer.
   */
  function rowSelection({ rows, open, context }) {
    let selected = new Set();
    let focused = null;
    let anchor = null;
    let list = null;
    let openTimer;

    const rowOf = (key) => rows().find((row) => row.key === key);
    const indexOf = (key) => rows().findIndex((row) => row.key === key);
    const selectedRows = () => rows().filter((row) => selected.has(row.key));

    /** What a command from the context menu gets: the row, and every selected row when it is one of several. */
    const contextOf = (row) => JSON.stringify(context(row, selected.has(row.key) && selected.size > 1 ? selectedRows() : [row]));

    /** What a row element needs from the selection: its classes, and its attributes. */
    function attributes(row) {
      return {
        classes: [selected.has(row.key) ? "selected" : "", focused === row.key ? "focused" : ""],
        role: "option",
        "aria-selected": selected.has(row.key) ? "true" : "false",
        "data-key": row.key,
        "data-vscode-context": contextOf(row),
      };
    }

    /** The rows changed: forget the ones that went. */
    function keep() {
      const keys = new Set(rows().map((row) => row.key));
      selected = new Set([...selected].filter((key) => keys.has(key)));
      focused = keys.has(focused) ? focused : null;
      anchor = keys.has(anchor) ? anchor : focused;
    }

    /** Selection changed: the rows' classes and context objects follow, without redrawing. */
    function refresh() {
      if (!list) {
        return;
      }
      for (const element of list.querySelectorAll(".row")) {
        const row = rowOf(element.dataset.key);
        const on = selected.has(row.key);
        element.classList.toggle("selected", on);
        element.classList.toggle("focused", focused === row.key);
        element.setAttribute("aria-selected", on ? "true" : "false");
        element.setAttribute("data-vscode-context", contextOf(row));
      }
    }

    function select(key, mode) {
      if (mode === "range" && anchor !== null) {
        const [from, to] = [indexOf(anchor), indexOf(key)].sort((a, b) => a - b);
        selected = new Set(rows().slice(from, to + 1).map((row) => row.key));
      } else if (mode === "toggle") {
        if (selected.has(key)) {
          selected.delete(key);
        } else {
          selected.add(key);
        }
        anchor = key;
      } else {
        selected = new Set([key]);
        anchor = key;
      }
      focused = key;
      refresh();
    }

    /**
     * Show the row. While walking with the arrow keys: after a short pause, the keyboard stays here.
     * The row is looked up when the pause is over: a redraw in between may have rewritten or dropped it.
     */
    function show(key, keepFocus, delay) {
      clearTimeout(openTimer);
      openTimer = setTimeout(() => {
        const row = rowOf(key);
        if (row) {
          open(row, keepFocus);
        }
      }, delay);
    }

    function step(to, extend) {
      const all = rows();
      if (!all.length) {
        return;
      }
      const key = all[Math.max(0, Math.min(all.length - 1, to))].key;
      select(key, extend ? "range" : "single");
      const element = list.querySelector(`.row[data-key="${CSS.escape(key)}"]`);
      if (element) {
        element.scrollIntoView({ block: "nearest" });
      }
      show(key, true, 150);
    }

    /** Clicks, keys and right-clicks on the rows of `element`. A click or Enter on a row's button is the page's. */
    function wire(element) {
      list = element;
      element.addEventListener("click", (event) => {
        const row = event.target.closest(".row");
        if (!row || event.target.closest("button")) {
          return;
        }
        const key = row.dataset.key;
        select(key, event.shiftKey ? "range" : event.ctrlKey || event.metaKey ? "toggle" : "single");
        show(key, true, 0);
      });
      element.addEventListener("dblclick", (event) => {
        const row = event.target.closest(".row");
        if (row && !event.target.closest("button")) {
          show(row.dataset.key, false, 0);
        }
      });
      element.addEventListener("contextmenu", (event) => {
        const row = event.target.closest(".row");
        if (row && !selected.has(row.dataset.key)) {
          select(row.dataset.key, "single");
        }
      });
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target.closest("button")) {
          return; // it presses the button
        }
        const at = focused === null ? -1 : indexOf(focused);
        const keys = {
          ArrowDown: () => step(at + 1, event.shiftKey),
          ArrowUp: () => step(at < 0 ? 0 : at - 1, event.shiftKey),
          Home: () => step(0, event.shiftKey),
          End: () => step(rows().length - 1, event.shiftKey),
          Enter: () => focused !== null && show(focused, false, 0),
          Escape: () => focused !== null && select(focused, "single"),
        };
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
          selected = new Set(rows().map((row) => row.key));
          refresh();
        } else if (!keys[event.key] || keys[event.key]() === false) {
          return; // with no focused row, Enter and Escape are VS Code's
        }
        event.preventDefault();
      });
    }

    return { attributes, keep, wire, select, selectedRows, rowOf, has: (key) => selected.has(key) };
  }

  window.ger = {
    h,
    icon,
    badge,
    actions,
    info,
    post,
    panel,
    listen,
    rowSelection,
    getState: () => vscode.getState() || {},
    setState: (state) => vscode.setState(state),
  };
})();
