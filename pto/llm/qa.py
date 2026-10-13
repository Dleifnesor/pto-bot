"""Vision-model quality check of finished artwork."""
from __future__ import annotations

import io

from PIL import Image

from .. import config, log
from .ollama import Ollama

_log = log.get("qa")

SYSTEM = """You are a strict quality inspector for a print-on-demand shop. You look at one finished artwork and decide
whether it is good enough to sell. Be critical: customers return blurry, malformed or messy products.
Return JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "matches_brief": {"type": "boolean"},
        "anatomy_ok": {"type": "boolean"},
        "clean_edges": {"type": "boolean"},
        "subject_cut_off": {"type": "boolean"},
        "unwanted_text_or_gibberish": {"type": "boolean"},
        "slogan_correct": {"type": "boolean"},
        "watermark_or_signature": {"type": "boolean"},
        "logo_brand_or_real_person": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "matches_brief", "anatomy_ok", "clean_edges", "subject_cut_off", "unwanted_text_or_gibberish",
                 "slogan_correct", "watermark_or_signature", "logo_brand_or_real_person", "issues"],
}


def preview_bytes(img: Image.Image, kind: str, size: int = 896) -> bytes:
    im = img.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (128, 128, 128))  # neutral gray so white AND black edges are visible
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def inspect(img: Image.Image, design: dict, kind: str, cfg: config.Config | None = None, llm: Ollama | None = None) -> dict:
    cfg = cfg or config.load()
    llm = llm or Ollama(cfg.ollama)
    slogan = design.get("slogan") or ""
    if kind == "poster":
        what = "a full-background portrait wall poster"
        edge_rule = "clean_edges: true if the composition fills the frame without awkward empty areas or borders."
    else:
        what = "a die-cut sticker (shown on a neutral gray background; the gray is NOT part of the art)"
        edge_rule = ("clean_edges: true if the cutout edge is clean, with no leftover white/colored background, "
                     "halo, stray specks or chopped-off parts.")
    text_rule = (f'The ONLY intended text is the slogan "{slogan}". slogan_correct: true if it is present, spelled '
                 "exactly, and readable." if slogan else
                 "No text is intended. slogan_correct: true. Any letters or gibberish glyphs = unwanted_text_or_gibberish true.")
    license_rule = (f"\nThis is licensed fan art of {design['license']}: its characters are expected. "
                    "logo_brand_or_real_person is only for logos, wordmarks and real people." if design.get("license") else "")
    user = f"""This is {what}.
Brief: style "{design.get('style')}", subject: {design.get('subject')}{license_rule}
{text_rule}
{edge_rule}
anatomy_ok: false if faces, eyes, hands, paws or limbs are malformed, duplicated or melted.
subject_cut_off: true if part of the main subject is cut off by the image boundary.
score: 1-10 overall commercial quality (7 = good enough to sell, 9+ = excellent).
issues: short list of concrete problems (empty if none)."""
    res = llm.chat_json(SYSTEM, user, schema=SCHEMA, images=[preview_bytes(img, kind)], model=cfg.ollama.vision_model,
                        temperature=0.1)
    res["score"] = int(res.get("score") or 0)
    reasons = []
    if res["score"] < cfg.qa.min_score:
        reasons.append(f"score {res['score']} < {cfg.qa.min_score}")
    for key, bad in (("anatomy_ok", False), ("matches_brief", False), ("watermark_or_signature", True),
                     ("logo_brand_or_real_person", True), ("unwanted_text_or_gibberish", True), ("slogan_correct", False)):
        if res.get(key) is bad:
            reasons.append(key.replace("_", " ") + (" = no" if bad is False else ""))
    if kind == "sticker":
        if res.get("clean_edges") is False:
            reasons.append("edges not clean")
        if res.get("subject_cut_off") is True:
            reasons.append("subject cut off")
    res["passed"] = not reasons
    res["fail_reasons"] = reasons
    return res
