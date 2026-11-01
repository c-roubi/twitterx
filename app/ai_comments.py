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
import urllib.error
import urllib.request

from . import net
from .logger import get_logger

log = get_logger("ai")

# نماذج Gemini القديمة (1.5) أُوقفت؛ نجرّب بالترتيب عند خطأ 404 إن لم يحدّد المستخدم نموذجاً
RETIRED_GEMINI = ("gemini-1.5", "gemini-pro", "gemini-1.0")
GEMINI_FALLBACKS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
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


_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u2060\ufeff"))
PROVIDER_NAMES = {"gemini": "Google Gemini", "groq": "Groq", "openai": "OpenAI"}


def clean_key(key):
    """ينظّف المفتاح الملصوق: مسافات، علامات اقتباس، أسطر، ورموز اتجاه مخفية."""
    k = (key or "").translate(_ZW).strip().strip("\"'`").strip()
    return "".join(k.split())


def detect_key_provider(key):
    """يستنتج مزوّد المفتاح من بادئته (AIza = Gemini، gsk_ = Groq، sk- = OpenAI) أو None."""
    k = clean_key(key)
    if k.startswith("AIza"):
        return "gemini"
    if k.startswith("gsk_"):
        return "groq"
    if k.startswith("sk-"):
        return "openai"
    return None


def check_key_format(provider, key):
    """يرمي AIError برسالة مفهومة إن بدا المفتاح لمزوّد آخر. البادئة غير المعروفة لا تُرفض (قد تتغير صيغ المفاتيح)."""
    k = clean_key(key)
    seen = detect_key_provider(k)
    if seen and seen != provider:
        raise AIError("هذا المفتاح يبدو مفتاح {} وأنت مختار المزوّد «{}». غيّر المزوّد إلى «{}» أو ضع مفتاحاً من المزوّد المختار.".format(
            PROVIDER_NAMES[seen], PROVIDER_NAMES.get(provider, provider), PROVIDER_NAMES[seen]))


class AIError(Exception):
    """خطأ بسبب مفهوم للمستخدم (يُعرض في الاختبار ويُسجَّل في السجلات)."""


def _call_api(url, headers, body, ctx, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else str(msg)
        except Exception:
            msg = ""
        hint = ""
        if e.code in (400, 401, 403) and "key" in (msg or "").lower():
            hint = " — المفتاح مرفوض من المزوّد: أنشئ مفتاحاً جديداً وانسخه كاملاً (Gemini: aistudio.google.com/apikey، Groq: console.groq.com/keys)"
        raise AIError("HTTP {}: {}{}".format(e.code, (msg or e.reason or "")[:200], hint)) from None
    except urllib.error.URLError as e:
        raise AIError("تعذّر الاتصال بالمزوّد: {}".format(e.reason)) from None


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


def generate_ai_comment(tweet_text, ai_cfg, net_cfg=None, raise_errors=False, ignore_interval=False, lang=None):
    """توليد تعليق (إنجليزي غالباً مع $TICKER) من نص التغريدة عبر المزوّد المحدد.

    يرجع None عند أي خطأ (مع تسجيل السبب في السجلات)؛ raise_errors=True يرفع AIError لعرضه في الاختبار.
    """
    global _last_call

    def fail(msg):
        log.warning("الذكاء الاصطناعي: " + msg)
        if raise_errors:
            raise AIError(msg)
        return None

    if not tweet_text or not tweet_text.strip():
        return fail("نص التغريدة فارغ - لا يمكن تحليلها (تعذّر قراءة النص من الصفحة)")
    api_key = clean_key(ai_cfg.get("api_key"))
    if not api_key:
        return fail("مفتاح API فارغ")
    try:
        check_key_format(ai_cfg.get("provider", "gemini"), api_key)
    except AIError as e:
        return fail(str(e))

    # تباعد زمني بين الطلبات (حماية الطبقة المجانية). لا يُطبَّق على الاختبار ولا على التنفيذ العاجل.
    min_interval = float(ai_cfg.get("min_interval_s", 30) or 0)
    now = time.time()
    if not ignore_interval and now - _last_call < min_interval:
        log.info("الذكاء الاصطناعي: تخطّي (أقل من {} ثانية عن الطلب السابق) - يُستخدم تعليق محلي".format(int(min_interval)))
        return None
    _last_call = now

    provider = ai_cfg.get("provider", "gemini")
    user_model = (ai_cfg.get("model") or "").strip()
    if provider != "gemini" and user_model.lower().startswith("gemini"):
        log.warning("اسم النموذج «{}» خاص بـ Gemini وليس لـ {} - سيُستخدم النموذج الافتراضي".format(user_model, provider))
        user_model = ""
    if provider == "gemini" and user_model.startswith(RETIRED_GEMINI):
        log.warning("النموذج {} متقاعد - سيُستخدم نموذج حديث تلقائياً (فرّغ حقل النموذج في الإعدادات)".format(user_model))
        user_model = ""
    persona = random.choice(_PERSONAS)
    user_msg = "Persona: {}.\n\nTweet:\n{}\n\nWrite only the comment.".format(persona, tweet_text[:500])
    system = _SYSTEM_PROMPT
    if lang == "en":
        system += " IMPORTANT: the tweet is in English, so reply ONLY in English."
    elif lang == "ar":
        system += " IMPORTANT: the tweet is in Arabic, so reply ONLY in simple Arabic (no English sentences)."
    system += " Never repeat common stock phrases; make the reply specific to this tweet."
    ctx = net.ssl_context(net_cfg)

    try:
        if provider == "gemini":
            models = [user_model] if user_model else GEMINI_FALLBACKS
            resp = None
            for i, model in enumerate(models):
                url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent".format(model)
                body = {"contents": [{"parts": [{"text": "{}\n\n{}".format(system, user_msg)}]}]}
                try:
                    resp = _call_api(url, {"Content-Type": "application/json", "x-goog-api-key": api_key}, body, ctx)
                    break
                except AIError as e:
                    if "HTTP 404" in str(e) and i < len(models) - 1:
                        continue  # النموذج غير متاح: جرّب التالي
                    raise
        else:
            url = {
                "openai": "https://api.openai.com/v1/chat/completions",
                "groq": "https://api.groq.com/openai/v1/chat/completions",
            }.get(provider)
            if not url:
                return fail("مزوّد غير مدعوم: {}".format(provider))
            body = {
                "model": user_model or DEFAULT_MODELS.get(provider),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
                "temperature": 0.9,
                "max_tokens": 60,
            }
            resp = _call_api(url, {"Content-Type": "application/json", "Authorization": "Bearer " + api_key}, body, ctx)
    except AIError as e:
        return fail(str(e))
    except Exception as e:  # شبكة/شهادات/وقت
        return fail("{}: {}".format(type(e).__name__, str(e)[:160]))

    text = _clean(_extract_text(resp, provider))
    if not text:
        return fail("ردّ المزوّد فارغ أو محجوب")
    return text
