"""
Standalone scraper for the Glencoe Park District calendar.

Fetches month-view HTML (e.g., https://glencoeparkdistrict.com/calendar/month/12/2025/)
and emits a JSON list of events so we can validate parsing before wiring it into the
main aggregator.
"""

import argparse
import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup


BASE_URL = "https://glencoeparkdistrict.com"
USER_AGENT = "LibraryScraper/0.1 (+https://github.com/)"
REQUEST_DELAY = 0.35  # polite pause between month fetches
CALENDAR_PATH = "/calendar/month/{month}/{year}/"

logger = logging.getLogger(__name__)


def clean_text(value: Optional[str]) -> str:
    """Normalize whitespace and drop stray artifacts."""
    if not value:
        return ""
    text = value.replace("\xa0", " ").replace("\u200b", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def make_soup(html: str) -> BeautifulSoup:
    """Prefer lxml parser; fall back to built-in if unavailable."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def month_iter(start: date, end: date) -> List[Tuple[int, int]]:
    """Yield (year, month) pairs that cover the inclusive [start, end] range."""
    months: List[Tuple[int, int]] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        # Advance to first day of next month
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


async def fetch_month_html(session: aiohttp.ClientSession, year: int, month: int) -> str:
    """Download a single month page."""
    url = urljoin(BASE_URL, CALENDAR_PATH.format(month=month, year=year))
    headers = {"User-Agent": USER_AGENT}
    async with session.get(url, headers=headers, timeout=30) as resp:
        resp.raise_for_status()
        return await resp.text()


def _is_canceled(anchor) -> bool:
    """Check if the anchor is wrapped by a line-through container."""
    for parent in anchor.parents:
        style = getattr(parent, "attrs", {}).get("style", "") if parent else ""
        if style and "line-through" in style:
            return True
    return False


def _time_and_title_from_anchor(anchor) -> Tuple[str, str]:
    """
    Split the anchor text into (time, title).
    Expected structure: <a><strong>8:30 AM:</strong><br>Title</a>
    """
    parts = [clean_text(p) for p in anchor.stripped_strings if clean_text(p)]
    if not parts:
        return "", ""

    time_text = ""
    title = ""
    # If the first part looks like a time, treat it as such
    if re.match(r"^\d{1,2}(:\d{2})?\s*[AP]M:?$", parts[0], re.IGNORECASE):
        time_text = parts[0].rstrip(":")
        title = parts[1] if len(parts) > 1 else ""
    else:
        title = parts[-1]
    return time_text, title


def parse_month_page(html: str) -> List[Dict[str, str]]:
    """Parse a month page into structured event dictionaries."""
    soup = make_soup(html)
    calendar = soup.select_one("div.calendar")
    if not calendar:
        return []

    try:
        month = int(calendar.get("data-month", "0"))
        year = int(calendar.get("data-year", "0"))
    except ValueError:
        return []

    events: List[Dict[str, str]] = []

    # Both "day" and "currentdaydisplay" contain event boxes
    for cell in soup.select("table.calendar td.day, table.calendar td.currentdaydisplay"):
        day_span = cell.select_one("span.cal_num")
        if not day_span:
            continue
        try:
            day = int(day_span.get_text(strip=True))
        except ValueError:
            continue

        try:
            event_date = date(year, month, day)
        except ValueError:
            continue

        for anchor in cell.select("a.event"):
            time_text, title = _time_and_title_from_anchor(anchor)
            title = clean_text(title)
            if not title:
                continue

            link = anchor.get("href", "").strip()
            if link and not link.startswith("http"):
                link = urljoin(BASE_URL, link)

            status = "Canceled" if _is_canceled(anchor) else "Scheduled"

            events.append(
                {
                    "Library": "Glencoe Park District",
                    "Title": title,
                    "Date": event_date.isoformat(),
                    "Time": time_text or "See site",
                    "Location": "Glencoe Park District",
                    "Program Type": "Calendar",
                    "Age Group": "General",
                    "Description": "",
                    "Status": status,
                    "Link": link or BASE_URL,
                }
            )
    return events


async def fetch_range(start: date, days: int) -> List[Dict[str, str]]:
    """Fetch enough month pages to cover the date window and filter events to that window."""
    end = start + timedelta(days=max(days - 1, 0))
    events: List[Dict[str, str]] = []
    async with aiohttp.ClientSession() as session:
        for year, month in month_iter(start, end):
            try:
                html = await fetch_month_html(session, year, month)
                month_events = parse_month_page(html)
                # Filter to the desired window
                for ev in month_events:
                    try:
                        ev_date = datetime.strptime(ev["Date"], "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if start <= ev_date <= end:
                        events.append(ev)
                logger.info("Parsed %s events for %04d-%02d", len(month_events), year, month)
            except Exception as exc:  # pragma: no cover - best effort logging
                logger.warning("Failed to fetch/parse %04d-%02d: %s", year, month, exc)
            await asyncio.sleep(REQUEST_DELAY)
    return events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Glencoe Park District calendar.")
    parser.add_argument(
        "--start-date",
        default=date.today().strftime("%Y-%m-%d"),
        help="Start date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="How many days forward to include (default: 60).",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args()


def to_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    start_day = to_date(args.start_date)
    events: List[Dict[str, str]] = asyncio.run(fetch_range(start_day, max(1, args.days)))
    print(json.dumps(events, indent=2 if args.pretty else None, ensure_ascii=False))
