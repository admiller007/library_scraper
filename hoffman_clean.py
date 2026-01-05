async def fetch_hoffmanesates_events() -> List[Dict[str, Any]]:
    """
    Fetch Hoffman Estates Public Library events via Firecrawl web scraping.
    Uses Firecrawl to bypass anti-bot protection on Schaumburg Library District website.
    """
    logger.info(f"Fetching {HOFFMANESATES_LIBRARY_NAME} events (ALL EVENTS)...")

    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY not set; skipping Hoffman Estates fetch")
        return []

    app = AsyncFirecrawl(api_key=FIRECRAWL_API_KEY)
    events_url = f"https://www.{HOFFMANESATES_BASE_URL}/events"

    try:
        response = await retry_with_backoff(firecrawl_scrape, app, url=events_url)
        markdown = response.markdown if hasattr(response, "markdown") else ""
        if not markdown:
            logger.warning("No markdown content received from Hoffman Estates")
            return []
    except ValueError as e:
        logger.error(f"Invalid response from Hoffman Estates API: {e}")
        return []
    except ConnectionError as e:
        logger.error(f"Connection error while fetching Hoffman Estates events: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching Hoffman Estates events: {e}", exc_info=True)
        return []

    events = []

    try:
        # Parse events from markdown content based on the actual website structure
        lines = markdown.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines and navigation/header content
            if (not line or line.startswith('#') or line.startswith('*') or
                'Skip to main content' in line or 'My Account' in line or
                'Search' in line or 'Menu' in line or 'Footer' in line or
                any(skip in line.lower() for skip in ['facebook', 'twitter', 'instagram', 'youtube',
                                                     'main navigation', 'secondary menu', 'powered by',
                                                     'select language', 'library catalog', 'newsletter'])):
                i += 1
                continue

            # Look for event titles that match the pattern: "Event Name - age info with caregiver"
            # Example: "Baby Play - 0-18 months with caregiver"
            if (' - ' in line and any(age_word in line.lower() for age_word in
                                    ['months', 'years', 'caregiver', 'adults', 'kids', 'teens', 'ages', 'grade'])):

                title = clean_text(line)

                # Look ahead for the date/time/location line
                date_str = "Not found"
                time_str = "Not found"
                age_group = "All ages"
                location = f"{HOFFMANESATES_LIBRARY_NAME} Branch"

                # Check next few lines for the pattern: "Thu Dec 11 • 11 a.m. - Noon • Kids • Hoffman Estates Branch"
                for j in range(i+1, min(i+5, len(lines))):
                    next_line = lines[j].strip()

                    if ('•' in next_line and
                        any(day in next_line[:15] for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])):

                        parts = [p.strip() for p in next_line.split('•')]

                        if len(parts) >= 2:
                            # First part is the date: "Thu Dec 11"
                            date_str = parts[0]

                            # Second part is the time: "11 a.m. - Noon"
                            time_str = parts[1]

                            # Extract age group if present
                            if len(parts) >= 3:
                                for part in parts[2:]:
                                    if any(age in part.lower() for age in ['kids', 'teens', 'adults', 'family']):
                                        age_group = part
                                        break

                            # Extract location if present
                            if len(parts) >= 4:
                                for part in parts[2:]:
                                    if 'hoffman' in part.lower() or 'branch' in part.lower():
                                        location = part
                                        break
                        break

                # Only include events that are actually at Hoffman Estates
                if ('hoffman' in title.lower() or 'hoffman' in location.lower() or
                    date_str != "Not found"):
                    events.append({
                        "Library": HOFFMANESATES_LIBRARY_NAME,
                        "Title": title,
                        "Date": date_str,
                        "Time": time_str,
                        "Location": location,
                        "Age Group": age_group,
                        "Program Type": "Library Program",
                        "Description": "",
                        "Link": events_url
                    })

            i += 1

    except Exception as e:
        logger.error(f"Error parsing Hoffman Estates markdown: {e}")
        return []

    logger.info(f"Found {len(events)} events for {HOFFMANESATES_LIBRARY_NAME}")
    return events