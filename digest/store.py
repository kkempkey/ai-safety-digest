"""SQLite state: item dedupe, edition records, per-source health."""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse, urlencode

from . import DB_PATH, DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url_hash    TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    source      TEXT NOT NULL,
    tier        INTEGER NOT NULL,
    theme_hint  TEXT,
    published   TEXT,            -- ISO8601 UTC, may be null for html sources
    first_seen  TEXT NOT NULL,   -- ISO8601 UTC
    snippet     TEXT,
    edition     TEXT             -- YYYY-MM-DD once assigned to an edition, else NULL
);
CREATE TABLE IF NOT EXISTS editions (
    date        TEXT PRIMARY KEY,   -- YYYY-MM-DD
    created_at  TEXT NOT NULL,
    title       TEXT,
    intro       TEXT,
    item_count  INTEGER NOT NULL,
    emailed_at  TEXT,
    payload     TEXT NOT NULL       -- full curated JSON for re-rendering
);
CREATE TABLE IF NOT EXISTS source_health (
    source      TEXT NOT NULL,
    run_date    TEXT NOT NULL,      -- YYYY-MM-DD
    ok          INTEGER NOT NULL,
    item_count  INTEGER NOT NULL,
    error       TEXT,
    PRIMARY KEY (source, run_date)
);
"""

TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_)")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_url(url: str) -> str:
    """Canonical form used for dedupe: strip tracking params, fragment, trailing slash."""
    p = urlparse(url.strip())
    query = ""
    if p.query:
        kept = {
            k: v
            for k, v in parse_qs(p.query, keep_blank_values=False).items()
            if not TRACKING_PARAMS.match(k.lower())
        }
        if kept:
            query = urlencode(sorted(kept.items()), doseq=True)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf8")).hexdigest()[:24]


def insert_item(conn, item: dict) -> bool:
    """Insert if unseen. Returns True if the item is new."""
    h = url_hash(item["url"])
    cur = conn.execute(
        """INSERT OR IGNORE INTO items
           (url_hash, url, title, source, tier, theme_hint, published, first_seen, snippet)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            h,
            item["url"],
            item["title"],
            item["source"],
            item["tier"],
            item.get("theme_hint"),
            item.get("published"),
            now_iso(),
            item.get("snippet", ""),
        ),
    )
    return cur.rowcount > 0


def unassigned_items(conn) -> List[sqlite3.Row]:
    """Items fetched but not yet placed in any edition."""
    return conn.execute(
        "SELECT * FROM items WHERE edition IS NULL ORDER BY COALESCE(published, first_seen) DESC"
    ).fetchall()


def assign_edition(conn, url_hashes: List[str], date: str) -> None:
    conn.executemany(
        "UPDATE items SET edition = ? WHERE url_hash = ?",
        [(date, h) for h in url_hashes],
    )


def record_edition(conn, date: str, title: str, intro: str, item_count: int, payload: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO editions (date, created_at, title, intro, item_count, payload)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (date, now_iso(), title, intro, item_count, json.dumps(payload)),
    )


def edition_exists(conn, date: str) -> bool:
    return conn.execute("SELECT 1 FROM editions WHERE date = ?", (date,)).fetchone() is not None


def mark_emailed(conn, date: str) -> None:
    conn.execute("UPDATE editions SET emailed_at = ? WHERE date = ?", (now_iso(), date))


def list_editions(conn) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM editions ORDER BY date DESC").fetchall()


def get_edition(conn, date: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM editions WHERE date = ?", (date,)).fetchone()


def record_source_health(conn, source: str, run_date: str, ok: bool,
                         item_count: int, error: Optional[str] = None) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO source_health (source, run_date, ok, item_count, error)
           VALUES (?, ?, ?, ?, ?)""",
        (source, run_date, 1 if ok else 0, item_count, error),
    )


def stale_sources(conn, days: int = 7) -> List[str]:
    """Sources with zero items on every one of their last `days` recorded runs."""
    rows = conn.execute(
        """SELECT source,
                  COUNT(*) AS runs,
                  SUM(item_count) AS total
           FROM (SELECT source, run_date, item_count,
                        ROW_NUMBER() OVER (PARTITION BY source ORDER BY run_date DESC) AS rn
                 FROM source_health)
           WHERE rn <= ?
           GROUP BY source
           HAVING runs >= ? AND total = 0""",
        (days, days),
    ).fetchall()
    return [r["source"] for r in rows]
