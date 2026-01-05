import requests
from bs4 import BeautifulSoup
import re
import logging
from typing import Dict

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def clean_text(text: str) -> str:
    if not text: return ""
    # Normalize whitespace
    return ' '.join(text.split()).strip()

def fetch_event_details(url: str) -> Dict[str, str]:
    logger.info(f"Testing URL: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'lxml')

        details = {"Description": "Not found", "Time": "Not found", "Date": "Not found"}

        # --- FIX 1: Find Date & Time (Sibling Strategy) ---
        # Find the text "Date and Time"
        date_label = soup.find(string=re.compile(r'Date and Time', re.IGNORECASE))
        
        if date_label:
            logger.info("Found 'Date and Time' label...")
            
            # The structure is typically:
            # <div class="label">Date and Time</div>
            # <div class="content">Dec 10...</div>
            # So we get the parent of the label text, then find its NEXT sibling.
            label_parent = date_label.parent
            content_container = label_parent.find_next_sibling()
            
            # If find_next_sibling fails, try the parent's parent text (the wrapper)
            full_text = ""
            if content_container:
                full_text = content_container.get_text(separator=" ", strip=True)
            else:
                full_text = label_parent.parent.get_text(separator=" ", strip=True)

            # Debug check what text we are working with
            # logger.info(f"Raw Date/Time text found: {full_text}")

            # 1. Extract Date (e.g., "Dec 10, 2025" or "December 10, 2025")
            # Looks for: Month (3+ letters) + Space + Day(1-2 digits) + Comma(optional) + Space + Year(4 digits)
            date_match = re.search(r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})', full_text)
            if date_match:
                details["Date"] = date_match.group(1)

            # 2. Extract Time (e.g., "4:00 PM - 6:00 PM")
            # Looks for: Time range or single time
            time_match = re.search(r'(\d{1,2}:\d{2}\s*[AP]M\s*[-–]\s*\d{1,2}:\d{2}\s*[AP]M)', full_text)
            if not time_match:
                # Fallback to single time if range not found
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)', full_text)
            
            if time_match:
                details["Time"] = time_match.group(0)

        # --- FIX 2: Find Description (Existing Logic) ---
        desc_label = soup.find(string=re.compile(r'^Description$|^About this Event$', re.IGNORECASE))
        
        if desc_label:
            logger.info("Found 'Description' label...")
            parent = desc_label.find_parent()
            next_elem = parent.find_next_sibling()
            
            if next_elem:
                details["Description"] = clean_text(next_elem.get_text())
            else:
                details["Description"] = clean_text(parent.get_text().replace(desc_label, ''))
        
        # Fallback for description
        if details["Description"] == "Not found":
            body = soup.find(class_=re.compile(r'field--name-body', re.IGNORECASE))
            if body:
                details["Description"] = clean_text(body.get_text())

        return details

    except Exception as e:
        logger.error(f"Error: {e}")
        return {}

if __name__ == "__main__":
    test_url = "https://www.chicagoparkdistrict.com/events/cocoa-santa-adams"
    
    print("-" * 50)
    result = fetch_event_details(test_url)
    
    print("\n--- EXTRACTION RESULTS ---")
    print(f"Date:        {result.get('Date')}")
    print(f"Time:        {result.get('Time')}")
    print(f"Description: {result.get('Description')}")
    print("-" * 50)
    
    if result.get('Date') != "Not found" and result.get('Time') != "Not found":
         print("✅ SUCCESS: Date and Time extracted.")
    else:
         print("❌ FAIL: Date or Time still missing.")