// "Change": the view model of src/changeRows.ts. The commit message is a text box (Apply writes
// it to the commit; its Change-Id is a read-only line under it), or plain text where it cannot be
// reworded. Two pickers choose the version and what it is compared with, and the files, comment
// chains and CI findings are rows that open what they name.

(function () {
  const { h, icon, badge, actions, info, post, panel, listen, getState, setState } = window.ger;
  const root = document.getElementById("root");

  let vm = {};
  /** Commit messages being typed, by change: kept while the view is hidden or redrawn. */
  const drafts = getState().drafts || {};
  /** Sections the user opened or closed, by name: their choice wins over the default. */
  const toggled = {};
  /** Comment chains shown in full: every comment's whole text. */
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
    const changeId = h("div", { class: "change-id" });
    const block = h("div", { class: "message-block" }, h("div", { class: "heading" }, h("span", { class: "label" }, "Commit message"), author), box, changeId, bar);
    const state = { key, base: "", block, box, author, changeId };
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

  /** The Change-Id trailer, out of the box: always shown, selectable, never edited. Apply puts it back on. */
  function changeIdLine(element, changeId) {
    element.textContent = changeId ? `Change-Id: ${changeId}` : "";
    element.hidden = !changeId;
    return element;
  }

  const LIST_ITEM = /^\s*(?:[-*+•]|\d+[.)])\s+/;
  const TRAILER = /^[A-Za-z][\w-]*: \S/;
  const PART_CLASS = { subject: "subject", text: "para", item: "item", pre: "pre", trailers: "trailers" };

  /**
   * A commit message as parts to draw in a narrow view: the subject line as it is; paragraphs
   * hard-wrapped at 72 joined into one line each, for the view to wrap at its own width; list
   * items the same, each its own; indented lines and fenced blocks kept as they are (`pre`); and the
   * trailers at the end (`Signed-off-by:`, …) as they are. A `gap` stands for the blank lines.
   */
  function messageParts(text) {
    const lines = text.replace(/\s+$/, "").split("\n");
    const parts = [{ kind: "subject", text: lines[0] }];
    const paragraphs = [];
    let current = [];
    for (const line of lines.slice(1)) {
      if (line.trim() === "") {
        if (current.length) paragraphs.push(current);
        current = [];
      } else {
        current.push(line);
      }
    }
    if (current.length) paragraphs.push(current);
    const last = paragraphs[paragraphs.length - 1];
    const trailers = last && last.every((line) => TRAILER.test(line)) ? paragraphs.pop() : null;
    let fenced = false;
    for (const paragraph of paragraphs) {
      // A fence open across a blank line goes on in the same block; anything else starts anew.
      let open = fenced ? parts[parts.length - 1] : null;
      if (!fenced) parts.push({ kind: "gap" });
      else open.text += "\n";
      for (const line of paragraph) {
        if (fenced || line.trimStart().startsWith("```")) {
          const fence = line.trimStart().startsWith("```");
          if (fenced) open.text += `\n${line}`;
          else parts.push((open = { kind: "pre", text: line }));
          fenced = fence ? !fenced : fenced;
          if (!fenced) open = null;
        } else if (LIST_ITEM.test(line)) {
          parts.push((open = { kind: "item", text: line.trim(), depth: line.length - line.trimStart().length }));
        } else if (/^(\t| {2,})/.test(line) && !(open && open.kind === "item")) {
          // Indented (code, a quote, a table): as it is, scrolled sideways where it is wide.
          if (open && open.kind === "pre") open.text += `\n${line}`;
          else parts.push((open = { kind: "pre", text: line }));
        } else if (open && open.kind !== "pre") {
          open.text += ` ${line.trim()}`; // the same paragraph or item, wrapped at 72
        } else {
          parts.push((open = { kind: "text", text: line.trim() }));
        }
      }
    }
    if (trailers) parts.push({ kind: "gap" }, { kind: "trailers", text: trailers.join("\n") });
    return parts;
  }

  /** A message that cannot be reworded here: plain text reflowed to the view's width, no box, no note saying so. */
  function plainBlock(message) {
    editor = null;
    const heading = h("div", { class: "heading" }, h("span", { class: "label" }, "Commit message"), h("span", { class: "detail" }, message.author));
    const parts = messageParts(message.text).map((part) =>
      part.kind === "gap" ? h("div", { class: "message-gap" }) : h("div", { class: `message-${PART_CLASS[part.kind]}`, style: part.depth ? `margin-left: ${Math.min(part.depth, 8) / 2}em` : undefined }, part.text),
    );
    const text = h("div", { class: "message-text", "aria-label": "Commit message" }, parts);
    return h("div", { class: "message-block" }, heading, text, changeIdLine(h("div", { class: "change-id" }), message.changeId));
  }

  function messageBlock(message) {
    if (message.plain) {
      return plainBlock(message);
    }
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
    changeIdLine(editor.changeId, message.changeId);
    editor.sync();
    return editor.block;
  }

  /**
   * The message's place until it comes: the block drawn empty, as tall as a change's shortest
   * message (subject, blank line, a trailer), its bar saying what the real one will. A longer
   * message grows the box when it comes. One that will be plain text holds its place without a box.
   */
  function messagePlace(readOnly, plain) {
    if (plain) {
      const heading = h("div", { class: "heading" }, h("span", { class: "label" }, "Commit message"), h("span", { class: "detail" }, "loading…"));
      return h("div", { class: "message-block loading" }, heading, h("div", { class: "message-text" }, "\u00a0"));
    }
    const box = h("textarea", { class: "message", rows: "4", readonly: "", disabled: "", tabindex: "-1", "aria-hidden": "true" });
    const bar = h("div", { class: "message-bar" }, h("span", { class: "detail" }, readOnly || ""));
    const heading = h("div", { class: "heading" }, h("span", { class: "label" }, "Commit message"), h("span", { class: "detail" }, "loading…"));
    return h("div", { class: "message-block loading" }, heading, box, bar);
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
      h(
        "span",
        { class: "actions" },
        h("button", {
          class: "codicon codicon-comment",
          title: "Comment on This File…",
          onclick: (event) => {
            event.stopPropagation();
            post("fileComment", { index });
          },
        }),
      ),
    );
  }

  function threadRows(thread) {
    const act = (action) => (event) => {
      event.stopPropagation();
      post("thread", { id: thread.id, action });
    };
    const open = expanded.has(thread.id);
    const toggle = (event) => {
      event.stopPropagation();
      if (open) {
        expanded.delete(thread.id);
      } else {
        expanded.add(thread.id);
      }
      draw(vm);
    };
    const count = thread.replies.length ? ` and ${thread.replies.length} ${thread.replies.length === 1 ? "reply" : "replies"}` : "";
    // Every chain opens: a long comment is cut on its row, and here it can be read whole.
    const twist = h("button", {
      class: `codicon codicon-chevron-${open ? "down" : "right"} twist`,
      title: open ? "Hide the full text" : `Show the full text${count}`,
      "aria-expanded": open ? "true" : "false",
      onclick: toggle,
    });
    // An index: a chain on a line of a file is read and answered in its editor thread, where a click
    // takes you. Only one about the whole change, which no file shows, is answered from here.
    const answers = !thread.file;
    const row = item(
      {
        class: `thread${revealed === thread.id ? " revealed" : ""}`,
        title: thread.file ? `${thread.tooltip}\n\nClick to open it in its file, where it is answered.` : thread.tooltip,
        "data-id": thread.id,
        onclick: thread.file ? act("open") : toggle,
      },
      twist,
      icon(thread.icon, thread.tone),
      h("span", { class: "label" }, thread.text),
      h("span", { class: "detail" }, thread.notes.join(" · ")),
      h(
        "span",
        { class: "actions" },
        answers && thread.edit ? h("button", { class: "codicon codicon-edit", title: "Edit…", onclick: act("chain.edit") }) : null,
        answers && thread.reply ? h("button", { class: "codicon codicon-reply", title: "Reply as Draft…", onclick: act("reply") }) : null,
        answers && thread.open ? h("button", { class: "codicon codicon-check", title: "Reply Done and Resolve as Draft", onclick: act("replyDone") }) : null,
        thread.open && !thread.held ? h("button", { class: "codicon codicon-person", title: "Keep for Me (Not for the Agent)", onclick: act("hold") }) : null,
        thread.open && thread.held ? h("button", { class: "codicon codicon-hubot", title: "Let the Agent Handle It", onclick: act("release") }) : null,
        answers && thread.discard ? h("button", { class: "codicon codicon-trash", title: thread.discard, onclick: act("chain.delete") }) : null,
      ),
    );
    if (!open) {
      return [row];
    }
    const comment = (author, state, text) =>
      h(
        "div",
        { class: "comment" },
        h("div", { class: "reply" }, icon(state ? "edit" : "account"), h("span", { class: "label" }, author), state ? h("span", { class: "detail" }, `[${state}]`) : null),
        h("div", { class: "comment-text" }, text),
      );
    return [
      row,
      comment(thread.author, "", thread.body),
      thread.replies.map((reply) => comment(reply.author, reply.state, reply.text)),
    ];
  }

  function ciBody(ci) {
    if (ci.state === "closed" || ci.state === "loading") {
      return h("div", { class: "reply" }, h("span", { class: "codicon codicon-loading codicon-modifier-spin" }), h("span", { class: "detail" }, "Fetching the findings…"));
    }
    if (ci.state === "error") {
      return h("div", { class: "reply" }, icon("error", "bad"), h("span", { class: "label" }, ci.error));
    }
    if (!ci.links.length) {
      return h("div", { class: "reply" }, ci.noLinks ? h("span", { class: "label" }, ci.noLinks) : null, h("span", { class: "detail" }, ci.summary));
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

  /** One side's picker: a label and its select, as two cells of the compare grid. */
  function picker(label, choice, type) {
    const id = `pick-${type}`;
    const select = h(
      "select",
      { id, disabled: choice.options.length < 2, onchange: (event) => post(type, { value: event.target.value }) },
      choice.options.map((option) => h("option", { value: option.value, disabled: option.disabled, selected: option.value === choice.selected }, option.label)),
    );
    return [h("label", { class: "side", for: id }, label), select];
  }

  /**
   * From and To, one above the other so each select has the width of the view, and beside them the
   * toggle of the default mode: lit while From and To are the default's, dim once picked by hand.
   */
  function compareBlock(compare) {
    const mode = compare.mode;
    const toggle = h(
      "button",
      {
        class: `mode-toggle${mode.active ? " active" : ""}`,
        title: mode.title,
        "aria-label": mode.title,
        "aria-pressed": mode.active ? "true" : "false",
        disabled: mode.next === mode.mode && mode.active,
        onclick: () => post("mode", { value: mode.next }),
      },
      icon(mode.icon),
    );
    return h("div", { class: "compare" }, ...picker("From", compare.from, "from"), toggle, ...picker("To", compare.to, "to"));
  }

  /** An explanation: an icon and words that wrap under each other in a narrow view, never cut off. */
  function explain(iconId, tone, text, detail, ...rest) {
    return h(
      "div",
      { class: "head note explain" },
      icon(iconId, tone),
      h("span", { class: "words" }, h("span", { class: "label" }, text), detail ? [" ", h("span", { class: "detail" }, detail)] : null),
      ...rest,
    );
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
    const key = vm.key;
    if (key !== shownKey) {
      shownKey = key;
      delete toggled.ci;
      expanded.clear();
      revealed = null;
    }
    const parts = [];
    if (vm.head) {
      const head = vm.head;
      // Votes and comments as badges, drawn as the Local stack draws them; words only when there are none.
      const right = head.badges.length ? h("span", { class: "badges" }, head.badges.map((item) => badge(item))) : h("span", { class: "detail" }, head.detail);
      parts.push(h("div", { class: "head", title: `${head.text} — ${head.detail}` }, icon(head.icon, head.tone), h("span", { class: "label" }, head.text), right));
      // A rebase stopped here: the way on, as Local stack's banner offers it.
      if (head.actions && head.actions.length) {
        parts.push(actions(head.actions));
      }
    }
    // Next: the one thing to do now, named on the button; Alt+Enter does the same.
    if (vm.next) {
      parts.push(h("div", { class: "next-bar" }, actions([{ label: vm.next.label, title: vm.next.title, command: { id: "ger.next", args: [] }, primary: true }])));
    }
    for (const note of vm.notes || []) {
      parts.push(explain(note.icon, note.tone, note.text, note.detail));
      if (note.actions && note.actions.length) {
        parts.push(actions(note.actions));
      }
    }
    if (vm.message) {
      parts.push(messageBlock(vm.message));
    } else if (vm.loading && vm.loading.message) {
      parts.push(messagePlace(vm.loading.readOnly, vm.loading.plain));
    }
    if (vm.compare) {
      parts.push(compareBlock(vm.compare));
    }
    if (vm.hint) {
      const to = vm.compare ? vm.compare.to.selected : "current";
      const show = vm.hint.show ? h("button", { class: "link", onclick: () => post("compare", { from: "reviewed", to }) }, "Show") : null;
      parts.push(explain(vm.hint.show ? "git-compare" : "pass", vm.hint.show ? undefined : "good", vm.hint.text, "", show));
    }
    if (vm.conflicts) {
      // The files still conflicted, as under Local stack's banner: a click opens one to resolve.
      const conflicts = vm.conflicts;
      parts.push(section("conflicts", "Conflicts", h("span", { class: "detail" }, conflicts.summary), true, () => h("div", {}, conflicts.rows.map(info))));
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
      const summary = [icon("error", "bad"), h("span", { class: "detail" }, ci.state === "closed" ? [ci.summary, "open to fetch the findings"].filter(Boolean).join(" — ") : ci.summary)];
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
      if (message.open) {
        expanded.add(message.id); // about the whole change: its text whole, its buttons on the row
      }
      draw(vm);
      const row = document.querySelector(`.thread[data-id="${CSS.escape(message.id)}"]`);
      if (row) {
        row.scrollIntoView({ block: "nearest" });
        if (message.open) {
          row.focus();
        }
      }
    }
  });
})();
