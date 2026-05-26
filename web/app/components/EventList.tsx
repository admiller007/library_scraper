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
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [isDownloading, setIsDownloading] = useState(false);

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

  const filteredIds = useMemo(() => filtered.map((e) => e.id), [filtered]);
  const selectedCount = selectedIds.size;
  const allFilteredSelected =
    filteredIds.length > 0 && filteredIds.every((id) => selectedIds.has(id));

  const setEventSelected = (id: string, selected: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (selected) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const setFilteredSelected = (selected: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      for (const id of filteredIds) {
        if (selected) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  };

  const downloadSelected = async () => {
    if (!selectedIds.size || isDownloading) return;
    setIsDownloading(true);
    try {
      const response = await fetch('/api/export/ics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: Array.from(selectedIds) }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') ?? '';
      const filename =
        disposition.match(/filename="([^"]+)"/)?.[1] ?? 'selected-library-events.ics';
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } finally {
      setIsDownloading(false);
    }
  };

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
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm">
          <div className="text-gray-600">
            {filtered.length} events
            {selectedCount > 0 ? ` · ${selectedCount} selected` : ''}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-gray-700">
              <input
                type="checkbox"
                checked={allFilteredSelected}
                onChange={(e) => setFilteredSelected(e.target.checked)}
                disabled={!filteredIds.length}
              />
              Select shown
            </label>
            {selectedCount > 0 && (
              <button
                type="button"
                className="text-gray-600 hover:text-gray-900"
                onClick={() => setSelectedIds(new Set())}
              >
                Clear
              </button>
            )}
            <button
              type="button"
              className="rounded border border-blue-200 px-3 py-1 text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400 disabled:hover:bg-transparent"
              disabled={!selectedCount || isDownloading}
              onClick={downloadSelected}
            >
              {isDownloading ? 'Downloading...' : 'Download selected ICS'}
            </button>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map((e) => (
            <EventCard
              key={e.id}
              event={e}
              selected={selectedIds.has(e.id)}
              onSelectedChange={(selected) => setEventSelected(e.id, selected)}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
