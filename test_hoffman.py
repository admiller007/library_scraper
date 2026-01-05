#!/usr/bin/env python3

import asyncio
import sys
import os
sys.path.append('.')

# Import the function we want to test
from library_all_events import fetch_hoffmanesates_events, START_DATE, DAYS_TO_FETCH

async def test_hoffman():
    """Test the Hoffman Estates library fetcher"""

    # Set global date variables (normally set in main())
    global START_DATE, DAYS_TO_FETCH

    if not START_DATE:
        from datetime import datetime
        START_DATE = datetime.now().strftime("%Y-%m-%d")

    if not DAYS_TO_FETCH:
        DAYS_TO_FETCH = 90

    print(f"Testing Hoffman Estates fetcher with date range: {START_DATE} for {DAYS_TO_FETCH} days")

    try:
        events = await fetch_hoffmanesates_events()
        print(f"\n✅ Success! Found {len(events)} events")

        if events:
            print("\nFirst few events:")
            for i, event in enumerate(events[:3]):
                print(f"\n--- Event {i+1} ---")
                for key, value in event.items():
                    print(f"{key}: {value}")
        else:
            print("No events found - this might be expected if no Hoffman events are currently listed")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_hoffman())