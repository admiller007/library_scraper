# CLAUDE.md — Library Event Scraper

**Last Updated:** 2026-05-26
**Primary languages:** Python 3.11 (scraper), TypeScript / Next.js 16 (frontend)

## Architecture (current)

```
GitHub Actions (.github/workflows/scrape.yml)
  └─ python scripts/scrape_to_supabase.py
        ├─ calls collect_all_events() from library_all_events.py
        └─ UPSERTs into Supabase (events, scrape_runs)
                                │
                                ▼
                       Supabase Postgres
                                │
                                ▼
                       Next.js on Vercel (web/)
                       - lib/events.ts: cached server fetchers
                       - app/page.tsx: server component renders events
                       - app/components/EventList.tsx: client filtering
                       - app/api/revalidate: bumps cacheTag('events')
                       - app/api/export/{ics,pdf}: format exports
```

There is **no Flask server** anymore and **no Render/Heroku deployment**. CSV/PDF/ICS files are not produced by the scraper by default; the Next.js routes render them on demand from Supabase.

## Repository structure

```
LibraryScrapper/
├── library_all_events.py      # Scraper (all library fetchers + collect_all_events)
├── library_gui.py             # Tkinter desktop GUI — reads local CSV exports only
├── library.py                 # Legacy module retained for any leftover imports
├── scripts/
│   └── scrape_to_supabase.py  # Adapter: runs scraper -> Supabase UPSERT
├── supabase/
│   └── schema.sql             # Provisioning SQL (events + scrape_runs + RLS)
├── .github/workflows/
│   └── scrape.yml             # Daily cron @ 12:00 UTC + manual dispatch
├── web/                       # Next.js 16 frontend (Vercel)
│   ├── lib/
│   │   ├── supabase.ts        # Lazy client with anon key (read-only)
│   │   └── events.ts          # Cached fetchers: getUpcomingEvents, getLatestScrapeRun
│   ├── app/
│   │   ├── layout.tsx         # Header w/ ICS/PDF links + last-refreshed
│   │   ├── page.tsx           # Server Component -> <EventList>
│   │   ├── components/        # EventCard (server), EventList (client)
│   │   └── api/
│   │       ├── revalidate/    # Bearer-protected revalidateTag('events', 'max')
│   │       └── export/{ics,pdf}/
│   └── next.config.ts         # cacheComponents: true
├── docs/
│   ├── deployment.md          # User-facing setup guide
│   └── plans/                 # Design + implementation plans
└── requirements.txt           # Python deps (no Flask/gunicorn anymore)
```

## Core components

### Scraper — `library_all_events.py`

- All library fetchers return `List[Dict[str, Any]]` with the schema: `Library, Title, Date, Time, Location, Age Group, Program Type, Description, Link`.
- **`_gather_and_filter_events(start_date_str, days)`** does the orchestration: runs all fetchers via `asyncio.gather`, deduplicates by `(Library, Title, Date, Time)`, parses dates, filters to the window, sorts.
- **`collect_all_events(start_date_str=None, days=None)`** is the public entry point used by the Supabase adapter. It initializes progress state and calls the gather helper.
- **`main()`** is the CLI entry point — calls `collect_all_events()` then writes CSV/PDF/ICS files locally. Used for ad-hoc local runs, not in production.

The progress-state JSON (`scrape_progress.json`) is still written but no longer consumed by anything; it can stay for now.

### Supabase adapter — `scripts/scrape_to_supabase.py`

- Creates a `scrape_runs` row with `status='running'`.
- Calls `collect_all_events()` and maps the result to Supabase rows via `_to_row()`. Computes `start_at = event_date + event_time` in `America/Chicago`.
- UPSERTs in batches of 200 against `on_conflict=library,title,event_date,event_time`.
- On success: marks the run `success`, POSTs to `$VERCEL_REVALIDATE_URL` with the Bearer secret.
- On failure: marks the run `failed` with the exception repr.

Required env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `FIRECRAWL_API_KEY`. Optional: `VERCEL_REVALIDATE_URL`, `REVALIDATE_SECRET`, `TIMEZONE`.

### Frontend — `web/`

