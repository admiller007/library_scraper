import asyncio
import csv
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import argparse
import aiohttp
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any
from pathlib import Path
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
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY', 'fc-fe1ba845d9c748c1871061a8366dcd43')
TIMEZONE = os.getenv('TIMEZONE', 'America/Chicago')

# Date window configuration (computed at runtime in main())
DEFAULT_DAYS_TO_FETCH = 31
START_DATE = None  # will be set in main()
DAYS_TO_FETCH = DEFAULT_DAYS_TO_FETCH  # will be set in main()
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# --- Library Specific Config (MODIFIED: Remove age group filtering) ---
LINCOLNWOOD_URL = 'https://www.lincolnwoodlibrary.org/events/list'  # Remove age group filter
LINCOLNWOOD_DATE_REGEX = r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b'
MGPL_URL = 'https://www.mgpl.org/events/list'  # Remove age group filter
EVANSTON_BASE_URL = 'https://evanstonlibrary.bibliocommons.com/v2/events'
CPL_BASE_URL = 'https://chipublib.bibliocommons.com/v2/events'
GLENCOE_AJAX_URL = "https://calendar.glencoelibrary.org/ajax/calendar/list"
GLENCOE_CALENDAR_ID = "19721"
SKOKIE_PARKS_URL = "https://www.skokieparks.org/events/"
SKOKIE_PARKS_BASE = "https://www.skokieparks.org"
FPDCC_EVENTS_API = "https://fpdcc.com/wp-json/tribe/events/v1/events"
CHICAGO_PARKS_URL = "https://www.chicagoparkdistrict.com/events"

