# CLAUDE.md - Library Event Scraper Documentation

**Last Updated**: 2025-12-20
**Repository**: library_scraper
**Primary Language**: Python 3

## Project Overview

This repository contains a comprehensive library event scraping system that aggregates children's programming events from multiple Chicago-area libraries and presents them through multiple interfaces (web, desktop GUI, CSV, PDF, ICS calendar).

### Purpose
- Scrape and aggregate library events from 8+ Chicago-area libraries
- Filter events by age group (particularly K-2 and 3-5 grades)
- Export events in multiple formats (CSV, PDF, ICS calendar)
- Provide web and desktop GUI interfaces for viewing and filtering events

### Target Audience
Parents and educators looking for children's programming at local libraries.

---

## Repository Structure

```
library_scraper/
├── library.py              # Core scraper library (older version, ~49KB)
├── library_all_events.py   # Main comprehensive scraper (~91KB)
├── library_web_gui.py      # Flask web interface (~54KB)
├── library_gui.py          # Tkinter desktop GUI (~15KB)
├── skokie_test.py          # Skokie library scraper test script
├── templates/
│   └── index.html          # Web GUI template (38KB)
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── Procfile                # Heroku deployment config
├── render.yaml             # Render.com deployment config
└── .gitignore              # Git ignore patterns
```

---

## Core Components

### 1. Main Scraper (`library_all_events.py`)

**Location**: `/home/user/library_scraper/library_all_events.py`

**Primary Functions**:
- `fetch_lincolnwood_events()` - Scrapes Lincolnwood Library using Firecrawl
- `fetch_mgpl_events()` - Scrapes Morton Grove Public Library
- `fetch_bibliocommons_events()` - Generic fetcher for Bibliocommons-based libraries (Evanston, CPL)
- `fetch_libnet_events()` - Generic fetcher for LibNet-based libraries (Wilmette, Niles)
- `fetch_skokie_events()` - Scrapes Skokie Library using Firecrawl
- `generate_pdf_report()` - Creates PDF event listings
- `generate_ics_file()` - Creates iCalendar format exports
- `main()` - Orchestrates all fetchers and generates outputs

**Key Patterns**:
- All fetchers return `List[Dict[str, Any]]` with standardized event schema
- Async/await pattern for concurrent scraping (lines 973-1076)
- Retry logic with exponential backoff (lines 65-94)
- Robust error handling with detailed logging

**Event Schema**:
```python
{
    "Library": str,          # Library name
    "Title": str,            # Event title
    "Date": str,             # Formatted date string
    "Time": str,             # Time or "All Day"
    "Location": str,         # Specific location/room
    "Age Group": str,        # Target age group
    "Program Type": str,     # Event category
    "Description": str,      # Event description
    "Link": str              # URL to event details
}
```

### 2. Web Interface (`library_web_gui.py`)

**Location**: `/home/user/library_scraper/library_web_gui.py`

**Technology**: Flask web framework

**Key Routes**:
- `/` - Main interface (renders `index.html`)
- `/api/events` - JSON API for filtered events
- `/api/export/ics` - ICS calendar export
- `/api/export/pdf` - PDF export
- `/api/scrape` - Trigger new scrape
- `/api/scrape/status` - Check scrape progress

**Features**:
- Real-time event filtering (library, date range, search terms)
- Advanced search with multiple modes (any/all/exact/fuzzy)
- Multi-field search (title, description, location, age group)
- Progress tracking for scraping operations
- Export functionality (ICS, PDF)

### 3. Desktop GUI (`library_gui.py`)

**Location**: `/home/user/library_scraper/library_gui.py`

**Technology**: Tkinter

**Features**:
- CSV file loading
- Library filtering
- Text search
- Event details display
- Link opening in browser

---

## Libraries Covered

The scraper currently supports these libraries (as of latest commit):

1. **Lincolnwood Library** - Firecrawl scraper
2. **Morton Grove Public Library (MGPL)** - Firecrawl scraper
3. **Evanston Public Library** - Bibliocommons API
4. **Chicago Public Library (CPL)**:
   - Edgebrook branch
   - Budlong Woods branch
5. **Wilmette Library** - LibNet API
6. **Skokie Public Library** - Firecrawl scraper
7. **Niles Library** - LibNet API
8. **Chicago Park District** - Based on commit history (recently added/merged)

