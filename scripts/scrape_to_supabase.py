"""Run the existing scraper and UPSERT results into Supabase.

Env vars required:
  SUPABASE_URL                  e.g. https://xxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY     server-side write key (bypasses RLS)
  FIRECRAWL_API_KEY             passed through to existing scraper
  TIMEZONE                      default 'America/Chicago'

Optional:
  VERCEL_REVALIDATE_URL         if set, POST after a successful run
  REVALIDATE_SECRET             Bearer token for the revalidate endpoint
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, date, time
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

# Make `library_all_events` importable when invoked from repo root or scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from library_all_events import (  # noqa: E402
    collect_all_events,
    failed_sources,
    parse_time_to_sortable,
    zero_event_sources,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scrape_to_supabase")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/Chicago"))
BATCH_SIZE = 200

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _require_env() -> None:
    missing = [k for k, v in {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_KEY,
    }.items() if not v]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")


def _parse_event_date(s: str) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%A, %B %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _compute_start_at(event_date_str: str, event_time_str: str) -> Optional[str]:
    d = _parse_event_date(event_date_str)
    if not d:
        return None
    t = parse_time_to_sortable(event_time_str) or time.min
    return datetime.combine(d, t, tzinfo=TZ).isoformat()


def _to_row(ev: Dict[str, Any], scrape_run_id: str) -> Optional[Dict[str, Any]]:
    title = (ev.get("Title") or "").strip()
    library = (ev.get("Library") or "").strip()
    date_str = ev.get("Date") or ""
    parsed_date = _parse_event_date(date_str)
    if not title or not library or not parsed_date:
        return None
    return {
        "library": library,
        "title": title,
        "event_date": parsed_date.isoformat(),
        "event_time": ev.get("Time") or "All Day",
        "location": ev.get("Location") or None,
        "age_group": ev.get("Age Group") or None,
        "program_type": ev.get("Program Type") or None,
        "description": ev.get("Description") or None,
        "link": ev.get("Link") or None,
        "start_at": _compute_start_at(date_str, ev.get("Time", "")),
        "scrape_run_id": scrape_run_id,
        "last_seen_at": datetime.now(TZ).isoformat(),
    }


def _create_scrape_run() -> str:
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/scrape_runs",
        headers={**HEADERS, "Prefer": "return=representation"},
        json={"status": "running"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]["id"]


def _finish_scrape_run(run_id: str, status: str, event_count: Optional[int], err: Optional[str]) -> None:
    body = {
        "finished_at": datetime.now(TZ).isoformat(),
        "status": status,
        "event_count": event_count,
        "error_message": (err[:1000] if err else None),
    }
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/scrape_runs?id=eq.{run_id}",
        headers=HEADERS,
        json=body,
        timeout=30,
    )
    r.raise_for_status()


def _upsert_events(rows: List[Dict[str, Any]]) -> None:
    """UPSERT against the natural unique constraint, batched."""
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/events?on_conflict=library,title,event_date,event_time",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=chunk,
            timeout=60,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase upsert failed: {r.status_code} {r.text[:500]}")
        log.info("Upserted batch %d-%d", i, i + len(chunk))


def _source_health_note() -> Optional[str]:
    """Summarize broken-looking sources from the scraper's progress state.

    A source that 'succeeds' with 0 events is the silent-breakage signature;
    recorded on the scrape_runs row so it is visible without a schema change."""
    parts = []
    failed = failed_sources()
    zero = zero_event_sources()
    if failed:
        parts.append(f"failed_sources: {', '.join(failed)}")
    if zero:
        parts.append(f"zero_event_sources: {', '.join(zero)}")
    return "; ".join(parts) or None


def _revalidate_vercel() -> None:
    url = os.environ.get("VERCEL_REVALIDATE_URL")
    secret = os.environ.get("REVALIDATE_SECRET")
    if not url or not secret:
        log.info("Revalidate skipped (no VERCEL_REVALIDATE_URL/REVALIDATE_SECRET)")
        return
    r = requests.post(url, headers={"Authorization": f"Bearer {secret}"}, timeout=30)
    log.info("Revalidate %s -> %s", url, r.status_code)


def main() -> None:
    _require_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--start-date", default=None)
    args = parser.parse_args()

    run_id = _create_scrape_run()
    log.info("Created scrape_run %s", run_id)
    try:
        events = asyncio.run(collect_all_events(
            start_date_str=args.start_date,
            days=args.days,
        ))
        rows = [row for row in (_to_row(e, run_id) for e in events) if row is not None]
        log.info("Prepared %d rows (raw events: %d)", len(rows), len(events))
        if rows:
            _upsert_events(rows)
        note = _source_health_note()
        if note:
            log.warning("Source health: %s", note)
        _finish_scrape_run(run_id, "success", len(rows), note)
        _revalidate_vercel()
    except Exception as exc:
        log.exception("Scrape failed")
        try:
            _finish_scrape_run(run_id, "failed", None, repr(exc))
        except Exception:
            log.exception("Failed to mark scrape_run failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
