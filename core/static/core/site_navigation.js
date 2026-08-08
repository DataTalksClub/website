(function () {
  "use strict";

  const toggle = document.querySelector(".site-navigation-toggle");
  const links = document.getElementById("site-navigation-links");
  if (!toggle || !links) return;

  function setOpen(open) {
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    links.classList.toggle("is-open", open);
  }

  toggle.addEventListener("click", function () {
    setOpen(toggle.getAttribute("aria-expanded") !== "true");
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      setOpen(false);
      toggle.focus();
    }
  });

  document.addEventListener("click", function (event) {
    if (!event.target.closest(".site-navigation")) setOpen(false);
  });

  window.addEventListener("resize", function () {
    if (window.matchMedia("(min-width: 80rem)").matches) setOpen(false);
  });
})();
