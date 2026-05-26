import { NextResponse } from 'next/server';
import { connection } from 'next/server';
import {
  renderToBuffer,
  Document,
  Page,
  Text,
  View,
  StyleSheet,
} from '@react-pdf/renderer';
import React from 'react';
import { getUpcomingEvents } from '@/lib/events';

const styles = StyleSheet.create({
  page: { padding: 32, fontSize: 11 },
  h1: { fontSize: 18, marginBottom: 12 },
  event: { marginBottom: 10, paddingBottom: 6, borderBottom: '1px solid #ccc' },
  title: { fontWeight: 700, fontSize: 12 },
  meta: { color: '#555', marginTop: 2 },
});

export async function GET() {
  await connection();
  const events = await getUpcomingEvents();
  const doc = React.createElement(
    Document,
    null,
    React.createElement(
      Page,
      { size: 'LETTER', style: styles.page },
      React.createElement(Text, { style: styles.h1 }, 'Chicago Library Events'),
      ...events.map((e) =>
        React.createElement(
          View,
          { key: e.id, style: styles.event },
          React.createElement(Text, { style: styles.title }, e.title),
          React.createElement(
            Text,
            { style: styles.meta },
            `${e.library} • ${e.event_date} • ${e.event_time}` +
              (e.location ? ` • ${e.location}` : ''),
          ),
          e.description
            ? React.createElement(Text, { style: styles.meta }, e.description)
            : null,
        ),
      ),
    ),
  );
  const buf = await renderToBuffer(doc);
  return new NextResponse(buf as unknown as BodyInit, {
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': 'attachment; filename="library-events.pdf"',
    },
  });
}
