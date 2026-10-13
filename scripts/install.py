"""One-click installer, run by install.bat inside the project venv.

Sets up fonts, .env, data/ComfyUI paths, ComfyUI portable (matching your GPU) with the BiRefNet node, the SDXL
checkpoints / LoRAs / upscaler named in config.yaml, Ollama and its model, the Chrome extension config and a desktop
shortcut, then runs `pto doctor`. Anything already present is skipped, so re-running it is also a repair.

    install.bat [--yes] [--skip-downloads] [--no-shortcut]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import yaml  # noqa: E402

from pto import config  # noqa: E402

UA = {"User-Agent": "pto-installer/0.1"}
COMFY_RELEASE = "https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest"
RMBG_ZIP = "https://github.com/1038lab/ComfyUI-RMBG/archive/refs/heads/main.zip"
OLLAMA_DOWNLOAD = "https://ollama.com/download/windows"
HF = "https://huggingface.co/"
# Where each model named in config.yaml comes from: (models subfolder, file name) -> URL
MODEL_URLS = {
    ("checkpoints", "DreamShaperXL_Lightning.safetensors"):
        HF + "Lykon/dreamshaper-xl-lightning/resolve/main/DreamShaperXL_Lightning.safetensors",
    ("checkpoints", "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"):
        HF + "RunDiffusion/Juggernaut-XL-v9/resolve/main/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
    ("loras", "sdxl_lightning_8step_lora.safetensors"):
        HF + "ByteDance/SDXL-Lightning/resolve/main/sdxl_lightning_8step_lora.safetensors",
    ("loras", "CuteCartoonRedmond-CuteCartoon-CuteCartoonAF.safetensors"):
        HF + "artificialguybr/CuteCartoonRedmond-V2/resolve/main/CuteCartoonRedmond-CuteCartoon-CuteCartoonAF.safetensors",
    ("loras", "StickersRedmond.safetensors"):
        HF + "artificialguybr/StickersRedmond/resolve/main/StickersRedmond.safetensors",
    ("loras", "LogoRedmondV2-Logo-LogoRedmAF.safetensors"):
        HF + "artificialguybr/LogoRedmond-LogoLoraForSDXL-V2/resolve/main/LogoRedmondV2-Logo-LogoRedmAF.safetensors",
    ("upscale_models", "RealESRGAN_x4plus.pth"):
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
}
GB = 1024 ** 3


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"\n== {msg}")


# ------------------------------------------------------------------------------------------------ paths

def _set_path(text: str, key: str, value: str) -> str:
    return re.sub(rf"^(\s+{key}:[ \t]*)[^#\n]*?([ \t]*(#.*)?)$", lambda m: f"{m.group(1)}{value}{m.group(2)}",
                  text, count=1, flags=re.M)


def fix_paths(text: str, root: Path, drive_exists=lambda p: Path(p.anchor).exists()) -> tuple[str, list[str]]:
    """Point data_dir / comfyui_dir inside the project when their drive doesn't exist on this PC (e.g. no E:)."""
    cfg = config.Config.model_validate(yaml.safe_load(text) or {})
    changes = []
    for key, current, fallback in (("data_dir", cfg.paths.data_dir, root / "data"),
                                   ("comfyui_dir", cfg.paths.comfyui_dir, root / "ComfyUI_windows_portable")):
        if not drive_exists(Path(current)):
            text = _set_path(text, key, fallback.as_posix())
            changes.append(f"{key}: {Path(current).as_posix()} -> {fallback.as_posix()} (that drive doesn't exist here)")
    return text, changes


# ------------------------------------------------------------------------------------------------ detection

def pick_vendor(gpu_names: list[str]) -> str:
    """ComfyUI portable flavor for these GPUs; a discrete card wins over integrated graphics."""
    names = " | ".join(gpu_names).lower()
    if "nvidia" in names:
        return "nvidia"
    if "amd" in names or "radeon" in names:
        return "amd"
    if "arc" in names:
        return "intel"
    return "nvidia"


