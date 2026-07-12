import asyncio
import csv
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import argparse
import aiohttp
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from urllib.parse import urljoin, unquote
from bs4 import BeautifulSoup, FeatureNotFound
from bs4 import BeautifulSoup
try:
    from firecrawl import AsyncFirecrawl as _AsyncFirecrawlClient  # v2 SDK
    _FIRECRAWL_V2 = True
except ImportError:
    from firecrawl import AsyncFirecrawlApp as _AsyncFirecrawlClient  # legacy SDK
    _FIRECRAWL_V2 = False
AsyncFirecrawl = _AsyncFirecrawlClient  # Alias so downstream typing still works
from ics import Calendar, Event
import hashlib
from pylatex import Document, Section, Subsection, Command
from pylatex.utils import NoEscape, escape_latex
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from html import unescape

# --- LOGGING CONFIGURATION ---
# Load environment from .env if present
load_dotenv()

# Data directory for generated artifacts (CSV/ICS/PDF/logs)
DATA_DIR = Path(os.getenv("DATA_DIR", ".")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Configure rotating file logs to avoid unbounded growth
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_root = logging.getLogger()
for _h in list(_root.handlers):
    _root.removeHandler(_h)
_stream = logging.StreamHandler()
_file = RotatingFileHandler(DATA_DIR / 'library_all_events.log', maxBytes=2 * 1024 * 1024, backupCount=3)
for _h in (_stream, _file):
    _h.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    _root.addHandler(_h)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY')
if not FIRECRAWL_API_KEY:
    raise ValueError(
        "FIRECRAWL_API_KEY environment variable is required. "
        "Please set it in your .env file or environment."
    )
TIMEZONE = os.getenv('TIMEZONE', 'America/Chicago')

# Date window configuration (computed at runtime in main())
DEFAULT_DAYS_TO_FETCH = 90
START_DATE = None  # will be set in main()
DAYS_TO_FETCH = DEFAULT_DAYS_TO_FETCH  # will be set in main()
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# --- Library Specific Config (MODIFIED: Remove age group filtering) ---
MGPL_URL = 'https://www.mgpl.org/events/list'  # Remove age group filter
EVANSTON_BASE_URL = 'https://evanstonlibrary.bibliocommons.com/v2/events'
CPL_BASE_URL = 'https://chipublib.bibliocommons.com/v2/events'
GLENVIEW_BASE_URL = 'https://glenviewpl.bibliocommons.com/v2/events'
GLENCOE_AJAX_URL = "https://calendar.glencoelibrary.org/ajax/calendar/list"
GLENCOE_CALENDAR_ID = "19721"
SKOKIE_PARKS_URL = "https://www.skokieparks.org/events/"
SKOKIE_PARKS_BASE = "https://www.skokieparks.org"
CHICAGO_PARKS_URL = "https://www.chicagoparkdistrict.com/events"
WNPLD_BASE_URL = "https://www.wnpld.org"
WNPLD_MAX_PAGES = 12
WNPLD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Pre-compiled regex patterns for performance
COMPILED_PATTERNS = {
    'time_pattern': re.compile(r'(\d{1,2}:\d{2}[ap]m(?:–\d{1,2}:\d{2}[ap]m)?)'),
    'markdown_links': re.compile(r'!?\[.*?\]\(.*?\)'),
    'bold_text': re.compile(r'\*{1,2}(.*?)\*{1,2}'),
    'age_patterns': {
        'baby': re.compile(r'\b(baby|babies|infant|toddler|0-2|birth|newborn)\b', re.IGNORECASE),
        'preschool': re.compile(r'\b(preschool|pre-school|k-2|kindergarten|grades?\s*k|ages?\s*3-5|early\s+childhood)\b', re.IGNORECASE),
        'elementary': re.compile(r'\b(elementary|grades?\s*[1-5]|ages?\s*6-11|school\s*age)\b', re.IGNORECASE),
        'middle': re.compile(r'\b(middle|grades?\s*[6-8]|ages?\s*12-14|tween|teen)\b', re.IGNORECASE),
        'teen': re.compile(r'\b(high\s*school|grades?\s*(?:9|1[0-2])|ages?\s*15-18|teen|young\s*adult)\b', re.IGNORECASE),
        'adult': re.compile(r'\b(adult|adults|seniors?|elderly|ages?\s*18\+|21\+|mature)\b', re.IGNORECASE),
        'family': re.compile(r'\b(family|families|all\s*ages?|everyone)\b', re.IGNORECASE),
        'kids': re.compile(r'\b(kids?|children)\b', re.IGNORECASE)
    }
}

# Chicago Park District specific configuration
CPD_BASE_URL = "https://www.chicagoparkdistrict.com"
CPD_EVENTS_LIST_URL = "https://www.chicagoparkdistrict.com/events"

# Optimized throttling and connection management
FIRECRAWL_CONCURRENCY = int(os.getenv('FIRECRAWL_CONCURRENCY', '3'))
REQUESTS_CONCURRENCY = int(os.getenv('REQUESTS_CONCURRENCY', '5'))
CPD_CONCURRENCY = int(os.getenv('CPD_CONCURRENCY', '2'))  # Lower for Chicago Parks to be respectful
FIRECRAWL_SEM = asyncio.Semaphore(FIRECRAWL_CONCURRENCY)
REQUESTS_SEM = asyncio.Semaphore(REQUESTS_CONCURRENCY)
CPD_SEM = asyncio.Semaphore(CPD_CONCURRENCY)

# Global session for connection pooling
_http_session = None
WNPLD_CACHE: Optional[List[Dict[str, Any]]] = None
WNPLD_CACHE_LOCK = asyncio.Lock()

# --- PROGRESS TRACKING ---
PROGRESS_FILE = DATA_DIR / "scrape_progress.json"
# Source labels come from the single registry in _event_sources(); see
# source_labels() near _gather_and_filter_events().
progress_state: Dict[str, Any] = {}
progress_lock = asyncio.Lock()


def _compute_summary_from_sources(sources: Dict[str, Dict[str, Any]], existing_events: int = 0, message: str = "", state_override: Optional[str] = None) -> Dict[str, Any]:
    """Compute an aggregate view from individual source states."""
    succeeded = sum(1 for s in sources.values() if s.get("state") == "success")
    failed = sum(1 for s in sources.values() if s.get("state") == "error")
    running = sum(1 for s in sources.values() if s.get("state") == "running")
    pending = sum(1 for s in sources.values() if s.get("state") == "pending")
    total = len(sources)
    events = sum(int(s.get("count") or 0) for s in sources.values())
    overall = "running"
    if state_override:
        overall = state_override
    elif running == 0 and pending == 0:
        overall = "completed_with_errors" if failed else "completed"
    elif failed:
        overall = "degraded"

    return {
        "state": overall,
        "total_sources": total,
        "succeeded": succeeded,
        "failed": failed,
        "running": running,
        "pending": pending,
        "events": events or existing_events,
        "message": message
    }


def _safe_write_progress():
    try:
        PROGRESS_FILE.write_text(json.dumps(progress_state, indent=2))
    except Exception as exc:  # pragma: no cover - best effort safety
        logger.warning(f"Failed to write progress file: {exc}")


async def init_progress_state():
    """Initialize the shared progress file so the UI can poll immediately."""
    global progress_state
    now = datetime.utcnow().isoformat()
    progress_state = {
        "started_at": now,
        "updated_at": now,
        "sources": {name: {"state": "pending", "count": 0, "message": ""} for name in source_labels()},
    }
    progress_state["summary"] = _compute_summary_from_sources(progress_state["sources"])
    _safe_write_progress()


async def mark_progress(source: str, state: str, count: Optional[int] = None, message: str = ""):
    """Update a single source entry and recompute summary."""
    async with progress_lock:
        if not progress_state:
            await init_progress_state()
        src = progress_state["sources"].setdefault(source, {"state": "pending", "count": 0, "message": ""})
        src["state"] = state
        if count is not None:
            src["count"] = count
        if message:
            src["message"] = message
        progress_state["updated_at"] = datetime.utcnow().isoformat()
        progress_state["summary"] = _compute_summary_from_sources(
            progress_state["sources"],
            progress_state.get("summary", {}).get("events", 0),
            message=progress_state.get("summary", {}).get("message", "")
        )
        _safe_write_progress()


async def mark_overall_state(state: str, total_events: Optional[int] = None, message: str = ""):
    """Update the top-level summary (e.g., finished, failed)."""
    async with progress_lock:
        if not progress_state:
            await init_progress_state()
        events = total_events if total_events is not None else progress_state.get("summary", {}).get("events", 0)
        progress_state["summary"] = _compute_summary_from_sources(
            progress_state["sources"],
            existing_events=events,
            message=message,
            state_override=state
        )
        progress_state["summary"]["events"] = events
        progress_state["updated_at"] = datetime.utcnow().isoformat()
        _safe_write_progress()


async def run_source_with_progress(label: str, coroutine_factory):
    """Wrap a scraper coroutine to report progress consistently."""
    await mark_progress(label, "running")
    try:
        result = await coroutine_factory()
        count = len(result) if isinstance(result, list) else 0
        if count == 0:
            logger.warning(f"Source {label} returned 0 events — possible breakage")
            await mark_progress(label, "success", count=0, message="0 events — possible breakage")
        else:
            await mark_progress(label, "success", count=count)
        return result
    except Exception as exc:
        await mark_progress(label, "error", message=str(exc))
        raise


def zero_event_sources() -> List[str]:
    """Labels of sources that finished 'successfully' with 0 events in the
    last run — the silent-breakage signature. Keyed by progress labels, so it
    works even when a fetcher's Library value differs from its label."""
    sources = progress_state.get("sources", {}) if progress_state else {}
    return [name for name, s in sources.items() if s.get("state") == "success" and not s.get("count")]


def failed_sources() -> List[str]:
    """Labels of sources that raised during the last run."""
    sources = progress_state.get("sources", {}) if progress_state else {}
    return [name for name, s in sources.items() if s.get("state") == "error"]


async def get_http_session():
    global _http_session
    if _http_session is None:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=5)
        _http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _http_session


async def close_http_session():
    global _http_session
    if _http_session:
        await _http_session.close()
        _http_session = None


def _make_soup(html: str) -> BeautifulSoup:
    """Create a BeautifulSoup object, preferring lxml, with fallback to html.parser."""
    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        logger.warning("lxml parser not available; falling back to html.parser")
        return BeautifulSoup(html, "html.parser")

# --- HELPER FUNCTIONS ---

