// "Local stack": the rows of src/stackRows.ts, newest first. Selection (one or several rows),
// arrow-key walking, drag and drop with a line where the commits will land, and the context
// object each row hands to VS Code's context menu (`webview/context` in package.json).

(function () {
  const { h, icon, post, panel, listen } = window.ger;
  const root = document.getElementById("root");

  let vm = { info: [], rows: [], frozen: null };
  /** Row keys (the Change-Id, so a rewrite keeps the selection). */
  let selected = new Set();
  let focused = null;
  let anchor = null;
  let list = null;
  let line = null;
  let dragging = false;
  let openTimer;

  const CLOUD_ICON = { "not-pushed": "cloud-upload", merged: "check" };
  const rowOf = (key) => vm.rows.find((row) => row.key === key);
  const indexOf = (key) => vm.rows.findIndex((row) => row.key === key);
  const selectedRows = () => vm.rows.filter((row) => selected.has(row.key));

  /** What a command from the context menu gets: the row, and every selected row when it is one of several. */
  function context(row) {
    const rows = selected.has(row.key) && selected.size > 1 ? selectedRows() : [row];
    return JSON.stringify({
      webviewSection: "commit",
      preventDefaultContextMenuItems: true,
      sha: row.sha,
      shas: rows.map((r) => r.sha),
      gerUrl: Boolean(row.url),
      gerSquash: row.squash,
      gerChangeId: rows.some((r) => r.changeId),
      gerMulti: rows.length > 1,
    });
  }

  const BADGE_KINDS = ["comments", "review", "ci"];

  /** One slot on the right of a row; empty when the commit has nothing to say there, so the columns line up. */
  function badge(item, slot) {
    const kind = `badge badge-${BADGE_KINDS[slot]}`;
    if (!item) {
      return h("span", { class: kind });
    }
    return h("span", { class: `${kind} tone-${item.tone}${item.stale ? " stale" : ""}`, title: item.title }, icon(item.icon), item.text);
  }

  function rowElement(row, slots) {
    const classes = ["row", selected.has(row.key) ? "selected" : "", focused === row.key ? "focused" : "", row.dim ? "dim" : ""];
    const marker = row.marker ? icon("debug-stackframe", row.marker === "conflict" ? "bad" : "warn") : null;
    return h(
      "div",
      {
        class: classes.filter(Boolean).join(" "),
        role: "option",
        "aria-selected": selected.has(row.key) ? "true" : "false",
        draggable: "true",
        title: row.tooltip,
        "data-key": row.key,
        "data-vscode-context": context(row),
      },
      h("span", { class: "marker" }, marker),
      h("span", { class: `codicon codicon-${CLOUD_ICON[row.cloud] || "cloud"} cloud cloud-${row.cloud}`, title: row.cloudTitle }),
      h("span", { class: "label" }, row.summary),
      h("span", { class: "detail" }, row.notes.join(" · ")),
      h(
        "span",
        { class: "actions" },
        h("button", { class: "codicon codicon-edit", title: "Edit Commit Here", "data-command": "ger.stack.edit" }),
        row.url ? h("button", { class: "codicon codicon-link-external", title: "Open in Gerrit", "data-command": "ger.openInGerrit" }) : null,
      ),
      h("span", { class: "badges" }, slots.map((slot) => badge(row.badges[slot], slot))),
    );
  }

  function infoElement(info) {
    const run = info.command ? () => post("command", { command: info.command.id, args: info.command.args }) : undefined;
    return h(
      "div",
      { class: `info${run ? " clickable" : ""}`, title: info.tooltip, role: run ? "button" : undefined, tabindex: run ? "0" : undefined, onclick: run },
      info.icon ? icon(info.icon, info.tone) : h("span", { class: "codicon" }),
      h("span", { class: "label" }, info.text),
      h("span", { class: "detail" }, info.detail || ""),
    );
  }

  function draw(next) {
    vm = next;
    const keys = new Set(vm.rows.map((row) => row.key));
    selected = new Set([...selected].filter((key) => keys.has(key)));
    focused = keys.has(focused) ? focused : null;
    anchor = keys.has(anchor) ? anchor : focused;
    const hadFocus = list !== null && document.activeElement === list;
    const scroll = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
    if (vm.waiting) {
      root.replaceChildren(panel(vm.waiting.text, vm.waiting.link));
      list = null;
      return;
    }
    // Only the badge kinds some row has get a column, so the rows line up without empty space.
    const slots = [0, 1, 2].filter((slot) => vm.rows.some((row) => row.badges[slot]));
    line = h("div", { class: "dropline", hidden: true });
    list = h(
      "div",
      {
        class: `list${vm.rows.some((row) => row.marker || row.dim) ? " rebasing" : ""}`,
        role: "listbox",
        tabindex: "0",
        "aria-multiselectable": "true",
        "aria-label": "Local stack, newest first",
      },
      vm.rows.map((row) => rowElement(row, slots)),
      line,
    );
    wire(list);
    root.replaceChildren(h("div", { class: "infos" }, vm.info.map(infoElement)), list);
    if (document.scrollingElement) {
      document.scrollingElement.scrollTop = scroll;
    }
    if (hadFocus) {
      list.focus();
    }
  }

  /** Selection changed: the rows' classes and context objects follow, without redrawing. */
  function refresh() {
    for (const element of list.querySelectorAll(".row")) {
      const row = rowOf(element.dataset.key);
      const on = selected.has(row.key);
      element.classList.toggle("selected", on);
      element.classList.toggle("focused", focused === row.key);
      element.setAttribute("aria-selected", on ? "true" : "false");
      element.setAttribute("data-vscode-context", context(row));
    }
  }

  function select(key, mode) {
    if (mode === "range" && anchor !== null) {
      const [from, to] = [indexOf(anchor), indexOf(key)].sort((a, b) => a - b);
      selected = new Set(vm.rows.slice(from, to + 1).map((row) => row.key));
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

  /** Show the commit in the Change view. While walking with the arrow keys: after a short pause, the keyboard stays here. */
  function open(key, keepFocus, delay) {
    clearTimeout(openTimer);
    const row = rowOf(key);
    if (row) {
      openTimer = setTimeout(() => post("open", { sha: row.sha, keepFocus }), delay);
    }
  }

  function step(to, extend) {
    if (!vm.rows.length) {
      return;
    }
    const index = Math.max(0, Math.min(vm.rows.length - 1, to));
    const key = vm.rows[index].key;
    select(key, extend ? "range" : "single");
    const element = list.querySelector(`.row[data-key="${CSS.escape(key)}"]`);
    if (element) {
      element.scrollIntoView({ block: "nearest" });
    }
    open(key, true, 150);
  }

  // --- Drag and drop ----------------------------------------------------------------

  /** Where a drop at `y` lands: the gap above row `n`, or under the last row. */
  function gapAt(y) {
    const rows = [...list.querySelectorAll(".row")];
    const index = rows.findIndex((element) => {
      const box = element.getBoundingClientRect();
      return y < box.top + box.height / 2;
    });
    return index < 0 ? rows.length : index;
  }

  /** Whether dropping the selection at `gap` changes the order at all. */
  function moves(gap) {
    const keys = vm.rows.map((row) => row.key);
    const stays = (key) => !selected.has(key);
    const next = [...keys.slice(0, gap).filter(stays), ...keys.filter((key) => selected.has(key)), ...keys.slice(gap).filter(stays)];
    return next.some((key, index) => key !== keys[index]);
  }

  function showLine(gap) {
    const rows = [...list.querySelectorAll(".row")];
    if (!rows.length || !moves(gap)) {
      line.hidden = true;
      return;
    }
    const last = rows[rows.length - 1];
    const top = gap < rows.length ? rows[gap].offsetTop : last.offsetTop + last.offsetHeight;
    line.style.top = `${Math.max(0, top - 1)}px`;
    line.hidden = false;
  }

  function endDrag() {
    dragging = false;
    line.hidden = true;
    list.classList.remove("dragging");
  }

  function wire(element) {
    element.addEventListener("click", (event) => {
      const row = event.target.closest(".row");
      if (!row) {
        return;
      }
      const key = row.dataset.key;
      const button = event.target.closest("button");
      if (button) {
        post("command", { command: button.dataset.command, args: [{ sha: rowOf(key).sha }] });
        return;
      }
      select(key, event.shiftKey ? "range" : event.ctrlKey || event.metaKey ? "toggle" : "single");
      open(key, true, 0);
    });
    element.addEventListener("dblclick", (event) => {
      const row = event.target.closest(".row");
      if (row && !event.target.closest("button")) {
        open(row.dataset.key, false, 0);
      }
    });
    element.addEventListener("contextmenu", (event) => {
      const row = event.target.closest(".row");
      if (row && !selected.has(row.dataset.key)) {
        select(row.dataset.key, "single");
      }
    });
    element.addEventListener("keydown", (event) => {
      const at = focused === null ? -1 : indexOf(focused);
      const keys = {
        ArrowDown: () => step(at + 1, event.shiftKey),
        ArrowUp: () => step(at < 0 ? 0 : at - 1, event.shiftKey),
        Home: () => step(0, event.shiftKey),
        End: () => step(vm.rows.length - 1, event.shiftKey),
        Enter: () => focused !== null && open(focused, false, 0),
        Escape: () => focused !== null && select(focused, "single"),
      };
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
        selected = new Set(vm.rows.map((row) => row.key));
        refresh();
      } else if (keys[event.key]) {
        keys[event.key]();
      } else {
        return;
      }
      event.preventDefault();
    });
    element.addEventListener("dragstart", (event) => {
      const row = event.target.closest(".row");
      if (!row || vm.frozen) {
        event.preventDefault();
        if (row) {
          post("frozen"); // the extension says why nothing can move
        }
        return;
      }
      if (!selected.has(row.dataset.key)) {
        select(row.dataset.key, "single");
      }
      dragging = true;
      list.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", selectedRows().map((r) => r.sha).join("\n"));
    });
    element.addEventListener("dragover", (event) => {
      if (dragging) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        showLine(gapAt(event.clientY));
      }
    });
    element.addEventListener("dragleave", (event) => {
      if (dragging && !list.contains(event.relatedTarget)) {
        line.hidden = true;
      }
    });
    element.addEventListener("drop", (event) => {
      if (!dragging) {
        return;
      }
      event.preventDefault();
      const gap = gapAt(event.clientY);
      const shas = selectedRows().map((row) => row.sha);
      const changes = moves(gap);
      endDrag();
      if (changes) {
        post("move", { shas, gap });
      }
    });
    element.addEventListener("dragend", endDrag);
  }

  // Above the first row and under the last is still the list: a drop there puts the commits at that end.
  document.addEventListener("dragover", (event) => {
    if (dragging && list && !list.contains(event.target)) {
      event.preventDefault();
      showLine(gapAt(event.clientY));
    }
  });
  document.addEventListener("drop", (event) => {
    if (dragging && list && !list.contains(event.target)) {
      event.preventDefault();
      const gap = gapAt(event.clientY);
      const shas = selectedRows().map((row) => row.sha);
      const changes = moves(gap);
      endDrag();
      if (changes) {
        post("move", { shas, gap });
      }
    }
  });

  document.body.setAttribute("data-vscode-context", JSON.stringify({ preventDefaultContextMenuItems: true }));
  listen(draw);
})();