def gpu_names() -> list[str]:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_VideoController).Name"], capture_output=True, text=True, timeout=30)
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def needed_models(cfg: config.Config) -> list[tuple[str, str]]:
    want = []
    for s in cfg.styles.values():
        want.append(("checkpoints", s.checkpoint))
        want += [("loras", lo.name) for lo in s.loras]
    want.append(("upscale_models", cfg.comfyui.upscale_model))
    return list(dict.fromkeys(want))


def model_present(cfg: config.Config, folder: str, name: str) -> bool:
    return any((base / folder / name).exists()
               for base in (cfg.paths.models, cfg.paths.comfyui_dir / "ComfyUI" / "models"))


def rmbg_dir(cfg: config.Config) -> Path | None:
    nodes = cfg.paths.comfyui_dir / "ComfyUI" / "custom_nodes"
    return next((p for p in nodes.glob("*") if p.is_dir() and p.name.lower().startswith("comfyui-rmbg")), None) \
        if nodes.exists() else None


def ollama_exe() -> str | None:
    found = shutil.which("ollama")
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return found or (str(local) if local.exists() else None)


def ollama_models(cfg: config.Config) -> list[str] | None:
    try:
        r = httpx.get(cfg.ollama.url.rstrip("/") + "/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ downloads

def head_size(c: httpx.Client, url: str) -> int:
    try:
        h = c.head(url)
        return int(h.headers.get("x-linked-size") or h.headers.get("content-length") or 0)
    except (httpx.HTTPError, ValueError):
        return 0


def download(c: httpx.Client, url: str, dest: Path, label: str) -> Path:
    """Stream to dest (resuming a previous .part), with a progress line."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with c.stream("GET", url, headers=headers) as r:
        if r.status_code == 416:  # already complete
            part.replace(dest)
            return dest
        r.raise_for_status()
        if have and r.status_code != 206:  # server ignored the range: start over
            have = 0
        total = have + int(r.headers.get("content-length") or 0)
        done, last = have, 0.0
        with open(part, "ab" if have else "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.monotonic() - last > 1:
                    last = time.monotonic()
                    pct = f"{done / total:6.1%}" if total else ""
                    print(f"\r   {label}: {done / GB:5.2f} / {total / GB:5.2f} GB {pct}   ", end="", flush=True)
    print(f"\r   {label}: done ({done / GB:.2f} GB)                    ", flush=True)
    part.replace(dest)
    return dest


def _sevenzip() -> str | None:
    return next((p for p in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe") if p and Path(p).exists()), None)


def extract_7z(archive: Path, dest: Path) -> None:
    """ComfyUI's archive uses LZMA2 + BCJ2: 7-Zip or Windows' own bsdtar can unpack it (py7zr can't)."""
    if sevenzip := _sevenzip():
        subprocess.run([sevenzip, "x", "-y", f"-o{dest}", str(archive)], check=True, stdout=subprocess.DEVNULL)
        return
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if tar.exists() and subprocess.run([str(tar), "-xf", str(archive), "-C", str(dest)]).returncode == 0:
        return
    if shutil.which("winget"):
        say("   installing 7-Zip with winget to unpack it...")
        subprocess.run(["winget", "install", "-e", "--id", "7zip.7zip"], check=False)
        if sevenzip := _sevenzip():
            subprocess.run([sevenzip, "x", "-y", f"-o{dest}", str(archive)], check=True, stdout=subprocess.DEVNULL)
            return
    raise RuntimeError(f"couldn't unpack {archive.name}: install 7-Zip (https://www.7-zip.org) and run install.bat again")


# ------------------------------------------------------------------------------------------------ install steps

def install_comfyui(c: httpx.Client, cfg: config.Config, asset: dict) -> None:
    target = cfg.paths.comfyui_dir
    archive = download(c, asset["browser_download_url"], cfg.paths.data_dir / "downloads" / asset["name"], asset["name"])
    say("   extracting ComfyUI...")
    extract_7z(archive, target.parent)
    unpacked = target.parent / "ComfyUI_windows_portable"
    if unpacked != target and unpacked.exists() and not target.exists():
        unpacked.rename(target)
    archive.unlink(missing_ok=True)
    if not (target / "python_embeded" / "python.exe").exists():
        raise RuntimeError(f"ComfyUI didn't end up in {target}")


def install_rmbg(c: httpx.Client, cfg: config.Config) -> None:
    nodes = cfg.paths.comfyui_dir / "ComfyUI" / "custom_nodes"
    zpath = download(c, RMBG_ZIP, cfg.paths.data_dir / "downloads" / "ComfyUI-RMBG.zip", "ComfyUI-RMBG")
    with zipfile.ZipFile(zpath) as z:
        top = z.namelist()[0].split("/")[0]
        z.extractall(nodes)
    (nodes / top).rename(nodes / "comfyui-rmbg")
    zpath.unlink(missing_ok=True)
    py = cfg.paths.comfyui_dir / "python_embeded" / "python.exe"
    say("   installing the node's Python packages into ComfyUI...")
    subprocess.run([str(py), "-s", "-m", "pip", "install", "-q", "-r", str(nodes / "comfyui-rmbg" / "requirements.txt")],
                   check=True)


def install_ollama() -> str | None:
    if shutil.which("winget"):
        say("   installing Ollama with winget (accept its prompts if it asks)...")
        subprocess.run(["winget", "install", "-e", "--id", "Ollama.Ollama"], check=False)
    exe = ollama_exe()
    if not exe:
        say(f"   Couldn't install Ollama automatically. Get it from {OLLAMA_DOWNLOAD}, then run install.bat again.")
    return exe


def ensure_ollama_running(exe: str, cfg: config.Config) -> bool:
    if ollama_models(cfg) is not None:
        return True
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    for _ in range(30):
        time.sleep(1)
        if ollama_models(cfg) is not None:
            return True
    return False


def make_shortcut() -> None:
    target = ROOT / "start.bat"
    ps = ("$d = [Environment]::GetFolderPath('Desktop'); $s = (New-Object -ComObject WScript.Shell)"
          f".CreateShortcut(\"$d\\PTO Bot.lnk\"); $s.TargetPath = '{target}'; $s.WorkingDirectory = '{ROOT}'; "
          "$s.IconLocation = 'shell32.dll,13'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)


# ------------------------------------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install or repair everything PTO needs.")
    ap.add_argument("--yes", action="store_true", help="don't ask before downloading")
    ap.add_argument("--skip-downloads", action="store_true", help="only the local setup; no ComfyUI/models/Ollama")
    ap.add_argument("--no-shortcut", action="store_true", help="don't create the desktop shortcut")
    args = ap.parse_args(argv)

    step("Fonts")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "get_fonts.py")], check=False)

    step("Settings")
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists() and example.exists():
        shutil.copy(example, env)
        say("   created .env (optional API keys go there)")
    text = config.CONFIG_PATH.read_text(encoding="utf-8")
    text, changes = fix_paths(text, ROOT)
    if changes:
        config.CONFIG_PATH.write_text(text, encoding="utf-8")
    for ch in changes:
        say(f"   {ch}")
    cfg = config.load(reload=True)
    cfg.ensure_dirs()
    say(f"   data folder: {cfg.paths.data_dir}\n   ComfyUI:     {cfg.paths.comfyui_dir}")

    c = httpx.Client(timeout=httpx.Timeout(60, read=300), headers=UA, follow_redirects=True)
    step("Checking what's missing")
    todo: list[tuple[str, int, object]] = []  # (label, bytes, action)
    if not (cfg.paths.comfyui_dir / "python_embeded" / "python.exe").exists():
        vendor = pick_vendor(gpu_names())
        assets = {a["name"]: a for a in c.get(COMFY_RELEASE).json().get("assets", [])}
        asset = assets.get(f"ComfyUI_windows_portable_{vendor}.7z") or assets["ComfyUI_windows_portable_nvidia.7z"]
        todo.append((f"ComfyUI portable ({vendor} GPU build)", asset["size"],
                     lambda a=asset: install_comfyui(c, cfg, a)))
    if rmbg_dir(cfg) is None:
        todo.append(("ComfyUI-RMBG node (BiRefNet background removal)", 20 << 20, lambda: install_rmbg(c, cfg)))
    for folder, name in needed_models(cfg):
        if model_present(cfg, folder, name):
            continue
        url = MODEL_URLS.get((folder, name))
        if not url:
            say(f"   !! no download source known for {folder}/{name}: put it in {cfg.paths.models / folder} yourself")
            continue
        dest = cfg.paths.models / folder / name
        todo.append((f"{folder}/{name}", head_size(c, url), lambda u=url, d=dest, n=name: download(c, u, d, n)))
    exe = ollama_exe()
    if not exe:
        todo.append(("Ollama (local LLM runtime)", 1 * GB, None))
    elif not ensure_ollama_running(exe, cfg):  # needed to see which models it already has
        say("   !! Ollama is installed but didn't start; open the Ollama app and run install.bat again.")
    have = ollama_models(cfg) or []
    pulls = [m for m in dict.fromkeys([cfg.ollama.model, cfg.ollama.vision_model]) if m not in have]
    for m in pulls:
        todo.append((f"Ollama model {m}", 6 * GB, None))

    if not todo:
        say("   nothing missing")
    else:
        for label, size, _ in todo:
            say(f"   - {label}" + (f"  (~{size / GB:.1f} GB)" if size >= GB / 10 else f"  (~{size >> 20} MB)" if size else ""))
        say(f"   total download: ~{sum(s for _, s, _ in todo) / GB:.0f} GB")
    go = bool(todo) and not args.skip_downloads
    if go and not args.yes:
        go = input("\nDownload and install these now? [Y/n] ").strip().lower() in ("", "y", "yes")
    failed = []
    if go:
        for label, _, action in todo:
            if action is None:
                continue
            step(f"Installing {label}")
            try:
                action()
            except Exception as e:  # noqa: BLE001 - keep going, report at the end
                failed.append(label)
                say(f"   FAILED: {e}")
        if pulls or not exe:
            step("Ollama")
            exe = exe or install_ollama()
            if exe and ensure_ollama_running(exe, cfg):
                for m in pulls:
                    subprocess.run([exe, "pull", m], check=False)
            elif exe:
                failed.append("Ollama server")
                say("   Ollama is installed but didn't start; open the Ollama app and run install.bat again.")
    elif todo:
        say("   skipped downloads")

    step("ComfyUI model folder")
    if (cfg.paths.comfyui_dir / "ComfyUI").exists():
        subprocess.run([sys.executable, "-m", "pto", "setup-comfy"], cwd=ROOT, check=False)

    step("Chrome extension")
    from pto import extension_setup

    say(f"   config written to {extension_setup.write(cfg)}")
    if not args.no_shortcut:
        make_shortcut()
        say("   desktop shortcut 'PTO Bot' -> start.bat")

    step("Health check (pto doctor)")
    subprocess.run([sys.executable, "-m", "pto", "doctor"], cwd=ROOT, check=False)
    say("\nNext: double-click 'PTO Bot' on your desktop (or start.bat), then load the Chrome extension:")
    say(f"   chrome://extensions -> Developer mode -> Load unpacked -> {ROOT / 'extension'}")
    say("   See README.md -> 'Going live on Redbubble' for the rest.")
    if failed:
        say(f"\nSome steps failed: {', '.join(failed)}. Run install.bat again to retry them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