### Library System Types

**Bibliocommons**: Evanston, CPL branches
- API-based scraping
- Paginated results
- Structured markdown parsing

**LibNet**: Wilmette, Niles
- JSON API endpoints
- Age-based filtering
- Post-processing for K-2 and 3-5 grade groups

**Custom Sites**: Lincolnwood, Morton Grove, Skokie
- Firecrawl-based scraping
- Markdown parsing
- Regex-based date/time extraction

---

## Configuration & Environment

### Environment Variables

Defined in `.env.example`:

```bash
FIRECRAWL_API_KEY=your_api_key_here  # Required for Firecrawl scrapers
TIMEZONE=America/Chicago              # Timezone for event parsing
DATA_DIR=./data                       # Data storage directory
```

Additional runtime configuration (from code):

```python
DEFAULT_DAYS_TO_FETCH = 31                    # Event fetch window
DEFAULT_LIBNET_AGES = ["Grades K-2", "Grades 3-5"]  # Age filter
FIRECRAWL_CONCURRENCY = 1                     # Rate limiting
MAX_RETRIES = 3                               # Retry attempts
```

### Command Line Arguments

`library_all_events.py` supports:

```bash
--start-date YYYY-MM-DD       # Event start date
--days N                      # Number of days to fetch
--start-offset-days N         # Offset from today
--libnet-ages "K-2,3-5"      # LibNet age filters
--libnet-request-ages "Kids"  # LibNet API ages
```

---

## Development Workflows

### Adding a New Library

1. **Identify library system type** (Bibliocommons/LibNet/Custom)
2. **For Bibliocommons**:
   - Use `fetch_bibliocommons_events(name, base_url, query_params)`
   - Add to tasks list in `main()` (line 986-995)
3. **For LibNet**:
   - Use `fetch_libnet_events(name, domain)`
   - Configure age filtering if needed
4. **For Custom**:
   - Create new async fetcher function following pattern
   - Use Firecrawl for reliable scraping
   - Parse markdown/HTML to extract events
   - Return standardized event schema

### Testing a New Scraper

```bash
# Create test file (see skokie_test.py as example)
python skokie_test.py

# Or run full scraper and check logs
python library_all_events.py --days 7
```

### Code Style Conventions

- **Error Handling**: Try/except blocks with specific exception types
- **Logging**: Use `logger.info/warning/error/debug` extensively
- **Type Hints**: Use for function signatures where helpful
- **Async**: Use `async/await` for IO-bound operations
- **Validation**: Check data types before processing
- **Cleaning**: Use `clean_text()` for all text fields (lines 100-118)

---

## Key Helper Functions

### Text Processing

**`clean_text(text: str) -> str`** (library.py:100-118)
- Removes non-ASCII characters
- Strips markdown formatting
- Removes extra whitespace
- Handles duplicate content

**`parse_time_to_sortable(time_str: str) -> datetime.time`** (library.py:120-139)
- Parses various time formats
- Returns `datetime.time` for sorting
- Handles "All Day" events

### Scraping Utilities

**`retry_with_backoff(func, *args, **kwargs)`** (library.py:65-94)
- Exponential backoff retry logic
- Handles connection errors, timeouts, rate limits
- Configurable max retries

**`firecrawl_scrape(app, url, **kwargs)`** (library.py:96-98)
- Semaphore-controlled Firecrawl requests
- Prevents rate limiting

### Report Generation

**`generate_pdf_report(all_events, filename)`** (library.py:745-799)
- Uses PyLaTeX for PDF generation
- Groups events by date
- Includes all event details

**`generate_ics_file(all_events, filename)`** (library.py:802-896)
- Creates iCalendar format
- Sets proper timezones
- Includes event URLs and descriptions
- Generates stable UIDs for deduplication

---

## Data Flow

