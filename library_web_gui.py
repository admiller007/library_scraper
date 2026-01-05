#!/usr/bin/env python3
"""
Library Events Web GUI
A simple Flask-based web interface to view, filter, and export library events.
"""

from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import re
import hashlib
import csv
from io import BytesIO
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from ics import Calendar, Event
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from html import escape
import webbrowser
import threading
import time
import math
import requests

# Import AI service (optional)
try:
    from ai_service import enhance_events_batch, get_summarizer
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR)).resolve()
TIMEZONE = os.getenv("TIMEZONE", "America/Chicago")
TZINFO = ZoneInfo(TIMEZONE)
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = DATA_DIR / "scrape_progress.json"

# Geocoding cache setup
GEO_CACHE_FILE = DATA_DIR / "geo_cache.json"
geo_cache = {}
if GEO_CACHE_FILE.exists():
    try:
        with open(GEO_CACHE_FILE, 'r') as f:
            geo_cache = json.load(f)
        print(f"📍 Loaded {len(geo_cache)} cached geocoding results")
    except Exception as e:
        print(f"⚠️ Could not load geo cache: {e}")
        geo_cache = {}

def save_geo_cache():
    """Helper to save geocoding cache to disk"""
    try:
        with open(GEO_CACHE_FILE, 'w') as f:
            json.dump(geo_cache, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save geo cache: {e}")

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Global variable to store events data
events_data = []
SCRAPER_THREAD = None
SCRAPER_LOCK = threading.Lock()


def clean_text(value: str) -> str:
    """Lightweight cleaner for ICS-friendly text."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\u200b", " ").split())


def geocode_address(address: str) -> Optional[tuple[float, float]]:
    """Geocode an address using cache, hardcoded mapping, then Nominatim API fallback."""
    if not address or not address.strip():
        return None

    address = address.strip()
    print(f"🌍 Geocoding address: '{address}'")  # Debug

    # Check cache first for any previous successful geocoding
    if address in geo_cache:
        print(f"⚡ Using cached coordinates for: '{address}'")
        return tuple(geo_cache[address])

    # First try hardcoded ZIP code mapping for Chicago area (fast and reliable)
    zip_coords = {
        "60645": (41.9700, -87.6800),  # North Chicago (general area, not specific library)
        "60646": (41.9917, -87.7581),  # North Chicago (near CPL Edgebrook)
        "60016": (42.0334, -87.8834),  # Des Plaines
        "60201": (42.0450, -87.6877),  # Evanston
        "60305": (41.8950, -87.8031),  # River Forest
        "60022": (42.1372, -87.7581),  # Glencoe
        "60169": (42.0631, -88.0834),  # Hoffman Estates
        "60712": (42.0075, -87.7220),  # Lincolnwood
        "60053": (42.0406, -87.7834),  # Morton Grove
        "60714": (42.0281, -87.8009),  # Niles
        "60068": (42.0111, -87.8406),  # Park Ridge
        "60077": (42.0406, -87.7334),  # Skokie
        "60091": (42.0722, -87.7220),  # Wilmette
        "60659": (41.9740, -87.6700),  # North Chicago (near Budlong Woods area)
        "60640": (41.9675, -87.6947),  # Uptown Chicago
        "60613": (41.9536, -87.6547),  # Lakeview Chicago
        "60614": (41.9297, -87.6436),  # Lincoln Park Chicago
        "60033": (42.0522, -88.0392),  # Glendale Heights
        "60005": (41.9581, -87.9331),  # Arlington Heights
        "60007": (42.0042, -87.9373),  # Elk Grove Village
        "60018": (42.0403, -87.9545),  # Des Plaines
        "60025": (42.0631, -87.8006),  # Glenview
        "60056": (42.1231, -87.7370),  # Mount Prospect
        "60062": (42.1478, -87.9215),  # Northbrook
        "60076": (42.0406, -87.7545),  # Skokie
        "60089": (42.2331, -87.8609),  # Buffalo Grove
        "60090": (42.2189, -87.8845),  # Wheeling
        "60004": (42.0039, -87.9006),  # Arlington Heights
        "60008": (42.0331, -87.9698),  # Rolling Meadows
    }

    # Check if it's a known ZIP code
    coords = zip_coords.get(address)
    if coords:
        print(f"✅ Found hardcoded ZIP coordinates: {coords}")  # Debug
        # Cache the result for faster future lookups
        geo_cache[address] = coords
        save_geo_cache()
        return coords

    # Try simple pattern matching for common Chicago addresses
    chicago_patterns = {
        'downtown chicago': (41.8781, -87.6298),
        'loop chicago': (41.8781, -87.6298),
        'north side chicago': (41.9500, -87.6500),
        'south side chicago': (41.8000, -87.6298),
        'west side chicago': (41.8781, -87.7000),
        'chicago': (41.8781, -87.6298),  # Default Chicago center
    }

    address_lower = address.lower()
    for pattern, coords in chicago_patterns.items():
        if pattern in address_lower:
            print(f"✅ Found pattern match for '{pattern}': {coords}")
            # Cache the result for faster future lookups
            geo_cache[address] = coords
            save_geo_cache()
            return coords

    # Fallback to Nominatim API for full addresses with improved implementation
    try:
        print(f"🌐 Trying Nominatim API for: {address}")  # Debug

        # Proper headers - NO example.com domains (they are blocked)
        headers = {
            'User-Agent': 'ChicagoLibraryEventMap/1.0 (local_dev_project; dev_chicago_libs_2024)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Referer': 'http://localhost:8888/'
        }

        url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': address,
            'format': 'json',
            'limit': 1,
            'countrycodes': 'us',  # Limit to US addresses
            'addressdetails': 1,
            'extratags': 0,
            'namedetails': 0
            # Removed email parameter - using unique User-Agent instead
        }

        # Log the exact request for debugging
        print(f"📝 Request URL: {url}")
        print(f"📝 Request params: {params}")
        print(f"📝 Request headers: {headers}")

        # Add delay to respect rate limiting (1 request per second max)
        import time
        time.sleep(1.1)  # Slightly more than 1 second

        print(f"🔄 Making HTTP request...")
        response = requests.get(url, params=params, headers=headers, timeout=30)

        # Log response details for debugging
        print(f"📊 Response status: {response.status_code}")
        print(f"📊 Response headers: {dict(response.headers)}")

        # Check for specific error responses
        if response.status_code == 403:
            print(f"🚫 HTTP 403 Forbidden - Access denied by Nominatim")
            print(f"🚫 This might be due to:")
            print(f"   - Too many requests (rate limiting)")
            print(f"   - Missing or invalid User-Agent")
            print(f"   - IP-based blocking")
            print(f"   - Need for email parameter")
            return None
        elif response.status_code == 429:
            print(f"🚫 HTTP 429 Too Many Requests - Rate limited")
            return None

        response.raise_for_status()

        try:
            data = response.json()
            print(f"🔍 Nominatim response: {len(data) if data else 0} results")  # Debug

            if data and len(data) > 0:
                result = data[0]
                print(f"📍 Full result: {result}")  # Debug full response

                lat = float(result['lat'])
                lon = float(result['lon'])
                coords = (lat, lon)
                print(f"✅ Found Nominatim coordinates: {coords}")  # Debug

                # Cache the successful result
                geo_cache[address] = coords
                save_geo_cache()
                return coords
            else:
                print(f"🔍 Nominatim returned empty results for: {address}")
                return None

        except (ValueError, KeyError) as e:
            print(f"⚠️ Failed to parse Nominatim response: {e}")
            print(f"📄 Raw response: {response.text[:500]}...")
            return None

    except requests.exceptions.RequestException as e:
        print(f"⚠️ Nominatim API request failed for '{address}': {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"📄 Error response status: {e.response.status_code}")
            print(f"📄 Error response text: {e.response.text[:200]}...")
    except Exception as e:
        print(f"⚠️ Unexpected error during Nominatim geocoding: {e}")

    print(f"❌ No coordinates found for: {address}")  # Debug
    print(f"💡 Tip: Try using a ZIP code instead (like 60601) for better results")  # Debug
    return None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points on the earth in miles.
    Uses the haversine formula."""
    # Convert to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))

    # Radius of earth in miles
    r = 3956
    return c * r


def get_library_coordinates(library_name: str, location: str) -> Optional[tuple[float, float]]:
    """Get coordinates for a library location using hardcoded mapping."""
    # Hardcoded library coordinates mapping
    library_coords = {
        "CPL Budlong Woods": (41.9748, -87.6677),  # 5630 N Lincoln Avenue, Chicago, IL 60659
        "CPL Edgebrook": (41.9917, -87.7581),      # 5331 W Devon Avenue, Chicago, IL 60646
        "Des Plaines": (42.0334, -87.8834),        # 1501 Ellinwood Street, Des Plaines, IL 60016
        "Evanston": (42.0450, -87.6877),           # 1703 Orrington Avenue, Evanston, IL 60201
        "Forest Preserves of Cook County": (41.8950, -87.8031), # 536 N Harlem Avenue, River Forest, IL 60305
        "Glencoe": (42.1372, -87.7581),            # 320 Park Avenue, Glencoe, IL 60022
        "Hoffman Estates": (42.0631, -88.0834),    # 1550 Hassell Road, Hoffman Estates, IL 60169
        "Lincolnwood": (42.0075, -87.7220),        # 4000 W Pratt Avenue, Lincolnwood, IL 60712
        "Morton Grove": (42.0406, -87.7834),       # 6140 Lincoln Avenue, Morton Grove, IL 60053
        "Niles": (42.0281, -87.8009),              # 6960 W Oakton Street, Niles, IL 60714
        "Park Ridge": (42.0111, -87.8406),         # 20 S Prospect Avenue, Park Ridge, IL 60068
        "Skokie": (42.0406, -87.7334),             # 5215 Oakton Street, Skokie, IL 60077
        "Skokie Park District": (42.0406, -87.7220), # 9300 Weber Park Place, Skokie, IL 60077
        "Wilmette": (42.0722, -87.7220),           # 1242 Wilmette Avenue, Wilmette, IL 60091
    }

    print(f"🏛️ Getting coordinates for library: '{library_name}'")  # Debug
    coords = library_coords.get(library_name)
    if coords:
        print(f"✅ Found hardcoded coordinates: {coords}")  # Debug
        return coords
    else:
        print(f"❌ No coordinates found for library: {library_name}")  # Debug
        return None


def parse_event_date(dstr: str):
    """Parse common date strings used in the CSV."""
    if not dstr:
        return None
    for fmt in ("%A, %B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(dstr), fmt).date()
        except Exception:
            continue
    return None


def parse_time_to_sortable(time_str: str) -> datetime.time:
    """Parses various time formats into a time object for sorting."""
    if not isinstance(time_str, str):
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
        return datetime.min.time()
    except Exception:
        return datetime.min.time()


def filter_events(
    library_filters=None,
    type_filters=None,
    search_term='',
    start_date='',
    end_date='',
    date_filter='',
    search_fields=None,
    search_mode='any',
    user_address='',
    max_distance=None
):
    """Apply shared filtering logic using optimized single-pass approach."""
    # Prepare filter parameters
    library_filters = [lf for lf in (library_filters or []) if (lf or '').strip() and lf != 'All']
    type_filters = type_filters or []
    search_mode = (search_mode or 'any').lower()
    if search_mode not in {'any', 'all', 'exact', 'fuzzy'}:
        search_mode = 'any'

    # Pre-compute filter parameters for efficiency
    selected_libs = set(library_filters) if library_filters else None
    selected_types = set(type_filters) if type_filters else None

    # Parse date filters once
    start_date_obj = None
    end_date_obj = None
    target_date = None

    if start_date or end_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
        except ValueError:
            pass
        try:
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        except ValueError:
            pass
    elif date_filter:
        try:
            target_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Pre-compute search parameters
    search_tokens = []
    search_fields_list = []
    fuzzy_threshold = 0.65

    if search_term:
        raw = search_term.lower().strip()
        # Support quoted phrases or space-separated tokens
        for m in re.finditer(r'"([^"]+)"|(\S+)', raw):
            token = (m.group(1) or m.group(2) or '').strip()
            if token:
                search_tokens.append(token)

        field_alias = {
            'title': 'Title',
            'description': 'Description',
            'location': 'Location',
            'age': 'Age Group',
            'age_group': 'Age Group',
            'type': 'Age Group',
            'library': 'Library'
        }
        for sf in search_fields or []:
            key = field_alias.get(sf.lower())
            if key:
                search_fields_list.append(key)
        if not search_fields_list:
            search_fields_list = ['Title', 'Description', 'Location']

    # Geocoding setup
    user_coords = None
    if user_address:
        user_coords = geocode_address(user_address)

    # Helper functions for single-pass filtering
    def get_event_types(ev):
        raw = (ev.get('Age Group', '') or '')
        parts = [p.strip() for p in str(raw).split(',') if p.strip()]
        return set(parts) if parts else {raw} if raw else set()

    def matches_search(ev):
        if not search_term:
            return True

        values_list = [str(ev.get(f, '') or '').lower() for f in search_fields_list]
        combined = " ".join(values_list)
        raw = search_term.lower().strip()

        if not raw:
            return True

        if search_mode == 'exact':
            return raw in combined

        if search_mode == 'fuzzy':
            if not any(values_list):
                return False
            score_fn = lambda needle: max((SequenceMatcher(None, needle, val).ratio() for val in values_list if val), default=0)
            if score_fn(raw) >= fuzzy_threshold:
                return True
            return any(score_fn(tok) >= fuzzy_threshold for tok in search_tokens)

        if raw in combined:
            return True
        if not search_tokens:
            return True
        if search_mode == 'all':
            return all(tok in combined for tok in search_tokens)
        return any(tok in combined for tok in search_tokens)

    def matches_date(ev):
        if target_date:
            ev_date = parse_event_date(ev.get('Date') or '')
            return ev_date == target_date

        if start_date_obj or end_date_obj:
            ev_date = parse_event_date(ev.get('Date') or '')
            if not ev_date:
                return False
            if start_date_obj and ev_date < start_date_obj:
                return False
            if end_date_obj and ev_date > end_date_obj:
                return False

        return True

    # SINGLE-PASS FILTERING
    filtered_events = []

    for event in events_data:
        # Check library filter
        if selected_libs and event.get('Library', '') not in selected_libs:
            continue

        # Check type (age group) filter
        if selected_types and not (get_event_types(event) & selected_types):
            continue

        # Check search filter
        if not matches_search(event):
            continue

        # Check date filter
        if not matches_date(event):
            continue

        # Add distance calculation if needed
        if user_coords:
            user_lat, user_lon = user_coords
            library = event.get('Library', '')
            location = event.get('Location', '')

            event_coords = get_library_coordinates(library, location)
            if event_coords:
                event_lat, event_lon = event_coords
                distance = haversine_distance(user_lat, user_lon, event_lat, event_lon)
                event['_distance'] = round(distance, 1)

                # Filter by distance if specified
                if max_distance is not None and distance > max_distance:
                    continue
            else:
                # Skip events without coordinates if distance filtering is active
                if max_distance is not None:
                    continue

        filtered_events.append(event)

    # Sort by distance if geocoding was used
    if user_coords:
        filtered_events.sort(key=lambda e: e.get('_distance', float('inf')))

    return filtered_events


def group_events(events):
    """Group events by date -> library -> time for organized exports."""
    grouped = {}
    for ev in events:
        date_obj = parse_event_date(ev.get('Date') or '')
        date_key = date_obj.strftime("%A, %B %d, %Y") if date_obj else (ev.get('Date') or 'Date TBD')
        time_val = parse_time_to_sortable(ev.get('Time') or '')
        lib = ev.get('Library') or 'Unknown Library'

        if date_key not in grouped:
            grouped[date_key] = {'date_obj': date_obj, 'libraries': {}}
        libs = grouped[date_key]['libraries']
        libs.setdefault(lib, []).append((time_val, ev))

    ordered_dates = sorted(
        grouped.items(),
        key=lambda kv: (
            kv[1]['date_obj'] is None,
            kv[1]['date_obj'] or date.max
        )
    )

    for _, payload in ordered_dates:
        for lib_name, items in payload['libraries'].items():
            payload['libraries'][lib_name] = sorted(items, key=lambda t: t[0])

    return ordered_dates


def slugify(text: str) -> str:
    """Turn user-facing labels into safe filename fragments."""
    return re.sub(r'[^A-Za-z0-9]+', '_', text or '').strip('_') or 'events'


def events_to_ics(events, filename_prefix="library_events") -> tuple[str, str]:
    """Convert a list of event dicts into an ICS string and download filename."""
    cal = Calendar()
    for event in events:
        event_date = parse_event_date(event.get('Date') or '')
        if not event_date:
            continue

        time_str = event.get('Time', '') or ''
        start_time = parse_time_to_sortable(time_str)
        start_dt = datetime.combine(event_date, start_time).replace(tzinfo=TZINFO)
        end_dt = start_dt + timedelta(hours=1)

        time_parts = re.split(r'–|-', time_str)
        if len(time_parts) > 1:
            end_time_str = time_parts[1].strip()
            for fmt in ['%I:%M %p', '%I:%M%p', '%-I:%M %p', '%I %p', '%-I%p']:
                try:
                    end_time_obj = datetime.strptime(end_time_str.replace(" ", ""), fmt).time()
                    end_dt = datetime.combine(event_date, end_time_obj).replace(tzinfo=TZINFO)
                    break
                except ValueError:
                    continue

        e = Event()
        e.name = clean_text(event.get('Title', 'Untitled Event'))
        e.begin = start_dt
        e.end = end_dt

        if start_time == datetime.min.time():
            e.make_all_day()

        description_parts = []
        age_group = event.get('Age Group', '')
        description = event.get('Description', '')
        link = event.get('Link', '')

        if age_group and age_group != 'Not specified':
            description_parts.append(f"Age Group: {age_group}")
        if description and description != 'Not found':
            description_parts.append(clean_text(description))
        if link and link != "N/A":
            description_parts.append(f"More Info: {link}")

        e.description = "\n".join(description_parts)
        e.location = clean_text(event.get('Location', ''))

        uid_src = "|".join([
            str(event.get('Library', '')),
            str(event.get('Title', '')),
            str(event.get('Date', '')),
            str(event.get('Time', '')),
            str(event.get('Location', ''))
        ])
        e.uid = hashlib.md5(uid_src.encode('utf-8', errors='ignore')).hexdigest() + "@library-scraper"
        if link and link != 'N/A':
            try:
                e.url = link
            except Exception:
                pass

        cal.events.add(e)

    filename = f"{filename_prefix}.ics"
    return cal.serialize(), filename


def events_to_pdf(events, filename_prefix="library_events") -> tuple[BytesIO, str]:
    """Create a PDF organized by day, library, then time for the given events."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Library Events"
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Meta", parent=styles["Normal"], textColor=colors.grey, fontSize=9))
    styles.add(ParagraphStyle(name="Small", parent=styles["Normal"], fontSize=9))

    story = [
        Paragraph("Library Events", styles["Title"]),
        Spacer(1, 0.2 * inch)
    ]

    grouped = group_events(events)
    if not grouped:
        story.append(Paragraph("No events available for export.", styles["Normal"]))
    else:
        for date_label, payload in grouped:
            story.append(Paragraph(escape(date_label), styles["Heading2"]))
            story.append(Spacer(1, 0.05 * inch))

            for library_name in sorted(payload['libraries'].keys()):
                story.append(Paragraph(escape(library_name), styles["Heading3"]))

                for _time_value, ev in payload['libraries'][library_name]:
                    title = escape(ev.get('Title') or 'Untitled Event')
                    time_str = escape(ev.get('Time') or 'Time TBD')
                    location = escape(ev.get('Location') or 'Location TBD')
                    age = escape(ev.get('Age Group') or 'All Ages')
                    description = ev.get('Description')
                    link = ev.get('Link')

                    story.append(Paragraph(title, styles["Heading4"]))
                    meta = f"{time_str} | {location} | Age: {age}"
                    story.append(Paragraph(meta, styles["Meta"]))

                    if description and description != 'Not found':
                        story.append(Paragraph(escape(description), styles["Normal"]))

                    if link and link not in ('N/A', ''):
                        story.append(Paragraph(f"Link: <a href='{escape(link)}'>{escape(link)}</a>", styles["Small"]))

                    story.append(Spacer(1, 0.18 * inch))

                story.append(Spacer(1, 0.1 * inch))

            story.append(Spacer(1, 0.15 * inch))

    doc.build(story)
    buffer.seek(0)
    return buffer, f"{filename_prefix}.pdf"

def load_latest_csv():
    """Load the most recent CSV file using optimized csv module instead of Pandas."""
    global events_data

    csv_files = list(DATA_DIR.glob('all_library_events_*.csv'))

    if csv_files:
        latest_file = max(csv_files, key=lambda p: p.stat().st_mtime)
        try:
            # Use csv.DictReader instead of Pandas for lower memory usage
            with open(latest_file, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                events_data = list(reader)

                # Clean empty/None values (equivalent to Pandas fillna)
                for event in events_data:
                    for key in event:
                        if event[key] is None or event[key] == 'nan' or event[key] == '':
                            event[key] = ''

            # Enhance events with AI summaries if enabled
            if AI_AVAILABLE and os.getenv("ENABLE_AI_SUMMARIZATION", "false").lower() == "true":
                print(f"🤖 Enhancing events with AI summaries...")
                events_data = enhance_events_batch(events_data, max_count=None)  # Process ALL events with fast rule-based summaries
                print(f"✨ AI enhancement completed")

            print(f"✅ Loaded {len(events_data)} events from {latest_file.name} (using optimized csv module)")
            return latest_file.name
        except Exception as e:
            print(f"❌ Error loading CSV: {e}")
            events_data = []
            return None
    else:
        print(f"⚠️ No CSV files found in {DATA_DIR}")
        events_data = []
        return None


def get_library_address_info(library_name: str) -> str:
    """Get the actual street address for each library."""
    # Mapping of library names to their actual street addresses
    library_addresses = {
        "CPL Budlong Woods": "5630 N Lincoln Avenue, Chicago, IL 60659",
        "CPL Edgebrook": "5331 W Devon Avenue, Chicago, IL 60646",
        "Des Plaines": "1501 Ellinwood Street, Des Plaines, IL 60016",
        "Evanston": "1703 Orrington Avenue, Evanston, IL 60201",
        "Forest Preserves of Cook County": "536 N Harlem Avenue, River Forest, IL 60305",
        "Forest Preserves": "536 N Harlem Avenue, River Forest, IL 60305",  # Alternative name
        "Glencoe": "320 Park Avenue, Glencoe, IL 60022",
        "Hoffman Estates": "1550 Hassell Road, Hoffman Estates, IL 60169",
        "Lincolnwood": "4000 W Pratt Avenue, Lincolnwood, IL 60712",
        "Morton Grove": "6140 Lincoln Avenue, Morton Grove, IL 60053",
        "Morton Grove (MGPL)": "6140 Lincoln Avenue, Morton Grove, IL 60053",
        "Niles": "6960 Oakton Street, Niles, IL 60714",
        "Park Ridge": "20 S Prospect Avenue, Park Ridge, IL 60068",
        "Skokie": "5215 Oakton Street, Skokie, IL 60077",
        "Skokie Library": "5215 Oakton Street, Skokie, IL 60077",
        "Skokie Park District": "9300 Weber Park Place, Skokie, IL 60077",
        "Skokie Parks": "9300 Weber Park Place, Skokie, IL 60077",
        "Wilmette": "1242 Wilmette Avenue, Wilmette, IL 60091",
        "Chicago Parks": "541 N Fairbanks Court, Chicago, IL 60611"
    }

    return library_addresses.get(library_name, "")

@lru_cache(maxsize=1)
def _cached_progress_read(mtime: float):
    """Cached progress file reader - invalidates when file is modified."""
    with PROGRESS_FILE.open() as f:
        data = json.load(f)
        # Add address information to sources
        if "sources" in data:
            for source_name, source_info in data["sources"].items():
                if not source_info.get("address"):
                    source_info["address"] = get_library_address_info(source_name)
        return data

def read_progress_file():
    """Return the latest scrape progress snapshot for the UI."""
    if not PROGRESS_FILE.exists():
        return {
            "summary": {
                "state": "idle",
                "message": "Waiting to start",
                "total_sources": 0,
                "succeeded": 0,
                "failed": 0,
                "running": 0,
                "pending": 0,
                "events": len(events_data),
            },
            "sources": {}
        }
    try:
        # Use mtime-based caching to avoid redundant file I/O
        mtime = PROGRESS_FILE.stat().st_mtime
        return _cached_progress_read(mtime)
    except Exception as exc:
        return {"summary": {"state": "error", "message": f"Could not read progress: {exc}"}}


def write_progress_stub(state: str, message: str):
    """Lightweight updater for progress summary without overwriting per-source data."""
    payload = read_progress_file()
    now = datetime.now(timezone.utc).isoformat()
    payload.setdefault("started_at", now)
    payload["updated_at"] = now
    payload.setdefault("sources", {})
    payload["summary"] = {
        **payload.get("summary", {}),
        "state": state,
        "message": message,
        "events": payload.get("summary", {}).get("events", len(events_data)),
        "total_sources": payload.get("summary", {}).get("total_sources", len(payload.get("sources", {})))
    }
    try:
        PROGRESS_FILE.write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        print(f"Failed to write progress stub: {exc}")

@app.route('/')
def index():
    """Main page"""
    csv_file = load_latest_csv()
    libraries = []
    types = []
    min_date_iso, max_date_iso = None, None
    
    if events_data:
        libraries = sorted(list(set(event.get('Library', '') for event in events_data)))
        # Collect distinct age groups (used as "Type")
        types = sorted(list(set(
            e.get('Age Group', '') for e in events_data if (e.get('Age Group', '') or '').strip()
        )))
        # Compute min/max date bounds from loaded events
        parsed_dates = []
        for e in events_data:
            dstr = e.get('Date') or ''
            try:
                dt = datetime.strptime(str(dstr), "%A, %B %d, %Y")
                parsed_dates.append(dt.date())
            except Exception:
                continue
        if parsed_dates:
            min_date_iso = min(parsed_dates).isoformat()
            max_date_iso = max(parsed_dates).isoformat()
    
    return render_template('index.html', 
                         total_events=len(events_data),
                         csv_file=csv_file,
                         libraries=libraries,
                         types=types,
                         min_date=min_date_iso,
                         max_date=max_date_iso)

@app.route('/health')
def health():
    return jsonify({"ok": True, "total_events": len(events_data)}), 200

@app.route('/api/events')
def get_events():
    """API endpoint to get filtered events"""
    library_filters = [l.strip() for l in request.args.getlist('library') if (l or '').strip()]
    fallback_library = (request.args.get('library', '') or '').strip()
    if not library_filters and fallback_library and fallback_library != 'All':
        library_filters = [fallback_library]
    # Support multiple `type` params, e.g., ?type=A&type=B
    type_filters = [t.strip() for t in request.args.getlist('type') if (t or '').strip()]
    search_term = request.args.get('search', '').lower().strip()
    date_filter = request.args.get('date', '').strip()  # legacy single date YYYY-MM-DD
    start_date = request.args.get('start', '').strip()  # YYYY-MM-DD
    end_date = request.args.get('end', '').strip()      # YYYY-MM-DD
    search_fields = [s.strip() for s in request.args.get('search_fields', '').split(',') if (s or '').strip()]
    search_mode = request.args.get('search_mode', 'any').lower().strip() or 'any'

    # Location filtering parameters
    user_address = request.args.get('address', '').strip()
    max_distance = request.args.get('distance', '').strip()
    max_distance = float(max_distance) if max_distance and max_distance.replace('.', '').isdigit() else None

    filtered_events = filter_events(
        library_filters=library_filters,
        type_filters=type_filters,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        date_filter=date_filter,
        search_fields=search_fields,
        search_mode=search_mode,
        user_address=user_address,
        max_distance=max_distance
    )

    return jsonify({
        'events': filtered_events,
        'total': len(filtered_events),
        'ai_enabled': AI_AVAILABLE and os.getenv("ENABLE_AI_SUMMARIZATION", "false").lower() == "true"
    })


@app.route('/api/ics')
def download_ics():
    """Download an ICS file for all or filtered events using the same filters as /api/events."""
    library_filters = [l.strip() for l in request.args.getlist('library') if (l or '').strip()]
    fallback_library = (request.args.get('library', '') or '').strip()
    if not library_filters and fallback_library and fallback_library != 'All':
        library_filters = [fallback_library]
    type_filters = [t.strip() for t in request.args.getlist('type') if (t or '').strip()]
    search_term = request.args.get('search', '').lower().strip()
    date_filter = request.args.get('date', '').strip()
    start_date = request.args.get('start', '').strip()
    end_date = request.args.get('end', '').strip()
    search_fields = [s.strip() for s in request.args.get('search_fields', '').split(',') if (s or '').strip()]
    search_mode = request.args.get('search_mode', 'any').lower().strip() or 'any'

    # Location filtering parameters
    user_address = request.args.get('address', '').strip()
    max_distance = request.args.get('distance', '').strip()
    max_distance = float(max_distance) if max_distance and max_distance.replace('.', '').isdigit() else None

    filtered_events = filter_events(
        library_filters=library_filters,
        type_filters=type_filters,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        date_filter=date_filter,
        search_fields=search_fields,
        search_mode=search_mode,
        user_address=user_address,
        max_distance=max_distance
    )

    if not filtered_events:
        return jsonify({'error': 'No events available for ICS export'}), 404

    filename_parts = ["library_events"]
    if library_filters:
        filename_parts.append(slugify("_".join(sorted(set(library_filters)))))
    if start_date or end_date:
        filename_parts.append("_".join(filter(None, [start_date, end_date])))
    ics_content, filename = events_to_ics(filtered_events, "_".join(filter(None, filename_parts)))

    buffer = BytesIO(ics_content.encode('utf-8'))
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='text/calendar',
        as_attachment=True,
        download_name=filename
    )


@app.route('/api/pdf')
def download_pdf():
    """Download a PDF organized by day, then library, then time, honoring any filters."""
    library_filters = [l.strip() for l in request.args.getlist('library') if (l or '').strip()]
    fallback_library = (request.args.get('library', '') or '').strip()
    if not library_filters and fallback_library and fallback_library != 'All':
        library_filters = [fallback_library]
    type_filters = [t.strip() for t in request.args.getlist('type') if (t or '').strip()]
    search_term = request.args.get('search', '').lower().strip()
    date_filter = request.args.get('date', '').strip()
    start_date = request.args.get('start', '').strip()
    end_date = request.args.get('end', '').strip()
    search_fields = [s.strip() for s in request.args.get('search_fields', '').split(',') if (s or '').strip()]
    search_mode = request.args.get('search_mode', 'any').lower().strip() or 'any'

    # Location filtering parameters
    user_address = request.args.get('address', '').strip()
    max_distance = request.args.get('distance', '').strip()
    max_distance = float(max_distance) if max_distance and max_distance.replace('.', '').isdigit() else None

    filtered_events = filter_events(
        library_filters=library_filters,
        type_filters=type_filters,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        date_filter=date_filter,
        search_fields=search_fields,
        search_mode=search_mode,
        user_address=user_address,
        max_distance=max_distance
    )

    if not filtered_events:
        return jsonify({'error': 'No events available for PDF export'}), 404

    filename_parts = ["library_events"]
    if library_filters:
        filename_parts.append(slugify("_".join(sorted(set(library_filters)))))
    if start_date or end_date:
        filename_parts.append("_".join(filter(None, [start_date, end_date])))

    buffer, filename = events_to_pdf(filtered_events, "_".join(filter(None, filename_parts)))

    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )

@app.route('/api/refresh', methods=['POST', 'GET'])
def refresh_data():
    """Refresh data by running the scraper in the background so the UI can poll progress."""
    global SCRAPER_THREAD
    with SCRAPER_LOCK:
        if SCRAPER_THREAD and SCRAPER_THREAD.is_alive():
            return jsonify({
                'success': False,
                'running': True,
                'message': 'Scraper is already running'
            }), 409

        SCRAPER_THREAD = threading.Thread(target=run_scraper_background, daemon=True)
        SCRAPER_THREAD.start()

    return jsonify({
        'success': True,
        'started': True,
        'message': 'Scraper started. Progress will update below.',
        'total_events': len(events_data)
    })


def run_scraper_background():
    """Kick off the scraper in a background thread so the UI stays responsive."""
    import subprocess
    write_progress_stub("queued", "Starting scraper...")
    env = os.environ.copy()
    env["DATA_DIR"] = str(DATA_DIR)

    try:
        result = subprocess.run(
            ['python3', 'library_all_events.py'],
            capture_output=True,
            text=True,
            timeout=330,
            cwd=BASE_DIR,
            env=env
        )
        if result.returncode == 0:
            load_latest_csv()
            # If the scraper did not emit a progress file, provide a minimal success snapshot
            if not PROGRESS_FILE.exists():
                write_progress_stub("completed", f"Scrape finished with {len(events_data)} events")
        else:
            msg = (result.stderr or result.stdout or "Unknown error").strip()
            write_progress_stub("error", f"Scraper failed: {msg[:500]}")
    except subprocess.TimeoutExpired:
        write_progress_stub("error", "Scraper timed out after 5.5 minutes")
    except Exception as exc:
        write_progress_stub("error", f"Failed to run scraper: {exc}")
    finally:
        global SCRAPER_THREAD
        with SCRAPER_LOCK:
            SCRAPER_THREAD = None


@app.route('/api/progress')
def progress_status():
    """Expose the latest scraper progress snapshot."""
    return jsonify(read_progress_file())

def create_html_template():
    """Create the HTML template"""
    template_dir = BASE_DIR / 'templates'
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / 'index.html'
    # If a template already exists, do not overwrite it
    if template_path.exists():
        return

    html_content = '''<!DOCTYPE html>
<html class="light" lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chicago Library Events</title>
    <script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            darkMode: "class",
            theme: {
                extend: {
                    colors: {
                        "primary": "#137fec",
                        "background-light": "#f6f7f8",
                        "background-dark": "#101922",
                    },
                    fontFamily: {
                        "display": ["Inter", "sans-serif"]
                    },
                    borderRadius: {"DEFAULT": "0.25rem", "lg": "0.5rem", "xl": "0.75rem", "full": "9999px"},
                },
            },
        }
    </script>
    <style>
        .material-symbols-outlined {
            font-variation-settings:
            'FILL' 0,
            'wght' 400,
            'GRAD' 0,
            'opsz' 24
        }
        .checkbox-container {
            max-height: 150px;
            overflow-y: auto;
            scrollbar-width: thin;
        }
        .checkbox-container::-webkit-scrollbar {
            width: 6px;
        }
        .checkbox-container::-webkit-scrollbar-track {
            background: #f1f5f9;
        }
        .checkbox-container::-webkit-scrollbar-thumb {
            background: #cbd5e1;
            border-radius: 3px;
        }
    </style>
</head>
<body class="font-display bg-background-light dark:bg-background-dark min-h-screen">
    <div class="relative flex h-auto min-h-screen w-full flex-col">
        <!-- Header -->
        <header class="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 dark:border-slate-800 bg-white/80 dark:bg-background-dark/80 backdrop-blur-sm px-6 py-4">
            <div class="flex items-center gap-3">
                <span class="material-symbols-outlined text-primary text-2xl">local_library</span>
                <div>
                    <h1 class="text-slate-800 dark:text-slate-200 text-xl font-bold">Chicago Library Events</h1>
                    <p class="text-slate-600 dark:text-slate-400 text-sm">
                        Total Events: {{ total_events }}
                        {% if csv_file %}• Loaded from: {{ csv_file }}{% endif %}
                    </p>
                </div>
            </div>
            <div class="flex gap-3">
                <button onclick="downloadICS()"
                        class="inline-flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg font-medium transition-colors">
                    <span class="material-symbols-outlined text-lg">calendar_month</span>
                    Download ICS
                </button>
                <button onclick="downloadPDF()"
                        class="inline-flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg font-medium transition-colors">
                    <span class="material-symbols-outlined text-lg">picture_as_pdf</span>
                    Download PDF
                </button>
                <button onclick="refreshData()"
                        id="refresh-btn"
                        class="inline-flex items-center gap-2 px-4 py-2 bg-primary hover:bg-blue-600 text-white rounded-lg font-medium transition-colors">
                    <span class="material-symbols-outlined text-lg">refresh</span>
                    Refresh Data
                </button>
            </div>
        </header>

        <!-- Filters Section -->
        <div class="bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 px-6 py-4">
            <div class="grid grid-cols-1 lg:grid-cols-4 xl:grid-cols-6 gap-4 items-end">
                <!-- Library Filter -->
                <div>
                    <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Library</label>
                    <select id="library-filter"
                            class="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-primary focus:border-primary">
                        <option value="All">All Libraries</option>
                        {% for library in libraries %}
                        <option value="{{ library }}">{{ library }}</option>
                        {% endfor %}
                    </select>
                </div>

                <!-- Age Group Filter -->
                <div>
                    <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Age Groups</label>
                    <div class="border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 p-3 checkbox-container">
                        {% if types %}
                            {% for t in types %}
                            <label class="flex items-center text-sm text-slate-700 dark:text-slate-300 mb-2 last:mb-0 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700 px-2 py-1 rounded">
                                <input type="checkbox" name="type" value="{{ t }}"
                                       class="w-4 h-4 text-primary border-slate-300 rounded focus:ring-primary mr-2">
                                <span class="truncate">{{ t }}</span>
                            </label>
                            {% endfor %}
                        {% endif %}
                    </div>
                </div>

                <!-- Search -->
                <div class="space-y-2">
                    <div>
                        <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Search</label>
                        <div class="relative">
                            <span class="material-symbols-outlined absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 text-lg">search</span>
                            <input type="text" id="search-input" placeholder="Search events..."
                                   class="w-full pl-10 pr-4 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-primary focus:border-primary">
                        </div>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <div>
                            <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Search Mode</label>
                            <select id="search-mode"
                                    class="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-primary focus:border-primary">
                                <option value="any" selected>Any word</option>
                                <option value="all">All words</option>
                                <option value="exact">Exact phrase</option>
                                <option value="fuzzy">Fuzzy match</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Fields</label>
                            <div class="grid grid-cols-2 gap-2 text-sm">
                                <label class="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                                    <input type="checkbox" name="search-field" value="title" class="w-4 h-4 text-primary border-slate-300 rounded focus:ring-primary" checked>
                                    <span>Title</span>
                                </label>
                                <label class="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                                    <input type="checkbox" name="search-field" value="description" class="w-4 h-4 text-primary border-slate-300 rounded focus:ring-primary" checked>
                                    <span>Description</span>
                                </label>
                                <label class="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                                    <input type="checkbox" name="search-field" value="location" class="w-4 h-4 text-primary border-slate-300 rounded focus:ring-primary" checked>
                                    <span>Location</span>
                                </label>
                                <label class="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                                    <input type="checkbox" name="search-field" value="library" class="w-4 h-4 text-primary border-slate-300 rounded focus:ring-primary">
                                    <span>Library</span>
                                </label>
                                <label class="flex items-center gap-2 text-slate-700 dark:text-slate-300">
                                    <input type="checkbox" name="search-field" value="age_group" class="w-4 h-4 text-primary border-slate-300 rounded focus:ring-primary">
                                    <span>Age Group</span>
                                </label>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Date Preset -->
                <div>
                    <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Date Range</label>
                    <select id="date-preset"
                            class="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-primary focus:border-primary">
                        <option value="all">All Dates</option>
                        <option value="today">Today</option>
                        <option value="tomorrow">Tomorrow</option>
                        <option value="next7">Next 7 Days</option>
                        <option value="next14">Next 14 Days</option>
                        <option value="thisweek">This Week</option>
                        <option value="weekend">This Weekend</option>
                        <option value="thismonth">This Month</option>
                        <option value="custom">Custom Range</option>
                    </select>
                </div>

                <!-- Custom Date Range (Hidden by default) -->
                <div id="custom-date-range" class="hidden col-span-2">
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Start Date</label>
                            <input type="date" id="start-date"
                                   {% if min_date %}min="{{ min_date }}"{% endif %}
                                   {% if max_date %}max="{{ max_date }}"{% endif %}
                                   class="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-primary focus:border-primary">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">End Date</label>
                            <input type="date" id="end-date"
                                   {% if min_date %}min="{{ min_date }}"{% endif %}
                                   {% if max_date %}max="{{ max_date }}"{% endif %}
                                   class="w-full px-3 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-primary focus:border-primary">
                        </div>
                    </div>
                </div>

                <!-- Apply Filters Button -->
                <div class="lg:col-span-1">
                    <button onclick="applyFilters()"
                            class="w-full px-4 py-2 bg-primary hover:bg-blue-600 text-white rounded-lg font-medium transition-colors inline-flex items-center justify-center gap-2">
                        <span class="material-symbols-outlined text-lg">filter_list</span>
                        Apply Filters
                    </button>
                </div>
            </div>
        </div>

        <!-- Events Grid -->
        <main class="flex-1 p-6">
            <div id="events-grid" class="grid gap-6 grid-cols-1 lg:grid-cols-2 xl:grid-cols-3">
                <div class="col-span-full text-center py-12">
                    <div class="animate-spin w-8 h-8 border-4 border-primary border-t-transparent rounded-full mx-auto mb-4"></div>
                    <p class="text-slate-600 dark:text-slate-400">Loading events...</p>
                </div>
            </div>
        </main>

        <!-- Status Bar -->
        <footer class="border-t border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900 px-6 py-3">
            <div class="flex items-center justify-between">
                <span id="status" class="text-sm text-slate-600 dark:text-slate-400">Ready</span>
                <div class="text-xs text-slate-500 dark:text-slate-500">
                    Last updated: <span id="last-updated">{{ moment().format('YYYY-MM-DD HH:mm') }}</span>
                </div>
            </div>
        </footer>
    </div>

    <script>
        let allEvents = [];
        let filteredEvents = [];

        // Load events on page load
        document.addEventListener('DOMContentLoaded', function() {
            // Initialize preset to Today if within bounds
            const preset = document.getElementById('date-preset');
            const startInput = document.getElementById('start-date');
            const endInput = document.getElementById('end-date');
            const today = new Date();
            const yyyy = today.getFullYear();
            const mm = String(today.getMonth() + 1).padStart(2, '0');
            const dd = String(today.getDate()).padStart(2, '0');
            const todayIso = `${yyyy}-${mm}-${dd}`;
            const min = startInput ? startInput.getAttribute('min') : null;
            const max = endInput ? endInput.getAttribute('max') : null;
            if ((!min || todayIso >= min) && (!max || todayIso <= max)) {
                preset.value = 'today';
            } else {
                preset.value = 'all';
            }

            // Toggle custom range UI
            preset.addEventListener('change', function() {
                const show = preset.value === 'custom';
                const customRange = document.getElementById('custom-date-range');
                if (show) {
                    customRange.classList.remove('hidden');
                } else {
                    customRange.classList.add('hidden');
                }
            });

            loadEvents().then(() => applyFilters());

            // Add enter key support for search
            document.getElementById('search-input').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    applyFilters();
                }
            });

            // Update last updated time
            document.getElementById('last-updated').textContent = new Date().toLocaleString();
        });

        async function loadEvents() {
            try {
                const response = await fetch('/api/events');
                const data = await response.json();
                allEvents = data.events;
                displayEvents(allEvents);
                updateStatus(`Showing ${data.total} events`);
            } catch (error) {
                document.getElementById('events-grid').innerHTML =
                    `<div class="col-span-full text-center py-12">
                        <span class="material-symbols-outlined text-4xl text-red-500 mb-4 block">error</span>
                        <p class="text-slate-600 dark:text-slate-400">Error loading events: ${error.message}</p>
                    </div>`;
                updateStatus('Error loading events');
            }
        }

        async function applyFilters() {
            const library = document.getElementById('library-filter').value;
            const typeSelected = Array.from(document.querySelectorAll('input[name="type"]:checked')).map(cb => cb.value);
            const search = document.getElementById('search-input').value;
            const searchMode = document.getElementById('search-mode').value;
            const searchFields = Array.from(document.querySelectorAll('input[name="search-field"]:checked')).map(cb => cb.value);
            const preset = document.getElementById('date-preset').value;
            const startInput = document.getElementById('start-date');
            const endInput = document.getElementById('end-date');
            const { startIso, endIso, note } = computeDateRange(preset, startInput.value, endInput.value);

            updateStatus('Filtering events...');

            try {
                const typeParams = typeSelected.map(t => `&type=${encodeURIComponent(t)}`).join('');
                const fieldParams = searchFields.length ? `&search_fields=${encodeURIComponent(searchFields.join(','))}` : '';
                const modeParam = searchMode ? `&search_mode=${encodeURIComponent(searchMode)}` : '';
                const rangeParams = (startIso ? `&start=${encodeURIComponent(startIso)}` : '') + (endIso ? `&end=${encodeURIComponent(endIso)}` : '');
                const url = `/api/events?library=${encodeURIComponent(library)}${typeParams}&search=${encodeURIComponent(search)}${fieldParams}${modeParam}${rangeParams}`;
                const response = await fetch(url);
                const data = await response.json();
                filteredEvents = data.events;
                displayEvents(filteredEvents);
                const dateNote = note ? ` ${note}` : '';
                updateStatus(`Showing ${data.total} of ${allEvents.length} events${dateNote}`);
            } catch (error) {
                updateStatus('Error filtering events');
            }
        }

        function computeDateRange(preset, customStart, customEnd) {
            const today = new Date();
            const toISO = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
            let start = null, end = null, note = '';
            const dow = today.getDay(); // 0=Sun..6=Sat
            if (preset === 'all') {
                return { startIso: '', endIso: '', note: '' };
            }
            if (preset === 'today') {
                start = new Date(today); end = new Date(today);
                note = `on ${toISO(start)}`;
            } else if (preset === 'tomorrow') {
                start = new Date(today); start.setDate(start.getDate()+1); end = new Date(start);
                note = `on ${toISO(start)}`;
            } else if (preset === 'next7') {
                start = new Date(today); end = new Date(today); end.setDate(end.getDate()+6);
                note = `from ${toISO(start)} to ${toISO(end)}`;
            } else if (preset === 'next14') {
                start = new Date(today); end = new Date(today); end.setDate(end.getDate()+13);
                note = `from ${toISO(start)} to ${toISO(end)}`;
            } else if (preset === 'thisweek') {
                // Sunday-Saturday
                start = new Date(today); start.setDate(start.getDate() - dow);
                end = new Date(start); end.setDate(start.getDate()+6);
                note = `this week (${toISO(start)} to ${toISO(end)})`;
            } else if (preset === 'weekend') {
                // Upcoming Fri-Sun (if already weekend, use current Fri-Sun)
                const daysUntilFri = (5 - dow + 7) % 7;
                start = new Date(today); start.setDate(start.getDate() + daysUntilFri);
                end = new Date(start); end.setDate(start.getDate() + 2);
                // If today is Sun (0), daysUntilFri=5 ⇒ next weekend; that's fine
                note = `weekend (${toISO(start)} to ${toISO(end)})`;
            } else if (preset === 'thismonth') {
                start = new Date(today.getFullYear(), today.getMonth(), 1);
                end = new Date(today.getFullYear(), today.getMonth()+1, 0);
                note = `this month (${toISO(start)} to ${toISO(end)})`;
            } else if (preset === 'custom') {
                const s = customStart || '';
                const e = customEnd || '';
                if (s && e) note = `from ${s} to ${e}`;
                else if (s) note = `on/after ${s}`;
                else if (e) note = `on/before ${e}`;
                return { startIso: s, endIso: e, note };
            }
            return { startIso: start ? toISO(start) : '', endIso: end ? toISO(end) : '', note };
        }

        function displayEvents(events) {
            const grid = document.getElementById('events-grid');

            if (events.length === 0) {
                grid.innerHTML = `
                    <div class="col-span-full text-center py-12">
                        <span class="material-symbols-outlined text-4xl text-slate-400 mb-4 block">search_off</span>
                        <p class="text-slate-600 dark:text-slate-400">No events found matching your criteria.</p>
                    </div>`;
                return;
            }

            grid.innerHTML = events.map(event => {
                const ageGroup = event['Age Group'] || 'All Ages';
                const ageColor = getAgeGroupColor(ageGroup);

                return `
                    <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-6 shadow-sm hover:shadow-md transition-shadow duration-200">
                        <div class="flex justify-between items-start mb-4">
                            <h3 class="text-lg font-semibold text-slate-900 dark:text-slate-100 leading-tight pr-4">
                                ${escapeHtml(event.Title || 'Untitled Event')}
                            </h3>
                            <span class="px-3 py-1 bg-primary/10 text-primary text-xs font-medium rounded-full whitespace-nowrap">
                                ${escapeHtml(event.Library || 'Unknown')}
                            </span>
                        </div>

                        <div class="space-y-3 mb-4">
                            <div class="flex items-center text-sm text-slate-600 dark:text-slate-400">
                                <span class="material-symbols-outlined text-lg mr-3 text-slate-400">event</span>
                                ${escapeHtml(event.Date || 'Date TBD')}
                            </div>
                            <div class="flex items-center text-sm text-slate-600 dark:text-slate-400">
                                <span class="material-symbols-outlined text-lg mr-3 text-slate-400">schedule</span>
                                ${escapeHtml(event.Time || 'Time TBD')}
                            </div>
                            <div class="flex items-center text-sm text-slate-600 dark:text-slate-400">
                                <span class="material-symbols-outlined text-lg mr-3 text-slate-400">location_on</span>
                                ${escapeHtml(event.Location || 'Location TBD')}
                            </div>
                            <div class="flex items-center text-sm text-slate-600 dark:text-slate-400">
                                <span class="material-symbols-outlined text-lg mr-3 text-slate-400">group</span>
                                <span class="px-2 py-1 ${ageColor} text-xs font-medium rounded">
                                    ${escapeHtml(ageGroup)}
                                </span>
                            </div>
                        </div>

                        ${event.Description && event.Description !== 'Not found' ?
                            `<p class="text-sm text-slate-600 dark:text-slate-400 mb-4 line-clamp-3">
                                ${escapeHtml(event.Description)}
                            </p>` : ''}

                        ${event.Link && event.Link !== 'N/A' && event.Link !== '' ?
                            `<div class="pt-3 border-t border-slate-200 dark:border-slate-700">
                                <a href="${escapeHtml(event.Link)}"
                                   target="_blank"
                                   rel="noopener"
                                   class="inline-flex items-center gap-2 text-primary hover:text-blue-700 font-medium text-sm transition-colors">
                                    <span>More Information</span>
                                    <span class="material-symbols-outlined text-sm">open_in_new</span>
                                </a>
                            </div>` : ''}
                    </div>`;
            }).join('');
        }

        function getAgeGroupColor(ageGroup) {
            const colors = {
                'Baby/Toddler': 'bg-pink-100 text-pink-700 dark:bg-pink-900 dark:text-pink-300',
                'Preschool/Early Elementary': 'bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300',
                'Elementary': 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300',
                'Middle School/Teen': 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
                'Teen/Young Adult': 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300',
                'Adult': 'bg-gray-100 text-gray-700 dark:bg-gray-900 dark:text-gray-300',
                'Family/All Ages': 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300',
                'Kids': 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300'
            };
            return colors[ageGroup] || 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
        }

        async function downloadICS() {
            const params = getCurrentFilterParams();
            window.location.href = `/api/ics?${params}`;
        }

        async function downloadPDF() {
            const params = getCurrentFilterParams();
            window.location.href = `/api/pdf?${params}`;
        }

        function getCurrentFilterParams() {
            const library = document.getElementById('library-filter').value;
            const typeSelected = Array.from(document.querySelectorAll('input[name="type"]:checked')).map(cb => cb.value);
            const search = document.getElementById('search-input').value;
            const searchMode = document.getElementById('search-mode').value;
            const searchFields = Array.from(document.querySelectorAll('input[name="search-field"]:checked')).map(cb => cb.value);
            const preset = document.getElementById('date-preset').value;
            const startInput = document.getElementById('start-date');
            const endInput = document.getElementById('end-date');
            const { startIso, endIso } = computeDateRange(preset, startInput.value, endInput.value);

            const params = new URLSearchParams();
            if (library !== 'All') params.append('library', library);
            typeSelected.forEach(t => params.append('type', t));
            if (search) params.append('search', search);
            if (searchMode) params.append('search_mode', searchMode);
            if (searchFields.length) params.append('search_fields', searchFields.join(','));
            if (startIso) params.append('start', startIso);
            if (endIso) params.append('end', endIso);

            return params.toString();
        }

        async function refreshData() {
            updateStatus('Refreshing data... This may take a few minutes.');
            const refreshBtn = document.getElementById('refresh-btn');
            const originalText = refreshBtn.innerHTML;
            refreshBtn.innerHTML = `
                <div class="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full"></div>
                Refreshing...
            `;
            refreshBtn.disabled = true;

            try {
                const response = await fetch('/api/refresh');
                const result = await response.json();

                if (result.success) {
                    updateStatus(result.message);
                    await loadEvents();
                    document.getElementById('last-updated').textContent = new Date().toLocaleString();
                } else {
                    updateStatus('Error: ' + result.message);
                }
            } catch (error) {
                updateStatus('Error refreshing data: ' + error.message);
            } finally {
                refreshBtn.disabled = false;
                refreshBtn.innerHTML = originalText;
            }
        }

        function updateStatus(message) {
            document.getElementById('status').textContent = message;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    </script>
</body>
</html>'''
    
    with open(template_path, 'w') as f:
        f.write(html_content)

def open_browser(port):
    """Open the web browser after a short delay"""
    time.sleep(1.5)
    webbrowser.open(f'http://localhost:{port}')

if __name__ == '__main__':
    print("🚀 Starting Library Events Web GUI...")
    
    # Create HTML template
    create_html_template()
    
    # Load initial data
    load_latest_csv()
    
    port = int(os.getenv("PORT", "8888"))

    # Open browser in a separate thread
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    
    print("📚 Library Events GUI will open in your browser...")
    print(f"🌐 Access at: http://localhost:{port}")
    print("⏹️  Press Ctrl+C to stop the server")
    
    # Start Flask app
    app.run(debug=False, host='0.0.0.0', port=port)
