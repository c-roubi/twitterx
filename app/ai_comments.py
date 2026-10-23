# -*- coding: utf-8 -*-
"""توليد تعليقات عبر الذكاء الاصطناعي (اختياري) - بدون أي مكتبات خارجية.

المزوّدون المدعومون (الطبقات المجانية):
  - gemini : Google AI Studio -> https://aistudio.google.com/apikey
  - openai : OpenAI API (يوجد حد مجاني) -> https://platform.openai.com
  - groq   : Groq (مجاني سخي) -> https://console.groq.com/keys

أي خطأ في الشبكة أو المفتاح -> إرجاع None ليقع المحرك على البديل السياقي المحلي.
"""
import json
import random
import time
import urllib.request

DEFAULT_MODELS = {
    "gemini": "gemini-1.5-flash",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.1-8b-instant",
}

_PERSONAS = [
    "an excited crypto trader who loves quick gains",
    "a technical analyst studying every signal",
    "a conservative trader who asks smart questions",
    "a HODLER learning from the best calls",
    "a day trader who trades every signal",
    "a risk-aware trader who checks details",
    "an optimistic follower who trusts the account",
    "a data-driven trader who verifies calls",
    "a beginner trying to understand TA",
    "a confident trader who acts on signals",
]

_SYSTEM_PROMPT = (
    "You are a crypto trader/follower on a signal account. Reply with ONE short sentence (10-70 chars) in English (~90%) or Arabic.\n\n"
    "DETECT TWEET TYPE:\n"
    "1. RESULTS/PROFITS (shows past calls + profits): Praise the analyst, thank them, express confidence. DO NOT focus on individual coins.\n"
    "   Example: 'Amazing track record, great calls! 🔥'\n\n"
    "2. NEW SIGNAL (introduces a new coin): React on THAT coin with $TICKER - ALWAYS include $TICKER in 85-90% of replies.\n"
    "   Example: '$EPIC looking bullish, support holding 👀'\n"
    "   Include: '$TICKER will pump', '$TICKER entering', 'loading $TICKER', '$TICKER targets' etc.\n\n"
    "3. GENERAL (chart, analysis, news): Ask smart questions or add TA insight.\n\n"
    "RULES:\n"
    "- CRITICAL: Include $TICKER in 85%+ of replies (especially for NEW SIGNAL tweets)\n"
    "- Vary tone: excited, analytical, cautious, confident (not the same every time)\n"
    "- $TICKER mention increases engagement - use it liberally\n"
    "- Forbidden: hashtags, repeated emojis (max 1), account mentions, quotes\n"
    "- Be authentic, like a real trader, not a bot\n"
    "Output ONLY the comment."
)

_last_call = 0.0


def _call_api(url, headers, body, timeout=15):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_text(resp, provider):
    if provider == "gemini":
        try:
            return resp["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return None
    # openai / groq (متوافقان مع نفس الشكل)
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _clean(text):
    if not text:
        return None
    text = text.strip().strip('"').strip("'").strip("«»")
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    text = text[:280]
    return text or None


def generate_ai_comment(tweet_text, ai_cfg):
    """توليد تعليق (إنجليزي غالباً مع $TICKER) من نص التغريدة عبر المزوّد المحدد. None عند أي خطأ."""
    global _last_call
    if not tweet_text or not tweet_text.strip():
        return None

    api_key = (ai_cfg.get("api_key") or "").strip()
    if not api_key:
        return None

    # تباعد زمني بين الطلبات (حماية من الحظر + احترام حدود الطبقة المجانية)
    min_interval = float(ai_cfg.get("min_interval_s", 30) or 30)
    now = time.time()
    if now - _last_call < min_interval:
        return None
    _last_call = now

    provider = ai_cfg.get("provider", "gemini")
    model = (ai_cfg.get("model") or "").strip() or DEFAULT_MODELS.get(provider, DEFAULT_MODELS["gemini"])
    persona = random.choice(_PERSONAS)
    user_msg = f"Persona: {persona}.\n\nTweet:\n{tweet_text[:500]}\n\nWrite only the comment."

    try:
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            body = {"contents": [{"parts": [{"text": f"{_SYSTEM_PROMPT}\n\n{user_msg}"}]}]}
            headers = {"Content-Type": "application/json"}
            resp = _call_api(url, headers, body)
        else:
            url = {
                "openai": "https://api.openai.com/v1/chat/completions",
                "groq": "https://api.groq.com/openai/v1/chat/completions",
            }.get(provider)
            if not url:
                return None
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 1.1,
                "max_tokens": 70,
            }
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            resp = _call_api(url, headers, body)
    except Exception:
        return None

    return _clean(_extract_text(resp, provider))