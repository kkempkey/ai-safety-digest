"""Render stage: edition payload → website HTML (site/) and email HTML."""

import shutil
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import SITE_DIR, TEMPLATE_DIR

THEME_ORDER = ["research", "policy", "industry", "community"]
THEME_LABELS = {
    "research": "Research",
    "policy": "Policy",
    "industry": "Industry",
    "community": "Community",
}


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["relative_time"] = relative_time
    return env


def relative_time(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    delta = datetime.now(timezone.utc) - dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return "%dh ago" % hours
    return "%dd ago" % (hours // 24)


def build_context(payload: dict, date: str, stale: Optional[List[str]] = None) -> dict:
    items = payload["items"]
    top_ids = payload.get("top_story_ids", [])
    top = [items[i] for i in top_ids if 0 <= i < len(items)]
    top_hashes = {t["url_hash"] for t in top}
    sections = []
    for theme in THEME_ORDER:
        themed = [it for it in items
                  if it["theme"] == theme and it["url_hash"] not in top_hashes]
        themed.sort(key=lambda it: -it["significance"])
        if themed:
            # key is "entries", not "items" — dict.items shadows it in Jinja
            sections.append({"key": theme, "label": THEME_LABELS[theme], "entries": themed})
    dt = datetime.strptime(date, "%Y-%m-%d")
    return {
        "date": date,
        "date_human": dt.strftime("%A, %B %-d, %Y"),
        "edition_title": payload.get("edition_title") or "AI Safety Digest",
        "intro": payload.get("intro", ""),
        "top_stories": top,
        "sections": sections,
        "item_count": len(items),
        "stale_sources": stale or [],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def render_site(payload: dict, date: str, editions: List[Tuple[str, str, int]],
                stale: Optional[List[str]] = None) -> None:
    """Write editions/DATE.html, index.html, archive.html, and the stylesheet.

    `editions` is [(date, title, item_count)] newest first, for the archive.
    """
    env = _env()
    ctx = build_context(payload, date, stale)

    (SITE_DIR / "editions").mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "assets").mkdir(parents=True, exist_ok=True)

    edition_tpl = env.get_template("edition.html")
    (SITE_DIR / "editions" / ("%s.html" % date)).write_text(
        edition_tpl.render(depth="../", is_index=False, **ctx))
    (SITE_DIR / "index.html").write_text(
        edition_tpl.render(depth="", is_index=True, **ctx))

    archive_tpl = env.get_template("archive.html")
    (SITE_DIR / "archive.html").write_text(
        archive_tpl.render(depth="", editions=editions))

    shutil.copyfile(str(TEMPLATE_DIR / "style.css"),
                    str(SITE_DIR / "assets" / "style.css"))


def render_email(payload: dict, date: str, stale: Optional[List[str]] = None) -> Tuple[str, str]:
    """Return (html_body, text_body) for the daily email."""
    env = _env()
    ctx = build_context(payload, date, stale)
    html = env.get_template("email.html").render(**ctx)

    lines = ["AI SAFETY DIGEST — %s" % ctx["date_human"], ""]
    if ctx["intro"]:
        lines += [ctx["intro"], ""]
    if ctx["top_stories"]:
        lines.append("TOP STORIES")
        for it in ctx["top_stories"]:
            lines += ["* %s" % it["headline"],
                      "  %s" % it["summary"],
                      "  Why it matters: %s" % it["why_it_matters"],
                      "  %s" % it["url"], ""]
    for sec in ctx["sections"]:
        lines.append(sec["label"].upper())
        for it in sec["entries"]:
            lines += ["* %s (%s)" % (it["headline"], it["source"]),
                      "  %s" % it["summary"],
                      "  %s" % it["url"], ""]
    if ctx["stale_sources"]:
        lines.append("Stale sources (no items 7+ days): %s"
                     % ", ".join(ctx["stale_sources"]))
    return html, "\n".join(lines)
