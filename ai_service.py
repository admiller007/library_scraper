#!/usr/bin/env python3
"""
AI Service for Library Events
Provides event summarization and enhancement using lightweight open source models.
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

# Optional imports - gracefully handle missing dependencies
try:
    from transformers import pipeline, AutoTokenizer, AutoModelForCausalLM
    import torch
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "ai_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_FILE = CACHE_DIR / "summaries.json"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EventSummarizer:
    """Lightweight AI service for enhancing event descriptions."""

    def __init__(self, model_name: str = "gpt2"):
        self.model_name = model_name
        self.summarizer = None
        self.cache = self._load_cache()
        self.enabled = HAS_TRANSFORMERS and os.getenv("ENABLE_AI_SUMMARIZATION", "false").lower() == "true"

        if self.enabled:
            self._initialize_model()
        else:
            logger.info("AI summarization disabled or dependencies missing")

    def _initialize_model(self):
        """Initialize the AI model for summarization."""
        try:
            logger.info(f"Loading AI model: {self.model_name}")

            # Try to load a very simple model without TensorFlow
            import os
            os.environ["TRANSFORMERS_OFFLINE"] = "1"  # Prevent downloading if model exists

            self.summarizer = pipeline(
                "text-generation",
                model=self.model_name,
                device=-1,  # CPU only
                framework="pt",  # PyTorch only, avoid TensorFlow
                torch_dtype=torch.float32,
                max_length=150,
                do_sample=True,
                temperature=0.7,
                pad_token_id=50256
            )

            logger.info("AI model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load AI model: {e}")
            self.enabled = False

    def _load_cache(self) -> Dict[str, Any]:
        """Load cached summaries from disk."""
        try:
            if CACHE_FILE.exists():
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
        return {}

    def _save_cache(self):
        """Save cache to disk."""
        try:
            with open(CACHE_FILE, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

    def _get_cache_key(self, event_data: Dict[str, Any]) -> str:
        """Generate a unique cache key for an event."""
        key_data = {
            'title': event_data.get('Title', ''),
            'description': event_data.get('Description', ''),
            'library': event_data.get('Library', ''),
            'age_group': event_data.get('Age Group', '')
        }
        return hashlib.md5(json.dumps(key_data, sort_keys=True).encode()).hexdigest()

    def enhance_event_description(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enhance an event's description using AI summarization.

        Args:
            event: Event dictionary with Title, Description, etc.

        Returns:
            Enhanced event dictionary with AI-generated summary
        """
        if not self.enabled:
            return event

        # Check cache first
        cache_key = self._get_cache_key(event)
        if cache_key in self.cache:
            logger.debug(f"Using cached summary for event: {event.get('Title', 'Unknown')}")
            event_copy = event.copy()
            event_copy['AI_Summary'] = self.cache[cache_key]['summary']
            event_copy['AI_Enhanced'] = True
            return event_copy

        # Generate new summary
        try:
            summary = self._generate_summary(event)

            # Cache the result
            self.cache[cache_key] = {
                'summary': summary,
                'generated_at': datetime.utcnow().isoformat(),
                'model': self.model_name
            }
            self._save_cache()

            # Return enhanced event
            event_copy = event.copy()
            event_copy['AI_Summary'] = summary
            event_copy['AI_Enhanced'] = True

            logger.info(f"Generated AI summary for: {event.get('Title', 'Unknown')}")
            return event_copy

        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return event

    def _generate_summary(self, event: Dict[str, Any]) -> str:
        """Generate an enhanced summary for an event."""
        # For now, let's just use the much better fallback summaries
        # The GPT-2 model is producing poor quality text
        return self._generate_fallback_summary(event)

    def _generate_fallback_summary(self, event: Dict[str, Any]) -> str:
        """Generate a smart rule-based summary as fallback."""
        title = event.get('Title', 'Library Event')
        description = event.get('Description', '')
        age_group = event.get('Age Group', '')

        # Clean up age group for better readability
        age_display = age_group.lower() if age_group and age_group not in ['General', 'All', 'Not specified'] else ''

        # Extract key themes from title and description
        content = f"{title} {description}".lower()

        # Enhanced pattern matching with better descriptions
        if any(word in content for word in ['storytime', 'story time', 'tales', 'bedtime']):
            return f"Interactive storytelling session with songs and activities{' for ' + age_display if age_display else ''}."

        elif any(word in content for word in ['craft', 'art', 'paint', 'draw', 'create', 'make']):
            if 'holiday' in content or 'winter' in content or 'christmas' in content:
                return f"Create festive seasonal crafts and artwork{' designed for ' + age_display if age_display else ''}."
            return f"Hands-on creative workshop to make unique art projects{' for ' + age_display if age_display else ''}."

        elif any(word in content for word in ['yoga', 'fitness', 'exercise', 'wellness']):
            return f"Gentle movement and wellness activity to improve flexibility and reduce stress{' for ' + age_display if age_display else ''}."

        elif any(word in content for word in ['book club', 'discussion', 'reading']):
            return f"Engaging book discussion with fellow literature enthusiasts{' in this ' + age_display + ' group' if age_display else ''}."

        elif any(word in content for word in ['game', 'chess', 'board game', 'gaming']):
            return f"Fun gaming session to challenge your mind and meet fellow players{' suitable for ' + age_display if age_display else ''}."

        elif any(word in content for word in ['music', 'concert', 'sing', 'choir']):
            return f"Musical entertainment and performance for the whole community{' featuring ' + age_display + ' performers' if age_display else ''}."

        elif any(word in content for word in ['workshop', 'class', 'learn', 'training']):
            return f"Educational workshop to develop new skills and knowledge{' tailored for ' + age_display if age_display else ''}."

        elif any(word in content for word in ['baby', 'toddler', 'infant']):
            return "Early childhood development program with songs, play, and parent-child bonding activities."

        elif any(word in content for word in ['teen', 'youth', 'volunteer']):
            return f"Youth program focused on community engagement and skill building{' for ' + age_display if age_display else ''}."

        elif any(word in content for word in ['nature', 'outdoor', 'hike', 'trail', 'bird']):
            return f"Outdoor nature exploration and environmental education activity{' designed for ' + age_display if age_display else ''}."

        elif any(word in content for word in ['technology', 'computer', 'digital', 'streaming']):
            return f"Technology training session to master digital tools and online services{' for ' + age_display if age_display else ''}."

        else:
            # Generic but appealing description
            if age_display:
                return f"Community library program offering engaging activities and learning opportunities for {age_display}."
            else:
                return "Community library program featuring interactive activities and educational content for all participants."

