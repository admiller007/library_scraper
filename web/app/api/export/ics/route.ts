import { NextResponse } from 'next/server';
import { connection } from 'next/server';
import { getUpcomingEvents } from '@/lib/events';
import { createIcs } from '@/lib/ics';

export async function GET() {
  await connection();
  const events = await getUpcomingEvents();
  const { error, value } = createIcs(events);
  if (error || !value) {
    return new NextResponse(error?.message ?? 'ICS generation failed', { status: 500 });
  }
  return new NextResponse(value, {
    headers: {
      'Content-Type': 'text/calendar; charset=utf-8',
      'Content-Disposition': 'attachment; filename="library-events.ics"',
    },
  });
}
