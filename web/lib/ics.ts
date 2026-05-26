import 'server-only';
import * as ics from 'ics';
import type { EventRow } from './supabase';

export function validHttpUrl(raw: string | null): string | undefined {
  const match = raw?.match(/https?:\/\/\S+/);
  if (!match) return undefined;
  const value = match[0].replace(/[)"'<>]+$/, '');
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function eventToIcsAttributes(event: EventRow): ics.EventAttributes | null {
  if (!event.start_at) return null;
  const d = new Date(event.start_at);
  if (Number.isNaN(d.getTime())) return null;

  const start: ics.DateArray = [
    d.getUTCFullYear(),
    d.getUTCMonth() + 1,
    d.getUTCDate(),
    d.getUTCHours(),
    d.getUTCMinutes(),
  ];

  return {
    uid: event.id,
    start,
    startInputType: 'utc',
    duration: { hours: 1 },
    title: event.title,
    description: event.description ?? '',
    location: event.location ?? event.library,
    url: validHttpUrl(event.link),
  };
}

export function createIcs(events: EventRow[]): { error?: Error; value?: string } {
  const calEvents = events.flatMap((event) => {
    const attrs = eventToIcsAttributes(event);
    return attrs ? [attrs] : [];
  });
  const result = ics.createEvents(calEvents);
  return {
    error: result.error ?? undefined,
    value: result.value ?? undefined,
  };
}

export function eventIcsFilename(event: EventRow): string {
  const base = `${event.event_date}-${event.library}-${event.title}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 90);
  return `${base || 'library-event'}.ics`;
}
