"""CLI entry point: python -m digest <command>."""

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import DATA_DIR, SITE_DIR
from . import store, prefilter as pf


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def date_label() -> str:
    return datetime.now().strftime("%A, %B %d, %Y")


# ---------------------------------------------------------------- pipeline

def get_candidates(conn, log=print):
    from .fetch import load_sources
    rows = store.unassigned_items(conn)
    caps = pf.source_caps_from_config(load_sources())
    return pf.prefilter(rows, caps)


def cmd_fetch(args):
    from .fetch import fetch_all
    conn = store.connect()
    print("Fetching sources...")
    results = fetch_all(conn, today())
    total = sum(results.values())
    print("Total new items: %d" % total)
    if args.show_candidates:
        candidates, _ = get_candidates(conn)
        print("\nCandidates after prefilter (%d):" % len(candidates))
        for c in candidates:
            kw = (" [kw: %s]" % c["matched_keyword"]) if c["matched_keyword"] else ""
            print("  %-28s %s%s" % (c["source"][:28], c["title"][:80], kw))
    return 0


def cmd_prefilter(args):
    conn = store.connect()
    candidates, dropped = get_candidates(conn)
    print("KEPT (%d):" % len(candidates))
    for c in candidates:
        kw = (" [kw: %s]" % c["matched_keyword"]) if c["matched_keyword"] else " [tier 1]"
        print("  %-28s %s%s" % (c["source"][:28], c["title"][:78], kw))
    if args.explain:
        print("\nDROPPED (%d):" % len(dropped))
        for d in dropped:
            print("  %-28s %-60s -- %s"
                  % (d["source"][:28], d["title"][:60], d["drop_reason"]))
    return 0


def cmd_triage(args):
    from .triage import triage
    conn = store.connect()
    candidates, _ = get_candidates(conn)
    if not candidates:
        print("No candidates to triage.")
        return 0
    try:
        kept = triage(candidates)
    except Exception as exc:  # noqa: BLE001 — fall back per plan
        print("  WARN triage failed (%s); passing top 40 prefiltered items" % exc)
        kept = candidates[:40]
    Path(args.out).write_text(json.dumps(kept, indent=2))
    print("Wrote %d items to %s" % (len(kept), args.out))
    return 0


def cmd_curate(args):
    from .curate import curate
    candidates = json.loads(Path(args.input).read_text())
    payload = curate(candidates, date_label())
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print("Curated %d items → %s" % (len(payload["items"]), args.out))
    return 0


def cmd_render(args):
    from .render import render_site
    conn = store.connect()
    payload = json.loads(Path(args.input).read_text())
    date = args.date or today()
    editions = [(r["date"], r["title"] or "AI Safety Digest", r["item_count"])
                for r in store.list_editions(conn)]
    if date not in [e[0] for e in editions]:
        editions.insert(0, (date, payload.get("edition_title", ""), len(payload["items"])))
    render_site(payload, date, editions, store.stale_sources(conn))
    print("Rendered site → %s" % (SITE_DIR / "index.html"))
    return 0


def cmd_email(args):
    from .render import render_email
    from . import mailer
    conn = store.connect()
    payload = json.loads(Path(args.input).read_text())
    date = args.date or today()
    html, text = render_email(payload, date, store.stale_sources(conn))
    subject = "AI Safety Digest — %s · %d items" % (
        datetime.strptime(date, "%Y-%m-%d").strftime("%a %b %d"),
        len(payload["items"]))
    if args.test:
        subject = "[TEST] " + subject
    mailer.send(subject, html, text)
    return 0


