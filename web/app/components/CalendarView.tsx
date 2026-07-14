'use client';
import { useMemo } from 'react';
import type { EventRow } from '@/lib/supabase';
import { dateKey, dayLabel, monthKey, monthLabel, todayKey } from '@/lib/dates';
import { EventCard } from './EventCard';

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MAX_CHIPS = 2;

type Month = { year: number; month: number };

type Props = {
  eventsByDay: Map<string, EventRow[]>;
  month: Month;
  onMonthChange: (month: Month) => void;
  selectedDay: string | null;
  onSelectDay: (day: string) => void;
  minMonthKey: string;
  maxMonthKey: string;
  selectedIds: Set<string>;
  onEventSelectedChange: (id: string, selected: boolean) => void;
};

export function CalendarView({
  eventsByDay,
  month,
  onMonthChange,
  selectedDay,
  onSelectDay,
  minMonthKey,
  maxMonthKey,
  selectedIds,
  onEventSelectedChange,
}: Props) {
  const { year, month: monthIndex } = month;
  const displayedMonthKey = monthKey(year, monthIndex);
  const today = todayKey();

  const leadingBlanks = new Date(year, monthIndex, 1).getDay();
  const daysInMonth = new Date(year, monthIndex + 1, 0).getDate();
  const trailingBlanks = (7 - ((leadingBlanks + daysInMonth) % 7)) % 7;

  const monthHasEvents = useMemo(() => {
    for (let d = 1; d <= daysInMonth; d++) {
      if (eventsByDay.has(dateKey(year, monthIndex, d))) return true;
    }
    return false;
  }, [eventsByDay, year, monthIndex, daysInMonth]);

  const navigate = (delta: number) => {
    const next = new Date(year, monthIndex + delta, 1);
    onMonthChange({ year: next.getFullYear(), month: next.getMonth() });
  };

  const dayEvents = selectedDay ? eventsByDay.get(selectedDay) ?? [] : [];

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300 disabled:hover:bg-transparent"
          onClick={() => navigate(-1)}
          disabled={displayedMonthKey <= minMonthKey}
          aria-label="Previous month"
        >
          &larr;
        </button>
        <h2 className="text-base font-semibold text-gray-900">
          {monthLabel(year, monthIndex)}
        </h2>
        <button
          type="button"
          className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300 disabled:hover:bg-transparent"
          onClick={() => navigate(1)}
          disabled={displayedMonthKey >= maxMonthKey}
          aria-label="Next month"
        >
          &rarr;
        </button>
      </div>
      <div className="grid grid-cols-7 text-center text-xs font-medium text-gray-500">
        {WEEKDAYS.map((d) => (
          <div key={d} className="py-1">
            {d}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-gray-200 bg-gray-200">
        {Array.from({ length: leadingBlanks }, (_, i) => (
          <div key={`lead-${i}`} className="bg-gray-50" />
        ))}
        {Array.from({ length: daysInMonth }, (_, i) => {
          const day = i + 1;
          const key = dateKey(year, monthIndex, day);
          const events = eventsByDay.get(key) ?? [];
          const isToday = key === today;
          const isSelected = key === selectedDay;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onSelectDay(key)}
              aria-pressed={isSelected}
              aria-label={`${dayLabel(key)}, ${events.length} events`}
              className={`min-h-14 p-1 text-left align-top focus-visible:ring-2 focus-visible:ring-blue-400 md:min-h-28 ${
                isSelected
                  ? 'bg-blue-50 ring-1 ring-inset ring-blue-300'
                  : events.length
                    ? 'bg-white hover:bg-gray-50'
                    : 'bg-gray-50 hover:bg-gray-100'
              }`}
            >
              <span
                className={
                  isToday
                    ? 'inline-flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 text-xs font-medium text-white'
                    : `text-xs ${events.length ? 'text-gray-700' : 'text-gray-400'}`
                }
              >
                {day}
              </span>
              {events.length > 0 && (
                <>
                  <div className="mt-1 hidden space-y-0.5 md:block">
                    {events.slice(0, MAX_CHIPS).map((e) => (
                      <div
                        key={e.id}
                        className="truncate rounded bg-blue-100 px-1 text-[11px] leading-4 text-blue-800"
                      >
                        {e.title}
                      </div>
                    ))}
                    {events.length > MAX_CHIPS && (
                      <div className="text-[11px] text-gray-500">
                        +{events.length - MAX_CHIPS} more
                      </div>
                    )}
                  </div>
                  <div className="mt-1 md:hidden">
                    <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-600 px-1 text-[10px] text-white">
                      {events.length}
                    </span>
                  </div>
                </>
              )}
            </button>
          );
        })}
        {Array.from({ length: trailingBlanks }, (_, i) => (
          <div key={`trail-${i}`} className="bg-gray-50" />
        ))}
      </div>
      {!monthHasEvents && (
        <p className="mt-3 text-sm text-gray-500">
          No events match your filters in {monthLabel(year, monthIndex)}.
        </p>
      )}
      {selectedDay && (
        <section className="mt-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {dayLabel(selectedDay)}
            {dayEvents.length > 0 ? ` · ${dayEvents.length} events` : ''}
          </h2>
          {dayEvents.length ? (
            <div className="mt-2 grid grid-cols-1 gap-4 md:grid-cols-2">
              {dayEvents.map((e) => (
                <EventCard
                  key={e.id}
                  event={e}
                  selected={selectedIds.has(e.id)}
                  onSelectedChange={(selected) => onEventSelectedChange(e.id, selected)}
                />
              ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-gray-500">
              No events on this day match your filters.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
