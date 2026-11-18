"""SQLite state database: the farm's memory (see docs/DESIGN.md §6 and §9).

Google Sheets is only a view. Status, clip IDs and posting history live here, so the farm
can restart at any moment without losing work or posting anything twice.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from core.paths import DB_FILE, DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id     TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    title        TEXT,
    channel      TEXT,
    duration     REAL,
    language     TEXT,
    section      TEXT,
    layout       TEXT,
    clips_wanted INTEGER,
    platforms    TEXT,
    status       TEXT NOT NULL DEFAULT 'new',
    stage_detail TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    sheet_row    INTEGER,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    clip_id      TEXT PRIMARY KEY,
    video_id     TEXT NOT NULL REFERENCES videos(video_id),
    idx          INTEGER NOT NULL,
    start        REAL NOT NULL,
    end          REAL NOT NULL,
    score        REAL,
    kind         TEXT,
    hook         TEXT,
    title        TEXT,
    caption      TEXT,
    hashtags     TEXT,
    description  TEXT,
    music        TEXT,
    layout       TEXT,
    file         TEXT,
    nomusic_file TEXT,
    qc           TEXT,
    status       TEXT NOT NULL DEFAULT 'planned',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE(video_id, idx)
);
CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id         TEXT NOT NULL REFERENCES clips(clip_id),
    platform        TEXT NOT NULL,
    scheduled_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'scheduled',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    external_id     TEXT,
    url             TEXT,
    posted_at       TEXT,
    idempotency_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    level   TEXT NOT NULL,
    item    TEXT,
    stage   TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, scheduled_at);
CREATE TABLE IF NOT EXISTS stats (
    platform     TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    clip_id      TEXT,
    title        TEXT,
    url          TEXT,
    published_at TEXT,
    duration_s   REAL,
    views        INTEGER,
    likes        INTEGER,
    comments     INTEGER,
    shares       INTEGER,
    saves        INTEGER,
    checked_at   TEXT,
    PRIMARY KEY (platform, external_id)
);
CREATE TABLE IF NOT EXISTS stats_history (
    platform    TEXT NOT NULL,
    external_id TEXT NOT NULL,
    checked_at  TEXT NOT NULL,
    views       INTEGER,
    likes       INTEGER,
    comments    INTEGER
);
"""

VIDEO_STATUSES = ("new", "retry", "downloading", "transcribing", "analyzing", "editing", "done", "failed")
CLIP_STATUSES = ("planned", "rendering", "rendered", "qc_failed", "ready", "approved", "rejected",
                 "scheduled", "posting", "posted", "manual_post", "failed", "remake")
POST_STATUSES = ("scheduled", "publishing", "posted", "failed", "needs_manual", "cancelled")

