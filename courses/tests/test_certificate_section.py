"""The course landing pages promise the certificate the platform issues.

``courses/_certificate_preview.html`` draws the certificate as a simplified
picture of the artifact the generator renders (banner-generator's
dtc-zoomcamp-certificate), and both landing pages include it so a reader
knows finishing the course ends in a certificate.  The card keeps its own
theme-invariant palette (the artifact is a white sheet with blue lettering
in either theme), so — like every page-local palette in this system — its
colours must live in the token block and never inline in a rule.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates/courses"
COHORT_LANDING = TEMPLATES_DIR / "course.html"
FAMILY_LANDING = TEMPLATES_DIR / "course_family.html"
STYLES = TEMPLATES_DIR / "_certificate_styles.html"
PREVIEW = TEMPLATES_DIR / "_certificate_preview.html"
STATIC_DIR = Path(__file__).resolve().parents[1] / "static/courses"
CERT_ASSETS = (
    "alexey-signature.svg",
    "certificate-dtc-logo.svg",
    "certificate-main-deco.svg",
    "certificate-name-deco-left.svg",
    "certificate-name-deco-right.svg",
)

COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(")


class CertificateSectionContractTests(SimpleTestCase):
    def test_both_landing_pages_include_the_certificate(self) -> None:
        for page in (COHORT_LANDING, FAMILY_LANDING):
            with self.subTest(page=page.name):
                source = page.read_text()
                self.assertIn(
                    '{% include "courses/_certificate_styles.html" %}',
                    source,
                )
                self.assertIn(
                    '{% include "courses/_certificate_preview.html" %}',
                    source,
                )

    def test_certificate_names_the_course_from_the_database(self) -> None:
        """The course line is data, never a hardcoded course name."""

        source = PREVIEW.read_text()
        self.assertIn("certificate_course_name|default:course_family.title", source)

    def test_the_artifact_assets_are_shipped(self) -> None:
        for asset in CERT_ASSETS:
            with self.subTest(asset=asset):
                self.assertTrue((STATIC_DIR / asset).is_file())
        self.assertIn("courses/alexey-signature.svg", PREVIEW.read_text())

    def test_certificate_colours_live_in_the_token_block(self) -> None:
        source = STYLES.read_text()
        start = source.index(":root {")
        end = source.index("}", start)
        rules_after_token_block = source[:start] + source[end:]
        outside = COLOR_LITERAL.search(rules_after_token_block)
        self.assertIsNone(
            outside,
            f"colour literal outside the token block: {outside!r}",
        )
