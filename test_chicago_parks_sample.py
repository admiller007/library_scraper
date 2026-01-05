#!/usr/bin/env python3
"""
Test Chicago Parks scraper with targeted sampling
"""
import asyncio
import os
import time
from dotenv import load_dotenv

# Import the Chicago Parks functions from the main script
from library_all_events import _fetch_chicago_parks_content, parse_chicago_parks_markdown, AsyncFirecrawl, FIRECRAWL_API_KEY, logger

async def test_chicago_parks_sample():
    """Test sampling across multiple pages to estimate total."""
    print("Testing Chicago Park District scraper with strategic sampling...")

    if not FIRECRAWL_API_KEY:
        print("FIRECRAWL_API_KEY not set")
        return

    app = AsyncFirecrawl(api_key=FIRECRAWL_API_KEY)

    # Sample pages strategically: early, middle, and later pages
    sample_pages = [1, 5, 10, 15, 20, 25]
    total_sampled = 0
    successful_pages = 0

    print(f"Sampling pages: {sample_pages}")

    for page in sample_pages:
        try:
            print(f"\nFetching page {page}...")
            markdown = await _fetch_chicago_parks_content(app, page)

            if not markdown:
                print(f"  No content found for page {page}")
                break

            events = parse_chicago_parks_markdown(markdown)
            events_count = len(events)
            total_sampled += events_count
            successful_pages += 1

            print(f"  Found {events_count} events on page {page}")

            if events_count == 0:
                print(f"  Page {page} appears to be past the end of events")
                break

            # Show sample event from this page
            if events:
                sample_event = events[0]
                print(f"  Sample: '{sample_event.get('Title', 'N/A')}' on {sample_event.get('Date', 'N/A')}")

            await asyncio.sleep(4)  # Longer delay to avoid rate limiting

        except Exception as e:
            print(f"  Error on page {page}: {e}")
            if "429" in str(e):
                print(f"  Rate limited - stopping sampling")
                break
            continue

    print(f"\n=== SAMPLING RESULTS ===")
    print(f"Successfully sampled {successful_pages} pages")
    print(f"Total events found in sample: {total_sampled}")

    if successful_pages > 0:
        avg_per_page = total_sampled / successful_pages
        print(f"Average events per page: {avg_per_page:.1f}")
        estimated_total = avg_per_page * 27  # Assuming 27 total pages
        print(f"Estimated total events across 27 pages: {estimated_total:.0f}")

if __name__ == "__main__":
    load_dotenv()
    asyncio.run(test_chicago_parks_sample())