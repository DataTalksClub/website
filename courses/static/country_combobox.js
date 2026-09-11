(function () {
  const input = document.querySelector("[data-country-combobox-input]");
  const panel = document.querySelector("[data-country-combobox-panel]");
  const optionsScript = document.getElementById("country-options-json");

  if (!input || !panel || !optionsScript) {
    return;
  }

  const countries = JSON.parse(optionsScript.textContent);
  let activeIndex = -1;
  let visibleCountries = [];

  // UX-03: the no-results state is announced politely through a region of
  // its own, outside the listbox -- a status inside role="listbox" would
  // corrupt the listbox semantics.
  const announcements = document.createElement("p");
  announcements.className = "sr-only";
  announcements.setAttribute("aria-live", "polite");
  panel.insertAdjacentElement("afterend", announcements);

  function normalize(value) {
    return value.trim().toLowerCase();
  }

  function matchingCountries(query) {
    const normalizedQuery = normalize(query);
    if (!normalizedQuery) {
      return countries.slice();
    }

    const startsWith = [];
    const contains = [];

    countries.forEach((country) => {
      const normalizedCountry = normalize(country);
      if (normalizedCountry.startsWith(normalizedQuery)) {
        startsWith.push(country);
      } else if (normalizedCountry.includes(normalizedQuery)) {
        contains.push(country);
      }
    });

    return startsWith.concat(contains).slice(0, 12);
  }

  function selectCountry(country) {
    input.value = country;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    closePanel();
  }

  // The single close transition: panel visibility, aria-expanded, the
  // active option, and aria-activedescendant always move together, so the
  // reported popup state can never disagree with the rendered one.
  function closePanel() {
    panel.hidden = true;
    panel.innerHTML = "";
    visibleCountries = [];
    activeIndex = -1;
    input.setAttribute("aria-expanded", "false");
    // Never leave a reference to a removed option element.
    input.removeAttribute("aria-activedescendant");
    announcements.textContent = "";
  }

  function renderOptions() {
    visibleCountries = matchingCountries(input.value);
    activeIndex = visibleCountries.length ? 0 : -1;
    panel.innerHTML = "";

    if (!visibleCountries.length) {
      const empty = document.createElement("div");
      empty.className = "country-combobox-empty";
      empty.textContent = "No matching countries";
      panel.appendChild(empty);
      panel.hidden = false;
      // The options were just removed; never leave a reference to one.
      input.removeAttribute("aria-activedescendant");
      return;
    }

    visibleCountries.forEach((country, index) => {
      const option = document.createElement("button");
      option.type = "button";
      option.id = "country-option-" + index;
      option.className = "country-combobox-option";
      option.textContent = country;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", index === activeIndex ? "true" : "false");
      // Options are not Tab stops: the input keeps focus and Arrow keys
      // move the active descendant.  Activation is Enter from the input or
      // a pointer press/click here -- mousedown alone missed touch and
      // assistive-tech click synthesis.
      option.tabIndex = -1;
      option.addEventListener("mousedown", (event) => {
        event.preventDefault();
        selectCountry(country);
      });
      option.addEventListener("click", (event) => {
        event.preventDefault();
        selectCountry(country);
      });
      panel.appendChild(option);
    });

    panel.hidden = false;
    input.setAttribute("aria-activedescendant", "country-option-" + activeIndex);
  }

  function announceMatches() {
    announcements.textContent = visibleCountries.length
      ? ""
      : "No matching countries";
  }

  // The single open transition, paired with closePanel above.
  function openPanel() {
    renderOptions();
    announceMatches();
    input.setAttribute("aria-expanded", "true");
  }

  function updateActiveOption(nextIndex) {
    if (!visibleCountries.length) {
      return;
    }

    activeIndex = (nextIndex + visibleCountries.length) % visibleCountries.length;
    panel.querySelectorAll(".country-combobox-option").forEach((option, index) => {
      const isActive = index === activeIndex;
      option.setAttribute("aria-selected", isActive ? "true" : "false");
      if (isActive) {
        option.scrollIntoView({ block: "nearest" });
      }
    });
    input.setAttribute("aria-activedescendant", "country-option-" + activeIndex);
  }

  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", "country-combobox-listbox");
  panel.id = "country-combobox-listbox";
  panel.setAttribute("role", "listbox");

  input.addEventListener("focus", () => {
    openPanel();
  });

  input.addEventListener("input", () => {
    openPanel();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (panel.hidden) {
        openPanel();
      } else {
        updateActiveOption(activeIndex + 1);
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (panel.hidden) {
        // Defined closed behavior: open with the last option active.
        openPanel();
        if (visibleCountries.length) {
          updateActiveOption(visibleCountries.length - 1);
        }
      } else {
        updateActiveOption(activeIndex - 1);
      }
    } else if (event.key === "Enter" && !panel.hidden && activeIndex >= 0) {
      event.preventDefault();
      selectCountry(visibleCountries[activeIndex]);
    } else if (event.key === "Escape") {
      // Close without discarding the typed value.
      closePanel();
    } else if (event.key === "Tab") {
      // Close and let the browser move focus to the next form control.
      closePanel();
    }
  });

  input.addEventListener("blur", () => {
    window.setTimeout(() => {
      // Focus may have already come back (a fast refocus between the blur
      // and this timeout must not close an open popup).
      if (document.activeElement !== input) {
        closePanel();
      }
    }, 120);
  });
})();
