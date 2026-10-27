(function () {
  "use strict";

  const region = document.querySelector("[data-credential-copy]");
  if (!region) return;

  const token = document.getElementById("one-time-token");
  const copyButton = document.getElementById("copy-token");
  const status = document.getElementById("copy-status");
  const error = document.getElementById("copy-error");
  if (!token || !copyButton || !status || !error) return;

  const returnUrl = region.getAttribute("data-return-url");
  if (returnUrl) history.replaceState(null, "", returnUrl);

  function legacyCopy(text) {
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "0";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.focus();
      area.select();
      const succeeded = document.execCommand("copy");
      area.remove();
      return succeeded;
    } catch (_legacyError) {
      return false;
    }
  }

  function clipboardCopy(text) {
    if (
      typeof navigator === "undefined" ||
      !navigator.clipboard ||
      typeof navigator.clipboard.writeText !== "function"
    ) {
      return Promise.reject(new Error("clipboard-unavailable"));
    }
    try {
      const attempt = navigator.clipboard.writeText(text);
      const timeout = new Promise(function (_, reject) {
        window.setTimeout(function () {
          reject(new Error("clipboard-timeout"));
        }, 2000);
      });
      return Promise.race([attempt, timeout]);
    } catch (_clipboardError) {
      return Promise.reject(_clipboardError);
    }
  }

  copyButton.addEventListener("click", async function () {
    copyButton.disabled = true;
    copyButton.setAttribute("aria-busy", "true");
    status.textContent = "Copying credential";
    error.textContent = "";
    const text = token.textContent || "";
    let copied = false;
    try {
      await clipboardCopy(text);
      copied = true;
    } catch (_primaryError) {
      copied = legacyCopy(text);
    }
    if (copied) {
      status.textContent = "Credential copied";
    } else {
      status.textContent = "";
      error.textContent = "Credential could not be copied. Select and copy it manually.";
    }
    copyButton.disabled = false;
    copyButton.removeAttribute("aria-busy");
    if (document.activeElement !== copyButton) {
      try {
        copyButton.focus({ preventScroll: true });
      } catch (_focusError) {
        /* The status announcement above is the contract; focus is best-effort. */
      }
    }
  });

  window.addEventListener("pagehide", function () {
    token.textContent = "Credential no longer available";
    copyButton.disabled = true;
  });
})();
