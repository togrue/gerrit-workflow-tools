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
  /** Comment chains shown in full: every comment's whole text, and (with no line in a file) a reply box. */
  const expanded = new Set();
  /** Reply boxes by chain, kept across redraws so typing is never interrupted. */
  const replyBoxes = new Map();
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

  /** A message that cannot be reworded here: plain wrapped text, no box, no note saying so. */
  function plainBlock(message) {
    editor = null;
    const heading = h("div", { class: "heading" }, h("span", { class: "label" }, "Commit message"), h("span", { class: "detail" }, message.author));
    const text = h("div", { class: "message-text", "aria-label": "Commit message" }, message.text);
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
    const row = item(
      {
        class: `thread${revealed === thread.id ? " revealed" : ""}`,
        title: thread.tooltip,
        "data-id": thread.id,
        // On a line of a file: open it there, in its thread. About the whole change: read and answer it here.
        onclick: thread.file ? act("open") : toggle,
      },
      twist,
      icon(thread.icon, thread.tone),
      h("span", { class: "label" }, thread.text),
      h("span", { class: "detail" }, thread.notes.join(" · ")),
      h(
        "span",
        { class: "actions" },
        thread.edit ? h("button", { class: "codicon codicon-edit", title: "Edit…", onclick: act("chain.edit") }) : null,
        thread.reply ? h("button", { class: "codicon codicon-reply", title: "Reply as Draft…", onclick: act("reply") }) : null,
        thread.open ? h("button", { class: "codicon codicon-check", title: "Reply Done and Resolve as Draft", onclick: act("replyDone") }) : null,
        thread.open && !thread.held ? h("button", { class: "codicon codicon-person", title: "Keep for Me (Not for the Agent)", onclick: act("hold") }) : null,
        thread.open && thread.held ? h("button", { class: "codicon codicon-hubot", title: "Let the Agent Handle It", onclick: act("release") }) : null,
        thread.discard ? h("button", { class: "codicon codicon-trash", title: thread.discard, onclick: act("chain.delete") }) : null,
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
      thread.reply && !thread.file ? replyBox(thread) : null,
    ];
  }

  /**
   * A box to answer a chain that has no line in a file, saved as a draft reply as an editor thread's
   * reply box saves it. As on Gerrit: a Resolved checkbox, and bottom right Discard (while your
   * draft reply is there), Cancel and Save. Discard deletes it as the trash on the row does, asking
   * first; once it is deleted, the box is emptied.
   */
  function replyBox(thread) {
    let box = replyBoxes.get(thread.id);
    if (!box) {
      const text = h("textarea", { class: "message", rows: "3", spellcheck: "true", "aria-label": "Reply", placeholder: "Reply… (Ctrl+Enter saves it as a draft)" });
      const resolved = h("input", { type: "checkbox" });
      const tick = h("label", { class: "resolved", title: "The reply resolves the comment; untick it to leave the comment open" }, resolved, "Resolved");
      const save = h("button", { class: "button", title: "Save as your draft reply on Gerrit (Ctrl+Enter)" }, "Save");
      const cancel = h("button", { class: "button secondary", title: "Drop what you typed and close the comment" }, "Cancel");
      const discard = h("button", { class: "button secondary" }, "Discard");
      const note = h("span", { class: "detail" });
      const send = () => {
        // With your draft there, Save with nothing typed still sets whether it resolves the comment.
        if (text.value.trim() || box.draft) {
          note.textContent = "saving…";
          post("thread", { id: thread.id, action: "replyText", text: text.value, resolved: resolved.checked });
        }
      };
      text.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          send();
        }
      });
      save.addEventListener("click", send);
      cancel.addEventListener("click", () => {
        text.value = "";
        note.textContent = "";
        resolved.checked = box.resolving;
        expanded.delete(thread.id);
        draw(vm);
      });
      discard.addEventListener("click", () => post("thread", { id: thread.id, action: "discard" }));
      box = { block: h("div", { class: "reply-box" }, text, h("div", { class: "message-bar" }, tick, note, discard, cancel, save)), text, note, resolved, discard };
      replyBoxes.set(thread.id, box);
    }
    // The checkbox starts where the comment stands once your drafts are published; while you type, it is yours.
    if (!box.text.value || box.resolving === undefined) {
      box.resolved.checked = thread.resolving;
    }
    box.resolving = thread.resolving;
    box.draft = thread.draft;
    box.discard.hidden = !thread.discard;
    box.discard.title = thread.discard;
    return box.block;
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
    const key = vm.key;
    if (key !== shownKey) {
      shownKey = key;
      delete toggled.ci;
      expanded.clear();
      replyBoxes.clear();
      revealed = null;
    }
    const parts = [];
    if (vm.head) {
      const head = vm.head;
      // Votes and comments as badges, drawn as the Local stack draws them; words only when there are none.
      const right = head.badges.length ? h("span", { class: "badges" }, head.badges.map((item) => badge(item))) : h("span", { class: "detail" }, head.detail);
      parts.push(h("div", { class: "head", title: [[head.text, head.detail].filter(Boolean).join(" — "), head.why].filter(Boolean).join("\n") }, icon(head.icon, head.tone), h("span", { class: "label" }, head.text), right));
      // A rebase stopped here: the way on, as Local stack's banner offers it.
      if (head.actions && head.actions.length) {
        parts.push(actions(head.actions));
      }
    }
    if (vm.message) {
      parts.push(messageBlock(vm.message));
    } else if (vm.loading && vm.loading.message) {
      parts.push(messagePlace(vm.loading.readOnly, vm.loading.plain));
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
    } else if (message.type === "replied") {
      // Saved: the draft shows in the chain now. Not saved: the text stays, to try again.
      const box = replyBoxes.get(message.id);
      if (box) {
        box.text.value = message.saved ? "" : box.text.value;
        box.note.textContent = message.saved ? "" : "not saved";
      }
    } else if (message.type === "discarded") {
      // Your draft reply is deleted: what you typed to add to it goes too, and the checkbox is back
      // where the comment now stands, as Cancel leaves it. Dismissed, both stay.
      const box = replyBoxes.get(message.id);
      if (box && message.deleted) {
        box.text.value = "";
        box.note.textContent = "";
        box.resolved.checked = box.resolving;
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
