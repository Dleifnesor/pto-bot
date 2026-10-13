# PTO: trend-driven print-on-demand bot

Pulls trending topics, has a local LLM turn them into original design concepts, renders them locally in ComfyUI,
cuts them out into tight transparent PNG stickers (slogans typeset with real fonts), quality-checks them, and posts
them to Redbubble (plus TeePublic, Displate and Printify→Etsy once you have those accounts).

```
trends (Google Trends RSS, Reddit, YouTube, holiday calendar, evergreen niches)
  -> tragedy / non-merch filter (code)          -> LLM merch score 0-10 (Qwen3-VL 8B instruct, Ollama)
  -> character research: Wikipedia article (premise, setting, cast) -> LLM extracts characters and their looks
  -> LLM concepts: style, prompt, slogan, title, 15 tags  (+ IP pivot: no brands, people, characters, titles)
  -> ComfyUI: SDXL style workflow -> RealESRGAN 4x -> BiRefNet background removal
  -> cutout cleanup (specks, pinholes, crisp edge) -> slogan typesetting -> 4500 px transparent PNG
  -> code checks (cut-off, leftover background, blur, duplicates) -> vision-model QA -> retry with new seed (max 3)
  -> ready queue -> spaced-out uploads within each platform's daily limit
```

## Where things live

