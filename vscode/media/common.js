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
    const mark = item.ring ? ring(item.ring.share, item.ring.shape) : icon(item.icon);
    return h("span", { class: `${cls} tone-${item.tone}${item.stale ? " stale" : ""}`, title: item.title }, mark, item.text);
  }

  const SVG = "http://www.w3.org/2000/svg";
  let masks = 0;

  function svg(tag, attrs, ...children) {
    const element = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attrs)) {
      element.setAttribute(name, String(value));
    }
    element.append(...children);
    return element;
  }

  /**
   * The comment ring of src/commentState.ts: the arc is the open share, its shape the state besides
   * the colour: solid and thick (to answer), dashed (your drafts), thin (waiting on a reply), a full
   * ring with a tick (all resolved).
   */
  function ring(share, shape) {
    const circle = (attrs) => svg("circle", { cx: 8, cy: 8, r: 6, fill: "none", pathLength: 100, transform: "rotate(-90 8 8)", ...attrs });
    const arc = Math.max(0, Math.min(1, share)) * 100;
    const parts = [circle({ class: "ring-track", "stroke-width": shape === "solid" ? 3 : 2 })];
    if (shape === "tick") {
      parts.push(svg("path", { class: "ring-tick", d: "M5 8.2l2 2 4-4.2", fill: "none", "stroke-width": 1.6 }));
    } else if (shape === "dashed") {
      // Dashes along the open share: the share is a mask over a dashed circle.
      const id = `ring-mask-${++masks}`;
      parts.push(svg("mask", { id }, circle({ stroke: "white", "stroke-width": 4, "stroke-dasharray": `${arc} 100` })));
      parts.push(circle({ class: "ring-arc", "stroke-width": 2, "stroke-dasharray": "7 5", mask: `url(#${id})` }));
    } else {
      parts.push(circle({ class: "ring-arc", "stroke-width": shape === "solid" ? 3 : 1.2, "stroke-dasharray": `${arc} 100` }));
    }
    return svg("svg", { class: `ring ring-${shape}`, viewBox: "0 0 16 16", width: 14, height: 14, "aria-hidden": "true" }, ...parts);
  }

  /** Set once the extension traces (a render carries `trace`): what the page sends then says when (`at`). */
  let tracing = false;
  const clock = () => (typeof performance === "object" ? performance.timeOrigin + performance.now() : Date.now());

  function post(type, fields) {
    vscode.postMessage(tracing ? { type, at: clock(), ...(fields || {}) } : { type, ...(fields || {}) });
  }

  /** Tell the extension render `seq` is on screen: after the next frame is painted. */
  function painted(seq) {
    const later = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (run) => setTimeout(run, 0);
    later(() => setTimeout(() => post("painted", { trace: seq, at: clock() }), 0));
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
   * The words of a message that must be read whole (views.css `.wraps`): `text`, then what it means
   * under it, then the raw words of git or Gerrit it rests on, as they came.
   */
  function words(text, detail, raw) {
    return h(
      "span",
      { class: "words" },
      h("span", { class: "label" }, text),
      detail ? [" ", h("span", { class: "detail" }, detail)] : null,
      raw ? h("span", { class: "raw" }, raw) : null,
    );
  }

  /**
   * An info row (a `StackInfo` of src/stackRows.ts): Local stack's banner and its lines, a
   * conflicted file in either view. A conflicted file opens to resolve it; its buttons go under it.
   * A conflicted file is a row like a file's, cut when long; every other line says what state
   * things are in, or what to do about it, and wraps.
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
      { class: `info${run ? " clickable" : ""}${row.conflict ? " conflict" : " wraps"}`, title: row.tooltip, role: run ? "button" : undefined, tabindex: run ? "0" : undefined, onclick: run, onkeydown: keys },
      row.icon ? icon(row.icon, row.tone) : h("span", { class: "codicon" }),
      row.conflict ? [h("span", { class: "label" }, row.text), h("span", { class: "detail" }, row.detail || "")] : words(row.text, row.detail, row.raw),
    );
    return row.actions && row.actions.length ? [element, actions(row.actions)] : element;
  }

  /** Call `draw(vm)` for every view model the extension sends, and tell it the page is listening. */
  function listen(draw, other) {
    window.addEventListener("message", (event) => {
      const message = event.data;
      if (message && message.type === "render") {
        tracing = message.trace !== undefined;
        draw(message.vm);
        if (message.trace) {
          painted(message.trace);
        }
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

  // --- The task header (src/taskRows.ts) ---------------------------------------------------

  function mark(item) {
    const glyph = item.ring ? ring(item.ring.share, item.ring.shape) : icon(item.icon);
    return h("span", { class: `mark${item.tone ? ` tone-${item.tone}` : ""}` }, glyph, h("span", { class: "mark-text" }, item.text));
  }

  function step(item) {
    const classes = ["step", item.current ? "current" : "", item.phase ? `phase-${item.phase}` : ""].filter(Boolean).join(" ");
    const go = () => post("taskStep", { key: item.key });
    return h(
      "li",
      {
        class: classes,
        title: item.tooltip,
        tabindex: "0",
        role: "button",
        "aria-current": item.current ? "step" : undefined,
        onclick: go,
        onkeydown: (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            go();
          }
        },
      },
      h("span", { class: "step-position" }, `${item.position}`),
      h("span", { class: "step-title" }, item.title),
      h("span", { class: "marks" }, item.marks.map(mark)),
    );
  }

  /** How the last step ended, in ger's words: its line, the lines after it on hover; tone and icon, never the colour alone. */
  function outcome(item) {
    const title = [item.text, ...item.more].join("\n");
    return h("div", { class: "task-outcome", role: "status", title }, icon(item.ok ? "check" : "warning", item.ok ? "good" : "warn"), h("span", {}, item.text));
  }

  /** A way on in the task header: what it does beside it; refused now, drawn and behaving disabled, with why beside it. */
  function taskAction(item) {
    return h(
      "div",
      { class: `task-action${item.notNow ? " not-now" : ""}` },
      h(
        "button",
        {
          class: `button${item.primary && !item.notNow ? "" : " secondary"}`,
          disabled: Boolean(item.notNow),
          "aria-disabled": item.notNow ? "true" : undefined,
          title: item.tooltip ? `Not now: ${item.tooltip}` : item.notNow ? `Not now: ${item.notNow}` : item.detail,
          onclick: () => post("command", { command: item.command }),
        },
        item.label,
      ),
      h("span", { class: "task-detail", title: item.tooltip }, item.notNow ? `not now: ${item.notNow}` : item.detail),
    );
  }

  /**
   * The task header: the mode in words and where you are, how much is left, the chain's changes
   * oldest first (each state an icon and a word; a click goes there), and the ways out in words.
   */
  function task(vm) {
    const views = h(
      "a",
      {
        class: "task-views",
        href: "#",
        title: vm.views.detail,
        onclick: (event) => {
          event.preventDefault();
          post("command", { command: vm.views.command });
        },
      },
      vm.views.label,
    );
    return h(
      "section",
      { class: `task task-${vm.mode}`, "aria-label": vm.heading },
      h("div", { class: "task-heading" }, icon(vm.mode === "review" ? "eye" : "edit"), h("span", { class: "task-mode", title: vm.title }, vm.heading), h("span", { class: "task-position" }, vm.position), views),
      h("div", { class: "task-left" }, h("span", { class: "task-label" }, "Left: "), vm.left),
      vm.busy ? h("div", { class: "task-busy" }, icon("loading"), h("span", {}, vm.busy)) : null,
      !vm.busy && vm.outcome ? outcome(vm.outcome) : null,
      vm.steps.length ? h("ol", { class: "steps", "aria-label": `The chain, oldest first: ${vm.title}` }, vm.steps.map(step)) : null,
      vm.actions.length ? h("div", { class: "task-actions" }, vm.actions.map(taskAction)) : null,
    );
  }

  window.ger = {
    task,
    outcome,
    h,
    icon,
    badge,
    ring,
    actions,
    info,
    words,
    post,
    panel,
    listen,
    rowSelection,
    getState: () => vscode.getState() || {},
    setState: (state) => vscode.setState(state),
  };
})();
