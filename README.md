# Library Event Scraper

Aggregates children's programming events from Chicago-area libraries into one daily-refreshed site. Python scraper writes to Supabase; Next.js frontend on Vercel reads from it; GitHub Actions runs the scraper on a daily cron.

## Architecture

```
GitHub Actions (daily cron + manual dispatch)
  └─ Python scraper  ──UPSERT──▶  Supabase Postgres  ◀──SELECT──  Next.js on Vercel
                                                                       │
                                                                       ▼
                                                                  Users
```

Three independently deployed pieces:

- **Python scraper** (`library_all_events.py` + `scripts/scrape_to_supabase.py`) — runs in GitHub Actions
- **Supabase** — Postgres + RLS (`supabase/schema.sql` provisions everything)
- **Next.js frontend** (`web/`) — Server Components reading from Supabase with Cache Components

Detailed setup: see [`docs/deployment.md`](docs/deployment.md).

## Libraries Covered

Library systems aggregated:
- Lincolnwood, Morton Grove, Skokie, Glencoe, Wilmette, Northbrook, Niles, Mount Prospect, Schaumburg, Des Plaines, Winnetka/Northfield (WNPLD)
- Evanston + Chicago Public Library branches (Edgebrook, Budlong Woods, Albany Park, Northtown, Rogers Park) + Glenview (Bibliocommons)
- Chicago Park District, Skokie Park District, Forest Preserves of Cook County

## Features

- Daily-refreshed event listings, filterable by library, age group, date range, and free text
- ICS calendar export (`/api/export/ics`) and PDF export (`/api/export/pdf`)
- "Last refreshed" indicator pulled from the most recent `scrape_runs` row
- All viewing is public; scraping is private (GitHub Actions workflow dispatch only)

## Local Development

### Scraper

```bash
python -m venv library_env
source library_env/bin/activate
pip install -r requirements.txt

# Test a single library
python library_all_events.py --days 1

# Run end-to-end against your Supabase project
export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... FIRECRAWL_API_KEY=...
python scripts/scrape_to_supabase.py --days 3
```

### Frontend

```bash
cd web
cp .env.local.example .env.local   # fill in NEXT_PUBLIC_* + REVALIDATE_SECRET
npm install
npm run dev   # http://localhost:3000
```

## Command-line Options (scraper)

```bash
python library_all_events.py --start-date 2026-01-15 --days 14
python library_all_events.py --start-offset-days 3 --days 14
python library_all_events.py --libnet-ages "Grades K-2,Grades 3-5"
```

## Project Structure

```
LibraryScrapper/
├── library_all_events.py        # Scraper engine (all library fetchers)
├── library_gui.py               # Tkinter desktop GUI (reads local CSV exports)
├── scripts/
│   └── scrape_to_supabase.py    # Runs the scraper, UPSERTs to Supabase
├── supabase/
│   └── schema.sql               # Provisioning SQL
├── .github/workflows/
│   └── scrape.yml               # Daily scrape on GitHub Actions
├── web/                         # Next.js 16 frontend
│   ├── app/                     # App Router routes + components
│   └── lib/                     # Supabase client + cached fetchers
├── docs/
│   ├── deployment.md            # Step-by-step deploy guide
│   └── plans/                   # Design + implementation plans
└── requirements.txt
```

## Deployment

See [`docs/deployment.md`](docs/deployment.md) for the full walkthrough (Supabase → Vercel → GitHub Actions). Required secrets:

| Where | Names |
| --- | --- |
| Vercel | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `REVALIDATE_SECRET` |
| GitHub Actions | `FIRECRAWL_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `VERCEL_REVALIDATE_URL`, `REVALIDATE_SECRET` |

## How It Works

1. **Scrape.** Each library fetcher returns events in a common schema. `_gather_and_filter_events()` dedupes by `(Library, Title, Date, Time)`, filters to the requested window, and sorts.
2. **Persist.** `scrape_to_supabase.py` opens a `scrape_runs` row, UPSERTs events against the natural unique constraint, then marks the run `success` or `failed`.
3. **Invalidate.** On success the script POSTs `/api/revalidate` (Bearer-protected) which calls `revalidateTag('events', 'max')`, so the next page view fetches fresh data while serving stale content in the meantime.
4. **Render.** Next.js Server Components fetch events with `'use cache'` + `cacheTag('events')`; client-side React handles filtering.

## Adding a New Library

1. Identify the system (Bibliocommons / LibNet / custom Firecrawl).
2. Add a fetcher function in `library_all_events.py` returning the standard event schema.
3. Append it to the `sources` list in `_gather_and_filter_events()`.
4. Test with `python library_all_events.py --days 1` and verify locally with the Supabase adapter.

## Troubleshooting

- **Site shows "Refresh pending."** No `scrape_runs` rows yet — trigger the GitHub Action manually.
- **Events appear in Supabase but not the site.** Cache wasn't busted. Confirm `VERCEL_REVALIDATE_URL` and `REVALIDATE_SECRET` are set on both sides and match.
- **Action fails with 401.** Wrong `SUPABASE_SERVICE_ROLE_KEY` — service_role bypasses RLS so an auth error there means the key itself is wrong.
- **Rate limiting (429) during scrape.** Lower `FIRECRAWL_CONCURRENCY` in `library_all_events.py`.

## License

Open source. See repository for details.
