# Vercel + Supabase Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Render-hosted Flask + CSV stack with a Next.js frontend on Vercel, events stored in Supabase Postgres, and the existing Python scraper run by GitHub Actions on a daily schedule.

**Architecture:** Three decoupled pieces. GitHub Actions runs `library_all_events.py` (unchanged scraper logic) and a thin adapter writes results to Supabase via REST UPSERT. Next.js Server Components read from Supabase with Cache Components for static-like performance; the scraper hits a Bearer-protected revalidate endpoint after each run.

**Tech Stack:** Python 3.11 (existing scraper), Supabase (Postgres + RLS), GitHub Actions, Next.js 16 App Router + TypeScript + Tailwind CSS, `@supabase/supabase-js`, `ics` (npm), `@react-pdf/renderer`.

**Design doc:** `docs/plans/2026-05-25-vercel-supabase-migration-design.md` (committed to `main`)

**Worktree:** `.worktrees/vercel-supabase` on branch `feature/vercel-supabase-migration`

---

## Phase 1: Supabase schema

### Task 1: Add Supabase schema SQL to the repo

**Files:**
- Create: `supabase/schema.sql`

**Step 1: Write the schema file**

```sql
-- supabase/schema.sql
-- Run this once in the Supabase SQL editor when provisioning the project.

create extension if not exists "pgcrypto";

create table if not exists scrape_runs (
  id            uuid primary key default gen_random_uuid(),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  status        text not null check (status in ('running','success','failed')),
  event_count   int,
  error_message text
);

create table if not exists events (
  id              uuid primary key default gen_random_uuid(),
  library         text not null,
  title           text not null,
  event_date      date not null,
  event_time      text not null,
  location        text,
  age_group       text,
  program_type    text,
  description     text,
  link            text,
  start_at        timestamptz,
  scrape_run_id   uuid references scrape_runs(id) on delete set null,
  first_seen_at   timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  unique (library, title, event_date, event_time)
);

create index if not exists events_start_at_idx on events (start_at);
create index if not exists events_library_idx on events (library);
create index if not exists events_age_group_idx on events (age_group);
create index if not exists events_search_idx on events using gin (
  to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,''))
);

alter table events enable row level security;
alter table scrape_runs enable row level security;

drop policy if exists "events readable" on events;
create policy "events readable" on events for select to anon using (true);

drop policy if exists "scrape_runs readable" on scrape_runs;
create policy "scrape_runs readable" on scrape_runs for select to anon using (true);
-- service_role bypasses RLS automatically; no INSERT/UPDATE policy needed for anon.
```

**Step 2: Commit**

```bash
git add supabase/schema.sql
git commit -m "Add Supabase schema for events and scrape_runs"
```

**Manual verification (user):**
1. Create new Supabase project at https://supabase.com/dashboard
2. Open SQL editor, paste contents of `supabase/schema.sql`, run
3. Verify `events` and `scrape_runs` tables exist in Table Editor

---

## Phase 2: Refactor scraper for Supabase

### Task 2: Make `library_all_events.collect_all_events()` callable

**Files:**
- Modify: `library_all_events.py` (extract the aggregation logic from `main()` into a reusable function)

**Step 1: Identify the section in `main()` that produces `all_events`**

Read `library_all_events.py` and locate where it gathers events from all fetchers and deduplicates them (per CLAUDE.md, around lines 973-1006).

**Step 2: Extract into `collect_all_events(args) -> List[Dict[str, Any]]`**

The new function should:
- Accept the same parsed args (start_date, days, libnet_ages, etc.)
- Run all async fetchers via `asyncio.gather`
- Deduplicate by `(Library, Title, Date, Time)`
- Return the final sorted list
- NOT write any CSV/PDF/ICS files

`main()` keeps its current CLI behavior by calling `collect_all_events()` and then writing files.

**Step 3: Run the scraper end-to-end to confirm no regression**

```bash
cd .worktrees/vercel-supabase
python library_all_events.py --days 1
```

Expected: CSV produced as before, no errors.

**Step 4: Commit**

```bash
git add library_all_events.py
git commit -m "Extract collect_all_events() for reuse outside CLI"
```

---

### Task 3: Add `scripts/scrape_to_supabase.py` adapter

**Files:**
- Create: `scripts/scrape_to_supabase.py`

**Step 1: Write the adapter**

