// Local-safe date key helpers. Event days are identified by their
// 'yyyy-mm-dd' string; Date objects are only constructed via the local
// (year, monthIndex, day) form to avoid UTC off-by-one shifts.

export function dateKey(year: number, monthIndex: number, day: number): string {
  return `${year}-${String(monthIndex + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

export function parseKey(key: string): { year: number; monthIndex: number; day: number } {
  const [year, month, day] = key.split('-').map(Number);
  return { year, monthIndex: month - 1, day };
}

export function todayKey(): string {
  const now = new Date();
  return dateKey(now.getFullYear(), now.getMonth(), now.getDate());
}

export function addDaysKey(key: string, days: number): string {
  const { year, monthIndex, day } = parseKey(key);
  const d = new Date(year, monthIndex, day + days);
  return dateKey(d.getFullYear(), d.getMonth(), d.getDate());
}

export function monthKey(year: number, monthIndex: number): string {
  return `${year}-${String(monthIndex + 1).padStart(2, '0')}`;
}

export function monthLabel(year: number, monthIndex: number): string {
  return new Date(year, monthIndex, 1).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  });
}

export function dayLabel(key: string): string {
  const { year, monthIndex, day } = parseKey(key);
  return new Date(year, monthIndex, day).toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
}
