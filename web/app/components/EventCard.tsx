import type { EventRow } from '@/lib/supabase';

type Props = {
  event: EventRow;
  selected?: boolean;
  onSelectedChange?: (selected: boolean) => void;
};

export function EventCard({ event, selected = false, onSelectedChange }: Props) {
  return (
    <article
      className={`rounded-lg border bg-white p-4 transition ${
        selected ? 'border-blue-300 shadow-sm ring-1 ring-blue-100' : 'border-gray-200 hover:shadow-md'
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <input
            type="checkbox"
            className="mt-1 h-4 w-4 shrink-0"
            checked={selected}
            onChange={(e) => onSelectedChange?.(e.target.checked)}
            aria-label={`Select ${event.title}`}
          />
          <h3 className="font-semibold text-gray-900">{event.title}</h3>
        </div>
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
