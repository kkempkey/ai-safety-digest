"""Fetch stage: RSS and HTML adapters producing normalized item dicts."""

import calendar
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

from . import BROWSER_HEADERS, CONFIG_DIR, WINDOW_HOURS
from . import store


def load_sources() -> List[dict]:
    cfg = json.loads((CONFIG_DIR / "sources.json").read_text())
    return [s for s in cfg["sources"] if s.get("enabled", True)]


def save_sources(sources: List[dict]) -> None:
    cfg = json.loads((CONFIG_DIR / "sources.json").read_text())
    cfg["sources"] = sources
    (CONFIG_DIR / "sources.json").write_text(json.dumps(cfg, indent=2) + "\n")


def http_get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def strip_html(text: str, limit: int = 600) -> str:
    if not text:
        return ""
    clean = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    clean = re.sub(r"\s+", " ", clean)
    return clean[:limit]


def unwrap_google_news(url: str, title: str) -> Tuple[str, str]:
    """Google News RSS wraps article URLs and appends ' - Publisher' to titles."""
    p = urlparse(url)
    if "news.google.com" in p.netloc:
        qs = parse_qs(p.query)
        if "url" in qs:
            url = qs["url"][0]
    title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip() or title
    return url, title


def parse_date(entry) -> Optional[str]:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.fromtimestamp(
                    calendar.timegm(t), tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, OverflowError):
                pass
    return None


def within_window(published: Optional[str], window_hours: int = WINDOW_HOURS) -> bool:
    if published is None:
        return True  # dateless items pass; first_seen handles recency
    try:
        dt = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=window_hours)


UNDEFINED_ENTITY = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)\w+;")


def fetch_rss(source: dict, check_window: bool = True) -> List[dict]:
    raw = http_get(source["url"])
    feed = feedparser.parse(raw)
    if feed.bozo and not feed.entries:
        # Some feeds (e.g. Brookings) ship undefined HTML entities that kill
        # the XML parser; replace them with spaces and retry once.
        sanitized = UNDEFINED_ENTITY.sub(" ", raw.decode("utf8", "replace"))
        feed = feedparser.parse(sanitized)
    if feed.bozo and not feed.entries:
        raise ValueError("feed unparseable: %s" % feed.get("bozo_exception"))
    items = []
    for e in feed.entries:
        url = e.get("link") or ""
        title = (e.get("title") or "").strip()
        if not url or not title:
            continue
        url, title = unwrap_google_news(url, title)
        published = parse_date(e)
        if check_window and not within_window(published):
            continue
        snippet = strip_html(e.get("summary") or e.get("description") or "")
        items.append({
            "url": url,
            "title": title,
            "source": source["name"],
            "tier": source["tier"],
            "theme_hint": source.get("theme_hint"),
            "published": published,
            "snippet": snippet,
        })
    return items


def fetch_html(source: dict) -> List[dict]:
    """Scrape an index page for post links matching link_pattern.

    No reliable publish dates here — first_seen recency (in the store) does the
    windowing. Run `digest bootstrap` after adding an html source so the back
    catalogue is swallowed silently rather than flooding the next edition.
    """
    base = source["url"]
    raw = http_get(base)
    soup = BeautifulSoup(raw, "html.parser")
    pattern = re.compile(source["link_pattern"])
    exclude = re.compile(source["exclude_pattern"]) if source.get("exclude_pattern") else None

    seen, items = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not (pattern.search(href)):
            continue
        if exclude and exclude.search(href):
            continue
        full = urljoin(base, href)
        if full.rstrip("/") == base.rstrip("/"):
            continue
        if full in seen:
            continue
        seen.add(full)
        title = a.get_text(" ", strip=True)
        # anchor text is often a whole card; keep it plausible-title-sized
        if not title or len(title) > 200:
            title = href.rstrip("/").split("/")[-1].replace("-", " ").title()
        items.append({
            "url": full,
            "title": title,
            "source": source["name"],
            "tier": source["tier"],
            "theme_hint": source.get("theme_hint"),
            "published": None,
            "snippet": "",
        })
    return items


def fetch_source(source: dict, check_window: bool = True) -> List[dict]:
    """check_window=False validates feed health regardless of recency —
    used by `sources validate` so a quiet weekend doesn't read as failure."""
    if source.get("adapter", "rss") == "html":
        time.sleep(1)  # be polite to scraped hosts
        return fetch_html(source)
    return fetch_rss(source, check_window=check_window)


def wait_for_network(max_wait_seconds: int = 7200, log=print) -> bool:
    """Block until the internet is actually reachable (DNS + HTTP), up to a cap.

    The morning run can fire the moment the Mac wakes, before Wi-Fi is up —
    without this gate a whole run's worth of sources fails with DNS errors.
    Per Kristina: if the internet isn't connected, run when it is — so wait
    for hours if needed, not minutes; the cap only prevents an infinite hang.
    """
    probes = ("https://news.google.com/", "https://www.google.com/")
    deadline = time.time() + max_wait_seconds
    attempt = 0
    while time.time() < deadline:
        for probe in probes:
            try:
                http_get(probe, timeout=10)
                if attempt:
                    log("  network up after %d probe attempts" % attempt)
                return True
            except Exception:  # noqa: BLE001
                pass
        attempt += 1
        if attempt == 1:
            log("  network not ready; waiting (up to %ds)..." % max_wait_seconds)
        time.sleep(10)
    log("  WARN network still unreachable after %ds" % max_wait_seconds)
    return False


def fetch_all(conn, run_date: str, log=print) -> Dict[str, int]:
    """Fetch every enabled source.

    Per-source failure never fails the run, but a *systemic* failure does:
    if more than 40% of sources error (network outage at wake, DNS down),
    raises RuntimeError so no thin edition is recorded and the 08:15 retry
    rebuilds from scratch.

    Returns {source_name: new_item_count}.
    """
    wait_for_network(log=log)
    results: Dict[str, int] = {}
    failed = 0
    sources = load_sources()
    for source in sources:
        name = source["name"]
        try:
            items = fetch_source(source)
            new = sum(1 for it in items if store.insert_item(conn, it))
            conn.commit()
            store.record_source_health(conn, name, run_date, True, new)
            results[name] = new
            log("  %-32s fetched=%-4d new=%d" % (name, len(items), new))
        except Exception as exc:  # noqa: BLE001 — isolation is the point
            failed += 1
            store.record_source_health(conn, name, run_date, False, 0, str(exc)[:300])
            results[name] = 0
            log("  WARN %-27s %s: %s" % (name, type(exc).__name__, str(exc)[:120]))
    conn.commit()
    if sources and failed > 0.4 * len(sources):
        raise RuntimeError(
            "systemic fetch failure: %d/%d sources errored — likely a network "
            "outage; aborting so the retry run rebuilds with full coverage"
            % (failed, len(sources)))
    return results
