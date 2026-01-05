#!/usr/bin/env python3
"""
Analyze the Chicago Parks HTML structure in detail
"""
import asyncio
import aiohttp
import re
from bs4 import BeautifulSoup

async def analyze_chicago_parks():
    """Analyze Chicago Parks HTML structure."""
    url = "https://www.chicagoparkdistrict.com/events"

    print("Analyzing Chicago Park District events page structure...")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "LibraryScraper/1.0 (+https://github.com/)"}
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                html = await resp.text()

        print(f"Successfully fetched {len(html)} characters")

        # Parse with BeautifulSoup for better analysis
        soup = BeautifulSoup(html, 'html.parser')

        # Look for actual event listings
        event_containers = soup.find_all(['div', 'article', 'li'], class_=re.compile(r'(event|calendar)', re.I))
        print(f"Found {len(event_containers)} potential event containers")

        # Look for event-specific content
        event_links = soup.find_all('a', href=re.compile(r'/event', re.I))
        print(f"Found {len(event_links)} event links")

        if event_links:
            print("\nSample event links:")
            for i, link in enumerate(event_links[:5]):
                href = link.get('href', '')
                text = link.get_text(strip=True)
                print(f"  {i+1}. {text} -> {href}")

        # Look for date/time patterns
        date_patterns = soup.find_all(text=re.compile(r'\b(Dec|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov)\s+\d{1,2}\b'))
        print(f"\nFound {len(date_patterns)} potential dates")

        if date_patterns:
            print("Sample dates found:")
            for i, date_text in enumerate(date_patterns[:5]):
                print(f"  {i+1}. {date_text.strip()}")

        # Check for pagination or "No events" message
        pagination = soup.find_all(['nav', 'div'], class_=re.compile(r'pag', re.I))
        print(f"\nFound {len(pagination)} pagination elements")

        no_results = soup.find_all(text=re.compile(r'no\s+(events?|results?)', re.I))
        print(f"Found {len(no_results)} 'no events' messages")

        if no_results:
            for msg in no_results[:3]:
                print(f"  No events message: {msg.strip()}")

        # Look for JavaScript that might load events dynamically
        scripts = soup.find_all('script')
        ajax_scripts = [s for s in scripts if s.string and ('ajax' in s.string.lower() or 'fetch' in s.string.lower())]
        print(f"\nFound {len(ajax_scripts)} scripts that might load content dynamically")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(analyze_chicago_parks())