# Pre-compiled regex patterns for performance
COMPILED_PATTERNS = {
    'lincolnwood_date': re.compile(LINCOLNWOOD_DATE_REGEX),
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

# Optimized throttling and connection management
FIRECRAWL_CONCURRENCY = int(os.getenv('FIRECRAWL_CONCURRENCY', '3'))
REQUESTS_CONCURRENCY = int(os.getenv('REQUESTS_CONCURRENCY', '5'))
FIRECRAWL_SEM = asyncio.Semaphore(FIRECRAWL_CONCURRENCY)
REQUESTS_SEM = asyncio.Semaphore(REQUESTS_CONCURRENCY)

# Global session for connection pooling
_http_session = None

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

# --- HELPER FUNCTIONS ---

async def retry_with_backoff(func, *args, max_retries=MAX_RETRIES, delay=RETRY_DELAY, **kwargs):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (ConnectionError, requests.exceptions.ConnectionError, requests.exceptions.Timeout, aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError) as e:
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
        if 'all day' in time_str: return datetime.min.time()
        formats_to_try = ['%I:%M %p', '%I:%M%p', '%-I:%M %p', '%I %p', '%-I%p']
        for fmt in formats_to_try:
            try:
                return datetime.strptime(time_str.replace(" ", ""), fmt).time()
            except ValueError: continue
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

# UPDATED: Changed to use AsyncFirecrawl
async def _fetch_lincolnwood_content(app: AsyncFirecrawl) -> str:
    """Fetch content from Lincolnwood with error handling."""
    response = await firecrawl_scrape(app, url=LINCOLNWOOD_URL, only_main_content=True)
    return response.markdown if hasattr(response, "markdown") else ""

async def fetch_lincolnwood_events() -> List[Dict[str, Any]]:
    logger.info("Fetching Lincolnwood events (ALL EVENTS)...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Lincolnwood fetch")
        return []
    app = AsyncFirecrawl(api_key=FIRECRAWL_API_KEY)  # UPDATED: Changed from AsyncFirecrawlApp
    try:
        markdown = await retry_with_backoff(_fetch_lincolnwood_content, app)
        if not markdown:
            logger.warning("No markdown content received from Lincolnwood")
            return []
    except ValueError as e:
        logger.error(f"Invalid response from Lincolnwood API: {e}")
        return []
    except ConnectionError as e:
        logger.error(f"Connection error while fetching Lincolnwood events: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching Lincolnwood events: {e}", exc_info=True)
        return []
    if not markdown: return []
    try:
        events_raw = markdown.split("### ")[1:]
    except (IndexError, AttributeError) as e:
        logger.error(f"Error parsing Lincolnwood markdown structure: {e}")
        return []
    
    all_events = []

    # Common non-event headings that appear on the page; skip these blocks
    non_event_title_patterns = [
        re.compile(r"policy", re.IGNORECASE),
        re.compile(r"library\s+hours", re.IGNORECASE),
        re.compile(r"about\s+the\s+library", re.IGNORECASE),
        re.compile(r"accessibilit(y|ies)", re.IGNORECASE),
        re.compile(r"registration\s+info|how\s+to\s+register", re.IGNORECASE),
    ]
    for raw_text in events_raw:
        try:
            lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]
            if not lines:
                logger.debug("Skipping empty event block")
                continue
            
            title = clean_text(lines[0])
            if not title:
                logger.debug("Skipping event with empty title")
                continue

            # Skip obvious non-event headings such as policies or info pages
            if any(p.search(title) for p in non_event_title_patterns):
                logger.debug(f"Skipping non-event heading: '{title}'")
                continue
            
            time_match = COMPILED_PATTERNS['time_pattern'].search(raw_text)
            time_str = time_match.group(0) if time_match else "Not found"
            if time_str == "Not found":
                logger.debug(f"Skipping event '{title}' - no time found")
                continue
            
            markdown_before = markdown[:markdown.find(raw_text)]
            date_matches = COMPILED_PATTERNS['lincolnwood_date'].findall(markdown_before)
            date_str = date_matches[-1] if date_matches else "Not found"
            
            if date_str == "Not found":
                logger.debug(f"Skipping event '{title}' - no date found")
                continue
            
            description_lines = [clean_text(l) for l in lines[1:] if "register" not in l.lower()]
            description = " ".join(description_lines) if description_lines else "Not found"

            # Enhanced location extraction for Lincolnwood
            location = "Lincolnwood Library"  # Default

            # Look for room information in the content
            room_patterns = [
                r'Room:\s*Meeting Room\s*([A-Z/]+)',
                r'Meeting Room\s*([A-Z/]+)',
                r'Room:\s*([^,\n*]+)',
                r'Library Branch:\s*[^,\n]*Room:\s*([^,\n*]+)'
            ]

            full_content = raw_text
            for pattern in room_patterns:
                room_match = re.search(pattern, full_content, re.IGNORECASE)
                if room_match:
                    room = clean_text(room_match.group(1))
                    if room and room.strip() and "lincolnwood" not in room.lower():
                        location = f"{room} at Lincolnwood Library"
                        break

            # Extract age group from content instead of hardcoding
            age_group = extract_age_group(f"{title} {description}")

            all_events.append({
                "Library": "Lincolnwood",
                "Title": title,
                "Date": date_str,
                "Time": time_str,
                "Location": location,
                "Age Group": age_group,
                "Program Type": "Not found",
                "Description": description,
                "Link": "N/A"
            })
        except Exception as e:
            logger.warning(f"Error processing Lincolnwood event: {e}")
            continue
    logger.info(f"Found {len(all_events)} events for Lincolnwood")
    return all_events

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
    except IndexError: return []
    event_blocks, page_events = re.split(r'-\s+\w{3}\n', events_section), []
    for block in event_blocks[1:]:
        title_match = re.search(r'### \[(.*?)\]\((.*?)\)', block)
        if not title_match: continue
        title, link = title_match.groups()
        
        datetime_match = re.search(r'(\w+,\s+\w+\s+\d{1,2})on.*?(\d{4}),\s*(\d{1,2}:\d{2}[ap]m–\d{1,2}:\d{2}[ap]m)', block)
        if not datetime_match: continue
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
        
        page_events.append({"Library": library_name, "Title": title, "Date": f"{date_part}, {year}", "Time": time_part.replace('–', ' - '), "Location": location, "Age Group": age_group, "Program Type": "Not found", "Description": description, "Link": link})
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
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"https://{base_url}/events"}

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
                                    out.append(s.strip()); break
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
            date_match = re.search(r'((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})', block)
            if not date_match:
                continue
                
            event_date = date_match.group(1)
            
            # Extract time (support hyphen and en-dash ranges)
            time_str = "Not found"
            time_match = re.search(r'(\d{1,2}:\d{2}[ap]m\s*[\-–]\s*\d{1,2}:\d{2}[ap]m|\d{1,2}:\d{2}[ap]m|All\s+Day)', block, re.IGNORECASE)
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
    """Fetch Chicago Park District events using Firecrawl."""
    logger.info("Fetching Chicago Park District events...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Chicago Parks fetch")
        return []

    app = AsyncFirecrawl(api_key=FIRECRAWL_API_KEY)
    all_events = []

    # Try fetching all available pages (212 total events across ~27 pages)
    max_pages = 30  # Set higher to capture all available events

    for page in range(1, max_pages + 1):
        try:
            markdown = await retry_with_backoff(_fetch_chicago_parks_content, app, page)
            if not markdown:
                logger.debug(f"No content found for Chicago Parks page {page}")
                break

            # Debug: Save the markdown content to see what we're getting
            try:
                with open(f'/tmp/chicago_parks_debug_page_{page}.txt', 'w') as f:
                    f.write("=== CHICAGO PARKS RAW MARKDOWN ===\n")
                    f.write(f"Page {page} content length: {len(markdown)}\n")
                    f.write(f"Full content:\n{markdown}\n")
                    f.write("=== END MARKDOWN ===\n")
                logger.info(f"Chicago Parks debug content written to /tmp/chicago_parks_debug_page_{page}.txt")
            except Exception as e:
                logger.debug(f"Failed to write debug file: {e}")

            events_on_page = parse_chicago_parks_markdown(markdown)
            if not events_on_page:
                logger.debug(f"No events found on Chicago Parks page {page}")
                break

            all_events.extend(events_on_page)
            logger.info(f"Page {page}: Found {len(events_on_page)} events (total so far: {len(all_events)})")

            # Stop if we've found all available events (212 total mentioned on page 1)
            if len(all_events) >= 212:
                logger.info(f"Reached expected total of 212 events, stopping at page {page}")
                break

            # Longer delay between pages to avoid rate limiting
            await asyncio.sleep(3)

        except Exception as e:
            # Check if it's a rate limiting error and handle it specially
            if "429" in str(e) or "rate limit" in str(e).lower():
                logger.warning(f"Rate limited on Chicago Parks page {page}. Waiting 60 seconds...")
                await asyncio.sleep(60)
                # Don't break - try to continue with next page after wait
                continue
            else:
                logger.error(f"Error fetching Chicago Parks page {page}: {e}")
                break

    logger.info(f"Found {len(all_events)} events for Chicago Park District")
    return all_events

