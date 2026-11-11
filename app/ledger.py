# -*- coding: utf-8 -*-
"""دفتر النشاط اليومي بتوقيت التقارير (غزة): أعداد كل حساب + أسماء من تُوبعوا.

يُغذَّى تلقائياً من config.save_state: نقارن عدّادات الحساب بآخر قيم رأيناها (high-water mark)
فنلتقط كل نشاط مهما كان مصدره (جلسة، تغريدة عاجلة، إحماء، متابعات) دون لمس تلك الأكواد،
وهو محصّن ضد كتابات متزامنة قديمة (لا يُحسب النشاط مرتين) وضد اختلاف توقيت الجهاز عن توقيت التقرير.
"""
import json
import os
import threading
import time

from .config import DATA_DIR, _atomic_save
from . import tz

LEDGER_PATH = os.path.join(DATA_DIR, "daily_ledger.json")
KEEP_DAYS = 45
KINDS = ("likes", "retweets", "comments", "follows")
_lock = threading.RLock()
_tz_cache = {"t": 0.0, "name": tz.DEFAULT_TZ}


def tz_name():
    """اسم منطقة التقارير من الإعدادات (مخزّن مؤقتاً 30 ثانية لأن save_state يُستدعى كثيراً)."""
    if time.time() - _tz_cache["t"] > 30:
        try:
            from .config import load_config
            _tz_cache["name"] = load_config()["telegram"]["daily_summary"].get("timezone") or tz.DEFAULT_TZ
        except Exception:
            pass
        _tz_cache["t"] = time.time()
    return _tz_cache["name"]


def _load():
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("days", {})
            d.setdefault("hw", {})
            return d
    except (OSError, ValueError):
        pass
    return {"days": {}, "hw": {}}


def _save(d):
    days = d["days"]
    for old in sorted(days)[:-KEEP_DAYS]:
        days.pop(old, None)
    try:
        os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
        _atomic_save(d, LEDGER_PATH)
    except OSError:
        pass


def _iso(d):
    return isinstance(d, str) and len(d) == 10 and d[4] == "-" and d[7] == "-" and d.replace("-", "").isdigit()


def record_state(acc_id, state, when=None):
    """يسجّل الفرق بين حالة الحساب الجديدة وآخر ما رأيناه. يرجع True إن سُجّل شيء."""
    if not isinstance(state, dict):
        return False
    done = state.get("done") or {}
    sdate = state.get("date") or ""
    followed = list(state.get("followed_handles") or [])
    day = when or tz.today(tz_name())
    with _lock:
        d = _load()
        hw = d["hw"].get(acc_id)
        if hw is not None and _iso(sdate) and _iso(hw.get("date")) and hw["date"] > sdate:
            return False   # حالة يوم أقدم وصلت متأخرة: لا نُرجع خط الأساس فنعدّ النشاط مرتين
        first_seen = hw is None
        if hw is None or hw.get("date") != sdate:
            # أول مرة نرى الحساب: خط أساس (لا نحسب ما سبق نشر الدفتر). يوم جديد للحساب: العدّادات تبدأ من صفر.
            hw = {"date": sdate, **{k: (int(done.get(k, 0) or 0) if first_seen else 0) for k in KINDS},
                  "followed": followed if first_seen else (hw or {}).get("followed", followed)}
        changed = False
        rec = d["days"].setdefault(day, {"accounts": {}, "followed": []})
        acc = rec["accounts"].setdefault(acc_id, {k: 0 for k in KINDS})
        rest = bool(state.get("rest_day"))
        if acc.get("rest_day") != rest and sdate == day:
            acc["rest_day"] = rest   # يوم الراحة كما كان في ذلك اليوم (للملخص)
            changed = True
        for k in KINDS:
            cur = int(done.get(k, 0) or 0)
            inc = cur - int(hw.get(k, 0) or 0)
            if inc > 0:
                acc[k] += inc
                hw[k] = cur
                changed = True
        # أسماء المتابَعين الجدد (followed_handles قائمة تراكمية): الفرق بالمجموعات
        known = set(hw.get("followed") or [])
        new = [h for h in followed if h not in known]
        if new:
            have = {(f["acc"], f["handle"]) for f in rec["followed"]}
            now_s = int(time.time())
            for h in new:
                if (acc_id, h) not in have:
                    rec["followed"].append({"acc": acc_id, "handle": h, "t": now_s})
            hw["followed"] = (list(known) + new)[-3000:]
            changed = True
        elif first_seen or hw.get("followed") is None:
            hw["followed"] = followed[-3000:]
        d["hw"][acc_id] = hw
        if changed or first_seen:
            _save(d)
        return changed


def day(date):
    """سجل يوم (تاريخ بتوقيت التقارير) أو None."""
    with _lock:
        return _load()["days"].get(date)


def days():
    with _lock:
        return sorted(_load()["days"])