```python
"""
Adapter: run the existing scraper and UPSERT results into Supabase.

Env vars required:
  SUPABASE_URL                  e.g. https://xxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY     server-side write key (bypasses RLS)
  FIRECRAWL_API_KEY             passed through to existing scraper
  TIMEZONE                      default 'America/Chicago'
  VERCEL_REVALIDATE_URL         optional; if set, POST to it on success
  REVALIDATE_SECRET             Bearer token for the revalidate endpoint
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, date, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

from library_all_events import collect_all_events, parse_time_to_sortable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scrape_to_supabase")

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/Chicago"))
BATCH_SIZE = 200

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def parse_event_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%A, %B %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def compute_start_at(event_date_str: str, event_time_str: str) -> str | None:
    d = parse_event_date(event_date_str)
    if not d:
        return None
    t = parse_time_to_sortable(event_time_str) or time.min
    return datetime.combine(d, t, tzinfo=TZ).isoformat()


def to_row(ev: dict, scrape_run_id: str) -> dict:
    return {
        "library":       ev.get("Library", ""),
        "title":         ev.get("Title", ""),
        "event_date":    (parse_event_date(ev.get("Date", "")) or date.today()).isoformat(),
        "event_time":    ev.get("Time", "All Day"),
        "location":      ev.get("Location") or None,
        "age_group":     ev.get("Age Group") or None,
        "program_type":  ev.get("Program Type") or None,
        "description":   ev.get("Description") or None,
        "link":          ev.get("Link") or None,
        "start_at":      compute_start_at(ev.get("Date", ""), ev.get("Time", "")),
        "scrape_run_id": scrape_run_id,
        "last_seen_at":  datetime.now(TZ).isoformat(),
    }


def create_scrape_run() -> str:
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/scrape_runs",
        headers={**HEADERS, "Prefer": "return=representation"},
        json={"status": "running"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]["id"]


def finish_scrape_run(run_id: str, status: str, event_count: int | None, err: str | None):
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/scrape_runs?id=eq.{run_id}",
        headers=HEADERS,
        json={
            "finished_at": datetime.now(TZ).isoformat(),
            "status": status,
            "event_count": event_count,
            "error_message": (err[:1000] if err else None),
        },
        timeout=30,
    ).raise_for_status()


def upsert_events(rows: list[dict]):
    """UPSERT against the natural unique constraint."""
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/events?on_conflict=library,title,event_date,event_time",
            headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=chunk,
            timeout=60,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase upsert failed: {r.status_code} {r.text}")
        log.info("Upserted batch %d-%d", i, i + len(chunk))


def revalidate_vercel():
    url = os.environ.get("VERCEL_REVALIDATE_URL")
    secret = os.environ.get("REVALIDATE_SECRET")
    if not url or not secret:
        log.info("Revalidate skipped (no VERCEL_REVALIDATE_URL/REVALIDATE_SECRET)")
        return
    r = requests.post(url, headers={"Authorization": f"Bearer {secret}"}, timeout=30)
    log.info("Revalidate %s -> %s", url, r.status_code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=31)
    parser.add_argument("--start-offset-days", type=int, default=0)
    args = parser.parse_args()

    run_id = create_scrape_run()
    log.info("Created scrape_run %s", run_id)
    try:
        events = asyncio.run(collect_all_events(
            days=args.days,
            start_offset_days=args.start_offset_days,
        ))
        rows = [to_row(e, run_id) for e in events if e.get("Title") and e.get("Date")]
        log.info("Prepared %d rows (raw events: %d)", len(rows), len(events))
        upsert_events(rows)
        finish_scrape_run(run_id, "success", len(rows), None)
        revalidate_vercel()
    except Exception as exc:
        log.exception("Scrape failed")
        finish_scrape_run(run_id, "failed", None, repr(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 2: Smoke test locally** (only if Supabase env vars are configured)

```bash
export SUPABASE_URL=...
export SUPABASE_SERVICE_ROLE_KEY=...
export FIRECRAWL_API_KEY=...
python scripts/scrape_to_supabase.py --days 1
```

Expected: scrape_runs row created, events upserted, second row marked `success`.

**Step 3: Commit**

```bash
git add scripts/scrape_to_supabase.py
git commit -m "Add Supabase upsert adapter for the scraper"
```

---

## Phase 3: GitHub Actions

### Task 4: Add the daily scrape workflow

**Files:**
- Create: `.github/workflows/scrape.yml`

**Step 1: Write the workflow**

```yaml
name: Daily scrape
on:
  schedule:
    - cron: '0 12 * * *'   # 6 AM Central (CST). Slides 1h in DST.
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
      - name: Run scraper -> Supabase
        env:
          FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          VERCEL_REVALIDATE_URL: ${{ secrets.VERCEL_REVALIDATE_URL }}
          REVALIDATE_SECRET: ${{ secrets.REVALIDATE_SECRET }}
          TIMEZONE: America/Chicago
        run: python scripts/scrape_to_supabase.py
