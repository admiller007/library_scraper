import 'server-only';
import { cacheLife, cacheTag } from 'next/cache';
import { getSupabase, type EventRow, type ScrapeRun } from './supabase';

export async function getUpcomingEvents(): Promise<EventRow[]> {
  'use cache';
  cacheLife('hours');
  cacheTag('events');

  const today = new Date().toISOString().slice(0, 10);
  const { data, error } = await getSupabase()
    .from('events')
    .select('*')
    .gte('event_date', today)
    .order('start_at', { ascending: true })
    .limit(2000);
  if (error) throw new Error(error.message);
  return (data ?? []) as EventRow[];
}

export async function getLatestScrapeRun(): Promise<ScrapeRun | null> {
  'use cache';
  cacheLife('minutes');
  cacheTag('events');

  const { data, error } = await getSupabase()
    .from('scrape_runs')
    .select('*')
    .order('started_at', { ascending: false })
    .limit(1);
  if (error) throw new Error(error.message);
  return ((data?.[0] ?? null) as ScrapeRun | null);
}
