"""Printify (official REST API) -> publishes to the Etsy/Shopify store connected to the Printify shop."""
from __future__ import annotations

import base64
import json
import math
import time

import httpx

from .. import config, log
from .base import UploadResult, Uploader

_log = log.get("printify")
API = "https://api.printify.com/v1"


def retail_price(cost_cents: int, margin: float) -> int:
    """cost / (1 - margin), rounded up to the next .99."""
    raw = cost_cents / max(0.05, 1 - margin)
    return max(99, math.ceil(raw / 100) * 100 - 1)


def fit_scale(img_w: int, img_h: int, ph_w: int, ph_h: int, margin: float = 0.9) -> float:
    """Printify's `scale` is image width relative to the print area width; keep the whole image inside the area."""
    if not ph_w or not ph_h:
        return margin
    return round(min(1.0, (ph_h * img_w) / (ph_w * img_h)) * margin, 4)


class PrintifyClient:
    def __init__(self, token: str):
        self.http = httpx.Client(base_url=API, timeout=120, headers={
            "Authorization": f"Bearer {token}", "User-Agent": "PTO-Bot/0.1", "Content-Type": "application/json"})

    def _req(self, method: str, path: str, **kw):
        for attempt in range(4):
            r = self.http.request(method, path, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Printify {method} {path} -> {r.status_code}: {r.text[:800]}")
            return r.json() if r.content else {}
        raise RuntimeError(f"Printify {method} {path}: gave up after retries")

    def shops(self):
        return self._req("GET", "/shops.json")

    def blueprints(self):
        return self._req("GET", "/catalog/blueprints.json")

    def providers(self, bid: int):
        return self._req("GET", f"/catalog/blueprints/{bid}/print_providers.json")

    def variants(self, bid: int, ppid: int):
        return self._req("GET", f"/catalog/blueprints/{bid}/print_providers/{ppid}/variants.json")

    def upload_image(self, name: str, data: bytes) -> dict:
        return self._req("POST", "/uploads/images.json", json={"file_name": name, "contents": base64.b64encode(data).decode()})

    def create_product(self, shop: str, body: dict) -> dict:
        return self._req("POST", f"/shops/{shop}/products.json", json=body)

    def get_product(self, shop: str, pid: str) -> dict:
        return self._req("GET", f"/shops/{shop}/products/{pid}.json")

    def update_product(self, shop: str, pid: str, body: dict) -> dict:
        return self._req("PUT", f"/shops/{shop}/products/{pid}.json", json=body)

    def publish(self, shop: str, pid: str) -> dict:
        return self._req("POST", f"/shops/{shop}/products/{pid}/publish.json", json={
            "title": True, "description": True, "images": True, "variants": True, "tags": True,
            "keyFeatures": True, "shipping_template": True})


class PrintifyUploader(Uploader):
    name = "printify"

    def __init__(self, cfg: config.Config | None = None):
        super().__init__(cfg)
        self.pcfg: config.PrintifyCfg = self.cfg.platforms.printify
        token = config.secret("PRINTIFY_TOKEN")
        self.client = PrintifyClient(token) if token else None
        self._cache_path = self.cfg.paths.data_dir / "printify_catalog.json"

    def resolve(self) -> dict[str, dict]:
        """Map each configured product key to blueprint / provider / variant ids (cached on disk)."""
        cache = json.loads(self._cache_path.read_text()) if self._cache_path.exists() else {}
        wanted = {p.key: p for p in self.pcfg.products}
        if all(k in cache for k in wanted):
            return {k: cache[k] for k in wanted}
        bps = self.client.blueprints()
        for key, prod in wanted.items():
            if key in cache:
                continue
            q = prod.blueprint_query.lower()
            matches = [b for b in bps if q in b["title"].lower()]
            if not matches:
                _log.warning("printify: no blueprint matches %r", prod.blueprint_query)
                continue
            bp = sorted(matches, key=lambda b: len(b["title"]))[0]
            provs = self.client.providers(bp["id"])
            if prod.provider_query:
                provs = [p for p in provs if prod.provider_query.lower() in p["title"].lower()] or provs
            if not provs:
                continue
            pp = provs[0]
            variants = self.client.variants(bp["id"], pp["id"]).get("variants", [])
            if prod.colors:
                want = {c.lower() for c in prod.colors}
                variants = [v for v in variants if str(v.get("options", {}).get("color", "")).lower() in want] or variants
            variants = variants[: prod.max_variants]
            ph = next((p for p in (variants[0].get("placeholders") or []) if p.get("position") == "front"),
                      (variants[0].get("placeholders") or [{}])[0]) if variants else {}
            cache[key] = {"blueprint_id": bp["id"], "blueprint": bp["title"], "provider_id": pp["id"], "provider": pp["title"],
                          "variant_ids": [v["id"] for v in variants], "position": ph.get("position", "front"),
                          "ph_w": ph.get("width"), "ph_h": ph.get("height"), "fallback_price": prod.fallback_price_cents}
            _log.info("printify: %s -> %s / %s (%d variants)", key, bp["title"], pp["title"], len(variants))
        self._cache_path.write_text(json.dumps(cache, indent=1))
        return {k: cache[k] for k in wanted if k in cache}

    def upload(self, design: dict, *, dry_run: bool = False) -> UploadResult:
        if not self.client or not self.pcfg.shop_id:
            return UploadResult("failed", error="PRINTIFY_TOKEN (.env) and platforms.printify.shop_id are required")
        path = self.asset_path(design)
        from PIL import Image

        with Image.open(path) as im:
            iw, ih = im.size
        catalog = self.resolve()
        if dry_run:
            return UploadResult("dry_run", error=f"would create {len(catalog)} products: {', '.join(catalog)}")
        img = self.client.upload_image(path.name, path.read_bytes())
        tags = [t for t in design["tags"] if len(t) <= 20][: self.pcfg.max_tags]
        desc = f"{design.get('description') or design['title']}\n\n{self.pcfg.ai_disclosure}".strip()
        shop, urls, errors = self.pcfg.shop_id, [], []
        for key, c in catalog.items():
            try:
                body = {
                    "title": design["title"][:140], "description": desc, "tags": tags,
                    "blueprint_id": c["blueprint_id"], "print_provider_id": c["provider_id"],
                    "variants": [{"id": v, "price": c["fallback_price"], "is_enabled": True} for v in c["variant_ids"]],
                    "print_areas": [{"variant_ids": c["variant_ids"], "placeholders": [{
                        "position": c["position"],
                        "images": [{"id": img["id"], "x": 0.5, "y": 0.5, "scale": fit_scale(iw, ih, c["ph_w"], c["ph_h"]), "angle": 0}],
                    }]}],
                }
                prod = self.client.create_product(shop, body)
                full = self.client.get_product(shop, prod["id"])
                priced = [{"id": v["id"], "price": retail_price(int(v.get("cost") or c["fallback_price"]), self.pcfg.profit_margin),
                           "is_enabled": True} for v in full.get("variants", []) if v.get("is_enabled")]
                if priced:
                    self.client.update_product(shop, prod["id"], {"variants": priced})
                self.client.publish(shop, prod["id"])
                urls.append(f"https://printify.com/app/editor/{prod['id']}")
            except Exception as e:  # noqa: BLE001 - keep going with the other products
                errors.append(f"{key}: {e}")
                _log.warning("printify %s failed: %s", key, e)
        if not urls:
            return UploadResult("failed", error="; ".join(errors)[:1500])
        return UploadResult("success", url=" ".join(urls), error="; ".join(errors)[:1500] or None)