```

**Step 2: Commit**

```bash
git add .github/workflows/scrape.yml
git commit -m "Add daily scrape workflow (GitHub Actions)"
```

**Manual verification (user, after secrets are configured):**
1. Push branch and add secrets in GitHub repo settings (see Task 14)
2. Run "Daily scrape" via "Run workflow" button on Actions tab
3. Confirm a `scrape_runs` row with `status='success'` and `event_count > 0`

---

## Phase 4: Next.js frontend scaffolding

### Task 5: Scaffold the Next.js app in `web/`

**Files:**
- Create: `web/` directory via `create-next-app`

**Step 1: Run the generator**

```bash
cd /Users/aaronmiller/LibraryScrapper/.worktrees/vercel-supabase
npx create-next-app@latest web --typescript --tailwind --app --src-dir=false --import-alias="@/*" --no-eslint --use-npm --yes
```

**Step 2: Verify dev server starts**

```bash
cd web
npm run dev
# visit http://localhost:3000, confirm default Next.js page renders, then Ctrl-C
```

**Step 3: Commit**

```bash
cd /Users/aaronmiller/LibraryScrapper/.worktrees/vercel-supabase
git add web
git commit -m "Scaffold Next.js 16 app in web/"
```

---

### Task 6: Add Supabase client + env template

**Files:**
- Create: `web/lib/supabase.ts`
- Create: `web/.env.local.example`

**Step 1: Install Supabase client**

```bash
cd web
npm install @supabase/supabase-js
```

**Step 2: Write `web/lib/supabase.ts`**

```ts
import { createClient } from '@supabase/supabase-js';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

if (!url || !anon) {
  throw new Error('NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set');
}

export const supabase = createClient(url, anon, {
  auth: { persistSession: false },
});

export type EventRow = {
  id: string;
  library: string;
  title: string;
  event_date: string;
  event_time: string;
  location: string | null;
  age_group: string | null;
  program_type: string | null;
  description: string | null;
  link: string | null;
  start_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
};

export type ScrapeRun = {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: 'running' | 'success' | 'failed';
  event_count: number | null;
};
```

**Step 3: Write `web/.env.local.example`**

```
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
REVALIDATE_SECRET=generate-a-long-random-string
```

**Step 4: Commit**

```bash
git add web/lib web/.env.local.example web/package.json web/package-lock.json
git commit -m "Add Supabase client + env template"
```

---

### Task 7: Event types + server-side fetcher

**Files:**
- Create: `web/lib/events.ts`

**Step 1: Write the fetcher**

```ts
import 'server-only';
import { unstable_cacheLife as cacheLife, unstable_cacheTag as cacheTag } from 'next/cache';
import { supabase, type EventRow, type ScrapeRun } from './supabase';

export async function getUpcomingEvents(): Promise<EventRow[]> {
  'use cache';
  cacheLife('hours');
  cacheTag('events');

  const todayIso = new Date().toISOString().slice(0, 10);
  const { data, error } = await supabase
    .from('events')
    .select('*')
    .gte('event_date', todayIso)
    .order('start_at', { ascending: true })
    .limit(2000);
  if (error) throw new Error(error.message);
  return data ?? [];
}

export async function getLatestScrapeRun(): Promise<ScrapeRun | null> {
  'use cache';
  cacheLife('minutes');
  cacheTag('events');

  const { data, error } = await supabase
    .from('scrape_runs')
    .select('*')
    .order('started_at', { ascending: false })
    .limit(1);
  if (error) throw new Error(error.message);
  return data?.[0] ?? null;
}
```

**Step 2: Commit**

```bash
git add web/lib/events.ts
git commit -m "Add server-side event + scrape_run fetchers with cache tags"
```

---

### Task 8: Build `EventCard` and `EventList` (client component)

**Files:**
- Create: `web/app/components/EventCard.tsx`
- Create: `web/app/components/EventList.tsx`

**Step 1: Write `EventCard.tsx`**

```tsx
import type { EventRow } from '@/lib/supabase';

