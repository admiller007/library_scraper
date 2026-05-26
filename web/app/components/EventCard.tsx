import type { EventRow } from '@/lib/supabase';

export function EventCard({ event }: { event: EventRow }) {
  return (
    <article className="rounded-lg border border-gray-200 bg-white p-4 hover:shadow-md transition">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="font-semibold text-gray-900">{event.title}</h3>
        <span className="text-xs uppercase tracking-wide text-gray-500 whitespace-nowrap">
          {event.library}
        </span>
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
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        {event.link && (
          <a
            className="text-blue-600 hover:underline"
            href={event.link}
            target="_blank"
            rel="noreferrer"
          >
            Event details &rarr;
          </a>
        )}
        <a
          className="text-blue-600 hover:underline"
          href={`/api/export/ics/${event.id}`}
          download
        >
          Add to calendar
        </a>
      </div>
    </article>
  );
}
