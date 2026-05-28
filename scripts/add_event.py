"""Manually add (or update) events in Supabase.

Usage:
  # Single event via CLI flags
  python scripts/add_event.py \
      --library "Manual" \
      --title "Local Author Reading" \
      --date 2026-06-12 \
      --time "6:30 PM" \
      --location "Skokie Public Library" \
      --age-group "Adults" \
      --program-type "Books" \
      --description "An evening with author Jane Doe." \
      --link "https://example.org/event"

  # Batch — pass a JSON array on stdin
  cat events.json | python scripts/add_event.py --json -

  # Or a JSON file
  python scripts/add_event.py --json events.json

JSON shape (one object or an array of them) — same keys as the scraper:
  {
    "Library": "Manual",
    "Title": "...",
    "Date": "2026-06-12",
    "Time": "6:30 PM",
    "Location": "...",
    "Age Group": "...",
    "Program Type": "...",
    "Description": "...",
    "Link": "..."
  }

Env vars required:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

Optional:
  VERCEL_REVALIDATE_URL, REVALIDATE_SECRET  -> POST after a successful insert
  TIMEZONE                                  -> default 'America/Chicago'
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(_ROOT / ".env")
_load_dotenv(_ROOT / ".env.local")

from scripts.scrape_to_supabase import _to_row, _require_env, HEADERS, SUPABASE_URL  # type: ignore  # noqa: E402

log = logging.getLogger("add_event")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/Chicago"))


def _create_manual_run() -> str:
    body = {"status": "success", "finished_at": datetime.now(TZ).isoformat(), "event_count": 0}
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/scrape_runs",
        headers={**HEADERS, "Prefer": "return=representation"},
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]["id"]


def _upsert(rows: List[Dict[str, Any]]) -> int:
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/events?on_conflict=library,title,event_date,event_time",
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        json=rows,
        timeout=60,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Supabase upsert failed: {r.status_code} {r.text[:500]}")
    return len(r.json())


def _revalidate() -> None:
    url = os.environ.get("VERCEL_REVALIDATE_URL")
    secret = os.environ.get("REVALIDATE_SECRET")
    if not url or not secret:
        return
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {secret}"}, timeout=30)
        log.info("Revalidate %s -> %s", url, r.status_code)
    except Exception:
        log.exception("Revalidate failed (non-fatal)")


def _events_from_args(args: argparse.Namespace) -> List[Dict[str, Any]]:
    if args.json is not None:
        raw = sys.stdin.read() if args.json == "-" else Path(args.json).read_text()
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    required = {"library": args.library, "title": args.title, "date": args.date, "time": args.time}
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise SystemExit(f"Missing required flags for single-event mode: --{', --'.join(missing)}")
    return [{
        "Library": args.library,
        "Title": args.title,
        "Date": args.date,
        "Time": args.time,
        "Location": args.location or "",
        "Age Group": args.age_group or "",
        "Program Type": args.program_type or "",
        "Description": args.description or "",
        "Link": args.link or "",
    }]


def main() -> None:
    parser = argparse.ArgumentParser(description="Manually add events to Supabase.")
    parser.add_argument("--library")
    parser.add_argument("--title")
    parser.add_argument("--date", help="e.g. 2026-06-12")
    parser.add_argument("--time", help='e.g. "6:30 PM" or "All Day"')
    parser.add_argument("--location")
    parser.add_argument("--age-group")
    parser.add_argument("--program-type")
    parser.add_argument("--description")
    parser.add_argument("--link")
    parser.add_argument("--json", help="Path to JSON file, or '-' to read from stdin")
    parser.add_argument("--dry-run", action="store_true", help="Print rows, don't upsert")
    parser.add_argument("--no-revalidate", action="store_true")
    args = parser.parse_args()

    _require_env()
    events = _events_from_args(args)
    run_id = "00000000-0000-0000-0000-000000000000"
    if not args.dry_run:
        run_id = _create_manual_run()

    rows = [r for r in (_to_row(e, run_id) for e in events) if r is not None]
    invalid = len(events) - len(rows)
    if invalid:
        log.warning("Skipped %d event(s) with missing/invalid required fields", invalid)
    if not rows:
        raise SystemExit("No valid events to insert.")

    if args.dry_run:
        print(json.dumps(rows, indent=2, default=str))
        return

    count = _upsert(rows)
    log.info("Upserted %d row(s) (run_id=%s)", count, run_id)
    if not args.no_revalidate:
        _revalidate()


if __name__ == "__main__":
    main()
