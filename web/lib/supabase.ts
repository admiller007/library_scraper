import { createClient, type SupabaseClient } from '@supabase/supabase-js';

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

let cached: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (cached) return cached;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anon) {
    throw new Error(
      'NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set',
    );
  }
  cached = createClient(url, anon, { auth: { persistSession: false } });
  return cached;
}
