#!/usr/bin/env python3
"""
Library Events Web GUI
A simple Flask-based web interface to view, filter, and export library events.
"""

from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
import os
import json
import re
import hashlib
from io import BytesIO
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
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
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR)).resolve()
TIMEZONE = os.getenv("TIMEZONE", "America/Chicago")
TZINFO = ZoneInfo(TIMEZONE)
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

# Global variable to store events data
events_data = []


def clean_text(value: str) -> str:
    """Lightweight cleaner for ICS-friendly text."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\u200b", " ").split())


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
    library_filter='All',
    type_filters=None,
    search_term='',
    start_date='',
    end_date='',
    date_filter='',
    search_fields=None,
    search_mode='any'
):
    """Apply shared filtering logic used by both JSON API and ICS export."""
    type_filters = type_filters or []
    filtered_events = events_data.copy()
    search_mode = (search_mode or 'any').lower()

    # Apply library filter
    if library_filter and library_filter != 'All':
        filtered_events = [e for e in filtered_events if e.get('Library', '') == library_filter]

    # Apply type (age group) filters
    if type_filters:
        selected = set(type_filters)

        def get_event_types(ev):
            raw = (ev.get('Age Group', '') or '')
            parts = [p.strip() for p in str(raw).split(',') if p.strip()]
            return set(parts) if parts else {raw} if raw else set()

        filtered_events = [e for e in filtered_events if get_event_types(e) & selected]

    # Apply search filter with configurable fields and modes
    if search_term:
        raw = search_term.lower().strip()
        # Support quoted phrases or space-separated tokens
        tokens = []
        for m in re.finditer(r'"([^"]+)"|(\S+)', raw):
            token = (m.group(1) or m.group(2) or '').strip()
            if token:
                tokens.append(token)

        field_alias = {
            'title': 'Title',
            'description': 'Description',
            'location': 'Location',
            'age': 'Age Group',
            'age_group': 'Age Group',
            'type': 'Age Group',
            'library': 'Library'
        }
        selected_fields = []
        for sf in search_fields or []:
            key = field_alias.get(sf.lower())
            if key:
                selected_fields.append(key)
        if not selected_fields:
            selected_fields = ['Title', 'Description', 'Location']

        def matches(ev):
            values = " ".join(str(ev.get(f, '') or '') for f in selected_fields).lower()
            if raw and raw in values:
                return True
            if not tokens:
                return True
            if search_mode == 'all':
                return all(tok in values for tok in tokens)
            return any(tok in values for tok in tokens)

        filtered_events = [e for e in filtered_events if matches(e)]

    # Date filtering: single date (legacy) or range [start, end]
    # If explicit range provided, prefer that
    def within_range(ev_date, start, end):
        if not ev_date:
            return False
        if start and ev_date < start:
            return False
        if end and ev_date > end:
            return False
        return True

    if start_date or end_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
        except ValueError:
            start = None
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        except ValueError:
            end = None
        filtered_events = [e for e in filtered_events if within_range(parse_event_date(e.get('Date') or ''), start, end)]
    elif date_filter:
        try:
            target = datetime.strptime(date_filter, "%Y-%m-%d").date()
        except ValueError:
            target = None
        if target:
            filtered_events = [e for e in filtered_events if parse_event_date(e.get('Date') or '') == target]

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
    """Load the most recent CSV file"""
    global events_data
    
    csv_files = list(DATA_DIR.glob('all_library_events_*.csv'))
    
    if csv_files:
        latest_file = max(csv_files, key=lambda p: p.stat().st_mtime)
        try:
            df = pd.read_csv(latest_file)
            # Replace NaN values with empty strings
            df = df.fillna('')
            events_data = df.to_dict('records')
            print(f"Loaded {len(events_data)} events from {latest_file}")
            return latest_file.name
        except Exception as e:
            print(f"Error loading CSV: {e}")
            events_data = []
            return None
    else:
        print(f"No CSV files found in {DATA_DIR}")
        events_data = []
        return None

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
    library_filter = request.args.get('library', 'All')
    # Support multiple `type` params, e.g., ?type=A&type=B
    type_filters = [t.strip() for t in request.args.getlist('type') if (t or '').strip()]
    search_term = request.args.get('search', '').lower().strip()
    date_filter = request.args.get('date', '').strip()  # legacy single date YYYY-MM-DD
    start_date = request.args.get('start', '').strip()  # YYYY-MM-DD
    end_date = request.args.get('end', '').strip()      # YYYY-MM-DD
    search_fields = [s.strip() for s in request.args.get('search_fields', '').split(',') if (s or '').strip()]
    search_mode = request.args.get('search_mode', 'any').lower().strip() or 'any'

    filtered_events = filter_events(
        library_filter=library_filter,
        type_filters=type_filters,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        date_filter=date_filter,
        search_fields=search_fields,
        search_mode=search_mode
    )

    return jsonify({
        'events': filtered_events,
        'total': len(filtered_events)
    })


@app.route('/api/ics')
def download_ics():
    """Download an ICS file for all or filtered events using the same filters as /api/events."""
    library_filter = request.args.get('library', 'All')
    type_filters = [t.strip() for t in request.args.getlist('type') if (t or '').strip()]
    search_term = request.args.get('search', '').lower().strip()
    date_filter = request.args.get('date', '').strip()
    start_date = request.args.get('start', '').strip()
    end_date = request.args.get('end', '').strip()
    search_fields = [s.strip() for s in request.args.get('search_fields', '').split(',') if (s or '').strip()]
    search_mode = request.args.get('search_mode', 'any').lower().strip() or 'any'

    filtered_events = filter_events(
        library_filter=library_filter,
        type_filters=type_filters,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        date_filter=date_filter,
        search_fields=search_fields,
        search_mode=search_mode
    )

    if not filtered_events:
        return jsonify({'error': 'No events available for ICS export'}), 404

    filename_parts = ["library_events"]
    if library_filter and library_filter != 'All':
        filename_parts.append(slugify(library_filter))
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
    library_filter = request.args.get('library', 'All')
    type_filters = [t.strip() for t in request.args.getlist('type') if (t or '').strip()]
    search_term = request.args.get('search', '').lower().strip()
    date_filter = request.args.get('date', '').strip()
    start_date = request.args.get('start', '').strip()
    end_date = request.args.get('end', '').strip()
    search_fields = [s.strip() for s in request.args.get('search_fields', '').split(',') if (s or '').strip()]
    search_mode = request.args.get('search_mode', 'any').lower().strip() or 'any'

    filtered_events = filter_events(
        library_filter=library_filter,
        type_filters=type_filters,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        date_filter=date_filter,
        search_fields=search_fields,
        search_mode=search_mode
    )

    if not filtered_events:
        return jsonify({'error': 'No events available for PDF export'}), 404

    filename_parts = ["library_events"]
    if library_filter and library_filter != 'All':
        filename_parts.append(slugify(library_filter))
    if start_date or end_date:
        filename_parts.append("_".join(filter(None, [start_date, end_date])))

    buffer, filename = events_to_pdf(filtered_events, "_".join(filter(None, filename_parts)))

    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )

@app.route('/api/refresh')
def refresh_data():
    """Refresh data by running the scraper"""
    try:
        import subprocess
        # Use the comprehensive aggregator which preserves exact age labels
        env = os.environ.copy()
        env["DATA_DIR"] = str(DATA_DIR)
        result = subprocess.run(['python3', 'library_all_events.py'], 
                              capture_output=True, text=True, timeout=300, cwd=BASE_DIR, env=env)
        
        if result.returncode == 0:
            csv_file = load_latest_csv()
            return jsonify({
                'success': True, 
                'message': f'Data refreshed successfully! Loaded from {csv_file}',
                'total_events': len(events_data)
            })
        else:
            return jsonify({
                'success': False, 
                'message': f'Scraper failed: {result.stderr}'
            })
    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False, 
            'message': 'Scraper timed out after 5 minutes'
        })
    except Exception as e:
        return jsonify({
            'success': False, 
            'message': f'Failed to run scraper: {e}'
        })

def create_html_template():
    """Create the HTML template"""
    template_dir = BASE_DIR / 'templates'
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / 'index.html'
    # If a template already exists, do not overwrite it
    if template_path.exists():
        return
    
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Library Events Viewer</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .header {
            background: #2c3e50;
            color: white;
            padding: 20px;
            border-radius: 8px 8px 0 0;
        }
        .header h1 {
            margin: 0;
            font-size: 28px;
        }
        .controls {
            padding: 20px;
            border-bottom: 1px solid #eee;
            background: #f8f9fa;
        }
        .filter-group {
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
        }
        .filter-group label {
            font-weight: 600;
            color: #333;
        }
        .filter-group select, .filter-group input {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }
        .filter-group button {
            padding: 8px 16px;
            background: #3498db;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }
        .filter-group button:hover {
            background: #2980b9;
        }
        .refresh-btn {
            background: #27ae60 !important;
        }
        .refresh-btn:hover {
            background: #219a52 !important;
        }
        .events-container {
            padding: 20px;
        }
        .events-grid {
            display: grid;
            gap: 15px;
        }
        .event-card {
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            padding: 15px;
            background: white;
            transition: box-shadow 0.2s;
        }
        .event-card:hover {
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .event-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 10px;
        }
        .event-title {
            font-weight: 600;
            color: #2c3e50;
            font-size: 16px;
            margin: 0;
        }
        .event-library {
            background: #3498db;
            color: white;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
        }
        .event-meta {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 8px;
            margin-bottom: 10px;
            font-size: 14px;
            color: #666;
        }
        .event-meta span {
            display: flex;
            align-items: center;
        }
        .event-meta .icon {
            margin-right: 6px;
            width: 16px;
        }
        .event-description {
            color: #555;
            line-height: 1.4;
            margin-bottom: 10px;
            font-size: 14px;
        }
        .event-link {
            text-align: right;
        }
        .event-link a {
            color: #3498db;
            text-decoration: none;
            font-size: 14px;
        }
        .event-link a:hover {
            text-decoration: underline;
        }
        .status-bar {
            padding: 15px 20px;
            background: #f8f9fa;
            border-top: 1px solid #eee;
            color: #666;
            font-size: 14px;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
        .no-events {
            text-align: center;
            padding: 40px;
            color: #999;
        }
        @media (max-width: 768px) {
            .filter-group {
                flex-direction: column;
                align-items: stretch;
            }
            .event-header {
                flex-direction: column;
                gap: 10px;
            }
            .event-meta {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📚 Library Events Viewer</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">
                Total Events: {{ total_events }} 
                {% if csv_file %}| Loaded from: {{ csv_file }}{% endif %}
            </p>
        </div>
        
        <div class="controls">
            <div class="filter-group">
                <label for="library-filter">Library:</label>
                <select id="library-filter">
                    <option value="All">All Libraries</option>
                    {% for library in libraries %}
                    <option value="{{ library }}">{{ library }}</option>
                    {% endfor %}
                </select>
                
                <label>Type:</label>
                <div id="type-checkboxes" class="type-checkboxes" style="max-height: 120px; overflow: auto; border: 1px solid #ddd; padding: 6px 10px; border-radius: 4px; background: #fff;">
                    {% if types %}
                        {% for t in types %}
                        <label style="display:inline-flex; align-items:center; margin-right: 12px; margin-bottom: 6px; white-space: nowrap;">
                            <input type="checkbox" name="type" value="{{ t }}" style="margin-right:6px;"> {{ t }}
                        </label>
                        {% endfor %}
                    {% endif %}
                </div>

                <label for="search-input">Search:</label>
                <input type="text" id="search-input" placeholder="Search events..." style="min-width: 200px;">

                <label for="date-preset">Dates:</label>
                <select id="date-preset">
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
                <span id="custom-date-range" style="display:none;">
                    <label for="start-date">Start:</label>
                    <input type="date" id="start-date" {% if min_date %}min="{{ min_date }}"{% endif %} {% if max_date %}max="{{ max_date }}"{% endif %}>
                    <label for="end-date">End:</label>
                    <input type="date" id="end-date" {% if min_date %}min="{{ min_date }}"{% endif %} {% if max_date %}max="{{ max_date }}"{% endif %}>
                    <button type="button" id="clear-range-btn">Clear</button>
                </span>
                
                <button onclick="applyFilters()">Apply Filters</button>
                <button onclick="refreshData()" class="refresh-btn">Refresh Data</button>
            </div>
        </div>
        
        <div class="events-container">
            <div id="events-grid" class="events-grid">
                <div class="loading">Loading events...</div>
            </div>
        </div>
        
        <div class="status-bar">
            <span id="status">Ready</span>
        </div>
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
                document.getElementById('custom-date-range').style.display = show ? 'inline-flex' : 'none';
            });
            // Clear range button
            document.getElementById('clear-range-btn').addEventListener('click', function() {
                startInput.value = '';
                endInput.value = '';
                applyFilters();
            });

            loadEvents().then(() => applyFilters());
            
            // Add enter key support for search
            document.getElementById('search-input').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    applyFilters();
                }
            });
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
                    '<div class="no-events">Error loading events: ' + error.message + '</div>';
                updateStatus('Error loading events');
            }
        }

        async function applyFilters() {
            const library = document.getElementById('library-filter').value;
            const typeSelected = Array.from(document.querySelectorAll('#type-checkboxes input[type="checkbox"]:checked')).map(cb => cb.value);
            const search = document.getElementById('search-input').value;
            const preset = document.getElementById('date-preset').value;
            const startInput = document.getElementById('start-date');
            const endInput = document.getElementById('end-date');
            const { startIso, endIso, note } = computeDateRange(preset, startInput.value, endInput.value);
            
            updateStatus('Filtering events...');
            
            try {
                const typeParams = typeSelected.map(t => `&type=${encodeURIComponent(t)}`).join('');
                const rangeParams = (startIso ? `&start=${encodeURIComponent(startIso)}` : '') + (endIso ? `&end=${encodeURIComponent(endIso)}` : '');
                const url = `/api/events?library=${encodeURIComponent(library)}${typeParams}&search=${encodeURIComponent(search)}${rangeParams}`;
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
                grid.innerHTML = '<div class="no-events">No events found matching your criteria.</div>';
                return;
            }
            
            grid.innerHTML = events.map(event => `
                <div class="event-card">
                    <div class="event-header">
                        <h3 class="event-title">${escapeHtml(event.Title || 'Untitled Event')}</h3>
                        <span class="event-library">${escapeHtml(event.Library || 'Unknown')}</span>
                    </div>
                    
                    <div class="event-meta">
                        <span><span class="icon">📅</span> ${escapeHtml(event.Date || 'Date TBD')}</span>
                        <span><span class="icon">🕐</span> ${escapeHtml(event.Time || 'Time TBD')}</span>
                        <span><span class="icon">📍</span> ${escapeHtml(event.Location || 'Location TBD')}</span>
                        <span><span class="icon">👥</span> ${escapeHtml(event['Age Group'] || 'All Ages')}</span>
                    </div>
                    
                    ${event.Description && event.Description !== 'Not found' ? 
                        `<div class="event-description">${escapeHtml(event.Description)}</div>` : ''}
                    
                    ${event.Link && event.Link !== 'N/A' && event.Link !== '' ? 
                        `<div class="event-link">
                            <a href="${escapeHtml(event.Link)}" target="_blank" rel="noopener">More Info →</a>
                        </div>` : ''}
                </div>
            `).join('');
        }

        async function refreshData() {
            updateStatus('Refreshing data... This may take a few minutes.');
            const refreshBtn = document.querySelector('.refresh-btn');
            refreshBtn.disabled = true;
            refreshBtn.textContent = 'Refreshing...';
            
            try {
                const response = await fetch('/api/refresh');
                const result = await response.json();
                
                if (result.success) {
                    updateStatus(result.message);
                    await loadEvents(); // Reload events
                } else {
                    updateStatus('Error: ' + result.message);
                }
            } catch (error) {
                updateStatus('Error refreshing data: ' + error.message);
            } finally {
                refreshBtn.disabled = false;
                refreshBtn.textContent = 'Refresh Data';
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
