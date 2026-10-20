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
    "an excited crypto follower since years",
    "a retail trader learning from the analysis",
    "a beginner analyst who loves charts and indicators",
    "a calm follower who values clear calls",
    "a daily crypto trader who knows the market well",
]

_SYSTEM_PROMPT = (
    "You are a crypto follower replying to a Twitter signal account. "
    "Reply with one very short sentence (10-60 characters) in English in ~90% of cases, "
    "and rarely in simple Arabic. When the tweet mentions a coin ticker, include it with "
    "a dollar sign (e.g. $EPIC) in most of your English replies. "
    "React positively, ask a smart question, or add a quick TA note. "
    "Forbidden: hashtags (#), repeated emojis, mentioning the account owner, or quotes. "
    "Output only the comment."
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
                "temperature": 0.9,
                "max_tokens": 60,
            }
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            resp = _call_api(url, headers, body)
    except Exception:
        return None

    return _clean(_extract_text(resp, provider))