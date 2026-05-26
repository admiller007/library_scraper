-- Supabase schema for the library event scraper.
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
