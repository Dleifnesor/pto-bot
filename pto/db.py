"""SQLite storage for trends, designs, uploads and small key/value state."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    region TEXT,
    title TEXT NOT NULL,
    norm TEXT NOT NULL,
    day TEXT NOT NULL,
    traffic INTEGER DEFAULT 0,
    context TEXT DEFAULT '[]',
    url TEXT,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',      -- new | scored | used | rejected
    score REAL,
    score_reason TEXT,
    UNIQUE(source, norm, day)
);
CREATE TABLE IF NOT EXISTS designs (
    id INTEGER PRIMARY KEY,
    trend_id INTEGER REFERENCES trends(id),
    parent_id INTEGER REFERENCES designs(id),
    kind TEXT NOT NULL DEFAULT 'sticker',      -- sticker | poster
    style TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',    -- planned | rendering | ready | failed | rejected | retired
    attempts INTEGER NOT NULL DEFAULT 0,
    seed INTEGER,
    theme TEXT,
    subject TEXT,
    slogan TEXT,
    text_layout TEXT,
    title TEXT,
    tags TEXT DEFAULT '[]',
    description TEXT,
    protected_terms TEXT DEFAULT '[]',
    prompt TEXT,
    negative TEXT,
    files TEXT DEFAULT '{}',
    phash TEXT,
    qa TEXT DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ready_at TEXT
);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY,
    design_id INTEGER NOT NULL REFERENCES designs(id),
    platform TEXT NOT NULL,
    status TEXT NOT NULL,                      -- success | dry_run | failed | needs_login | captcha_timeout
    url TEXT,
    error TEXT,
    screenshot TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ext_jobs (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'upload',       -- upload | inspect
    design_id INTEGER REFERENCES designs(id),
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',     -- queued | claimed | done | expired
    result TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ext_jobs_status ON ext_jobs(status, platform);
CREATE INDEX IF NOT EXISTS idx_designs_status ON designs(status, kind);
CREATE INDEX IF NOT EXISTS idx_uploads_design ON uploads(design_id, platform);
CREATE INDEX IF NOT EXISTS idx_trends_status ON trends(status, fetched_at);
"""

JSON_COLS = {"context", "tags", "protected_terms", "files", "qa", "payload", "result", "meta", "research"}

# columns added after the first release: (table, column, definition)
MIGRATIONS = [("trends", "meta", "TEXT DEFAULT '{}'"), ("trends", "research", "TEXT DEFAULT '{}'"),
              ("designs", "license", "TEXT")]  # license = licensed franchise name for fan art, else NULL


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def _encode(v: Any) -> Any:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.executescript(SCHEMA)
        for table, col, ddl in MIGRATIONS:
            cols = {r[1] for r in self._con.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self._con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        self._con.commit()

    # -- low level -------------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._con.execute(sql, params)
            self._con.commit()
            return cur

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [_decode(r) for r in self._con.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            return _decode(self._con.execute(sql, params).fetchone())

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        with self._lock:
            row = self._con.execute(sql, params).fetchone()
            return row[0] if row else None

    def insert(self, table: str, data: dict) -> int:
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(_encode(v) for v in data.values()))
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, data: dict) -> None:
        if table == "designs":
            data = {**data, "updated_at": now_iso()}
        sets = ", ".join(f"{k} = ?" for k in data)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*(_encode(v) for v in data.values()), row_id))

    # -- kv ----------------------------------------------------------------------
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        v = self.scalar("SELECT value FROM kv WHERE key = ?", (key,))
        return default if v is None else v

    def set_kv(self, key: str, value: str | None) -> None:
        if value is None:
            self.execute("DELETE FROM kv WHERE key = ?", (key,))
        else:
            self.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # -- domain helpers ------------------------------------------------------------
    def add_trend(self, item: dict) -> int | None:
        """Insert a trend; returns None when it was already seen today."""
        try:
            return self.insert("trends", item)
        except sqlite3.IntegrityError:
            return None

    def design(self, design_id: int) -> dict | None:
        return self.one("SELECT * FROM designs WHERE id = ?", (design_id,))

    def uploads_today(self, platform: str) -> int:
        today = dt.date.today().isoformat()
        return int(self.scalar(
            "SELECT COUNT(*) FROM uploads WHERE platform = ? AND status IN ('success', 'dry_run') AND substr(created_at, 1, 10) = ?",
            (platform, today)) or 0)

    def next_for_platform(self, platform: str, kind: str, licenses: list[str] | tuple = ()) -> dict | None:
        """Oldest ready design of `kind` that hasn't been posted to `platform` yet (failures retried up to 3 times).

        Licensed fan art is only eligible when its franchise is in `licenses` (the ones allowed on this platform)."""
        marks = ",".join("?" for _ in licenses) or "NULL"
        return self.one(
            f"""
            SELECT d.* FROM designs d
            WHERE d.status = 'ready' AND d.kind = ?
              AND (d.license IS NULL OR d.license IN ({marks}))
              AND NOT EXISTS (SELECT 1 FROM uploads u WHERE u.design_id = d.id AND u.platform = ? AND u.status IN ('success', 'dry_run'))
              AND (SELECT COUNT(*) FROM uploads u WHERE u.design_id = d.id AND u.platform = ? AND u.status = 'failed') < 3
            ORDER BY d.ready_at ASC LIMIT 1
            """,
            (kind, *licenses, platform, platform),
        )

    def ready_backlog(self, platforms: list[str], kind: str = "sticker", license: str | None = None) -> int:
        """Ready designs (unlicensed, or fan art of `license`) still waiting for at least one of `platforms`."""
        if not platforms:
            return 0
        marks = ",".join("?" for _ in platforms)
        return int(self.scalar(
            f"""
            SELECT COUNT(*) FROM designs d WHERE d.status = 'ready' AND d.kind = ? AND d.license IS ?
            AND (SELECT COUNT(DISTINCT u.platform) FROM uploads u WHERE u.design_id = d.id
                 AND u.status IN ('success','dry_run') AND u.platform IN ({marks})) < ?
            """,
            (kind, license, *platforms, len(platforms))) or 0)

    def recent_phashes(self, days: int = 120) -> list[str]:
        since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
        rows = self.all("SELECT phash FROM designs WHERE phash IS NOT NULL AND created_at >= ?", (since,))
        return [r["phash"] for r in rows]

    def recent_titles(self, days: int = 30) -> list[str]:
        since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
        return [r["title"] for r in self.all("SELECT title FROM designs WHERE title IS NOT NULL AND created_at >= ?", (since,))]


_db: DB | None = None


def get() -> DB:
    global _db
    if _db is None:
        from . import config

        _db = DB(config.load().paths.db)
    return _db
