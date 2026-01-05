"""
Chicago Park District Events Scraper - Deep Scrape (List + Details)
"""
import requests
from bs4 import BeautifulSoup
import logging
from typing import List, Dict, Any
from datetime import datetime
import re
import time
import csv
import os

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CHICAGO_PARKS_URL = 'https://www.chicagoparkdistrict.com/events'
MAX_RETRIES = 3
RETRY_DELAY = 2
MAX_PAGES = 50 

def clean_text(text: str) -> str:
    """Clean and normalize text."""
    if not text:
        return ""
    text = ' '.join(text.split())
    text = ''.join(char for char in text if char.isprintable() or char.isspace())
    return text.strip()

def parse_date(date_str: str) -> str:
    if not date_str or date_str == "Not found": return "Not found"
    try:
        cleaned = re.sub(r'\s+', ' ', date_str).strip()
        # Handle "Dec 10" format by adding current year context if missing
        if re.match(r'^[A-Za-z]+\s+\d{1,2}$', cleaned):
            cleaned = f"{cleaned}, {datetime.now().year}"

        date_formats = ["%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%A, %B %d, %Y"]
        for fmt in date_formats:
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.strftime("%A, %B %d, %Y")
            except ValueError:
                continue
        return cleaned
    except Exception:
        return date_str

def fetch_event_details(session, url: str) -> Dict[str, str]:
    """
    Visits the specific event page to extract full description and time.
    """
    details = {"Description": "Not found", "Time": "Not found"}
    
    if not url or "http" not in url:
        return details

    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'lxml')

        # --- 1. Extract Description ---
        # Look for standard Drupal body fields
        desc_elem = soup.find(class_=re.compile(r'field--name-body|field-name-body|event-description', re.IGNORECASE))
        if desc_elem:
            details["Description"] = clean_text(desc_elem.get_text())
        else:
            # Fallback: Find the 'About this Event' or similar headers
            headers = soup.find_all(['h2', 'h3', 'h4'])
            for h in headers:
                if 'about' in h.get_text().lower() or 'description' in h.get_text().lower():
                    # Get the text immediately following the header
                    next_node = h.find_next_sibling()
                    if next_node:
                        details["Description"] = clean_text(next_node.get_text())
                        break

        # --- 2. Extract Specific Time ---
        # Look for date/time fields specifically on the detail page
        time_elem = soup.find(class_=re.compile(r'field--name-field-date-time|event-time|date-display-range', re.IGNORECASE))
        if time_elem:
            time_text = clean_text(time_elem.get_text())
            # Try to extract just the time part (e.g. "5:00 PM - 7:00 PM")
            time_match = re.search(r'(\d{1,2}:\d{2}\s*[APap][Mm].*)', time_text)
            if time_match:
                details["Time"] = time_match.group(1)
            else:
                details["Time"] = time_text

        return details

    except Exception as e:
        logger.warning(f"Failed to fetch details for {url}: {e}")
        return details

def save_to_csv(events: List[Dict[str, Any]]):
    if not events: return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"chicago_parks_events_detailed_{timestamp}.csv"
    try:
        keys = events[0].keys()
        with open(filename, 'w', newline='', encoding='utf-8-sig') as output_file:
            dict_writer = csv.DictWriter(output_file, fieldnames=keys)
            dict_writer.writeheader()
            dict_writer.writerows(events)
        logger.info(f"✓ Data successfully saved to: {os.path.abspath(filename)}")
    except IOError as e:
        logger.error(f"Error saving to CSV: {e}")

