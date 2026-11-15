// Newsletter signup: posts the email to the same-origin /newsletter endpoint
// (which forwards to Relay) and always ends in a visible success or error
// message. Every request has a timeout, so the button never spins forever.
(function (root) {
  var TIMEOUT_MS = 10000;
  var MESSAGES = {
    invalid_email: "Please enter a valid email address.",
    failed:
      "Something went wrong and we couldn't subscribe you. Please try again, or email alexey@datatalks.club.",
    timeout:
      "The request took too long. Please try again, or email alexey@datatalks.club.",
  };

  function show(status, result) {
    if (!status) {
      return;
    }
    status.textContent = result.message;
    status.className =
      "newsletter-status " + (result.ok ? "is-success" : "is-error");
    status.hidden = false;
  }

  function submitForm(endpoint, email, company, page, deps) {
    var controller = new deps.AbortController();
    var timer = deps.setTimeout(function () {
      controller.abort();
    }, TIMEOUT_MS);
    return deps
      .fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({
          email: email,
          company: company,
          page: page,
        }),
        signal: controller.signal,
      })
      .then(function (response) {
        return response.json().then(
          function (body) {
            if (body && typeof body.message === "string") {
              return { ok: Boolean(body.ok), message: body.message };
            }
            return { ok: false, message: MESSAGES.failed };
          },
          function () {
            return { ok: false, message: MESSAGES.failed };
          }
        );
      })
      .catch(function (error) {
        var aborted = error && error.name === "AbortError";
        return {
          ok: false,
          message: aborted ? MESSAGES.timeout : MESSAGES.failed,
        };
      })
      .then(function (result) {
        deps.clearTimeout(timer);
        return result;
      });
  }

  function attach(form, deps) {
    var container = form.parentNode;
    var status = container ? container.querySelector(".newsletter-status") : null;
    var button = form.querySelector("button[type=submit]");
    var input = form.querySelector("input[type=email]");
    var honeypot = form.querySelector("input[name=company]");
    var endpoint = form.getAttribute("action") || "/newsletter";

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!input || !button) {
        return;
      }
      var email = (input.value || "").trim();
      if (!email) {
        show(status, { ok: false, message: MESSAGES.invalid_email });
        input.focus();
        return;
      }
      button.disabled = true;
      if (status) {
        status.className = "newsletter-status";
        status.textContent = "Subscribing…";
        status.hidden = false;
      }
      var company = honeypot ? honeypot.value || "" : "";
      submitForm(endpoint, email, company, deps.page(), deps).then(function (
        result
      ) {
        show(status, result);
        if (result.ok) {
          input.value = "";
        }
        button.disabled = false;
      });
    });
  }

  if (root && root.document && !root.__dtcNewsletterSignup) {
    root.__dtcNewsletterSignup = true;
    var deps = {
      fetch: root.fetch.bind(root),
      AbortController: root.AbortController,
      setTimeout: root.setTimeout.bind(root),
      clearTimeout: root.clearTimeout.bind(root),
      page: function () {
        return root.location.href;
      },
    };
    var start = function () {
      Array.prototype.forEach.call(
        root.document.querySelectorAll("form[data-relay-signup]"),
        function (form) {
          attach(form, deps);
        }
      );
    };
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", start);
    } else {
      start();
    }
  }
})(typeof window !== "undefined" ? window : null);
