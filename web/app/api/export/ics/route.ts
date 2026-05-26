import { NextResponse } from 'next/server';
import * as ics from 'ics';
import { getUpcomingEvents } from '@/lib/events';

export async function GET() {
  const events = await getUpcomingEvents();
  const calEvents: ics.EventAttributes[] = events.flatMap((e) => {
    if (!e.start_at) return [];
    const d = new Date(e.start_at);
    const start: ics.DateArray = [
      d.getUTCFullYear(),
      d.getUTCMonth() + 1,
      d.getUTCDate(),
      d.getUTCHours(),
      d.getUTCMinutes(),
    ];
    return [{
      uid: e.id,
      start,
      startInputType: 'utc',
      duration: { hours: 1 },
      title: e.title,
      description: e.description ?? '',
      location: e.location ?? e.library,
      url: e.link ?? undefined,
    } satisfies ics.EventAttributes];
  });
  const { error, value } = ics.createEvents(calEvents);
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
