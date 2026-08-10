(function () {
  "use strict";

  const summary = document.querySelector("[data-focus-error-summary]");
  if (summary instanceof HTMLElement) {
    summary.focus();
  }

  document.querySelectorAll("[data-busy-label]").forEach(function (control) {
    const form = control.closest("form");
    if (!form) return;

    form.addEventListener("submit", function () {
      control.setAttribute("aria-disabled", "true");
      control.setAttribute("aria-busy", "true");
      const busyLabel = control.getAttribute("data-busy-label");
      if (busyLabel) control.textContent = busyLabel;
    });
  });
})();
