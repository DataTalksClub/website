from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page

from core.accessibility_registry import AXE_EXCEPTIONS

AXE_ENGINE_VERSION = "4.12.1"
WCAG_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa")
_TIMEZONE_RE = re.compile(r"(?:UTC|GMT|CEST|CET|[+-][0-9]{2}:[0-9]{2})", re.IGNORECASE)


def _bounded(items: Iterable[str], *, limit: int = 12) -> str:
    values = list(dict.fromkeys(items))
    shown = values[:limit]
    suffix = f"; … {len(values) - limit} more" if len(values) > limit else ""
    return "; ".join(shown) + suffix


def axe_issues(page: Page, state: str) -> list[str]:
    axe = Axe()
    page.evaluate(axe.axe_script)
    version = page.evaluate("window.axe.version")
    if version != AXE_ENGINE_VERSION:
        return [f"{state}: axe engine {version!r}, expected {AXE_ENGINE_VERSION}"]
    results = page.evaluate(
        """
        async ({tags}) => axe.run(document, {
          runOnly: {type: "tag", values: tags},
          resultTypes: ["violations"],
          rules: {"target-size": {enabled: true}}
        })
        """,
        {"tags": list(WCAG_TAGS)},
    )
    issues: list[str] = []
    for violation in results["violations"]:
        for node in violation["nodes"]:
            selector = " ".join(str(value) for value in node["target"])
            exception = next(
                (
                    candidate
                    for candidate in AXE_EXCEPTIONS
                    if candidate.rule == violation["id"]
                    and candidate.selector == selector
                    and candidate.state == state
                    and candidate.expires > date.today()
                ),
                None,
            )
            if exception is None:
                issues.append(f"{state}: axe {violation['id']} at {selector}")
    return issues


def chromium_blink_tree_issues(
    page: Page,
    state: str,
    *,
    required_roles: tuple[str, ...],
    required_name_fragments: tuple[str, ...] = (),
) -> tuple[dict[str, str], list[str]]:
    """Inspect Chromium/Blink's platform accessibility tree through CDP.

    This is named browser-engine evidence, not a substitute for a human test with a real screen
    reader. The separate manual gate records that pairing.
    """

    cdp = page.context.new_cdp_session(page)
    version = cdp.send("Browser.getVersion")
    cdp.send("Accessibility.enable")
    tree = cdp.send("Accessibility.getFullAXTree")
    nodes = [node for node in tree["nodes"] if not node.get("ignored", False)]
    roles = {
        str(node.get("role", {}).get("value", ""))
        for node in nodes
        if node.get("role", {}).get("value")
    }
    names = [
        str(node.get("name", {}).get("value", "")).strip()
        for node in nodes
        if str(node.get("name", {}).get("value", "")).strip()
    ]
    issues = [
        f"{state}: Chromium/Blink accessibility tree is missing role {role}"
        for role in required_roles
        if role not in roles
    ]
    for fragment in required_name_fragments:
        if not any(fragment.casefold() in name.casefold() for name in names):
            issues.append(
                f"{state}: Chromium/Blink accessibility tree has no name containing {fragment!r}"
            )
    evidence = {
        "browser": str(version["product"]),
        "engine": "Blink accessibility tree",
        "protocol": str(version["protocolVersion"]),
    }
    return evidence, issues


def structure_issues(page: Page, state: str) -> list[str]:
    result = page.evaluate(
        """
        () => ({
          title: document.title.trim(),
          mains: document.querySelectorAll('main, [role="main"]').length,
          h1s: document.querySelectorAll(
            'main h1, [role="main"] [role="heading"][aria-level="1"]'
          ).length,
          headers: document.querySelectorAll('body > header, [role="banner"]').length,
          footers: document.querySelectorAll('body > footer, [role="contentinfo"]').length,
          duplicateIds: [...document.querySelectorAll('[id]')]
            .map((node) => node.id)
            .filter((id, index, values) => values.indexOf(id) !== index),
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          overflowSources: [...document.querySelectorAll('body *')]
            .filter((node) => {
              const rect = node.getBoundingClientRect();
              return rect.right > document.documentElement.clientWidth + 1
                || rect.left < -1;
            })
            .slice(0, 5)
            .map((node) => {
              const rect = node.getBoundingClientRect();
              return `${node.tagName.toLowerCase()}#${node.id}.${node.className}`
                + ` [${Math.round(rect.left)},${Math.round(rect.right)}]`;
            }),
        })
        """
    )
    issues = []
    for key, expected in (("mains", 1), ("h1s", 1), ("headers", 1), ("footers", 1)):
        if result[key] != expected:
            issues.append(f"{state}: expected {expected} {key}, found {result[key]}")
    if not result["title"]:
        issues.append(f"{state}: document title is empty")
    if result["duplicateIds"]:
        issues.append(f"{state}: duplicate ids {_bounded(result['duplicateIds'])}")
    if result["overflow"] > 1:
        issues.append(
            f"{state}: page overflows horizontally by {result['overflow']} CSS px; "
            f"sources {_bounded(result['overflowSources'])}"
        )
    return issues


