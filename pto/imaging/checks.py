"""Deterministic quality checks that run before (and independent of) the vision model."""
from __future__ import annotations

import cv2
import imagehash
import numpy as np
from PIL import Image

from .. import config


def sharpness(img: Image.Image) -> float:
    """Variance of the Laplacian over the visible pixels, measured at 1024px."""
    im = img.copy()
    im.thumbnail((1024, 1024), Image.LANCZOS)
    arr = np.array(im.convert("RGBA"))
    gray = cv2.cvtColor(arr[..., :3], cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    mask = arr[..., 3] > 200
    if im.mode == "RGBA" and mask.sum() > 500:
        inner = cv2.erode(mask.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0  # ignore the cut edge itself
        if inner.sum() > 500:
            mask = inner
        return float(lap[mask].var())
    return float(lap.var())


def phash(img: Image.Image) -> str:
    im = img.convert("RGBA")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.getchannel("A"))
    return str(imagehash.phash(bg, hash_size=8))


def nearest_duplicate(h: str, prior: list[str]) -> int | None:
    me = imagehash.hex_to_hash(h)
    dists = [me - imagehash.hex_to_hash(p) for p in prior if p and len(p) == len(h)]
    return min(dists) if dists else None


def sticker(metrics: dict, final: Image.Image, cfg: config.QaCfg, prior_hashes: list[str]) -> tuple[list[str], dict]:
    reasons = []
    if metrics["coverage"] < cfg.min_coverage:
        reasons.append(f"subject too small / missing (coverage {metrics['coverage']:.2f})")
    if metrics["coverage"] > cfg.max_coverage:
        reasons.append(f"background not removed (coverage {metrics['coverage']:.2f})")
    if metrics["border_touch"] > cfg.max_border_touch:
        reasons.append(f"subject cut off by frame (edge touch {metrics['border_touch']:.2f})")
    if metrics["fragments"] > cfg.max_fragments:
        reasons.append(f"messy cutout ({metrics['fragments']} separate pieces)")
    sharp = sharpness(final)
    if sharp < cfg.min_sharpness:
        reasons.append(f"blurry (sharpness {sharp:.0f})")
    h = phash(final)
    dup = nearest_duplicate(h, prior_hashes)
    if dup is not None and dup < cfg.phash_min_distance:
        reasons.append(f"near-duplicate of an earlier design (distance {dup})")
    return reasons, {**metrics, "sharpness": round(sharp, 1), "phash": h, "nearest_dup": dup}


def poster(img: Image.Image, cfg: config.QaCfg, prior_hashes: list[str]) -> tuple[list[str], dict]:
    reasons = []
    sharp = sharpness(img)
    if sharp < cfg.min_sharpness:
        reasons.append(f"blurry (sharpness {sharp:.0f})")
    std = float(np.array(img.convert("L").resize((256, 256))).std())
    if std < 18:
        reasons.append("nearly blank image")
    h = phash(img)
    dup = nearest_duplicate(h, prior_hashes)
    if dup is not None and dup < cfg.phash_min_distance:
        reasons.append(f"near-duplicate (distance {dup})")
    return reasons, {"sharpness": round(sharp, 1), "contrast": round(std, 1), "phash": h, "nearest_dup": dup}