async def retry_with_backoff(func, *args, max_retries=MAX_RETRIES, delay=RETRY_DELAY, **kwargs):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (ConnectionError, requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError) as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed after {max_retries} attempts: {e}")
                raise
            wait_time = delay * (2 ** attempt)
            logger.warning(f"Attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
            await asyncio.sleep(wait_time)
        except aiohttp.ClientError as e:
            # Handle 429 rate limits with intelligent backoff
            msg = str(e)
            if '429' in msg or hasattr(e, 'status') and e.status == 429:
                if attempt == max_retries - 1:
                    logger.error(f"Rate limited and exceeded retries: {e}")
                    raise
                # Extract retry-after header or use exponential backoff
                retry_after = getattr(e, 'headers', {}).get('Retry-After')
                if retry_after and retry_after.isdigit():
                    wait_time = min(int(retry_after), 60)  # Cap at 60s
                else:
                    wait_time = min(delay * (2 ** attempt), 30)  # Cap at 30s
                logger.warning(f"Rate limited (429). Waiting {wait_time}s before retry {attempt+2}/{max_retries}")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Non-retryable aiohttp error: {e}")
                raise
        except Exception as e:
            logger.error(f"Non-retryable error: {e}")
            raise

# UPDATED: Firecrawl helper that supports both legacy and v2 SDKs
async def firecrawl_scrape(app: AsyncFirecrawl, url: str, **kwargs):
    """Scrape a URL using whichever Firecrawl SDK is available."""
    async with FIRECRAWL_SEM:
        if _FIRECRAWL_V2:
            kwargs.pop('only_main_content', None)
            return await app.scrape(url, formats=["markdown"])
        return await app.scrape_url(url=url, **kwargs)

def clean_text(text: str) -> str:
    """Optimized text cleaning using pre-compiled patterns."""
    if not isinstance(text, str):
        return ""

    try:
        # Fast path for empty/short strings
        if not text or len(text) < 3:
            return text.strip()

        text = text.encode('ascii', 'ignore').decode('ascii')
        text = text.replace('\u200b', '').replace('\n', ' ')
        text = COMPILED_PATTERNS['markdown_links'].sub('', text)
        text = COMPILED_PATTERNS['bold_text'].sub(r'\1', text)
        # Remove common location prefixes while preserving location content
        text = text.replace('Event location:', '').replace('Location:', '').strip()

        # Check for duplicate content (optimization for repeated text)
        if len(text) > 20:
            mid = len(text) // 2
            if text[:mid].strip() == text[mid:].strip():
                text = text[:mid].strip()

        return ' '.join(text.split())
    except Exception:
        return str(text) if text else ""

def html_to_text(html_fragment: str) -> str:
    """Convert basic HTML into cleaned plain text."""
    if not isinstance(html_fragment, str):
        return ""
    try:
        text = unescape(html_fragment)
        text = re.sub(r'(?i)<\s*br\s*/?>', ' ', text)
        text = re.sub(r'(?i)</p>', ' ', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        return clean_text(text)
    except Exception:
        return clean_text(html_fragment)

def parse_time_to_sortable(time_str: str) -> datetime.time:
    """Parses various time formats into a time object for sorting."""
    if not isinstance(time_str, str):
        logger.debug(f"Invalid time input: {type(time_str)}")
        return datetime.min.time()
    
    try:
        time_str = time_str.lower().strip().split('@')[-1]
        time_str = re.split(r'–|-', time_str)[0].strip()
        if 'all day' in time_str:
            return datetime.min.time()
        formats_to_try = ['%I:%M %p', '%I:%M%p', '%-I:%M %p', '%I %p', '%-I%p']
        for fmt in formats_to_try:
            try:
                return datetime.strptime(time_str.replace(" ", ""), fmt).time()
            except ValueError:
                continue
        logger.debug(f"Could not parse time string: {time_str}")
        return datetime.min.time()
    except Exception as e:
        logger.warning(f"Error parsing time '{time_str}': {e}")
        return datetime.min.time()

def extract_age_group(content: str) -> str:
    """Optimized age group extraction using pre-compiled patterns."""
    if not content:
        return "General"

    # Check patterns in order of specificity using pre-compiled regex
    patterns = COMPILED_PATTERNS['age_patterns']

    if patterns['baby'].search(content):
        return "Baby/Toddler"
    elif patterns['preschool'].search(content):
        return "Preschool/Early Elementary"
    elif patterns['elementary'].search(content):
        return "Elementary"
    elif patterns['middle'].search(content):
        return "Middle School/Teen"
    elif patterns['teen'].search(content):
        return "Teen/Young Adult"
    elif patterns['adult'].search(content):
        return "Adult"
    elif patterns['family'].search(content):
        return "Family/All Ages"
    elif patterns['kids'].search(content):
        return "Kids"
    else:
        return "General"

def get_enhanced_location(item: dict, library_name: str) -> str:
    """Enhanced location extraction for LibNet events."""
    # Try to get specific location from the event data
    location = clean_text(item.get("location", ""))

    # Try additional location fields that LibNet might use
    if not location:
        for field in ["venue", "room", "location_name", "meeting_room"]:
            if field in item and item[field]:
                location = clean_text(str(item[field]))
                break

    # If we have a specific location, combine with library name
    if location and location.strip() and location.lower() != library_name.lower():
        # Check if library name is already included
        if library_name.lower() not in location.lower():
            return f"{location} at {library_name}"
        else:
            return location

    # Fallback to generic library name
    return library_name

# --- FETCHERS (MODIFIED: Remove age filtering) ---

async def fetch_lincolnwood_events() -> List[Dict[str, Any]]:
    """Fetch Lincolnwood Public Library events.

    The library moved to a LibraryMarket LibraryCalendar (Drupal 'lc-') site,
    so this now delegates to the generic plain-HTTP adapter instead of the old
    Firecrawl markdown scrape (which stopped matching the redesigned page and
    silently returned 0 events).
    """
    return await fetch_librarycalendar_events(
        "Lincolnwood",
        "https://www.lincolnwoodlibrary.org",
        default_location="Lincolnwood Library",
    )

# UPDATED: Changed to use AsyncFirecrawl
async def _fetch_mgpl_content(app: AsyncFirecrawl) -> str:
    """Fetch content from Morton Grove with error handling."""
    response = await firecrawl_scrape(app, url=MGPL_URL)
    return response.markdown if hasattr(response, "markdown") else ""

async def fetch_mgpl_events() -> List[Dict[str, Any]]:
    logger.info("Fetching Morton Grove events (ALL EVENTS)...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Morton Grove fetch")
        return []
    app = AsyncFirecrawl(api_key=FIRECRAWL_API_KEY)  # UPDATED: Changed from AsyncFirecrawlApp
    try:
        markdown = await retry_with_backoff(_fetch_mgpl_content, app)
        if not markdown:
            logger.warning("No markdown content received from Morton Grove")
            return []
    except ValueError as e:
        logger.error(f"Invalid response from Morton Grove API: {e}")
        return []
    except ConnectionError as e:
        logger.error(f"Connection error while fetching Morton Grove events: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching Morton Grove events: {e}", exc_info=True)
        return []
    try:
        events_raw = re.split(r'\n## ', markdown)
    except (AttributeError, TypeError) as e:
        logger.error(f"Error parsing Morton Grove markdown structure: {e}")
        return []
        
    all_events = []
    for block in events_raw[1:]:
        try:
            title_link_match = re.search(r'\[(.*?)\]\((.*?)\)', block)
            if not title_link_match:
                logger.debug("Skipping block - no title/link found")
                continue
                
            title, link = title_link_match.groups()
            title = title.strip()
            if not title:
                logger.debug("Skipping event with empty title")
                continue
                
            if not link.startswith('http'):
                link = 'https://www.mgpl.org' + link
            
            date_str, time_str, location_str = "Not found", "Not found", "Morton Grove Public Library"
            description = "Not found"
            
            # Parse date
            date_block_match = re.search(r'(\w{3})\n(\d{1,2})\n(\d{4})', block)
            if date_block_match:
                month, day, year = date_block_match.groups()
                date_str = f"{month} {day}, {year}"
            else:
                date_line_match = re.search(r'^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), .*? \d{4}', block, re.MULTILINE)
                if date_line_match:
                    date_str = date_line_match.group(0)
            
            # Parse time
            time_line_match = re.search(r' at\n(.*?)\n', block)
            if time_line_match:
                time_str = time_line_match.group(1).strip()
            
            if "All Day" in block:
                time_str = "All Day"
                all_day_match = re.search(r'All Day (.*?)\n', block)
                if all_day_match:
                    date_str = f"All Day {all_day_match.group(1).strip()}"
            
            # Parse structured content
            structured_match = re.search(r'### .*?\n(.*)', block, re.DOTALL)
            if structured_match:
                structured_text = structured_match.group(1)
                desc_match = re.search(r'\*\*Event Details:\*\*\n(.*?)(?=\n###|\Z)', structured_text, re.DOTALL)
                if desc_match:
                    description = clean_text(desc_match.group(1))
                
                # Enhanced location parsing for Morton Grove
                loc_match = re.search(r'\*\*Location:\*\*\n(.*?)\n', structured_text)
                if loc_match:
                    parsed_location = loc_match.group(1).strip()
                    if parsed_location:
                        location_str = parsed_location

                room_match = re.search(r'\*\*Room:\*\*\n(.*?)\n', structured_text)
                if room_match:
                    room = room_match.group(1).strip()
                    if room and room not in location_str:
                        if location_str == "Morton Grove Public Library":
                            location_str = f"{room} at Morton Grove Public Library"
                        else:
                            location_str = f"{room} at {location_str}"
            
            # Extract age group from content
            age_group = extract_age_group(f"{title} {description}")
            
            all_events.append({
                "Library": "Morton Grove", 
                "Title": title, 
                "Date": date_str, 
                "Time": time_str, 
                "Location": location_str, 
                "Age Group": age_group, 
                "Program Type": "Not found", 
                "Description": description, 
                "Link": link
            })
        except Exception as e:
            logger.warning(f"Error processing Morton Grove event: {e}")
            continue
    logger.info(f"Found {len(all_events)} events for Morton Grove")
    return all_events

def parse_bibliocommons_markdown(markdown: str, library_name: str) -> List[Dict[str, Any]]:
    """Helper function to parse the specific markdown from Bibliocommons sites."""
    try:
        events_section = markdown.split('## Event items')[1]
    except IndexError:
        return []
    event_blocks, page_events = re.split(r'-\s+\w{3}\n', events_section), []
    for block in event_blocks[1:]:
        title_match = re.search(r'### \[(.*?)\]\((.*?)\)', block)
        if not title_match:
            continue
        title, link = title_match.groups()
        
        datetime_match = re.search(r'(\w+,\s+\w+\s+\d{1,2})on.*?(\d{4}),\s*(\d{1,2}:\d{2}[ap]m–\d{1,2}:\d{2}[ap]m)', block)
        if not datetime_match:
            continue
        date_part, year, time_part = datetime_match.groups()
        
        # Enhanced location parsing for Bibliocommons
        location = f"{library_name}"  # Default fallback

        # Try to extract detailed location
        location_match = re.search(r'\[.*?Event location:\s*(.*?)\]\(.*?\)', block)
        if location_match:
            specific_location = clean_text(location_match.group(1))
            if specific_location and specific_location.strip():
                location = specific_location
        else:
            # Try offsite location pattern
            offsite_match = re.search(r'Offsite location:\s*(.*?)\n', block)
            if offsite_match:
                offsite_location = clean_text(offsite_match.group(1))
                if offsite_location and offsite_location.strip():
                    location = offsite_location
        
        desc_match = re.search(r'Event location:.*?\n\n(.*?)(?=\n\n(Register for|Join waitlist)|- \[)', block, re.DOTALL)
        description = clean_text(desc_match.group(1)) if desc_match else "Not found"
        
        # Extract age group from content
        age_group = extract_age_group(f"{title} {description}")
        
        page_events.append({
            "Library": library_name,
            "Title": title,
            "Date": f"{date_part}, {year}",
            "Time": time_part.replace('–', ' - '),
            "Location": location,
            "Age Group": age_group,
            "Program Type": "Not found",
            "Description": description,
            "Link": link
        })
    return page_events

async def fetch_bibliocommons_events(library_name: str, base_url: str, query_params: str = "") -> List[Dict[str, Any]]:
    """Generic fetcher for any Bibliocommons library, with pagination. MODIFIED: Remove age filtering."""
    logger.info(f"Fetching {library_name} events (ALL EVENTS)...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Bibliocommons fetch for %s", library_name)
        return []
    app = AsyncFirecrawl(api_key=FIRECRAWL_API_KEY)  # UPDATED: Changed from AsyncFirecrawlApp
    all_events, current_page, max_pages = [], 1, 5
    while current_page <= max_pages:
        # Build URL without age group filtering
        if query_params:
            url = f"{base_url}?{query_params}&page={current_page}"
        else:
            url = f"{base_url}?page={current_page}"
        logger.debug(f"Fetching page {current_page} for {library_name}...")
        try:
            response = await retry_with_backoff(firecrawl_scrape, app, url=url)
            markdown = response.markdown if hasattr(response, "markdown") else ""
            if not markdown or "No events found" in markdown:
                logger.debug(f"No more events found on page {current_page}")
                break
        except ValueError as e:
            logger.error(f"Invalid response from {library_name} API on page {current_page}: {e}")
            break
        except ConnectionError as e:
            logger.error(f"Connection error while fetching {library_name} page {current_page}: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error scraping page {current_page} for {library_name}: {e}", exc_info=True)
            break
        events_on_page = parse_bibliocommons_markdown(markdown, library_name)
        if not events_on_page:
            logger.warning(f"Could not parse events on page {current_page} for {library_name}. Stopping")
            break
        all_events.extend(events_on_page)
        if len(events_on_page) < 20:
            logger.debug("Reached the last page of results")
            break
        current_page += 1
        # Small delay to avoid rate limits
        await asyncio.sleep(1)
    logger.info(f"Found a total of {len(all_events)} events for {library_name}")
    return all_events

async def fetch_libnet_events(library_name: str, base_url: str) -> List[Dict[str, Any]]:
    """MODIFIED: Fetch all events from LibNet libraries, no age filtering."""
    logger.info(f"Fetching {library_name} events (ALL EVENTS)...")
    api_url = f"https://{base_url}/eeventcaldata"
    
    # Request all events - empty ages array means all age groups
    payload = {
        "event_type": 0,
        "req": (
            f'{{"private":false,"date":"{START_DATE}","days":{DAYS_TO_FETCH},'
            f'"locations":[],"ages":[],"types":[]}}'  # Empty ages array = all events
        ),
    }
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{base_url}/events",
        # Some Communico installs on custom domains (e.g. ahml.info) sit behind
        # WAFs that reject requests without a browser-like UA.
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }

    session = await get_http_session()
    async with REQUESTS_SEM:
        try:
            async with session.get(api_url, headers=headers, params=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, list):
                    logger.error(f"Invalid response format from {library_name}: expected list, got {type(data)}")
                    return []
        except asyncio.TimeoutError as e:
            logger.error(f"Timeout error fetching {library_name} events: {e}")
            return []
        except aiohttp.ClientConnectionError as e:
            logger.error(f"Connection error fetching {library_name} events: {e}")
            return []
        except aiohttp.ClientResponseError as e:
            logger.error(f"HTTP error fetching {library_name} events: {e}")
            return []
        except ValueError as e:
            logger.error(f"JSON decode error for {library_name} events: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected request error fetching {library_name} events: {e}", exc_info=True)
            return []
    
    events = []
    for item in data:
        try:
            if not isinstance(item, dict):
                logger.debug(f"Skipping invalid item type: {type(item)}")
                continue
                
            title = clean_text(item.get("title", ""))
            if not title:
                logger.debug("Skipping event with empty title")
                continue
            
            dt_obj = None
            if item.get("event_start"):
                try:
                    dt_obj = datetime.strptime(item["event_start"], "%Y-%m-%d %H:%M:%S")
                except ValueError as e:
                    logger.debug(f"Could not parse date '{item['event_start']}': {e}")
            
            # Extract age/grade group from multiple possible LibNet fields
            def coerce_labels(val):
                # Accept list[str] | list[dict] | str | None and return list[str]
                if not val:
                    return []
                if isinstance(val, str):
                    return [val.strip()] if val.strip() else []
                if isinstance(val, list):
                    out = []
                    for v in val:
                        if isinstance(v, str) and v.strip():
                            out.append(v.strip())
                        elif isinstance(v, dict):
                            # Common keys seen in LibNet-like APIs
                            for key in ("name", "title", "label", "text", "value"):
                                s = v.get(key)
                                if isinstance(s, str) and s.strip():
                                    out.append(s.strip())
                                    break
                    return out
                if isinstance(val, dict):
                    # Single dict with a label
                    for key in ("name", "title", "label", "text", "value"):
                        s = val.get(key)
                        if isinstance(s, str) and s.strip():
                            return [s.strip()]
                    return []
                return []

            age_labels = []
            # Primary
            age_labels += coerce_labels(item.get("ages"))
            # Alternate shapes
            age_labels += coerce_labels(item.get("age"))
            age_labels += coerce_labels(item.get("age_group"))
            age_labels += coerce_labels(item.get("ageGroup"))
            age_labels += coerce_labels(item.get("age_groups"))
            age_labels += coerce_labels(item.get("audiences"))
            age_labels += coerce_labels(item.get("audience"))
            # De-duplicate while preserving order
            seen_labels = set()
            age_labels = [x for x in age_labels if not (x in seen_labels or seen_labels.add(x))]

            if age_labels:
                age_group = ", ".join(age_labels)
            else:
                # Fallback: infer from title/description
                content_for_age = f"{title} {item.get('description', '')}"
                age_group = extract_age_group(content_for_age)

            # Fix double slash issue in LibNet URLs
            event_url = item.get("url", "")
            if "//" in event_url and "://" not in event_url:
                event_url = event_url.replace("//", "/")
            elif "://" in event_url:
                # Fix double slashes after protocol
                event_url = event_url.replace("://", "PROTOCOL_PLACEHOLDER").replace("//", "/").replace("PROTOCOL_PLACEHOLDER", "://")

            events.append({
                "Library": library_name, 
                "Title": title, 
                "Date": dt_obj.strftime("%Y-%m-%d") if dt_obj else "Not found", 
                "Time": dt_obj.strftime("%-I:%M %p") if dt_obj else "Not found", 
                "Location": get_enhanced_location(item, library_name), 
                "Age Group": age_group, 
                "Program Type": "Not found", 
                "Description": clean_text(item.get("description", "")), 
                "Link": event_url
            })
        except Exception as e:
            logger.warning(f"Error processing {library_name} event: {e}")
            continue
    
    logger.info(f"Found {len(events)} events for {library_name}")
    return events

async def fetch_glencoe_events() -> List[Dict[str, Any]]:
    """Fetch Glencoe Public Library events via its LibCal JSON feed."""
    logger.info("Fetching Glencoe events (ALL EVENTS)...")
    session = await get_http_session()
    per_page = 100
    max_pages = 5
    page = 1
    events: List[Dict[str, Any]] = []
    try:
        window_start = datetime.strptime(START_DATE, "%Y-%m-%d").date()
        window_end = window_start + timedelta(days=max(DAYS_TO_FETCH - 1, 0))
    except (TypeError, ValueError):
        window_end = None
    stop_fetching = False
    while page <= max_pages and not stop_fetching:
        params = {
            "c": GLENCOE_CALENDAR_ID,
            "date": "0000-00-00",
            "perpage": per_page,
            "page": page,
            "audience": "",
            "cats": "",
            "camps": "",
            "inc": 0,
        }
        async with REQUESTS_SEM:
            try:
                async with session.get(GLENCOE_AJAX_URL, params=params) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            except asyncio.TimeoutError as e:
                logger.error(f"Timeout fetching Glencoe page {page}: {e}")
                break
            except aiohttp.ClientError as e:
                logger.error(f"HTTP error fetching Glencoe page {page}: {e}")
                break
            except ValueError as e:
                logger.error(f"JSON decode error for Glencoe page {page}: {e}")
                break
            except Exception as e:
                logger.error(f"Unexpected error fetching Glencoe page {page}: {e}", exc_info=True)
                break
        results = data.get("results")
        if not isinstance(results, list) or not results:
            logger.debug("No Glencoe events returned on page %s", page)
            break
        for item in results:
            try:
                title = clean_text(item.get("title", ""))
                if not title:
                    continue
                start_dt = None
                start_raw = item.get("startdt")
                if start_raw:
                    try:
                        start_dt = datetime.strptime(start_raw, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        logger.debug(f"Could not parse Glencoe start date '{start_raw}'")
                if start_dt:
                    date_str = start_dt.strftime("%A, %B %d, %Y")
                else:
                    date_str = clean_text(item.get("date", "Not found")) or "Not found"
                if item.get("all_day"):
                    time_str = "All Day Event"
                else:
                    start_time = clean_text(item.get("start", ""))
                    end_time = clean_text(item.get("end", ""))
                    if start_time and end_time and start_time != end_time:
                        time_str = f"{start_time} - {end_time}"
                    else:
                        time_str = start_time or "Not found"
                description_html = (
                    item.get("description") or item.get("shortdesc") or item.get("more_info") or ""
                )
                description = html_to_text(description_html) or "Not found"
                location_parts = []
                for loc in item.get("locations", []) or []:
                    name = clean_text(loc.get("name", ""))
                    if name:
                        location_parts.append(name)
                fallback_location = clean_text(item.get("location", ""))
                if fallback_location:
                    location_parts.append(fallback_location)
                campus = clean_text(item.get("campus", ""))
                if campus:
                    location_parts.append(campus)
                if item.get("online_event"):
                    location_parts.append("Online")
                seen_locations = []
                for loc in location_parts:
                    if loc and loc not in seen_locations:
                        seen_locations.append(loc)
                location = ", ".join(seen_locations) if seen_locations else "Glencoe Public Library"
                audience_names = []
                for aud in item.get("audiences", []) or []:
                    name = clean_text(aud.get("name", ""))
                    if name:
                        audience_names.append(name)
                age_group = ", ".join(audience_names) if audience_names else extract_age_group(f"{title} {description}")
                categories = []
                for cat in item.get("categories_arr", []) or []:
                    name = clean_text(cat.get("name", ""))
                    if name:
                        categories.append(name)
                program_type = ", ".join(categories) if categories else "Not found"
                events.append({
                    "Library": "Glencoe",
                    "Title": title,
                    "Date": date_str,
                    "Time": time_str,
                    "Location": location,
                    "Age Group": age_group or "General",
                    "Program Type": program_type,
                    "Description": description,
                    "Link": item.get("url") or "N/A",
                })
                if window_end and start_dt and start_dt.date() > window_end + timedelta(days=30):
                    stop_fetching = True
            except Exception as e:
                logger.warning(f"Error processing Glencoe event: {e}")
                continue
        total_results = data.get("total_results", 0)
        perpage_returned = data.get("perpage", per_page) or per_page
        if page * perpage_returned >= total_results:
            break
        page += 1
    logger.info(f"Found {len(events)} events for Glencoe")
    return events

async def fetch_civicplus_events(library_name: str, base_url: str, cids: List[int]) -> List[Dict[str, Any]]:
    """Generic fetcher for CivicPlus/CivicEngage municipal calendars.

    Fetches `{base_url}/calendar.aspx?view=list&CID={cid}` with a
    startDate/enddate window. Each event <li> carries a hidden schema.org
    Event block with an ISO startDate, description, and venue address.
    The calendar's own title (e.g. "Community Calendar") becomes Program Type.
    """
    logger.info(f"Fetching {library_name} events...")
    base_url = base_url.rstrip("/")

    start_str = START_DATE or compute_date_window()[0]
    days = DAYS_TO_FETCH or DEFAULT_DAYS_TO_FETCH
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        start_dt = datetime.now()
    end_dt = start_dt + timedelta(days=max(days - 1, 0))

    headers = {
        "User-Agent": WNPLD_HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    session = await get_http_session()
    events: List[Dict[str, Any]] = []

    for cid in cids:
        params = {
            "view": "list",
            "CID": str(cid),
            "startDate": start_dt.strftime("%m/%d/%Y"),
            "enddate": end_dt.strftime("%m/%d/%Y"),
        }
        try:
            async with REQUESTS_SEM:
                async with session.get(f"{base_url}/calendar.aspx", params=params, headers=headers) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
        except Exception as e:
            logger.error(f"Failed to fetch {library_name} CID={cid}: {e}")
            continue

        soup = _make_soup(html)
        for cal in soup.select("div.calendar[id^=CID]"):
            title_el = cal.select_one("h2.title")
            calendar_name = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""
            for li in cal.select("ol > li"):
                try:
                    link_el = li.select_one("h3 a[href*='EID']")
                    if not link_el:
                        continue
                    title = clean_text(link_el.get_text(" ", strip=True))
                    if not title:
                        continue
                    link = urljoin(base_url, link_el.get("href") or "")

                    start_el = li.select_one('[itemprop="startDate"]')
                    start_value = _parse_tribe_datetime(start_el.get_text(strip=True) if start_el else None)
                    if not start_value:
                        continue
                    date_value = start_value.strftime("%Y-%m-%d")
                    if start_value.time() == datetime.min.time():
                        time_value = "All Day"
                    else:
                        time_value = start_value.strftime("%I:%M %p").lstrip("0")

                    desc_el = li.select_one('[itemprop="description"]')
                    description = clean_text(desc_el.get_text(" ", strip=True)) if desc_el else ""

                    loc_parts = []
                    place_el = li.select_one('[itemprop="location"]')
                    if place_el:
                        name_el = place_el.select_one('[itemprop="name"]')
                        street_el = place_el.select_one('[itemprop="streetAddress"]')
                        place_name = clean_text(name_el.get_text(strip=True)) if name_el else ""
                        if place_name and place_name.lower() != "event location":
                            loc_parts.append(place_name)
                        if street_el:
                            loc_parts.append(clean_text(street_el.get_text(strip=True)))
                    location = ", ".join([p for p in loc_parts if p]) or library_name

                    events.append({
                        "Library": library_name,
                        "Title": title,
                        "Date": date_value,
                        "Time": time_value,
                        "Location": location,
                        "Age Group": extract_age_group(f"{title} {description}"),
                        "Program Type": calendar_name or "Not found",
                        "Description": description or "Not found",
                        "Link": link,
                    })
                except Exception as e:
                    logger.debug(f"Error parsing {library_name} event: {e}")
                    continue

    logger.info(f"Found {len(events)} events for {library_name}")
    return events

def _infer_event_year(month: int, day: int, start_dt: datetime) -> int:
    """Pick the year for a month/day with no explicit year: the one that puts
    the date on/after the scrape window start (allowing a small grace period)."""
    try:
        candidate = datetime(start_dt.year, month, day)
    except ValueError:
        return start_dt.year
    if candidate.date() < start_dt.date() - timedelta(days=45):
        return start_dt.year + 1
    return start_dt.year


_MONTH_NAMES = {name: i for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


async def fetch_cbg_events() -> List[Dict[str, Any]]:
    """Fetch Chicago Botanic Garden events from its Drupal calendar listing.

    `chicagobotanic.org/calendar?range_start=&range_end=` renders event cards;
    cards with a concrete "Weekday, Month D" line are emitted, undated
    ongoing-program cards (classes, exhibits) are skipped.
    """
    library_name = "Chicago Botanic Garden"
    logger.info(f"Fetching {library_name} events...")
    start_str = START_DATE or compute_date_window()[0]
    days = DAYS_TO_FETCH or DEFAULT_DAYS_TO_FETCH
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        start_dt = datetime.now()
    end_dt = start_dt + timedelta(days=max(days - 1, 0))

    url = (
        "https://www.chicagobotanic.org/calendar"
        f"?range_start={start_dt.strftime('%Y-%m-%d')}&range_end={end_dt.strftime('%Y-%m-%d')}"
    )
    session = await get_http_session()
    try:
        async with REQUESTS_SEM:
            async with session.get(url, headers=WNPLD_HEADERS) as resp:
                resp.raise_for_status()
                html = await resp.text()
    except Exception as e:
        logger.error(f"Failed to fetch {library_name}: {e}")
        return []

    date_re = re.compile(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})"
    )
    time_re = re.compile(r"(\d{1,2})(?::(\d{2}))?(?:\s*[–—-].*?)?\s*(a\.m\.|p\.m\.)", re.I)

    events: List[Dict[str, Any]] = []
    soup = _make_soup(html)
    for card in soup.select("article.card--calendar"):
        try:
            title_el = card.select_one("h2 a")
            if not title_el:
                continue
            title = clean_text(title_el.get_text(" ", strip=True))
            link = urljoin("https://www.chicagobotanic.org", title_el.get("href") or "")
            body_el = card.select_one(".card__body")
            body_text = body_el.get_text("\n", strip=True) if body_el else ""

            dm = date_re.search(body_text)
            if not title or not dm:
                continue
            month, day = _MONTH_NAMES[dm.group(1)], int(dm.group(2))
            year = _infer_event_year(month, day, start_dt)
            date_value = f"{year:04d}-{month:02d}-{day:02d}"

            tm = time_re.search(body_text)
            if tm:
                hour, minute = int(tm.group(1)), int(tm.group(2) or 0)
                meridiem = "PM" if tm.group(3).lower().startswith("p") else "AM"
                time_value = f"{hour}:{minute:02d} {meridiem}"
            else:
                time_value = "All Day"

            bare_date_re = re.compile(
                r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}"
            )
            weekday_only_re = re.compile(
                r"^(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)s?[\s&,–—:-]*(?:and\s+)?)+$", re.I
            )
            lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]
            location = ""
            for ln in reversed(lines):
                if ("$" in ln or time_re.search(ln) or bare_date_re.search(ln)
                        or weekday_only_re.match(ln)
                        or ln.lower() in ("free", "members-only")):
                    continue
                location = clean_text(ln)
                break

            events.append({
                "Library": library_name,
                "Title": title,
                "Date": date_value,
                "Time": time_value,
                "Location": location or "Chicago Botanic Garden, Glencoe",
                "Age Group": extract_age_group(f"{title} {body_text}"),
                "Program Type": "Not found",
                "Description": clean_text(body_text.replace("\n", " ")) or "Not found",
                "Link": link,
            })
        except Exception as e:
            logger.debug(f"Error parsing {library_name} card: {e}")
            continue

    logger.info(f"Found {len(events)} events for {library_name}")
    return events