_LOCK = threading.RLock()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path=DB_FILE):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add columns introduced after the first release (old databases keep working)."""
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(clips)")}
        for col in ("post_time", "folder", "creator", "sheet_approve", "sheet_time"):
            if col not in have:
                self.conn.execute(f"ALTER TABLE clips ADD COLUMN {col} TEXT")
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(posts)")}
        for col in ("next_try", "claimed_at", "note"):          # Phase 6
            if col not in have:
                self.conn.execute(f"ALTER TABLE posts ADD COLUMN {col} TEXT")

    @contextmanager
    def tx(self):
        with _LOCK:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # ---------------------------------------------------------------- events
    def event(self, level, item, stage, message):
        with self.tx() as c:
            c.execute("INSERT INTO events(at, level, item, stage, message) VALUES (?,?,?,?,?)",
                      (now(), level, item, stage, str(message)[:2000]))

    # ---------------------------------------------------------------- videos
    def add_video(self, video_id, url, **fields) -> bool:
        """Insert a new video. Returns False if it is already known (no duplicate processing)."""
        with self.tx() as c:
            if c.execute("SELECT 1 FROM videos WHERE video_id=?", (video_id,)).fetchone():
                return False
            cols = {"video_id": video_id, "url": url, "status": "new", "created_at": now(),
                    "updated_at": now(), **fields}
            c.execute(f"INSERT INTO videos({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      list(cols.values()))
            return True

    def update_video(self, video_id, **fields):
        fields["updated_at"] = now()
        with self.tx() as c:
            c.execute(f"UPDATE videos SET {', '.join(f'{k}=?' for k in fields)} WHERE video_id=?",
                      [*fields.values(), video_id])

    def get_video(self, video_id):
        r = self.conn.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
        return dict(r) if r else None

    def videos(self, *statuses):
        q = "SELECT * FROM videos"
        if statuses:
            q += f" WHERE status IN ({','.join('?' * len(statuses))})"
        return [dict(r) for r in self.conn.execute(q + " ORDER BY created_at", statuses)]

    # ---------------------------------------------------------------- clip ids
    def next_clip_id(self) -> str:
        with self.tx() as c:
            c.execute("INSERT OR IGNORE INTO counters(name, value) VALUES ('clip', 0)")
            c.execute("UPDATE counters SET value = value + 1 WHERE name='clip'")
            n = c.execute("SELECT value FROM counters WHERE name='clip'").fetchone()[0]
        return f"CLIP{n:04d}"

    # ---------------------------------------------------------------- clips
    def add_clip(self, video_id, idx, start, end, **fields) -> str:
        """Create the clip once; calling again for the same (video, idx) returns the same ID."""
        r = self.conn.execute("SELECT clip_id FROM clips WHERE video_id=? AND idx=?", (video_id, idx)).fetchone()
        if r:
            return r[0]
        clip_id = self.next_clip_id()
        for k in ("hashtags",):
            if isinstance(fields.get(k), (list, tuple)):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        cols = {"clip_id": clip_id, "video_id": video_id, "idx": idx, "start": start, "end": end,
                "status": "planned", "created_at": now(), "updated_at": now(), **fields}
        with self.tx() as c:
            c.execute(f"INSERT INTO clips({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      list(cols.values()))
        return clip_id

    def update_clip(self, clip_id, **fields):
        for k in ("hashtags", "qc"):
            if isinstance(fields.get(k), (list, tuple, dict)):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        fields["updated_at"] = now()
        with self.tx() as c:
            c.execute(f"UPDATE clips SET {', '.join(f'{k}=?' for k in fields)} WHERE clip_id=?",
                      [*fields.values(), clip_id])

    def clips(self, video_id=None, *statuses):
        q, args = "SELECT * FROM clips WHERE 1=1", []
        if video_id:
            q += " AND video_id=?"
            args.append(video_id)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args += statuses
        return [dict(r) for r in self.conn.execute(q + " ORDER BY video_id, idx", args)]

    def get_clip(self, clip_id):
        r = self.conn.execute("SELECT * FROM clips WHERE clip_id=?", (clip_id,)).fetchone()
        return dict(r) if r else None

    # ---------------------------------------------------------------- posts
    def schedule_post(self, clip_id, platform, scheduled_at) -> bool:
        """One post per clip per platform, ever. Returns False if it already exists."""
        key = f"{clip_id}:{platform}"
        with self.tx() as c:
            try:
                c.execute("INSERT INTO posts(clip_id, platform, scheduled_at, idempotency_key) VALUES (?,?,?,?)",
                          (clip_id, platform, scheduled_at, key))
                return True
            except sqlite3.IntegrityError:
                return False

    def claim_post(self, post_id) -> bool:
        """Atomically move scheduled/failed → publishing. Only one worker can win."""
        with self.tx() as c:
            cur = c.execute("UPDATE posts SET status='publishing', attempts=attempts+1, claimed_at=? "
                            "WHERE id=? AND status IN ('scheduled','failed')", (now(), post_id))
            return cur.rowcount == 1

    def update_post(self, post_id, **fields):
        with self.tx() as c:
            c.execute(f"UPDATE posts SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                      [*fields.values(), post_id])

    def events(self, level=None, limit=50):
        q, args = "SELECT * FROM events", []
        if level:
            q += " WHERE level=?"
            args.append(level)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY id DESC LIMIT ?", [*args, limit])]

    def get_post(self, post_id):
        r = self.conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return dict(r) if r else None

    # ---------------------------------------------------------------- stats (Phase 8)
    def save_stat(self, row: dict):
        cols = ["platform", "external_id", "clip_id", "title", "url", "published_at", "duration_s", "views",
                "likes", "comments", "shares", "saves", "checked_at"]
        vals = [row.get(c) for c in cols]
        with self.tx() as c:
            c.execute(f"INSERT INTO stats({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                      f"ON CONFLICT(platform, external_id) DO UPDATE SET "
                      + ", ".join(f"{k}=COALESCE(excluded.{k}, {k})" for k in cols[2:]), vals)
            c.execute("INSERT INTO stats_history(platform, external_id, checked_at, views, likes, comments) "
                      "VALUES (?,?,?,?,?,?)", [row["platform"], row["external_id"], row.get("checked_at"),
                                               row.get("views"), row.get("likes"), row.get("comments")])

    def stats(self, platform=None):
        q, a = "SELECT * FROM stats", []
        if platform:
            q, a = q + " WHERE platform=?", [platform]
        return [dict(r) for r in self.conn.execute(q + " ORDER BY COALESCE(views, -1) DESC", a)]

    def posts(self, *statuses):
        q = "SELECT * FROM posts"
        if statuses:
            q += f" WHERE status IN ({','.join('?' * len(statuses))})"
        return [dict(r) for r in self.conn.execute(q + " ORDER BY scheduled_at", statuses)]


_local = threading.local()


def get_db() -> DB:
    """One connection per thread (the Sheets sync runs in its own thread next to the video worker)."""
    db = getattr(_local, "db", None)
    if db is None:
        db = _local.db = DB()
    return db