- **Cache Components are enabled** (`cacheComponents: true`). All event reads use `'use cache'` + `cacheLife('hours' | 'minutes')` + `cacheTag('events')`.
- `getSupabase()` returns `null` if env vars are missing so builds without Supabase configured still succeed (degraded mode: empty list, "Refresh pending").
- Filtering is **client-side**. `getUpcomingEvents()` returns the full upcoming window in one request; `<EventList>` filters in memory.
- Cache invalidation: `/api/revalidate` calls `revalidateTag('events', 'max')` — note the second argument is required as of Next.js 16; the single-arg form is deprecated.

### Desktop GUI — `library_gui.py`

Unchanged. Still loads local CSV files. Use it if you have CSVs from a local scraper run; it does **not** read from Supabase.

## Event schema

```python
{
  "Library": str,          # Source name
  "Title": str,
  "Date": str,             # Parseable date string
  "Time": str,             # "HH:MM AM/PM" or "All Day"
  "Location": str,
  "Age Group": str,
  "Program Type": str,
  "Description": str,
  "Link": str,             # URL or "N/A"
}
```

Mapped to Supabase columns by `_to_row()` — note casing differences (`Library` → `library`, etc.).

**Dedup key:** `(library, title, event_date, event_time)` — enforced by the Postgres unique constraint and matched in the Python dedup pass.

## Common tasks

### Add a new library

1. Identify the system (Bibliocommons / LibNet / custom Firecrawl).
2. Add a fetcher in `library_all_events.py`.
3. Append `("<Label>", lambda: fetch_<library>_events(...))` to the `sources` list in `_gather_and_filter_events()`.
4. Test with `python library_all_events.py --days 1`.
5. Run `python scripts/scrape_to_supabase.py --days 1` against a dev Supabase project to verify upserts and types.

### Modify the frontend

- Server-side data fetching changes go in `web/lib/events.ts`.
- UI changes go in `web/app/page.tsx`, `web/app/layout.tsx`, or `web/app/components/`.
- After substantive edits run `cd web && npm run build` — it does TypeScript checks + cache-components validation.

### Add a new export format

Add a new route under `web/app/api/export/<format>/route.ts` that calls `getUpcomingEvents()` and returns the appropriate `Content-Type` + body. Add a link in `web/app/layout.tsx` so it's reachable.

### Schedule changes

GitHub Actions cron is in `.github/workflows/scrape.yml` — UTC. The daily 12:00 UTC slot equals 06:00 CST / 07:00 CDT.

## Important constraints

- **No Node.js dependencies in the scraper.** Stays pure Python so GitHub Actions can install via pip.
- **No Python in the frontend.** Stays pure Next.js so Vercel can build it cleanly.
- **`revalidateTag(tag, profile)` requires two args** in Next.js 16. Always pass `'max'` (or another defined profile).
- **`runtime = 'nodejs'`** route-segment export is not allowed when `cacheComponents` is true. Don't add it.
- **Cached functions can't access `cookies()`/`headers()`/`searchParams`.** Read those outside the cache scope and pass values as arguments.

## Deployment

See [`docs/deployment.md`](docs/deployment.md). Short version:

1. Run `supabase/schema.sql` in your Supabase project.
2. Connect Vercel to the repo, root = `web/`. Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `REVALIDATE_SECRET`.
3. Set GitHub Actions secrets: `FIRECRAWL_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `VERCEL_REVALIDATE_URL`, `REVALIDATE_SECRET`.
4. Trigger "Daily scrape" once manually to seed events.

## Testing

There is no formal test suite. Smoke tests:

- **Scraper:** `python library_all_events.py --days 1` should produce CSV/PDF/ICS without crashing.
- **Adapter helpers:** see the inline checks in the commit that introduced `scrape_to_supabase.py` (date parsers, row mapping).
- **Frontend:** `cd web && npm run build` covers TS + cache-component constraints. For runtime, `npm run dev` and hit `/`, `/api/export/ics`, `/api/export/pdf`, `/api/revalidate` (with Bearer).

## Plans + history

- Design: `docs/plans/2026-05-25-vercel-supabase-migration-design.md`
- Implementation plan: `docs/plans/2026-05-25-vercel-supabase-migration.md`

The implementation plan contains the original task breakdown; later sessions should follow new plans, not re-execute that one.
