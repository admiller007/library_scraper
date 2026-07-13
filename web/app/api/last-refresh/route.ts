import { NextResponse } from 'next/server';
import { getSupabase } from '@/lib/supabase';

// Uncached: the freshness stamp must reflect the real latest scrape run, not
// the page's cached render. `Cache-Control: no-store` keeps the CDN and the
// browser from holding a stale copy (the reason the header label used to lag).
export async function GET() {
  const noStore = { 'Cache-Control': 'no-store' };
  const client = getSupabase();
  if (!client) {
    return NextResponse.json({ finishedAt: null, status: null }, { headers: noStore });
  }
  const { data, error } = await client
    .from('scrape_runs')
    .select('finished_at,status')
    .order('started_at', { ascending: false })
    .limit(1);
  if (error) {
    return NextResponse.json(
      { finishedAt: null, status: null, error: error.message },
      { status: 500, headers: noStore },
    );
  }
  const run = data?.[0];
  return NextResponse.json(
    { finishedAt: run?.finished_at ?? null, status: run?.status ?? null },
    { headers: noStore },
  );
}
