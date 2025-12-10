"""
Chicago Park District Events Scraper using Beautiful Soup

This scraper fetches events from the Chicago Park District website
and extracts event information including title, date, time, location,
description, and links.
"""
import requests
from bs4 import BeautifulSoup
import logging
from typing import List, Dict, Any
from datetime import datetime
import re
import time
import json
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CHICAGO_PARKS_URL = 'https://www.chicagoparkdistrict.com/events'
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if not text:
        return ""
    # Remove extra whitespace and normalize
    text = ' '.join(text.split())
    # Remove non-printable characters
    text = ''.join(char for char in text if char.isprintable() or char.isspace())
    return text.strip()

def parse_date(date_str: str) -> str:
    """
    Parse various date formats and return standardized format.
    Handles formats like:
    - December 10, 2025
    - Dec 10, 2025
    - 12/10/2025
    - Monday, December 10, 2025
    """
    if not date_str or date_str == "Not found":
        return "Not found"

    try:
        # Try common date formats
        date_formats = [
            "%B %d, %Y",  # December 10, 2025
            "%b %d, %Y",   # Dec 10, 2025
            "%m/%d/%Y",    # 12/10/2025
            "%Y-%m-%d",    # 2025-12-10
            "%A, %B %d, %Y",  # Monday, December 10, 2025
        ]

        # Clean the date string
        cleaned = re.sub(r'\s+', ' ', date_str).strip()

        for fmt in date_formats:
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.strftime("%A, %B %d, %Y")
            except ValueError:
                continue

        # If no format matches, return original
        return cleaned
    except Exception as e:
        logger.debug(f"Error parsing date '{date_str}': {e}")
        return date_str

