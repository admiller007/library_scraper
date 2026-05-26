# Vercel + Supabase Migration Design

**Date:** 2026-05-25
**Status:** Approved, ready for implementation
**Author:** Aaron Miller (with Claude)

## Goal

Move the library event scraper site from Render-hosted Flask + CSV files to a modern stack: Python scraper on GitHub Actions, events stored in Supabase Postgres, frontend rebuilt in Next.js on Vercel. Site is publicly viewable; scraping is private (GitHub Actions only).

## Architecture

```
GitHub Actions (daily 12:00 UTC + manual dispatch)
  -> library_all_events.py (unchanged)
  -> scripts/scrape_to_supabase.py (new adapter)
  -> Supabase REST API (UPSERT)
                                |
                                v
                       Supabase Postgres
                       - events table
                       - scrape_runs table
                       - RLS: anon SELECT only
                                |
                                v
                    Next.js on Vercel
                    - Server Components read from Supabase
                    - Cache Components with updateTag('events')
                    - Client-side filtering/search
                    - API routes for ICS + PDF export
```

Three pieces, decoupled:
1. Scraper stays Python. GitHub Actions runs the existing scraper unchanged; a thin adapter writes results to Supabase instead of CSV.
2. Supabase is the source of truth. Replaces the CSV. Lets the site query, filter, and add future features without touching the scraper.
3. Site is read-only. Vercel never runs the scraper. No "manual refresh" button on the public site -- that's a GitHub Actions `workflow_dispatch` trigger only the repo owner can run.

## Data Model

```sql
create table scrape_runs (
  id            uuid primary key default gen_random_uuid(),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  status        text not null,          -- 'running' | 'success' | 'failed'
  event_count   int,
  error_message text
);

create table events (
  id              uuid primary key default gen_random_uuid(),
  -- Natural key (matches current dedup tuple)
  library         text not null,
  title           text not null,
  event_date      date not null,
  event_time      text not null,        -- "2:30 PM" or "All Day"
  -- Display fields
  location        text,
  age_group       text,
  program_type    text,
  description     text,
  link            text,
  -- Derived sortable timestamp (event_date + event_time in America/Chicago)
  start_at        timestamptz,
  -- Bookkeeping
  scrape_run_id   uuid references scrape_runs(id),
  first_seen_at   timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  unique (library, title, event_date, event_time)
);

create index events_start_at_idx on events (start_at);
create index events_library_idx on events (library);
create index events_age_group_idx on events (age_group);
create index events_search_idx on events using gin (
  to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,''))
);

alter table events enable row level security;
create policy "events readable" on events for select to anon using (true);
-- service_role bypasses RLS; no INSERT/UPDATE policy needed for anon
```

Rationale:
- Natural key matches the existing in-memory dedup tuple `(Library, Title, Date, Time)`. Scraper can `UPSERT` cleanly.
- `start_at` computed once at insert time so the site sorts/filters on a real timestamp.
- `first_seen_at` / `last_seen_at` enable "new this week" badges and stale-event detection.
- `scrape_runs` powers a "last refreshed" indicator and aids debugging.

## Scraper: GitHub Actions

**`.github/workflows/scrape.yml`**

```yaml
name: Daily scrape
on:
  schedule:
    - cron: '0 12 * * *'   # 6 AM Central (CST) = 12:00 UTC; slides 1h in DST
  workflow_dispatch:

concurrency:
  group: scrape
  cancel-in-progress: false

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - name: Run scraper
        env:
          FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          TIMEZONE: America/Chicago
        run: python scripts/scrape_to_supabase.py
      - name: Revalidate Vercel cache
        env:
          REVALIDATE_URL: ${{ secrets.VERCEL_REVALIDATE_URL }}
          REVALIDATE_SECRET: ${{ secrets.REVALIDATE_SECRET }}
        run: curl -fsS -X POST "$REVALIDATE_URL" -H "Authorization: Bearer $REVALIDATE_SECRET"
```

**`scripts/scrape_to_supabase.py`** -- thin adapter that:
1. Creates a `scrape_runs` row with `status='running'`.
2. Calls existing scraper logic and collects the in-memory `all_events` list (instead of writing CSV).
3. Computes `start_at` per event from `event_date` + `event_time` in `America/Chicago`.
4. Batches `UPSERT` calls to `POST /rest/v1/events` with header `Prefer: resolution=merge-duplicates`, keyed on the natural unique constraint. Sets `last_seen_at = now()` on existing rows.
5. Marks the `scrape_runs` row as `success` (with `event_count`) or `failed` (with `error_message`).

