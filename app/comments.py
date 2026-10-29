# -*- coding: utf-8 -*-
"""Unified comment generation engine: AI → contextual (crypto) → generic.

Order:
  1) AI (if enabled and available): comment generated from tweet text.
  2) Contextual: detect tweet topic (crypto) and select template (usually English) from topic library.
  3) Generic: regular comments.txt templates.

Comment ratio: 90% English (comments.en_ratio) and 10% Arabic, and when $TICKER
currency symbol exists in tweet most English comments have «$» + symbol.
"""
import random

from .config import COMMENTS_CRYPTO_PATH, load_comments, load_config
from .crypto_comments import (
    EN_POOLS,
    _render_template,
    detect_topics,
    extract_ticker,
    load_crypto_pools_from_json,
)
from .humanizer import build_comment


def _detect_language(text):
    """Detect tweet language: Arabic or English (based on characters)."""
    if not text:
        return "en"  # default: English

    # Count Arabic and English characters
    arabic_count = sum(1 for c in text if 'ء' <= c <= 'ي')  # Arabic character range
    english_count = sum(1 for c in text if 'a' <= c.lower() <= 'z')

    if arabic_count > english_count * 1.5:  # Strong Arabic
        return "ar"
    elif english_count > arabic_count * 1.5:  # Strong English
        return "en"
    else:
        return "mixed"  # Mixed


def _is_sensitive_topic(tweet_text):
    """Avoid sensitive tweets: war, disasters, politics, hate, etc."""
    sensitive_keywords = [
        # War and military
        "war", "attack", "military", "ukraine", "russia", "israel", "palestine",
        "bombing", "conflict", "troops", "forces",
        # Disasters and death
        "disaster", "death", "killed", "dead", "died", "earthquake", "tsunami",
        "crash", "collapse", "accident", "fatal",
        # Sensitive politics
        "politics", "election", "politician", "coup",
        # Violence and hate
        "violence", "hate", "terror", "extremist", "racist",
    ]
    text_lower = (tweet_text or "").lower()
    return any(keyword in text_lower for keyword in sensitive_keywords)


def _unique(text, used_texts, force_unique=True):
    text = (text or "").strip()
    if not text:
        return False
    if force_unique and len(text) < 5:
        return False
    return text not in set(used_texts)


# Bias templates that have {TICKER} when currency symbol exists in tweet
# Raised to 0.95 to maximize Twitter with $TICKER mentions
_TICKER_BIAS = 0.95


def _contextual_comment(tweet_text, used, cfg=None, en_ratio=None):
    """Contextual comment from crypto topic library (English/Arabic), or None if no topic found."""
    topics = detect_topics(tweet_text)
    if not topics:
        return None
    pools = load_crypto_pools_from_json(COMMENTS_CRYPTO_PATH)
    ticker = extract_ticker(tweet_text)

    # Choose language: English ratio from account/config (en_ratio) or general comments.en_ratio config
    if en_ratio is None:
        en_ratio = 0.9
        if cfg:
            try:
                en_ratio = float((cfg.get("comments") or {}).get("en_ratio", 0.9) or 0.9)
            except (TypeError, ValueError):
                en_ratio = 0.9
    try:
        en_ratio = min(1.0, max(0.0, float(en_ratio)))
    except (TypeError, ValueError):
        en_ratio = 0.9
    source = EN_POOLS if random.random() < en_ratio else pools

    merged, seen = [], set()
    for t in topics:
        for tmpl in source.get(t, []):
            if tmpl not in seen:
                seen.add(tmpl)
                merged.append(tmpl)
    if not merged:
        return None
    # When currency symbol exists: majority of English comments have $TICKER
    if source is EN_POOLS and ticker:
        ticker_tmpls = [t for t in merged if "{TICKER}" in t]
        if ticker_tmpls and random.random() < _TICKER_BIAS:
            merged = ticker_tmpls
    random.shuffle(merged)
    for tmpl in merged:
        rendered = _render_template(tmpl, ticker)
        if rendered is None:
            continue  # قالب يتطلب {TICKER} ولا يوجد رمز في التغريدة
        text = build_comment(rendered, used_texts=used, force_unique=True)
        if text:
            return text
    return None


def build_comment_for(tweet_text, used_texts=(), cfg=None, force_unique=True, en_ratio=None):
    """Smart contextual comment from tweet text, avoiding comment repetition.

    Avoid sensitive tweets (war, disasters, politics) - return None instead of inappropriate comment."""
    # Avoid sensitive tweets
    if _is_sensitive_topic(tweet_text):
        return None

    cfg = cfg or load_config()
    comments_cfg = cfg.get("comments", {})
    used = set(used_texts or ())

    # 1) Artificial intelligence (optional)
    ai_cfg = comments_cfg.get("ai", {})
    if ai_cfg.get("enabled"):
        try:
            from .ai_comments import generate_ai_comment

            text = generate_ai_comment(tweet_text, ai_cfg)
            if text and _unique(text, used, force_unique):
                return text
        except Exception:
            pass

    # 2) Contextual from tweet topic (crypto)
    if comments_cfg.get("contextual", True) and tweet_text and tweet_text.strip():
        try:
            text = _contextual_comment(tweet_text, used, cfg, en_ratio=en_ratio)
            if text:
                return text
        except Exception:
            pass

    # 3) Generic from comments.txt (last layer with fallback fixes)
    templates = load_comments()

    # Protect against crash if comments file is empty
    if not templates:
        return "🔥"

    # Try to find unique comment by testing up to 10 random templates
    for tmpl in random.sample(templates, min(len(templates), 10)):
        text = build_comment(tmpl, used_texts=used, force_unique=force_unique)
        if text:
            return text

    # If all 10 templates already used, send random comment without enforcing uniqueness
    fallback = build_comment(random.choice(templates), used_texts=used, force_unique=False)
    return fallback or "🔥"