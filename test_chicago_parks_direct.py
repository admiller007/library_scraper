#!/usr/bin/env python3
"""
Alternative test using direct HTTP request to see Chicago Parks content
"""
import asyncio
import aiohttp
import re
from datetime import datetime

async def test_chicago_parks_direct():
    """Test Chicago Parks scraping with direct HTTP request."""
    url = "https://www.chicagoparkdistrict.com/events"

    print("Testing direct access to Chicago Park District events...")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "LibraryScraper/1.0 (+https://github.com/)"}
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                html = await resp.text()

        print(f"Successfully fetched {len(html)} characters from {url}")

        # Look for event patterns in the HTML
        # Try to find event containers
        event_patterns = [
            r'<li[^>]*class="[^"]*calendar-item[^"]*"[^>]*>(.*?)</li>',
            r'<div[^>]*class="[^"]*event[^"]*"[^>]*>(.*?)</div>',
            r'<article[^>]*class="[^"]*event[^"]*"[^>]*>(.*?)</article>',
        ]

        found_events = []
        for pattern in event_patterns:
            matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
            if matches:
                print(f"Found {len(matches)} potential events with pattern: {pattern[:50]}...")
                found_events.extend(matches[:3])  # Take first 3 examples
                break

        # Show sample content
        if found_events:
            print("\nSample event HTML content:")
            for i, event_html in enumerate(found_events[:2]):
                print(f"\n--- Event {i+1} ---")
                print(event_html[:500] + "..." if len(event_html) > 500 else event_html)
        else:
            print("\nNo events found with standard patterns. Showing sample HTML:")
            print(html[:1000] + "..." if len(html) > 1000 else html)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_chicago_parks_direct())