'use client';
import { useMemo, useState } from 'react';
import type { EventRow } from '@/lib/supabase';
import { EventCard } from './EventCard';

type Props = { events: EventRow[] };

export function EventList({ events }: Props) {
  const [query, setQuery] = useState('');
  const [libraryFilter, setLibraryFilter] = useState<string[]>([]);
  const [ageFilter, setAgeFilter] = useState<string[]>([]);
  const [from, setFrom] = useState<string>('');
  const [to, setTo] = useState<string>('');

  const libraries = useMemo(
    () => Array.from(new Set(events.map((e) => e.library))).sort(),
    [events],
  );
  const ageGroups = useMemo(
    () =>
      Array.from(
        new Set(events.map((e) => e.age_group).filter((v): v is string => !!v)),
      ).sort(),
    [events],
  );

  const filtered = useMemo(
    () =>
      events.filter((e) => {
        if (libraryFilter.length && !libraryFilter.includes(e.library)) return false;
        if (ageFilter.length && (!e.age_group || !ageFilter.includes(e.age_group))) return false;
        if (from && e.event_date < from) return false;
        if (to && e.event_date > to) return false;
        if (query) {
          const q = query.toLowerCase();
          const hay = `${e.title} ${e.description ?? ''} ${e.location ?? ''}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      }),
    [events, libraryFilter, ageFilter, from, to, query],
  );

  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6">
      <aside className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">Search</label>
          <input
            className="w-full border rounded px-2 py-1"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Title, description, location"
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-sm font-medium mb-1">From</label>
            <input
              type="date"
              className="w-full border rounded px-2 py-1"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">To</label>
            <input
              type="date"
              className="w-full border rounded px-2 py-1"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
        </div>
        <fieldset>
          <legend className="text-sm font-medium mb-1">Libraries</legend>
          <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
            {libraries.map((l) => (
              <label key={l} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={libraryFilter.includes(l)}
                  onChange={() => setLibraryFilter((s) => toggle(s, l))}
                />
                {l}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className="text-sm font-medium mb-1">Age groups</legend>
          <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
            {ageGroups.map((a) => (
              <label key={a} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={ageFilter.includes(a)}
                  onChange={() => setAgeFilter((s) => toggle(s, a))}
                />
                {a}
              </label>
            ))}
          </div>
        </fieldset>
      </aside>
      <section>
        <div className="text-sm text-gray-600 mb-3">{filtered.length} events</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map((e) => (
            <EventCard key={e.id} event={e} />
          ))}
        </div>
      </section>
    </div>
  );
}
