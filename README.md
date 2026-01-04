# Library Event Scraper

A comprehensive event aggregation system that scrapes and consolidates children's programming events from multiple Chicago-area libraries into easy-to-use formats (web interface, CSV, PDF, and iCalendar).

## Features

- **Multi-Library Support**: Aggregates events from 8+ Chicago-area libraries including:
  - Lincolnwood Library
  - Morton Grove Public Library
  - Evanston Public Library
  - Chicago Public Library (Edgebrook, Budlong Woods branches)
  - Wilmette Library
  - Skokie Public Library
  - Niles Library
  - Chicago Park District

- **Smart Filtering**: Filter events by age group (K-2, 3-5 grades), date range, and search terms

- **Multiple Export Formats**:
  - CSV for spreadsheet applications
  - PDF for printing and sharing
  - ICS (iCalendar) for importing into Google Calendar, Outlook, etc.

- **Dual Interface Options**:
  - Modern web interface with real-time filtering
  - Desktop GUI (Tkinter) for offline use

- **Automated Updates**: Configurable scheduling for automatic event refreshes

## Quick Start

### Prerequisites

- Python 3.8 or higher
- [Firecrawl API key](https://firecrawl.dev) (free tier available)
- LaTeX installation (for PDF generation - optional)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/admiller007/library_scraper.git
cd library_scraper
```

2. Create and activate a virtual environment:
```bash
python -m venv library_env
source library_env/bin/activate  # On Windows: library_env\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env and add your FIRECRAWL_API_KEY
```

### Basic Usage

**Run the scraper** to fetch latest events:
```bash
python library_all_events.py
```

This generates three files with today's date:
- `all_library_events_YYYYMMDD.csv` - Spreadsheet format
- `all_library_events_YYYYMMDD.pdf` - Printable report
- `all_library_events_YYYYMMDD.ics` - Calendar import file

**Launch the web interface**:
```bash
python library_web_gui.py
# Visit http://localhost:5000
```

**Launch the desktop GUI**:
```bash
python library_gui.py
```

## Configuration

### Environment Variables

Configure via `.env` file:

```bash
# Required: Get your API key from https://firecrawl.dev
FIRECRAWL_API_KEY=your_api_key_here

# Optional: Timezone for event parsing (default: America/Chicago)
TIMEZONE=America/Chicago

# Optional: Directory for generated files (default: ./data)
DATA_DIR=./data
```

### Command Line Options

Customize the scraping behavior:

```bash
# Fetch events for next 7 days
python library_all_events.py --days 7

# Fetch events starting from specific date
python library_all_events.py --start-date 2026-01-15

# Start fetching 3 days from now, for 14 days
python library_all_events.py --start-offset-days 3 --days 14

# Customize age group filtering for LibNet libraries
python library_all_events.py --libnet-ages "Grades K-2,Grades 3-5"
```

## Web Interface Features

The Flask-based web interface provides:

- **Real-time Filtering**: Filter by library, date range, and search terms
- **Advanced Search**:
  - Multiple search modes (any/all/exact/fuzzy)
  - Multi-field search (title, description, location, age group)
- **Export Options**: Download filtered results as ICS or PDF
- **Live Scraping**: Trigger new event scrapes with progress tracking
- **Responsive Design**: Works on desktop and mobile devices

## Project Structure

```
library_scraper/
├── library_all_events.py   # Main scraper engine
├── library_web_gui.py      # Flask web interface
├── library_gui.py          # Tkinter desktop GUI
├── library.py              # Legacy scraper (deprecated)
├── skokie_test.py          # Scraper testing utility
├── templates/
│   └── index.html          # Web interface template
├── requirements.txt        # Python dependencies
├── .env.example            # Environment configuration template
├── Procfile                # Heroku deployment config
├── render.yaml             # Render.com deployment config
└── CLAUDE.md               # AI assistant documentation
```

## Deployment

### Render.com (Recommended)

The project includes a `render.yaml` configuration for easy deployment:

1. Fork this repository
2. Connect your GitHub account to [Render.com](https://render.com)
3. Create a new Web Service from the repository
4. Add your `FIRECRAWL_API_KEY` in the environment variables
5. Deploy!

Features:
- Automatic daily refresh at 6 AM via cron job
- 1GB persistent storage for event data
- Free tier available

### Heroku

Deploy to Heroku using the included `Procfile`:

```bash
heroku create your-app-name
heroku config:set FIRECRAWL_API_KEY=your_key_here
git push heroku main
```

### Local Development Server

For development and testing:

```bash
# Using Flask development server (not for production)
python library_web_gui.py

# Using Gunicorn (production-ready)
gunicorn library_web_gui:app --bind 0.0.0.0:8000 --workers 2 --timeout 180
```

## How It Works

1. **Scraping**: The system uses multiple strategies to fetch events:
   - **Firecrawl API**: For custom library websites (Lincolnwood, Morton Grove, Skokie)
   - **Bibliocommons API**: For libraries using Bibliocommons platform (Evanston, CPL)
   - **LibNet API**: For libraries using LibNet platform (Wilmette, Niles)

2. **Processing**: Events are:
   - Normalized to a standard schema
   - Filtered by age group and date range
   - Deduplicated to remove duplicate listings
   - Sorted by date and time

3. **Output**: Events are exported in multiple formats for different use cases

## API Rate Limiting

The scraper implements robust rate limiting and retry logic:
- Concurrent request limiting via semaphores
- Exponential backoff for failed requests
- Graceful handling of API rate limits (429 errors)

## Troubleshooting

### No events found
- Check that your `FIRECRAWL_API_KEY` is valid
- Verify the date range includes upcoming events
- Check `library_all_events.log` for detailed error messages

### PDF generation fails
Install LaTeX:
```bash
# Ubuntu/Debian
sudo apt-get install texlive-latex-base texlive-fonts-recommended

# macOS
brew install basictex
```

### Rate limiting errors
Reduce concurrent requests by editing `library_all_events.py`:
```python
FIRECRAWL_CONCURRENCY = 1  # Lower if getting 429 errors
```

## Contributing

Contributions are welcome! Areas for improvement:

- Adding support for additional libraries
- Improving event parsing accuracy
- Enhancing the web interface
- Adding automated tests
- Improving documentation

Please submit issues and pull requests through GitHub.

## Development

### Adding a New Library

1. Identify the library's event system type (Bibliocommons/LibNet/Custom)
2. Add a new fetcher function in `library_all_events.py`
3. Follow the existing patterns for your library type
4. Add the fetcher to the tasks list in `main()`
5. Test with a limited date range first

See `CLAUDE.md` for detailed development documentation.

### Running Tests

```bash
# Test individual library scraper
python skokie_test.py

# Test with limited date range
python library_all_events.py --days 3
```

## License

This project is open source. Please check the repository for license details.

## Support

- **Issues**: Report bugs and request features via [GitHub Issues](https://github.com/admiller007/library_scraper/issues)
- **Documentation**: See `CLAUDE.md` for comprehensive technical documentation

## Acknowledgments

Built to help parents and educators discover enriching children's programming at Chicago-area libraries.

---

**Project Status**: Active maintenance
**Last Updated**: January 2026
