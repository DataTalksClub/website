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

  function poll(sort, callback) {
    var path = "questions/?sort=" + encodeURIComponent(sort || config.settings.default_sort || "popular");
    var headers = etag ? { "If-None-Match": etag } : {};
    api(path, { headers: headers }).then(function (result) {
      if (result.notModified) {
        return;
      }
      etag = result.response.headers.get("ETag") || result.value.etag || etag;
      callback(result.value.items || [], result.value.counts || {});
    }).catch(function (error) {
      var status = document.getElementById("qna-banner");
      if (status) {
        status.textContent = error.message;
      }
    });
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
    setEtag: function (value) { etag = value || ""; },
  };
}());
