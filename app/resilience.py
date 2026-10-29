# -*- coding: utf-8 -*-
"""طبقة المرونة: تصنيف الأخطاء + إعادة المحاولة (backoff) + كشف التغريدات المحذوفة.

الهدف: أن يتعامل البرنامج مع الظروف الطارئة بدل أن يتوقف أو يتعطّل حيالها:
  1. إنترنت ضعيف / انقطاع مؤقت → إعادة محاولة بفترات متدرجة ثم الاستسلام بأمان.
  2. فشل عملية واحدة (لايك/ريتويت/تعليق/متابعة) → تخطيها والاستمرار دون كسر الدورة.
  3. تغريدة محذوفة من المدير → رصدها وإزالتها من الجدول نهائياً.
"""
import random
import time

from .logger import get_logger

log = get_logger("resilience")

# ---------------------------------------------------------------------------
# كشف التغريدة المحذوفة / غير الموجودة
# ---------------------------------------------------------------------------
DELETED_MARKERS_EN = [
    "hmm...this page doesn't exist",
    "this post was deleted",
    "this tweet was deleted",
    "post not found",
    "tweet not found",
    "sorry, that page doesn't exist",
    "something went wrong, but don't fret",
    "the post has been deleted",
    "content is not available",
]

DELETED_MARKERS_AR = [
    "هذه التغريدة غير متوفرة",
    "تم حذف هذه التغريدة",
    "تغريدة محذوفة",
    "لم يتم العثور على هذه الصفحة",
    "عذرًا، هذه الصفحة غير موجودة",
    "المحتوى غير متاح",
    "حدث خطأ ما",
]

# ---------------------------------------------------------------------------
# كشف أخطاء الشبكة / المهلة / تقييد المعدل
# ---------------------------------------------------------------------------
_NETWORK_SUBSTRINGS = (
    "net::err", "err_", "temporary failure in name resolution",
    "getaddrinfo", "enotfound", "econn", "ehostunreach", "enetunreach",
    "connection reset", "connection refused", "connection aborted",
    "broken pipe", "target closed", "protocol error", "failed to fetch",
    "neterror", "internet disconnected", "network changed",
)

_TIMEOUT_SUBSTRINGS = (
    "timeout", "timed out", "timeouterr", "timeout exception",
)

_RATELIMIT_SUBSTRINGS = (
    "429", "rate limit", "too many requests", "too many",
)


def classify_error(e):
    """تصنيف استثناء إلى: network | timeout | rate_limit | other."""
    name = type(e).__name__
    s = str(e) if e else ""
    low = s.lower()

    if any(k in low for k in _RATELIMIT_SUBSTRINGS):
        return "rate_limit"
    if any(k in low for k in _TIMEOUT_SUBSTRINGS) or "timeout" in name.lower():
        return "timeout"
    if any(k in low for k in _NETWORK_SUBSTRINGS):
        return "network"
    if name in (
        "ConnectionError", "ConnectionResetError", "ConnectionAbortedError",
        "ConnectionRefusedError", "OSError", "BrokenPipeError", "socket",
    ):
        return "network"
    return "other"


def is_retryable(kind):
    """هل هذا النوع من الأخطاء يستحق إعادة المحاولة؟"""
    return kind in ("network", "timeout")


def error_kind_from_msg(msg):
    """تحويل رسالة خطأ نصية (مثل 'error:...') إلى فئة خطأ."""
    if not msg:
        return "other"
    s = str(msg)
    if s.startswith("network"):
        return "network"
    if s.startswith("timeout"):
        return "timeout"
    if "error:" in s:
        body = s.split("error:", 1)[1]
        low = body.lower()
        if any(k in low for k in _TIMEOUT_SUBSTRINGS):
            return "timeout"
        if any(k in low for k in _NETWORK_SUBSTRINGS):
            return "network"
        if any(k in low for k in _RATELIMIT_SUBSTRINGS):
            return "rate_limit"
    return "other"


def backoff_delay(attempt, base=5.0):
    """فترة انتظار متدرجة مع ضجيج عشوائي (المرة الأولى attempt=0)."""
    return base * (2 ** attempt) + random.uniform(0, 2.0)


def retry(fn, attempts=2, base_delay=5.0, exc_filter=None, on_retry=None):
    """تنفيذ fn وإعادة محاولته عند أخطاء شبكة/مهلة فقط.

    ترجع (ok, value) حيث value هي ناتج fn عند النجاح، أو الاستثناء عند الفشل النهائي.
    """
    exc_filter = exc_filter or (lambda e: is_retryable(classify_error(e)))
    total = max(1, attempts + 1)
    last_err = None
    for i in range(total):
        try:
            return True, fn()
        except Exception as e:
            last_err = e
            if not exc_filter(e) or i == total - 1:
                return False, e
            d = backoff_delay(i, base_delay)
            if on_retry:
                on_retry(i + 1, e, d)
            log.warning(f"Retrying ({i + 2}/{total}) after {d:.1f} seconds: {e}")
            time.sleep(d)
    return False, last_err


def is_tweet_deleted(page):
    """هل صفحة التغريدة تعرض رسالة حذف/عدم وجود؟"""
    try:
        # الانتظار بحد أقصى 2.5 ثانية للـ body لتجنب تجميد الكود
        text = (page.locator("body").inner_text(timeout=2500) or "").strip()
    except Exception:
        return False
    if not text:
        return False
    low = text.lower()
    if any(m in low for m in DELETED_MARKERS_EN):
        return True
    if any(m in text for m in DELETED_MARKERS_AR):
        return True
    return False


def goto_with_retry(page, url, cfg, timeout=None, wait_until="domcontentloaded"):
    """فتح صفحة مع إعادة محاولة عند أخطاء الشبكة/المهلة. ترجع (ok, status)."""
    r = cfg.get("resilience", {}).get("network_retries", 2)
    base = float(cfg.get("resilience", {}).get("retry_base_delay_s", 5.0))
    if timeout is None:
        timeout = int(cfg.get("resilience", {}).get("goto_timeout_s", 60000))
    last = "other"
    for i in range(int(r) + 1):
        try:
            # ✅ تم التعديل: إضافة wait_until كمعامل افتراضي لمنع הTimeout نهائياً
            page.goto(url, timeout=timeout, wait_until=wait_until)
            return True, "ok"
        except Exception as e:
            last = classify_error(e)
            if not is_retryable(last) or i == int(r):
                return False, last
            d = backoff_delay(i, base)
            log.warning(f"Network problem opening {url} - retrying ({i + 2}/{int(r) + 1}) after {d:.1f} seconds")
            time.sleep(d)
    return False, last


# اسم مستعار للتوافق مع أسماء الاستدعاءات القديمة
goto = goto_with_retry