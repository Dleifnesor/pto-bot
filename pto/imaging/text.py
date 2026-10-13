"""Typesets slogans with real fonts (never misspelled) and merges them into the sticker silhouette."""
from __future__ import annotations

import colorsys
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def pick_fill(art: Image.Image) -> tuple[int, int, int]:
    """A saturated, mid-dark color from the artwork so the text matches it."""
    small = art.copy()
    small.thumbnail((96, 96))
    arr = np.array(small)
    px = arr[arr[..., 3] > 200][:, :3]
    if len(px) < 20:
        return (34, 34, 34)
    pal_img = Image.fromarray(px.reshape(-1, 1, 3).astype(np.uint8), "RGB").quantize(colors=8)
    palette = pal_img.getpalette()[:24]
    counts = pal_img.getcolors() or []
    best, best_score = (34, 34, 34), 0.0
    for count, idx in counts:
        r, g, b = palette[idx * 3: idx * 3 + 3]
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if 0.18 <= l <= 0.6 and s >= 0.35:
            score = count * s
            if score > best_score:
                best, best_score = (r, g, b), score
    return best


def _darken(c: tuple[int, int, int], f: float = 0.25) -> tuple[int, int, int]:
    return tuple(int(v * f) for v in c)  # type: ignore[return-value]


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(path), size)
    try:  # variable fonts (Fredoka, Oswald): use the boldest named instance
        names = f.get_variation_names()
        for want in (b"Bold", b"SemiBold", b"Medium"):
            if want in names:
                f.set_variation_by_name(want)
                break
    except (OSError, AttributeError):
        pass
    return f


def _wrap(words: list[str], font: ImageFont.FreeTypeFont, max_w: int, max_lines: int) -> list[str] | None:
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines or any(font.getlength(l) > max_w for l in lines):
        return None
    return lines


def render_block(text: str, font_path: Path, max_w: int, max_h: int, fill: tuple[int, int, int],
                 max_lines: int = 2) -> Image.Image:
    """Largest font size where `text` fits in max_w x max_h. Fill + white ring + dark outer ring."""
    words = text.upper().split() if len(text) <= 24 else text.split()
    lo, hi, best = 12, max(16, int(max_h * 0.9)), None
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _font(font_path, mid)
        lines = _wrap(words, font, int(max_w * 0.86), max_lines)
        if lines:
            asc, desc = font.getmetrics()
            stroke = int(mid * 0.16)
            total_h = len(lines) * (asc + desc) * 0.92 + 2 * stroke
            if total_h <= max_h:
                best = (mid, lines)
                lo = mid + 1
                continue
        hi = mid - 1
    size, lines = best or (12, [text])
    font = _font(font_path, size)
    asc, desc = font.getmetrics()
    inner, outer = max(2, int(size * 0.09)), max(2, int(size * 0.07))
    pad = inner + outer + 4
    line_h = int((asc + desc) * 0.92)
    width = int(max(font.getlength(l) for l in lines)) + 2 * pad
    height = line_h * len(lines) + 2 * pad + desc // 2
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    dark = _darken(fill) if fill != (34, 34, 34) else (10, 10, 10)
    for i, line in enumerate(lines):
        x = (width - font.getlength(line)) / 2
        y = pad + i * line_h
        d.text((x, y), line, font=font, fill=dark, stroke_width=inner + outer, stroke_fill=dark)
    for i, line in enumerate(lines):
        x = (width - font.getlength(line)) / 2
        y = pad + i * line_h
        d.text((x, y), line, font=font, fill=fill, stroke_width=inner, stroke_fill=(255, 255, 255))
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


def split_balanced(text: str) -> tuple[str, str]:
    words = text.split()
    if len(words) < 2:
        return text, ""
    best_i, best_diff = 1, 1e9
    for i in range(1, len(words)):
        diff = abs(len(" ".join(words[:i])) - len(" ".join(words[i:])))
        if diff < best_diff:
            best_i, best_diff = i, diff
    return " ".join(words[:best_i]), " ".join(words[best_i:])


def compose(art: Image.Image, slogan: str, layout: str, fonts: list[Path], seed: int = 0) -> Image.Image:
    """Place slogan above/below the art, overlapping slightly so the die-cut outline stays one piece."""
    if not slogan or layout == "none" or not fonts:
        return art
    rng = random.Random(seed)
    font_path = rng.choice(fonts)
    fill = pick_fill(art)
    aw, ah = art.size
    max_w = int(max(aw, ah * 0.9))
    if layout == "top_bottom":
        top_text, bottom_text = split_balanced(slogan)
        if not bottom_text:
            layout, top_text = "bottom", ""
    else:
        top_text, bottom_text = (slogan, "") if layout == "top" else ("", slogan)
    blocks_h = int(ah * (0.26 if layout == "top_bottom" else 0.40))
    top = render_block(top_text, font_path, max_w, blocks_h, fill) if top_text else None
    bottom = render_block(bottom_text, font_path, max_w, blocks_h, fill) if bottom_text else None

    overlap = lambda b: int(b.height * 0.18) if b else 0  # noqa: E731
    width = max(aw, *(b.width for b in (top, bottom) if b))
    height = ah + sum(b.height - overlap(b) for b in (top, bottom) if b)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y_art = (top.height - overlap(top)) if top else 0
    canvas.alpha_composite(art, ((width - aw) // 2, y_art))
    if top:
        canvas.alpha_composite(top, ((width - top.width) // 2, 0))
    if bottom:
        canvas.alpha_composite(bottom, ((width - bottom.width) // 2, y_art + ah - overlap(bottom)))
    return canvas
