'use client';

import { useEffect, useState } from 'react';

type Status = 'running' | 'success' | 'failed' | null;

type Props = {
  finishedAt: string | null;
  status: Status;
};

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const hrs = Math.floor(diffMs / 3_600_000);
  if (hrs < 1) {
    const mins = Math.max(1, Math.floor(diffMs / 60_000));
    return `${mins}m ago`;
  }
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function LastRefreshed({ finishedAt, status }: Props) {
  // Seed from the server-rendered (cached) props for a fast first paint, then
  // replace with the live value so the stamp reflects the real latest run even
  // when the page HTML itself is served from cache.
  const [live, setLive] = useState<{ finishedAt: string | null; status: Status }>({
    finishedAt,
    status,
  });
  const [relativeTime, setRelativeTime] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const res = await fetch('/api/last-refresh', { cache: 'no-store' });
        if (!res.ok || !active) return;
        const data = (await res.json()) as { finishedAt: string | null; status: Status };
        setLive({ finishedAt: data.finishedAt ?? null, status: data.status ?? null });
      } catch {
        // Network hiccup — keep the last known value.
      }
    };
    refresh();
    const id = window.setInterval(refresh, 60_000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!live.finishedAt) {
      setRelativeTime(null);
      return;
    }
    const iso = live.finishedAt;
    const update = () => setRelativeTime(timeAgo(iso));
    update();
    const id = window.setInterval(update, 60_000);
    return () => window.clearInterval(id);
  }, [live.finishedAt]);

  const label = live.finishedAt
    ? `Updated ${relativeTime ?? ''}`.trim()
    : live.status === 'running'
      ? 'Refreshing...'
      : 'Refresh pending';

  return <span className="text-gray-600">{label}</span>;
}
