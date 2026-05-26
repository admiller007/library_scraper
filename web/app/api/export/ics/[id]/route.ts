import { NextResponse } from 'next/server';
import { connection } from 'next/server';
import { getUpcomingEvents } from '@/lib/events';
import { createIcs, eventIcsFilename } from '@/lib/ics';

type Context = {
  params: Promise<{ id: string }>;
};

export async function GET(_req: Request, { params }: Context) {
  await connection();
  const { id } = await params;
  const events = await getUpcomingEvents();
  const event = events.find((item) => item.id === id);

  if (!event) {
    return new NextResponse('Event not found', { status: 404 });
  }

  const { error, value } = createIcs([event]);
  if (error || !value) {
    return new NextResponse(error?.message ?? 'ICS generation failed', { status: 500 });
  }

  return new NextResponse(value, {
    headers: {
      'Content-Type': 'text/calendar; charset=utf-8',
      'Content-Disposition': `attachment; filename="${eventIcsFilename(event)}"`,
    },
  });
}
