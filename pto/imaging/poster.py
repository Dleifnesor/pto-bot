"""Displate-style poster output: exact portrait aspect, high-res JPEG."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .. import config


def finalize(img: Image.Image, cfg: config.PosterCfg) -> Image.Image:
    img = img.convert("RGB")
    target = cfg.width / cfg.height
    w, h = img.size
    if w / h > target:  # too wide -> crop sides
        nw = int(h * target)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:  # too tall -> crop top/bottom
        nh = int(w / target)
        img = img.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return img.resize((cfg.width, cfg.height), Image.LANCZOS)


def save(img: Image.Image, path: Path, cfg: config.PosterCfg) -> None:
    img.save(path, "JPEG", quality=cfg.jpeg_quality, subsampling=0, optimize=True)
