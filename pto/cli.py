"""Command line: python -m pto <command>  (or .\\pto.ps1 <command>)"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import config, db as dbmod, log

EXTRA_PATHS_KEY = "pto_models"


def _setup_logging() -> config.Config:
    cfg = config.load()
    cfg.ensure_dirs()
    log.setup(cfg.paths.logs)
    return cfg


def cmd_serve(args) -> None:
    import uvicorn

    cfg = _setup_logging()
    from . import extension_setup, scheduler
    from .web.app import app

    extension_setup.write(cfg)  # keep the extension's token/config in sync
    if not args.no_scheduler:
        scheduler.start()
    print(f"Dashboard: http://{cfg.web.host}:{cfg.web.port}/")
    try:
        uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="warning")
    finally:
        scheduler.shutdown()


def cmd_setup_comfy(args) -> None:
    cfg = _setup_logging()
    target = cfg.paths.comfyui_dir / "ComfyUI" / "extra_model_paths.yaml"
    models = cfg.paths.models.as_posix()
    block = (f"{EXTRA_PATHS_KEY}:\n  base_path: {models}\n  checkpoints: checkpoints\n  loras: loras\n"
             f"  upscale_models: upscale_models\n")
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if EXTRA_PATHS_KEY in existing:
        print(f"already configured: {target}")
        return
    for sub in ("checkpoints", "loras", "upscale_models"):
        (cfg.paths.models / sub).mkdir(parents=True, exist_ok=True)
    target.write_text((existing.rstrip() + "\n\n" if existing.strip() else "") + block, encoding="utf-8")
    print(f"wrote {target}\n{block}")


def _model_present(cfg: config.Config, folder: str, name: str) -> bool:
    for base in (cfg.paths.models / folder, cfg.paths.comfyui_dir / "ComfyUI" / "models" / folder):
        if (base / name).exists():
            return True
    return False


def cmd_doctor(args) -> None:
    cfg = _setup_logging()
    ok = True

    def line(good: bool, msg: str, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(("  OK   " if good else "  FAIL ") + msg + (f"\n         -> {hint}" if hint and not good else ""))

    print("Paths")
    free = shutil.disk_usage(cfg.paths.data_dir).free / 1e9
    line(free > 10, f"data dir {cfg.paths.data_dir} ({free:.0f} GB free)", "free up space on the data drive")
    fonts = [f for s in cfg.styles.values() for f in s.fonts]
    missing_fonts = sorted({f for f in fonts if not (cfg.paths.fonts_dir / f).exists()})
    line(not missing_fonts, f"fonts ({len(set(fonts)) - len(missing_fonts)}/{len(set(fonts))})", f"missing {missing_fonts}; run scripts/get_fonts.py")
    line((cfg.paths.comfyui_dir / "python_embeded" / "python.exe").exists(), f"ComfyUI portable at {cfg.paths.comfyui_dir}")
    extra = cfg.paths.comfyui_dir / "ComfyUI" / "extra_model_paths.yaml"
    line(extra.exists() and EXTRA_PATHS_KEY in extra.read_text(encoding="utf-8"), "ComfyUI sees the E: model folder", "run: pto setup-comfy")

    print("Models")
    for name, s in cfg.styles.items():
        line(_model_present(cfg, "checkpoints", s.checkpoint), f"[{name}] checkpoint {s.checkpoint}")
        for lo in s.loras:
            line(_model_present(cfg, "loras", lo.name), f"[{name}] lora {lo.name}")
    line(_model_present(cfg, "upscale_models", cfg.comfyui.upscale_model), f"upscaler {cfg.comfyui.upscale_model}")
    rmbg_nodes = cfg.paths.comfyui_dir / "ComfyUI" / "custom_nodes" / "comfyui-rmbg"
    line(rmbg_nodes.exists(), "comfyui-rmbg custom node (BiRefNet)", "install ComfyUI-RMBG via ComfyUI-Manager")

    print("Services")
    from .imaging.comfy import ComfyClient
    from .llm.ollama import Ollama

    try:
        models = Ollama(cfg.ollama).models()
        line(True, f"Ollama at {cfg.ollama.url}")
        for m in {cfg.ollama.model, cfg.ollama.vision_model}:
            line(m in models, f"Ollama model {m}", f"run: ollama pull {m}")
    except Exception as e:  # noqa: BLE001
        line(False, f"Ollama at {cfg.ollama.url}: {e}", "start Ollama")
    cc = ComfyClient(cfg.comfyui)
    if cc.alive():
        nodes = cc.node_types()
        for n in ("BiRefNetRMBG", "ImageUpscaleWithModel", "LoraLoader"):
            line(n in nodes, f"ComfyUI node {n}")
    else:
        print("  --   ComfyUI not running (fine: it is started automatically for each night run)")

    print("Platforms")
    if cfg.browser.mode == "extension":
        from . import extension_setup

        line(config.BROWSER_EXES["chrome"].exists(), f"Chrome installed (uploads run in your profile '{cfg.browser.chrome_profile}')")
        line(extension_setup.configured(cfg), "PTO Uploader extension config written", "run: pto extension")
        seen = dbmod.get().get_kv("ext_last_seen")
        line(bool(seen), f"extension has checked in with the bot (last: {seen or 'never'})",
             "load D:\\PTO\\extension in chrome://extensions (Developer mode -> Load unpacked), with `pto serve` running")
    else:
        exe = config.BROWSER_EXES[cfg.browser.channel]
        line(exe.exists(), f"{cfg.browser.label} at {exe} (used by the browser uploaders)", "install it or change browser.channel")
    for name in ("redbubble", "teepublic", "displate"):
        pc = getattr(cfg.platforms, name)
        if not pc.enabled:
            print(f"  --   {name}: disabled")
        elif cfg.browser.mode == "extension":
            print(f"  OK   {name}: enabled ({'live' if pc.live else 'dry run'}) via your Chrome")
        else:
            prof = cfg.paths.profiles / name
            line(prof.exists() and any(prof.iterdir()), f"{name}: enabled ({'live' if pc.live else 'dry run'}), browser profile exists",
                 f"run: pto login {name}")
    pf = cfg.platforms.printify
    if pf.enabled:
        line(bool(config.secret("PRINTIFY_TOKEN")), "printify: PRINTIFY_TOKEN set", "add it to .env")
        line(bool(pf.shop_id), "printify: shop_id set", "run: pto printify-shops")
    else:
        print("  --   printify: disabled")
    print("Optional trend sources")
    print(f"  {'OK  ' if config.secret('YOUTUBE_API_KEY') else '--  '} YouTube (YOUTUBE_API_KEY)")
    print(f"  {'OK  ' if config.secret('REDDIT_CLIENT_ID') else '--  '} Reddit API (else public RSS)")
    print("\nAll good." if ok else "\nSome checks failed (see hints above).")
    sys.exit(0 if ok else 1)


def cmd_trends(args) -> None:
    _setup_logging()
    from . import trends

    print(trends.collect())
    rows = dbmod.get().all("SELECT source, region, title, status, score_reason FROM trends ORDER BY id DESC LIMIT ?", (args.show,))
    for r in rows:
        print(f"[{r['source']}/{r['region']}] {r['title']}  ({r['status']}{': ' + r['score_reason'] if r['score_reason'] else ''})")


def cmd_research(args) -> None:
    cfg = _setup_logging()
    from . import research

    trend = {"title": args.title, "source": "manual", "context": [], "meta": {"kind": args.kind} if args.kind else {}}
    import httpx

    try:
        notes = research.lookup(trend, cfg)
    except httpx.ConnectError as e:
        sys.exit(f"Can't reach Ollama at {cfg.ollama.url} ({e}); start it and try again.")
    print(research.brief(notes) or "Nothing found (no film / show / game article on Wikipedia matched that title).")
    lic = research.license_for(trend, notes, cfg)
    if lic:
        print(f"\nLicensed fan art: {lic.name} -> characters may be named; posts only to {', '.join(lic.platforms)}.")
    elif notes:
        print("\nNot licensed: designs pivot to a generic theme and these names are kept out of listings:\n  "
              + ", ".join(dict.fromkeys(research.protected_names(notes) + notes.get("real_people", []))))


def cmd_generate(args) -> None:
    _setup_logging()
    from . import pipeline

    res = pipeline.Pipeline().run(count=args.count, trend=args.trend, style=args.style, reason="manual")
    print(json.dumps(res, indent=1))


def cmd_render(args) -> None:
    _setup_logging()
    from . import pipeline

    p = pipeline.Pipeline()
    dbmod.get().update("designs", args.id, {"status": "planned", "attempts": 0})
    p.comfy.ensure_running()
    try:
        print(p.render(args.id))
    finally:
        if p.comfy.started_by_us and p.cfg.comfyui.stop_after_run:
            p.comfy.stop()


def cmd_upload(args) -> None:
    _setup_logging()
    from . import uploaders

    db = dbmod.get()
    cfg = config.load()
    kind = "poster" if args.platform == "displate" else "sticker"
    allowed = cfg.licenses_for(args.platform)
    design = db.design(args.design) if args.design else db.next_for_platform(args.platform, kind, allowed)
    if not design:
        print("no ready design to upload")
        return
    if design.get("license") and design["license"] not in allowed:
        sys.exit(f"design {design['id']} is fan art of {design['license']}, which isn't licensed for {args.platform} "
                 "(research.licensed_franchises -> platforms in config.yaml)")
    if uploaders.uses_extension(args.platform):
        live = getattr(config.load().platforms, args.platform).live
        jid = uploaders.queue_for_extension(args.platform, design, dry_run=args.dry_run or not live)
        print(json.dumps(_wait_for_extension(jid), indent=1))
        return
    res = uploaders.upload_one(args.platform, design, dry_run=True if args.dry_run else None)
    print(json.dumps(res.__dict__, indent=1))


def _wait_for_extension(job_id: int) -> dict:
    from . import extension_setup, extjobs

    cfg = config.load()
    extension_setup.write(cfg)
    started = extension_setup.ensure_server(cfg)
    print(f"Job {job_id} queued for the PTO Uploader extension in Chrome ({cfg.browser.chrome_profile})"
          f"{' - started the API for it' if started else ''}. It checks in every minute "
          "(click the extension's 'Check for jobs now' to go faster)...")
    job = extjobs.wait(job_id, timeout_s=cfg.browser.job_timeout_minutes * 60)
    if job is None:
        return {"status": "timeout", "hint": "Is Chrome open with the extension loaded? See `pto doctor`."}
    return job["result"] or {"status": job["status"]}


def cmd_tick(args) -> None:
    _setup_logging()
    from . import uploaders

    print(uploaders.tick(force=True))


def cmd_login(args) -> None:
    _setup_logging()
    from . import uploaders

    cfg = config.load()
    if cfg.browser.mode == "extension":
        print(f"Opening the {args.platform} login page in your Chrome ({cfg.browser.chrome_profile}). Log in there; "
              "the extension uses that session.")
        uploaders.get(args.platform).login()
        return
    print(f"Opening {args.platform} in a dedicated {cfg.browser.label} window. Log in, then close the window.")
    uploaders.get(args.platform).login()
    print("Saved.")


def cmd_inspect(args) -> None:
    _setup_logging()
    from . import uploaders

    if uploaders.uses_extension(args.platform):
        jid = uploaders.queue_for_extension(args.platform, None, dry_run=True, kind="inspect")
        print(json.dumps(_wait_for_extension(jid), indent=1))
        return
    print(f"Field dump: {uploaders.get(args.platform).inspect()}")


def cmd_extension(args) -> None:
    cfg = _setup_logging()
    from . import extension_setup

    path = extension_setup.write(cfg)
    print(f"""Extension ready at {path}