async def fetch_glenview_parks_events() -> List[Dict[str, Any]]:
    """Fetch Glenview Park District events from its custom WP REST API
    (`/wp-json/events/v1/all/{year}/{month}`)."""
    library_name = "Glenview Park District"
    logger.info(f"Fetching {library_name} events...")
    start_str = START_DATE or compute_date_window()[0]
    days = DAYS_TO_FETCH or DEFAULT_DAYS_TO_FETCH
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        start_dt = datetime.now()
    end_dt = start_dt + timedelta(days=max(days - 1, 0))

    months = []
    cursor = datetime(start_dt.year, start_dt.month, 1)
    while cursor <= end_dt:
        months.append((cursor.year, cursor.month))
        cursor = datetime(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)

    session = await get_http_session()
    events: List[Dict[str, Any]] = []
    for year, month in months:
        api_url = f"https://glenviewparks.org/wp-json/events/v1/all/{year}/{month}"
        try:
            async with REQUESTS_SEM:
                async with session.get(api_url, headers=WNPLD_HEADERS) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch {library_name} {year}-{month}: {e}")
            continue

        month_objs = data if isinstance(data, list) else [data]
        for month_obj in month_objs:
            if not isinstance(month_obj, dict):
                continue
            for day_obj in month_obj.get("calendar") or []:
                try:
                    raw_date = ((day_obj.get("date") or {}).get("date") or "")[:10]
                    day_date = datetime.strptime(raw_date, "%Y-%m-%d")
                except (ValueError, AttributeError):
                    continue
                if not (start_dt.date() <= day_date.date() <= end_dt.date()):
                    continue
                for item in day_obj.get("events") or []:
                    try:
                        title = clean_text(item.get("title"))
                        if not title:
                            continue
                        if item.get("allDay"):
                            time_value = "All Day"
                        else:
                            times = item.get("times") or []
                            raw_time = (times[0].get("start_time") if times and isinstance(times[0], dict) else "") or ""
                            t = parse_time_to_sortable(raw_time.upper())
                            time_value = t.strftime("%I:%M %p").lstrip("0") if t != datetime.min.time() else "All Day"
                        locations = item.get("location") or []
                        location = ", ".join(
                            clean_text(l.get("name")) for l in locations
                            if isinstance(l, dict) and l.get("name")
                        ) or library_name
                        description = html_to_text(item.get("excerpt") or item.get("details") or "")
                        price = clean_text(item.get("price"))
                        if price:
                            description = f"{description} (Price: {price})".strip()
                        events.append({
                            "Library": library_name,
                            "Title": title,
                            "Date": day_date.strftime("%Y-%m-%d"),
                            "Time": time_value,
                            "Location": location,
                            "Age Group": extract_age_group(f"{title} {description}"),
                            "Program Type": clean_text(item.get("eventType")) or "Not found",
                            "Description": description or "Not found",
                            "Link": item.get("permalink") or item.get("eventPage") or "N/A",
                        })
                    except Exception as e:
                        logger.debug(f"Error parsing {library_name} event: {e}")
                        continue

    logger.info(f"Found {len(events)} events for {library_name}")
    return events


