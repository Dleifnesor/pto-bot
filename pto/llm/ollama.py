"""Minimal Ollama client: structured JSON chat (text or vision) and model unloading."""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from .. import config, log

_log = log.get("ollama")


def extract_json(text: str) -> Any:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


class Ollama:
    def __init__(self, cfg: config.OllamaCfg | None = None):
        self.cfg = cfg or config.load().ollama
        self._send_think = True

    def _post(self, path: str, payload: dict) -> httpx.Response:
        return httpx.post(self.cfg.url.rstrip("/") + path, json=payload, timeout=self.cfg.timeout_s)

    def models(self) -> list[str]:
        r = httpx.get(self.cfg.url.rstrip("/") + "/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def chat_json(self, system: str, user: str, *, schema: dict | None = None, images: list[bytes] | None = None,
                  model: str | None = None, temperature: float = 0.7, retries: int = 3) -> Any:
        msg: dict = {"role": "user", "content": user}
        if images:
            msg["images"] = [base64.b64encode(b).decode() for b in images]
        payload = {
            "model": model or self.cfg.model,
            "messages": [{"role": "system", "content": system}, msg],
            "stream": False,
            "format": schema or "json",
            "options": {"temperature": temperature, "num_ctx": self.cfg.num_ctx},
            "keep_alive": "20m",
        }
        last_err: Exception | None = None
        for attempt in range(retries):
            if self._send_think:
                payload["think"] = False
            else:
                payload.pop("think", None)
            r = self._post("/api/chat", payload)
            if r.status_code == 400 and "think" in r.text.lower() and self._send_think:
                self._send_think = False  # model doesn't support the think flag
                continue
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "")
            try:
                return extract_json(content)
            except (ValueError, json.JSONDecodeError) as e:
                last_err = e
                _log.warning("LLM returned unparsable JSON (attempt %d): %s", attempt + 1, e)
                payload["options"]["temperature"] = max(0.2, temperature - 0.2 * (attempt + 1))
        raise RuntimeError(f"LLM failed to return valid JSON: {last_err}")

    def unload(self, model: str | None = None) -> None:
        try:
            self._post("/api/generate", {"model": model or self.cfg.model, "keep_alive": 0})
        except httpx.HTTPError as e:
            _log.debug("unload failed: %s", e)
