# -*- coding: utf-8 -*-
"""المحرك الموحد لتوليد التعليقات: AI → سياقي (كريبتو) → عام.

الترتيب:
  1) AI (إن كان مفعّلاً ومتاحاً): تعليق مولّد من نص التغريدة.
  2) سياقي: كشف موضوع التغريدة (كريبتو) واختيار قالب (إنجليزي غالباً) من مكتبة المواضيع.
  3) عام: قوالب comments.txt العادية.

نسبة التعليقات: 90% إنجليزي (comments.en_ratio) و10% عربي، وعند وجود رمز
عملة $TICKER في التغريدة أغلب التعليقات الإنجليزية تحمل «$» + الرمز.
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


def _unique(text, used_texts, force_unique=True):
    text = (text or "").strip()
    if not text:
        return False
    if force_unique and len(text) < 5:
        return False
    return text not in set(used_texts)


# انحياز القوالب التي تحمل {TICKER} عند وجود رمز عملة في التغريدة
_TICKER_BIAS = 0.8


def _contextual_comment(tweet_text, used, cfg=None, en_ratio=None):
    """تعليق سياقي من مكتبة مواضيع الكريبتو (إنجليزي/عربي)، أو None إن لم يُعثر على موضوع."""
    topics = detect_topics(tweet_text)
    if not topics:
        return None
    pools = load_crypto_pools_from_json(COMMENTS_CRYPTO_PATH)
    ticker = extract_ticker(tweet_text)

    # اختيار اللغة: نسبة إنجليزية من حساب/إعداد (en_ratio) أو الإعداد العام comments.en_ratio
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
    # عند وجود رمز عملة: أغلبية التعليقات الإنجليزية تحمل $TICKER
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
    """تعليق سياقي ذكي من نص التغريدة، مع تجنب تكرار التعليقات."""
    cfg = cfg or load_config()
    comments_cfg = cfg.get("comments", {})
    used = set(used_texts or ())

    # 1) الذكاء الاصطناعي (اختياري)
    ai_cfg = comments_cfg.get("ai", {})
    if ai_cfg.get("enabled"):
        try:
            from .ai_comments import generate_ai_comment

            text = generate_ai_comment(tweet_text, ai_cfg)
            if text and _unique(text, used, force_unique):
                return text
        except Exception:
            pass

    # 2) سياقي من موضوع التغريدة (كريبتو)
    if comments_cfg.get("contextual", True) and tweet_text and tweet_text.strip():
        try:
            text = _contextual_comment(tweet_text, used, cfg, en_ratio=en_ratio)
            if text:
                return text
        except Exception:
            pass

    # 3) عام من comments.txt (الطبقة الأخيرة مع إصلاح ثغرات الفشل)
    templates = load_comments()
    
    # حماية من انهيار السكربت إذا كان ملف التعليقات فارغاً
    if not templates:
        return "🔥"
        
    # محاولة إيجاد تعليق فريد بتجربة ما يصل إلى 10 قوالب عشوائية
    for tmpl in random.sample(templates, min(len(templates), 10)):
        text = build_comment(tmpl, used_texts=used, force_unique=force_unique)
        if text:
            return text
            
    # إذا كانت كل القوالب الـ 10 قد استُخدمت مسبقاً، نرسل تعليقاً عشوائياً دون فرض التفرّد
    fallback = build_comment(random.choice(templates), used_texts=used, force_unique=False)
    return fallback or "🔥"