(function () {
  "use strict";

  var configNode = document.getElementById("qna-config");
  var config = configNode ? JSON.parse(configNode.textContent || "{}") : {};
  var etag = "";

  function apiUrl(path) {
    var base = (config.api_base || "").replace(/\/+$/, "");
    return base + "/" + path.replace(/^\/+/, "");
  }

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function api(path, options) {
    var request = options || {};
    request.headers = Object.assign({
      Accept: "application/json",
      "X-CSRFToken": csrfToken(),
    }, request.headers || {});
    if (request.body && typeof request.body !== "string") {
      request.headers["Content-Type"] = "application/json";
      request.body = JSON.stringify(request.body);
    }
    return fetch(apiUrl(path), request).then(function (response) {
      if (response.status === 304) {
        return { notModified: true, response: response };
      }
      return response.text().then(function (body) {
        var value = body ? JSON.parse(body) : {};
        if (!response.ok) {
          var error = new Error(value.error && value.error.message || "Q&A request failed");
          error.payload = value;
          error.response = response;
          throw error;
        }
        return { value: value, response: response };
      });
    });
  }

  // UX-07: connection trouble reports on its own status line, never the
  // lifecycle banner — a failed poll must not make a room look opened or
  // closed, and recovery says so once.
  var connectionBroken = false;

  function setConnection(message) {
    var node = document.getElementById("qna-connection");
    if (!node) return;
    node.hidden = !message;
    node.textContent = message || "";
  }

  function poll(sort, callback) {
    var settings = config.settings || {};
    var path = "questions/?sort=" + encodeURIComponent(sort || settings.default_sort || "popular");
    var headers = etag ? { "If-None-Match": etag } : {};
    api(path, { headers: headers }).then(function (result) {
      // Any server answer — 200 or 304 — proves the transport healthy.
      if (connectionBroken) {
        connectionBroken = false;
        setConnection("Connection restored.");
      }
      // A 304 vouches for the session state as well as the question rows:
      // the ETag covers the session revision, so an unchanged list cannot
      // hide a lifecycle change.
      if (result.notModified) {
        return;
      }
      etag = result.response.headers.get("ETag") || result.value.etag || etag;
      callback(result.value);
    }).catch(function (error) {
      connectionBroken = true;
      setConnection(
        error && error.name === "TypeError"
          ? "The connection to the Q&A was lost. Retrying…"
          : (error && error.message) || "The connection to the Q&A was lost. Retrying…"
      );
    });
  }

  var liveRegion = null;

  // Polite, screen-reader-only announcements for list changes a sighted
  // user sees directly (a question leaving the list).
  function announce(message) {
    if (!liveRegion) {
      liveRegion = document.createElement("p");
      liveRegion.id = "qna-live";
      liveRegion.className = "qna-sr-only";
      liveRegion.setAttribute("role", "status");
      document.body.appendChild(liveRegion);
    }
    liveRegion.textContent = "";
    window.setTimeout(function () { liveRegion.textContent = message; }, 50);
  }

  // Keyed list renderer shared by the participant room and the host
  // moderation queue. Rows keep a stable identity (spec.key), so a poll or
  // an optimistic update touches existing nodes in place instead of
  // clearing the list, which used to drop the focused action on every
  // refresh (UX-06). spec.build(li, item) creates one row's static
  // structure once and returns an update(item) closure for in-place
  // refreshes; spec.removalMessage(item) returns the polite announcement
  // for a row leaving the list, or "" to stay silent.
  function createList(list, spec) {
    if (!list) return function () {};
    var rows = {};
    var section = list.closest("[aria-labelledby]");
    var heading = section
      ? document.getElementById(section.getAttribute("aria-labelledby"))
      : null;
    if (heading) heading.setAttribute("tabindex", "-1");

    function captureFocus() {
      var active = document.activeElement;
      if (!active || active === document.body || !list.contains(active)) return null;
      var row = active.closest("[data-qna-key]");
      if (!row || !list.contains(row)) return null;
      return { key: row.getAttribute("data-qna-key"), action: active.getAttribute("data-qna-action") || "" };
    }

    function controlFor(key, action) {
      var row = rows[key];
      if (!row) return null;
      if (action) {
        var control = row.li.querySelector('[data-qna-action="' + action + '"]');
        if (control) return control;
      }
      return row.li.querySelector("button");
    }

    return function render(items) {
      var focus = captureFocus();
      var desired = [];
      var keyed = {};
      items.forEach(function (item) {
        var key = String(spec.key(item));
        desired.push(key);
        keyed[key] = item;
      });

      // Rows whose question vanished (moderation, resolved pending card).
      // Capture the focused row's position before any removal so the
      // fallback can land on the question that takes its place.
      var removals = [];
      Object.keys(rows).forEach(function (key) {
        if (keyed[key] !== undefined) return;
        removals.push({
          key: key,
          index: Array.prototype.indexOf.call(list.children, rows[key].li),
        });
      });
      removals.sort(function (a, b) { return a.index - b.index; });
      var fallbackIndex = null;
      removals.forEach(function (removal, position) {
        if (focus && focus.key === removal.key) {
          fallbackIndex = Math.max(0, removal.index - position);
        }
        var message = spec.removalMessage ? spec.removalMessage(rows[removal.key].item) : "";
        rows[removal.key].li.remove();
        delete rows[removal.key];
        if (message) announce(message);
      });

      desired.forEach(function (key, index) {
        var item = keyed[key];
        var row = rows[key];
        if (!row) {
          var li = document.createElement("li");
          li.className = "qna-item";
          li.setAttribute("data-qna-key", key);
          row = { li: li, item: item, update: spec.build(li, item) };
          rows[key] = row;
        }
        row.update(item);
        var current = list.children[index];
        if (current !== row.li) list.insertBefore(row.li, current || null);
      });

      if (!focus) return;
      var restored = controlFor(focus.key, focus.action);
      if (restored && document.contains(restored)) {
        if (document.activeElement !== restored) restored.focus();
        return;
      }
      // Documented fallback: the action of the question that now occupies
      // the removed row's place, else the list heading — never page start.
      var target = null;
      if (fallbackIndex !== null && list.children.length > 0) {
        var candidate = list.children[Math.min(fallbackIndex, list.children.length - 1)];
        if (candidate) target = candidate.querySelector("button");
      }
      if (target) target.focus();
      else if (heading) heading.focus();
    };
  }

  function startPolling(sort, callback, visibleMs) {
    var timer;
    function currentSort() {
      return typeof sort === "function" ? sort() : sort;
    }
    function delay() {
      return document.hidden ? 30000 : visibleMs;
    }
    function run() {
      poll(currentSort(), callback);
      window.clearTimeout(timer);
      timer = window.setTimeout(run, delay());
    }
    document.addEventListener("visibilitychange", function () {
      window.clearTimeout(timer);
      run();
    });
    run();
  }

  window.EventQna = {
    config: config,
    api: api,
    poll: poll,
    startPolling: startPolling,
    createList: createList,
    announce: announce,
    setEtag: function (value) { etag = value || ""; },
  };
}());
