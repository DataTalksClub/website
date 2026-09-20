from pathlib import Path

from django.template.loader import render_to_string
from django.templatetags.static import static
from django.test import SimpleTestCase

ILLUSTRATION_DIR = Path(__file__).resolve().parents[1] / "static/core/illustrations"


class IllustrationAssetPairTests(SimpleTestCase):
    def test_every_light_illustration_has_a_dark_companion(self):
        light_names = {
            path.name for path in ILLUSTRATION_DIR.glob("*.webp") if "-dark." not in path.name
        }
        expected_dark_names = {name.removesuffix(".webp") + "-dark.webp" for name in light_names}
        installed_dark_names = {path.name for path in ILLUSTRATION_DIR.glob("*-dark.webp")}

        self.assertSetEqual(installed_dark_names, expected_dark_names)

    def test_tour_hero_uses_the_matching_light_and_dark_pair(self):
        markup = render_to_string("core/_tour_hero_illustration.html", {"loading": "lazy"})

        self.assertIn(static("core/illustrations/tour-community.webp"), markup)
        self.assertIn(static("core/illustrations/tour-community-dark.webp"), markup)
        self.assertEqual(markup.count('alt=""'), 2)
        self.assertEqual(markup.count('decoding="async"'), 2)
        self.assertEqual(markup.count('loading="lazy"'), 2)