def skip_link_issues(page: Page, state: str) -> list[str]:
    return page.evaluate(
        """
        ({state}) => {
          const issues = [];
          const skip = document.querySelector('a.skip-link');
          if (!skip) return [`${state}: skip link is missing`];
          const target = document.querySelector(skip.hash);
          if (!target) return [`${state}: skip target ${skip.hash} is missing`];
          skip.focus();
          if (document.activeElement !== skip) {
            issues.push(`${state}: skip link cannot receive focus`);
          }
          skip.click();
          if (document.activeElement !== target) {
            issues.push(`${state}: skip target cannot receive focus`);
          }
          return issues;
        }
        """,
        {"state": state},
    )


def focus_issues(page: Page, state: str) -> list[str]:
    selector = (
        'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
        'select:not([disabled]), textarea:not([disabled]), summary, [tabindex="0"]'
    )
    page.evaluate(
        """
        () => {
          document.body.tabIndex = -1;
          document.body.focus();
          window.scrollTo(0, 0);
        }
        """
    )
    page.keyboard.press("Tab")
    settle_focus = (
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    page.evaluate(settle_focus)
    issues: list[str] = page.evaluate(
        """
        ({state}) => [...document.querySelectorAll('[tabindex]')].flatMap((node) => {
          const value = Number(node.getAttribute('tabindex'));
          return Number.isFinite(value) && value > 0
            ? [`${state}: positive tabindex creates a non-logical keyboard focus order`]
            : [];
        })
        """,
        {"state": state},
    )
    seen: list[str] = []
    previous: int | None = None
    for _step in range(40):
        result = page.evaluate(
            """
            ({selector}) => {
              const all = [...document.querySelectorAll(selector)];
              const node = document.activeElement;
              if (!(node instanceof HTMLElement) || node === document.body) return null;
              node.scrollIntoView({block: 'center', inline: 'center'});
              const style = getComputedStyle(node);
              const outline = parseFloat(style.outlineWidth) >= 2
                && style.outlineStyle !== 'none';
              const shadow = style.boxShadow !== 'none';
              const rect = node.getBoundingClientRect();
              const x = Math.min(innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
              const y = Math.min(innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
              const top = document.elementFromPoint(x, y);
              const candidateCount = all.filter((candidate) => {
                const candidateStyle = getComputedStyle(candidate);
                const candidateRect = candidate.getBoundingClientRect();
                return candidateStyle.display !== 'none'
                  && candidateStyle.visibility !== 'hidden'
                  && candidateRect.width > 0 && candidateRect.height > 0;
              }).length;
              return {
                index: all.indexOf(node),
                candidateCount,
                description: `${node.tagName.toLowerCase()}#${node.id}`
                  + `[${(node.getAttribute('aria-label') || node.textContent || '')
                    .trim().slice(0, 32)}]`,
                focusVisible: outline || shadow,
                focusStyle: `${style.outlineWidth}/${style.outlineStyle}/${style.boxShadow}`,
                obscured: Boolean(
                  top && top !== node && !node.contains(top) && !top.contains(node)
                ),
              };
            }
            """,
            {"selector": selector},
        )
        if result is None:
            if not seen:
                issues.append(f"{state}: keyboard focus left the document unexpectedly")
            break
        index = result["index"]
        if index < 0:
            issues.append(f"{state}: keyboard focus reached an untracked control")
            break
        signature = f"{index}:{result['description']}"
        if seen and signature == seen[0]:
            if len(seen) == 1 and result["candidateCount"] > 1:
                issues.append(f"{state}: keyboard focus is trapped at {result['description']}")
            break
        if signature in seen:
            issues.append(f"{state}: keyboard focus is trapped at {result['description']}")
            break
        if previous is not None and index <= previous:
            break
        seen.append(signature)
        previous = index
        if not result["focusVisible"] or result["obscured"]:
            page.wait_for_timeout(100)
            settled = page.evaluate(
                """
                () => {
                  const node = document.activeElement;
                  if (!(node instanceof HTMLElement)) return null;
                  const style = getComputedStyle(node);
                  const rect = node.getBoundingClientRect();
                  const x = Math.min(innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
                  const y = Math.min(innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
                  const top = document.elementFromPoint(x, y);
                  return {
                    focusVisible: (
                      parseFloat(style.outlineWidth) >= 2 && style.outlineStyle !== 'none'
                    ) || style.boxShadow !== 'none',
                    obscured: Boolean(
                      top && top !== node && !node.contains(top) && !top.contains(node)
                    ),
                  };
                }
                """
            )
            if settled is not None:
                result.update(settled)
        if not result["focusVisible"]:
            issues.append(
                f"{state}: focus is not visible on {result['description']} ({result['focusStyle']})"
            )
        if result["obscured"]:
            issues.append(f"{state}: focus target is obscured: {result['description']}")
        page.keyboard.press("Tab")
        page.evaluate(settle_focus)
    if not seen:
        issues.append(f"{state}: no keyboard-focusable controls were reached")
    page.evaluate("window.scrollTo({left: 0, top: 0, behavior: 'instant'})")
    return issues


def control_state_issues(page: Page, state: str) -> list[str]:
    return page.evaluate(
        """
        ({state}) => {
          const issues = [];
          for (const toggle of document.querySelectorAll('[aria-expanded]')) {
            const controls = toggle.getAttribute('aria-controls');
            if (controls && !document.getElementById(controls)) {
              issues.push(`${state}: aria-expanded control has missing target #${controls}`);
            }
            if (!['true', 'false'].includes(toggle.getAttribute('aria-expanded'))) {
              issues.push(`${state}: aria-expanded is stale or invalid`);
            }
          }
          for (const invalid of document.querySelectorAll('[aria-invalid="true"]')) {
            const errorId = invalid.getAttribute('aria-errormessage');
            const error = errorId && document.getElementById(errorId);
            if (!error || !error.textContent.trim()) {
              issues.push(`${state}: #${invalid.id} has no linked field error`);
            }
            const summaryLink = document.querySelector(
              `[data-focus-error-summary] a[href="#${CSS.escape(invalid.id)}"]`
            );
            if (!summaryLink) issues.push(`${state}: #${invalid.id} is absent from error summary`);
          }
          for (const status of document.querySelectorAll('[role="status"], [aria-live]')) {
            const live = status.getAttribute('aria-live');
            if (status.getAttribute('role') !== 'status' && (!live || live === 'off')) {
              issues.push(`${state}: status region has no polite announcement semantics`);
            }
          }
          return issues;
        }
        """,
        {"state": state},
    )


def target_size_issues(page: Page, state: str) -> list[str]:
    return page.evaluate(
        """
        ({state}) => {
          const targets = [...document.querySelectorAll(
            'button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
            + 'select:not([disabled]), textarea:not([disabled]), summary, a[href]'
          )].flatMap((node) => {
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            // Chromium can retain a stale rectangle for descendants of a closed details element.
            if (!node.checkVisibility()) return [];
            if (style.display === 'none' || style.visibility === 'hidden') return [];
            if (rect.width <= 0 || rect.height <= 0) return [];
            if (node.matches('a') && style.display === 'inline') return [];
            return [{node, rect}];
          });
          const spacingArea = (rect) => {
            const inline = Math.max(0, 24 - rect.width) / 2;
            const block = Math.max(0, 24 - rect.height) / 2;
            return {
              left: rect.left - inline,
              right: rect.right + inline,
              top: rect.top - block,
              bottom: rect.bottom + block,
            };
          };
          const overlaps = (left, right) => (
            left.left < right.right && left.right > right.left
              && left.top < right.bottom && left.bottom > right.top
          );
          return targets.flatMap(({node, rect}, index) => {
            if (rect.width + 0.5 >= 24 && rect.height + 0.5 >= 24) return [];
            const area = spacingArea(rect);
            const crowded = targets.some((other, otherIndex) => (
              otherIndex !== index && overlaps(area, spacingArea(other.rect))
            ));
            return crowded
              ? [`${state}: undersized target ${node.tagName.toLowerCase()}#${node.id}`
                + `.${String(node.className)}[${(node.getAttribute('aria-label')
                  || node.textContent || '').trim().slice(0, 48)}] `
                + `${rect.width.toFixed(1)}x${rect.height.toFixed(1)} without spacing`]
              : [];
          });
        }
        """,
        {"state": state},
    )


def media_date_issues(page: Page, state: str) -> list[str]:
    values = page.evaluate(
        """
        () => ({
          images: [...document.querySelectorAll('img')].map((node) => ({
            alt: node.getAttribute('alt'),
            src: node.getAttribute('src') || '',
          })),
          media: [...document.querySelectorAll('audio, video')].map((node) => ({
            captions: Boolean(node.querySelector('track[kind="captions"]')),
            transcript: Boolean(document.querySelector(
              'a[href*="transcript"], #transcript-heading'
            )),
          })),
          times: [...document.querySelectorAll('time')].map((node) => ({
            machine: node.getAttribute('datetime') || '',
            text: node.textContent.trim(),
          })),
        })
        """
    )
    issues = []
    for image in values["images"]:
        if image["alt"] is None:
            issues.append(f"{state}: image is missing an alt attribute")
    for media in values["media"]:
        if not media["captions"] and not media["transcript"]:
            issues.append(f"{state}: audio/video has neither captions nor transcript")
    for time in values["times"]:
        if not time["machine"]:
            issues.append(f"{state}: time has no machine-readable datetime")
        if "T" in time["machine"] and not _TIMEZONE_RE.search(time["text"]):
            issues.append(f"{state}: date/time text has no explicit timezone")
    return issues


def motion_issues(page: Page, state: str) -> list[str]:
    return page.evaluate(
        """
        ({state}) => {
          const seconds = (value) => Math.max(...value.split(',').map((part) => {
            const number = parseFloat(part) || 0;
            return part.trim().endsWith('ms') ? number / 1000 : number;
          }));
          return [...document.querySelectorAll('body *')].flatMap((node) => {
            const style = getComputedStyle(node);
            const moves = seconds(style.animationDuration) > 0.011
              || seconds(style.transitionDuration) > 0.011;
            const description = `${node.tagName.toLowerCase()}#${node.id}`;
            return moves
              ? [`${state}: non-essential motion remains on ${description}`]
              : [];
          });
        }
        """,
        {"state": state},
    )


def preserved_value_issues(
    page: Page,
    state: str,
    expected: Mapping[str, str],
) -> list[str]:
    actual = page.evaluate(
        """
        (names) => Object.fromEntries(names.map((name) => {
          const field = document.querySelector(`[name="${CSS.escape(name)}"]`);
          return [name, field ? field.value : null];
        }))
        """,
        list(expected),
    )
    return [
        f"{state}: valid value for {name} was not preserved"
        for name, value in expected.items()
        if actual[name] != value
    ]


def text_spacing_issues(page: Page, state: str) -> list[str]:
    page.add_style_tag(
        content="""
        * { line-height: 1.5 !important; letter-spacing: 0.12em !important;
            word-spacing: 0.16em !important; }
        p { margin-bottom: 2em !important; }
        """
    )
    return structure_issues(page, f"{state}.text-spacing")


def assert_accessible_page(page: Page, state: str, *, comprehensive: bool = False) -> None:
    issue_groups = []
    if comprehensive:
        issue_groups.extend(
            (
                focus_issues(page, state),
                target_size_issues(page, state),
                motion_issues(page, state),
            )
        )
    issue_groups.extend(
        (
            axe_issues(page, state),
            structure_issues(page, state),
            skip_link_issues(page, state),
            control_state_issues(page, state),
            media_date_issues(page, state),
        )
    )
    issues = [issue for group in issue_groups for issue in group]
    if issues:
        raise AssertionError(_bounded(issues))
