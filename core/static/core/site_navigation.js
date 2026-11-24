(function () {
  "use strict";

  const toggle = document.querySelector(".site-navigation-toggle");
  const links = document.getElementById("site-navigation-links");
  if (!toggle || !links) return;

  const groupToggles = Array.from(links.querySelectorAll(".nav-group-toggle"));

  /* One group panel open at a time; the panels only show from 69rem up, where
     the toggles are visible.  Below that every group is open inside the menu. */
  function setGroupOpen(groupToggle, open) {
    groupToggle.setAttribute("aria-expanded", open ? "true" : "false");
    groupToggle.parentElement.classList.toggle("is-open", open);
  }

  function closeGroups(except) {
    groupToggles.forEach(function (groupToggle) {
      if (groupToggle !== except) setGroupOpen(groupToggle, false);
    });
  }

  function setOpen(open) {
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    links.classList.toggle("is-open", open);
    if (!open && links.contains(document.activeElement)) toggle.focus();
  }

  toggle.addEventListener("click", function () {
    setOpen(toggle.getAttribute("aria-expanded") !== "true");
  });

  groupToggles.forEach(function (groupToggle) {
    groupToggle.addEventListener("click", function () {
      const open = groupToggle.getAttribute("aria-expanded") !== "true";
      closeGroups(groupToggle);
      setGroupOpen(groupToggle, open);
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const openGroup = groupToggles.find(function (groupToggle) {
      return groupToggle.getAttribute("aria-expanded") === "true";
    });
    if (openGroup) {
      const hadFocus = openGroup.parentElement.contains(document.activeElement);
      setGroupOpen(openGroup, false);
      if (hadFocus) openGroup.focus();
      return;
    }
    if (toggle.getAttribute("aria-expanded") === "true") {
      setOpen(false);
      toggle.focus();
    }
  });

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;
    if (!event.target.closest(".nav-group")) closeGroups(null);
    if (!event.target.closest(".site-navigation")) setOpen(false);
  });

  /* Tabbing out of a group closes it, so a keyboard user never leaves a panel
     hanging over the page behind them. */
  links.addEventListener("focusout", function (event) {
    const group = event.target instanceof Element ? event.target.closest(".nav-group") : null;
    if (!group) return;
    if (event.relatedTarget instanceof Node && group.contains(event.relatedTarget)) return;
    const groupToggle = group.querySelector(".nav-group-toggle");
    if (groupToggle) setGroupOpen(groupToggle, false);
  });

  window.addEventListener("resize", function () {
    if (window.matchMedia("(min-width: 80rem)").matches) setOpen(false);
    closeGroups(null);
  });
})();
