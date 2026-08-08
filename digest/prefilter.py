"""Prefilter stage: free, deterministic noise removal before any model call.

Tier 1 sources bypass keyword filtering; tier 2 requires a keyword hit in
title or snippet. Per-source daily caps come from sources.json.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import CONFIG_DIR, WINDOW_HOURS


def load_keywords() -> List[str]:
    cfg = json.loads((CONFIG_DIR / "keywords.json").read_text())
    return [k.lower() for k in cfg["keywords"]]


def keyword_match(title: str, snippet: str, keywords: List[str]) -> Optional[str]:
    text = ("%s %s" % (title, snippet)).lower()
    for kw in keywords:
        if kw in text:
            return kw
    return None


def _recent(row) -> bool:
    """Window on published date, falling back to first_seen (html sources)."""
    stamp = row["published"] or row["first_seen"]
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)


def prefilter(rows, source_caps: Dict[str, int]) -> Tuple[List[dict], List[dict]]:
    """Split unassigned items into (candidates, dropped).

    Each returned dict carries the row fields plus 'matched_keyword' and
    'drop_reason' for --explain output.
    """
    keywords = load_keywords()
    per_source: Dict[str, int] = {}
    candidates, dropped = [], []

    for row in rows:
        item = dict(row)
        item["matched_keyword"] = None
        item["drop_reason"] = None

        if not _recent(row):
            item["drop_reason"] = "outside %dh window" % WINDOW_HOURS
            dropped.append(item)
            continue

        if row["tier"] >= 2:
            kw = keyword_match(row["title"], row["snippet"] or "", keywords)
            if kw is None:
                item["drop_reason"] = "no keyword match (tier 2)"
                dropped.append(item)
                continue
            item["matched_keyword"] = kw

        cap = source_caps.get(row["source"], 6)
        count = per_source.get(row["source"], 0)
        if count >= cap:
            item["drop_reason"] = "over per-source cap (%d)" % cap
            dropped.append(item)
            continue
        per_source[row["source"]] = count + 1
        candidates.append(item)

    return candidates, dropped


def source_caps_from_config(sources: List[dict]) -> Dict[str, int]:
    return {s["name"]: s.get("max_per_day", 6) for s in sources}
