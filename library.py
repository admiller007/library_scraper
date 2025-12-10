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
from firecrawl import AsyncFirecrawlApp
from ics import Calendar, Event
import hashlib
from pylatex import Document, Section, Subsection, Command
from pylatex.utils import NoEscape, escape_latex
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import time

# --- LOGGING CONFIGURATION ---
# Load environment from .env if present
load_dotenv()
# Configure rotating file logs to avoid unbounded growth
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_root = logging.getLogger()
for _h in list(_root.handlers):
    _root.removeHandler(_h)
_stream = logging.StreamHandler()
_file = RotatingFileHandler('library_events.log', maxBytes=2 * 1024 * 1024, backupCount=3)
for _h in (_stream, _file):
    _h.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    _root.addHandler(_h)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY', 'fc-fe1ba845d9c748c1871061a8366dcd43')
TIMEZONE = os.getenv('TIMEZONE', 'America/Chicago')

# Date window configuration (computed at runtime in main())
# Defaults: start date is today, days is 31 unless overridden by CLI/env
DEFAULT_DAYS_TO_FETCH = 31
# Default LibNet ages. Set to your requested groups by default.
DEFAULT_LIBNET_AGES = ["Grades K-2", "Grades 3-5"]
# Ages to send to LibNet API request (broad to ensure results); we post-filter locally.
DEFAULT_LIBNET_REQUEST_AGES = ["Kids"]
START_DATE = None  # will be set in main()
DAYS_TO_FETCH = DEFAULT_DAYS_TO_FETCH  # will be set in main()
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# --- Library Specific Config ---
LINCOLNWOOD_URL = 'https://www.lincolnwoodlibrary.org/events/list?age_groups%5B2%5D=2'
LINCOLNWOOD_DATE_REGEX = r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b'
MGPL_URL = 'https://www.mgpl.org/events/list?age_groups%5B21%5D=21'
EVANSTON_BASE_URL = 'https://evanstonlibrary.bibliocommons.com/v2/events'
CPL_BASE_URL = 'https://chipublib.bibliocommons.com/v2/events'

# Throttle Firecrawl requests to avoid 429s
FIRECRAWL_CONCURRENCY = int(os.getenv('FIRECRAWL_CONCURRENCY', '1'))
FIRECRAWL_SEM = asyncio.Semaphore(FIRECRAWL_CONCURRENCY)

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
            # Handle 429 rate limits with backoff if detected
            msg = str(e)
            if '429' in msg:
                # Try to parse suggested retry seconds from message
                m = re.search(r'retry after (\d+)s', msg, re.IGNORECASE)
                wait_time = int(m.group(1)) if m else delay * (2 ** attempt)
                if attempt == max_retries - 1:
                    logger.error(f"Rate limited and exceeded retries: {e}")
                    raise
                logger.warning(f"Rate limited (429). Waiting {wait_time}s before retry {attempt+2}/{max_retries}")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Non-retryable aiohttp error: {e}")
                raise
        except Exception as e:
            logger.error(f"Non-retryable error: {e}")
            raise

async def firecrawl_scrape(app: AsyncFirecrawlApp, url: str, **kwargs):
    async with FIRECRAWL_SEM:
        return await app.scrape_url(url=url, **kwargs)

