import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import "./globals.css";
import { getLatestScrapeRun } from "@/lib/events";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Chicago Library Events",
  description: "Daily-refreshed children's programming from Chicago-area libraries.",
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

async function LastRefreshed() {
  const run = await getLatestScrapeRun();
  const label = run?.finished_at
    ? `Updated ${timeAgo(run.finished_at)}`
    : run?.status === 'running'
      ? 'Refreshing…'
      : 'Refresh pending';
  return <span className="text-gray-600">{label}</span>;
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-gray-50 text-gray-900">
        <header className="border-b bg-white">
          <div className="max-w-6xl mx-auto p-4 flex items-center justify-between text-sm">
            <span className="font-semibold">Library Events</span>
            <div className="flex items-center gap-4">
              <a href="/api/export/ics" className="text-blue-600 hover:underline">
                ICS
              </a>
              <a href="/api/export/pdf" className="text-blue-600 hover:underline">
                PDF
              </a>
              <Suspense fallback={<span className="text-gray-400">…</span>}>
                <LastRefreshed />
              </Suspense>
            </div>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