async def fetch_morton_grove_parks_events() -> List[Dict[str, Any]]:
    """Fetch Morton Grove Park District events from its Modern Events Calendar
    (MEC) grid at /events-calendar-v2/."""
    library_name = "Morton Grove Park District"
    logger.info(f"Fetching {library_name} events...")
    start_str = START_DATE or compute_date_window()[0]
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        start_dt = datetime.now()

    # The site's WAF rejects aiohttp requests; plain `requests` passes.
    try:
        html = await _wnpld_request_async("https://mortongroveparks.com/events-calendar-v2/")
    except Exception as e:
        logger.error(f"Failed to fetch {library_name}: {e}")
        return []

    date_re = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})"
    )
    events: List[Dict[str, Any]] = []
    soup = _make_soup(html)
    for art in soup.select(".mec-event-article"):
        try:
            title_el = art.select_one(".mec-event-title a")
            if not title_el:
                continue
            title = clean_text(title_el.get_text(" ", strip=True))
            link = title_el.get("href") or "N/A"

            date_el = art.select_one(".mec-start-date-label")
            dm = date_re.search(date_el.get_text(" ", strip=True)) if date_el else None
            if not title or not dm:
                continue
            month, day = _MONTH_NAMES[dm.group(1)], int(dm.group(2))
            year = _infer_event_year(month, day, start_dt)
            date_value = f"{year:04d}-{month:02d}-{day:02d}"

            time_el = art.select_one(".mec-start-time")
            raw_time = time_el.get_text(strip=True).upper() if time_el else ""
            t = parse_time_to_sortable(raw_time)
            time_value = t.strftime("%I:%M %p").lstrip("0") if t != datetime.min.time() else "All Day"

            loc_el = art.select_one(".mec-grid-event-location")
            location = clean_text(loc_el.get_text(" ", strip=True)) if loc_el else ""

            events.append({
                "Library": library_name,
                "Title": title,
                "Date": date_value,
                "Time": time_value,
                "Location": location or library_name,
                "Age Group": extract_age_group(title),
                "Program Type": "Not found",
                "Description": "Not found",
                "Link": link,
            })
        except Exception as e:
            logger.debug(f"Error parsing {library_name} event: {e}")
            continue

    logger.info(f"Found {len(events)} events for {library_name}")
    return events


async def fetch_evanston_city_events() -> List[Dict[str, Any]]:
    """Fetch City of Evanston community events from its Revize CMS calendar feed.

    The calendar page (cityofevanston.org/calendar.php) is rendered client-side
    by the revizeCalendar plugin, which loads all events as JSON from
    `calendar_data_handler.php?webspace=evanstonil`. The feed mixes three
    calendars; only `primary_calendar_name == "Events"` holds community events
    (festivals, markets, concerts) — "Meetings" and "City Council" are skipped.
    Start times of midnight or >= 11:50 PM are Revize all-day placeholders.
    """
    library_name = "Evanston City"
    logger.info(f"Fetching {library_name} events...")
    base_url = "https://www.cityofevanston.org"
    feed_url = (
        f"{base_url}/_assets_/plugins/revizeCalendar/calendar_data_handler.php"
        "?webspace=evanstonil&relative_revize_url=//cms6.revize.com&protocol=https:"
    )

    start_str = START_DATE or compute_date_window()[0]
    days = DAYS_TO_FETCH or DEFAULT_DAYS_TO_FETCH
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        start_dt = datetime.now()
    end_dt = start_dt + timedelta(days=max(days - 1, 0))

    session = await get_http_session()
    try:
        async with REQUESTS_SEM:
            async with session.get(feed_url, headers=WNPLD_HEADERS) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.error(f"Failed to fetch {library_name}: {e}")
        return []
    if not isinstance(data, list):
        logger.error(f"Unexpected {library_name} feed payload: {type(data)}")
        return []

    events: List[Dict[str, Any]] = []
    for item in data:
        try:
            if not isinstance(item, dict) or item.get("primary_calendar_name") != "Events":
                continue
            title = clean_text(unescape(item.get("title") or ""))
            if not title:
                continue

            # Recurring events carry an iCal rrule whose single `start` is often
            # months before the window; expand it into per-occurrence dates.
            occurrences = _evanston_occurrences(item, start_dt, end_dt)
            if not occurrences:
                continue

            description = html_to_text(unquote(item.get("desc") or ""))
            location = clean_text(item.get("location") or "")

            link = (item.get("url") or "").strip()
            if link and not re.match(r"^https?://", link):
                link = f"https://{link}" if "." in link.split("/")[0] else urljoin(base_url, link)

            for start_value in occurrences:
                # Midnight and ~11:55 PM starts are the feed's all-day placeholders.
                if start_value.time() == datetime.min.time() or (
                    start_value.hour == 23 and start_value.minute >= 50
                ):
                    time_value = "All Day"
                else:
                    time_value = start_value.strftime("%I:%M %p").lstrip("0")

                events.append({
                    "Library": library_name,
                    "Title": title,
                    "Date": start_value.strftime("%Y-%m-%d"),
                    "Time": time_value,
                    "Location": location or "Evanston, IL",
                    "Age Group": extract_age_group(f"{title} {description}"),
                    "Program Type": "City Event",
                    "Description": description or "Not found",
                    "Link": link or "N/A",
                })
        except Exception as e:
            logger.debug(f"Error parsing {library_name} event: {e}")
            continue

    logger.info(f"Found {len(events)} events for {library_name}")
    return events


def _evanston_occurrences(item: Dict[str, Any], start_dt: datetime, end_dt: datetime) -> List[datetime]:
    """Return the in-window start datetimes for an Evanston feed item.

    Non-recurring items yield their single `start` if it falls in the window.
    Recurring items (with an iCal `rrule`) are expanded to every occurrence
    inside the window — otherwise weekly series whose base `start` predates the
    window would be dropped entirely.
    """
    window_start = datetime.combine(start_dt.date(), datetime.min.time())
    window_end = datetime.combine(end_dt.date(), datetime.max.time())

    rrule_str = item.get("rrule")
    if rrule_str:
        try:
            from dateutil.rrule import rrulestr
            ruleset = rrulestr(rrule_str, forceset=True)
            return [d for d in ruleset.between(window_start, window_end, inc=True)]
        except Exception as e:
            logger.debug(f"Failed to expand Evanston rrule '{str(rrule_str)[:40]}': {e}")
            # Fall through to the single-start handling below.

    start_value = _parse_tribe_datetime(item.get("start"))
    if start_value and window_start.date() <= start_value.date() <= window_end.date():
        return [start_value]
    return []


