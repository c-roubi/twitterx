# -*- coding: utf-8 -*-
"""المنطقة الزمنية للتقارير (افتراضياً غزة) بغض النظر عن توقيت جهاز التشغيل."""
import time
from datetime import datetime, timedelta, timezone

from .logger import get_logger

log = get_logger("tz")
DEFAULT_TZ = "Asia/Gaza"
_warned = set()
_cache = {}


def tzinfo(name=None):
    """tzinfo للمنطقة المطلوبة؛ عند تعذّر قاعدة المناطق (ويندوز بلا tzdata) نستخدم توقيت الجهاز مع تحذير."""
    name = (name or DEFAULT_TZ).strip() or DEFAULT_TZ
    if name in _cache:
        return _cache[name]
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(name)
    except Exception:
        if name not in _warned:
            _warned.add(name)
            log.warning("المنطقة الزمنية {} غير متاحة (ثبّت الحزمة tzdata: pip install tzdata) - سيُستخدم توقيت الجهاز".format(name))
        tz = timezone(timedelta(seconds=-time.timezone if not time.localtime().tm_isdst else -time.altzone))
    _cache[name] = tz
    return tz


def now(name=None):
    return datetime.now(tzinfo(name))


def today(name=None):
    return now(name).strftime("%Y-%m-%d")


def valid(name):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return False
