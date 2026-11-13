// "Change": the view model of src/changeRows.ts. The commit message is a text box (Apply writes
// it to the commit), two pickers choose the version and what it is compared with, and the
// files, comment chains and CI findings are rows that open what they name.

(function () {
  const { h, icon, post, panel, listen, getState, setState } = window.ger;
  const root = document.getElementById("root");

  let vm = {};
  /** Commit messages being typed, by change: kept while the view is hidden or redrawn. */
  const drafts = getState().drafts || {};
  /** Sections the user opened or closed, by name: their choice wins over the default. */
  const toggled = {};
  /** Comment chains whose replies are shown. */
  const expanded = new Set();
  /** The message block, kept across redraws so typing is never interrupted. */
  let editor = null;
  let revealed = null;
  /** The change the page was last drawn for: another one starts with its sections as they come. */
  let shownKey;

  const saveDrafts = () => setState({ drafts });

  // --- Commit message ---------------------------------------------------------------

  function buildEditor(key) {
    const box = h("textarea", { class: "message", spellcheck: "false", "aria-label": "Commit message" });
    const apply = h("button", { class: "button", title: "Rewrite the commit with this message (Ctrl+Enter)" }, "Apply");
    const revert = h("button", { class: "button secondary", title: "Back to the commit's message" }, "Revert");
    const note = h("span", { class: "detail" });
    const author = h("span", { class: "detail" });
    const bar = h("div", { class: "message-bar" }, apply, revert, note);
    const block = h("div", { class: "message-block" }, h("div", { class: "heading" }, h("span", { class: "label" }, "Commit message"), author), box, bar);
    const state = { key, base: "", block, box, author };
    const fit = () => {
      box.rows = Math.max(3, Math.min(16, box.value.split("\n").length + 1));
    };
    state.sync = () => {
      const changed = box.value !== state.base;
      if (changed && !box.readOnly) {
        drafts[state.key] = box.value;
      } else {
        delete drafts[state.key];
      }
      saveDrafts();
      apply.hidden = revert.hidden = !changed || box.readOnly;
      note.textContent = box.readOnly ? box.title : changed ? "not applied yet" : "";
      fit();
    };
    const send = () => {
      if (box.value !== state.base && !box.readOnly) {
        note.textContent = "applying…";
        post("message", { text: box.value });
      }
    };
    box.addEventListener("input", state.sync);
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        send();
      }
    });
    apply.addEventListener("click", send);
    revert.addEventListener("click", () => {
      box.value = state.base;
      state.sync();
    });
    return state;
  }

  function messageBlock(message) {
    if (!editor || editor.key !== message.key) {
      editor = buildEditor(message.key);
      editor.box.value = drafts[message.key] ?? message.text;
    } else if (editor.box.value === editor.base || drafts[message.key] === undefined) {
      // Nothing typed: follow the commit. Something typed stays, whatever happened underneath.
      editor.box.value = message.text;
    }
    editor.base = message.text;
    editor.box.readOnly = Boolean(message.readOnly);
    editor.box.title = message.readOnly || "";
    editor.author.textContent = message.author;
    editor.sync();
    return editor.block;
  }

  // --- Rows -------------------------------------------------------------------------

  /** A row that does something: clickable, and reachable with the keyboard. */
  function item(attrs, ...children) {
    return h("div", { ...attrs, class: `item ${attrs.class || ""}`, role: "button", tabindex: "0" }, ...children);
  }

  function section(name, title, summary, open, body, onOpen) {
    const isOpen = toggled[name] ?? open;
    const header = item(
      {
        class: "section-head",
        "aria-expanded": isOpen ? "true" : "false",
        onclick: () => {
          toggled[name] = !isOpen;
          if (!isOpen && onOpen) {
            onOpen();
          }
          draw(vm);
        },
      },
      icon(isOpen ? "chevron-down" : "chevron-right"),
      h("span", { class: "label" }, title),
      summary,
    );
    return h("div", { class: "section" }, header, isOpen ? body() : null);
  }

  function counts(added, deleted) {
    return h("span", { class: "counts" }, h("span", { class: "added" }, `+${added}`), h("span", { class: "deleted" }, `−${deleted}`));
  }

  function fileRow(file, index) {
    return item(
      { class: "file", title: file.tooltip, onclick: () => post("file", { index }) },
      icon("file"),
      h("span", { class: "label" }, file.name),
      h("span", { class: "detail" }, [file.dir, file.state].filter(Boolean).join(" · ")),
      file.added === null ? h("span", { class: "counts detail" }, "binary") : counts(file.added, file.deleted),
    );
  }

  function threadRows(thread) {
    const act = (action) => (event) => {
      event.stopPropagation();
      post("thread", { id: thread.id, action });
    };
    const open = expanded.has(thread.id);
    const twist = thread.replies.length
      ? h("button", {
          class: `codicon codicon-chevron-${open ? "down" : "right"} twist`,
          title: `${thread.replies.length} ${thread.replies.length === 1 ? "reply" : "replies"}`,
          onclick: (event) => {
            event.stopPropagation();
            if (open) {
              expanded.delete(thread.id);
            } else {
              expanded.add(thread.id);
            }
            draw(vm);
          },
        })
      : h("span", { class: "twist" });
    const row = item(
      { class: `thread${revealed === thread.id ? " revealed" : ""}`, title: thread.tooltip, "data-id": thread.id, onclick: act("open") },
      twist,
      icon(thread.icon, thread.tone),
      h("span", { class: "label" }, thread.text),
      h("span", { class: "detail" }, thread.notes.join(" · ")),
      h(
        "span",
        { class: "actions" },
        thread.reply ? h("button", { class: "codicon codicon-reply", title: "Reply as Draft…", onclick: act("reply") }) : null,
        thread.open ? h("button", { class: "codicon codicon-check", title: "Reply Done and Resolve as Draft", onclick: act("replyDone") }) : null,
        thread.open && !thread.held ? h("button", { class: "codicon codicon-person", title: "Keep for Me (Not for the Agent)", onclick: act("hold") }) : null,
        thread.open && thread.held ? h("button", { class: "codicon codicon-hubot", title: "Let the Agent Handle It", onclick: act("release") }) : null,
      ),
    );
    const replies = open
      ? thread.replies.map((reply) =>
          h(
            "div",
            { class: "reply", title: reply.text },
            icon(reply.state ? "edit" : "account"),
            h("span", { class: "label" }, reply.author),
            h("span", { class: "detail" }, `${reply.state ? `[${reply.state}] ` : ""}${reply.text.split("\n")[0]}`),
          ),
        )
      : [];
    return [row, ...replies];
  }

  function ciBody(ci) {
    if (ci.state === "closed" || ci.state === "loading") {
      return h("div", { class: "reply" }, h("span", { class: "codicon codicon-loading codicon-modifier-spin" }), h("span", { class: "detail" }, "Fetching the findings…"));
    }
    if (ci.state === "error") {
      return h("div", { class: "reply" }, icon("error", "bad"), h("span", { class: "label" }, ci.error));
    }
    if (!ci.links.length) {
      return h("div", { class: "reply" }, h("span", { class: "label" }, "No CI link was found for the failed build"), h("span", { class: "detail" }, ci.summary));
    }
    return h(
      "div",
      {},
      ci.links.map((link) => [
        item(
          { class: "thread", title: link.tooltip, onclick: () => post("url", { url: link.url }) },
          icon(link.icon, link.tone),
          h("span", { class: "label" }, link.text),
          h("span", { class: "detail" }, link.detail),
        ),
        link.findings.map((finding) => findingRow(finding, "finding")),
        warningRows(link),
      ]),
    );
  }

  function findingRow(finding, cls) {
    return item(
      {
        class: cls,
        title: finding.tooltip,
        onclick: finding.path ? () => post("finding", { path: finding.path, line: finding.line }) : undefined,
      },
      icon(finding.icon, finding.tone),
      h("span", { class: "label" }, finding.text),
      h("span", { class: "detail" }, finding.notes.join(" · ")),
    );
  }

  /** Warnings sit below the errors behind one row that opens them: they are rarely why the build failed. */
  function warningRows(link) {
    if (!link.warnings.length) {
      return [];
    }
    const key = `warnings:${link.url}`;
    const isOpen = Boolean(toggled[key]);
    const count = `${link.warnings.length} warning${link.warnings.length === 1 ? "" : "s"}`;
    const head = item(
      {
        class: "finding",
        "aria-expanded": isOpen ? "true" : "false",
        onclick: () => {
          toggled[key] = !isOpen;
          draw(vm);
        },
      },
      icon(isOpen ? "chevron-down" : "chevron-right"),
      h("span", { class: "detail" }, count),
    );
    return [head, isOpen ? link.warnings.map((finding) => findingRow(finding, "finding nested")) : []];
  }

  function picker(label, choice, type) {
    const select = h(
      "select",
      { "aria-label": label, disabled: choice.options.length < 2, onchange: (event) => post(type, { value: event.target.value }) },
      choice.options.map((option) => h("option", { value: option.value, disabled: option.disabled, selected: option.value === choice.selected }, option.label)),
    );
    return h("label", { class: "picker" }, h("span", { class: "detail" }, label), select);
  }

  // --- The page ---------------------------------------------------------------------

  function draw(next) {
    vm = next;
    const scroll = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
    const plain = vm.waiting || (vm.problem ? { text: vm.problem } : vm.empty ? { text: vm.empty } : null);
    if (plain) {
      root.replaceChildren(panel(plain.text, plain.link));
      return;
    }
    const key = vm.message ? vm.message.key : undefined;
    if (key !== shownKey) {
      shownKey = key;
      delete toggled.ci;
      expanded.clear();
      revealed = null;
    }
    const parts = [];
    if (vm.head) {
      parts.push(h("div", { class: "head", title: vm.head.tooltip }, icon(vm.head.icon, vm.head.tone), h("span", { class: "label" }, vm.head.text), h("span", { class: "detail" }, vm.head.detail)));
    }
    for (const note of vm.notes || []) {
      parts.push(h("div", { class: "head note", title: `${note.text} — ${note.detail}` }, icon(note.icon, note.tone), h("span", { class: "label" }, note.text), h("span", { class: "detail" }, note.detail)));
    }
    if (vm.message) {
      parts.push(messageBlock(vm.message));
    }
    if (vm.versions && vm.modes) {
      parts.push(h("div", { class: "pickers" }, picker("Show", vm.versions, "version"), picker("Compare", vm.modes, "mode")));
    }
    if (vm.hint) {
      parts.push(
        h(
          "div",
          { class: "head note" },
          icon(vm.hint.show ? "git-compare" : "pass", vm.hint.show ? undefined : "good"),
          h("span", { class: "label" }, vm.hint.text),
          vm.hint.show ? h("button", { class: "link", onclick: () => post("mode", { value: "reviewed" }) }, "Show") : null,
        ),
      );
    }
    if (vm.files) {
      const files = vm.files;
      const summary = [h("span", { class: "detail" }, files.summary), files.rows.length ? counts(files.added, files.deleted) : null];
      parts.push(section("files", files.title, summary, true, () => h("div", { title: files.tooltip }, files.rows.map(fileRow))));
    }
    if (vm.comments) {
      const comments = vm.comments;
      parts.push(section("comments", "Comments", h("span", { class: "detail" }, comments.summary), comments.open, () => h("div", {}, comments.threads.map(threadRows))));
    }
    if (vm.ci) {
      const ci = vm.ci;
      const summary = [icon("error", "bad"), h("span", { class: "detail" }, ci.state === "closed" ? `${ci.summary} — open to fetch the findings` : ci.summary)];
      parts.push(section("ci", "CI", summary, ci.state !== "closed", () => ciBody(ci), () => post("ci")));
    }
    root.replaceChildren(...parts);
    if (document.scrollingElement) {
      document.scrollingElement.scrollTop = scroll;
    }
  }

  // Rows act on Enter and Space, and the arrow keys walk them, as in a tree.
  document.addEventListener("keydown", (event) => {
    const row = event.target.closest && event.target.closest(".item");
    if (!row || event.target.tagName === "BUTTON" || event.target.tagName === "SELECT") {
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      row.click();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const rows = [...document.querySelectorAll(".item")];
      const to = rows[rows.indexOf(row) + (event.key === "ArrowDown" ? 1 : -1)];
      if (to) {
        event.preventDefault();
        to.focus();
      }
    }
  });

  document.body.setAttribute("data-vscode-context", JSON.stringify({ preventDefaultContextMenuItems: true }));
  listen(draw, (message) => {
    if (message.type === "applied") {
      // The commit has the message now: what was typed is no longer a draft.
      delete drafts[message.key];
      saveDrafts();
      if (editor && editor.key === message.key) {
        editor.base = editor.box.value;
        editor.sync();
      }
    } else if (message.type === "reveal") {
      revealed = message.id;
      toggled.comments = true;
      draw(vm);
      const row = document.querySelector(`.thread[data-id="${CSS.escape(message.id)}"]`);
      if (row) {
        row.scrollIntoView({ block: "nearest" });
      }
    }
  });
})();