def cmd_run(args):
    """Full pipeline. Idempotent per day: exits 0 immediately if today's
    edition already exists (that is what makes the 8:15 retry free)."""
    from .fetch import fetch_all
    from .triage import triage
    from .curate import curate
    from .render import render_site, render_email
    from . import mailer

    conn = store.connect()
    date = today()
    if store.edition_exists(conn, date) and not args.force:
        print("Edition for %s already exists; nothing to do." % date)
        return 0

    log_lines = []

    def log(msg):
        print(msg)
        log_lines.append(str(msg))

    try:
        log("[1/6] fetch")
        fetch_all(conn, date, log=log)

        log("[2/6] prefilter")
        candidates, dropped = get_candidates(conn, log=log)
        log("  %d candidates, %d dropped" % (len(candidates), len(dropped)))

        log("[3/6] triage")
        if candidates:
            try:
                candidates = triage(candidates, log=log)
            except Exception as exc:  # noqa: BLE001
                log("  WARN triage failed (%s); using top 40 prefiltered" % exc)
                candidates = candidates[:40]

        log("[4/6] curate")
        payload = curate(candidates, date_label(), log=log)
        items = payload["items"]
        log("  %d items included" % len(items))

        # Record the edition and mark every *candidate* consumed (including
        # ones Claude excluded) so tomorrow's run starts clean.
        store.record_edition(conn, date, payload["edition_title"],
                             payload["intro"], len(items), payload)
        store.assign_edition(conn, [c["url_hash"] for c in candidates], date)
        conn.commit()

        log("[5/6] render")
        editions = [(r["date"], r["title"] or "AI Safety Digest", r["item_count"])
                    for r in store.list_editions(conn)]
        stale = store.stale_sources(conn)
        render_site(payload, date, editions, stale)

        log("[6/6] email")
        html, text = render_email(payload, date, stale)
        subject = "AI Safety Digest — %s · %d items" % (
            datetime.now().strftime("%a %b %d"), len(items))
        if args.no_email:
            log("  --no-email: skipping send")
        else:
            mailer.send(subject, html, text, log=log)
            store.mark_emailed(conn, date)
            conn.commit()
        log("Done: %s (%d items)" % (payload["edition_title"], len(items)))
        return 0
    except Exception as exc:  # noqa: BLE001
        err = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(err, file=sys.stderr)
        if not args.no_email:
            mailer.send_failure_notice(str(exc), "\n".join(log_lines[-25:]))
        return 1


def cmd_bootstrap(args):
    """Fetch everything and assign all current items to a sentinel edition so
    the next real run only sees genuinely new posts. Run once at install, and
    again after adding an html-adapter source."""
    from .fetch import fetch_all
    conn = store.connect()
    print("Bootstrapping (swallowing current back catalogue)...")
    fetch_all(conn, today())
    rows = store.unassigned_items(conn)
    store.assign_edition(conn, [r["url_hash"] for r in rows], "bootstrap")
    conn.commit()
    print("Marked %d existing items as consumed. Next run starts clean." % len(rows))
    return 0


# ---------------------------------------------------------------- sources

def _print_source_table(sources, health):
    print("%-34s %-4s %-4s %-9s %s" % ("NAME", "TIER", "CAP", "ADAPTER", "STATUS"))
    for s in sources:
        h = health.get(s["name"])
        if not s.get("enabled", True):
            status = "disabled"
        elif h is None:
            status = "never fetched"
        elif h["ok"]:
            status = "ok, %d new on %s" % (h["item_count"], h["run_date"])
        else:
            status = "ERROR: %s" % (h["error"] or "")[:50]
        print("%-34s %-4s %-4s %-9s %s" % (
            s["name"][:34], s["tier"], s.get("max_per_day", 6),
            s.get("adapter", "rss"), status))