def clean_text(text: str) -> str:
    """Cleans text by removing problematic chars, markdown, and extra whitespace."""
    if not isinstance(text, str):
        logger.debug(f"Invalid text input: {type(text)}")
        return ""
    
    try:
        text = text.encode('ascii', 'ignore').decode('ascii')
        text = text.replace('\u200b', '')
        text = re.sub(r'!?\[.*?\]\(.*?\)', '', text)
        text = text.replace('\n', ' ').strip()
        text = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', text)
        text = text.replace('Event location:', '').strip()
        if len(text) > 10 and text[:len(text)//2].strip() == text[len(text)//2:].strip():
            text = text[:len(text)//2].strip()
        return ' '.join(text.split())
    except Exception as e:
        logger.warning(f"Error cleaning text: {e}")
        return str(text) if text else ""

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

# --- FETCHERS (Your existing fetcher functions remain unchanged) ---

async def _fetch_lincolnwood_content(app: AsyncFirecrawlApp) -> str:
    """Fetch content from Lincolnwood with error handling."""
    response = await firecrawl_scrape(app, url=LINCOLNWOOD_URL, only_main_content=True)
    return response.markdown

async def fetch_lincolnwood_events() -> List[Dict[str, Any]]:
    logger.info("Fetching Lincolnwood events...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Lincolnwood fetch")
        return []
    app = AsyncFirecrawlApp(api_key=FIRECRAWL_API_KEY)
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
            
            time_match = re.search(r'(\d{1,2}:\d{2}[ap]m(?:–\d{1,2}:\d{2}[ap]m)?)', raw_text)
            time_str = time_match.group(0) if time_match else "Not found"
            if time_str == "Not found":
                logger.debug(f"Skipping event '{title}' - no time found")
                continue
            
            markdown_before = markdown[:markdown.find(raw_text)]
            date_matches = re.findall(LINCOLNWOOD_DATE_REGEX, markdown_before)
            date_str = date_matches[-1] if date_matches else "Not found"
            
            if date_str == "Not found":
                logger.debug(f"Skipping event '{title}' - no date found")
                continue
            
            description_lines = [clean_text(l) for l in lines[1:] if "register" not in l.lower()]
            description = " ".join(description_lines) if description_lines else "Not found"
            
            all_events.append({
                "Library": "Lincolnwood", 
                "Title": title, 
                "Date": date_str, 
                "Time": time_str, 
                "Location": "Lincolnwood Library", 
                "Age Group": "Kids", 
                "Program Type": "Not found", 
                "Description": description, 
                "Link": "N/A"
            })
        except Exception as e:
            logger.warning(f"Error processing Lincolnwood event: {e}")
            continue
    logger.info(f"Found {len(all_events)} events for Lincolnwood")
    return all_events

async def _fetch_mgpl_content(app: AsyncFirecrawlApp) -> str:
    """Fetch content from Morton Grove with error handling."""
    response = await firecrawl_scrape(app, url=MGPL_URL)
    return response.markdown

async def fetch_mgpl_events() -> List[Dict[str, Any]]:
    logger.info("Fetching Morton Grove events...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Morton Grove fetch")
        return []
    app = AsyncFirecrawlApp(api_key=FIRECRAWL_API_KEY)
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
                
                loc_match = re.search(r'\*\*Location:\*\*\n(.*?)\n', structured_text)
                if loc_match:
                    location_str = loc_match.group(1).strip()
                
                room_match = re.search(r'\*\*Room:\*\*\n(.*?)\n', structured_text)
                if room_match and room_match.group(1).strip() not in location_str:
                    location_str = f"{room_match.group(1).strip()} at {location_str}"
            
            all_events.append({
                "Library": "Morton Grove", 
                "Title": title, 
                "Date": date_str, 
                "Time": time_str, 
                "Location": location_str, 
                "Age Group": "Kids", 
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
        
        location_match = re.search(r'\n(\[.*?Event location:.*?\]\(.*?\)|Offsite location:.*?)\n', block)
        location = clean_text(location_match.group(1)) if location_match else f"{library_name}"
        
        desc_match = re.search(r'Event location:.*?\n\n(.*?)(?=\n\n(Register for|Join waitlist)|- \[)', block, re.DOTALL)
        description = clean_text(desc_match.group(1)) if desc_match else "Not found"
        
        page_events.append({"Library": library_name, "Title": title, "Date": f"{date_part}, {year}", "Time": time_part.replace('–', ' - '), "Location": location, "Age Group": "Kids/Family", "Program Type": "Not found", "Description": description, "Link": link})
    return page_events

async def fetch_bibliocommons_events(library_name: str, base_url: str, query_params: str) -> List[Dict[str, Any]]:
    """Generic fetcher for any Bibliocommons library, with pagination."""
    logger.info(f"Fetching {library_name} events...")
    app = AsyncFirecrawlApp(api_key=FIRECRAWL_API_KEY)
    all_events, current_page, max_pages = [], 1, 5
    while current_page <= max_pages:
        url = f"{base_url}?{query_params}&page={current_page}"
        logger.debug(f"Fetching page {current_page} for {library_name}...")
        try:
            response = await retry_with_backoff(firecrawl_scrape, app, url=url)
            markdown = response.markdown
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

def fetch_libnet_events(library_name: str, base_url: str) -> List[Dict[str, Any]]:
    logger.info(f"Fetching {library_name} events...")
    api_url = f"https://{base_url}/eeventcaldata"
    # Build ages JSON array for request from configured LIBNET_REQUEST_AGES (broad fetch)
    ages_json = ",".join([f'"{a}"' for a in LIBNET_REQUEST_AGES]) if LIBNET_REQUEST_AGES else ""
    ages_clause = f"[{ages_json}]" if ages_json else "[]"
    payload = {
        "event_type": 0,
        "req": (
            f'{{"private":false,"date":"{START_DATE}","days":{DAYS_TO_FETCH},'
            f'"locations":[],"ages":{ages_clause},"types":[]}}'
        ),
    }
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"https://{base_url}/events"}
    try:
        resp = requests.get(api_url, headers=headers, params=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            logger.error(f"Invalid response format from {library_name}: expected list, got {type(data)}")
            return []
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout error fetching {library_name} events: {e}")
        return []
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error fetching {library_name} events: {e}")
        return []
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching {library_name} events: {e}")
        return []
    except ValueError as e:
        logger.error(f"JSON decode error for {library_name} events: {e}")
        return []
    except requests.RequestException as e:
        logger.error(f"Unexpected request error fetching {library_name} events: {e}", exc_info=True)
        return []
    events = []
    observed_age_labels = set()
    filtered_out = 0
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
            
            # Prefer ages from item if available (robust to different shapes)
            def coerce_labels(val):
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
                            for key in ("name", "title", "label", "text", "value"):
                                s = v.get(key)
                                if isinstance(s, str) and s.strip():
                                    out.append(s.strip()); break
                    return out
                if isinstance(val, dict):
                    for key in ("name", "title", "label", "text", "value"):
                        s = val.get(key)
                        if isinstance(s, str) and s.strip():
                            return [s.strip()]
                    return []
                return []

            age_labels = []
            try:
                age_labels += coerce_labels(item.get("ages"))
                age_labels += coerce_labels(item.get("age"))
                age_labels += coerce_labels(item.get("age_group"))
                age_labels += coerce_labels(item.get("ageGroup"))
                age_labels += coerce_labels(item.get("age_groups"))
                age_labels += coerce_labels(item.get("audiences"))
                age_labels += coerce_labels(item.get("audience"))
            except Exception:
                pass
            # De-duplicate while preserving order
            seen_labels = set()
            age_labels = [x for x in age_labels if not (x in seen_labels or seen_labels.add(x))]
            for lab in age_labels:
                observed_age_labels.add(lab)
            age_group = ", ".join(age_labels) if age_labels else ""

            # Post-filter: keep only events matching configured LIBNET_AGES if provided  
            keep = True
            if LIBNET_AGES:
                def matches_k2(label: str) -> bool:
                    s = label.lower()
                    return bool(
                        re.search(r"\b(k|kindergarten)\s*(to|-|–|—|through|/)\s*2(nd)?\b", s)
                        or re.search(r"\bgrades?\s*k\s*(to|-|–|—|through|/)\s*2\b", s)
                        or "lower elementary" in s
                        or re.search(r"grade\s*k\s*[-–—/]\s*2", s)
                    )
                def matches_35(label: str) -> bool:
                    s = label.lower()
                    return bool(
                        re.search(r"\b3(rd)?\s*(to|-|–|—|through|/)\s*5(th)?\b", s)
                        or re.search(r"\bgrades?\s*3\s*(to|-|–|—|through|/)\s*5\b", s)
                        or "upper elementary" in s
                        or re.search(r"grade\s*3\s*[-–—/]\s*5", s)
                    )

                wanted_k2 = any('k-2' in a.lower() or 'k' in a.lower() and '2' in a for a in LIBNET_AGES)
                wanted_35 = any('3-5' in a.lower() or ('3' in a and '5' in a) for a in LIBNET_AGES)

                item_ages = item.get("ages") if isinstance(item.get("ages"), list) else []
                # If no ages field, check if this is a general "Kids" event (common for LibNet)
                if not item_ages and not age_group:
                    # For LibNet libraries, assume general events are "Kids" if no age specified
                    item_ages = ["Kids"]
                    age_group = "Kids"
                
                normalized = [str(x) for x in item_ages]
                keep = False
                for lab in normalized:
                    if wanted_k2 and matches_k2(lab):
                        keep = True; break
                    if wanted_35 and matches_35(lab):
                        keep = True; break
                    # Accept "Kids" for both K-2 and 3-5 since LibNet libraries use broad categories
                    if lab.lower() == "kids" and (wanted_k2 or wanted_35):
                        keep = True; break

                # Fallback heuristics: title/description
                if not keep:
                    text_blob = f"{item.get('title','')}\n{item.get('description','')}"
                    if wanted_k2 and (
                        re.search(r"\bK\s*[-–—/]?\s*2\b", text_blob, re.IGNORECASE)
                        or re.search(r"Kindergarten.*(to|through|-)\s*2(nd)?", text_blob, re.IGNORECASE)
                        or re.search(r"lower\s+elementary", text_blob, re.IGNORECASE)
                    ):
                        keep = True
                    if wanted_35 and (
                        re.search(r"\b3\s*[-–—/]?\s*5\b", text_blob)
                        or re.search(r"3(rd)?.*(to|through|-)\s*5(th)?", text_blob, re.IGNORECASE)
                        or re.search(r"upper\s+elementary", text_blob, re.IGNORECASE)
                    ):
                        keep = True

            if not keep:
                filtered_out += 1
                continue

            # For LibNet libraries, convert generic "Kids" to more specific age groups based on content
            final_age_group = age_group or "Kids"
            if final_age_group == "Kids" and LIBNET_AGES:
                # Try to determine specific age group from title/description
                text_content = f"{title} {item.get('description', '')}".lower()
                if any(word in text_content for word in ['toddler', 'preschool', 'kindergarten', 'baby', 'infant']):
                    final_age_group = "Grades K-2"
                elif any(word in text_content for word in ['elementary', 'grade 3', 'grade 4', 'grade 5']):
                    final_age_group = "Grades 3-5"
                # Default to K-2 for general kids events since many are storytime/early childhood focused
                elif wanted_k2:
                    final_age_group = "Grades K-2"
                elif wanted_35:
                    final_age_group = "Grades 3-5"

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
                "Location": clean_text(item.get("location", library_name)), 
                "Age Group": final_age_group, 
                "Program Type": "Not found", 
                "Description": clean_text(item.get("description", "")), 
                "Link": event_url
            })
        except Exception as e:
            logger.warning(f"Error processing {library_name} event: {e}")
            continue
    if observed_age_labels:
        sample = sorted(list(observed_age_labels))[:20]
        logger.info(f"Observed age labels for {library_name} (sample): {sample}")
    else:
        logger.info(f"No age labels observed for {library_name}")
    if filtered_out:
        logger.info(f"Filtered out {filtered_out} events for {library_name} not matching ages {LIBNET_AGES}")
    logger.info(f"Found {len(events)} events for {library_name}")
    return events


async def _fetch_skokie_content(app: AsyncFirecrawlApp, url: str) -> str:
    """Fetch content from Skokie with error handling."""
    response = await firecrawl_scrape(app, url=url, only_main_content=True)
    return response.markdown

async def fetch_skokie_events() -> List[Dict[str, Any]]:
    """Fetch Skokie events using Firecrawl for better reliability."""
    logger.info("Fetching Skokie events (Firecrawl)...")
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Skokie fetch")
        return []
    app = AsyncFirecrawlApp(api_key=FIRECRAWL_API_KEY)
    
    # Use the list view which has event descriptions
    url = "https://www.skokielibrary.info/events/list?age_groups%5B2%5D=2&age_groups%5B73%5D=73&age_groups%5B74%5D=74"
    
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
    
    try:
        # In list view, events are separated by "View Details" links
        # Split by event entries that start with titles
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
            
            # Determine age group
            age_group = ""
            block_lower = block.lower()
            has_kids = re.search(r'\bkids\b', block_lower) is not None
            # Prefer explicit grade ranges when present
            if re.search(r'Grade\s*K[–-]2', block, re.IGNORECASE):
                age_group = "Grades K-2"
            elif re.search(r'Grade\s*3[–-]5', block, re.IGNORECASE):
                age_group = "Grades 3-5"
            elif re.search(r'Age\s*0[–-]5', block, re.IGNORECASE):
                age_group = "Grades K-2"  # Treat early childhood as K-2 bucket for our purposes
            elif has_kids:
                # Include general Kids events even if grade not specified (prevents false negatives)
                age_group = "Kids"
            else:
                # Not a kids-focused event; skip
                continue
            
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
                        not re.search(r'This event is in the|Event Type:|Age Group:|^Kids$|^Age 0|^Grade [K\d]|Registration Required', line, re.IGNORECASE) and
                        re.search(r'[.!?]$', line)):  # Ends with proper punctuation
                        desc_lines.append(line)
                
                if desc_lines:
                    description = clean_text(" ".join(desc_lines))
                    
            all_events.append({
                "Library": "Skokie",
                "Title": event_title,
                "Date": event_date,
                "Time": time_str,
                "Location": "Skokie Public Library",
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

def fetch_chicago_parks_events() -> List[Dict[str, Any]]:
    """
    Scrape events from Chicago Park District using Beautiful Soup.
    Returns a list of event dictionaries in the standard format.

    The scraper attempts multiple strategies to find events:
    1. Look for Drupal views-row structure (common CMS pattern)
    2. Look for event/program class containers
    3. Look for article elements
    4. Look for specific data attributes
    """
    logger.info("Fetching Chicago Park District events...")

    CHICAGO_PARKS_URL = 'https://www.chicagoparkdistrict.com/events'

    # Use headers to appear as a regular browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    # Retry logic for network resilience
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"Attempt {attempt + 1}/{MAX_RETRIES} to fetch Chicago Parks events")
            response = requests.get(CHICAGO_PARKS_URL, headers=headers, timeout=30)
            response.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES - 1:
                logger.error(f"Failed to fetch Chicago Parks events after {MAX_RETRIES} attempts: {e}")
                return []
            logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    # Parse with lxml for speed
    soup = BeautifulSoup(response.content, 'lxml')

    all_events = []

    # Strategy 1: Drupal views pattern (most common for Chicago Park District site)
    event_items = soup.select('.view-content .views-row')

    if not event_items:
        # Strategy 2: Look for event/program containers
        event_items = soup.find_all(['article', 'div'], class_=re.compile(r'event|program', re.IGNORECASE))

    if not event_items:
        # Strategy 3: Look for data attributes
        event_items = soup.find_all(['div', 'li', 'article'], attrs={'data-event': True})

    if not event_items:
        # Strategy 4: Generic article/event structure
        event_items = soup.find_all('article')

    logger.info(f"Found {len(event_items)} potential event items")

    if not event_items:
        logger.warning("No event items found. Page structure may have changed.")
        # Log a sample of the HTML for debugging
        logger.debug(f"Page title: {soup.title.string if soup.title else 'No title'}")

    for idx, item in enumerate(event_items):
        try:
            # Extract title - try multiple strategies
            title_elem = (
                item.find('h2') or
                item.find('h3') or
                item.find('h4') or
                item.find(class_=re.compile(r'title', re.IGNORECASE)) or
                item.find('a')
            )

            if not title_elem:
                logger.debug(f"Skipping item {idx} - no title found")
                continue

            title = clean_text(title_elem.get_text())
            if not title or len(title) < 3:
                logger.debug(f"Skipping item {idx} - title too short: '{title}'")
                continue

            # Extract link
            link_elem = item.find('a', href=True)
            link = link_elem.get('href', "N/A") if link_elem else "N/A"
            if link and link != "N/A" and not link.startswith('http'):
                # Handle relative URLs
                if link.startswith('/'):
                    link = f"https://www.chicagoparkdistrict.com{link}"
                else:
                    link = f"https://www.chicagoparkdistrict.com/{link}"

            # Extract date - try multiple approaches
            date_str = "Not found"

            # Look for time element with datetime attribute
            time_elem = item.find('time', attrs={'datetime': True})
            if time_elem:
                date_str = time_elem.get('datetime', '')
                # Also try the text content if datetime is empty
                if not date_str:
                    date_str = clean_text(time_elem.get_text())

            # If no time element, look for date classes
            if date_str == "Not found":
                date_elem = item.find(class_=re.compile(r'date|when', re.IGNORECASE))
                if date_elem:
                    date_str = clean_text(date_elem.get_text())

            # Extract time
            time_str = "Not found"

            # Look for time in class names
            time_elem = item.find(class_=re.compile(r'time|hour', re.IGNORECASE))
            if time_elem:
                time_text = clean_text(time_elem.get_text())
                # Extract time patterns like "10:00 AM - 2:00 PM" or "10am-2pm"
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[ap]m\s*[-–]\s*\d{1,2}:\d{2}\s*[ap]m|\d{1,2}\s*[ap]m\s*[-–]\s*\d{1,2}\s*[ap]m)', time_text, re.IGNORECASE)
                if time_match:
                    time_str = time_match.group(1)
                elif re.search(r'\d{1,2}:\d{2}\s*[ap]m', time_text, re.IGNORECASE):
                    time_str = time_text

            # If not found in dedicated time element, try to extract from date string
            if time_str == "Not found" and date_str != "Not found":
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[ap]m\s*[-–]\s*\d{1,2}:\d{2}\s*[ap]m)', date_str, re.IGNORECASE)
                if time_match:
                    time_str = time_match.group(1)
                    # Remove time from date string
                    date_str = date_str.replace(time_str, '').strip()

            # Extract location/venue
            location_str = "Chicago Park District"
            location_elem = item.find(class_=re.compile(r'location|venue|park|where', re.IGNORECASE))
            if location_elem:
                location_text = clean_text(location_elem.get_text())
                if location_text and location_text.lower() not in ['location', 'venue', 'where']:
                    location_str = location_text

            # Extract description
            description = "Not found"
            # Look for description, summary, or body classes
            desc_elem = (
                item.find(class_=re.compile(r'description|summary|body|excerpt|teaser', re.IGNORECASE)) or
                item.find('p')
            )
            if desc_elem:
                description = clean_text(desc_elem.get_text())
                # Truncate very long descriptions
                if len(description) > 500:
                    description = description[:497] + "..."

            # Determine age group based on content
            age_group = "All Ages"
            text_content = f"{title} {description}".lower()

            # Check for kid-specific keywords
            kid_keywords = ['kid', 'child', 'family', 'youth', 'junior', 'ages 5-12', 'elementary']
            if any(keyword in text_content for keyword in kid_keywords):
                age_group = "Kids/Family"

            # Check for teen keywords
            teen_keywords = ['teen', 'adolescent', 'ages 13-17', 'middle school', 'high school']
            if any(keyword in text_content for keyword in teen_keywords):
                age_group = "Teens"

            # Check for adult keywords
            adult_keywords = ['adult', 'senior', '55+', '18+', '21+']
            if any(keyword in text_content for keyword in adult_keywords):
                age_group = "Adults"

            # Determine program type
            program_type = "Recreation"
            if any(word in text_content for word in ['sport', 'athletic', 'fitness', 'swim', 'basketball']):
                program_type = "Sports/Fitness"
            elif any(word in text_content for word in ['art', 'craft', 'paint', 'draw', 'music']):
                program_type = "Arts/Culture"
            elif any(word in text_content for word in ['nature', 'garden', 'environment', 'outdoor']):
                program_type = "Nature/Outdoors"

            all_events.append({
                "Library": "Chicago Parks",
                "Title": title,
                "Date": date_str,
                "Time": time_str,
                "Location": location_str,
                "Age Group": age_group,
                "Program Type": program_type,
                "Description": description,
                "Link": link
            })

        except Exception as e:
            logger.warning(f"Error processing Chicago Parks event item {idx}: {e}")
            continue

    logger.info(f"Successfully parsed {len(all_events)} events from Chicago Parks")
    return all_events

