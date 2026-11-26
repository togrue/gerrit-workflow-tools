// "Local stack": the rows of src/stackRows.ts, newest first. Selection (one or several rows) and
// arrow-key walking are common.js's rowSelection; here are the rows, drag and drop with a line
// where the commits will land, and the context object each row hands to VS Code's context menu
// (`webview/context` in package.json).

(function () {
  const { h, icon, info, post, panel, listen, rowSelection } = window.ger;
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
      gerReview: Boolean(vm.review),
    }),
  });

  const BADGE_KINDS = ["comments", "review", "ci"];

  /** One slot on the right of a row; empty when the commit has nothing to say there, so the columns line up. */
  const badge = (item, slot) => window.ger.badge(item, BADGE_KINDS[slot]);

  function rowElement(row, slots) {
    const { classes, ...attributes } = selection.attributes(row);
    const all = ["row", ...classes, row.dim ? "dim" : "", row.merge ? "merge" : "", row.pinned ? "pinned" : ""];
    // Stopped on a conflict: a red warning, the row not dimmed; stopped with the commit applied: a marker.
    // Still to be replayed: a hollow circle as the task header's "to come", not the fading alone (Q4).
    const marker =
      row.marker === "conflict"
        ? icon("warning", "bad", "The rebase stopped here on a conflict")
        : row.marker
          ? icon("debug-stackframe", "warn")
          : row.dim
            ? icon("circle-large-outline", "dim", "Still to be replayed by the rebase")
            : null;
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
        // Not while the stack is frozen (a rebase underway, a staged review): ger edit would refuse.
        vm.frozen ? null : h("button", { class: "codicon codicon-edit", title: "Edit Commit Here", "data-command": "ger.stack.edit" }),
        row.url ? h("button", { class: "codicon codicon-link-external", title: "Open in Gerrit", "data-command": "ger.openInGerrit" }) : null,
      ),
      h("span", { class: "badges" }, slots.map((slot) => (slot === 0 && row.badges[0] ? commentBadge(row) : badge(row.badges[slot], slot)))),
    );
  }

  /** The ready boundary between the commits held back (above) and those a push sends (below), as ger log draws it. */
  function boundaryLine(boundary) {
    return h("div", { class: "boundary", role: "separator", "aria-label": boundary.text, title: boundary.tooltip }, h("span", {}, boundary.text));
  }

  // --- The comments of a row, on hovering its comment badge --------------------------

  let hover = null;
  let hoverTimer = null;

  function closeHover() {
    clearTimeout(hoverTimer);
    if (hover) {
      hover.remove();
      hover = null;
    }
  }

  /** The comment badge: hovered a moment, it asks for the comments, which come as a "comments" message. */
  function commentBadge(row) {
    const element = badge(row.badges[0], 0);
    element.addEventListener("mouseenter", () => {
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => post("comments", { sha: row.sha }), 350);
    });
    element.addEventListener("mouseleave", () => {
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => (hover && hover.matches(":hover") ? undefined : closeHover()), 300);
    });
    element.setAttribute("data-sha", row.sha);
    return element;
  }

  /** The hover: who said what where, and Go there, Reply Done and Apply on each, as Review chains' hover has them. */
  function showComments(message) {
    closeHover();
    const anchor = list && list.querySelector(`.badge[data-sha="${CSS.escape(message.sha)}"]`);
    if (!anchor || !message.entries.length) {
      return;
    }
    const link = (entry, action, label) =>
      h("button", { class: "link", onclick: (event) => (event.stopPropagation(), closeHover(), post("commentLink", { sha: message.sha, root: entry.root, action })) }, label);
    const entries = message.entries.map((entry) =>
      h(
        "div",
        { class: "hover-entry" },
        h("div", {}, icon(entry.tone === "open" ? "comment-unresolved" : entry.tone === "draft" ? "comment-draft" : entry.tone === "resolved" ? "pass" : "comment", entry.tone === "open" ? "warn" : entry.tone === "draft" ? "info" : undefined), h("span", { class: "label" }, entry.author), h("span", { class: "detail" }, `${entry.where} · ${entry.state}`)),
        entry.text ? h("div", { class: "comment-text" }, entry.text) : null,
        h("div", { class: "hover-links" }, link(entry, "goto", "Go there"), entry.done ? link(entry, "done", "Reply Done") : null, entry.apply ? link(entry, "apply", "Apply") : null),
      ),
    );
    const more = message.more > 0 ? h("div", { class: "detail" }, `…and ${message.more} more: the Change view lists them all.`) : null;
    hover = h("div", { class: "comment-hover", role: "dialog", "aria-label": "Comments" }, entries, more);
    hover.addEventListener("mouseleave", () => {
      hoverTimer = setTimeout(closeHover, 300);
    });
    hover.addEventListener("mouseenter", () => clearTimeout(hoverTimer));
    hover.addEventListener("mousedown", (event) => event.stopPropagation());
    document.body.appendChild(hover);
    // Below the badge, or above it where there is more room: the view may be only a few rows high.
    const box = anchor.getBoundingClientRect();
    const [width, height] = [Math.min(360, document.documentElement.clientWidth - 16), document.documentElement.clientHeight];
    const [below, above] = [height - box.bottom - 8, box.top - 8];
    const down = below >= 160 || below >= above;
    hover.style.width = `${width}px`;
    hover.style.maxHeight = `${Math.max(80, down ? below : above)}px`;
    hover.style.left = `${Math.max(8, Math.min(box.right - width, document.documentElement.clientWidth - width - 8))}px`;
    const scroll = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
    hover.style.top = `${scroll + (down ? box.bottom + 4 : Math.max(4, box.top - 4 - hover.offsetHeight))}px`;
  }

  document.addEventListener("keydown", (event) => (event.key === "Escape" ? closeHover() : undefined));
  document.addEventListener("mousedown", () => closeHover());

  function draw(next) {
    vm = next;
    closeHover(); // its badge is drawn anew
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
      vm.rows.map((row) => (vm.boundary && row.key === vm.boundary.under ? [rowElement(row, slots), boundaryLine(vm.boundary)] : rowElement(row, slots))),
      line,
    );
    wire(list);
    // How much of the stack is where, in ger log's words: one line above the rows.
    const summary = vm.summary ? h("div", { class: "stack-summary", role: "status" }, vm.summary) : null;
    root.replaceChildren(...[h("div", { class: "infos" }, vm.info.map(info)), summary, list].filter(Boolean));
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
  listen(draw, (message) => (message.type === "comments" ? showComments(message) : undefined));
})();
