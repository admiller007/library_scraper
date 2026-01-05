"""
Quick stand-alone scraper for https://www.kohlchildrensmuseum.org/visit/calendar/.

This does NOT integrate with library_all_events yet. It fetches the calendar
for one or more days and prints structured event dictionaries to stdout so we
can validate parsing before wiring it into the main pipeline.
"""

import argparse
import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp
from bs4 import BeautifulSoup


BASE_URL = "https://www.kohlchildrensmuseum.org/visit/calendar/"
MUSEUM_NAME = "Kohl Children's Museum"
LOCATION = "Kohl Children's Museum, Glenview IL"
USER_AGENT = "LibraryScraper/0.1 (+https://github.com/)"
REQUEST_DELAY = 0.35  # seconds between requests to be polite

logger = logging.getLogger(__name__)


def clean_text(value: Optional[str]) -> str:
    """Normalize whitespace and drop stray HTML artifacts."""
    if not value:
        return ""
    text = value.replace("\xa0", " ").replace("\u200b", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_page_date(soup: BeautifulSoup, fallback: Optional[date]) -> Tuple[Optional[date], str]:
    """
    Extract the date label shown on the page (e.g., 'December 15, 2025').
    Returns the parsed date (if possible) and the raw label text.
    """
    label_el = soup.select_one("p.font-sm")
    label_text = clean_text(label_el.get_text()) if label_el else ""

    parsed = None
    cleaned = label_text.replace(",", "")
    for fmt in ("%B %d %Y",):
        try:
            parsed = datetime.strptime(cleaned, fmt).date()
            break
        except ValueError:
            continue

    if not parsed:
        parsed = fallback
    return parsed, label_text


def parse_event_pods(soup: BeautifulSoup, event_date: Optional[date], date_label: str) -> List[Dict[str, str]]:
    """Turn the set of <a class='event-pod'> blocks into structured event dicts."""
    events: List[Dict[str, str]] = []

    for pod in soup.select("a.event-pod"):
        title_el = pod.find("h3")
        if not title_el:
            continue

        title = clean_text(title_el.get_text(" ", strip=True))
        if not title:
            continue

        time_el = pod.find("p", class_="date-time")
        time_text = clean_text(time_el.get_text(" ", strip=True)) if time_el else ""

        desc_el = pod.find(class_="desc")
        description = clean_text(desc_el.get_text(" ", strip=True)) if desc_el else ""

        category_el = pod.find_previous("h2")
        category = clean_text(category_el.get_text(" ", strip=True)) if category_el else "Museum Event"

        link = pod.get("href", "").strip()

        # Decide whether the date-time string is a time-of-day or a date range
        date_info = ""
        time_value = "See site"
        if time_text:
            if re.search(r"\d{1,2}:\d{2}", time_text) or re.search(r"\b(am|pm)\b", time_text, re.IGNORECASE):
                time_value = time_text
            elif re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)", time_text, re.IGNORECASE):
                date_info = time_text
            else:
                time_value = time_text

        events.append(
            {
                "Library": MUSEUM_NAME,
                "Title": title,
                "Date": event_date.isoformat() if event_date else (date_label or "Unknown"),
                "Date Display": date_label or "",
                "Time": time_value,
                "Date Info": date_info,
                "Location": LOCATION,
                "Age Group": "Kids/Family",
                "Program Type": category,
                "Description": description or (date_info or "See site for details"),
                "Link": link or BASE_URL,
            }
        )

    return events


def make_soup(html: str) -> BeautifulSoup:
    """Create a soup with lxml when available, otherwise fall back to html.parser."""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


async def fetch_html(session: aiohttp.ClientSession, target_date: Optional[date]) -> str:
    """Fetch the calendar HTML for a specific date (or today if None)."""
    url = BASE_URL
    if target_date:
        url = f"{BASE_URL}?date={target_date.strftime('%Y%m%d')}"
    headers = {"User-Agent": USER_AGENT}
    async with session.get(url, headers=headers, timeout=20) as resp:
        resp.raise_for_status()
        return await resp.text()


async def fetch_kohl_events(target_date: Optional[date]) -> List[Dict[str, str]]:
    """Fetch and parse events for a single date."""
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, target_date)
        soup = make_soup(html)
        parsed_date, date_label = parse_page_date(soup, target_date)
        return parse_event_pods(soup, parsed_date, date_label)


async def fetch_range(start: date, days: int) -> List[Dict[str, str]]:
    """Fetch multiple days in sequence (lightweight throttling)."""
    events: List[Dict[str, str]] = []
    async with aiohttp.ClientSession() as session:
        for offset in range(days):
            day = start + timedelta(days=offset)
            try:
                html = await fetch_html(session, day)
                soup = make_soup(html)
                parsed_date, date_label = parse_page_date(soup, day)
                day_events = parse_event_pods(soup, parsed_date, date_label)
                events.extend(day_events)
                logger.info("Parsed %s events for %s", len(day_events), date_label or day.isoformat())
            except Exception as exc:  # pragma: no cover - best effort logging
                logger.warning("Failed to parse %s: %s", day, exc)
            await asyncio.sleep(REQUEST_DELAY)
    return events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Kohl Children's Museum calendar.")
    parser.add_argument(
        "--start-date",
        help="Date to start from (YYYY-MM-DD). Defaults to today.",
        default=date.today().strftime("%Y-%m-%d"),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="How many sequential days to fetch (default: 1).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def to_date(raw: str) -> date:
    """Parse YYYY-MM-DD into a date; raises ValueError on failure."""
    return datetime.strptime(raw, "%Y-%m-%d").date()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    start_day = to_date(args.start_date)
    events: List[Dict[str, str]] = asyncio.run(fetch_range(start_day, max(1, args.days)))
    print(json.dumps(events, indent=2 if args.pretty else None, ensure_ascii=False))