export function EventCard({ event }: { event: EventRow }) {
  return (
    <article className="rounded-lg border border-gray-200 p-4 hover:shadow-md transition">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="font-semibold text-gray-900">{event.title}</h3>
        <span className="text-xs uppercase tracking-wide text-gray-500">{event.library}</span>
      </div>
      <div className="mt-1 text-sm text-gray-700">
        {event.event_date} &middot; {event.event_time}
        {event.location ? ` • ${event.location}` : ''}
      </div>
      {event.age_group && (
        <div className="mt-1 text-xs text-gray-600">Ages: {event.age_group}</div>
      )}
      {event.description && (
        <p className="mt-2 text-sm text-gray-700 line-clamp-3">{event.description}</p>
      )}
      {event.link && (
        <a className="mt-2 inline-block text-sm text-blue-600 hover:underline"
           href={event.link} target="_blank" rel="noreferrer">
          Event details &rarr;
        </a>
      )}
    </article>
  );
}
```

**Step 2: Write `EventList.tsx` (client component with filtering)**

```tsx
'use client';
import { useMemo, useState } from 'react';
import type { EventRow } from '@/lib/supabase';
import { EventCard } from './EventCard';

type Props = { events: EventRow[] };

export function EventList({ events }: Props) {
  const [query, setQuery] = useState('');
  const [libraryFilter, setLibraryFilter] = useState<string[]>([]);
  const [ageFilter, setAgeFilter] = useState<string[]>([]);
  const [from, setFrom] = useState<string>('');
  const [to, setTo] = useState<string>('');

  const libraries = useMemo(
    () => Array.from(new Set(events.map(e => e.library))).sort(),
    [events],
  );
  const ageGroups = useMemo(
    () => Array.from(new Set(events.map(e => e.age_group).filter(Boolean) as string[])).sort(),
    [events],
  );

  const filtered = useMemo(() => events.filter(e => {
    if (libraryFilter.length && !libraryFilter.includes(e.library)) return false;
    if (ageFilter.length && (!e.age_group || !ageFilter.includes(e.age_group))) return false;
    if (from && e.event_date < from) return false;
    if (to && e.event_date > to) return false;
    if (query) {
      const q = query.toLowerCase();
      const hay = `${e.title} ${e.description ?? ''} ${e.location ?? ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }), [events, libraryFilter, ageFilter, from, to, query]);

  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter(x => x !== v) : [...list, v];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6">
      <aside className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">Search</label>
          <input className="w-full border rounded px-2 py-1"
                 value={query} onChange={e => setQuery(e.target.value)}
                 placeholder="Title, description, location" />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">From</label>
          <input type="date" className="w-full border rounded px-2 py-1"
                 value={from} onChange={e => setFrom(e.target.value)} />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">To</label>
          <input type="date" className="w-full border rounded px-2 py-1"
                 value={to} onChange={e => setTo(e.target.value)} />
        </div>
        <fieldset>
          <legend className="text-sm font-medium mb-1">Libraries</legend>
          {libraries.map(l => (
            <label key={l} className="flex items-center gap-2 text-sm">
              <input type="checkbox"
                     checked={libraryFilter.includes(l)}
                     onChange={() => setLibraryFilter(s => toggle(s, l))} />
              {l}
            </label>
          ))}
        </fieldset>
        <fieldset>
          <legend className="text-sm font-medium mb-1">Age groups</legend>
          {ageGroups.map(a => (
            <label key={a} className="flex items-center gap-2 text-sm">
              <input type="checkbox"
                     checked={ageFilter.includes(a)}
                     onChange={() => setAgeFilter(s => toggle(s, a))} />
              {a}
            </label>
          ))}
        </fieldset>
      </aside>
      <section>
        <div className="text-sm text-gray-600 mb-3">{filtered.length} events</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map(e => <EventCard key={e.id} event={e} />)}
        </div>
      </section>
    </div>
  );
}
```

**Step 3: Commit**

```bash
git add web/app/components
git commit -m "Add EventCard and EventList (client-side filtering)"
```

---

### Task 9: Wire the main page + last-refreshed indicator

**Files:**
- Modify: `web/app/page.tsx`
- Modify: `web/app/layout.tsx`

**Step 1: Replace `web/app/page.tsx`**

```tsx
import { getUpcomingEvents } from '@/lib/events';
import { EventList } from './components/EventList';

export default async function Page() {
  const events = await getUpcomingEvents();
  return (
    <main className="max-w-6xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-4">Chicago Library Events</h1>
      <EventList events={events} />
    </main>
  );
}
```

**Step 2: Replace `web/app/layout.tsx` (add header w/ last-refreshed)**

```tsx
import './globals.css';
import { getLatestScrapeRun } from '@/lib/events';

export const metadata = { title: 'Chicago Library Events' };

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const hrs = Math.floor(diff / 3_600_000);
  if (hrs < 1) return 'just now';
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const run = await getLatestScrapeRun();
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900">
        <header className="border-b bg-white">
          <div className="max-w-6xl mx-auto p-4 flex items-center justify-between text-sm">
            <span className="font-semibold">Library Events</span>
            <span className="text-gray-600">
              {run?.finished_at
                ? `Updated ${timeAgo(run.finished_at)}`
                : 'Refresh pending'}
            </span>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
```

**Step 3: Smoke test**

```bash
cd web
cp .env.local.example .env.local
# fill in real Supabase values
npm run dev
# visit http://localhost:3000 — events render, filters work
```

**Step 4: Commit**

```bash
git add web/app/page.tsx web/app/layout.tsx
git commit -m "Wire main page + last-refreshed indicator"
```

---

### Task 10: Revalidate API route

**Files:**
- Create: `web/app/api/revalidate/route.ts`

**Step 1: Write the route**

```ts
import { NextResponse } from 'next/server';
import { revalidateTag } from 'next/cache';

export async function POST(req: Request) {
  const secret = process.env.REVALIDATE_SECRET;
  const auth = req.headers.get('authorization') ?? '';
  if (!secret || auth !== `Bearer ${secret}`) {
    return new NextResponse('Unauthorized', { status: 401 });
  }
  revalidateTag('events');
  return NextResponse.json({ revalidated: true });
}
```

**Step 2: Smoke test**

```bash
curl -i -X POST http://localhost:3000/api/revalidate \
  -H "Authorization: Bearer $REVALIDATE_SECRET"
# Expected: 200 {"revalidated":true}
```

**Step 3: Commit**

```bash
git add web/app/api/revalidate/route.ts
git commit -m "Add Bearer-protected revalidate endpoint"
```

---

### Task 11: ICS export route

**Files:**
- Create: `web/app/api/export/ics/route.ts`

**Step 1: Install `ics`**

```bash
cd web
npm install ics
```

**Step 2: Write the route**

```ts
import { NextResponse } from 'next/server';
import * as ics from 'ics';
import { getUpcomingEvents } from '@/lib/events';

export async function GET() {
  const events = await getUpcomingEvents();
  const calEvents = events.flatMap(e => {
    if (!e.start_at) return [];
    const d = new Date(e.start_at);
    const start: ics.DateArray = [
      d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate(),
      d.getUTCHours(), d.getUTCMinutes(),
    ];
    return [{
      uid: e.id,
      start,
      startInputType: 'utc' as const,
      duration: { hours: 1 },
      title: e.title,
      description: e.description ?? '',
      location: e.location ?? e.library,
      url: e.link ?? undefined,
    }];
  });
  const { error, value } = ics.createEvents(calEvents);
  if (error) return new NextResponse(error.message, { status: 500 });
  return new NextResponse(value!, {
    headers: {
      'Content-Type': 'text/calendar; charset=utf-8',
      'Content-Disposition': 'attachment; filename="library-events.ics"',
    },
  });
}
```

**Step 3: Smoke test**

```bash
curl -o /tmp/test.ics http://localhost:3000/api/export/ics
head /tmp/test.ics
# Expected: starts with BEGIN:VCALENDAR
```

**Step 4: Commit**

```bash
git add web/app/api/export/ics/route.ts web/package.json web/package-lock.json
git commit -m "Add ICS export endpoint"
```

---

### Task 12: PDF export route

**Files:**
- Create: `web/app/api/export/pdf/route.ts`

**Step 1: Install renderer**

```bash
cd web
npm install @react-pdf/renderer
```

**Step 2: Write the route**

```ts
import { NextResponse } from 'next/server';
import { renderToBuffer, Document, Page, Text, View, StyleSheet } from '@react-pdf/renderer';
import React from 'react';
import { getUpcomingEvents } from '@/lib/events';

const styles = StyleSheet.create({
  page: { padding: 32, fontSize: 11 },
  h1: { fontSize: 18, marginBottom: 12 },
  event: { marginBottom: 10, paddingBottom: 6, borderBottom: '1px solid #ccc' },
  title: { fontWeight: 700, fontSize: 12 },
  meta: { color: '#555', marginTop: 2 },
});

export async function GET() {
  const events = await getUpcomingEvents();
  const doc = React.createElement(Document, null,
    React.createElement(Page, { size: 'LETTER', style: styles.page },
      React.createElement(Text, { style: styles.h1 }, 'Chicago Library Events'),
      ...events.map(e =>
        React.createElement(View, { key: e.id, style: styles.event },
          React.createElement(Text, { style: styles.title }, e.title),
          React.createElement(Text, { style: styles.meta },
            `${e.library} • ${e.event_date} • ${e.event_time}` +
            (e.location ? ` • ${e.location}` : '')),
          e.description ? React.createElement(Text, { style: styles.meta }, e.description) : null,
        ),
      ),
    ),
  );
  const buf = await renderToBuffer(doc);
  return new NextResponse(buf, {
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': 'attachment; filename="library-events.pdf"',
    },
  });
}
```

**Step 3: Smoke test**

```bash
curl -o /tmp/test.pdf http://localhost:3000/api/export/pdf
file /tmp/test.pdf
# Expected: PDF document, version 1.x
```

**Step 4: Add export buttons to the layout header**

Modify `web/app/layout.tsx` to add two links in the header:

```tsx
<nav className="flex gap-3">
  <a href="/api/export/ics" className="text-blue-600 hover:underline">ICS</a>
  <a href="/api/export/pdf" className="text-blue-600 hover:underline">PDF</a>
</nav>
```

**Step 5: Commit**

```bash
git add web/app/api/export/pdf/route.ts web/app/layout.tsx web/package.json web/package-lock.json
git commit -m "Add PDF export endpoint + header links"
```

---

## Phase 5: Cutover and docs

### Task 13: Retire Render/Heroku/Flask files

**Files:**
- Delete: `library_web_gui.py`
- Delete: `templates/index.html`
- Delete: `templates/` (if empty)
- Delete: `Procfile`
- Delete: `render.yaml`

**Step 1: Confirm they aren't referenced**

```bash
grep -rn "library_web_gui\|render.yaml\|Procfile" --include="*.py" --include="*.md" --include="*.yml" .
```

**Step 2: Delete**

```bash
git rm library_web_gui.py templates/index.html Procfile render.yaml
rmdir templates 2>/dev/null || true
```

**Step 3: Commit**

```bash
git commit -m "Retire Flask web GUI and Render/Heroku configs"
```

---

### Task 14: Update README + add deployment notes

**Files:**
- Modify: `README.md` (replace the deployment section)
- Create: `docs/deployment.md` (detailed setup)

**Step 1: Write `docs/deployment.md`**

Include:
- Supabase: create project, run `supabase/schema.sql`, copy URL + service_role + anon keys
- Vercel: connect repo, set root to `web/`, env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `REVALIDATE_SECRET`
- GitHub secrets: `FIRECRAWL_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `VERCEL_REVALIDATE_URL`, `REVALIDATE_SECRET`
- Manual seed: trigger "Daily scrape" workflow once
- DST note re: 12:00 UTC cron

**Step 2: Update `README.md`** with a Quickstart pointing at `docs/deployment.md`.

**Step 3: Commit**

```bash
git add README.md docs/deployment.md
git commit -m "Document Vercel + Supabase + Actions deployment"
```

---

### Task 15: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update sections that mention Flask/Render/CSV as the primary path**

Add a section noting:
- Frontend is now `web/` (Next.js); Flask code is removed
- Persistence is Supabase; CSV is no longer produced by default
- Scheduling is GitHub Actions, not Render cron
- The old desktop GUI (`library_gui.py`) is unchanged and reads CSV from prior runs only

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Update CLAUDE.md for new Vercel+Supabase architecture"
```

---

### Task 16: Open the pull request

**Step 1: Push branch**

```bash
git push -u origin feature/vercel-supabase-migration
```

**Step 2: Open PR**

```bash
gh pr create --title "Migrate to Vercel + Supabase + GitHub Actions" --body "$(cat docs/plans/2026-05-25-vercel-supabase-migration.md)"
```

**Step 3: Manual end-to-end verification on a Vercel preview deploy**
- Trigger the GitHub Actions workflow on the branch
- Verify events appear on the preview URL
- Verify ICS + PDF exports download
- Verify the revalidate endpoint returns 200 with the right Bearer

---

## Out of scope (do NOT do in this plan)

- Authentication
- Favoriting / per-user state
- Server-side full-text search (the FTS index is created but unused for now)
- Replacing `library_gui.py`
- Mobile-specific UI polish beyond Tailwind defaults
- CI for the Next.js app (Vercel handles preview/prod builds)