```
┌─────────────────────────────────────────────────┐
│  library_all_events.py main()                   │
│                                                 │
│  1. Parse CLI args & env vars                  │
│  2. Launch async fetchers concurrently         │
│  3. Aggregate results                          │
│  4. Deduplicate events                         │
│  5. Parse & sort by date/time                  │
│  6. Generate outputs (CSV/PDF/ICS)             │
└─────────────────────────────────────────────────┘
                    │
                    ├─→ CSV: all_library_events_YYYYMMDD.csv
                    ├─→ PDF: all_library_events_YYYYMMDD.pdf
                    └─→ ICS: all_library_events_YYYYMMDD.ics

┌─────────────────────────────────────────────────┐
│  library_web_gui.py                             │
│                                                 │
│  - Loads latest CSV from DATA_DIR               │
│  - Serves web interface                         │
│  - Provides filtering & export APIs             │
│  - Can trigger new scrapes                      │
└─────────────────────────────────────────────────┘
```

---

## Deployment

### Render.com (Production)

Configuration in `render.yaml`:

- **Build**: `pip install -r requirements.txt`
- **Start**: Run scraper then start gunicorn
- **Cron**: Daily refresh at 6 AM (0 6 * * *)
- **Storage**: 1GB persistent disk at `/opt/render/project/src/data`
- **Workers**: 2 gunicorn workers, 180s timeout

### Heroku (Alternative)

Configuration in `Procfile`:
```
web: gunicorn library_web_gui:app --bind 0.0.0.0:$PORT --workers 2 --timeout 180
```

### Local Development

```bash
# Setup environment
python -m venv library_env
source library_env/bin/activate  # or library_env\Scripts\activate on Windows
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your FIRECRAWL_API_KEY

# Run scraper
python library_all_events.py

# Run web interface
python library_web_gui.py
# Visit http://localhost:5000

# Run desktop GUI
python library_gui.py
```

---

## Git Workflow

### Branch Naming Convention

Based on git status, the repository uses:
- `main` - Main branch (default branch appears to be unset)
- `feature/*` - Feature branches (e.g., `feature/add-chicago-parks-events`)
- `claude/*` - AI-assisted development branches (e.g., `claude/add-wilmette-park-scraper-...`)

### Commit Message Style

From recent history:
- Use descriptive, action-oriented messages
- Examples:
  - "Add Wilmette Park District events scraper"
  - "Enhance library web GUI with improved Chicago Parks integration"
  - "Fix event count display to update dynamically"

### Pull Request Workflow

