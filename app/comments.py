# -*- coding: utf-8 -*-
"""المحرك الموحد لتوليد التعليقات: AI → سياقي (كريبتو) → عام.

الترتيب:
  1) AI (إن كان مفعّلاً ومتاحاً): تعليق مولّد من نص التغريدة.
  2) سياقي: كشف موضوع التغريدة (كريبتو) واختيار قالب (إنجليزي غالباً) من مكتبة المواضيع.
  3) عام: قوالب comments.txt العادية.

اللغة: تتبع لغة التغريدة (comments.match_language، افتراضياً مفعّل)؛ وإن تعذّر تحديدها
(تغريدة صورة بلا نص) تُستخدم نسبة comments.en_ratio. عند وجود رمز عملة $TICKER
أغلب التعليقات الإنجليزية تحمل «$» + الرمز.

منع التكرار (تويتر يصنّف المتشابه «Probable spam»): نقارن بعد التطبيع (بدون إيموجي/تشكيل/ترقيم)
ونرفض المطابق والمتشابه جداً مع تعليقات هذه التغريدة وآخر تعليقات كل الحسابات (سجل محفوظ).
"""
import difflib
import json
import os
import random
import re
import threading
import time

from .config import COMMENTS_CRYPTO_PATH, DATA_DIR, _atomic_save, load_comments, load_config
from .crypto_comments import (
    EN_POOLS,
    _render_template,
    detect_topics,
    extract_ticker,
    load_crypto_pools_from_json,
)
from .humanizer import SUFFIX_POOL, spintax

HISTORY_PATH = os.path.join(DATA_DIR, "comment_history.json")
HISTORY_MAX = 400          # أقصى عدد تعليقات محفوظة في السجل
GLOBAL_WINDOW = 150        # نتجنب تشابه آخر N تعليق من كل الحسابات
ACCOUNT_WINDOW = 300       # ونتجنب تشابه آخر N تعليق لنفس الحساب
SIMILARITY = 0.82          # نسبة التشابه المرفوضة
_hist_lock = threading.Lock()

_AR = re.compile(r"[\u0600-\u06FF]")
_LAT = re.compile(r"[A-Za-z]")
_DIAC = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_ARMAP = str.maketrans("أإآٱىة", "اااايه")

DEFAULT_EN_GENERIC = [
    "Solid analysis, thanks for sharing",
    "Interesting setup, keeping an eye on this",
    "Great call, let's see how it plays out",
    "Good insight, appreciate the update",
    "Clean chart, the levels look clear",
    "Thanks for the heads up",
    "Strong move, watching closely",
    "Nice breakdown, very helpful",
    "Patience pays in this market",
    "Makes sense, volume will confirm it",
    "Appreciate the clear levels here",
    "Good thread, bookmarking this",
    "Fair point, the trend still looks intact",
    "Well explained, thanks",
    "This one looks promising",
    "Keeping this on my watchlist",
    "Agree with the view, risk management first",
    "Useful info as always",
    "Great timing on this call",
    "Looks like a healthy structure",
]


# ------------------------------------------------------------------ تطبيع ومقارنة
def norm_text(text):
    """نص مبسّط للمقارنة: بدون إيموجي/ترقيم/تشكيل وبتوحيد الألف والياء والتاء المربوطة."""
    t = _DIAC.sub("", (text or "").lower()).translate(_ARMAP)
    t = re.sub(r"[^\w\s]|_", " ", t)
    return " ".join(t.split())


def similar(a, b):
    """هل النصان (المطبَّعان) متطابقان أو متشابهان جداً؟"""
    if not a or not b:
        return False
    if a == b:
        return True
    if difflib.SequenceMatcher(None, a, b).ratio() >= SIMILARITY:
        return True
    sa, sb = set(a.split()), set(b.split())
    return len(sa) >= 3 and len(sb) >= 3 and len(sa & sb) / len(sa | sb) >= 0.8