async def _fetch_fpdcc_page(session, page: int, start_date: str, end_date: str) -> Dict[str, Any]:
    """Fetch a single page of Forest Preserves events."""
    params = {
        "page": page,
        "per_page": 50,
        "start_date": start_date,
        "end_date": end_date,
    }
    headers = {"User-Agent": "LibraryScraper/1.0 (+https://github.com/)"}
    async with REQUESTS_SEM:
        async with session.get(FPDCC_EVENTS_API, params=params, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

def _build_fpdcc_location(item: Dict[str, Any]) -> str:
    """Create a readable location string from the Forest Preserves event payload."""
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

    return ", ".join([p for p in parts if p]) or "Forest Preserves of Cook County"

def _parse_fpdcc_datetime(raw_value: Any) -> datetime | None:
    """Parse Forest Preserves start/end datetime strings into a datetime object."""
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

async def fetch_fpdcc_events() -> List[Dict[str, Any]]:
    """Fetch events from the Forest Preserves of Cook County site."""
    logger.info("Fetching Forest Preserves events...")

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
                data = await retry_with_backoff(_fetch_fpdcc_page, session, page, start_str, end_str)
            except Exception as e:
                logger.error(f"Failed to fetch FPDCC page {page}: {e}")
                break

            page_events = data.get("events") or data.get("data") or []
            total_pages = data.get("total_pages") or data.get("totalPages") or total_pages

            for item in page_events:
                try:
                    title = clean_text(item.get("title")) or "Untitled Event"
                    description = html_to_text(item.get("description", "")) or "Not found"
                    link = item.get("url") or item.get("link") or "N/A"
                    all_day = bool(item.get("all_day") or item.get("allDay"))

                    start_value = _parse_fpdcc_datetime(item.get("start_date") or item.get("start"))
                    if not start_value:
                        continue

                    date_value = start_value.strftime("%Y-%m-%d")
                    time_value = "All Day" if all_day else start_value.strftime("%I:%M %p").lstrip("0")

                    location = _build_fpdcc_location(item)
                    age_group = extract_age_group(f"{title} {description}")

                    events.append({
                        "Library": "Forest Preserves of Cook County",
                        "Title": title,
                        "Date": date_value,
                        "Time": time_value,
                        "Location": location,
                        "Age Group": age_group,
                        "Program Type": "Not found",
                        "Description": description,
                        "Link": link,
                    })
                except Exception as e:
                    logger.debug(f"Error parsing FPDCC event: {e}")
                    continue

            if not page_events:
                break
            page += 1

        logger.info(f"Found {len(events)} events for Forest Preserves")
        return events
    except Exception as e:
        logger.error(f"Unexpected error fetching FPDCC events: {e}")
        return []

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

async def main():
    # Compute date window from CLI/env and set module globals used by fetchers
    global START_DATE, DAYS_TO_FETCH
    START_DATE, DAYS_TO_FETCH = compute_date_window()
    logger.info(f"Using date window: start={START_DATE}, days={DAYS_TO_FETCH}")

    # Define tasks for all library systems (NO age group filtering)
    tasks = [
        fetch_lincolnwood_events(),
        fetch_mgpl_events(),
        fetch_glencoe_events(),
        fetch_bibliocommons_events("Evanston", EVANSTON_BASE_URL),
        fetch_bibliocommons_events("CPL Edgebrook", CPL_BASE_URL, "locations=27"),
        fetch_bibliocommons_events("CPL Budlong Woods", CPL_BASE_URL, "locations=16"),
        fetch_libnet_events("Wilmette", "wilmette.libnet.info"),
        fetch_skokie_events(),
        fetch_skokie_parks_events(),
        fetch_chicago_parks_events(),
        fetch_fpdcc_events(),
        fetch_libnet_events("Niles", "nmdl.libnet.info")
    ]
    
    try:
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # Ensure session cleanup
        await close_http_session()

    all_events = [event for res in all_results if isinstance(res, list) for event in res]
    logger.info(f"Total events found: {len(all_events)}")

    # Remove duplicates
    unique_events, seen = [], set()
    for event in all_events:
        identifier = (event.get('Library'), event.get('Title'), event.get('Date'), event.get('Time'))
        if identifier not in seen:
            unique_events.append(event); seen.add(identifier)
    logger.info(f"Total events after de-duplication: {len(unique_events)}")
    all_events = unique_events

    if not all_events: 
        logger.info("No events found")
        return

    # Determine the requested date window
    try:
        window_start = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        logger.warning("Invalid START_DATE; defaulting filter to today's date")
        window_start = datetime.now().date()
    window_end = window_start + timedelta(days=max(DAYS_TO_FETCH - 1, 0))

    # Process and add datetime objects for sorting and ICS generation
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
            
            # Try different date formats
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

    # Filter events to the requested window
    filtered_events = []
    for event in all_events:
        dt_obj = event.get('datetime_obj')
        if isinstance(dt_obj, datetime) and window_start <= dt_obj.date() <= window_end:
            filtered_events.append(event)
    if not filtered_events:
        logger.info("No events fall within the requested date window")
        return
    all_events = filtered_events

    # Sort events by date, library, and time
    all_events.sort(key=lambda x: (x.get('datetime_obj', datetime.max), x['Library'], x.get('time_obj', datetime.min.time())))

    # Generate reports
    base_filename = DATA_DIR / f"all_library_events_{datetime.now():%Y%m%d}"
    
    # Generate ICS and PDF reports
    generate_ics_file(all_events, base_filename)
    generate_pdf_report(all_events, base_filename)

    # Prepare DataFrame for CSV (dropping temporary datetime helper columns)
    try:
        df = pd.DataFrame(all_events).drop(columns=['datetime_obj', 'time_obj'], errors='ignore')
        csv_filename = base_filename.with_suffix('.csv')
        df.to_csv(csv_filename, index=False, quoting=csv.QUOTE_ALL)
        logger.info(f"Combined CSV report saved to {csv_filename}")
    except Exception as e:
        logger.error(f"Error generating CSV report: {e}", exc_info=True)

    # Print summary by age group
    age_group_counts = {}
    for event in all_events:
        age_group = event.get('Age Group', 'Unknown')
        age_group_counts[age_group] = age_group_counts.get(age_group, 0) + 1
    
    logger.info("Events by Age Group:")
    for age_group, count in sorted(age_group_counts.items()):
        logger.info(f"  {age_group}: {count} events")

if __name__ == "__main__":
    asyncio.run(main())
    
