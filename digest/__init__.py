"""AI Safety Daily Digest pipeline."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
SITE_DIR = PROJECT_ROOT / "site"
TEMPLATE_DIR = PROJECT_ROOT / "templates"
DB_PATH = DATA_DIR / "digest.db"

# Load .env once, for every entry point.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # pragma: no cover
    pass

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/rss+xml, application/atom+xml, application/xml, "
        "text/xml, text/html, */*"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Fetch window: items published within the last N hours are candidates.
WINDOW_HOURS = 36
