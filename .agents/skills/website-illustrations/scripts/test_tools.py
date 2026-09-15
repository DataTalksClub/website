# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow==12.3.0", "numpy==2.5.1", "scipy==1.18.1"]
# ///
"""Isolated script checks: uv run --script <this-file>. No website DB is used."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import finish_chroma
from PIL import Image
from verify_assets import verify_pair


class IllustrationToolsTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[4] / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="illustration-tools-", dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def keyed_source(self):
        source = self.root / "source.png"
        image = Image.new("RGB", (64, 64), (255, 0, 255))
        image.paste((180, 185, 240), (16, 16, 48, 48))
        image.paste((255, 255, 255), (24, 24, 40, 40))
        image.save(source)
        return source

    def finish(self, source, *options):
        output = self.root / "finished"
        with patch("sys.argv", ["finish_chroma", str(source), str(output), *options]):
            with contextlib.redirect_stdout(io.StringIO()):
                finish_chroma.main()
        return output

    def test_native_pixels_whites_and_lossless_alpha(self):
        output = self.finish(self.keyed_source())
        record = verify_pair(output / "candidate.png", output / "candidate.webp")
        self.assertEqual(record["dimensions"], (64, 64))
        with Image.open(output / "candidate.png") as image:
            self.assertEqual(image.getpixel((30, 30)), (255, 255, 255, 255))
            self.assertEqual(image.getpixel((18, 18)), (180, 185, 240, 255))

    def test_refuses_overwriting_an_existing_candidate(self):
        source = self.keyed_source()
        self.finish(source)
        with self.assertRaisesRegex(ValueError, "new output directory"):
            self.finish(source)

    def test_refuses_existing_transparency(self):
        source = self.root / "transparent.png"
        Image.new("RGBA", (64, 64)).save(source)
        with self.assertRaisesRegex(ValueError, "already has transparency"):
            self.finish(source)

    def test_requires_both_sizing_dimensions(self):
        with self.assertRaisesRegex(ValueError, "both width and height"):
            self.finish(self.keyed_source(), "--width", "32")

    def test_rejects_aggressive_noise_cutoff(self):
        with self.assertRaisesRegex(ValueError, "measured key noise"):
            self.finish(self.keyed_source(), "--key-noise-alpha", "0.25")

    def test_detects_changed_webp_pixels(self):
        output = self.finish(self.keyed_source())
        Image.new("RGBA", (64, 64)).save(output / "changed.webp", lossless=True)
        with self.assertRaisesRegex(ValueError, "RGBA differs"):
            verify_pair(output / "candidate.png", output / "changed.webp")

    def test_detects_nontransparent_border(self):
        image = Image.new("RGBA", (64, 64))
        image.putpixel((0, 0), (255, 255, 255, 255))
        image.save(self.root / "border.png")
        image.save(self.root / "border.webp", lossless=True, exact=True)
        with self.assertRaisesRegex(ValueError, "canvas border"):
            verify_pair(self.root / "border.png", self.root / "border.webp")


if __name__ == "__main__":
    unittest.main()
