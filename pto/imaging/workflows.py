"""Builds ComfyUI API-format graphs for each style."""
from __future__ import annotations

from .. import config


def build_prompt(style: config.StyleSpec, subject: str) -> str:
    parts = [style.prefix.strip(), subject.strip().rstrip("."), style.suffix.strip()]
    return ", ".join(p for p in parts if p)


def _base(style: config.StyleSpec, positive: str, negative: str, seed: int) -> tuple[dict, list]:
    g: dict = {"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": style.checkpoint}}}
    model, clip = ["ckpt", 0], ["ckpt", 1]
    for i, lora in enumerate(style.loras):
        nid = f"lora{i}"
        g[nid] = {"class_type": "LoraLoader", "inputs": {
            "model": model, "clip": clip, "lora_name": lora.name,
            "strength_model": lora.strength, "strength_clip": lora.strength}}
        model, clip = [nid, 0], [nid, 1]
    g["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": clip}}
    g["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": clip}}
    g["latent"] = {"class_type": "EmptyLatentImage", "inputs": {"width": style.width, "height": style.height, "batch_size": 1}}
    g["sample"] = {"class_type": "KSampler", "inputs": {
        "model": model, "seed": seed, "steps": style.steps, "cfg": style.cfg, "sampler_name": style.sampler,
        "scheduler": style.scheduler, "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["latent", 0],
        "denoise": 1.0}}
    g["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["ckpt", 2]}}
    g["raw_out"] = {"class_type": "PreviewImage", "inputs": {"images": ["decode", 0]}}
    g["upmodel"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": config.load().comfyui.upscale_model}}
    g["upscale"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["upmodel", 0], "image": ["decode", 0]}}
    return g, ["upscale", 0]


def sticker_graph(style: config.StyleSpec, subject: str, seed: int, *, refine_foreground: bool = True) -> tuple[dict, dict, str]:
    """txt2img -> 4x upscale -> BiRefNet background removal. Outputs: raw (1024 RGB), cut (4096 RGBA)."""
    positive = build_prompt(style, subject)
    g, up = _base(style, positive, style.negative, seed)
    g["rmbg"] = {"class_type": "BiRefNetRMBG", "inputs": {
        "image": up, "model": style.rmbg_model, "mask_blur": 0, "mask_offset": 0, "invert_output": False,
        "refine_foreground": refine_foreground, "background": "Alpha", "background_color": "#222222"}}
    g["cut_out"] = {"class_type": "PreviewImage", "inputs": {"images": ["rmbg", 0]}}
    return g, {"raw": "raw_out", "cut": "cut_out"}, positive


def poster_graph(style: config.StyleSpec, subject: str, seed: int) -> tuple[dict, dict, str]:
    """txt2img (portrait) -> 4x upscale. Outputs: raw, full."""
    positive = build_prompt(style, subject)
    g, up = _base(style, positive, style.negative, seed)
    g["full_out"] = {"class_type": "PreviewImage", "inputs": {"images": up}}
    return g, {"raw": "raw_out", "full": "full_out"}, positive