def fetch_chicago_parks_events() -> List[Dict[str, Any]]:
    """
    Scrape events from Chicago Park District using Beautiful Soup.
    Returns a list of event dictionaries in the standard format.

    The scraper attempts multiple strategies to find events:
    1. Look for Drupal views-row structure (common CMS pattern)
    2. Look for event/program class containers
    3. Look for article elements
    4. Look for specific data attributes

    Returns:
        List of event dictionaries with keys: Library, Title, Date, Time,
        Location, Age Group, Program Type, Description, Link
    """
    logger.info("Fetching Chicago Park District events...")

    # Use headers to appear as a regular browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    # Retry logic for network resilience
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"Attempt {attempt + 1}/{MAX_RETRIES} to fetch Chicago Parks events")
            response = requests.get(CHICAGO_PARKS_URL, headers=headers, timeout=30)
            response.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES - 1:
                logger.error(f"Failed to fetch Chicago Parks events after {MAX_RETRIES} attempts: {e}")
                return []
            logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

    # Parse with lxml for speed
    soup = BeautifulSoup(response.content, 'lxml')

    all_events = []

    # Strategy 1: Drupal views pattern (most common for Chicago Park District site)
    event_items = soup.select('.view-content .views-row')

    if not event_items:
        # Strategy 2: Look for event/program containers
        event_items = soup.find_all(['article', 'div'], class_=re.compile(r'event|program', re.IGNORECASE))

    if not event_items:
        # Strategy 3: Look for data attributes
        event_items = soup.find_all(['div', 'li', 'article'], attrs={'data-event': True})

    if not event_items:
        # Strategy 4: Generic article/event structure
        event_items = soup.find_all('article')

    logger.info(f"Found {len(event_items)} potential event items")

    if not event_items:
        logger.warning("No event items found. Page structure may have changed.")
        # Log a sample of the HTML for debugging
        logger.debug(f"Page title: {soup.title.string if soup.title else 'No title'}")
        logger.debug(f"Sample HTML classes: {[tag.get('class') for tag in soup.find_all(['div', 'article'])[:5]]}")

    for idx, item in enumerate(event_items):
        try:
            # Extract title - try multiple strategies
            title_elem = (
                item.find('h2') or
                item.find('h3') or
                item.find('h4') or
                item.find(class_=re.compile(r'title', re.IGNORECASE)) or
                item.find('a')
            )

            if not title_elem:
                logger.debug(f"Skipping item {idx} - no title found")
                continue

            title = clean_text(title_elem.get_text())
            if not title or len(title) < 3:
                logger.debug(f"Skipping item {idx} - title too short: '{title}'")
                continue

            # Skip non-events (alerts, closures, etc.)
            skip_keywords = ['alert', 'closure', 'closed', 'trail is closed', 'facility closed']
            if any(keyword in title.lower() for keyword in skip_keywords):
                logger.debug(f"Skipping non-event: '{title}'")
                continue

            # Extract link
            link_elem = item.find('a', href=True)
            link = link_elem.get('href', "N/A") if link_elem else "N/A"
            if link and link != "N/A" and not link.startswith('http'):
                # Handle relative URLs
                if link.startswith('/'):
                    link = f"https://www.chicagoparkdistrict.com{link}"
                else:
                    link = f"https://www.chicagoparkdistrict.com/{link}"

            # Extract date - try multiple approaches
            date_str = "Not found"

            # Look for time element with datetime attribute
            time_elem = item.find('time', attrs={'datetime': True})
            if time_elem:
                date_str = time_elem.get('datetime', '')
                # Also try the text content if datetime is empty
                if not date_str:
                    date_str = clean_text(time_elem.get_text())

            # If no time element, look for date classes
            if date_str == "Not found" or not date_str:
                date_elem = item.find(class_=re.compile(r'date|when|day', re.IGNORECASE))
                if date_elem:
                    date_str = clean_text(date_elem.get_text())

            # Also check for spans with date-related text
            if date_str == "Not found" or not date_str:
                for span in item.find_all('span'):
                    text = clean_text(span.get_text())
                    # Look for month patterns
                    if re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b', text, re.IGNORECASE):
                        date_str = text
                        break

            # Try to expand abbreviated dates like "Dec 10" to full date with year
            if date_str and date_str != "Not found":
                # If we have something like "Dec 10" without a year, add current year
                if re.match(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}$', date_str, re.IGNORECASE):
                    current_year = datetime.now().year
                    date_str = f"{date_str}, {current_year}"

                # Parse and standardize the date
                date_str = parse_date(date_str)

            # Extract time
            time_str = "Not found"

            # Look for time in class names
            time_elem = item.find(class_=re.compile(r'time|hour', re.IGNORECASE))
            if time_elem:
                time_text = clean_text(time_elem.get_text())
                # Extract time patterns like "10:00 AM - 2:00 PM" or "10am-2pm"
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[ap]m\s*[-–]\s*\d{1,2}:\d{2}\s*[ap]m|\d{1,2}\s*[ap]m\s*[-–]\s*\d{1,2}\s*[ap]m)', time_text, re.IGNORECASE)
                if time_match:
                    time_str = time_match.group(1)
                elif re.search(r'\d{1,2}:\d{2}\s*[ap]m', time_text, re.IGNORECASE):
                    time_str = time_text

            # If not found, search all text in the item for time patterns
            if time_str == "Not found":
                item_text = clean_text(item.get_text())
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[ap]m\s*[-–]\s*\d{1,2}:\d{2}\s*[ap]m|\d{1,2}\s*[ap]m\s*[-–]\s*\d{1,2}\s*[ap]m)', item_text, re.IGNORECASE)
                if time_match:
                    time_str = time_match.group(1)

            # If not found in dedicated time element, try to extract from date string
            if time_str == "Not found" and date_str != "Not found":
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[ap]m\s*[-–]\s*\d{1,2}:\d{2}\s*[ap]m)', date_str, re.IGNORECASE)
                if time_match:
                    time_str = time_match.group(1)
                    # Remove time from date string
                    date_str = date_str.replace(time_str, '').strip()
                    date_str = parse_date(date_str)

            # Extract location/venue
            location_str = "Chicago Park District"
            location_elem = item.find(class_=re.compile(r'location|venue|park|where', re.IGNORECASE))
            if location_elem:
                location_text = clean_text(location_elem.get_text())
                if location_text and location_text.lower() not in ['location', 'venue', 'where']:
                    location_str = location_text

            # Extract description
            description = "Not found"
            # Look for description, summary, or body classes
            desc_elem = (
                item.find(class_=re.compile(r'description|summary|body|excerpt|teaser|details', re.IGNORECASE)) or
                item.find('p')
            )
            if desc_elem:
                description = clean_text(desc_elem.get_text())

            # If no description found, try to extract from item text
            if description == "Not found" or not description:
                # Get all paragraphs
                paragraphs = item.find_all('p')
                if paragraphs:
                    # Combine paragraph text
                    desc_parts = [clean_text(p.get_text()) for p in paragraphs if clean_text(p.get_text())]
                    if desc_parts:
                        description = ' '.join(desc_parts)

            # If still not found, try to get a snippet from item text (excluding title and location)
            if description == "Not found" or not description:
                item_text = clean_text(item.get_text())
                # Remove title and location from text
                snippet = item_text.replace(title, '').replace(location_str, '')
                # Remove date and time patterns
                snippet = re.sub(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}', '', snippet, flags=re.IGNORECASE)
                snippet = re.sub(r'\d{1,2}:\d{2}\s*[ap]m', '', snippet, flags=re.IGNORECASE)
                snippet = clean_text(snippet)
                if snippet and len(snippet) > 20:
                    description = snippet

            # Truncate very long descriptions
            if description and len(description) > 500:
                description = description[:497] + "..."

            # Determine age group based on content
            age_group = "All Ages"
            text_content = f"{title} {description}".lower()

            # Check for kid-specific keywords
            kid_keywords = ['kid', 'child', 'family', 'youth', 'junior', 'ages 5-12', 'elementary']
            if any(keyword in text_content for keyword in kid_keywords):
                age_group = "Kids/Family"

            # Check for teen keywords
            teen_keywords = ['teen', 'adolescent', 'ages 13-17', 'middle school', 'high school']
            if any(keyword in text_content for keyword in teen_keywords):
                age_group = "Teens"

            # Check for adult keywords
            adult_keywords = ['adult', 'senior', '55+', '18+', '21+']
            if any(keyword in text_content for keyword in adult_keywords):
                age_group = "Adults"

            # Determine program type
            program_type = "Recreation"
            if any(word in text_content for word in ['sport', 'athletic', 'fitness', 'swim', 'basketball']):
                program_type = "Sports/Fitness"
            elif any(word in text_content for word in ['art', 'craft', 'paint', 'draw', 'music']):
                program_type = "Arts/Culture"
            elif any(word in text_content for word in ['nature', 'garden', 'environment', 'outdoor']):
                program_type = "Nature/Outdoors"

            all_events.append({
                "Library": "Chicago Parks",
                "Title": title,
                "Date": date_str,
                "Time": time_str,
                "Location": location_str,
                "Age Group": age_group,
                "Program Type": program_type,
                "Description": description,
                "Link": link
            })

        except Exception as e:
            logger.warning(f"Error processing Chicago Parks event item {idx}: {e}")
            continue

    logger.info(f"Successfully parsed {len(all_events)} events from Chicago Parks")
    return all_events