# --- REPORT GENERATORS ---

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
    tz = ZoneInfo(TIMEZONE)
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
            link = event.get('Link', '')
            
            if event_date_str != current_date_str:
                current_date_str = event_date_str
                doc.append(Section(escape_latex(event_date_str)))
                
            with doc.create(Subsection(escape_latex(title), numbering=False)):
                doc.append(Command("textbf", "Library: "))
                doc.append(f"{escape_latex(library)}\n")
                doc.append(Command("textbf", "Time: "))
                doc.append(f"{escape_latex(time)}\n")
                doc.append(Command("textbf", "Location: "))
                doc.append(f"{escape_latex(location)}\n")
                doc.append(NoEscape(r'\vspace{0.1cm}'))
                doc.append(escape_latex(description))
                
                if link and link != "N/A":
                    doc.append(NoEscape(r'\\\textbf{More Info: }'))
                    doc.append(Command("texttt", link.replace("_", r"\_")))
        except Exception as e:
            logger.warning(f"Error processing event for PDF: {e}")
            continue
    try:
        doc.generate_pdf(filename, clean_tex=False)
        logger.info(f"PDF report saved to {filename}.pdf")
    except Exception as e:
        logger.error(f"PDF generation failed: {e}", exc_info=True)

# --- NEW FUNCTION TO GENERATE ICS FILE ---
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
def compute_date_window(cli_args=None) -> tuple[str, int, List[str], List[str]]:
    """Compute START_DATE (YYYY-MM-DD) and DAYS_TO_FETCH from CLI/env with sane defaults.

    Precedence: CLI > ENV > defaults. If both start-date and offset are provided, start-date wins.
    Env vars: START_DATE, DAYS_TO_FETCH, START_OFFSET_DAYS.
    CLI flags: --start-date, --days, --start-offset-days
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--start-date", help="Start date in YYYY-MM-DD")
    parser.add_argument("--days", type=int, help="Number of days to fetch")
    parser.add_argument("--start-offset-days", type=int, help="Offset from today for start date")
    parser.add_argument(
        "--libnet-ages",
        help="Comma-separated LibNet ages (e.g., 'Grades K-2,Grades 3-5')",
    )
    parser.add_argument(
        "--libnet-request-ages",
        help="Comma-separated ages to send to LibNet API (default 'Kids')",
    )
    try:
        args, _ = parser.parse_known_args(cli_args)
    except SystemExit:
        # In case this is imported and parse_known_args tries to exit, fall back to defaults
        args = parser.parse_args([])

    env_start_date = os.getenv("START_DATE")
    env_days = os.getenv("DAYS_TO_FETCH")
    env_offset = os.getenv("START_OFFSET_DAYS")
    env_libnet_ages = os.getenv("LIBNET_AGES")  # comma-separated
    env_libnet_request_ages = os.getenv("LIBNET_REQUEST_AGES")  # comma-separated

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

    # Determine LibNet ages list (post-filter)
    if args.libnet_ages:
        ages_list = [a.strip() for a in args.libnet_ages.split(",") if a.strip()]
    elif env_libnet_ages:
        ages_list = [a.strip() for a in env_libnet_ages.split(",") if a.strip()]
    else:
        ages_list = DEFAULT_LIBNET_AGES

    # Determine LibNet request ages (broad fetch)
    if args.libnet_request_ages:
        req_ages_list = [a.strip() for a in args.libnet_request_ages.split(",") if a.strip()]
    elif env_libnet_request_ages:
        req_ages_list = [a.strip() for a in env_libnet_request_ages.split(",") if a.strip()]
    else:
        req_ages_list = DEFAULT_LIBNET_REQUEST_AGES

    return start_date_str, days, ages_list, req_ages_list

async def main():
    # Compute date window and ages from CLI/env and set module globals used by fetchers
    global START_DATE, DAYS_TO_FETCH, LIBNET_AGES, LIBNET_REQUEST_AGES
    START_DATE, DAYS_TO_FETCH, LIBNET_AGES, LIBNET_REQUEST_AGES = compute_date_window()
    logger.info(
        f"Using date window: start={START_DATE}, days={DAYS_TO_FETCH}; libnet_ages={LIBNET_AGES}; libnet_request_ages={LIBNET_REQUEST_AGES}"
    )

    # Define parameters for Bibliocommons libraries
    evanston_query = "audiences=664cce641e57af2800453c7b%2C6696ef5fe3e1ee300048b71b"
    cpl_edgebrook_query = "locations=27"
    cpl_budlong_query = "locations=16" # Added for Budlong Woods

    tasks = [
        fetch_lincolnwood_events(),
        fetch_mgpl_events(),
        fetch_bibliocommons_events("Evanston", EVANSTON_BASE_URL, evanston_query),
        fetch_bibliocommons_events("CPL Edgebrook", CPL_BASE_URL, cpl_edgebrook_query),
        fetch_bibliocommons_events("CPL Budlong Woods", CPL_BASE_URL, cpl_budlong_query), # Added for Budlong Woods
        asyncio.to_thread(fetch_libnet_events, "Wilmette", "wilmette.libnet.info"),
        fetch_skokie_events(),
        asyncio.to_thread(fetch_libnet_events, "Niles", "nmdl.libnet.info"),
        asyncio.to_thread(fetch_chicago_parks_events)
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_events = [event for res in all_results if isinstance(res, list) for event in res]
    logger.info(f"Total events found: {len(all_events)}")

    unique_events, seen = [], set()
    for event in all_events:
        identifier = (event.get('Library'), event.get('Title'), event.get('Date'), event.get('Time'))
        if identifier not in seen:
            unique_events.append(event); seen.add(identifier)
    logger.info(f"Total events after de-duplication: {len(unique_events)}")
    all_events = unique_events

    if not all_events: return

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

    all_events.sort(key=lambda x: (x.get('datetime_obj', datetime.max), x['Library'], x.get('time_obj', datetime.min.time())))

    # --- MODIFIED: Generate all reports from the fully processed data ---
    base_filename = f"all_library_events_{datetime.now():%Y%m%d}"
    
    # Generate ICS and PDF reports (which use the full data with datetime objects)
    generate_ics_file(all_events, base_filename)
    generate_pdf_report(all_events, base_filename)

    # Prepare DataFrame for CSV (dropping temporary datetime helper columns)
    try:
        df = pd.DataFrame(all_events).drop(columns=['datetime_obj', 'time_obj'], errors='ignore')
        csv_filename = f"{base_filename}.csv"
        df.to_csv(csv_filename, index=False, quoting=csv.QUOTE_ALL)
        logger.info(f"Combined CSV report saved to {csv_filename}")
    except Exception as e:
        logger.error(f"Error generating CSV report: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
