(function () {
  "use strict";

  var COOKIE_NAME = "dtc_analytics_consent";
  var COOKIE_VERSION = "v1";
  var COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 180;
  var ALLOWED_VALUE = COOKIE_VERSION + ".allow";
  var DENIED_VALUE = COOKIE_VERSION + ".deny";
  var OPTIONAL_COOKIE_PREFIXES = [
    "dtc_analytics_",
    "dtc_attribution_",
    "_ga",
    "_gid",
    "_gat",
    "_gcl_",
  ];

  function readChoice() {
    var prefix = COOKIE_NAME + "=";
    var cookies = document.cookie ? document.cookie.split(";") : [];
    for (var index = 0; index < cookies.length; index += 1) {
      var cookie = cookies[index].trim();
      if (cookie.indexOf(prefix) !== 0) {
        continue;
      }
      var value = "";
      try {
        value = decodeURIComponent(cookie.slice(prefix.length));
      } catch (_error) {
        continue;
      }
      if (value === ALLOWED_VALUE || value === DENIED_VALUE) {
        return value;
      }
    }
    return "";
  }

  function secureAttribute() {
    return window.location.protocol === "https:" ? "; Secure" : "";
  }

  function writeChoice(value) {
    document.cookie =
      COOKIE_NAME +
      "=" +
      encodeURIComponent(value) +
      "; Path=/; Max-Age=" +
      COOKIE_MAX_AGE_SECONDS +
      "; SameSite=Lax" +
      secureAttribute();
  }

  function isOptionalCookie(name) {
    if (name === COOKIE_NAME) {
      return false;
    }
    return OPTIONAL_COOKIE_PREFIXES.some(function (prefix) {
      return name === prefix || name.indexOf(prefix) === 0;
    });
  }

  function expireOptionalCookies() {
    var cookies = document.cookie ? document.cookie.split(";") : [];
    cookies.forEach(function (cookie) {
      var name = cookie.split("=", 1)[0].trim();
      if (!isOptionalCookie(name)) {
        return;
      }
      var expiration = "=; Path=/; Max-Age=0; SameSite=Lax" + secureAttribute();
      document.cookie = name + expiration;
      var hostname = window.location.hostname.toLowerCase();
      if (hostname === "datatalks.club" || hostname.endsWith(".datatalks.club")) {
        document.cookie = name + expiration + "; Domain=datatalks.club";
      }
    });
  }

  function init() {
    var dialog = document.getElementById("analytics-preferences-dialog");
    if (!(dialog instanceof HTMLDialogElement)) {
      return;
    }

    var status = document.getElementById("analytics-preferences-status");
    var openers = Array.prototype.slice.call(
      document.querySelectorAll("[data-analytics-preferences-open]")
    );
    var closeButton = dialog.querySelector("[data-analytics-preferences-close]");
    var choiceButtons = Array.prototype.slice.call(
      dialog.querySelectorAll("[data-analytics-consent]")
    );
    var lastOpener = null;
    var restoreFocus = false;

    function updateStatus() {
      if (!status) {
        return;
      }
      var choice = readChoice();
      status.textContent =
        choice === ALLOWED_VALUE
          ? "Current choice: analytics allowed."
          : choice === DENIED_VALUE
            ? "Current choice: analytics off."
            : "Current choice: not chosen.";
    }

    function closePreferences() {
      if (dialog.open) {
        dialog.close();
      }
    }

    function openPreferences(opener) {
      updateStatus();
      if (dialog.open) {
        restoreFocus = false;
        dialog.close();
      }
      lastOpener = opener;
      restoreFocus = true;
      dialog.showModal();
      var currentChoice = dialog.querySelector(
        readChoice() === ALLOWED_VALUE
          ? '[data-analytics-consent="allow"]'
          : '[data-analytics-consent="deny"]'
      );
      if (currentChoice instanceof HTMLElement) {
        currentChoice.focus();
      }
    }

    openers.forEach(function (opener) {
      opener.addEventListener("click", function () {
        openPreferences(opener);
      });
    });

    choiceButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        var choice = button.getAttribute("data-analytics-consent");
        if (choice === "allow") {
          writeChoice(ALLOWED_VALUE);
        } else {
          writeChoice(DENIED_VALUE);
          expireOptionalCookies();
        }
        updateStatus();
        closePreferences();
      });
    });

    if (closeButton) {
      closeButton.addEventListener("click", closePreferences);
    }

    dialog.addEventListener("close", function () {
      if (restoreFocus && lastOpener instanceof HTMLElement) {
        lastOpener.focus();
      }
      restoreFocus = false;
    });

    dialog.addEventListener("keydown", function (event) {
      if (event.key !== "Tab" || !dialog.matches(":modal")) {
        return;
      }
      var focusable = Array.prototype.filter.call(
        dialog.querySelectorAll(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ),
        function (element) {
          return element.getClientRects().length > 0;
        }
      );
      if (!focusable.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    });

    updateStatus();
    if (!readChoice()) {
      dialog.setAttribute("open", "");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
