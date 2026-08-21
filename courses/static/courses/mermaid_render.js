(function () {
  "use strict";

  /*
   * Mermaid is deliberately a module so the browser can fetch its large,
   * self-hosted runtime only after it finds a diagram.  The page remains fully
   * readable as escaped source if JavaScript is disabled or rendering fails.
   */
  var status = {
    state: "idle",
    error: "",
  };
  window.__DTC_MERMAID_STATUS__ = status;

  function updateStatus(state, error) {
    status.state = state;
    status.error = error || "";
  }

  function errorMessage(error) {
    if (!error) return "";
    return error.stack || error.message || String(error);
  }

  function diagramNodes() {
    return Array.prototype.slice.call(document.querySelectorAll("div.mermaid"));
  }

  function readToken(name) {
    var value = getComputedStyle(document.body).getPropertyValue(name).trim();
    return value || getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function themeVariables() {
    var page = readToken("--page") || readToken("--lavender");
    var card = readToken("--card");
    var ink = readToken("--ink");
    var muted = readToken("--lavender-deep") || readToken("--lavender");
    var line = readToken("--indigo") || readToken("--line");
    var bodyText = readToken("--body-text") || readToken("--muted");

    return {
      background: page,
      primaryColor: card,
      primaryTextColor: ink,
      primaryBorderColor: line,
      secondaryColor: muted,
      secondaryTextColor: ink,
      secondaryBorderColor: line,
      tertiaryColor: page,
      tertiaryTextColor: bodyText,
      tertiaryBorderColor: line,
      lineColor: line,
      textColor: ink,
      mainBkg: card,
      nodeBorder: line,
      clusterBkg: muted,
      clusterBorder: line,
      edgeLabelBackground: page,
      titleColor: ink,
      actorBkg: card,
      actorBorder: line,
      actorTextColor: ink,
      actorLineColor: line,
      signalColor: ink,
      signalTextColor: ink,
      labelBoxBkgColor: card,
      labelBoxBorderColor: line,
      labelTextColor: ink,
    };
  }

  function captureSources(nodes) {
    nodes.forEach(function (node) {
      if (!node.dataset.mermaidSource) {
        node.dataset.mermaidSource = node.textContent || "";
      }
    });
  }

  function restoreSources(nodes) {
    nodes.forEach(function (node) {
      node.textContent = node.dataset.mermaidSource || "";
      node.removeAttribute("data-processed");
    });
  }

  function lockSvgWidths(nodes) {
    nodes.forEach(function (node) {
      var svg = node.querySelector("svg");
      if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return;

      var width = svg.viewBox.baseVal.width;
      if (width > 0) {
        svg.style.width = width + "px";
        svg.style.maxWidth = "none";
      }
    });
  }

  function render(mermaid, nodes) {
    if (!nodes.length) return Promise.resolve();

    nodes.forEach(function (node) {
      node.textContent = node.dataset.mermaidSource || "";
      node.removeAttribute("data-processed");
    });
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: themeVariables(),
    });

    return mermaid.run({ nodes: nodes }).then(function () {
      lockSvgWidths(nodes);
    });
  }

  function watchTheme(mermaid) {
    var body = document.body;
    var lastDark = body.classList.contains("dark-mode");
    var observer = new MutationObserver(function () {
      var isDark = body.classList.contains("dark-mode");
      if (isDark === lastDark) return;
      lastDark = isDark;
      render(mermaid, diagramNodes()).catch(function (error) {
        restoreSources(diagramNodes());
        updateStatus("failed", errorMessage(error));
      });
    });
    observer.observe(body, { attributes: true, attributeFilter: ["class"] });
  }

  function renderMermaid() {
    var nodes = diagramNodes();
    if (!nodes.length) {
      updateStatus("idle");
      return;
    }

    captureSources(nodes);
    updateStatus("loading");
    var moduleUrl = new URL("./vendor/mermaid/10/mermaid.esm.min.mjs", import.meta.url);

    import(moduleUrl.href)
      .then(function (module) {
        updateStatus("rendering");
        return render(module.default, nodes).then(function () {
          watchTheme(module.default);
          updateStatus("rendered");
        });
      })
      .catch(function (error) {
        restoreSources(nodes);
        updateStatus("failed", errorMessage(error));
        if (window.console) {
          console.warn("[mermaid] render failed", moduleUrl.href, error);
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderMermaid);
  } else {
    renderMermaid();
  }
})();
