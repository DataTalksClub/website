# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow==12.3.0", "numpy==2.5.1", "scipy==1.18.1"]
# ///
"""Remove a magenta outer backdrop from generated illustration sources.

This is estimated chroma matting, not lossless alpha recovery. Original pixels
outside the connected key/fringe stay byte-identical before normalization.
Each theme must supply its own generated source; no masks are reused.

Run with ``uv run --script <this-file> SOURCE OUTPUT_DIRECTORY``.
Native dimensions are preserved unless both --width and --height are supplied.
This tool finishes magenta-keyed watercolor; it does not generate artwork or
remove noise painted into the foreground. Review the candidate before installing.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor
from scipy import ndimage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fringe-chroma", type=float, default=0)
    parser.add_argument("--solid-key-chroma", type=float, default=245)
    parser.add_argument("--fringe-radius", type=float, default=20)
    parser.add_argument("--max-fringe-distance", type=float, default=100)
    parser.add_argument("--key-noise-alpha", type=float, default=0)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    args = parser.parse_args()
    source = Image.open(args.source)
    if source.convert("RGBA").getchannel("A").getextrema()[0] < 255:
        raise ValueError("Source already has transparency; preserve its native alpha")
    if (args.width is None) != (args.height is None):
        raise ValueError("Supply both width and height, or neither for native size")
    if args.width is None:
        args.width, args.height = source.size
    if args.width <= 0 or args.height <= 0 or args.fringe_radius < 0:
        raise ValueError("Width and height must be positive and fringe radius nonnegative")
    if args.max_fringe_distance <= 0 or not 0 <= args.key_noise_alpha <= 0.05:
        raise ValueError("Fringe distance must be positive; measured key noise may be 0–0.05")
    if not 0 <= args.fringe_chroma < args.solid_key_chroma <= 255:
        raise ValueError("Chroma thresholds must satisfy 0 <= fringe < solid <= 255")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Use a new output directory to preserve earlier candidates")
    args.output.mkdir(parents=True, exist_ok=True)
    original = source.convert("RGB")
    rgb = np.array(original)
    c = rgb.astype(float)
    # Magenta has excess red AND blue over green. Green, ink, white and
    # ordinary lavender therefore remain opaque regardless of brightness.
    chroma = np.minimum(c[..., 0], c[..., 2]) - c[..., 1]
    possible_key = chroma > args.fringe_chroma
    seeds = np.zeros(possible_key.shape, dtype=bool)
    seeds[[0, -1], :] = possible_key[[0, -1], :]
    seeds[:, [0, -1]] = possible_key[:, [0, -1]]
    exterior = ndimage.binary_propagation(seeds, mask=possible_key)
    # Generated keys contain isolated pink pinholes a few pixels inside the
    # watercolor edge. Limit their treatment to this measured outer fringe.
    outer_distance = ndimage.distance_transform_edt(~exterior)
    exterior |= possible_key & (outer_distance <= args.fringe_radius)
    solid_key = exterior & (chroma > args.solid_key_chroma)
    if solid_key.sum() < 100:
        raise ValueError("No substantial connected magenta backdrop found")
    foreground = ~exterior
    if not foreground.any():
        raise ValueError("No foreground artwork remains outside the detected key")
    fg_distance = ndimage.distance_transform_edt(exterior)
    # A measured key estimate avoids nearest-neighbor seams across a broad fade.
    border_pixels = np.concatenate([c[0], c[-1], c[:, 0], c[:, -1]])
    b = np.broadcast_to(np.median(border_pixels, axis=0), c.shape)
    # Estimate coverage from magenta excess, with alpha=1 at the foreground
    # boundary. Only connected key/fringe is touched; enclosed whites stay put.
    alpha = np.ones(chroma.shape, dtype=float)
    unknown = exterior & ~solid_key & (fg_distance <= args.max_fringe_distance)
    alpha[exterior] = 0
    key_chroma = float(np.minimum(b[0, 0, 0], b[0, 0, 2]) - b[0, 0, 1])
    if key_chroma <= args.fringe_chroma:
        raise ValueError("Canvas borders are not predominantly magenta key")
    projection = (key_chroma - chroma) / (key_chroma - args.fringe_chroma)
    alpha[unknown] = np.clip(projection[unknown], 0, 1)
    alpha[exterior & (alpha <= args.key_noise_alpha)] = 0
    result = c.copy()
    usable = unknown & (alpha > 1 / 255)
    # Undo the measured key composite only in its fringe. No blur or denoising.
    result[usable] = np.clip(
        (c[usable] - (1 - alpha[usable, None]) * b[usable]) / alpha[usable, None],
        0,
        255,
    )
    result[alpha == 0] = 0
    rgba = np.dstack([np.rint(result).astype(np.uint8), np.rint(alpha * 255).astype(np.uint8)])
    native = Image.fromarray(rgba)
    native.save(args.output / "finished-native.png")
    # Pillow resizes RGBA through premultiplied alpha, avoiding dark/key halos.
    scale = min(args.width / native.width, args.height / native.height)
    scaled_size = (max(1, round(native.width * scale)), max(1, round(native.height * scale)))
    scaled = (
        native
        if scaled_size == native.size
        else native.resize(scaled_size, Image.Resampling.LANCZOS)
    )
    normalized = Image.new("RGBA", (args.width, args.height), (0, 0, 0, 0))
    normalized.paste(scaled, ((args.width - scaled.width) // 2, (args.height - scaled.height) // 2))
    normalized.save(args.output / "candidate.webp", lossless=True, exact=True)
    normalized.save(args.output / "candidate.png")
    normalized.getchannel("A").save(args.output / "alpha.png")
    for label, color in [("cream", "#fdfaf3"), ("navy", "#13162a"), ("white", "#ffffff")]:
        background = Image.new("RGBA", normalized.size, ImageColor.getrgb(color) + (255,))
        background.alpha_composite(normalized)
        background.convert("RGB").save(args.output / f"preview-{label}.png")
    a = np.array(normalized.getchannel("A"))
    roundtrip = np.array(Image.open(args.output / "candidate.webp").convert("RGBA"))
    evidence = {
        "source": str(args.source.resolve()),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "parameters": vars(args) | {"source": str(args.source), "output": str(args.output)},
        "source_size": original.size,
        "output_size": normalized.size,
        "native_opaque_pixels_preserved_exactly": bool(
            np.array_equal(rgba[foreground, :3], rgb[foreground])
        ),
        "native_preserved_pixel_count": int(foreground.sum()),
        "native_partial_alpha_count": int(((rgba[..., 3] > 0) & (rgba[..., 3] < 255)).sum()),
        "output_alpha_extrema": [int(a.min()), int(a.max())],
        "output_transparent_fraction": float((a == 0).mean()),
        "output_partial_alpha_count": int(((a > 0) & (a < 255)).sum()),
        "lossless_webp_matches_normalized_rgba": bool(
            np.array_equal(roundtrip, np.array(normalized))
        ),
        "measured_border_key_rgb": b[0, 0].tolist(),
        "canvas_borders_alpha_max": {
            "top": int(a[0].max()),
            "bottom": int(a[-1].max()),
            "left": int(a[:, 0].max()),
            "right": int(a[:, -1].max()),
        },
        "limitations": (
            "Fringe alpha is estimated from the key composite; source RGB is preserved "
            "outside the connected exterior fringe. Explicit sizing resamples pixels. "
            "Visual review on final surfaces remains required."
        ),
    }
    (args.output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
