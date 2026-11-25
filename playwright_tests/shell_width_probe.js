// Measures every visible content box inside <main> against the home shell's
// content box (the footer's `.shell` inner box, which is the same column the
// homepage composes in).  Returns {reference, offenders}.
//
// What counts as content: text runs, replaced elements (images, SVG, video,
// iframes, form controls), and drawn boxes (a visible border, background or
// shadow) that are not full-bleed bands.  A band that spans the viewport is
// a background, not content; its children are still measured.  Everything is
// clipped by ancestors that clip overflow, so a scrolling table measures as
// its visible frame.
(tolerance) => {
  const footerShell = document.querySelector(".site-footer-inner");
  const fs = getComputedStyle(footerShell);
  const fr = footerShell.getBoundingClientRect();
  const reference = {
    left: fr.left + parseFloat(fs.paddingLeft),
    right: fr.right - parseFloat(fs.paddingRight),
  };
  const viewport = document.documentElement.clientWidth;
  const main = document.querySelector("main");
  if (!main) return { reference, offenders: [], viewport };

  const transparent = (c) => !c || c === "transparent" || /rgba\([^)]*,\s*0\)$/.test(c);
  const describe = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += "#" + el.id;
    if (el.classList.length) s += "." + [...el.classList].slice(0, 3).join(".");
    return s;
  };
  const clipFor = (el) => {
    let left = -Infinity;
    let right = Infinity;
    for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const st = getComputedStyle(a);
      // A snap scroller (the phone card rows) starts on the shell column and
      // shows the next card peeking past it by design, as the homepage's own do.
      if (st.overflowX !== "visible" && st.scrollSnapType !== "none") {
        left = Math.max(left, reference.left);
        right = Math.min(right, reference.right);
        continue;
      }
      if (st.overflowX !== "visible" || st.contain.includes("paint")) {
        const r = a.getBoundingClientRect();
        left = Math.max(left, r.left);
        right = Math.min(right, r.right);
      }
    }
    return { left, right };
  };
  const visible = (el) => {
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || parseFloat(st.opacity) === 0) return false;
    if (el.closest("[hidden], [aria-hidden='true'] .sr-only, .sr-only, .visually-hidden, dialog:not([open])")) return false;
    return true;
  };

  const offenders = [];
  const consider = (el, rect, kind) => {
    if (rect.width < 1 || rect.height < 1) return;
    const clip = clipFor(el);
    const left = Math.max(rect.left, clip.left);
    const right = Math.min(rect.right, clip.right);
    if (right - left < 1) return;
    if (left < reference.left - tolerance || right > reference.right + tolerance) {
      offenders.push({
        element: describe(el),
        path: [el.parentElement, el.parentElement && el.parentElement.parentElement]
          .filter(Boolean).map(describe).reverse().join(" > "),
        kind,
        left: Math.round(left * 10) / 10,
        right: Math.round(right * 10) / 10,
      });
    }
  };

  for (const el of main.querySelectorAll("*")) {
    if (!visible(el)) continue;
    const st = getComputedStyle(el);
    if (st.position === "fixed") continue;
    const rect = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    if (["img", "svg", "video", "iframe", "canvas", "input", "select", "textarea", "button", "picture"].includes(tag)) {
      consider(el, rect, "replaced");
      continue;
    }
    if (el.closest("svg")) continue;
    const fullBleed = rect.width >= viewport - 1;
    const drawn =
      !transparent(st.backgroundColor) ||
      st.backgroundImage !== "none" ||
      st.boxShadow !== "none" ||
      ["Left", "Right"].some((side) => parseFloat(st[`border${side}Width`]) > 0 && st[`border${side}Style`] !== "none" && !transparent(st[`border${side}Color`]));
    if (drawn && !fullBleed) consider(el, rect, "box");
    for (const node of el.childNodes) {
      if (node.nodeType !== Node.TEXT_NODE || !node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) consider(el, r, "text");
    }
  }
  // One line per element is enough for a report.
  const seen = new Set();
  return {
    reference,
    viewport,
    offenders: offenders.filter((o) => {
      const key = o.path + " " + o.element + " " + o.kind;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }),
  };
}
