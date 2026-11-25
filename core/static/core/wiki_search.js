/* The wiki search box as a combobox (templates/public/_wiki_search_form.html).
 *
 * Without this script the box is a GET form for /wiki/search?q=.  With it,
 * the ranked topics from /wiki/search.json drop down as the reader types:
 * ↑/↓ move the highlight, Enter opens the highlighted topic (the top one when
 * nothing is highlighted), Esc closes the list and a second Esc clears the
 * field.  The ranking itself is the server's (content/wiki_hub.py), so the
 * list and the results page always agree.
 */
(function () {
  const root = document.querySelector("[data-wiki-search]");
  if (!root) return;
  const form = root.querySelector("form");
  const input = root.querySelector("input[name=q]");
  const list = root.querySelector(".wiki-search-results");
  const status = root.querySelector("[data-wiki-search-status]");
  const liveUrl = root.getAttribute("data-live-url") || "";
  if (!form || !input || !list || !liveUrl) return;

  input.setAttribute("role", "combobox");

  let results = [];
  let allUrl = "";
  let active = -1;
  let pending = null;
  let timer = 0;
  let lastQuery = null;
  let viaEnter = false;

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function close() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function highlight(index) {
    const options = list.querySelectorAll(".wiki-search-option");
    active = index;
    options.forEach((option, position) => {
      const on = position === index;
      option.classList.toggle("is-active", on);
      option.setAttribute("aria-selected", on ? "true" : "false");
      if (on) option.scrollIntoView({ block: "nearest" });
    });
    if (index >= 0 && options[index]) {
      input.setAttribute("aria-activedescendant", options[index].id);
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function render(query, payload) {
    results = payload.results || [];
    allUrl = payload.all_url || "";
    const total = payload.total || 0;
    if (!results.length) {
      list.innerHTML = `<p class="wiki-search-foot">No topic matches “${escapeHtml(query)}”.</p>`;
    } else {
      const options = results
        .map(
          (topic, index) => `
          <a class="wiki-search-option" id="wiki-search-option-${index}" role="option" aria-selected="false" href="${escapeHtml(topic.url)}">
            <strong>${escapeHtml(topic.title)}</strong>
            <span class="wiki-search-option-summary">${escapeHtml(topic.summary)}</span>
            ${topic.meta ? `<span class="wiki-search-option-meta">${escapeHtml(topic.meta)}</span>` : ""}
          </a>`,
        )
        .join("");
      const more =
        total > results.length
          ? `<a class="band-link" href="${escapeHtml(allUrl)}">All ${total} results →</a>`
          : "";
      list.innerHTML = `${options}<p class="wiki-search-foot"><span><kbd>Enter</kbd> opens ${escapeHtml(
        results[0].title,
      )}</span>${more}</p>`;
    }
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = -1;
    if (status) {
      status.textContent = total
        ? `${total} topic${total === 1 ? "" : "s"} found. Use the arrow keys to choose one.`
        : "No topics found.";
    }
  }

  function fetchResults() {
    const query = input.value.trim();
    if (query === lastQuery && !list.hidden) return;
    lastQuery = query;
    if (pending) pending.abort();
    if (!query) {
      results = [];
      close();
      return;
    }
    pending = new AbortController();
    fetch(`${liveUrl}?${new URLSearchParams({ q: query })}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal: pending.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("search unavailable");
        return response.json();
      })
      .then((payload) => {
        if (input.value.trim() === query) render(query, payload);
      })
      .catch((error) => {
        if (error.name !== "AbortError") close();
      });
  }

  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(fetchResults, 120);
  });

  input.addEventListener("focus", () => {
    if (input.value.trim() && results.length) {
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }
  });

  input.addEventListener("keydown", (event) => {
    const count = results.length;
    viaEnter = event.key === "Enter";
    if (event.key === "ArrowDown") {
      if (list.hidden) {
        lastQuery = null;
        fetchResults();
        return;
      }
      if (count) {
        event.preventDefault();
        highlight(active < count - 1 ? active + 1 : 0);
      }
    } else if (event.key === "ArrowUp") {
      if (!list.hidden && count) {
        event.preventDefault();
        highlight(active > 0 ? active - 1 : count - 1);
      }
    } else if (event.key === "Escape") {
      if (!list.hidden) {
        event.preventDefault();
        close();
      } else if (input.value) {
        event.preventDefault();
        input.value = "";
        lastQuery = null;
        results = [];
      }
    }
  });

  // Enter opens the highlighted topic, or the top one.  The Search button,
  // and Enter with no live answer yet (a fast typist, a failed fetch), submit
  // to the full results page instead.
  form.addEventListener("submit", (event) => {
    const query = input.value.trim();
    const pressedEnter = viaEnter;
    viaEnter = false;
    if (!query) {
      event.preventDefault();
      return;
    }
    if (pressedEnter && results.length && query === lastQuery) {
      event.preventDefault();
      const target = results[active >= 0 ? active : 0];
      window.location.assign(target.url);
    }
  });

  list.addEventListener("mousemove", (event) => {
    const option = event.target.closest(".wiki-search-option");
    if (!option) return;
    const index = Array.prototype.indexOf.call(list.querySelectorAll(".wiki-search-option"), option);
    if (index !== active) highlight(index);
  });

  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) close();
  });
})();
