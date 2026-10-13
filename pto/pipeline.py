"""Night run: trends -> concepts -> ComfyUI renders -> cutout/text -> QA -> ready queue."""
from __future__ import annotations

import datetime as dt
import math
import random
import threading
from pathlib import Path

from PIL import Image

from . import config, db as dbmod, log, notify, research, trends
from .imaging import checks, cutout, poster, text, workflows
from .imaging.comfy import ComfyError, ComfyProcess
from .llm import concepts, qa
from .llm.ollama import Ollama

_log = log.get("pipeline")
_run_lock = threading.Lock()
stop_event = threading.Event()

SOURCE_WEIGHT = {"google": 1.0, "reddit": 0.9, "youtube": 0.8, "seasonal": 1.1, "evergreen": 0.85,
                 "steam": 1.0, "tmdb": 1.0, "wikipedia": 0.9, "events": 1.05}
STICKER_PLATFORMS = ("redbubble", "teepublic", "printify")


class Pipeline:
    def __init__(self, cfg: config.Config | None = None):
        self.cfg = cfg or config.load()
        self.db = dbmod.get()
        self.llm = Ollama(self.cfg.ollama)
        self.comfy = ComfyProcess(self.cfg)

    # ---------------------------------------------------------------- planning
    def _fresh_since(self) -> str:
        return (dt.datetime.now() - dt.timedelta(hours=self.cfg.trends.max_age_hours)).isoformat()

    def score_new(self) -> int:
        new = self.db.all("SELECT * FROM trends WHERE status = 'new' AND fetched_at >= ? ORDER BY fetched_at DESC LIMIT 150",
                          (self._fresh_since(),))
        if not new:
            return 0
        scores = concepts.score_trends(new, self.cfg, self.llm)
        for t in new:
            s, reason = scores.get(t["id"], (0, "not scored"))
            self.db.update("trends", t["id"], {"status": "scored", "score": s, "score_reason": reason})
        _log.info("scored %d trends", len(new))
        return len(new)

    @staticmethod
    def timing_boost(meta: dict | None) -> float:
        """Favor releases/events that are 3-4 weeks out (listings need time to index) or just released."""
        days = (meta or {}).get("days_until")
        if days is None:
            return 1.0
        if 18 <= days <= 32:
            return 1.3
        if 0 <= days < 18:
            return 1.1
        return 1.15 if days >= -10 else 0.9

    def pick_trends(self, n: int, min_score: int = 6) -> list[dict]:
        rows = self.db.all("SELECT * FROM trends WHERE status = 'scored' AND score >= ? AND fetched_at >= ?",
                           (min_score, self._fresh_since()))
        for r in rows:
            r["_rank"] = (r["score"] * SOURCE_WEIGHT.get(r["source"], 1.0) * self.timing_boost(r.get("meta"))
                          + math.log10(max(10, r["traffic"] or 10)) * 0.3)
        rows.sort(key=lambda r: r["_rank"], reverse=True)
        seen_norms: set[str] = set()

        def take(pool: list[dict], k: int) -> list[dict]:
            got, per_source = [], {}
            cap = max(2, math.ceil(k * 0.6))  # no single source dominates its group
            for r in pool:
                if len(got) >= k:
                    break
                if per_source.get(r["source"], 0) >= cap or r["norm"] in seen_norms:
                    continue
                got.append(r)
                seen_norms.add(r["norm"])
                per_source[r["source"]] = per_source.get(r["source"], 0) + 1
            return got

        pop = [r for r in rows if r["source"] in trends.POPCULTURE_SOURCES]
        rest = [r for r in rows if r["source"] not in trends.POPCULTURE_SOURCES]
        n_pop = round(n * self.cfg.trends.popculture_share)
        picked = take(pop, n_pop)
        picked += take(rest, n - len(picked))
        if len(picked) < n:  # one group ran dry: fill from whatever is left
            picked += take(rows, n - len(picked))
        return picked

    def research_trend(self, trend: dict) -> dict:
        """Research notes for a trend (cached on its row); {} when off, not media, or the lookup fails."""
        if not self.cfg.research.enabled:
            return {}
        if trend.get("research"):
            return trend["research"]
        try:
            notes = research.lookup(trend, self.cfg, self.llm)
        except Exception as e:  # noqa: BLE001 - concepts still work without research
            _log.warning("research failed for %r: %s", trend["title"], e)
            return {}
        if notes:
            self.db.update("trends", trend["id"], {"research": notes})
        return notes

    def plan(self, n_designs: int, trend_override: str | None = None, style: str | None = None) -> list[int]:
        """Create up to n_designs new sticker designs (+ posters when enabled). Returns new design ids."""
        if n_designs <= 0:
            return []
        if trend_override:
            tid = self.db.add_trend({"source": "manual", "region": "global", "title": trend_override,
                                     "norm": trends.normalize(trend_override) + f" {random.randint(0, 1 << 30)}",
                                     "day": dt.date.today().isoformat(), "traffic": 0, "context": [], "url": None,
                                     "fetched_at": dbmod.now_iso(), "status": "scored", "score": 10})
            candidates = [self.db.one("SELECT * FROM trends WHERE id = ?", (tid,))]
        else:
            self.score_new()
            candidates = self.pick_trends(max(3, n_designs))
        recent = self.db.recent_titles()
        poster_budget = self.cfg.platforms.displate.daily_limit if self.cfg.posters_on else 0
        created: list[int] = []
        for trend in candidates:
            if len(created) >= n_designs:
                break
            try:
                hint = "exactly 1 design" if style else "1 to 3 designs (more for broad, hot trends; 1 for niche ones)"
                notes = self.research_trend(trend)
                designs, protected, theme = concepts.make_concepts(trend, n_hint=hint, cfg=self.cfg, llm=self.llm,
                                                                   recent_titles=recent, notes=notes)
            except Exception as e:  # noqa: BLE001
                _log.warning("concept generation failed for %r: %s", trend["title"], e)
                continue
            self.db.update("trends", trend["id"], {"status": "used"})
            if protected:
                _log.info("trend %r: pivoted away from protected terms %s -> %r", trend["title"], protected, theme)
            for d in designs:
                if len(created) >= n_designs:
                    break
                if style:
                    d["style"] = style
                now = dbmod.now_iso()
                base = {"trend_id": trend["id"], "theme": theme, "title": d["title"], "tags": d["tags"],
                        "description": d["description"], "protected_terms": protected, "license": d.get("license"),
                        "created_at": now, "updated_at": now}
                did = self.db.insert("designs", {**base, "kind": "sticker", "style": d["style"], "subject": d["subject"],
                                                 "slogan": d["slogan"], "text_layout": d["text_layout"]})
                created.append(did)
                recent.append(d["title"])
                if poster_budget > 0:
                    self.db.insert("designs", {**base, "kind": "poster", "style": "poster", "parent_id": did,
                                               "subject": d["poster_subject"], "slogan": "", "text_layout": "none"})
                    poster_budget -= 1
        _log.info("planned %d designs", len(created))
        return created

    # --------------------------------------------------------------- rendering
    def _dir(self, design_id: int) -> Path:
        p = self.cfg.paths.designs / f"{design_id:06d}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.cfg.paths.designs).as_posix()

    def _render_sticker(self, d: dict, style: config.StyleSpec, seed: int, attempt: int) -> tuple[list[str], dict, dict, str, str]:
        out = self._dir(d["id"])
        graph, outs, positive = workflows.sticker_graph(style, d["subject"], seed, refine_foreground=self.cfg.comfyui.refine_foreground)
        imgs = self.comfy.client.run(graph, outs)
        raw = imgs["raw"].convert("RGB")
        raw.save(out / f"raw_{attempt}.jpg", quality=90)
        art, metrics = cutout.prepare(imgs["cut"], self.cfg.cutout)
        fonts = [self.cfg.paths.fonts_dir / f for f in style.fonts if (self.cfg.paths.fonts_dir / f).exists()]
        composed = text.compose(art, d.get("slogan") or "", d.get("text_layout") or "none", fonts, seed)
        final = cutout.finalize(composed, self.cfg.cutout)
        reasons, m = checks.sticker(metrics, final, self.cfg.qa, self.db.recent_phashes())
        report: dict = {"checks": m, "check_failures": reasons}
        if not reasons and self.cfg.qa.vision:
            v = qa.inspect(final, d, "sticker", self.cfg, self.llm)
            report["vision"] = v
            reasons = v["fail_reasons"]
        name = "sticker.png" if not reasons else f"rejected_{attempt}.png"
        final.save(out / name, optimize=False, compress_level=6)
        thumb = final.copy()
        thumb.thumbnail((640, 640), Image.LANCZOS)
        thumb.save(out / ("thumb.png" if not reasons else f"thumb_rejected_{attempt}.png"))
        files = {"raw": self._rel(out / f"raw_{attempt}.jpg"), "final": self._rel(out / name),
                 "thumb": self._rel(out / ("thumb.png" if not reasons else f"thumb_rejected_{attempt}.png"))}
        return reasons, report, files, m["phash"], positive

    def _render_poster(self, d: dict, style: config.StyleSpec, seed: int, attempt: int) -> tuple[list[str], dict, dict, str, str]:
        out = self._dir(d["id"])
        graph, outs, positive = workflows.poster_graph(style, d["subject"], seed)
        imgs = self.comfy.client.run(graph, outs)
        imgs["raw"].convert("RGB").save(out / f"raw_{attempt}.jpg", quality=90)
        final = poster.finalize(imgs["full"], self.cfg.poster)
        reasons, m = checks.poster(final, self.cfg.qa, self.db.recent_phashes())
        report: dict = {"checks": m, "check_failures": reasons}
        if not reasons and self.cfg.qa.vision:
            v = qa.inspect(final, d, "poster", self.cfg, self.llm)
            report["vision"] = v
            reasons = v["fail_reasons"]
        name = "poster.jpg" if not reasons else f"rejected_{attempt}.jpg"
        poster.save(final, out / name, self.cfg.poster)
        thumb = final.copy()
        thumb.thumbnail((640, 640), Image.LANCZOS)
        tname = "thumb.png" if not reasons else f"thumb_rejected_{attempt}.png"
        thumb.save(out / tname)
        files = {"raw": self._rel(out / f"raw_{attempt}.jpg"), "final": self._rel(out / name), "thumb": self._rel(out / tname)}
        return reasons, report, files, m["phash"], positive

    def render(self, design_id: int) -> str:
        d = self.db.design(design_id)
        if not d or d["status"] not in ("planned", "rendering", "failed"):
            return d["status"] if d else "missing"
        style = self.cfg.styles.get(d["style"])
        if style is None:
            self.db.update("designs", design_id, {"status": "failed", "error": f"unknown style {d['style']}"})
            return "failed"
        history = d["qa"].get("attempts", []) if isinstance(d["qa"], dict) else []
        for attempt in range(d["attempts"], self.cfg.qa.max_attempts):
            if stop_event.is_set():
                self.db.update("designs", design_id, {"status": "planned"})
                return "planned"
            seed = random.randint(1, 2**48)
            self.db.update("designs", design_id, {"status": "rendering", "attempts": attempt + 1, "seed": seed})
            fn = self._render_poster if d["kind"] == "poster" else self._render_sticker
            try:
                reasons, report, files, ph, positive = fn(d, style, seed, attempt)
            except ComfyError as e:
                _log.error("design %d attempt %d: %s", design_id, attempt + 1, e)
                history.append({"attempt": attempt + 1, "seed": seed, "error": str(e)[:500]})
                self.db.update("designs", design_id, {"error": str(e)[:1000], "qa": {"attempts": history}})
                if "rejected workflow" in str(e):  # config problem (missing model/node) - retrying won't help
                    break
                continue
            history.append({"attempt": attempt + 1, "seed": seed, "reasons": reasons, "files": files, **report})
            update = {"qa": {"attempts": history, **report}, "prompt": positive, "negative": style.negative}
            if not reasons:
                self.db.update("designs", design_id, {**update, "status": "ready", "files": files, "phash": ph,
                                                      "error": None, "ready_at": dbmod.now_iso()})
                _log.info("design %d ready (%s, attempt %d)", design_id, d["style"], attempt + 1)
                return "ready"
            _log.info("design %d attempt %d rejected: %s", design_id, attempt + 1, "; ".join(reasons))
            self.db.update("designs", design_id, {**update, "error": "; ".join(reasons)[:1000], "files": files})
        self.db.update("designs", design_id, {"status": "failed"})
        return "failed"

    # ---------------------------------------------------------------- night run
    def enabled_sticker_platforms(self) -> list[str]:
        p = self.cfg.platforms
        return [n for n in STICKER_PLATFORMS if getattr(p, n).enabled]

    def backlog(self) -> int:
        """Ready stickers still owed to a platform they're allowed on (licensed fan art only counts its platforms)."""
        enabled = self.enabled_sticker_platforms() or ["redbubble"]
        n = self.db.ready_backlog(enabled)
        for f in self.cfg.research.licensed_franchises:
            n += self.db.ready_backlog([p for p in enabled if p in f.platforms], license=f.name)
        return n

    def run(self, *, deadline: dt.datetime | None = None, count: int | None = None, trend: str | None = None,
            style: str | None = None, reason: str = "scheduled") -> dict:
        if not _run_lock.acquire(blocking=False):
            _log.info("generation already running; skipping")
            return {"skipped": "already running"}
        stop_event.clear()
        summary = {"ready": 0, "failed": 0, "planned": 0}
        try:
            last = self.db.get_kv("trends_last_fetch")
            if not trend and (not last or dt.datetime.fromisoformat(last) < dt.datetime.now() - dt.timedelta(hours=self.cfg.schedule.trend_refresh_hours)):
                trends.collect(self.cfg)
            if count is None:
                backlog = self.backlog()
                count = max(0, min(self.cfg.schedule.designs_per_day, 2 * self.cfg.schedule.designs_per_day - backlog))
            pending_rows = self.db.all(
                "SELECT id, kind FROM designs WHERE status IN ('planned', 'rendering') ORDER BY kind DESC, id") if not trend else []
            pending = [r["id"] for r in pending_rows]
            pending_stickers = sum(1 for r in pending_rows if r["kind"] == "sticker")
            new_ids = self.plan(max(0, count - pending_stickers) if not trend else count or 1, trend_override=trend, style=style)
            queue = pending + new_ids
            queue += [r["id"] for r in self.db.all(
                "SELECT id FROM designs WHERE kind = 'poster' AND status = 'planned' AND parent_id IN (%s)" % ",".join(map(str, new_ids or [0])))]
            queue = list(dict.fromkeys(queue))
            summary["planned"] = len(queue)
            if not queue:
                _log.info("nothing to render (backlog is full)")
                return summary
            self.comfy.ensure_running()
            for did in queue:
                if deadline and dt.datetime.now() >= deadline:
                    _log.info("generation window closed; %d left for next night", len(queue) - summary["ready"] - summary["failed"])
                    break
                if stop_event.is_set():
                    break
                status = self.render(did)
                summary["ready" if status == "ready" else "failed" if status == "failed" else "planned"] += 1
            return summary
        finally:
            try:
                self.comfy.client.free()
                if self.comfy.started_by_us and self.cfg.comfyui.stop_after_run:
                    self.comfy.stop()
                self.llm.unload()
                if self.cfg.ollama.vision_model != self.cfg.ollama.model:
                    self.llm.unload(self.cfg.ollama.vision_model)
            finally:
                _run_lock.release()
            if summary.get("planned"):
                notify.toast("Designs generated", f"{summary['ready']} ready, {summary['failed']} failed ({reason})")
