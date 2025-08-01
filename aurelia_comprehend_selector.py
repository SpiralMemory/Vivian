from dotenv import load_dotenv
import os
load_dotenv()

import boto3
import random
import re
from difflib import SequenceMatcher

# Initialize Amazon Comprehend client
comprehend_client = boto3.client('comprehend', region_name='us-east-2')

def analyze_with_comprehend(text):
    sentiment = comprehend_client.detect_sentiment(Text=text, LanguageCode='en')
    key_phrases = comprehend_client.detect_key_phrases(Text=text, LanguageCode='en')
    entities = comprehend_client.detect_entities(Text=text, LanguageCode='en')

    return {
        "Sentiment": sentiment["Sentiment"],
        "SentimentScore": sentiment["SentimentScore"],
        "KeyPhrases": [kp["Text"].lower() for kp in key_phrases["KeyPhrases"]],
        "Entities": [ent["Text"].lower() for ent in entities["Entities"]]
    }

def fuzzy_match(phrase, text, threshold=0.8):
    """Returns True if phrase approximately matches text with given similarity threshold."""
    return SequenceMatcher(None, phrase, text).ratio() >= threshold

def score_precept(precept_text, key_phrases, entities, sentiment_label):
    text = precept_text.lower()
    score = 0

    # Phrase match bonus
    for phrase in key_phrases:
        if phrase in text:
            score += 3
        elif fuzzy_match(phrase, text):
            score += 2

    # Entity match bonus
    for entity in entities:
        if entity in text:
            score += 2

    # Sentiment alignment bonus
    sentiment_weights = {
        "POSITIVE": ["hope", "love", "compassion", "restoration"],
        "NEGATIVE": ["grief", "pain", "justice", "memory"],
        "MIXED": ["truth", "legacy", "power"],
        "NEUTRAL": ["system", "doctrine", "governance"]
    }
    for keyword in sentiment_weights.get(sentiment_label.upper(), []):
        if keyword in text:
            score += 2

    return score

def select_relevant_precepts(text, all_precepts, min_score_threshold=2, max_precepts=None):
    """
    all_precepts: list of dicts, each with keys:
      - PreceptID
      - MemoryText
      - Interpretation
    """
    analysis = analyze_with_comprehend(text)
    key_phrases = set(analysis["KeyPhrases"])
    entities = set(analysis["Entities"])
    sentiment = analysis["Sentiment"]

    scored = [
        (precept, score_precept(precept["MemoryText"], key_phrases, entities, sentiment))
        for precept in all_precepts
    ]

    # Keep all precepts with score above threshold
    relevant = [p for p, s in sorted(scored, key=lambda x: x[1], reverse=True) if s >= min_score_threshold]

    if not relevant:
        random.shuffle(all_precepts)
        return all_precepts

    if max_precepts:
        return relevant[:max_precepts]

    return relevant



