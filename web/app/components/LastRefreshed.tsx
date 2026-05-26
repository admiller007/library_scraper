'use client';

import { useEffect, useState } from 'react';

type Props = {
  finishedAt: string | null;
  status: 'running' | 'success' | 'failed' | null;
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
  const [relativeTime, setRelativeTime] = useState<string | null>(null);

  useEffect(() => {
    if (!finishedAt) return;
    const update = () => setRelativeTime(timeAgo(finishedAt));
    update();
    const id = window.setInterval(update, 60_000);
    return () => window.clearInterval(id);
  }, [finishedAt]);

  const label = finishedAt
    ? `Updated ${relativeTime ?? ''}`.trim()
    : status === 'running'
      ? 'Refreshing...'
      : 'Refresh pending';

  return <span className="text-gray-600">{label}</span>;
}