Load it once into your Chrome profile '{cfg.browser.chrome_profile}':
  1. In that profile, open  chrome://extensions
  2. Turn on "Developer mode" (top right)
  3. Click "Load unpacked" and pick  {path}
  4. Pin "PTO Uploader" from the puzzle-piece menu if you like (its popup shows the connection status)

It talks only to the bot at http://127.0.0.1:{cfg.web.port} using a secret token stored in
{cfg.paths.data_dir / 'ext_token.txt'}. Keep `pto serve` running; uploads happen while Chrome is open.
If you ever regenerate the token, click the reload icon on the extension card.""")


def cmd_printify_shops(args) -> None:
    _setup_logging()
    from .uploaders.printify import PrintifyClient

    tok = config.secret("PRINTIFY_TOKEN")
    if not tok:
        sys.exit("Set PRINTIFY_TOKEN in .env first")
    for s in PrintifyClient(tok).shops():
        print(f"{s['id']}\t{s.get('title')}\t{s.get('sales_channel')}")


def cmd_status(args) -> None:
    _setup_logging()
    db = dbmod.get()
    for r in db.all("SELECT kind, status, COUNT(*) n FROM designs GROUP BY kind, status ORDER BY kind, status"):
        print(f"{r['kind']:8} {r['status']:10} {r['n']}")
    for p in ("redbubble", "teepublic", "displate", "printify"):
        print(f"{p:10} uploaded today: {db.uploads_today(p)}")
    print("paused" if db.get_kv("paused") == "1" else "running")


def cmd_pause(args) -> None:
    _setup_logging()
    dbmod.get().set_kv("paused", "1" if args.cmd == "pause" else "0")
    print(args.cmd + "d")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pto", description="Trend-driven print-on-demand design bot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run dashboard + scheduler (the always-on mode)")
    s.add_argument("--no-scheduler", action="store_true")
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("doctor", help="check models, services, logins").set_defaults(fn=cmd_doctor)
    sub.add_parser("setup-comfy", help="point ComfyUI at the E: model folder").set_defaults(fn=cmd_setup_comfy)
    s = sub.add_parser("trends", help="fetch trends now and list the newest")
    s.add_argument("--show", type=int, default=30)
    s.set_defaults(fn=cmd_trends)
    s = sub.add_parser("research", help="look up the characters and world behind a film / show / game title")
    s.add_argument("title")
    s.add_argument("--kind", choices=["game", "movie", "tv"], help="narrow the Wikipedia search")
    s.set_defaults(fn=cmd_research)
    s = sub.add_parser("generate", help="run the generation pipeline now (ignores the GPU window)")
    s.add_argument("--count", type=int, help="number of sticker designs (default: fill the backlog)")
    s.add_argument("--trend", help="use this topic instead of fetched trends")
    s.add_argument("--style", help="force a style (kawaii, meme_text, retro_badge, flat_vector, realistic)")
    s.set_defaults(fn=cmd_generate)
    s = sub.add_parser("render", help="re-render one design by id")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("upload", help="upload one ready design to a platform")
    s.add_argument("--platform", required=True, choices=["redbubble", "teepublic", "displate", "printify"])
    s.add_argument("--design", type=int)
    s.add_argument("--dry-run", action="store_true", help="fill the form but don't publish")
    s.set_defaults(fn=cmd_upload)
    sub.add_parser("tick", help="run one upload tick now (ignores window and spacing)").set_defaults(fn=cmd_tick)
    for name, fn, hlp in (("login", cmd_login, "log in to a platform once (saved browser profile)"),
                          ("inspect", cmd_inspect, "dump a platform's upload form fields for selector tuning")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("platform", choices=["redbubble", "teepublic", "displate"])
        s.set_defaults(fn=fn)
    sub.add_parser("extension", help="set up the PTO Uploader Chrome extension (your everyday Chrome)").set_defaults(fn=cmd_extension)
    sub.add_parser("printify-shops", help="list Printify shops (to find shop_id)").set_defaults(fn=cmd_printify_shops)
    sub.add_parser("status", help="design counts and today's uploads").set_defaults(fn=cmd_status)
    sub.add_parser("pause", help="pause the scheduler jobs").set_defaults(fn=cmd_pause)
    sub.add_parser("resume", help="resume the scheduler jobs").set_defaults(fn=cmd_pause)
    args = ap.parse_args(argv)
    args.fn(args)