**Minimal change to `library_all_events.py`:** factor `main()` so it can return `all_events` instead of writing files. Individual library fetchers stay untouched.

## Frontend: Next.js on Vercel

Stack: Next.js 16 App Router, TypeScript, Tailwind CSS, `@supabase/supabase-js`.

```
web/
  app/
    page.tsx                  # Main events view (Server Component)
    layout.tsx                # Shell + last-refreshed indicator
    components/
      EventList.tsx           # Client: receives events, handles filtering
      FilterSidebar.tsx       # Library + date + age + search controls
      EventCard.tsx           # Single event display
    api/
      export/ics/route.ts     # GET -> iCalendar file
      export/pdf/route.ts     # GET -> PDF
      revalidate/route.ts     # POST (Bearer-protected) -> updateTag('events')
  lib/
    supabase.ts               # Anon client (read-only)
```

Data fetching:
- `page.tsx` is a Server Component running a single Supabase query: events in the next 31 days, ordered by `start_at`.
- Wrapped with `'use cache'` + `cacheLife('hours')` + `cacheTag('events')`. After each scrape the workflow hits `/api/revalidate` which calls `updateTag('events')`.

Filtering UX:
- Server returns the full 31-day window once. All filtering (library toggle, date slider, search, age group) is client-side over that dataset -- fast, no extra queries.
- Search uses `includes()` over title + description for now. The Postgres FTS index is reserved for server-side search if the dataset grows past ~10k events.

Exports:
- `/api/export/ics` -- uses `ics` npm package, mirrors current ICS logic.
- `/api/export/pdf` -- uses `@react-pdf/renderer` (Vercel-compatible; replaces PyLaTeX/reportlab).

"Last refreshed" indicator: layout fetches the latest `scrape_runs` row.

## Secrets

| Where | Secret | Purpose |
|---|---|---|
| GitHub repo | `FIRECRAWL_API_KEY` | Scraper |
| GitHub repo | `SUPABASE_URL` | e.g. `https://xxx.supabase.co` |
| GitHub repo | `SUPABASE_SERVICE_ROLE_KEY` | Server-side writes (bypasses RLS) |
| GitHub repo | `VERCEL_REVALIDATE_URL` | Post-scrape cache bust |
| GitHub repo | `REVALIDATE_SECRET` | Gates the revalidate endpoint |
| Vercel | `NEXT_PUBLIC_SUPABASE_URL` | Public, anon reads |
| Vercel | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Public, anon reads (RLS read-only) |
| Vercel | `REVALIDATE_SECRET` | Same value as above |

## Deployment Order

1. Create Supabase project, run schema SQL.
2. Create `web/` Next.js app in repo.
3. Add `scripts/scrape_to_supabase.py` + GitHub Actions workflow.
4. Connect Vercel to the repo, root = `web/`.
5. Trigger workflow manually once to seed Supabase, verify site renders.
6. Enable the daily schedule.

## What Gets Retired

- `library_web_gui.py` -- replaced by Next.js
- `templates/index.html` -- replaced by React components
- `Procfile`, `render.yaml` -- Render hosting no longer used
- CSV/PDF/ICS file outputs from `library_all_events.py` -- Supabase is the store; exports are generated on-demand by Next.js API routes

## What Stays

- `library_all_events.py` and every individual library fetcher -- untouched
- `requirements.txt`
- `library_gui.py` (desktop tkinter) -- can stay; not part of this migration

## Risks

- **DST drift.** GitHub Actions cron is UTC; 6 AM Central slides by an hour twice a year. Acceptable for now; can switch to two cron entries (11:00 + 12:00 UTC) and rely on dedup if exact timing matters.
- **Supabase free tier limits.** 500 MB database, 2 GB egress. With ~1000 events/month this is fine for years.
- **Firecrawl costs.** Unchanged, but visibility moves from Render dashboard to GitHub Actions logs.

## Out of Scope (v1)

- Authentication / user accounts
- Event favoriting or notifications
- RSS feed
- Mobile app
- Replacing the desktop tkinter GUI