def detect_lang(text):
    """'ar' / 'en' حسب أغلب حروف النص، أو None إن كان قصيراً/غير نصي."""
    text = text or ""
    # نتجاهل الروابط والمنشنات والوسوم والرموز المالية قبل العدّ
    text = re.sub(r"https?://\S+|[@#$]\w+", " ", text)
    ar, lat = len(_AR.findall(text)), len(_LAT.findall(text))
    if ar >= 3 and ar > lat:  # الأغلبية: تغريدة إنجليزية فيها كلمات عربية قليلة تبقى إنجليزية
        return "ar"
    if lat >= 3:
        return "en"
    return None


def has_arabic(text):
    return bool(_AR.search(text or ""))


# ------------------------------------------------------------------ سجل التعليقات
def _load_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _recent_blocked(acc_id=None):
    hist = _load_history()
    out = [h.get("n", "") for h in hist[-GLOBAL_WINDOW:]]
    if acc_id:
        out += [h.get("n", "") for h in hist[-ACCOUNT_WINDOW:] if h.get("a") == acc_id]
    return [x for x in out if x]


def _reserve(text, acc_id=None):
    """نحجز التعليق فور اختياره (قبل نشره) حتى لا يختاره حساب آخر يعمل بالتوازي."""
    n = norm_text(text)
    if not n:
        return
    with _hist_lock:
        hist = _load_history()
        hist.append({"n": n, "a": acc_id or "", "t": int(time.time())})
        try:
            os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
            _atomic_save(hist[-HISTORY_MAX:], HISTORY_PATH)
        except OSError:
            pass


def _ok(text, blocked, force_unique=True):
    text = (text or "").strip()
    n = norm_text(text)
    if not text or (force_unique and len(n) < 3):
        return False
    return not any(similar(n, b) for b in blocked)


def _unique(text, used_texts, force_unique=True):  # للتوافق مع الاستدعاءات القديمة
    return _ok(text, [norm_text(u) for u in used_texts or ()], force_unique)


def _pick_variant(template, blocked, force_unique=True):
    """يولّد صيغ spintax للقالب ويرجع أول صيغة غير مكررة (اللاحقة تُضاف للتنويع فقط ولا تُستخدم للتهرّب من التكرار)."""
    seen = set()
    for _ in range(8):
        base = spintax(template).strip()
        if base in seen:
            continue
        seen.add(base)
        if _ok(base, blocked, force_unique):
            if random.random() < 0.45:
                base += random.choice(SUFFIX_POOL)
            return base
    return None


# ------------------------------------------------------------------ مولّدات
# انحياز القوالب التي تحمل {TICKER} عند وجود رمز عملة في التغريدة
_TICKER_BIAS = 0.8


def _en_ratio_of(cfg, en_ratio):
    if en_ratio is None:
        en_ratio = 0.9
        if cfg:
            try:
                v = (cfg.get("comments") or {}).get("en_ratio", 0.9)
                en_ratio = 0.9 if v is None else float(v)  # 0 تعني «عربي بالكامل» وليست قيمة فارغة
            except (TypeError, ValueError):
                en_ratio = 0.9
    try:
        return min(1.0, max(0.0, float(en_ratio)))
    except (TypeError, ValueError):
        return 0.9


def _contextual_comment(tweet_text, blocked, cfg=None, en_ratio=None, lang=None):
    """تعليق سياقي من مكتبة مواضيع الكريبتو بلغة التغريدة، أو None."""
    topics = detect_topics(tweet_text)
    if not topics:
        return None
    pools = load_crypto_pools_from_json(COMMENTS_CRYPTO_PATH)
    ticker = extract_ticker(tweet_text)
    if lang == "en":
        use_en = True
    elif lang == "ar":
        use_en = False
    else:
        use_en = random.random() < _en_ratio_of(cfg, en_ratio)
    source = EN_POOLS if use_en else pools

    merged, seen = [], set()
    for t in topics:
        for tmpl in source.get(t, []):
            if tmpl not in seen:
                seen.add(tmpl)
                merged.append(tmpl)
    if not merged:
        return None
    if source is EN_POOLS and ticker:
        ticker_tmpls = [t for t in merged if "{TICKER}" in t]
        if ticker_tmpls and random.random() < _TICKER_BIAS:
            merged = ticker_tmpls
    random.shuffle(merged)
    for tmpl in merged:
        rendered = _render_template(tmpl, ticker)
        if rendered is None:
            continue  # قالب يتطلب {TICKER} ولا يوجد رمز في التغريدة
        text = _pick_variant(rendered, blocked)
        if text:
            return text
    return None


