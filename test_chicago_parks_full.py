#!/usr/bin/env python3
"""
Test Chicago Parks scraper with aggressive pagination
"""
import asyncio
import os
import time
from dotenv import load_dotenv

# Import the Chicago Parks functions from the main script
from library_all_events import fetch_chicago_parks_events, logger

async def test_chicago_parks_full():
    """Test the Chicago Parks scraper with more aggressive pagination."""
    print("Testing Chicago Park District scraper with full pagination...")
    start_time = time.time()

    try:
        events = await fetch_chicago_parks_events()
        end_time = time.time()

        print(f"\n=== RESULTS ===")
        print(f"Found {len(events)} Chicago Park District events")
        print(f"Time taken: {end_time - start_time:.2f} seconds")

        if events:
            print(f"\n=== SAMPLE EVENTS ===")
            # Show events from different dates to see variety
            dates_seen = set()
            sample_count = 0

            for event in events:
                event_date = event.get('Date', '')
                if event_date not in dates_seen and sample_count < 5:
                    dates_seen.add(event_date)
                    sample_count += 1

                    print(f"\nEvent {sample_count}:")
                    print(f"  Title: {event.get('Title', 'N/A')}")
                    print(f"  Date: {event_date}")
                    print(f"  Time: {event.get('Time', 'N/A')}")
                    print(f"  Location: {event.get('Location', 'N/A')}")
                    print(f"  Age Group: {event.get('Age Group', 'N/A')}")

            # Show date distribution
            print(f"\n=== DATE DISTRIBUTION ===")
            date_counts = {}
            for event in events:
                date = event.get('Date', 'Unknown')
                date_counts[date] = date_counts.get(date, 0) + 1

            for date in sorted(date_counts.keys())[:10]:  # Show first 10 dates
                print(f"  {date}: {date_counts[date]} events")

            if len(date_counts) > 10:
                print(f"  ... and {len(date_counts) - 10} more dates")

    except Exception as e:
        print(f"Error testing Chicago Parks scraper: {e}")

if __name__ == "__main__":
    load_dotenv()
    asyncio.run(test_chicago_parks_full())