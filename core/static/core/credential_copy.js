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

  copyButton.addEventListener("click", async function () {
    copyButton.disabled = true;
    copyButton.setAttribute("aria-busy", "true");
    status.textContent = "Copying credential";
    error.textContent = "";
    try {
      await navigator.clipboard.writeText(token.textContent || "");
      status.textContent = "Credential copied";
    } catch (_copyError) {
      status.textContent = "";
      error.textContent = "Credential could not be copied. Select and copy it manually.";
    } finally {
      copyButton.disabled = false;
      copyButton.removeAttribute("aria-busy");
    }
  });

  window.addEventListener("pagehide", function () {
    token.textContent = "Credential no longer available";
    copyButton.disabled = true;
  });
})();
