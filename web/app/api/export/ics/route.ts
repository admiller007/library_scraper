import { NextResponse } from 'next/server';
import { connection } from 'next/server';
import { getUpcomingEvents } from '@/lib/events';
import type { EventRow } from '@/lib/supabase';
import { createIcs } from '@/lib/ics';

function icsResponse(events: EventRow[], filename: string) {
  const { error, value } = createIcs(events);
  if (error || !value) {
    return new NextResponse(error?.message ?? 'ICS generation failed', { status: 500 });
  }
  return new NextResponse(value, {
    headers: {
      'Content-Type': 'text/calendar; charset=utf-8',
      'Content-Disposition': `attachment; filename="${filename}"`,
    },
  });
}

export async function GET() {
  await connection();
  const events = await getUpcomingEvents();
  return icsResponse(events, 'library-events.ics');
}

export async function POST(req: Request) {
  await connection();
  const body = (await req.json().catch(() => null)) as { ids?: unknown } | null;
  const ids = Array.isArray(body?.ids)
    ? body.ids.filter((id): id is string => typeof id === 'string')
    : [];

  if (!ids.length) {
    return new NextResponse('No events selected', { status: 400 });
  }

  const selected = new Set(ids);
  const events = (await getUpcomingEvents()).filter((event) => selected.has(event.id));

  if (!events.length) {
    return new NextResponse('Selected events not found', { status: 404 });
  }

  return icsResponse(events, 'selected-library-events.ics');
}
