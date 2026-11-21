// "Local stack": the rows of src/stackRows.ts, newest first. Selection (one or several rows) and
// arrow-key walking are common.js's rowSelection; here are the rows, drag and drop with a line
// where the commits will land, and the context object each row hands to VS Code's context menu
// (`webview/context` in package.json).

(function () {
  const { h, icon, post, panel, listen, rowSelection } = window.ger;
  const root = document.getElementById("root");

  let vm = { info: [], rows: [], frozen: null, movable: 0 };
  let list = null;
  let line = null;
  let dragging = false;

  /** Row keys are the Change-Id, so a rewrite keeps the selection. */
  const selection = rowSelection({
    rows: () => vm.rows,
    open: (row, keepFocus) => post("open", { sha: row.sha, keepFocus }),
    context: (row, rows) => ({
      webviewSection: "commit",
      preventDefaultContextMenuItems: true,
      sha: row.sha,
      shas: rows.map((r) => r.sha),
      gerUrl: Boolean(row.url),
      gerSquash: row.squash,
      gerChangeId: rows.some((r) => r.changeId),
      gerMulti: rows.length > 1,
    }),
  });

  const BADGE_KINDS = ["comments", "review", "ci"];

  /** One slot on the right of a row; empty when the commit has nothing to say there, so the columns line up. */
  const badge = (item, slot) => window.ger.badge(item, BADGE_KINDS[slot]);

  function rowElement(row, slots) {
    const { classes, ...attributes } = selection.attributes(row);
    const all = ["row", ...classes, row.dim ? "dim" : "", row.merge ? "merge" : "", row.pinned ? "pinned" : ""];
    // Stopped on a conflict: a red warning, the row not dimmed; stopped with the commit applied: a marker.
    const marker = row.marker === "conflict" ? icon("warning", "bad", "The rebase stopped here on a conflict") : row.marker ? icon("debug-stackframe", "warn") : null;
    return h(
      "div",
      {
        class: all.filter(Boolean).join(" "),
        ...attributes,
        draggable: "true",
        title: row.tooltip,
      },
      h("span", { class: "marker" }, marker),
      h("span", { class: `codicon codicon-${row.syncIcon} sync sync-${row.sync}`, title: row.syncTitle }),
      row.merge ? icon("git-merge", undefined, "A merge commit: what it brings in is not listed") : null,
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
    const run = info.conflict
      ? () => post("conflict", { path: info.conflict })
      : info.command
        ? () => post("command", { command: info.command.id, args: info.command.args })
        : undefined;
    const keys = run ? (event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), run()) : undefined;
    const row = h(
      "div",
      { class: `info${run ? " clickable" : ""}${info.conflict ? " conflict" : ""}`, title: info.tooltip, role: run ? "button" : undefined, tabindex: run ? "0" : undefined, onclick: run, onkeydown: keys },
      info.icon ? icon(info.icon, info.tone) : h("span", { class: "codicon" }),
      h("span", { class: "label" }, info.text),
      h("span", { class: "detail" }, info.detail || ""),
    );
    if (!info.actions || !info.actions.length) {
      return row;
    }
    // The way on from here, under the sentence it belongs to: the one to press now drawn as the primary button.
    const buttons = info.actions.map((action) =>
      h(
        "button",
        { class: `button${action.primary ? "" : " secondary"}`, title: action.title, onclick: () => post("command", { command: action.command.id, args: action.command.args }) },
        action.label,
      ),
    );
    return [row, h("div", { class: "info-buttons" }, buttons)];
  }

  function draw(next) {
    vm = next;
    selection.keep();
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

  /** Whether dropping the selection at `gap` changes the order at all. Below the newest merge nothing lands. */
  function moves(gap) {
    if (gap > vm.movable) {
      return false;
    }
    const keys = vm.rows.map((row) => row.key);
    const stays = (key) => !selection.has(key);
    const next = [...keys.slice(0, gap).filter(stays), ...keys.filter((key) => selection.has(key)), ...keys.slice(gap).filter(stays)];
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
    selection.wire(element);
    element.addEventListener("click", (event) => {
      const row = event.target.closest(".row");
      const button = event.target.closest("button");
      if (row && button) {
        post("command", { command: button.dataset.command, args: [{ sha: selection.rowOf(row.dataset.key).sha }] });
      }
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
      if (!selection.has(row.dataset.key)) {
        selection.select(row.dataset.key, "single");
      }
      if (selection.selectedRows().some((r) => r.pinned)) {
        event.preventDefault();
        post("pinned"); // the extension says why the merge and what is below it stay
        return;
      }
      dragging = true;
      list.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", selection.selectedRows().map((r) => r.sha).join("\n"));
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
      const shas = selection.selectedRows().map((row) => row.sha);
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
      const shas = selection.selectedRows().map((row) => row.sha);
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