def fetch_chicago_parks_events() -> List[Dict[str, Any]]:
    logger.info("Fetching Chicago Park District events list...")
    
    # Use a session for better performance (connection pooling)
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })

    all_events = []
    page = 0
    
    # --- PHASE 1: Scrape the List ---
    while page < MAX_PAGES:
        url = f"{CHICAGO_PARKS_URL}?page={page}"
        logger.info(f"Scanning list page {page + 1}...")
        
        response = None
        for attempt in range(MAX_RETRIES):
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                break
            except requests.exceptions.RequestException:
                time.sleep(RETRY_DELAY)

        if not response: break

        soup = BeautifulSoup(response.content, 'lxml')
        event_items = soup.select('.view-content .views-row')

        if not event_items or soup.select('.view-empty'):
            logger.info(f"No more events found on page {page + 1}. Stopping list scan.")
            break

        valid_items_on_page = 0
        for item in event_items:
            try:
                # Basic Extraction
                title_elem = (item.find('h2') or item.find('h3') or item.find(class_=re.compile(r'title', re.IGNORECASE)) or item.find('a'))
                if not title_elem: continue

                title = clean_text(title_elem.get_text())
                if not title or len(title) < 3 or title.lower() == "here": continue
                
                if any(k in title.lower() for k in ['alert', 'closure', 'closed']): continue

                link_elem = item.find('a', href=True)
                link = link_elem.get('href', "N/A") if link_elem else "N/A"
                if "googleusercontent" in link or "maps.google" in link: continue

                if link and link != "N/A" and not link.startswith('http'):
                    link = f"https://www.chicagoparkdistrict.com{link}" if link.startswith('/') else f"https://www.chicagoparkdistrict.com/{link}"

                # Grab Date from list view (usually accurate enough)
                date_str = "Not found"
                time_elem = item.find('time', attrs={'datetime': True})
                if time_elem: date_str = time_elem.get('datetime', '')
                if not date_str:
                    date_elem = item.find(class_=re.compile(r'date|when|day', re.IGNORECASE))
                    if date_elem: date_str = clean_text(date_elem.get_text())
                
                date_str = parse_date(date_str)

                # Initialize with basic info
                all_events.append({
                    "Library": "Chicago Parks",
                    "Title": title,
                    "Date": date_str,
                    "Time": "Pending...", # Will update in Phase 2
                    "Location": "Chicago Park District", # Will update if possible
                    "Age Group": "All Ages",
                    "Program Type": "Recreation",
                    "Description": "Pending...", # Will update in Phase 2
                    "Link": link
                })
                valid_items_on_page += 1

            except Exception:
                continue

        if valid_items_on_page == 0:
            break
        
        page += 1
        time.sleep(1)

    logger.info(f"Found {len(all_events)} events. Starting deep scrape for details...")

    # --- PHASE 2: Fetch Details for Each Event ---
    for i, event in enumerate(all_events, 1):
        if event['Link'] == "N/A": continue
        
        logger.info(f"[{i}/{len(all_events)}] Fetching details: {event['Title'][:30]}...")
        
        details = fetch_event_details(session, event['Link'])
        
        # Update event with found details
        if details["Description"] != "Not found":
            event["Description"] = details["Description"]
        if details["Time"] != "Not found":
            event["Time"] = details["Time"]

        # Re-run classification based on new description
        full_text = f"{event['Title']} {event['Description']}".lower()
        
        # Age Group Logic
        if any(k in full_text for k in ['kid', 'child', 'family', 'youth', 'tot']): event["Age Group"] = "Kids/Family"
        elif any(k in full_text for k in ['teen', 'adolescent']): event["Age Group"] = "Teens"
        elif any(k in full_text for k in ['adult', 'senior', '55+']): event["Age Group"] = "Adults"

        # Program Type Logic
        if any(w in full_text for w in ['sport', 'fitness', 'swim', 'gym']): event["Program Type"] = "Sports/Fitness"
        elif any(w in full_text for w in ['art', 'craft', 'music', 'theater']): event["Program Type"] = "Arts/Culture"
        elif any(w in full_text for w in ['nature', 'garden', 'walk']): event["Program Type"] = "Nature/Outdoors"

        # Polite delay
        time.sleep(0.5)

    return all_events

if __name__ == "__main__":
    events = fetch_chicago_parks_events()
    
    print(f"\n{'='*80}")
    print(f"TOTAL EVENTS SCRAPED: {len(events)}")
    print(f"{'='*80}\n")
    
    save_to_csv(events)