def _generic_comment(lang, blocked, cfg=None, en_ratio=None, force_unique=True):
    """تعليق عام من comments.txt بلغة مطابقة، مع قوالب إنجليزية افتراضية إن لم تتوفر."""
    if lang is None:
        lang = "en" if random.random() < _en_ratio_of(cfg, en_ratio) else "ar"
    templates = [t for t in load_comments() if has_arabic(t) == (lang == "ar")]
    if not templates and lang == "en":
        templates = list(DEFAULT_EN_GENERIC)
    random.shuffle(templates)
    for tmpl in templates:
        text = _pick_variant(tmpl, blocked, force_unique)
        if text:
            return text
    if lang == "ar":  # لا قوالب عربية كافية: لا نضطر لتعليق بلغة مخالفة؛ جرّب الإنجليزي الافتراضي
        for tmpl in random.sample(DEFAULT_EN_GENERIC, len(DEFAULT_EN_GENERIC)):
            text = _pick_variant(tmpl, blocked, force_unique)
            if text:
                return text
    return None


def build_comment_for(tweet_text, used_texts=(), cfg=None, force_unique=True, en_ratio=None, acc_id=None, reserve=True,
                      manager=False):
    """تعليق مناسب لنص التغريدة، بلغتها، وغير مكرر/متشابه مع ما سبق. يرجع None إن نفدت الخيارات.

    manager=True (تغريدات المدير): القوالب السياقية أولاً، والذكاء الاصطناعي فقط إن لم يوجد قالب
    مناسب لموضوع التغريدة. بقية التغريدات: الذكاء الاصطناعي أولاً (إن كان مفعّلاً) كما كان.
    """
    cfg = cfg or load_config()
    comments_cfg = cfg.get("comments", {})
    lang = detect_lang(tweet_text) if comments_cfg.get("match_language", True) else None
    blocked = [norm_text(u) for u in (used_texts or ())] + _recent_blocked(acc_id)
    text = None

    def _contextual():
        if comments_cfg.get("contextual", True) and tweet_text and tweet_text.strip():
            try:
                return _contextual_comment(tweet_text, blocked, cfg, en_ratio, lang)
            except Exception:
                return None
        return None

    if manager:
        text = _contextual()

    # 1) الذكاء الاصطناعي (اختياري)
    ai_cfg = comments_cfg.get("ai", {})
    if not text and ai_cfg.get("enabled"):
        try:
            from .ai_comments import generate_ai_comment

            cand = generate_ai_comment(tweet_text, ai_cfg, net_cfg=cfg.get("telegram"), lang=lang)
            if cand and _ok(cand, blocked, force_unique):
                text = cand
                from .logger import get_logger
                get_logger("comments").info("تعليق مولَّد بالذكاء الاصطناعي ({}): {}".format(lang or "؟", cand[:60]))
            elif cand is None:
                from .logger import get_logger
                get_logger("comments").info("لم يُولَّد تعليق بالذكاء الاصطناعي - استُخدم تعليق محلي بديل")
        except Exception:
            pass

    # 2) سياقي من موضوع التغريدة (كريبتو)
    if not text and not manager:
        text = _contextual()

    # 3) عام بلغة مطابقة
    if not text:
        text = _generic_comment(lang, blocked, cfg, en_ratio, force_unique)

    if text and reserve:
        _reserve(text, acc_id)
    return text
