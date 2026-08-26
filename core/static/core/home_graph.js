(function () {
  "use strict";

  const root = document.querySelector("[data-home-graph]");
  if (!root) return;

  const ADVANCES = {
    a: 0.621,
    b: 0.621,
    c: 0.519,
    d: 0.621,
    e: 0.582,
    f: 0.411,
    g: 0.633,
    h: 0.58,
    i: 0.229,
    j: 0.273,
    k: 0.558,
    l: 0.257,
    m: 0.919,
    n: 0.59,
    o: 0.617,
    p: 0.621,
    q: 0.621,
    r: 0.418,
    s: 0.478,
    t: 0.386,
    u: 0.581,
    v: 0.551,
    w: 0.763,
    x: 0.486,
    y: 0.581,
    z: 0.482,
    A: 0.642,
    B: 0.654,
    C: 0.636,
    D: 0.716,
    E: 0.571,
    F: 0.563,
    G: 0.696,
    H: 0.719,
    I: 0.267,
    J: 0.559,
    K: 0.662,
    L: 0.552,
    M: 0.822,
    N: 0.733,
    O: 0.761,
    P: 0.601,
    Q: 0.762,
    R: 0.681,
    S: 0.585,
    T: 0.619,
    U: 0.708,
    V: 0.674,
    W: 0.951,
    X: 0.639,
    Y: 0.584,
    Z: 0.647,
    "0": 0.608,
    "1": 0.383,
    "2": 0.564,
    "3": 0.533,
    "4": 0.568,
    "5": 0.566,
    "6": 0.548,
    "7": 0.537,
    "8": 0.545,
    "9": 0.565,
    " ": 0.278,
    "-": 0.394,
    "&": 0.699,
    ".": 0.226,
    "/": 0.553,
  };
  const FALLBACK_ADVANCE = 0.95;
  const SPOKE_FONT = 14;
  const HUB_FONT = 17;
  const LABEL_PAD = 13;
  const HUB_PAD = 16;
  const LINE_HEIGHT = 16.5;
  const SINGLE_LINE_HEIGHT = 32;
  const EXTRA_LINE_HEIGHT = 15;
  const HUB_HEIGHT = 38;
  const MARGIN = 8;
  const BASELINE_SHIFT_EM = 0.35;
  const WIDE_FRAME = [640, 400];
  const WIDE_HUB = [320, 200];
  const WIDE_POSITIONS = [
    [115, 76],
    [320, 36],
    [525, 76],
    [582, 200],
    [525, 324],
    [320, 364],
    [115, 324],
    [58, 200],
  ];
  const NARROW_FRAME = [320, 408];
  const NARROW_HUB = [160, 212];
  const NARROW_POSITIONS = [
    [160, 58],
    [232, 110],
    [261, 212],
    [232, 314],
    [160, 366],
    [88, 314],
    [59, 212],
    [88, 110],
  ];
  const NARROW_WRAP_OVER = 104;
  const WIDE_WRAP_OVER = 168;
  const RING_SPOKES = WIDE_POSITIONS.length;

  // Re-centre choreography: how long the outgoing ring holds before the new
  // one renders, and the per-node stagger cap on the way in.  Kept in one
  // place because home_graph.js's JS-driven *timing* (this setTimeout) isn't
  // touched by the CSS `prefers-reduced-motion` kill switch in
  // _design_system.html — only the transitions themselves are.
  const LEAVE_DELAY_MS = 120;
  const MAX_STAGGER_INDEX = RING_SPOKES - 1;
  const REDUCE_MOTION_QUERY =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : null;

  function prefersReducedMotion() {
    return Boolean(REDUCE_MOTION_QUERY && REDUCE_MOTION_QUERY.matches);
  }

  // A synthesized click from Enter/Space activation on a link or button has
  // `detail === 0`; a real mouse click has `detail >= 1`.  Used to decide
  // whether to restore focus to the new hub after a re-centre wipes the
  // clicked element out of the DOM.
  function isKeyboardActivation(event) {
    return Boolean(event) && event.detail === 0;
  }
  const TYPE_ORDER = [
    "wiki",
    "guide",
    "comparison",
    "roadmap",
    "transition",
    "how_to",
    "podcast",
    "person",
    "book",
    "topic",
    "article",
    "external",
  ];
  const TYPE_LABELS = {
    wiki: "Wiki",
    guide: "Guide",
    comparison: "Comparison",
    roadmap: "Roadmap",
    transition: "Transition",
    how_to: "How-to",
    podcast: "Podcast",
    person: "Person",
    book: "Book",
    topic: "Keyword",
    article: "Page",
    external: "External",
  };
  const PAGE_ACTIONS = {
    wiki: "Open wiki page",
    guide: "Open guide",
    comparison: "Open comparison",
    roadmap: "Open roadmap",
    transition: "Open transition",
    how_to: "Open how-to",
    podcast: "Open episode",
    person: "Open profile",
    book: "Open book",
    topic: "Open topic",
    article: "Open page",
  };

  const live = root.querySelector("[data-home-graph-live]");
  const fallback = root.querySelector("[data-home-graph-fallback]");
  const status = root.querySelector("[data-home-graph-status]");
  const more = root.querySelector("[data-home-graph-more]");
  const openPage = root.querySelector("[data-home-graph-open]");
  const backButton = root.querySelector("[data-home-graph-back]");
  const randomButton = root.querySelector("[data-home-graph-random]");
  const graphUrl = root.getAttribute("data-graph-url") || "";
  const startId = root.getAttribute("data-start-id") || "";
  const HISTORY_KEY = "homeWikiGraph";

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function labelEm(text) {
    let width = 0;
    for (const char of text) width += ADVANCES[char] || FALLBACK_ADVANCE;
    return width;
  }

  function balancedLines(title) {
    const words = title.split(/\s+/);
    if (words.length < 2) return [title];
    let best = [title];
    let bestScore = Infinity;
    for (let index = 1; index < words.length; index += 1) {
      const lines = [words.slice(0, index).join(" "), words.slice(index).join(" ")];
      const score = Math.max(labelEm(lines[0]), labelEm(lines[1]));
      if (score < bestScore) {
        best = lines;
        bestScore = score;
      }
    }
    return best;
  }

  function wrapLabel(title, limitEm) {
    const words = title.split(/\s+/);
    if (words.length < 2) return [title];
    const lines = [];
    let current = words[0];
    for (let index = 1; index < words.length; index += 1) {
      const candidate = `${current} ${words[index]}`;
      if (labelEm(candidate) <= limitEm) current = candidate;
      else {
        lines.push(current);
        current = words[index];
      }
    }
    lines.push(current);
    if (lines.length === 2) return balancedLines(title);
    return lines;
  }

  function ellipsize(text, limitEm) {
    if (labelEm(text) <= limitEm) return text;
    let clipped = text;
    while (clipped.length > 1 && labelEm(`${clipped}…`) > limitEm) {
      clipped = clipped.slice(0, -1).trimEnd();
    }
    return `${clipped}…`;
  }

  function twoLineLabel(title, wrapOver, font, pad) {
    const limitEm = wrapOver == null ? Infinity : Math.max(wrapOver - 2 * pad, 0) / font;
    if (!(labelEm(title) * font + 2 * pad > (wrapOver || Infinity))) return [title];
    let lines = wrapLabel(title, limitEm);
    if (lines.length > 2) {
      lines = [lines[0], lines.slice(1).join(" ")];
    }
    return lines.map((line) => ellipsize(line, limitEm));
  }

  function graphNode(point, x, y, frame, font, pad, height, wrapOver) {
    let lines = twoLineLabel(point.title, wrapOver, font, pad);
    let width = 0;
    for (const line of lines) width = Math.max(width, labelEm(line) * font + 2 * pad);
    const boxHeight = height + (lines.length - 1) * EXTRA_LINE_HEIGHT;
    x = Math.min(Math.max(x, MARGIN + width / 2), frame[0] - MARGIN - width / 2);
    y = Math.min(Math.max(y, MARGIN + boxHeight / 2), frame[1] - MARGIN - boxHeight / 2);
    const firstBaseline = y - ((lines.length - 1) / 2) * LINE_HEIGHT + BASELINE_SHIFT_EM * font;
    return {
      id: point.id,
      title: point.title,
      fullTitle: point.fullTitle || point.title,
      url: point.url,
      type: point.type,
      x: round1(x),
      y: round1(y),
      width: round1(width),
      height: boxHeight,
      fontSize: font,
      left: round1(x - width / 2),
      top: round1(y - boxHeight / 2),
      radius: round1(boxHeight / 2),
      lines: lines.map((text, index) => ({
        text,
        y: round1(firstBaseline + index * LINE_HEIGHT),
      })),
    };
  }

  function round1(value) {
    return Math.round(value * 10) / 10;
  }

  function graphLayout(kind, frame, hubCenter, positions, hub, spokes, wrapOver) {
    const hubNode = graphNode(
      hub,
      hubCenter[0],
      hubCenter[1],
      frame,
      HUB_FONT,
      HUB_PAD,
      HUB_HEIGHT,
      wrapOver
    );
    const nodes = spokes.map((point, index) =>
      graphNode(
        point,
        positions[index][0],
        positions[index][1],
        frame,
        SPOKE_FONT,
        LABEL_PAD,
        SINGLE_LINE_HEIGHT,
        wrapOver
      )
    );
    return {
      kind,
      width: frame[0],
      height: frame[1],
      hub: hubNode,
      nodes,
      edges: nodes.map((node) => ({ x1: hubNode.x, y1: hubNode.y, x2: node.x, y2: node.y })),
    };
  }

  function ringLayouts(hub, spokes) {
    const used = spokes.length;
    return [
      graphLayout(
        "wide",
        WIDE_FRAME,
        WIDE_HUB,
        WIDE_POSITIONS.slice(0, used),
        hub,
        spokes,
        WIDE_WRAP_OVER
      ),
      graphLayout(
        "narrow",
        NARROW_FRAME,
        NARROW_HUB,
        NARROW_POSITIONS.slice(0, used),
        hub,
        spokes,
        NARROW_WRAP_OVER
      ),
    ];
  }

  function typeKey(node) {
    if (node.type === "article" && node.collection) return node.collection;
    return node.type;
  }

  function typeRank(type) {
    const index = TYPE_ORDER.indexOf(type);
    return index === -1 ? TYPE_ORDER.length : index;
  }

  function nodePoint(node) {
    const title = node.label || node.title;
    return {
      id: node.id,
      title,
      fullTitle: node.title || title,
      url: node.url || "",
      type: typeKey(node),
    };
  }

  function pageAction(node) {
    return PAGE_ACTIONS[typeKey(node)] || "Open page";
  }

  function modifiedClick(event) {
    return event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0;
  }

  function currentHistoryState() {
    return history.state && typeof history.state === "object" ? history.state : {};
  }

  function withGraphState(nodeId, depth) {
    return { ...currentHistoryState(), [HISTORY_KEY]: { nodeId, depth } };
  }

  function graphState(state) {
    return state && state[HISTORY_KEY] ? state[HISTORY_KEY] : null;
  }

  function nodeMarkup(node, extraClass, index) {
    const lines = node.lines
      .map(
        (line) =>
          `<tspan x="${node.x}" y="${line.y}">${escapeHtml(line.text)}</tspan>`
      )
      .join("");
    const href = node.url ? ` href="${escapeHtml(node.url)}"` : "";
    const fullTitle = node.fullTitle || node.title;
    // `--i` staggers the entrance transition per spoke (see the
    // `[data-home-graph-live]` rules in home.html); the hub gets none, so it
    // lands first.
    const style = index == null ? "" : ` style="--i:${Math.min(index, MAX_STAGGER_INDEX)}"`;
    return (
      `<a class="graph-svg-node ${extraClass} graph-svg-type-${escapeHtml(node.type)}"${href}${style}` +
      ` data-node-id="${escapeHtml(node.id)}" title="${escapeHtml(fullTitle)}" aria-label="${escapeHtml(fullTitle)}">` +
      `<rect class="graph-svg-shape" x="${node.left}" y="${node.top}" width="${node.width}" height="${node.height}" rx="${node.radius}"/>` +
      `<text text-anchor="middle" font-size="${node.fontSize}">${lines}</text>` +
      `</a>`
    );
  }

  function layoutMarkup(layout, hub, spokeCount, connectionCount) {
    const edges = layout.edges
      .map(
        (edge, index) =>
          `<line class="graph-svg-edge" style="--i:${Math.min(index, MAX_STAGGER_INDEX)}"` +
          ` x1="${edge.x1}" y1="${edge.y1}" x2="${edge.x2}" y2="${edge.y2}"/>`
      )
      .join("");
    const nodes = layout.nodes.map((node, index) => nodeMarkup(node, "", index)).join("");
    const label = `${hub.title} and ${spokeCount} of its ${connectionCount} connections`;
    return (
      `<svg class="graph-svg graph-svg-${layout.kind}" viewBox="0 0 ${layout.width} ${layout.height}" role="group" aria-label="${escapeHtml(label)}">` +
      `<g aria-hidden="true">${edges}</g>${nodes}${nodeMarkup(layout.hub, "graph-svg-hub")}</svg>`
    );
  }

  function diverseNeighbors(linked, cap) {
    const groups = new Map();
    for (const node of linked) {
      const key = typeKey(node);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(node);
    }
    const keys = Array.from(groups.keys()).sort((a, b) => typeRank(a) - typeRank(b));
    const cursor = new Map(keys.map((key) => [key, 0]));
    const out = [];
    let advanced = true;
    while (out.length < cap && advanced) {
      advanced = false;
      for (const key of keys) {
        const items = groups.get(key);
        const index = cursor.get(key);
        if (index < items.length) {
          out.push(items[index]);
          cursor.set(key, index + 1);
          advanced = true;
          if (out.length >= cap) break;
        }
      }
    }
    return out;
  }

  function linkedNodes(graph, node, degreeById) {
    const nodes = new Map((graph.nodes || []).map((item) => [item.id, item]));
    const linked = new Map();
    for (const link of graph.links || []) {
      let id = "";
      if (link.source === node.id) id = link.target;
      if (link.target === node.id) id = link.source;
      if (!id || !nodes.has(id) || id === node.id) continue;
      const current = linked.get(id);
      const weight = Math.max(current ? current.weight : 0, link.weight || 1);
      linked.set(id, { ...nodes.get(id), weight });
    }
    return Array.from(linked.values()).sort(
      (a, b) =>
        (degreeById.get(b.id) || 0) - (degreeById.get(a.id) || 0) ||
        b.weight - a.weight ||
        typeRank(typeKey(a)) - typeRank(typeKey(b)) ||
        String(a.label).localeCompare(String(b.label))
    );
  }

  function pickRandomCenter(graph, degreeById) {
    const nodes = graph.nodes || [];
    const rich = nodes.filter((node) => typeKey(node) === "wiki" && (degreeById.get(node.id) || 0) >= 4);
    const pool = rich.length ? rich : nodes;
    if (!pool.length) return null;
    return pool[Math.floor(Math.random() * pool.length)];
  }

  function bindDrawing(onExplore) {
    if (!live) return;
    live.querySelectorAll("[data-node-id]").forEach((nodeEl) => {
      nodeEl.addEventListener("click", (event) => {
        if (modifiedClick(event)) return;
        event.preventDefault();
        const id = nodeEl.getAttribute("data-node-id");
        if (nodeEl.classList.contains("graph-svg-hub")) return;
        if (id) onExplore(id, isKeyboardActivation(event));
      });
    });
  }

  function render(graph, node, linked, degreeById, onExplore, options) {
    options = options || {};
    if (!live || !node) return;
    const spokes = diverseNeighbors(linked, RING_SPOKES);
    const layouts = ringLayouts(
      nodePoint(node),
      spokes.map(nodePoint)
    );
    if (options.animateEnter) live.classList.add("is-entering");
    live.innerHTML = layouts.map((layout) => layoutMarkup(layout, nodePoint(node), spokes.length, linked.length)).join("");
    live.classList.remove("is-leaving");
    bindDrawing(onExplore);
    if (fallback) fallback.hidden = true;
    live.hidden = false;
    root.classList.add("is-ready");
    if (options.animateEnter) {
      // Force a reflow so the browser commits the suppressed `.is-entering`
      // starting state before the next frame lifts it — otherwise removing
      // the class immediately after setting it never triggers a transition.
      void live.offsetWidth;
      window.requestAnimationFrame(() => {
        live.classList.remove("is-entering");
      });
    }
    if (options.focusHub) {
      const hubLink = live.querySelector(".graph-svg-hub");
      if (hubLink && typeof hubLink.focus === "function") {
        hubLink.focus({ preventScroll: true });
      }
    }
    if (status) {
      status.textContent = `Exploring ${node.label || node.title}. Click a neighbour to move there.`;
    }
    if (openPage) {
      if (node.url) {
        openPage.classList.remove("is-hidden-reserved");
        openPage.setAttribute("href", node.url);
        openPage.textContent = `${pageAction(node)} →`;
      } else {
        // Kept in the toolbar's flex flow (see `.is-hidden-reserved` in
        // home.html) rather than `hidden`, so a hub without a page of its
        // own doesn't reflow — and resize — the toolbar row.
        openPage.classList.add("is-hidden-reserved");
      }
    }
    if (more) {
      // Always in flow once JS is driving (see the `.home-graph-more` note
      // in home.html): an empty `<summary>` reserves the same row height a
      // real one would, so a hub with 8 or fewer connections doesn't shrink
      // the frame relative to one with more.
      more.hidden = false;
      const extras = linked.filter((item) => !spokes.some((spoke) => spoke.id === item.id));
      more.classList.toggle("is-empty", extras.length === 0);
      more.open = false;
      if (!extras.length) {
        more.innerHTML = `<summary aria-hidden="true">&nbsp;</summary>`;
      } else {
        more.innerHTML =
          `<summary>${extras.length} more connection${extras.length === 1 ? "" : "s"}</summary>` +
          `<ul class="home-graph-more-list">${extras
            .map(
              (item) =>
                `<li><button type="button" class="home-graph-more-item" data-explore-id="${escapeHtml(item.id)}">${escapeHtml(item.label || item.title)} <span>${escapeHtml(TYPE_LABELS[typeKey(item)] || item.type)}</span></button></li>`
            )
            .join("")}</ul>`;
        more.querySelectorAll("[data-explore-id]").forEach((button) => {
          button.addEventListener("click", (event) =>
            onExplore(button.getAttribute("data-explore-id"), isKeyboardActivation(event))
          );
        });
      }
    }
    const legendCount = root.querySelector("[data-home-graph-count]");
    if (legendCount) {
      legendCount.textContent = `${spokes.length} of ${linked.length} ${node.label || node.title} connections drawn`;
    }
  }

  // Orchestrates the leave -> enter crossfade around `render`: fades the
  // outgoing ring (holding the clicked node in place), waits, then renders
  // the new ring already staggering in.  Skipped — rendering immediately,
  // as the widget always used to — for the very first paint and whenever
  // prefers-reduced-motion is set, per the JS-timing guard called out above
  // `LEAVE_DELAY_MS`.
  function transitionRender(opts) {
    const animate = opts.transition && !prefersReducedMotion();
    if (!animate) {
      live.classList.remove("is-leaving");
      render(opts.graph, opts.node, opts.linked, opts.degreeById, opts.onExplore, {
        animateEnter: false,
        focusHub: opts.focusHub,
      });
      return;
    }
    live.classList.add("is-leaving");
    if (opts.chosenId) {
      const chosenEl = live.querySelector(`[data-node-id="${opts.chosenId}"]`);
      if (chosenEl) chosenEl.classList.add("is-chosen");
    }
    window.setTimeout(() => {
      render(opts.graph, opts.node, opts.linked, opts.degreeById, opts.onExplore, {
        animateEnter: true,
        focusHub: opts.focusHub,
      });
    }, LEAVE_DELAY_MS);
  }

  fetch(graphUrl, { credentials: "same-origin" })
    .then((response) => {
      if (!response.ok) throw new Error("graph unavailable");
      return response.json();
    })
    .then((graph) => {
      const nodesById = new Map((graph.nodes || []).map((node) => [node.id, node]));
      const degreeById = new Map();
      const seen = new Map();
      for (const link of graph.links || []) {
        let set = seen.get(link.source);
        if (!set) seen.set(link.source, (set = new Set()));
        set.add(link.target);
        set = seen.get(link.target);
        if (!set) seen.set(link.target, (set = new Set()));
        set.add(link.source);
      }
      for (const [id, set] of seen) degreeById.set(id, set.size);

      let depth = 0;
      // Whether `render` has run at least once: gates the re-centre
      // crossfade off for the very first paint, which should appear exactly
      // as it does today (no leaving ring to fade from).
      let hasRendered = false;
      const syncBack = () => {
        if (backButton) backButton.hidden = depth < 1;
      };
      const showNode = (node, historyMode, options) => {
        if (!node) return;
        options = options || {};
        const linked = linkedNodes(graph, node, degreeById);
        const shouldTransition = hasRendered;
        hasRendered = true;
        transitionRender({
          graph,
          node,
          linked,
          degreeById,
          onExplore: (id, keyboard) => {
            const next = nodesById.get(id);
            if (next) showNode(next, "push", { chosenId: id, keyboard });
          },
          chosenId: options.chosenId || null,
          focusHub: Boolean(options.keyboard),
          transition: shouldTransition,
        });
        if (historyMode === "push") {
          depth += 1;
          history.pushState(withGraphState(node.id, depth), "", window.location.href);
        } else if (historyMode !== false) {
          history.replaceState(withGraphState(node.id, depth), "", window.location.href);
        }
        syncBack();
      };

      if (backButton) {
        backButton.addEventListener("click", () => window.history.back());
      }
      if (randomButton) {
        randomButton.addEventListener("click", () => {
          const next = pickRandomCenter(graph, degreeById);
          if (next) showNode(next, "push");
        });
      }
      window.addEventListener("popstate", (event) => {
        const stored = graphState(event.state);
        depth = stored && typeof stored.depth === "number" ? stored.depth : 0;
        const node = nodesById.get(stored && stored.nodeId) || nodesById.get(startId);
        if (node) showNode(node, false);
      });

      const stored = graphState(currentHistoryState());
      depth = stored && typeof stored.depth === "number" ? stored.depth : 0;
      const initial =
        nodesById.get(stored && stored.nodeId) ||
        nodesById.get(startId) ||
        pickRandomCenter(graph, degreeById);
      showNode(initial, false);
    })
    .catch(() => {
      root.classList.add("is-fallback");
    });
})();
