import { getUpcomingEvents } from '@/lib/events';
import { EventList } from './components/EventList';

export default async function Page() {
  const events = await getUpcomingEvents();
  return (
    <main className="max-w-6xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-4">Chicago Library Events</h1>
      <EventList events={events} />
    </main>
  );
}
