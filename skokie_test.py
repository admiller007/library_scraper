#!/usr/bin/env python3
"""
Simple test script to fetch and examine Skokie library raw markdown
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from firecrawl import AsyncFirecrawlApp
    import aiohttp
    import re
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Please install: pip install firecrawl-py aiohttp python-dotenv")
    exit(1)

load_dotenv()

FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY', 'fc-fe1ba845d9c748c1871061a8366dcd43')
SKOKIE_URL = "https://www.skokielibrary.info/events/list"

async def fetch_skokie_debug():
    """Fetch Skokie events and save raw markdown for analysis"""
    print("Fetching Skokie events for analysis...")

    if not FIRECRAWL_API_KEY:
        print("ERROR: FIRECRAWL_API_KEY not set")
        return

    app = AsyncFirecrawlApp(api_key=FIRECRAWL_API_KEY)

    try:
        response = await app.scrape_url(url=SKOKIE_URL, only_main_content=True)
        markdown = response.markdown

        # Save full markdown
        with open('skokie_full_debug.txt', 'w', encoding='utf-8') as f:
            f.write(f"=== SKOKIE FULL MARKDOWN ({len(markdown)} chars) ===\n\n")
            f.write(markdown)
            f.write("\n\n=== END FULL MARKDOWN ===")

        print(f"✅ Saved {len(markdown)} characters of Skokie markdown to skokie_full_debug.txt")

        # Also show first few events
        print("\n=== FIRST 1500 CHARACTERS ===")
        print(markdown[:1500])
        print("=== (see full file for complete data) ===")

    except Exception as e:
        print(f"❌ Error fetching Skokie data: {e}")

if __name__ == "__main__":
    asyncio.run(fetch_skokie_debug())
