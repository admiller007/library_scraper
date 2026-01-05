#!/usr/bin/env python3
"""
Quick test script for Chicago Park District scraper functionality
"""
import asyncio
import os
from dotenv import load_dotenv

# Import the Chicago Parks functions from the main script
from library_all_events import fetch_chicago_parks_events

async def test_chicago_parks():
    """Test the Chicago Parks scraper."""
    print("Testing Chicago Park District scraper...")

    try:
        events = await fetch_chicago_parks_events()
        print(f"Found {len(events)} Chicago Park District events")

        # Print first few events if any found
        for i, event in enumerate(events[:3]):
            print(f"\nEvent {i+1}:")
            for key, value in event.items():
                print(f"  {key}: {value}")

    except Exception as e:
        print(f"Error testing Chicago Parks scraper: {e}")

if __name__ == "__main__":
    load_dotenv()
    asyncio.run(test_chicago_parks())