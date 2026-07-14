'use client';
import { useMemo, useState } from 'react';
import type { EventRow } from '@/lib/supabase';
import { addDaysKey, parseKey, todayKey } from '@/lib/dates';
import { SOURCE_CATEGORIES, categorize, type SourceCategory } from '@/lib/categories';
import { CalendarView } from './CalendarView';
import { EventCard } from './EventCard';

type Props = { events: EventRow[] };

export function EventList({ events }: Props) {
  const [query, setQuery] = useState('');
  const [libraryFilter, setLibraryFilter] = useState<string[]>([]);
  const [ageFilter, setAgeFilter] = useState<string[]>([]);
  const [programFilter, setProgramFilter] = useState<string[]>([]);
  const [from, setFrom] = useState<string>('');
  const [to, setTo] = useState<string>('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [isDownloading, setIsDownloading] = useState(false);
  const [viewMode, setViewMode] = useState<'list' | 'calendar'>('list');
  // null until the user first opens the calendar — reading the current date
  // during render would break Cache Components prerendering.
  const [calendarMonth, setCalendarMonth] = useState<{ year: number; month: number } | null>(
    null,
  );
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const showCalendar = () => {
    setViewMode('calendar');
    setCalendarMonth((current) => {
      if (current) return current;
      const now = new Date();
      return { year: now.getFullYear(), month: now.getMonth() };
    });
  };

  const libraries = useMemo(
    () => Array.from(new Set(events.map((e) => e.library))).sort(),
    [events],
  );
  // Group sources by category (Libraries / Park Districts / …) for the filter.
  const librariesByCategory = useMemo(() => {
    const map = new Map<SourceCategory, string[]>();
    for (const l of libraries) {
      const cat = categorize(l);
      const list = map.get(cat);
      if (list) list.push(l);
      else map.set(cat, [l]);
    }
    return map;
  }, [libraries]);
  // Categories start collapsed so the panel stays compact and the group
  // "select all" checkboxes are the prominent control.
  const [collapsedCats, setCollapsedCats] = useState<Set<string>>(
    () => new Set(SOURCE_CATEGORIES),
  );
  const toggleCategoryCollapsed = (cat: string) =>
    setCollapsedCats((current) => {
      const next = new Set(current);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  const toggleLibraryGroup = (items: string[]) =>
    setLibraryFilter((current) => {
      const allSelected = items.every((l) => current.includes(l));
      if (allSelected) return current.filter((l) => !items.includes(l));
      const next = new Set(current);
      items.forEach((l) => next.add(l));
      return Array.from(next);
    });
  // Age groups and program types are often comma-joined combos
  // ("Middle School, High School, Adults"); filter on the individual tags.
  const splitTags = (value: string | null) =>
    value ? value.split(',').map((s) => s.trim()).filter(Boolean) : [];

  const collectTags = (values: (string | null)[]) =>
    Array.from(new Set(values.flatMap(splitTags))).sort();

  const ageGroups = useMemo(() => collectTags(events.map((e) => e.age_group)), [events]);
  const programTypes = useMemo(
    () => collectTags(events.map((e) => e.program_type)),
    [events],
  );

  const filtered = useMemo(
    () =>
      events.filter((e) => {
        if (libraryFilter.length && !libraryFilter.includes(e.library)) return false;
        if (
          ageFilter.length &&
          !splitTags(e.age_group).some((tag) => ageFilter.includes(tag))
        )
          return false;
        if (
          programFilter.length &&
          !splitTags(e.program_type).some((tag) => programFilter.includes(tag))
        )
          return false;
        if (from && e.event_date < from) return false;
        if (to && e.event_date > to) return false;
        if (query) {
          const q = query.toLowerCase();
          const hay =
            `${e.title} ${e.description ?? ''} ${e.location ?? ''} ${e.library}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      }),
    [events, libraryFilter, ageFilter, programFilter, from, to, query],
  );

  const activeFilterCount =
    (query ? 1 : 0) +
    libraryFilter.length +
    ageFilter.length +
    programFilter.length +
    (from ? 1 : 0) +
    (to ? 1 : 0);

  const clearAllFilters = () => {
    setQuery('');
    setLibraryFilter([]);
    setAgeFilter([]);
    setProgramFilter([]);
    setFrom('');
    setTo('');
  };

  // Preset ranges are computed inside click handlers, never during render,
  // so prerendering stays date-free (see the Cache Components note below).
  const applyDatePreset = (preset: 'today' | 'weekend' | 'week') => {
    const today = todayKey();
    if (preset === 'today') {
      setFrom(today);
      setTo(today);
      return;
    }
    if (preset === 'week') {
      setFrom(today);
      setTo(addDaysKey(today, 6));
      return;
    }
    const { year, monthIndex, day } = parseKey(today);
    const dow = new Date(year, monthIndex, day).getDay();
    const saturday = dow === 0 ? null : addDaysKey(today, 6 - dow);
    setFrom(saturday ?? today);
    setTo(saturday ? addDaysKey(saturday, 1) : today);
  };

  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  const eventsByDay = useMemo(() => {
    const map = new Map<string, EventRow[]>();
    for (const e of filtered) {
      const list = map.get(e.event_date);
      if (list) list.push(e);
      else map.set(e.event_date, [e]);
    }
    return map;
  }, [filtered]);

  // Month navigation clamps use the unfiltered events so the range stays
  // stable while filters change. Only computed once the calendar is open;
  // calling todayKey() during prerender would break Cache Components.
  const calendarOpen = viewMode === 'calendar' && calendarMonth !== null;
  const minMonthKey = calendarOpen ? todayKey().slice(0, 7) : '';
  const maxMonthKey = useMemo(
    () =>
      calendarOpen
        ? events
            .reduce((max, e) => (e.event_date > max ? e.event_date : max), todayKey())
            .slice(0, 7)
        : '',
    [events, calendarOpen],
  );

  const handleMonthChange = (month: { year: number; month: number }) => {
    setCalendarMonth(month);
    setSelectedDay(null);
  };

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
      <aside className="space-y-4 lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
            Filters
          </h2>
          {activeFilterCount > 0 && (
            <button
              type="button"
              className="rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              onClick={clearAllFilters}
            >
              Clear all ({activeFilterCount})
            </button>
          )}
        </div>
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
        <div className="flex flex-wrap gap-1">
          {(
            [
              ['today', 'Today'],
              ['weekend', 'This weekend'],
              ['week', 'Next 7 days'],
            ] as const
          ).map(([preset, label]) => (
            <button
              key={preset}
              type="button"
              className="rounded-full border border-gray-300 px-2.5 py-0.5 text-xs text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              onClick={() => applyDatePreset(preset)}
            >
              {label}
            </button>
          ))}
        </div>
        <fieldset>
          <legend className="text-sm font-medium mb-1">
            Sources
            {libraryFilter.length > 0 ? ` · ${libraryFilter.length}` : ''}
          </legend>
          <div className="max-h-72 overflow-y-auto pr-1">
            {SOURCE_CATEGORIES.filter(
              (cat) => (librariesByCategory.get(cat)?.length ?? 0) > 0,
            ).map((cat) => {
              const items = librariesByCategory.get(cat) ?? [];
              const selectedInCat = items.filter((l) =>
                libraryFilter.includes(l),
              ).length;
              const allSelected = selectedInCat === items.length;
              const someSelected = selectedInCat > 0 && !allSelected;
              const collapsed = collapsedCats.has(cat);
              return (
                <div key={cat} className="border-b border-gray-100 last:border-0">
                  <div className="flex items-center gap-2 py-1">
                    <input
                      type="checkbox"
                      aria-label={`Select all ${cat}`}
                      checked={allSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = someSelected;
                      }}
                      onChange={() => toggleLibraryGroup(items)}
                    />
                    <button
                      type="button"
                      className="flex flex-1 items-center justify-between text-left text-sm font-medium"
                      aria-expanded={!collapsed}
                      onClick={() => toggleCategoryCollapsed(cat)}
                    >
                      <span>
                        {cat}
                        <span className="font-normal text-gray-400">
                          {' · '}
                          {selectedInCat > 0 ? `${selectedInCat}/` : ''}
                          {items.length}
                        </span>
                      </span>
                      <span className="text-gray-400">{collapsed ? '▸' : '▾'}</span>
                    </button>
                  </div>
                  {!collapsed && (
                    <div className="ml-6 space-y-1 pb-1.5">
                      {items.map((l) => (
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
                  )}
                </div>
              );
            })}
          </div>
        </fieldset>
        <fieldset>
          <legend className="text-sm font-medium mb-1">
            Age groups
            {ageFilter.length > 0 ? ` · ${ageFilter.length}` : ''}
          </legend>
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
        {programTypes.length > 0 && (
          <fieldset>
            <legend className="text-sm font-medium mb-1">
              Program types
              {programFilter.length > 0 ? ` · ${programFilter.length}` : ''}
            </legend>
            <div className="max-h-48 overflow-y-auto space-y-1 pr-1">
              {programTypes.map((p) => (
                <label key={p} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={programFilter.includes(p)}
                    onChange={() => setProgramFilter((s) => toggle(s, p))}
                  />
                  {p}
                </label>
              ))}
            </div>
          </fieldset>
        )}
      </aside>
      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-sm">
          <div className="text-gray-600">
            {activeFilterCount > 0
              ? `${filtered.length} of ${events.length} events`
              : `${filtered.length} events`}
            {selectedCount > 0 ? ` · ${selectedCount} selected` : ''}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div
              className="inline-flex overflow-hidden rounded border border-gray-300"
              role="group"
              aria-label="View mode"
            >
              <button
                type="button"
                aria-pressed={viewMode === 'list'}
                className={`px-3 py-1 ${
                  viewMode === 'list'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-700 hover:bg-gray-50'
                }`}
                onClick={() => setViewMode('list')}
              >
                List
              </button>
              <button
                type="button"
                aria-pressed={viewMode === 'calendar'}
                className={`px-3 py-1 ${
                  viewMode === 'calendar'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-700 hover:bg-gray-50'
                }`}
                onClick={showCalendar}
              >
                Calendar
              </button>
            </div>
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
        {viewMode === 'list' || !calendarMonth ? (
          filtered.length === 0 ? (
            <div className="rounded border border-dashed border-gray-300 p-8 text-center text-sm text-gray-600">
              <p>No events match the current filters.</p>
              {activeFilterCount > 0 && (
                <button
                  type="button"
                  className="mt-2 text-blue-700 underline hover:text-blue-900"
                  onClick={clearAllFilters}
                >
                  Clear all filters
                </button>
              )}
            </div>
          ) : (
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
          )
        ) : (
          <CalendarView
            eventsByDay={eventsByDay}
            month={calendarMonth}
            onMonthChange={handleMonthChange}
            selectedDay={selectedDay}
            onSelectDay={setSelectedDay}
            minMonthKey={minMonthKey}
            maxMonthKey={maxMonthKey}
            selectedIds={selectedIds}
            onEventSelectedChange={setEventSelected}
          />
        )}
      </section>
    </div>
  );
}
