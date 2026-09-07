"""Remove a magenta outer backdrop from square generated illustration sources.

This is estimated chroma matting, not lossless alpha recovery. Original pixels
outside the connected key/fringe stay byte-identical before normalization.
Each theme must supply its own generated source; no masks are reused.

Run through uv with transient pillow, numpy and scipy dependencies. See
_docs/design/course-illustrations.md for the accepted parameters and review gates.
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
    parser.add_argument("--fringe-chroma", type=float, default=12)
    parser.add_argument("--solid-key-chroma", type=float, default=100)
    parser.add_argument("--fringe-radius", type=float, default=20)
    parser.add_argument("--size", type=int, default=1024)
    args = parser.parse_args()
    source = Image.open(args.source)
    if source.width != source.height:
        raise ValueError("Use a square source; this helper must not stretch artwork")
    if source.convert("RGBA").getchannel("A").getextrema()[0] < 255:
        raise ValueError("Source already has transparency; preserve its native alpha")
    if args.size <= 0 or args.fringe_radius < 0:
        raise ValueError("Size must be positive and fringe radius nonnegative")
    if not 0 <= args.fringe_chroma < args.solid_key_chroma <= 255:
        raise ValueError("Chroma thresholds must satisfy 0 <= fringe < solid <= 255")
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
    fg_distance, fg_index = ndimage.distance_transform_edt(exterior, return_indices=True)
    _, bg_index = ndimage.distance_transform_edt(~solid_key, return_indices=True)
    f = c[tuple(fg_index)]
    b = c[tuple(bg_index)]
    direction = f - b
    # Project each fringe pixel onto its nearest local backdrop/paint colors.
    # This estimates coverage without bleaching green/white subject pixels.
    alpha = np.ones(chroma.shape, dtype=float)
    unknown = exterior & ~solid_key & (fg_distance <= 8)
    alpha[exterior] = 0
    projection = np.sum((c - b) * direction, axis=2) / np.maximum(
        np.sum(direction * direction, axis=2), 1
    )
    alpha[unknown] = np.clip(projection[unknown], 0, 1)
    result = c.copy()
    usable = unknown & (alpha > 1 / 255)
    # Local paint color is a safer edge estimate than inverse unmixing, which
    # amplifies generated magenta noise at low coverage. Only keyed fringe
    # pixels receive this estimate; all other source paint is unchanged.
    result[usable] = f[usable]
    result[alpha == 0] = 0
    rgba = np.dstack([np.rint(result).astype(np.uint8), np.rint(alpha * 255).astype(np.uint8)])
    native = Image.fromarray(rgba, "RGBA")
    native.save(args.output / "finished-native.png")
    # Pillow resizes RGBA through premultiplied alpha, avoiding dark/key halos.
    normalized = native.resize((args.size, args.size), Image.Resampling.LANCZOS)
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
        "white_interior_probe_alpha": {
            str(xy): int(rgba[xy[1], xy[0], 3])
            for xy in [(600, 350), (300, 880)]
            if xy[0] < rgb.shape[1] and xy[1] < rgb.shape[0]
        },
        "limitations": "Fringe alpha is estimated from the key composite; source RGB is preserved outside the connected exterior fringe. Sizing resamples pixels. Visual review on final surfaces remains required.",
    }
    (args.output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
