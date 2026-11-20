// The gallery page around the frames: each frame (frame.js) says how tall its content is and what
// its page posted to the extension. The frame gets that height; the messages are listed under it.
// Loaded in the head: a frame may speak before the page around it is read to its end.

(function () {
  const KEPT = 40;

  function log(frame, kind, message) {
    const list = frame.parentElement.querySelector(".log");
    const { type, ...rest } = message || {};
    const what = kind === "context" ? "context menu" : String(type);
    const entry = document.createElement("li");
    const name = document.createElement("b");
    name.textContent = what;
    entry.append(name, Object.keys(rest).length ? ` ${JSON.stringify(rest)}` : "");
    entry.title = JSON.stringify(message, null, 2);
    list.append(entry);
    while (list.children.length > KEPT) {
      list.firstElementChild.remove();
    }
    list.scrollTop = list.scrollHeight;
  }

  window.addEventListener("message", (event) => {
    const said = event.data;
    const frame = [...document.querySelectorAll("iframe")].find((candidate) => candidate.contentWindow === event.source);
    if (!frame || !said || !said.gallery) {
      return;
    }
    if (said.gallery === "height") {
      // Read by `npm run gallery -- --png`, which gives each picture the height of its frame.
      frame.dataset.height = String(said.height);
      frame.style.height = `${Math.max(said.height, 44)}px`;
    } else {
      log(frame, said.gallery, said.message);
    }
  });
})();