| What | Where |
|---|---|
| Code, config, fonts | `D:\PTO` |
| Database, designs, logs, browser profiles, screenshots | `E:\PTO-data` |
| New SDXL checkpoint + LoRAs | `E:\PTO-data\models` (ComfyUI reads it via `extra_model_paths.yaml`) |
| ComfyUI (unchanged, started/stopped by the bot) | `D:\ComfyUI_windows_portable` |
| LLM | Ollama, `qwen3-vl:8b-instruct` (Ollama's own model folder; see "Moving Ollama to E:") |

## Install (one click)

Download the repo (Code → Download ZIP, or `git clone`), then **double-click `install.bat`**. It:

- finds Python 3.10+ (or installs 3.12 with winget), creates `.venv` and installs the packages
- downloads the fonts, creates `.env`, and moves the data / ComfyUI folders into the project if `E:` / `D:` don't
  exist on that PC
- lists what's missing and, after one "Y", downloads it: ComfyUI portable for your GPU (NVIDIA / AMD / Intel Arc),
  the ComfyUI-RMBG node, the SDXL checkpoints, LoRAs and upscaler from `config.yaml` (~15 GB with everything),
  Ollama (via winget) and its model
- writes the Chrome extension config, adds a **PTO Bot** desktop shortcut (runs `start.bat`) and runs `pto doctor`

Re-run it any time to repair a broken install; it skips whatever is already there. Options:
`install.bat --yes` (no prompt), `--skip-downloads`, `--no-shortcut`. Needs Windows 10/11 and a GPU with 8 GB+ VRAM.
Use `pto.bat <command>` instead of `.\pto.ps1 <command>` if PowerShell blocks scripts on that PC.

## Going live on Redbubble

Uploads run in **your everyday Chrome** (`browser.mode: extension`, profile `browser.chrome_profile`) through the
small PTO Uploader extension in `D:\PTO\extension`. Chrome (since v136) doesn't let automation tools drive your main
profile, so the extension does the form-filling instead: every minute it asks the bot (localhost, secret token)
for a job, opens the upload page in a background tab, fills it, submits, reports back and closes the tab. Failed
or dry-run tabs stay open so you can look. Uploads only happen while Chrome is open; queued jobs wait for it.

1. `.\pto.ps1 extension`, then in Chrome **Profile 1**: `chrome://extensions` → Developer mode → Load unpacked →
   `D:\PTO\extension`. The extension's toolbar popup shows whether it can reach the bot.
2. Be logged in to Redbubble in that profile (`.\pto.ps1 login redbubble` just opens the login page there).
3. **Recommended:** upload one work by hand with exactly the products you want enabled (stickers, tees, hoodies,
   mugs, phone cases, totes) and their colors, then put its numeric ID in `platforms.redbubble.template_work_id`.
   Every upload then uses Redbubble's "copy settings" page, so products and colors match.
4. **Pricing:** Redbubble markup is an account setting (Account → Pricing; the default markup is 20%). Set it a
   little lower there, for example 15%. The bot never changes account settings.
5. `.\pto.ps1 inspect redbubble`: the extension opens the upload page and records its fields to
   `E:\PTO-data\screenshots\redbubble\` without submitting anything. If Redbubble's field IDs differ from the
   defaults in `pto/uploaders/redbubble.py`, override them in `config.yaml` under `platforms.redbubble.selectors`.
6. `.\pto.ps1 upload --platform redbubble --dry-run`: fills the real form and stops before publishing; the
   filled-in tab is left open for you to check.
7. `.\pto.ps1 serve`: dashboard at http://127.0.0.1:8765 plus the scheduler. Leave it running.

To use a separate bot-only browser profile instead (works with Chrome closed), set `browser.mode: playwright`
and log in once with `.\pto.ps1 login redbubble`.

## Daily rhythm (`schedule` in config.yaml)

- **Every 6 h:** fetch trends.
- **01:00–08:00:** generation window. The GPU is used only here. ComfyUI is launched headless and shut down
  afterwards; Ollama models are unloaded. If the PC was off at 01:00, it catches up when it starts inside the window.
- **09:00–23:00:** uploads, at most one per platform every `min_gap_minutes` plus random `jitter_minutes`, up to
  `daily_limit` per platform.
- Each night tops the ready queue up to `designs_per_day` (10) new designs, capped at two days of backlog.

## Commands

```
.\pto.ps1 serve                      # always-on mode (dashboard + scheduler)
.\pto.ps1 doctor                     # check models, services, logins
.\pto.ps1 trends                     # fetch trends now and list them
.\pto.ps1 research "Zootopia 2" --kind movie   # preview the character research for a title
.\pto.ps1 generate --count 3         # run the pipeline now (ignores the GPU window)
.\pto.ps1 generate --trend "frogs in raincoats" --style kawaii
.\pto.ps1 render 12                  # re-render design 12
.\pto.ps1 upload --platform redbubble [--design 12] [--dry-run]
.\pto.ps1 tick                       # one upload round now (ignores window/spacing)
.\pto.ps1 login|inspect redbubble|teepublic|displate
.\pto.ps1 printify-shops             # find your Printify shop_id
.\pto.ps1 pause | resume | status
```

## Other platforms

| Platform | How | Status |
|---|---|---|
| Redbubble | browser automation (no public API) | built; selectors need checking with `inspect` after login |
| TeePublic | browser automation (no public API) | built, **untested** (no account yet). `login`, then `inspect`, fix selectors, then `enabled: true` |
| Displate | browser automation, portrait posters (2900×4060 JPEG) | built, **untested**; needs an approved artist account. Enabling it also turns on poster generation |
| Printify → Etsy | official API | built, **untested**. Add `PRINTIFY_TOKEN` to `.env`, run `printify-shops`, set `shop_id`, `enabled: true`. Prices = cost / (1 − 0.35), i.e. a bit under Printify's ~40% default margin. An AI-disclosure line is added to every Etsy description |

CAPTCHAs are never bypassed. When one appears, you get a Windows notification, the Chrome window comes to the
front, and the upload waits up to `captcha_wait_minutes` for you to solve it.

## Guardrails (on by design)

- **IP pivot:** the LLM must list protected names (brands, real people, characters, titles, teams) and turn the
  trend into a generic theme. Code then drops any concept whose title or slogan still contains one, and strips them
  from tags, descriptions and prompts. Add names to `trends.ip_blocklist` as needed. Rights holders sue POD sellers
  directly and freeze payouts, and Redbubble and TeePublic are one company with shared repeat-infringer bans.
- **Character research:** for film / show / game trends the bot reads the Wikipedia article and pulls out the
  premise, setting, motifs and main characters (with what they look like). Prompts get much more specific to that
  world. For unlicensed media the character names, the title and the actors it finds go straight into the IP filter,
  so a design can be *for* the fans without naming or copying anyone.
- **Licensed fan art (opt-in, per franchise):** list a franchise under `research.licensed_franchises` only once you
  have permission for it, e.g. a Redbubble Fan Art Partner Program brand (each brand has its own guidelines and
  required tags, and approval covers Redbubble only). For those trends the LLM may name and draw the characters
  (optionally using the franchise's Fandom wiki for exact looks), the brand's required tags go first, and the designs
  are uploaded **only** to that franchise's `platforms`. Remove the entry and its designs stop being posted.
  Logos, actors' likenesses and other franchises stay blocked either way.
- **Tragedy filter:** news trends with words like *earthquake, killed, shooting* are dropped before the LLM sees
  them (`trends.sensitive_keywords`). Sports fixtures, live TV and price news are dropped too (`trends.skip_patterns`).
- **QA:** code checks plus the vision model must both pass, otherwise it re-renders with a new seed.

## Moving Ollama to E:

The Ollama desktop app uses the machine-wide `OLLAMA_MODELS=D:\ollama\models`. To move it, change "Model location"
in Ollama's Settings (or that system variable) to `E:\ollama-models`, restart Ollama, and re-run
`ollama pull qwen3-vl:8b-instruct`.

## Tuning

- **Styles:** each entry under `styles:` is one ComfyUI setup (checkpoint, LoRAs, prompt prefix/suffix, negatives,
  sampler). Add a style there and the LLM can pick it automatically.
- **Too many rejections:** lower `qa.min_score`, or `qa.min_sharpness` for very flat art.
- **Logs:** `E:\PTO-data\logs\pto.log` and `comfyui.log`, also in the dashboard's Logs tab.
