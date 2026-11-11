# -*- coding: utf-8 -*-
"""معرفة التوثيق (الشارة الزرقاء/الذهبية/الرمادية) من بيانات X نفسها بدل البحث عن أيقونة بالـ DOM.

X يُرجع لكل حساب في ردود GraphQL (UserByScreenName / SearchTimeline / Retweeters / ...)
الحقول is_blue_verified و legacy.verified وغيرها. نلتقطها عبر مراقبة ردود الشبكة في سياق
المتصفح ونخزّنها بمفتاح اسم الحساب. هذا أدق وأثبت من محددات الـ DOM التي تتغير وتُخطئ.
"""
import threading

from .logger import get_logger

log = get_logger("verify")

_CACHE = {}
_LOCK = threading.Lock()
_MAX = 20000


def _screen_name(d):
    for path in (("core", "screen_name"), ("legacy", "screen_name")):
        cur = d
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, str) and cur:
            return cur
    v = d.get("screen_name")
    return v if isinstance(v, str) and v else None


def _is_verified(d):
    legacy = d.get("legacy") if isinstance(d.get("legacy"), dict) else {}
    return bool(
        d.get("is_blue_verified")
        or legacy.get("verified")
        or d.get("verified")
        or legacy.get("verified_type")
        or (d.get("verification") or {}).get("verified")
    )


def _walk(node, out, depth=0):
    if depth > 40:
        return
    if isinstance(node, dict):
        if "is_blue_verified" in node:
            name = _screen_name(node)
            if name:
                out[name.lower()] = _is_verified(node)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                _walk(v, out, depth + 1)


def ingest(payload):
    """استخراج حالة التوثيق من JSON وتخزينها. ترجع عدد الحسابات المضافة."""
    found = {}
    _walk(payload, found)
    if found:
        with _LOCK:
            if len(_CACHE) > _MAX:
                _CACHE.clear()
            _CACHE.update(found)
    return len(found)


def lookup(handle):
    """True/False إن عرفنا حالة الحساب من بيانات X، أو None إن لم نرَه بعد."""
    if not handle:
        return None
    with _LOCK:
        return _CACHE.get(str(handle).lstrip("@").lower())


def attach(context):
    """مراقبة ردود GraphQL في السياق لتغذية الكاش (آمنة: أي خطأ يُتجاهل)."""
    def _on_response(resp):
        try:
            if "/graphql/" not in resp.url or resp.status != 200:
                return
            ingest(resp.json())
        except Exception:
            pass

    try:
        context.on("response", _on_response)
    except Exception as e:
        log.warning(f"تعذّر تفعيل مراقبة التوثيق: {e}")