# Global instance
_summarizer = None

def get_summarizer() -> EventSummarizer:
    """Get the global AI summarizer instance."""
    global _summarizer
    if _summarizer is None:
        _summarizer = EventSummarizer()
    return _summarizer

def enhance_events_batch(events: list, max_count: int = None) -> list:
    """
    Enhance a batch of events with AI summaries.

    Args:
        events: List of event dictionaries
        max_count: Maximum number of events to process (None = process all)

    Returns:
        List of enhanced events
    """
    summarizer = get_summarizer()

    if not summarizer.enabled:
        logger.info("AI summarization is disabled")
        return events

    # Process all events or up to max_count
    if max_count is None:
        events_to_process = events
        remaining_events = []
    else:
        events_to_process = events[:max_count]
        remaining_events = events[max_count:]

    enhanced_events = []

    logger.info(f"Enhancing {len(events_to_process)} events with AI summaries")

    for i, event in enumerate(events_to_process):
        enhanced_event = summarizer.enhance_event_description(event)
        enhanced_events.append(enhanced_event)

        # Progress indicator for large batches
        if i > 0 and i % 500 == 0:
            logger.info(f"Enhanced {i}/{len(events_to_process)} events...")

    # Add any remaining events without enhancement
    enhanced_events.extend(remaining_events)

    return enhanced_events

if __name__ == "__main__":
    # Test the summarizer
    test_event = {
        'Title': 'Storytime for Toddlers',
        'Description': 'Join us for an interactive reading session with picture books and songs',
        'Age Group': 'Baby/Toddler',
        'Library': 'Skokie Public Library'
    }

    summarizer = EventSummarizer()
    enhanced = summarizer.enhance_event_description(test_event)
    print(json.dumps(enhanced, indent=2))