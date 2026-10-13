"""ComfyUI HTTP API client and a process manager that starts/stops the portable install headlessly."""
from __future__ import annotations

import io
import subprocess
import time
import uuid
from urllib.parse import urlparse

import httpx
import psutil
from PIL import Image

from .. import config, log

_log = log.get("comfy")


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, cfg: config.ComfyCfg | None = None):
        self.cfg = cfg or config.load().comfyui
        self.base = self.cfg.url.rstrip("/")
        self.client_id = uuid.uuid4().hex

    def alive(self) -> bool:
        try:
            return httpx.get(f"{self.base}/system_stats", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def node_types(self) -> set[str]:
        r = httpx.get(f"{self.base}/object_info", timeout=60)
        r.raise_for_status()
        return set(r.json())

    def models(self, folder: str) -> list[str]:
        r = httpx.get(f"{self.base}/models/{folder}", timeout=20)
        r.raise_for_status()
        return r.json()

    def queue(self, graph: dict) -> str:
        r = httpx.post(f"{self.base}/prompt", json={"prompt": graph, "client_id": self.client_id}, timeout=30)
        if r.status_code != 200:
            raise ComfyError(f"ComfyUI rejected workflow ({r.status_code}): {r.text[:1500]}")
        return r.json()["prompt_id"]

    def wait(self, prompt_id: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = httpx.get(f"{self.base}/history/{prompt_id}", timeout=30)
            if r.status_code == 200 and prompt_id in r.json():
                entry = r.json()[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    detail = msgs[0][1].get("exception_message", "") if msgs else str(status)[:800]
                    raise ComfyError(f"ComfyUI execution error: {detail}")
                if status.get("completed", True) and entry.get("outputs"):
                    return entry["outputs"]
            time.sleep(1.0)
        raise ComfyError(f"ComfyUI job {prompt_id} timed out after {timeout:.0f}s")

    def fetch(self, info: dict) -> Image.Image:
        r = httpx.get(f"{self.base}/view", params={
            "filename": info["filename"], "subfolder": info.get("subfolder", ""), "type": info.get("type", "temp"),
        }, timeout=120)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        im.load()
        return im

    def run(self, graph: dict, outputs: dict[str, str], timeout: float | None = None) -> dict[str, Image.Image]:
        """Queue `graph` and return {label: first image} for each {label: node_id} in `outputs`."""
        pid = self.queue(graph)
        res = self.wait(pid, timeout or self.cfg.job_timeout_s)
        images = {}
        for label, node_id in outputs.items():
            imgs = res.get(node_id, {}).get("images") or []
            if not imgs:
                raise ComfyError(f"ComfyUI produced no image for '{label}' (node {node_id})")
            images[label] = self.fetch(imgs[0])
        return images

    def free(self) -> None:
        try:
            httpx.post(f"{self.base}/free", json={"unload_models": True, "free_memory": True}, timeout=30)
        except httpx.HTTPError as e:
            _log.debug("free failed: %s", e)


class ComfyProcess:
    """Starts ComfyUI from the portable folder (no console window, no browser tab) when it isn't already running."""

    def __init__(self, cfg: config.Config | None = None):
        self.cfg = cfg or config.load()
        self.client = ComfyClient(self.cfg.comfyui)
        self.proc: subprocess.Popen | None = None

    @property
    def started_by_us(self) -> bool:
        return self.proc is not None

    def ensure_running(self) -> None:
        if self.client.alive():
            return
        if not self.cfg.comfyui.autostart:
            raise ComfyError(f"ComfyUI is not running at {self.cfg.comfyui.url} and autostart is off")
        root = self.cfg.paths.comfyui_dir
        py = root / "python_embeded" / "python.exe"
        port = urlparse(self.cfg.comfyui.url).port or 8188
        args = [str(py), "-s", str(root / "ComfyUI" / "main.py"), "--windows-standalone-build", "--disable-auto-launch",
                "--listen", "127.0.0.1", "--port", str(port)]
        logf = (self.cfg.paths.logs / "comfyui.log").open("ab")
        _log.info("starting ComfyUI: %s", " ".join(args))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.proc = subprocess.Popen(args, cwd=str(root), stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)
        deadline = time.monotonic() + self.cfg.comfyui.startup_timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise ComfyError(f"ComfyUI exited during startup (code {self.proc.returncode}); see logs/comfyui.log")
            if self.client.alive():
                _log.info("ComfyUI is up")
                return
            time.sleep(2)
        self.stop()
        raise ComfyError("ComfyUI did not come up in time; see logs/comfyui.log")

    def stop(self) -> None:
        if not self.proc:
            return
        _log.info("stopping ComfyUI (pid %s)", self.proc.pid)
        try:
            parent = psutil.Process(self.proc.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        self.proc = None
