// Stands in for VS Code inside one frame of the gallery, so the real pages of media/ run in a
// plain browser: `acquireVsCodeApi()` answers the page's `ready` with the scenario's view model,
// and whatever the page posts goes to the gallery page around the frame, which lists it. Loaded
// before the page's own scripts; the scenario is the JSON in the `#gallery` element.

(function () {
  const scenario = JSON.parse(document.getElementById("gallery").textContent);
  const around = window.parent !== window ? window.parent : null;
  /** Say something to the gallery page; opened on its own, the frame says it in the console. */
  const tell = (kind, fields) => (around ? around.postMessage({ gallery: kind, ...fields }, "*") : console.log(`[gallery] ${kind}`, fields));

  let state;
  let acquired = false;
  let pressed = false;

  /** What the scenario wants opened: each element a selector matches is clicked, found anew every time as a click redraws the page. */
  function press(selectors) {
    for (const selector of selectors) {
      const count = document.querySelectorAll(selector).length;
      for (let index = 0; index < count; index++) {
        const element = document.querySelectorAll(selector)[index];
        if (element) {
          element.click();
        }
      }
    }
  }

  // The gallery page gives the frame the height of what it draws, so nothing is cut or scrolls.
  // And how far it is wider than the frame: a side bar never scrolls sideways (`npm run gallery -- --png` lists those that do).
  const sayHeight = () => {
    const page = document.documentElement;
    const wide = Math.max(0, page.scrollWidth - page.clientWidth);
    // What sticks out: the first element whose right edge is past the view's, as tag.class.
    const out = wide ? [...document.body.querySelectorAll("*")].find((element) => element.getBoundingClientRect().right > page.clientWidth + 0.5) : null;
    const what = out ? `${out.tagName.toLowerCase()}.${[...out.classList].join(".")}` : "";
    tell("height", { height: Math.ceil(document.body.getBoundingClientRect().height), wide, what });
  };

  /** As the extension does: the view model arrives as a message event on the window. */
  function render() {
    window.dispatchEvent(new MessageEvent("message", { data: { type: "render", vm: scenario.vm } }));
    if (!pressed) {
      pressed = true;
      press(scenario.click || []);
    }
    sayHeight();
  }

  window.acquireVsCodeApi = function () {
    if (acquired) {
      throw new Error("An instance of the VS Code API has already been acquired");
    }
    acquired = true;
    return {
      postMessage(message) {
        tell("posted", { message });
        if (message && message.type === "ready") {
          setTimeout(render, 0);
        }
      },
      getState: () => state,
      setState(next) {
        state = next;
        return next;
      },
    };
  };

  // VS Code builds a context menu from the `data-vscode-context` of the element under the pointer
  // and its ancestors, the nearest winning. A browser shows its own menu: the frame says what
  // VS Code would have been handed.
  document.addEventListener("contextmenu", (event) => {
    let context = {};
    for (let element = event.target; element && element.getAttribute; element = element.parentElement) {
      const own = element.getAttribute("data-vscode-context");
      if (own) {
        context = { ...JSON.parse(own), ...context };
      }
    }
    tell("context", { message: context });
  });

  // Said after each render too: a browser does not lay out a frame that is off screen, so the observer alone would wait for it to be scrolled to.
  new ResizeObserver(sayHeight).observe(document.body);
})();