if __name__ == "__main__":
    events = fetch_chicago_parks_events()

    print(f"\n{'='*60}")
    print(f"Scraped {len(events)} total events from Chicago Park District")
    print(f"{'='*60}\n")

    if events:
        # Save to JSON
        json_filename = 'chicago_parks_events.json'
        with open(json_filename, 'w', encoding='utf-8') as f:
            json.dump(events, f, indent=2, ensure_ascii=False)
        print(f"✓ Saved events to {json_filename}")

        # Save to CSV
        csv_filename = 'chicago_parks_events.csv'
        df = pd.DataFrame(events)
        df.to_csv(csv_filename, index=False, encoding='utf-8')
        print(f"✓ Saved events to {csv_filename}")

        # Print summary statistics
        print(f"\n{'='*60}")
        print("SUMMARY STATISTICS")
        print(f"{'='*60}")
        print(f"Total events: {len(events)}")
        print(f"\nEvents by Age Group:")
        print(df['Age Group'].value_counts().to_string())
        print(f"\nEvents by Program Type:")
        print(df['Program Type'].value_counts().to_string())
        print(f"\nEvents with valid dates: {df[df['Date'] != 'Not found'].shape[0]}")
        print(f"Events with valid times: {df[df['Time'] != 'Not found'].shape[0]}")

        # Print first 5 events as preview
        print(f"\n{'='*60}")
        print("PREVIEW - First 5 Events:")
        print(f"{'='*60}")
        for event in events[:5]:
            print(f"\n{event['Title']}")
            print(f"  Date: {event['Date']}")
            print(f"  Time: {event['Time']}")
            print(f"  Location: {event['Location']}")
            print(f"  Age Group: {event['Age Group']}")
            print(f"  Program Type: {event['Program Type']}")
            if event['Description'] and len(event['Description']) > 100:
                print(f"  Description: {event['Description'][:100]}...")
            else:
                print(f"  Description: {event['Description']}")
            print(f"  Link: {event['Link']}")
    else:
        print("⚠ No events were scraped. Check the logs above for errors.")