- Feature branches merged via PRs
- Some PRs include multiple related commits
- Occasionally features are reverted (e.g., PR #8 reverted PR #7)

---

## Common Tasks for AI Assistants

### 1. Adding a New Library

**Files to modify**:
- `library_all_events.py` - Add fetcher function and include in `main()`

**Steps**:
1. Identify the library's event system (Bibliocommons/LibNet/Custom)
2. Add fetcher function following existing patterns
3. Add to tasks list in `main()` (around line 986)
4. Test with limited date range first
5. Verify event schema compliance
6. Check for duplicates in output

**Example locations**:
- Bibliocommons: Lines 365-400
- LibNet: Lines 402-608
- Custom (Firecrawl): Lines 148-234, 241-339, 616-741

### 2. Fixing Scraper Issues

**Common problems**:
- **Rate limiting**: Adjust `FIRECRAWL_CONCURRENCY` or add delays
- **Parse errors**: Check markdown structure changes on library site
- **Missing events**: Verify date filtering and age group logic
- **Duplicate events**: Check deduplication logic (lines 1001-1006)

**Debugging**:
- Check logs: `library_events.log`
- Enable debug logging: Change `level=logging.INFO` to `level=logging.DEBUG`
- Test individual fetchers by calling them directly

### 3. Modifying Filters

**Age group filtering** (LibNet only):
- Global config: `DEFAULT_LIBNET_AGES` (line 44)
- Per-library logic: Lines 504-577 in `fetch_libnet_events()`
- Uses regex patterns to match various age formats

**Date range**:
- Default window: `DEFAULT_DAYS_TO_FETCH = 31` (line 42)
- Override via CLI: `--start-date`, `--days`, `--start-offset-days`
- Computed in: `compute_date_window()` (lines 900-971)

### 4. Adding Export Formats

**Current exports**:
- CSV: pandas DataFrame (lines 1066-1072)
- PDF: PyLaTeX (function at lines 745-799)
- ICS: ics library (function at lines 802-896)

**To add new format**:
1. Create generator function accepting `List[Dict[str, Any]]`
2. Follow pattern of existing generators
3. Add to `main()` around line 1062
4. Consider adding to web GUI export routes

### 5. UI Modifications

**Web GUI**:
- Template: `templates/index.html`
- Backend: `library_web_gui.py`
- Filtering logic: `filter_events()` function (lines 84-233)

**Desktop GUI**:
- File: `library_gui.py`
- Uses tkinter widgets
- Less actively maintained than web interface

---

## Important Constraints & Considerations

### API Rate Limits

- **Firecrawl**: Controlled via `FIRECRAWL_SEM` semaphore (default: 1 concurrent)
- Rate limit handling in `retry_with_backoff()` (lines 80-88)
- 429 responses trigger extended backoff

### Date/Time Parsing

- **Timezone aware**: Uses `zoneinfo.ZoneInfo` (default: America/Chicago)
- **Multiple formats supported**: See `parse_time_to_sortable()` and date parsing in `main()`
- **All Day events**: Handled as `datetime.min.time()`

### Event Deduplication

- Uses tuple of: `(Library, Title, Date, Time)` as unique identifier
- Implemented at lines 1001-1006
- Important for libraries that may list same event multiple times

### Text Encoding

- All text cleaned to ASCII via `clean_text()` (line 107)
- Removes zero-width spaces, markdown, extra whitespace
- LaTeX escaping for PDF generation

### Error Recovery

- Individual fetcher failures don't crash entire scrape
- `asyncio.gather(..., return_exceptions=True)` (line 996)
- Failed fetchers return empty list `[]`
- All errors logged for debugging

---

## Testing Strategy

### Unit Testing

Currently no formal test suite. To add tests:
```python
# Example test structure
import pytest
from library_all_events import clean_text, parse_time_to_sortable

def test_clean_text():
    assert clean_text("  Hello   World  ") == "Hello World"
    assert clean_text("[Link](url)") == ""

def test_parse_time():
    from datetime import time
    assert parse_time_to_sortable("2:30 PM") == time(14, 30)
    assert parse_time_to_sortable("All Day") == time.min
```

### Integration Testing

Manually test with limited date range:
```bash
python library_all_events.py --days 3 --start-date 2025-12-20
```

Verify:
- All libraries return events
- No crashes in logs
- Output files generated correctly
- Events are properly deduplicated

---

## Logging & Monitoring

### Log Configuration

- **Location**: `library_events.log` (rotating, 2MB max, 3 backups)
- **Level**: INFO (change to DEBUG for troubleshooting)
- **Format**: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

### Key Log Messages

- `"Fetching [Library] events..."` - Scraper start
- `"Found X events for [Library]"` - Scraper success
- `"Filtered out X events..."` - Age filtering applied
- `"Total events found: X"` - Aggregation complete
- `"Total events after de-duplication: X"` - Final count

### Monitoring Production

On Render.com:
- Check cron job logs for daily runs
- Monitor disk usage (1GB limit)
- Watch for API key issues (Firecrawl)
- Check gunicorn worker health

---

## Dependencies

From `requirements.txt`:

```
aiohttp>=3.8.0          # Async HTTP client
beautifulsoup4>=4.12.2  # HTML parsing (Park District scraper)
requests>=2.28.0        # HTTP client
pandas>=1.5.0           # Data manipulation
firecrawl-py>=0.0.16    # Web scraping API
ics>=0.7.2              # iCalendar format
pylatex>=1.4.1          # PDF generation
python-dotenv>=0.20.0   # Environment variables
Flask>=3.0.0            # Web framework
gunicorn>=21.2.0        # WSGI server
reportlab>=4.1.0        # PDF generation (web GUI)
```

### Dependency Notes

- **firecrawl-py**: Requires API key, main scraping engine
- **pylatex**: Requires LaTeX installation for PDF generation
- **reportlab**: Alternative PDF generation (used in web GUI)
- **beautifulsoup4**: Added for Park District integration

---

## Security Considerations

### API Keys

- Firecrawl API key in environment (never commit to git)
- `.env` in `.gitignore` (line 4)
- Default key in code (line 37) should be replaced in production

### Input Validation

- User input in web GUI is filtered but not extensively sanitized
- Search terms used in string matching (potential XSS in future HTML display)
- File paths constructed from user input (DATA_DIR should be validated)

### Recommendations for AI Assistants

1. Never commit `.env` files
2. Rotate API keys if exposed
3. Sanitize user input before display in HTML
4. Validate file paths in web GUI upload features
5. Use environment variables for all secrets

---

## Troubleshooting Guide

### Issue: Scraper returns 0 events

**Possible causes**:
1. Library website structure changed
2. API key invalid/expired
3. Network connectivity issues
4. Date range has no events

**Debug steps**:
```bash
# Enable debug logging
# Edit library_all_events.py, change logging.INFO to logging.DEBUG

# Test single library
python library_all_events.py --days 1

# Check logs
tail -f library_events.log
```

### Issue: Rate limiting / 429 errors

**Solution**:
- Reduce `FIRECRAWL_CONCURRENCY` (default: 1)
- Increase `RETRY_DELAY` (default: 1s)
- Add delays between library fetches

### Issue: PDF generation fails

**Cause**: Missing LaTeX installation

**Solution**:
```bash
# Ubuntu/Debian
sudo apt-get install texlive-latex-base texlive-fonts-recommended

# macOS
brew install basictex
```

### Issue: Wrong timezone for events

**Solution**: Set `TIMEZONE` environment variable
```bash
export TIMEZONE=America/New_York
python library_all_events.py
```

### Issue: Duplicate events in output

**Debug**: Check deduplication logic at lines 1001-1006
- Verify event schema has consistent Library/Title/Date/Time
- Some libraries may use different formatting

---

## Future Enhancement Ideas

Based on codebase analysis, potential improvements:

1. **Testing**: Add pytest suite for core functions
2. **Documentation**: Add inline docstrings to all functions
3. **Monitoring**: Add Sentry or similar for error tracking
4. **Performance**: Cache Firecrawl results to reduce API calls
5. **Features**:
   - Email notifications for new events
   - Event favoriting/bookmarking
   - Mobile-responsive web design improvements
   - RSS feed generation
   - Integration with Google Calendar
6. **Code Quality**:
   - Type hints throughout
   - Separate configuration module
   - Extract library-specific logic to plugins
   - Add CI/CD pipeline

---

## Quick Reference

### File Locations
- Main scraper: `library_all_events.py`
- Web interface: `library_web_gui.py`
- Desktop GUI: `library_gui.py`
- Configuration: `.env` (create from `.env.example`)
- Logs: `library_events.log`
- Output: `all_library_events_YYYYMMDD.*`

### Important Line Numbers (library_all_events.py)
- Configuration: Lines 36-61
- Helper functions: Lines 63-139
- Lincolnwood fetcher: Lines 143-234
- Morton Grove fetcher: Lines 236-339
- Bibliocommons fetcher: Lines 341-400
- LibNet fetcher: Lines 402-608
- Skokie fetcher: Lines 610-741
- PDF generator: Lines 745-799
- ICS generator: Lines 802-896
- Main execution: Lines 973-1076

### Key Functions to Understand
1. `retry_with_backoff()` - Error handling pattern
2. `clean_text()` - Text normalization
3. `fetch_libnet_events()` - Complex API interaction with filtering
4. `filter_events()` (web_gui) - Multi-faceted event filtering
5. `main()` - Orchestration and data flow

---

## Appendix: Event Schema Details

### Required Fields
- `Library` (str): Source library name
- `Title` (str): Event title
- `Date` (str): Date in parseable format
- `Time` (str): Time or "All Day"
- `Location` (str): Specific venue
- `Age Group` (str): Target audience
- `Description` (str): Event details
- `Link` (str): URL or "N/A"

### Optional Fields
- `Program Type` (str): Category (currently "Not found" for most)

### Field Constraints
- All text fields cleaned via `clean_text()`
- Dates must be parseable by datetime formats (lines 1028-1041)
- Links should be full URLs (some fetchers prepend base URL)

### Deduplication Key
`(Library, Title, Date, Time)` tuple must be unique

---

## Contact & Contribution

This documentation is intended for AI assistants working with this codebase. When making changes:

1. **Maintain backward compatibility** with existing event schema
2. **Test thoroughly** before deploying
3. **Update this documentation** when adding major features
4. **Follow existing code patterns** for consistency
5. **Log extensively** for troubleshooting

---

**End of CLAUDE.md**
