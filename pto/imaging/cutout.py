"""Turns a background-removed RGBA render into a clean, tightly cropped, print-size sticker PNG."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from .. import config


def border_touch(alpha: np.ndarray, thresh: int = 128) -> float:
    """Fraction of the image's outer edge pixels that are opaque (high = subject is cut off by the frame)."""
    edge = np.concatenate([alpha[0, :], alpha[-1, :], alpha[:, 0], alpha[:, -1]])
    return float((edge > thresh).mean())


def coverage(alpha: np.ndarray, thresh: int = 128) -> float:
    return float((alpha > thresh).mean())


def clean_alpha(alpha: np.ndarray, cfg: config.CutoutCfg) -> tuple[np.ndarray, dict]:
    """Remove specks, fill pinholes, and tighten the matte edge. Returns (alpha uint8, metrics)."""
    a = alpha.astype(np.float32) / 255.0
    hard = (a > 0.5).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(hard, connectivity=8)
    if n <= 1:
        return np.zeros_like(alpha), {"fragments": 0, "removed_specks": 0, "filled_holes": 0}
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep_ids = 1 + np.flatnonzero(areas >= cfg.min_island_frac * areas.max())
    keep = np.isin(labels, keep_ids)
    subject_area = int(areas[keep_ids - 1].sum())

    # fill small enclosed holes (BiRefNet sometimes punches pinholes in light areas)
    inv = (~keep).astype(np.uint8)
    hn, hl, hs, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    h, w = alpha.shape
    filled = np.zeros_like(keep)
    holes = 0
    for i in range(1, hn):
        x, y, bw, bh, area = hs[i]
        touches = x == 0 or y == 0 or x + bw == w or y + bh == h
        if not touches and area < cfg.max_hole_frac * subject_area:
            filled |= hl == i
            holes += 1

    region = cv2.dilate(keep.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    a = np.where(region, a, 0.0)
    a[filled] = 1.0
    s = max(1e-3, cfg.edge_softness)
    lo, hi = 0.5 - s / 2, 0.5 + s / 2
    t = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    a = t * t * (3 - 2 * t)
    return (a * 255).round().astype(np.uint8), {
        "fragments": int(len(keep_ids)), "removed_specks": int(n - 1 - len(keep_ids)), "filled_holes": holes,
    }


def trim(img: Image.Image, pad: int) -> Image.Image:
    bbox = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        return img
    cropped = img.crop(bbox)
    out = Image.new("RGBA", (cropped.width + 2 * pad, cropped.height + 2 * pad), (0, 0, 0, 0))
    out.paste(cropped, (pad, pad))
    return out


def resize_long_side(img: Image.Image, target: int) -> Image.Image:
    scale = target / max(img.size)
    if abs(scale - 1) < 1e-3:
        return img
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    # resize premultiplied so transparent pixels don't bleed dark fringes into the edge
    return img.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def decontaminate_transparent(img: Image.Image) -> Image.Image:
    """Zero the RGB of fully transparent pixels (smaller files, no hidden background colors)."""
    arr = np.array(img)
    arr[arr[..., 3] == 0, :3] = 0
    return Image.fromarray(arr, "RGBA")


def prepare(cut: Image.Image, cfg: config.CutoutCfg) -> tuple[Image.Image, dict]:
    """Cleaned + trimmed art at working resolution (before any slogan is added)."""
    cut = cut.convert("RGBA")
    raw_alpha = np.array(cut.getchannel("A"))
    metrics = {"coverage": coverage(raw_alpha), "border_touch": border_touch(raw_alpha)}
    alpha, m = clean_alpha(raw_alpha, cfg)
    metrics.update(m)
    cut.putalpha(Image.fromarray(alpha, "L"))
    return trim(cut, 0), metrics


def finalize(art: Image.Image, cfg: config.CutoutCfg) -> Image.Image:
    """Pad, scale to print size and clean hidden pixels."""
    out = trim(art, 0)
    out = resize_long_side(out, cfg.target_long_side - 2 * cfg.padding_px)
    padded = Image.new("RGBA", (out.width + 2 * cfg.padding_px, out.height + 2 * cfg.padding_px), (0, 0, 0, 0))
    padded.paste(out, (cfg.padding_px, cfg.padding_px))
    return decontaminate_transparent(padded)
