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
                <div>
                    <label class="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">Search</label>
                    <div class="relative">
                        <span class="material-symbols-outlined absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 text-lg">search</span>
                        <input type="text" id="search-input" placeholder="Search events..."
                               class="w-full pl-10 pr-4 py-2 border border-slate-300 dark:border-slate-700 rounded-lg bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-primary focus:border-primary">
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
            const preset = document.getElementById('date-preset').value;
            const startInput = document.getElementById('start-date');
            const endInput = document.getElementById('end-date');
            const { startIso, endIso } = computeDateRange(preset, startInput.value, endInput.value);

            const params = new URLSearchParams();
            if (library !== 'All') params.append('library', library);
            typeSelected.forEach(t => params.append('type', t));
            if (search) params.append('search', search);
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