def cmd_sources(args):
    from . import fetch as fetch_mod
    cfg_sources = json.loads(
        (Path(__file__).resolve().parent.parent / "config" / "sources.json").read_text()
    )["sources"]
    conn = store.connect()

    if args.action == "list":
        rows = conn.execute(
            """SELECT * FROM source_health
               WHERE (source, run_date) IN
                 (SELECT source, MAX(run_date) FROM source_health GROUP BY source)"""
        ).fetchall()
        health = {r["source"]: r for r in rows}
        _print_source_table(cfg_sources, health)
        return 0

    if args.action == "validate":
        failures = 0
        for s in cfg_sources:
            if not s.get("enabled", True):
                print("  skip %-30s (disabled)" % s["name"])
                continue
            try:
                items = fetch_mod.fetch_source(s, check_window=False)
                mark = "OK " if items else "!! "
                if not items:
                    failures += 1
                print("  %s %-30s %d items" % (mark, s["name"], len(items)))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("  !! %-30s %s: %s" % (s["name"], type(exc).__name__, str(exc)[:90]))
        print("\n%d source(s) returned nothing or failed." % failures)
        return 1 if failures else 0

    if args.action in ("enable", "disable", "remove"):
        name = args.target
        match = [s for s in cfg_sources if s["name"].lower() == name.lower()]
        if not match:
            print("No source named %r. Run: python -m digest sources list" % name)
            return 1
        if args.action == "remove":
            cfg_sources = [s for s in cfg_sources if s["name"].lower() != name.lower()]
        else:
            match[0]["enabled"] = (args.action == "enable")
        fetch_mod.save_sources(cfg_sources)
        print("%s: %s" % (args.action, match[0]["name"]))
        return 0

    if args.action == "test":
        name = args.target
        match = [s for s in cfg_sources if s["name"].lower() == name.lower()]
        if not match:
            print("No source named %r." % name)
            return 1
        items = fetch_mod.fetch_source(match[0])
        print("%s → %d items in window:" % (match[0]["name"], len(items)))
        for it in items[:10]:
            print("  %s  %s" % (it["published"] or "(no date)", it["title"][:85]))
        return 0

    if args.action == "discover":
        base = args.target.rstrip("/")
        candidates = [base + p for p in
                      ("/feed", "/rss.xml", "/atom.xml", "/index.xml", "/feed.xml", "/rss")]
        # also look for <link rel=alternate> on the homepage
        try:
            from bs4 import BeautifulSoup
            html = fetch_mod.http_get(base)
            soup = BeautifulSoup(html, "html.parser")
            for link in soup.find_all("link", rel="alternate"):
                href = link.get("href")
                if href and ("rss" in (link.get("type") or "") or "atom" in (link.get("type") or "")):
                    from urllib.parse import urljoin
                    candidates.insert(0, urljoin(base, href))
        except Exception:
            pass
        import feedparser
        found = False
        for url in candidates:
            try:
                feed = feedparser.parse(fetch_mod.http_get(url))
                if feed.entries:
                    print("  FOUND %-50s %d entries  (%r)"
                          % (url, len(feed.entries), feed.feed.get("title", "")))
                    found = True
            except Exception:
                continue
        if not found:
            print("No feed found at the usual paths. The site may need the html adapter.")
        return 0

    if args.action == "add":
        import feedparser
        url = args.url
        feed = feedparser.parse(fetch_mod.http_get(url))
        if not feed.entries:
            print("That URL did not parse as a feed with entries. "
                  "Try: python -m digest sources discover <homepage>")
            return 1
        name = args.name or feed.feed.get("title", url)[:60]
        print("Detected: %d entries, feed title %r" % (len(feed.entries), name))
        for e in feed.entries[:3]:
            print("  recent: %s" % (e.get("title", "")[:85]))
        entry = {
            "name": name,
            "url": url,
            "adapter": "rss",
            "tier": args.tier,
            "theme_hint": args.theme,
            "max_per_day": args.cap,
        }
        if not args.yes:
            ans = input("Add %r as tier %d, theme %r, cap %d/day? [y/N] "
                        % (name, args.tier, args.theme, args.cap))
            if ans.strip().lower() != "y":
                print("Aborted.")
                return 1
        cfg_sources.append(entry)
        fetch_mod.save_sources(cfg_sources)
        print("Added. New items appear from the next run "
              "(run 'python -m digest bootstrap' to swallow its back catalogue first).")
        return 0

    print("Unknown sources action %r" % args.action)
    return 1


# ---------------------------------------------------------------- parser

def main(argv=None):
    p = argparse.ArgumentParser(prog="digest", description="AI Safety Daily Digest")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("fetch", help="fetch all sources into the store")
    sp.add_argument("--show-candidates", action="store_true")
    sp.set_defaults(fn=cmd_fetch)

    sp = sub.add_parser("prefilter", help="show keyword-filter results")
    sp.add_argument("--explain", action="store_true", help="also show dropped items")
    sp.set_defaults(fn=cmd_prefilter)

    sp = sub.add_parser("triage", help="Haiku relevance pass")
    sp.add_argument("--out", default=str(DATA_DIR / "triaged.json"))
    sp.set_defaults(fn=cmd_triage)

    sp = sub.add_parser("curate", help="Opus editorial pass")
    sp.add_argument("--input", required=True)
    sp.add_argument("--out", default=str(DATA_DIR / "curated.json"))
    sp.set_defaults(fn=cmd_curate)

    sp = sub.add_parser("render", help="render site from curated JSON")
    sp.add_argument("--input", required=True)
    sp.add_argument("--date")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("email", help="send digest email from curated JSON")
    sp.add_argument("--input", required=True)
    sp.add_argument("--date")
    sp.add_argument("--test", action="store_true", help="prefix subject with [TEST]")
    sp.set_defaults(fn=cmd_email)

    sp = sub.add_parser("run", help="full pipeline (idempotent per day)")
    sp.add_argument("--force", action="store_true", help="rebuild even if today exists")
    sp.add_argument("--no-email", action="store_true")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("bootstrap", help="swallow current back catalogue")
    sp.set_defaults(fn=cmd_bootstrap)

    sp = sub.add_parser("sources", help="manage the source registry")
    ssub = sp.add_subparsers(dest="action", required=True)
    ssub.add_parser("list")
    ssub.add_parser("validate")
    for act in ("enable", "disable", "remove", "test", "discover"):
        a = ssub.add_parser(act)
        a.add_argument("target")
    a = ssub.add_parser("add")
    a.add_argument("--url", required=True)
    a.add_argument("--name")
    a.add_argument("--tier", type=int, default=2, choices=(1, 2))
    a.add_argument("--theme", default="industry",
                   choices=("research", "policy", "industry", "community"))
    a.add_argument("--cap", type=int, default=6)
    a.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_sources)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
