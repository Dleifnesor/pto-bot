"""Typed configuration loaded from config.yaml (+ secrets from .env)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("PTO_CONFIG", ROOT / "config.yaml"))
load_dotenv(ROOT / ".env")


def _parse_hhmm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


class TimeWindow(BaseModel):
    start: str = "01:00"
    end: str = "08:00"

    def contains(self, t: dt.time) -> bool:
        a, b = _parse_hhmm(self.start), _parse_hhmm(self.end)
        if a <= b:
            return a <= t < b
        return t >= a or t < b  # wraps past midnight

    def end_after(self, now: dt.datetime) -> dt.datetime:
        """The datetime at which the window that contains `now` closes."""
        end = now.replace(hour=_parse_hhmm(self.end).hour, minute=_parse_hhmm(self.end).minute, second=0, microsecond=0)
        if end <= now:
            end += dt.timedelta(days=1)
        return end


class Paths(BaseModel):
    data_dir: Path = Path("E:/PTO-data")
    comfyui_dir: Path = Path("D:/ComfyUI_windows_portable")
    fonts_dir: Path = ROOT / "assets" / "fonts"

    @property
    def db(self) -> Path:
        return self.data_dir / "pto.sqlite3"

    @property
    def designs(self) -> Path:
        return self.data_dir / "designs"

    @property
    def logs(self) -> Path:
        return self.data_dir / "logs"

    @property
    def profiles(self) -> Path:
        return self.data_dir / "browser-profiles"

    @property
    def models(self) -> Path:
        return self.data_dir / "models"


class ComfyCfg(BaseModel):
    url: str = "http://127.0.0.1:8188"
    autostart: bool = True
    stop_after_run: bool = True
    startup_timeout_s: int = 240
    job_timeout_s: int = 600
    upscale_model: str = "RealESRGAN_x4plus.pth"
    refine_foreground: bool = True


class OllamaCfg(BaseModel):
    url: str = "http://127.0.0.1:11434"
    model: str = "qwen3-vl:8b-instruct"
    vision_model: str = "qwen3-vl:8b-instruct"
    num_ctx: int = 8192
    timeout_s: int = 300


class Schedule(BaseModel):
    generation_window: TimeWindow = TimeWindow(start="01:00", end="08:00")
    upload_window: TimeWindow = TimeWindow(start="09:00", end="23:00")
    trend_refresh_hours: float = 6
    designs_per_day: int = 10
    upload_tick_minutes: int = 5


class RedditCfg(BaseModel):
    enabled: bool = True
    subreddits: list[str] = Field(default_factory=lambda: ["popular", "wholesomememes", "memes", "aww", "cats", "dogs"])
    sort: Literal["hot", "rising", "top"] = "hot"
    per_sub: int = 15


class TrendsCfg(BaseModel):
    regions: list[str] = Field(default_factory=lambda: ["US", "GB", "CA", "AU"])
    google_trends: bool = True
    reddit: RedditCfg = RedditCfg()
    youtube: bool = True  # only runs when YOUTUBE_API_KEY is set
    steam: bool = True
    tmdb: bool = True  # only runs when TMDB_API_KEY is set
    wikipedia: bool = True
    events: bool = True
    popculture_lead_days: int = 28  # upcoming releases/events enter the queue this many days ahead
    popculture_recent_days: int = 10  # ...and stay eligible this long after release
    popculture_share: float = 0.7  # fraction of each night's trends picked from the pop-culture sources
    seasonal: bool = True
    seasonal_lead_days: int = 60
    evergreen: bool = True
    evergreen_per_run: int = 4
    evergreen_niches: list[str] = Field(default_factory=list)
    max_age_hours: float = 48
    allow_politics: bool = False
    sensitive_keywords: list[str] = Field(default_factory=list)
    skip_patterns: list[str] = Field(default_factory=list)  # regexes for trends that never make merch
    ip_blocklist: list[str] = Field(default_factory=list)


class LicensedFranchise(BaseModel):
    """A franchise you have permission to make fan art of (e.g. a Redbubble Fan Art Partner Program brand)."""
    name: str
    match: list[str] = Field(default_factory=list)  # trend/page titles containing any of these (plus `name`)
    platforms: list[str] = Field(default_factory=lambda: ["redbubble"])  # where the permission applies
    required_tags: list[str] = Field(default_factory=list)  # tags the brand's guidelines ask for (go first)
    guidelines: str = ""  # brand rules passed to the LLM, e.g. "no logos, no gore, characters only"
    wiki: str | None = None  # Fandom wiki host (e.g. "hollowknight.fandom.com") for character appearance details

    @property
    def terms(self) -> list[str]:
        return [self.name, *self.match]


class ResearchCfg(BaseModel):
    enabled: bool = True  # look up the media behind pop-culture trends: premise, setting, characters
    max_characters: int = 8
    licensed_franchises: list[LicensedFranchise] = Field(default_factory=list)


class LoraSpec(BaseModel):
    name: str
    strength: float = 0.8


class StyleSpec(BaseModel):
    description: str = ""
    kind: Literal["sticker", "poster"] = "sticker"
    checkpoint: str
    loras: list[LoraSpec] = Field(default_factory=list)
    prefix: str = ""
    suffix: str = ""
    negative: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 6
    cfg: float = 2.0
    sampler: str = "dpmpp_sde"
    scheduler: str = "karras"
    rmbg_model: str = "BiRefNet_toonout"
    fonts: list[str] = Field(default_factory=list)


class CutoutCfg(BaseModel):
    target_long_side: int = 4500
    padding_px: int = 24
    min_island_frac: float = 0.02  # drop blobs smaller than 2% of the largest blob
    max_hole_frac: float = 0.002  # fill holes smaller than this fraction of the subject
    edge_softness: float = 0.15  # 0 = hard binary edge, 0.5 = original soft matte


class PosterCfg(BaseModel):
    enabled: Literal["auto", "always", "never"] = "auto"  # auto = only when Displate is enabled
    width: int = 2900
    height: int = 4060
    jpeg_quality: int = 95


class QaCfg(BaseModel):
    vision: bool = True
    min_score: int = 7
    max_attempts: int = 3
    min_sharpness: float = 40.0
    max_border_touch: float = 0.06
    min_coverage: float = 0.04
    max_coverage: float = 0.92
    max_fragments: int = 6
    phash_min_distance: int = 8


class PlatformCfg(BaseModel):
    enabled: bool = False
    live: bool = True  # False = fill the form and stop before the final publish click
    daily_limit: int = 10
    min_gap_minutes: int = 30
    jitter_minutes: int = 25
    max_tags: int = 15
    start_minimized: bool = True
    captcha_wait_minutes: int = 20
    selectors: dict[str, list[str]] = Field(default_factory=dict)
    urls: dict[str, str] = Field(default_factory=dict)


class RedbubbleCfg(PlatformCfg):
    enabled: bool = True
    template_work_id: str | None = None  # an existing work whose product settings get copied


class PrintifyProduct(BaseModel):
    key: str
    blueprint_query: str
    provider_query: str = ""
    max_variants: int = 40
    colors: list[str] = Field(default_factory=list)
    fallback_price_cents: int = 1500


class PrintifyCfg(PlatformCfg):
    shop_id: str | None = None
    profit_margin: float = 0.35  # Printify's UI default is ~40%; slightly cheaper
    max_tags: int = 13
    products: list[PrintifyProduct] = Field(default_factory=list)
    ai_disclosure: str = "This design was created with the help of AI image tools and finished by our shop."


class Platforms(BaseModel):
    redbubble: RedbubbleCfg = RedbubbleCfg()
    teepublic: PlatformCfg = PlatformCfg()
    displate: PlatformCfg = PlatformCfg(daily_limit=3)
    printify: PrintifyCfg = PrintifyCfg()


BROWSER_EXES = {
    "chrome": Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    "msedge": Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
}


class BrowserCfg(BaseModel):
    # extension = your everyday Chrome profile via the PTO extension; playwright = a separate bot-only profile
    mode: Literal["extension", "playwright"] = "extension"
    chrome_profile: str = "Profile 1"  # extension mode: the Chrome profile that has the extension + your logins
    queue_timeout_minutes: int = 180  # give up on a job Chrome never picked up (Chrome closed?)
    job_timeout_minutes: int = 30  # give up on a job the extension claimed but never reported back
    channel: Literal["chrome", "msedge"] = "chrome"  # playwright mode: installed browser to drive

    @property
    def label(self) -> str:
        return {"chrome": "Chrome", "msedge": "Edge"}[self.channel]


class Notifications(BaseModel):
    windows_toast: bool = True


class WebCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class Config(BaseModel):
    paths: Paths = Paths()
    comfyui: ComfyCfg = ComfyCfg()
    ollama: OllamaCfg = OllamaCfg()
    schedule: Schedule = Schedule()
    trends: TrendsCfg = TrendsCfg()
    research: ResearchCfg = ResearchCfg()
    styles: dict[str, StyleSpec] = Field(default_factory=dict)
    cutout: CutoutCfg = CutoutCfg()
    poster: PosterCfg = PosterCfg()
    qa: QaCfg = QaCfg()
    platforms: Platforms = Platforms()
    browser: BrowserCfg = BrowserCfg()
    notifications: Notifications = Notifications()
    web: WebCfg = WebCfg()

    @property
    def sticker_styles(self) -> dict[str, StyleSpec]:
        return {k: v for k, v in self.styles.items() if v.kind == "sticker"}

    @property
    def posters_on(self) -> bool:
        if self.poster.enabled == "always":
            return True
        if self.poster.enabled == "never":
            return False
        return self.platforms.displate.enabled

    def licenses_for(self, platform: str) -> list[str]:
        """Names of the licensed franchises whose fan art may be posted to `platform`."""
        return [f.name for f in self.research.licensed_franchises if platform in f.platforms]

    def ensure_dirs(self) -> None:
        for p in (self.paths.data_dir, self.paths.designs, self.paths.logs, self.paths.profiles):
            p.mkdir(parents=True, exist_ok=True)


def secret(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


_cached: Config | None = None


def load(path: Path | None = None, *, reload: bool = False) -> Config:
    global _cached
    if _cached is not None and not reload and path is None:
        return _cached
    p = path or CONFIG_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg = Config.model_validate(raw or {})
    if path is None:
        _cached = cfg
    return cfg


def validate_text(text: str) -> Config:
    """Parse YAML text into a Config without saving it (used by the dashboard editor)."""
    return Config.model_validate(yaml.safe_load(text) or {})