def _wnpld_clean_time(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    normalized = raw.replace("\u2013", "-").replace("\u2014", "-")
    return clean_text(normalized)

def _wnpld_request(url: str) -> str:
    resp = requests.get(url, headers=WNPLD_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text

async def _wnpld_request_async(url: str) -> str:
    async with REQUESTS_SEM:
        return await asyncio.to_thread(_wnpld_request, url)

def _wnpld_parse_listing(html: str, base_url: str, library_name: str, default_location: str, categories_as_location: bool = True) -> List[Dict[str, Any]]:
    """Parse a LibraryMarket LibraryCalendar upcoming-events listing into event dicts.

    `categories_as_location`: on WNPLD the listing's categories chip holds the
    branch name; on single-branch sites (PHPL, Vernon Area) it holds program
    types, so it must not be used as the location.
    """
    if not html:
        return []
    soup = _make_soup(html)
    events = []
    for card in soup.select("article.event-card"):
        link_el = card.select_one("a.lc-event__link")
        if not link_el:
            link_el = card.find("a", href=re.compile(r"^/event/"))
        if not link_el:
            continue
        title = clean_text(link_el.get_text(" ", strip=True))
        if not title:
            continue
        href = link_el.get("href") or ""
        link = urljoin(base_url, href)
        month_el = card.select_one(".lc-date-icon__item--month")
        day_el = card.select_one(".lc-date-icon__item--day")
        year_el = card.select_one(".lc-date-icon__item--year")
        month = clean_text(month_el.get_text(strip=True)) if month_el else ""
        day = clean_text(day_el.get_text(strip=True)) if day_el else ""
        year = clean_text(year_el.get_text(strip=True)) if year_el else ""
        date_str = f"{month} {day} {year}".strip() if month and day and year else "Not found"
        time_el = card.select_one(".lc-event-info-item--time")
        time_str = _wnpld_clean_time(time_el.get_text(" ", strip=True) if time_el else "") or "Not found"
        age_el = card.select_one(".lc-event-info__item--colors")
        age_group = clean_text(age_el.get_text(" ", strip=True)) if age_el else ""
        branch_el = card.select_one(".lc-event-info__item--categories")
        branch = clean_text(branch_el.get_text(" ", strip=True)) if branch_el else ""
        events.append({
            "Library": library_name,
            "Title": title,
            "Date": date_str,
            "Time": time_str,
            "Location": (branch or default_location) if categories_as_location else default_location,
            "Age Group": age_group or "Not found",
            "Program Type": "Not found",
            "Description": "Not found",
            "Link": link,
            "_wnpld_branch": branch,
        })
    return events

def _wnpld_parse_detail(html: str) -> Dict[str, str]:
    """Extract richer details from a WNPLD event detail page."""
    soup = _make_soup(html)
    title_el = soup.find("h1")
    title = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""
    branch_el = soup.select_one(".lc-event-branch")
    room_el = soup.select_one(".lc-event-room")
    date_el = soup.select_one(".lc-event-info-item--date")
    time_el = soup.select_one(".lc-event-info-item--time")
    program_el = soup.select_one(".lc-event__program-types")
    age_el = soup.select_one(".lc-event__age-groups")
    branch = clean_text(branch_el.get_text(" ", strip=True)) if branch_el else ""
    room = clean_text(room_el.get_text(" ", strip=True)) if room_el else ""
    date_str = clean_text(date_el.get_text(" ", strip=True)) if date_el else ""
    time_str = _wnpld_clean_time(time_el.get_text(" ", strip=True) if time_el else "")
    program_type = clean_text(program_el.get_text(" ", strip=True)) if program_el else ""
    program_type = re.sub(r"^Program Type:\s*", "", program_type, flags=re.I).strip()
    age_group = clean_text(age_el.get_text(" ", strip=True)) if age_el else ""
    age_group = re.sub(r"^Age Group:\s*", "", age_group, flags=re.I).strip()
    description = ""
    meta_desc = soup.find("meta", {"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = clean_text(meta_desc["content"])
    if not description:
        og_desc = soup.find("meta", {"property": "og:description"})
        if og_desc and og_desc.get("content"):
            description = clean_text(og_desc["content"])
    location = ""
    if branch and room:
        location = f"{room} at {branch}"
    elif branch:
        location = branch

    return {
        "Title": title,
        "Date": date_str,
        "Time": time_str,
        "Location": location,
        "Program Type": program_type,
        "Age Group": age_group,
        "Description": description or "Not found",
        "_wnpld_branch": branch,
    }

async def _wnpld_enrich_event(event: Dict[str, Any]) -> Dict[str, Any]:
    url = event.get("Link")
    if not url:
        return event
    try:
        html = await _wnpld_request_async(url)
    except Exception as e:
        logger.warning(f"WNPLD detail fetch failed for {url}: {e}")
        return event
    details = _wnpld_parse_detail(html)
    for key in ("Title", "Date", "Time", "Location", "Program Type", "Age Group", "Description", "_wnpld_branch"):
        value = details.get(key)
        if value:
            event[key] = value
    return event

async def fetch_librarycalendar_events(
    library_name: str,
    base_url: str,
    default_location: Optional[str] = None,
    max_pages: int = WNPLD_MAX_PAGES,
    enrich: bool = True,
    keep_branch: bool = False,
    categories_as_location: bool = False,
) -> List[Dict[str, Any]]:
    """Generic fetcher for LibraryMarket LibraryCalendar (Drupal 'lc-') sites.

    Paginates `{base_url}/events/upcoming?page=N` and optionally enriches each
    event from its detail page. Used by WNPLD (Winnetka/Northfield), Prospect
    Heights, and Vernon Area.
    """
    logger.info(f"Fetching {library_name} events...")
    base_url = base_url.rstrip("/")
    events_url = f"{base_url}/events/upcoming"
    default_location = default_location or library_name
    all_events: List[Dict[str, Any]] = []
    for page in range(max_pages):
        url = events_url if page == 0 else f"{events_url}?page={page}"
        try:
            html = await _wnpld_request_async(url)
        except Exception as e:
            logger.error(f"Failed to fetch {library_name} page {page}: {e}")
            break
        page_events = _wnpld_parse_listing(html, base_url, library_name, default_location, categories_as_location)
        if not page_events:
            break
        all_events.extend(page_events)
    # De-duplicate by link
    by_link = {}
    for ev in all_events:
        link = ev.get("Link")
        if link and link not in by_link:
            by_link[link] = ev
    unique_events = list(by_link.values())
    if unique_events and enrich:
        enriched = await asyncio.gather(*(_wnpld_enrich_event(ev) for ev in unique_events))
    else:
        enriched = unique_events
    events = [ev for ev in enriched if isinstance(ev, dict)]
    if not keep_branch:
        for ev in events:
            ev.pop("_wnpld_branch", None)
    logger.info(f"Found {len(events)} events for {library_name}")
    return events

async def fetch_wnpld_events_all() -> List[Dict[str, Any]]:
    """Fetch all Winnetka-Northfield events and enrich from detail pages."""
    global WNPLD_CACHE
    async with WNPLD_CACHE_LOCK:
        if WNPLD_CACHE is not None:
            return WNPLD_CACHE
        WNPLD_CACHE = await fetch_librarycalendar_events(
            "Winnetka-Northfield",
            WNPLD_BASE_URL,
            default_location="Winnetka-Northfield Public Library District",
            keep_branch=True,
            categories_as_location=True,
        )
        return WNPLD_CACHE

async def fetch_wnpld_branch_events(branch_name: str, library_label: str) -> List[Dict[str, Any]]:
    """Filter Winnetka-Northfield events by branch name."""
    all_events = await fetch_wnpld_events_all()
    branch_events = []
    branch_lower = branch_name.lower()
    for event in all_events:
        branch = (event.get("_wnpld_branch") or event.get("Location") or "").lower()
        if branch_lower in branch:
            cleaned = dict(event)
            cleaned["Library"] = library_label
            cleaned.pop("_wnpld_branch", None)
            branch_events.append(cleaned)
    logger.info(f"Found {len(branch_events)} events for {library_label}")
    return branch_events

async def _fetch_skokie_parks_page(session) -> str:
    """Fetch raw HTML for Skokie Park District events."""
    headers = {"User-Agent": "LibraryScraper/1.0 (+https://github.com/)"}
    async with REQUESTS_SEM:
        async with session.get(SKOKIE_PARKS_URL, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.text()

def parse_skokie_parks_html(html: str) -> List[Dict[str, Any]]:
    """Parse the Skokie Park District events listing HTML into event dicts."""
    if not html:
        return []

    events = []
    blocks = re.findall(
        r'<li[^>]*class="calendar-item"[^>]*>(.*?)</li>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    for block in blocks:
        try:
            link_match = re.search(r'<a[^>]+href="([^"]+)"', block)
            link = link_match.group(1).strip() if link_match else "N/A"
            if link and not link.startswith("http"):
                link = f"{SKOKIE_PARKS_BASE.rstrip('/')}/{link.lstrip('/')}"

            raw_text = clean_text(html_to_text(block))
            if not raw_text:
                continue

            header_split = re.split(r"\bDate\b", raw_text, maxsplit=1)
            if len(header_split) < 2:
                continue
            header, remainder = header_split[0].strip(), header_split[1].strip()

            title = header
            title_match = re.match(r"^[A-Za-z]{3,9}\s+\d{1,2}\s+(.*)", header)
            if title_match:
                title = title_match.group(1).strip()

            time_split = re.split(r"\bTime\b", remainder, maxsplit=1)
            date_text = time_split[0].strip()
            after_time = time_split[1] if len(time_split) > 1 else ""

            loc_split = re.split(r"\bLocation\b", after_time, maxsplit=1)
            time_text = loc_split[0].strip()
            location_text = loc_split[1] if len(loc_split) > 1 else ""
            location_text = location_text.replace("Event Details", "").strip()

            date_match = re.search(r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", date_text)
            if not date_match:
                continue
            raw_date = date_match.group(1)

            dt_obj = None
            for fmt in ("%B %d, %Y", "%b %d, %Y"):
                try:
                    dt_obj = datetime.strptime(raw_date, fmt)
                    break
                except ValueError:
                    continue
            date_value = dt_obj.strftime("%Y-%m-%d") if dt_obj else raw_date

            time_str = "Not found"
            for candidate in (time_text, date_text):
                if re.search(r"all day", candidate, re.IGNORECASE):
                    time_str = "All Day"
                    break
                time_match = re.search(
                    r"(\d{1,2}:\d{2}\s*[AP]M\s*(?:[-–]\s*\d{1,2}:\d{2}\s*[AP]M)?)",
                    candidate,
                    re.IGNORECASE,
                )
                if time_match:
                    time_str = time_match.group(1).replace(" - ", "–").strip()
                    break

            location_clean = clean_text(location_text) or "Skokie Park District"
            age_group = extract_age_group(f"{title} {date_text} {location_clean}") or "General"
            description = "See event page for details"

            events.append({
                "Library": "Skokie Park District",
                "Title": title,
                "Date": date_value,
                "Time": time_str,
                "Location": location_clean,
                "Age Group": age_group,
                "Program Type": "Not found",
                "Description": description,
                "Link": link or "N/A",
            })
        except Exception as e:
            logger.debug(f"Error parsing Skokie Parks event block: {e}")
            continue

    return events

async def fetch_skokie_parks_events() -> List[Dict[str, Any]]:
    """Fetch and parse Skokie Park District events from the public listing."""
    logger.info("Fetching Skokie Park District events...")
    try:
        session = await get_http_session()
        html = await retry_with_backoff(_fetch_skokie_parks_page, session)
    except Exception as e:
        logger.error(f"Failed to fetch Skokie Park District events: {e}")
        return []

    events = parse_skokie_parks_html(html)
    logger.info(f"Found {len(events)} events for Skokie Park District")
    return events

# UPDATED: Changed to use AsyncFirecrawl
async def _fetch_skokie_content(app: AsyncFirecrawl, url: str) -> str:
    """Fetch content from Skokie with error handling."""
    response = await firecrawl_scrape(app, url=url, only_main_content=True)
    return response.markdown if hasattr(response, "markdown") else ""

async def fetch_skokie_events() -> List[Dict[str, Any]]:
    """Fetch Skokie events using Firecrawl. MODIFIED: Remove age filtering."""
    logger.info("Fetching Skokie events (ALL EVENTS)...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Skokie fetch")
        return []
    app = AsyncFirecrawl(api_key=FIRECRAWL_API_KEY)  # UPDATED: Changed from AsyncFirecrawlApp
    
    # Use the list view without age group filtering
    url = "https://www.skokielibrary.info/events/list"
    
    try:
        markdown = await retry_with_backoff(_fetch_skokie_content, app, url)
        if not markdown:
            logger.warning("No markdown content received from Skokie")
            return []
    except ValueError as e:
        logger.error(f"Invalid response from Skokie API: {e}")
        return []
    except ConnectionError as e:
        logger.error(f"Connection error while fetching Skokie events: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching Skokie events: {e}", exc_info=True)
        return []

    if not markdown:
        return []

    # Parse events from markdown
    all_events = []

    # Debug: Write sample of raw markdown to file for investigation
    try:
        with open('/tmp/skokie_debug.txt', 'w') as f:
            f.write("=== SKOKIE RAW MARKDOWN SAMPLE ===\n")
            f.write(f"First 3000 chars:\n{markdown[:3000]}\n")
            f.write("=== END SAMPLE ===\n")
        logger.info("Skokie debug content written to /tmp/skokie_debug.txt")
    except Exception as e:
        logger.debug(f"Failed to write debug file: {e}")

    try:
        # In list view, events are separated by "View Details" links
        event_blocks = re.split(r'(?=\[([^\]]+)\]\([^)]+\)\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))', markdown)
        
        for block in event_blocks:
            if not block.strip() or len(block) < 50:
                continue
                
            # Extract event title and link
            title_match = re.search(r'^\[([^\]]+)\]\(([^)]+)\)', block.strip())
            if not title_match:
                continue
                
            event_title = title_match.group(1).strip()
            event_link = title_match.group(2).strip()
            
            # Skip non-events
            if re.search(r'library\s+closed|closing', event_title, re.IGNORECASE):
                continue
            
            # Extract date
            date_match = re.search(
                r'((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+'
                r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
                r'\s+\d{1,2},\s+\d{4})',
                block
            )
            if not date_match:
                continue
                
            event_date = date_match.group(1)
            
            # Extract time (support hyphen and en-dash ranges)
            time_str = "Not found"
            time_match = re.search(
                r'(\d{1,2}:\d{2}[ap]m\s*[\-–]\s*\d{1,2}:\d{2}[ap]m|\d{1,2}:\d{2}[ap]m|All\s+Day)',
                block,
                re.IGNORECASE
            )
            if time_match:
                time_str = time_match.group(1).replace(' - ', '–')
            
            # Enhanced location extraction for Skokie
            location = "Skokie Public Library"  # Default

            # Look for location/room information in the event block
            location_patterns = [
                # Standard location markers
                r'Location:\s*([^\n,]+)',
                r'Room:\s*([^\n,]+)',
                r'Event location:\s*([^\n,]+)',
                r'Meeting Room\s*([A-Z\d]+)',
                r'Library:\s*([^\n,]+)',
                r'Venue:\s*([^\n,]+)',
                # Skokie-specific patterns found in raw data
                r'in\s+the\s+(North\s+Courtyard|South\s+Courtyard|[\w\s]+\s+(?:Courtyard|Room|Hall|Center))',
                r'at\s+(Terminal\s+Park|[\w\s]+\s+(?:Park|Center|Hall|Room|Library|Building))',
                r'explore\s+nature\s+in\s+the\s+([\w\s]+)',
                r'Celebrate.*?at\s+([\w\s]+\s+(?:Park|Center))',
                # General patterns
                r'at\s+([\w\s]+\s+(?:Room|Hall|Center|Library|Building))',
                r'in\s+the\s+([\w\s]+\s+(?:Room|Hall|Center))'
            ]

            for pattern in location_patterns:
                loc_match = re.search(pattern, block, re.IGNORECASE)
                if loc_match:
                    potential_location = clean_text(loc_match.group(1))
                    if potential_location and len(potential_location.strip()) > 2:
                        # Clean up the location name
                        potential_location = potential_location.strip()

                        # Handle offsite locations differently
                        if ("park" in potential_location.lower() or
                            "offsite" in block.lower() or
                            potential_location.lower() in ["terminal park"]):
                            location = potential_location  # Keep offsite locations as-is
                        else:
                            # For onsite locations, add Skokie if not present
                            if "skokie" not in potential_location.lower():
                                location = f"{potential_location} at Skokie Public Library"
                            else:
                                location = potential_location
                        break

            # Extract age group from content
            age_group = extract_age_group(f"{event_title} {block}")

            # Extract description - look for substantial text between age group info and "View Details"
            description = "Not found"
            
            # Find text after age group section and before "View Details"
            age_section_end = re.search(r'Age Group:.*?(?=\n\n)', block, re.DOTALL)
            view_details_start = re.search(r'\[View Details\]', block)
            
            if age_section_end and view_details_start:
                desc_section = block[age_section_end.end():view_details_start.start()]
                
                # Extract meaningful sentences
                desc_lines = []
                for line in desc_section.split('\n'):
                    line = line.strip()
                    # Look for substantial descriptive sentences
                    if (line and 
                        len(line) > 20 and
                        not re.search(r'This event is in the|Event Type:|Age Group:|Registration Required', line, re.IGNORECASE) and
                        re.search(r'[.!?]$', line)):  # Ends with proper punctuation
                        desc_lines.append(line)
                
                if desc_lines:
                    description = clean_text(" ".join(desc_lines))
                    
            all_events.append({
                "Library": "Skokie",
                "Title": event_title,
                "Date": event_date,
                "Time": time_str,
                "Location": location,
                "Age Group": age_group,
                "Program Type": "Not found", 
                "Description": description,
                "Link": event_link
            })
                
    except Exception as e:
        logger.error(f"Error parsing Skokie markdown: {e}")
        return []

    logger.info(f"Found {len(all_events)} events for Skokie")
    return all_events

# -------------------------
# CHICAGO PARK DISTRICT HELPERS (FIXED)
# -------------------------

async def _fetch_cpd_listing_page(session, page: int) -> str:
    """Fetch one page of the CPD events listing using direct HTTP requests."""
    url = f"{CPD_EVENTS_LIST_URL}?page={page}"
    headers = {"User-Agent": "LibraryScraper/1.0 (+https://github.com/)"}
    async with CPD_SEM:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.text()

async def _fetch_cpd_event_detail(session, url: str) -> Dict[str, Any] | None:
    """Fetch and parse a single CPD event page using verified logic."""
    if not url.startswith("http"):
        url = f"{CPD_BASE_URL}{url}"

    try:
        headers = {"User-Agent": "LibraryScraper/1.0 (+https://github.com/)"}
        async with CPD_SEM:
            async with session.get(url, timeout=10, headers=headers) as resp:
                resp.raise_for_status()
                html = await resp.text()

        # Small delay to be respectful
        await asyncio.sleep(0.5)

        # Parse HTML using BeautifulSoup (via helper)
        soup = _make_soup(html)
        details = {
            "Description": "Not found",
            "Time": "Not found",
            "Date": "Not found",
            "Location": "Chicago Park District",
        }

        # 1. Extract Title
        title_elem = soup.find('h1', class_='page-header') or soup.find('h1')
        title = clean_text(title_elem.get_text()) if title_elem else "Untitled Event"

        # 2. Extract Date & Time (Sibling Strategy)
        date_label = soup.find(string=re.compile(r'Date and Time', re.IGNORECASE))
        if date_label:
            label_parent = date_label.parent
            content_container = label_parent.find_next_sibling()

            full_text = (
                content_container.get_text(separator=" ", strip=True)
                if content_container
                else label_parent.parent.get_text(separator=" ", strip=True)
            )

            # Date
            date_match = re.search(r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})', full_text)
            if date_match:
                details["Date"] = date_match.group(1)

            # Time
            time_match = re.search(
                r'(\d{1,2}:\d{2}\s*[AP]M\s*[-–]\s*\d{1,2}:\d{2}\s*[AP]M)', full_text
            )
            if not time_match:
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)', full_text)
            if time_match:
                details["Time"] = time_match.group(0)

        # 3. Extract Location
        loc_label = soup.find(string=re.compile(r'Location', re.IGNORECASE))
        if loc_label:
            loc_parent = loc_label.parent
            loc_container = loc_parent.find_next_sibling()
            if loc_container:
                raw_loc = loc_container.get_text(separator=" ", strip=True)
                details["Location"] = clean_text(raw_loc)

        # 4. Extract Description
        desc_label = soup.find(string=re.compile(r'^Description$|^About this Event', re.IGNORECASE))
        if desc_label:
            parent = desc_label.find_parent()
            next_elem = parent.find_next_sibling()
            if next_elem:
                details["Description"] = clean_text(next_elem.get_text())
            else:
                details["Description"] = clean_text(parent.get_text().replace(desc_label, ''))

        # Fallback Description
        if details["Description"] == "Not found":
            body = soup.find(class_=re.compile(r'field--name-body', re.IGNORECASE))
            if body:
                details["Description"] = clean_text(body.get_text())

        return {
            "Library": "Chicago Park District",
            "Title": title,
            "Date": details["Date"],
            "Time": details["Time"],
            "Location": details["Location"],
            "Age Group": extract_age_group(f"{title} {details['Description']}"),
            "Program Type": "Recreation",
            "Description": details["Description"],
            "Link": url
        }

    except Exception as e:
        logger.debug(f"Error parsing CPD detail {url}: {e}")
        return None

# Firecrawl-based parser (still available, but not used by the main CPD crawler)
async def _fetch_chicago_parks_content(app: AsyncFirecrawl, page: int = 1) -> str:
    """Fetch content from Chicago Park District with pagination support."""
    url = f"{CHICAGO_PARKS_URL}?page={page}"
    response = await firecrawl_scrape(app, url=url, only_main_content=True)
    return response.markdown if hasattr(response, "markdown") else ""

def parse_chicago_parks_markdown(markdown: str) -> List[Dict[str, Any]]:
    """Parse Chicago Park District events from markdown content."""
    events = []

    if not markdown:
        return events

    # The structure is:
    # Dec
    # 10
    #
    # ### [Event Title](link)
    # [Address](map link)
    # Time

    # Split by event headers (### [Title](link))
    event_pattern = r'### \[([^\]]+)\]\(([^)]+)\)'
    event_matches = list(re.finditer(event_pattern, markdown))

    for i, match in enumerate(event_matches):
        try:
            title = match.group(1).strip()
            event_link = match.group(2).strip()

            # Get the content before this event (to find date)
            start_pos = match.start()

            # Look backwards to find the date
            before_content = markdown[:start_pos]

            # Find the last occurrence of month/day pattern before this event
            date_pattern = r'(\w{3})\s*\n\s*(\d{1,2})\s*\n'
            date_matches = list(re.finditer(date_pattern, before_content))

            if not date_matches:
                continue

            last_date_match = date_matches[-1]
            month_abbr = last_date_match.group(1)
            day = last_date_match.group(2)

            # Convert to full date
            try:
                month_num = datetime.strptime(month_abbr, '%b').month
                current_year = datetime.now().year

                # Handle year rollover
                if month_num < datetime.now().month and datetime.now().month >= 10:
                    current_year += 1

                event_date = datetime(current_year, month_num, int(day))
                formatted_date = event_date.strftime("%A, %B %d, %Y")
            except (ValueError, TypeError):
                continue

            # Get content after this event title until next event or end
            if i + 1 < len(event_matches):
                end_pos = event_matches[i + 1].start()
                event_content = markdown[match.end():end_pos]
            else:
                event_content = markdown[match.end():]

            # Extract location (address in brackets with map link)
            location = "Chicago Park District"
            address_pattern = r'\[([^]]*?(?:\d+\s+[^,]+[^]]*?))\]\([^)]*google\.com/maps[^)]*\)'
            address_match = re.search(address_pattern, event_content)
            if address_match:
                address = clean_text(address_match.group(1))
                # Clean up address formatting
                address = re.sub(r'\\+', ' ', address)  # Remove backslashes
                address = ' '.join(address.split())  # Normalize whitespace
                if address:
                    location = address

            # Extract time
            time_str = "Not found"
            time_pattern = r'(\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M|\d{1,2}:\d{2}\s*[AP]M)'
            time_match = re.search(time_pattern, event_content, re.IGNORECASE)
            if time_match:
                time_str = time_match.group(1).strip()

            # Check if event is cancelled
            is_cancelled = 'cancelled' in event_content.lower()
            if is_cancelled:
                title = f"[CANCELLED] {title}"

            # Extract age group from title and content
            age_group = extract_age_group(f"{title} {event_content}")

            # Create description
            description = "See event page for details"

            # Try to extract any descriptive text that's not address or time
            desc_lines = []
            for line in event_content.split('\n'):
                line = line.strip()
                if (line and
                    not re.search(time_pattern, line, re.IGNORECASE) and
                    not re.search(r'\[.*?\]\(.*?google\.com/maps', line) and
                    not line.lower() in ['cancelled'] and
                    len(line) > 10):
                    desc_lines.append(clean_text(line))

            if desc_lines:
                description = " ".join(desc_lines[:2])

            events.append({
                "Library": "Chicago Park District",
                "Title": title,
                "Date": formatted_date,
                "Time": time_str,
                "Location": location,
                "Age Group": age_group,
                "Program Type": "Recreation",
                "Description": description,
                "Link": event_link if event_link.startswith('http') else f"https://www.chicagoparkdistrict.com{event_link}"
            })

        except Exception as e:
            logger.debug(f"Error parsing Chicago Parks event: {e}")
            continue

    return events

async def fetch_chicago_parks_events() -> List[Dict[str, Any]]:
    """Crawls Chicago Park District events with detailed page fetching."""
    logger.info("Fetching Chicago Park District events...")
    session = await get_http_session()

    # 1. Crawl Listing Pages to find event URLs
    # Dynamically fetch all pages until no more events found
    event_urls = set()
    page = 0
    max_pages = 30  # Safety limit to prevent infinite loops

    try:
        while page < max_pages:
            try:
                html = await retry_with_backoff(_fetch_cpd_listing_page, session, page)
                soup = _make_soup(html)

                # Find links that look like event pages
                # CPD usually uses /events/event-slug
                links = soup.find_all('a', href=re.compile(r'^/events/[^/]+$'))
                page_event_count = 0

                for link in links:
                    href = link.get('href')
                    # Filter out non-events and short/empty links
                    if (href and
                        len(href) > 8 and
                        href != '/events/map' and
                        href not in event_urls):
                        event_urls.add(href)
                        page_event_count += 1

                logger.info(f"Page {page}: Found {page_event_count} new event links (total: {len(event_urls)})")

                # Stop if no new events found on this page
                if page_event_count == 0:
                    logger.info(f"No new events found on page {page}, stopping crawl")
                    break

                page += 1

                if page % 5 == 0:
                    logger.debug(f"CPD progress: crawled up to page {page}, total links {len(event_urls)}")

                # Add delay between listing pages to be respectful
                if page < max_pages:
                    await asyncio.sleep(2)

            except Exception as e:
                logger.warning(f"Failed to fetch CPD listing page {page}: {e}")
                break
    except Exception as e:
        logger.error(f"CPD crawler failed: {e}")
        return []

    logger.info(f"Found {len(event_urls)} unique CPD event links. Fetching details...")

    # 2. Fetch Details Concurrently
    tasks = [_fetch_cpd_event_detail(session, url) for url in event_urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_events: List[Dict[str, Any]] = []
    invalid_count = 0
    error_count = 0

    for res in results:
        if isinstance(res, dict):
            if res.get("Date") != "Not found":
                valid_events.append(res)
            else:
                invalid_count += 1
                logger.debug(
                    f"CPD event dropped (missing date): title={res.get('Title')} link={res.get('Link')}"
                )
        elif isinstance(res, Exception):
            error_count += 1
            logger.debug(f"CPD detail error: {res}")

    logger.info(f"Successfully extracted {len(valid_events)} CPD events (direct detail parsing)")

    if invalid_count or error_count or not valid_events:
        logger.warning(
            f"CPD scrape diagnostics - valid: {len(valid_events)}, "
            f"invalid_no_date: {invalid_count}, exceptions: {error_count}"
        )

    return valid_events

# ------------- FPDCC (Forest Preserves) -------------

async def _fetch_tribe_page(session, api_url: str, page: int, start_date: str, end_date: str) -> Dict[str, Any]:
    """Fetch a single page from a The Events Calendar (tribe) REST API."""
    params = {
        "page": page,
        "per_page": 50,
        "start_date": start_date,
        "end_date": end_date,
    }
    headers = {"User-Agent": "LibraryScraper/1.0 (+https://github.com/)"}
    async with REQUESTS_SEM:
        async with session.get(api_url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

def _build_tribe_location(item: Dict[str, Any], default_location: str) -> str:
    """Create a readable location string from a tribe event payload."""
    parts: List[str] = []

    def _add(val: Any):
        text = clean_text(val)
        if text and text not in parts:
            parts.append(text)

    venue = item.get("venue")
    if isinstance(venue, dict):
        _add(venue.get("venue") or venue.get("name"))
        for key in ("address", "city", "state", "zip", "country"):
            _add(venue.get(key))
    else:
        _add(venue)

    for key in ("location", "address", "city", "state", "zip", "country"):
        _add(item.get(key))

    return ", ".join([p for p in parts if p]) or default_location

def _parse_tribe_datetime(raw_value: Any) -> datetime | None:
    """Parse tribe start/end datetime strings into a datetime object."""
    if not raw_value:
        return None
    if isinstance(raw_value, datetime):
        return raw_value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw_value), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(raw_value))
    except Exception:
        return None

async def fetch_tribe_events(library_name: str, base_url: str, default_location: Optional[str] = None) -> List[Dict[str, Any]]:
    """Generic fetcher for WordPress sites running The Events Calendar (tribe).

    Works against `{base_url}/wp-json/tribe/events/v1/events`. Used by the
    Forest Preserves and park-district sources.
    """
    logger.info(f"Fetching {library_name} events...")
    api_url = f"{base_url.rstrip('/')}/wp-json/tribe/events/v1/events"
    default_location = default_location or library_name

    # Ensure we have a date window to query
    start_str = START_DATE or compute_date_window()[0]
    days = DAYS_TO_FETCH or DEFAULT_DAYS_TO_FETCH
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        logger.warning(f"Invalid START_DATE '{start_str}', defaulting to today")
        start_dt = datetime.now()
        start_str = start_dt.strftime("%Y-%m-%d")
    end_dt = start_dt + timedelta(days=max(days - 1, 0))
    end_str = end_dt.strftime("%Y-%m-%d")

    try:
        session = await get_http_session()
        page = 1
        events: List[Dict[str, Any]] = []
        total_pages = 1

        while page <= total_pages:
            try:
                data = await retry_with_backoff(_fetch_tribe_page, session, api_url, page, start_str, end_str)
            except Exception as e:
                logger.error(f"Failed to fetch {library_name} page {page}: {e}")
                break

            page_events = data.get("events") or data.get("data") or []
            total_pages = data.get("total_pages") or data.get("totalPages") or total_pages

            for item in page_events:
                try:
                    title = clean_text(item.get("title")) or "Untitled Event"
                    description = html_to_text(item.get("description", "")) or "Not found"
                    link = item.get("url") or item.get("link") or "N/A"
                    all_day = bool(item.get("all_day") or item.get("allDay"))

                    start_value = _parse_tribe_datetime(item.get("start_date") or item.get("start"))
                    if not start_value:
                        continue

                    date_value = start_value.strftime("%Y-%m-%d")
                    time_value = "All Day" if all_day else start_value.strftime("%I:%M %p").lstrip("0")

                    location = _build_tribe_location(item, default_location)
                    age_group = extract_age_group(f"{title} {description}")

                    categories = item.get("categories")
                    program_type = ""
                    if isinstance(categories, list):
                        names = [html_to_text(c.get("name")) for c in categories if isinstance(c, dict)]
                        program_type = ", ".join([n for n in names if n])

                    events.append({
                        "Library": library_name,
                        "Title": title,
                        "Date": date_value,
                        "Time": time_value,
                        "Location": location,
                        "Age Group": age_group,
                        "Program Type": program_type or "Not found",
                        "Description": description,
                        "Link": link,
                    })
                except Exception as e:
                    logger.debug(f"Error parsing {library_name} event: {e}")
                    continue

            if not page_events:
                break
            page += 1

        logger.info(f"Found {len(events)} events for {library_name}")
        return events
    except Exception as e:
        logger.error(f"Unexpected error fetching {library_name} events: {e}")
        return []

async def fetch_fpdcc_events() -> List[Dict[str, Any]]:
    """Fetch events from the Forest Preserves of Cook County site."""
    return await fetch_tribe_events("Forest Preserves of Cook County", "https://fpdcc.com")

# --- REPORT GENERATORS ---

def latex_safe(text: Any) -> str:
    """Return ASCII-only, LaTeX-escaped text."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    ascii_text = text.encode('ascii', 'ignore').decode('ascii')
    return escape_latex(ascii_text)

def generate_pdf_report(all_events: List[Dict[str, Any]], filename: str):
    logger.info("Generating PDF report...")
    if not all_events:
        logger.warning("No events to generate PDF report")
        return
    
    if not isinstance(all_events, list):
        logger.error(f"Invalid events data type: {type(all_events)}")
        return
    doc = Document(geometry_options={"tmargin": "1in", "lmargin": "1in"})
    doc.preamble.append(Command('title', 'Upcoming Library Events by Date'))
    doc.preamble.append(Command('author', 'Event Scraper'))
    doc.preamble.append(Command('date', datetime.now().strftime("%B %d, %Y")))
    doc.append(NoEscape(r'\maketitle'))
    current_date_str = ""
    for event in all_events:
        try:
            if not isinstance(event, dict):
                logger.warning(f"Skipping invalid event type: {type(event)}")
                continue
                
            event_date_str = event.get('Date', 'Date Not Found')
            title = event.get('Title', 'Untitled Event')
            library = event.get('Library', 'Unknown Library')
            time = event.get('Time', 'Time Not Found')
            location = event.get('Location', 'Location Not Found')
            description = event.get('Description', 'No description available')
            age_group = event.get('Age Group', 'Not specified')
            link = event.get('Link', '')
            
            if event_date_str != current_date_str:
                current_date_str = event_date_str
                doc.append(Section(latex_safe(event_date_str)))
                
            with doc.create(Subsection(latex_safe(title), numbering=False)):
                doc.append(Command("textbf", "Library: "))
                doc.append(f"{latex_safe(library)}\n")
                doc.append(Command("textbf", "Time: "))
                doc.append(f"{latex_safe(time)}\n")
                doc.append(Command("textbf", "Location: "))
                doc.append(f"{latex_safe(location)}\n")
                doc.append(Command("textbf", "Age Group: "))
                doc.append(f"{latex_safe(age_group)}\n")
                doc.append(NoEscape(r'\vspace{0.1cm}'))
                doc.append(latex_safe(description))
                
                if link and link != "N/A":
                    doc.append(NoEscape(r'\\\textbf{More Info: }'))
                    safe_link = link.encode('ascii', 'ignore').decode('ascii').replace("_", r"\_")
                    doc.append(Command("texttt", safe_link))
        except Exception as e:
            logger.warning(f"Error processing event for PDF: {e}")
            continue
    try:
        doc.generate_pdf(str(filename), clean_tex=False)
        logger.info(f"PDF report saved to {filename}.pdf")
    except Exception as e:
        logger.error(f"PDF generation failed: {e}", exc_info=True)

def generate_ics_file(all_events: List[Dict[str, Any]], filename: str):
    """Generates an ICS calendar file from the list of events."""
    logger.info("Generating ICS file...")
    
    if not all_events:
        logger.warning("No events to generate ICS file")
        return
        
    if not isinstance(all_events, list):
        logger.error(f"Invalid events data type: {type(all_events)}")
        return
        
    cal = Calendar()
    
    tz = ZoneInfo(TIMEZONE)
    for event in all_events:
        try:
            if not isinstance(event, dict):
                logger.warning(f"Skipping invalid event type: {type(event)}")
                continue
                
            # Skip events that couldn't be parsed correctly
            if 'datetime_obj' not in event or event['datetime_obj'] == datetime.max:
                logger.debug(f"Skipping event with invalid datetime: {event.get('Title', 'Unknown')}")
                continue

            start_time = event.get('time_obj', datetime.min.time())
            start_dt = datetime.combine(event['datetime_obj'].date(), start_time).replace(tzinfo=tz)
            is_all_day = start_time == datetime.min.time()

            # Default end time to 1 hour after start if not specified
            end_dt = start_dt + timedelta(hours=1)
            time_str = event.get('Time', '').lower()
            
            # Try to parse end time from a range (e.g., "10am-11am" or "10am–11am")
            time_parts = re.split(r'–|-', time_str)
            if len(time_parts) > 1:
                end_time_str = time_parts[1].strip()
                formats_to_try = ['%I:%M %p', '%I:%M%p', '%-I:%M %p', '%I %p', '%-I%p']
                for fmt in formats_to_try:
                    try:
                        end_time_obj = datetime.strptime(end_time_str.replace(" ", ""), fmt).time()
                        end_dt = datetime.combine(event['datetime_obj'].date(), end_time_obj).replace(tzinfo=tz)
                        break
                    except ValueError:
                        continue

            # Create the calendar event
            e = Event()
            e.name = clean_text(event.get('Title', 'Untitled Event'))
            e.begin = start_dt
            e.end = end_dt
            # Attach stable UID and URL when present
            raw_link = (event.get('Link') or '').strip()
            uid_src = "|".join([
                str(event.get('Library','')),
                str(event.get('Title','')),
                str(event.get('Date','')),
                str(event.get('Time','')),
                str(event.get('Location',''))
            ])
            e.uid = hashlib.md5(uid_src.encode('utf-8', errors='ignore')).hexdigest() + "@library-scraper"
            if raw_link and raw_link != 'N/A':
                try:
                    e.url = raw_link
                except Exception:
                    pass
            
            # Set as all-day event if applicable
            if is_all_day:
                e.make_all_day()
            
            # Build a detailed description
            description_parts = []
            description = event.get('Description', '')
            age_group = event.get('Age Group', '')
            if age_group and age_group != 'Not specified':
                description_parts.append(f"Age Group: {age_group}")
            if description and description != 'Not found':
                description_parts.append(clean_text(description))
            
            link = event.get('Link', '')
            if link and link != "N/A":
                description_parts.append(f"\nMore Info: {link}")
                
            e.description = "\n".join(description_parts)
            e.location = clean_text(event.get('Location', ''))
            
            cal.events.add(e)
        except Exception as e:
            logger.warning(f"Error processing event for ICS: {e}")
            continue

    try:
        with open(f"{filename}.ics", 'w') as f:
            f.writelines(cal.serialize_iter())
        logger.info(f"ICS file saved to {filename}.ics")
    except Exception as e:
        logger.error(f"ICS file generation failed: {e}", exc_info=True)

# --- MAIN EXECUTION ---

def compute_date_window(cli_args=None) -> tuple[str, int]:
    """Compute START_DATE (YYYY-MM-DD) and DAYS_TO_FETCH from CLI/env with sane defaults."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--start-date", help="Start date in YYYY-MM-DD")
    parser.add_argument("--days", type=int, help="Number of days to fetch")
    parser.add_argument("--start-offset-days", type=int, help="Offset from today for start date")
    try:
        args, _ = parser.parse_known_args(cli_args)
    except SystemExit:
        # In case this is imported and parse_known_args tries to exit, fall back to defaults
        args = parser.parse_args([])

    env_start_date = os.getenv("START_DATE")
    env_days = os.getenv("DAYS_TO_FETCH")
    env_offset = os.getenv("START_OFFSET_DAYS")

    # Determine days
    days = args.days if args.days is not None else (
        int(env_days) if env_days and env_days.isdigit() else DEFAULT_DAYS_TO_FETCH
    )

    # Determine start date
    raw_start = args.start_date or env_start_date
    if raw_start:
        try:
            dt = datetime.strptime(raw_start, "%Y-%m-%d")
            start_date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            logger.warning(f"Invalid START_DATE '{raw_start}', falling back to today")
            start_date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        # Use offset from today
        try:
            offset = args.start_offset_days if args.start_offset_days is not None else int(env_offset) if env_offset else 0
        except ValueError:
            logger.warning(f"Invalid START_OFFSET_DAYS '{env_offset}', using 0")
            offset = 0
        start_dt = datetime.now().date() + timedelta(days=offset)
        start_date_str = start_dt.strftime("%Y-%m-%d")

    return start_date_str, days

def _event_sources() -> List[Tuple[str, Any]]:
    """Single registry of all scrape sources.

    The label doubles as the dedup-key component, the frontend Libraries
    filter value, and the progress-tracking key — it must be unique.
    Lambdas read module globals (START_DATE / DAYS_TO_FETCH) at call time.
    """
    return [
        ("Lincolnwood", lambda: fetch_lincolnwood_events()),
        ("Morton Grove (MGPL)", lambda: fetch_mgpl_events()),
        ("Glencoe", lambda: fetch_glencoe_events()),
        ("Evanston", lambda: fetch_bibliocommons_events("Evanston", EVANSTON_BASE_URL)),
        ("CPL Edgebrook", lambda: fetch_bibliocommons_events("CPL Edgebrook", CPL_BASE_URL, "locations=27")),
        ("CPL Budlong Woods", lambda: fetch_bibliocommons_events("CPL Budlong Woods", CPL_BASE_URL, "locations=16")),
        ("CPL Albany Park", lambda: fetch_bibliocommons_events("CPL Albany Park", CPL_BASE_URL, "locations=3")),
        ("CPL Northtown", lambda: fetch_bibliocommons_events("CPL Northtown", CPL_BASE_URL, "locations=56")),
        ("CPL Rogers Park", lambda: fetch_bibliocommons_events("CPL Rogers Park", CPL_BASE_URL, "locations=61")),
        ("Glenview", lambda: fetch_bibliocommons_events("Glenview", GLENVIEW_BASE_URL)),
        ("Wilmette", lambda: fetch_libnet_events("Wilmette", "wilmette.libnet.info")),
        ("Northbrook", lambda: fetch_libnet_events("Northbrook", "visit.northbrook.info")),
        ("Deerfield", lambda: fetch_libnet_events("Deerfield", "deerfield.libnet.info")),
        ("Winnetka", lambda: fetch_wnpld_branch_events("Winnetka", "Winnetka")),
        ("Northfield", lambda: fetch_wnpld_branch_events("Northfield", "Northfield")),
        ("Skokie Library", lambda: fetch_skokie_events()),
        ("Skokie Parks", lambda: fetch_skokie_parks_events()),
        ("Chicago Parks", lambda: fetch_chicago_parks_events()),
        ("Forest Preserves", lambda: fetch_fpdcc_events()),
        ("Niles", lambda: fetch_libnet_events("Niles", "nmdl.libnet.info")),
        ("Mount Prospect", lambda: fetch_libnet_events("Mount Prospect", "mppl.libnet.info")),
        ("Schaumburg", lambda: fetch_libnet_events("Schaumburg", "schaumburg.libnet.info")),
        ("Des Plaines", lambda: fetch_libnet_events("Des Plaines", "desplaines.libnet.info")),
        ("Park Ridge", lambda: fetch_libnet_events("Park Ridge", "parkridgelibrary.libnet.info")),
        ("Indian Trails", lambda: fetch_libnet_events("Indian Trails", "indiantrails.libnet.info")),
        ("Elk Grove Village", lambda: fetch_libnet_events("Elk Grove Village", "egvpl.libnet.info")),
        ("Highland Park", lambda: fetch_libnet_events("Highland Park", "www.hplibrary.org")),
        ("Wilmette Park District", lambda: fetch_tribe_events("Wilmette Park District", "https://www.wilmettepark.org")),
        ("Northbrook Park District", lambda: fetch_tribe_events("Northbrook Park District", "https://www.nbparks.org")),
        ("Prospect Heights", lambda: fetch_librarycalendar_events("Prospect Heights", "https://www.phpl.info")),
        ("Vernon Area", lambda: fetch_librarycalendar_events("Vernon Area", "https://calendar.vapld.info")),
        ("Village of Skokie", lambda: fetch_civicplus_events("Village of Skokie", "https://www.skokie.org", cids=[22, 40])),
        ("Village of Lincolnwood", lambda: fetch_civicplus_events("Village of Lincolnwood", "https://www.lincolnwoodil.org", cids=[14])),
        ("Lincolnwood Parks & Rec", lambda: fetch_civicplus_events("Lincolnwood Parks & Rec", "https://www.lincolnwoodil.org", cids=[23])),
        ("Chicago Botanic Garden", lambda: fetch_cbg_events()),
        ("Glenview Park District", lambda: fetch_glenview_parks_events()),
        ("Morton Grove Park District", lambda: fetch_morton_grove_parks_events()),
        ("Evanston City", lambda: fetch_evanston_city_events()),
    ]


def source_labels() -> List[str]:
    return [label for label, _ in _event_sources()]


async def _gather_and_filter_events(start_date_str: str, days: int) -> Tuple[List[Dict[str, Any]], bool]:
    """Run all source fetchers, dedup, parse dates, filter to window, sort.

    Sets module globals START_DATE / DAYS_TO_FETCH that downstream fetchers read.
    Returns (events, had_errors). Events include 'datetime_obj' and 'time_obj'
    helper fields used for sorting and downstream generators; callers that
    serialize the events should drop those keys.
    """
    global START_DATE, DAYS_TO_FETCH
    START_DATE, DAYS_TO_FETCH = start_date_str, days

    sources = _event_sources()

    tasks = [run_source_with_progress(label, fn) for label, fn in sources]

    try:
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await close_http_session()

    errors = [res for res in all_results if isinstance(res, Exception)]
    had_errors = bool(errors)
    if had_errors:
        logger.error(f"{len(errors)} sources failed during scrape")

    all_events = [event for res in all_results if isinstance(res, list) for event in res]
    logger.info(f"Total events found: {len(all_events)}")

    unique_events, seen = [], set()
    for event in all_events:
        identifier = (event.get('Library'), event.get('Title'), event.get('Date'), event.get('Time'))
        if identifier not in seen:
            unique_events.append(event)
            seen.add(identifier)
    all_events = unique_events
    logger.info(f"Total events after de-duplication: {len(all_events)}")

    if not all_events:
        return [], had_errors

    try:
        window_start = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        logger.warning("Invalid START_DATE; defaulting filter to today's date")
        window_start = datetime.now().date()
    window_end = window_start + timedelta(days=max(DAYS_TO_FETCH - 1, 0))

    for event in all_events:
        try:
            if not isinstance(event, dict):
                logger.warning(f"Skipping invalid event type: {type(event)}")
                continue

            date_str = event.get('Date', '')
            if not date_str or date_str == 'Not found':
                logger.debug(f"Event '{event.get('Title', 'Unknown')}' has no valid date")
                event['datetime_obj'] = datetime.max
                continue

            date_str_cleaned = date_str.replace(',', '').replace(' at', '')
            dt_obj = None
            date_formats = [
                ("%A %B %d %Y", r'\w+ \w+ \d{1,2} \d{4}'),
                ("%b %d %Y", r'\w{3} \d{1,2} \d{4}'),
                ("%Y-%m-%d", r'\d{4}-\d{2}-\d{2}')
            ]
            for fmt, pattern in date_formats:
                if re.fullmatch(pattern, date_str_cleaned):
                    try:
                        dt_obj = datetime.strptime(date_str_cleaned, fmt)
                        break
                    except ValueError as e:
                        logger.debug(f"Failed to parse date '{date_str_cleaned}' with format '{fmt}': {e}")
                        continue
            if dt_obj:
                event['datetime_obj'] = dt_obj
                event['Date'] = dt_obj.strftime("%A, %B %d, %Y")
            else:
                logger.debug(f"Could not parse date '{date_str_cleaned}' for event '{event.get('Title', 'Unknown')}'")
                event['datetime_obj'] = datetime.max
        except Exception as e:
            logger.warning(f"Error processing event datetime: {e}")
            event['datetime_obj'] = datetime.max

        event['time_obj'] = parse_time_to_sortable(event.get('Time', ''))

    filtered_events = [
        e for e in all_events
        if isinstance(e.get('datetime_obj'), datetime)
        and window_start <= e['datetime_obj'].date() <= window_end
    ]
    filtered_events.sort(key=lambda x: (x.get('datetime_obj', datetime.max), x['Library'], x.get('time_obj', datetime.min.time())))
    return filtered_events, had_errors


async def collect_all_events(start_date_str: Optional[str] = None, days: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run all fetchers and return filtered+sorted events.

    Public entry point for callers outside the CLI (e.g. the Supabase adapter).
    Initializes progress state, computes the date window from CLI/env if not
    provided, runs all source fetchers, and returns the deduplicated event list.
    """
    if start_date_str is None or days is None:
        sd, d = compute_date_window()
        if start_date_str is None:
            start_date_str = sd
        if days is None:
            days = d
    logger.info(f"Using date window: start={start_date_str}, days={days}")
    await init_progress_state()
    events, _ = await _gather_and_filter_events(start_date_str, days)
    return events


async def main():
    start_date_str, days = compute_date_window()
    logger.info(f"Using date window: start={start_date_str}, days={days}")
    try:
        await init_progress_state()
        all_events, had_errors = await _gather_and_filter_events(start_date_str, days)

        if not all_events:
            logger.info("No events found in window")
            await mark_overall_state(
                "completed_with_errors" if had_errors else "completed",
                total_events=0,
                message="No events found in window",
            )
            return

        base_filename = DATA_DIR / f"all_library_events_{datetime.now():%Y%m%d}"
        generate_ics_file(all_events, base_filename)
        generate_pdf_report(all_events, base_filename)

        try:
            df = pd.DataFrame(all_events).drop(columns=['datetime_obj', 'time_obj'], errors='ignore')
            csv_filename = base_filename.with_suffix('.csv')
            df.to_csv(csv_filename, index=False, quoting=csv.QUOTE_ALL)
            logger.info(f"Combined CSV report saved to {csv_filename}")
        except Exception as e:
            logger.error(f"Error generating CSV report: {e}", exc_info=True)

        final_state = "completed_with_errors" if had_errors else "completed"
        await mark_overall_state(final_state, total_events=len(all_events), message="Scrape finished")

        age_group_counts: Dict[str, int] = {}
        for event in all_events:
            age_group = event.get('Age Group', 'Unknown')
            age_group_counts[age_group] = age_group_counts.get(age_group, 0) + 1

        logger.info("Events by Age Group:")
        for age_group, count in sorted(age_group_counts.items()):
            logger.info(f"  {age_group}: {count} events")
    except Exception as exc:
        await mark_overall_state("error", message=str(exc))
        raise


if __name__ == "__main__":
    asyncio.run(main())
