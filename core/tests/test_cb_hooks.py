"""D7.6: the design system styles every shared public cb- hook."""

from pathlib import Path

from django.test import SimpleTestCase

DESIGN_SYSTEM = Path(__file__).resolve().parents[2] / "templates/core/_design_system.html"

HOOKS = (
    "cb-button-primary",
    "cb-field",
    "cb-page",
    "cb-page-header",
    "cb-page-title",
    "cb-list",
    "cb-card",
    "cb-card-title",
    "cb-card-meta",
    "cb-badge",
    "cb-button",
    "cb-form",
    "cb-alert",
    "cb-empty",
    "cb-pager",
)


class SharedPublicHookStyleTests(SimpleTestCase):
    def test_every_cb_hook_has_a_rule_and_the_primary_action_comes_first(self) -> None:
        source = DESIGN_SYSTEM.read_text(encoding="utf-8")
        positions = []
        for name in HOOKS:
            needle = f".{name} {{"
            index = source.find(needle)
            self.assertNotEqual(index, -1, f"missing rule for .{name}")
            positions.append((name, index))
        self.assertLess(
            dict(positions)["cb-button-primary"],
            dict(positions)["cb-page"],
        )
        self.assertLess(
            dict(positions)["cb-field"],
            dict(positions)["cb-page"],
        )
