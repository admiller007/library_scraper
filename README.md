# Library Events Scraper

A comprehensive event aggregator for Chicago-area libraries and parks, providing multiple interfaces to discover, filter, and export local events.

## Features

- **Multi-Source Scraping**: Aggregates events from 12+ library systems and park districts
- **Modern Web Interface**: Browse and filter events with an intuitive, responsive UI
- **Advanced Filtering**: Search by library, age group, date range, and keywords
- **Multiple Export Formats**: Export to CSV, PDF, and ICS (calendar) formats
- **Real-Time Progress Tracking**: Monitor scraping progress with live updates
- **Scheduled Refreshes**: Automatically update event data on a schedule
- **Desktop & Web GUI**: Choose between Flask web app or Tkinter desktop interface

## Supported Sources

### Libraries
- Lincolnwood Public Library
- Morton Grove Public Library
- Glencoe Public Library
- Evanston Public Library
- Chicago Public Library (Edgebrook & Budlong Woods)
- Wilmette Public Library
- Skokie Public Library
- Niles-Maine District Library

### Parks & Recreation
- Skokie Parks
- Chicago Park District
- Forest Preserves of Cook County

## Quick Start

### Prerequisites

- Python 3.10 or higher
- Firecrawl API key (get one at [firecrawl.dev](https://firecrawl.dev))

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/admiller007/library_scraper.git
   cd library_scraper
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env and add your Firecrawl API key
   ```

4. **Run the web application**
   ```bash
   python library_web_gui.py
   ```

   The web interface will be available at `http://localhost:5000`

## Configuration

### Environment Variables

Create a `.env` file with the following variables:

```bash
# Required: Get your API key from https://firecrawl.dev
FIRECRAWL_API_KEY=your_api_key_here

# Optional: Timezone for event parsing (default: America/Chicago)
TIMEZONE=America/Chicago

# Optional: Directory for generated files (default: ./data)
DATA_DIR=./data

# Optional: Concurrency settings
FIRECRAWL_CONCURRENCY=3
REQUESTS_CONCURRENCY=5
```

## Usage

### Web Interface (Recommended)

```bash
python library_web_gui.py
```

**Features:**
- Browse all events in a grid layout
- Filter by library, age group, and date range
- Search with multiple modes (any word, all words, exact phrase, fuzzy match)
- Export filtered events to ICS or PDF
- Trigger manual refresh of event data
- Monitor scraping progress in real-time

### Command Line

```bash
# Fetch all events and generate outputs
python library_all_events.py

# Specify custom date range
python library_all_events.py --days 30

# Generated files will be in the DATA_DIR:
# - library_events_YYYYMMDD.csv
# - library_events_YYYYMMDD.pdf
# - library_events_YYYYMMDD.ics
```

### Desktop GUI

```bash
python library_gui.py
```

## Deployment

### Deploy to Render

This project is configured for easy deployment to Render.com:

1. **Fork this repository**

2. **Create a new Web Service on Render**
   - Connect your GitHub repository
   - Render will automatically detect `render.yaml`

3. **Set environment variables**
   - Go to Environment tab in Render dashboard
   - Add `FIRECRAWL_API_KEY` with your API key
   - Other variables are optional (see `render.yaml` for defaults)

4. **Deploy**
   - Render will automatically deploy on push to main branch
   - Includes a daily cron job at 6am to refresh event data

### Deploy to Heroku

```bash
# Login to Heroku
heroku login

# Create app
heroku create your-app-name

# Set environment variables
heroku config:set FIRECRAWL_API_KEY=your_api_key_here

# Deploy
git push heroku main
```

## Project Structure

```
library_scraper/
├── library_all_events.py    # Main scraper with async fetching
├── library_web_gui.py       # Flask web application
├── library_gui.py           # Tkinter desktop GUI
├── library.py               # Legacy single-library scraper
├── skokie_test.py          # Debug utility for Skokie library
├── templates/
│   └── index.html          # Web UI template
├── requirements.txt         # Python dependencies
├── .env.example            # Environment configuration template
├── render.yaml             # Render.com deployment config
├── Procfile               # Heroku deployment config
└── README.md              # This file
```

## How It Works

1. **Async Scraping**: Uses `aiohttp` and `asyncio` to fetch events from all sources concurrently
2. **Web Scraping**: Combines Firecrawl API for JavaScript-heavy sites and BeautifulSoup for static HTML
3. **Data Processing**: Parses and normalizes events into a consistent format
4. **Age Group Detection**: Uses regex patterns to categorize events by age appropriateness
5. **Export Generation**: Produces CSV, PDF (via LaTeX/ReportLab), and ICS calendar files
6. **Progress Tracking**: Maintains state in JSON file for real-time progress updates

## Search Modes

The web interface supports multiple search modes:

- **Any Word**: Matches events containing any of the search terms
- **All Words**: Matches events containing all search terms (in any order)
- **Exact Phrase**: Matches the exact phrase as typed
- **Fuzzy Match**: Tolerates minor spelling differences (uses sequence matching)

Search can be applied to:
- Event titles
- Descriptions
- Library names
- Age groups

## API Endpoints

The web application exposes several JSON APIs:

- `GET /api/events` - Get filtered events
  - Query params: `library`, `type[]`, `search`, `start_date`, `end_date`, `date_filter`, `search_fields[]`, `search_mode`
- `GET /api/ics` - Export events to ICS calendar format
- `GET /api/pdf` - Export events to PDF
- `POST /api/refresh` - Trigger background scrape
- `GET /api/progress` - Get current scraping progress

## Troubleshooting

### "FIRECRAWL_API_KEY environment variable is required"

Make sure you've created a `.env` file with your API key:
```bash
cp .env.example .env
# Edit .env and add your key
```

### Events not loading in web interface

1. Check that the CSV file exists in `DATA_DIR`
2. Run the scraper manually: `python library_all_events.py`
3. Check logs in `DATA_DIR/library_all_events.log`

### Scraper timing out or failing

1. Reduce concurrency in `.env`:
   ```bash
   FIRECRAWL_CONCURRENCY=2
   REQUESTS_CONCURRENCY=3
   ```
2. Check your Firecrawl API quota
3. Some library websites may be temporarily down

## Development

### Adding a New Library Source

1. Add the library URL to the constants section in `library_all_events.py`
2. Create a new async fetch function:
   ```python
   async def fetch_newlibrary_events(client: AsyncFirecrawl, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
       # Implement scraping logic
       pass
   ```
3. Add to the main scraping function:
   ```python
   results.append(asyncio.create_task(fetch_newlibrary_events(firecrawl, session)))
   ```
4. Update the source list in `library_web_gui.py`

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov

# Run tests
pytest

# With coverage
pytest --cov=. --cov-report=html
```

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is open source and available under the MIT License.

## Acknowledgments

- Built with [Firecrawl](https://firecrawl.dev) for reliable web scraping
- Uses [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) for HTML parsing
- Web interface powered by [Flask](https://flask.palletsprojects.com/) and [Tailwind CSS](https://tailwindcss.com/)

## Support

For issues, questions, or suggestions:
- Open an issue on GitHub
- Check existing issues for solutions
- Review the troubleshooting section above

---

Made with ❤️ for Chicago-area families looking for library events
