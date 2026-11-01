# -*- coding: utf-8 -*-
"""ملخص يومي يصل أدمن تيليجرام: ماذا فعلت الحسابات اليوم وعلى أي تغريدات.

المصدر: حالة كل حساب اليومية (state) + سجل التغريدات (seen). تُحفظ لقطة دورية في data/daily_stats.json
حتى لو أُغلق التطبيق قبل موعد الإرسال أو بدأ يوم جديد، فيصل ملخص اليوم الفائت عند التشغيل التالي.
"""
import json
import os
import threading
import time
from datetime import datetime

from .config import DATA_DIR, _atomic_save, load_accounts, load_config, load_seen, load_state
from .logger import get_logger

log = get_logger("summary")

STATS_PATH = os.path.join(DATA_DIR, "daily_stats.json")
SENT_PATH = os.path.join(DATA_DIR, "summary_sent.json")
KEEP_DAYS = 14
SNAPSHOT_EVERY_S = 300
_lock = threading.Lock()
_last_tick = {"snap": 0.0}


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def collect(day=None):
    """أرقام يوم معيّن (افتراضياً اليوم) من الحالة الحالية."""
    day = day or _today()
    accounts, seen = load_accounts(), load_seen()
    rows, totals = [], {"likes": 0, "retweets": 0, "comments": 0, "follows": 0}
    attention = []
    for aid, a in accounts.items():
        st = load_state(aid)
        same_day = st.get("date") == day
        done = (st.get("done") or {}) if same_day else {}
        row = {"id": aid, "handle": a.get("handle", aid), "enabled": a.get("enabled", True),
               **{k: int(done.get(k, 0) or 0) for k in totals}, "rest_day": bool(st.get("rest_day")) and same_day}
        for k in totals:
            totals[k] += row[k]
        rows.append(row)
        if a.get("enabled", True) and st.get("needs_relogin"):
            attention.append("@{} يحتاج إعادة تسجيل دخول".format(a.get("handle", aid)))
    tweets = []
    for tid, e in seen.items():
        if (e.get("detected_at") or "")[:10] != day:
            continue
        q = e.get("queue", [])
        total = sum(len(t.get("actions", [])) for t in q)
        done = sum(len([x for x in t.get("done", []) if x in t.get("actions", [])]) for t in q)
        failed = sum(len(t.get("failed", [])) for t in q)
        tweets.append({"id": tid, "priority": bool(e.get("priority")), "source": e.get("source", "manager"),
                       "accounts": len(q), "done": done, "total": total, "failed": failed,
                       "deleted": bool(e.get("deleted")), "cancelled": bool(e.get("cancelled")),
                       "completed": bool(e.get("completed_at"))})
    return {"date": day, "accounts": rows, "totals": totals, "tweets": tweets, "attention": attention}


def snapshot():
    """يحفظ أرقام اليوم الحالية (تُستدعى دورياً)."""
    data = collect()
    with _lock:
        store = _read(STATS_PATH, {})
        store[data["date"]] = data
        for old in sorted(store)[:-KEEP_DAYS]:
            store.pop(old, None)
        try:
            os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
            _atomic_save(store, STATS_PATH)
        except OSError:
            pass
    return data


def get_day(day):
    """أرقام يوم محفوظ، أو أرقام اليوم الحيّة إن كان هو اليوم."""
    if day == _today():
        return collect(day)
    return _read(STATS_PATH, {}).get(day)


def format_html(d):
    t = d["totals"]
    acts = sum(t.values())
    active = [r for r in d["accounts"] if r["enabled"]]
    lines = ["📈 <b>ملخص يوم {}</b>".format(d["date"]), "━━━━━━━━━━━━",
             "👥 الحسابات المفعّلة: <b>{}</b> من {}".format(len(active), len(d["accounts"])),
             "❤️ إعجابات: <b>{}</b>  🔁 ريتويت: <b>{}</b>  💬 تعليقات: <b>{}</b>  ➕ متابعات: <b>{}</b>".format(
                 t["likes"], t["retweets"], t["comments"], t["follows"]),
             "الإجمالي: <b>{}</b> عملية".format(acts)]
    tw = d["tweets"]
    if tw:
        ops_done = sum(x["done"] for x in tw)
        ops_total = sum(x["total"] for x in tw)
        failed = sum(x["failed"] for x in tw)
        lines += ["", "🗂 <b>التغريدات المجدولة اليوم: {}</b> ({} فورية ⚡)".format(len(tw), sum(1 for x in tw if x["priority"])),
                  "العمليات عليها: {}/{}{}".format(ops_done, ops_total, " · فاشلة: {}".format(failed) if failed else "")]
        for x in sorted(tw, key=lambda y: -y["done"])[:5]:
            tag = "🗑" if x["deleted"] else "🛑" if x["cancelled"] else "✅" if x["completed"] else "⏳"
            lines.append("{} <code>{}</code> {}{}/{}".format(tag, x["id"][-10:], "⚡ " if x["priority"] else "", x["done"], x["total"]))
        if len(tw) > 5:
            lines.append("… و{} أخرى".format(len(tw) - 5))
    else:
        lines += ["", "🗂 لا تغريدات جديدة اليوم."]
    lines += ["", "<b>حسب الحساب:</b>"]
    for r in sorted(active, key=lambda r: -(r["likes"] + r["retweets"] + r["comments"] + r["follows"])):
        rest = " 😴" if r.get("rest_day") else ""
        lines.append("• @{}{} — ❤️ {} 🔁 {} 💬 {} ➕ {}".format(r["handle"], rest, r["likes"], r["retweets"], r["comments"], r["follows"]))
    if d.get("attention"):
        lines += ["", "⚠️ <b>تحتاج انتباه:</b>"] + ["• " + a for a in d["attention"]]
    return "\n".join(lines)


def _parse_time(s):
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except (ValueError, TypeError):
        return 23, 50


def _due_days(cfg, now=None):
    """أيام تستحق الإرسال: اليوم إن بلغنا الموعد، وأي يوم سابق محفوظ لم يُرسل (لم يكن التطبيق يعمل وقت الموعد)."""
    now = now or datetime.now()
    sent = _read(SENT_PATH, {}).get("last", "")
    days = []
    for day in sorted(_read(STATS_PATH, {})):
        if sent < day < now.strftime("%Y-%m-%d"):
            days.append(day)
    h, m = _parse_time(cfg.get("time", "23:50"))
    today = now.strftime("%Y-%m-%d")
    if (now.hour, now.minute) >= (h, m) and sent < today:
        days.append(today)
    return days[-2:]  # لا نُغرق الأدمن بأكثر من يومين


def _mark_sent(day):
    try:
        _atomic_save({"last": day}, SENT_PATH)
    except OSError:
        pass


def tick(bot, now=None):
    """يُستدعى بشكل متكرر (مثلاً كل دقيقة): يحفظ لقطة دورية ويرسل الملخص المستحق. يرجع الأيام المرسلة."""
    cfg_all = load_config()
    cfg = cfg_all["telegram"].get("daily_summary", {})
    t = time.time()
    if t - _last_tick["snap"] >= SNAPSHOT_EVERY_S:
        _last_tick["snap"] = t
        try:
            snapshot()
        except Exception as e:
            log.warning("تعذّر حفظ لقطة اليوم: {}".format(e))
    if not cfg.get("enabled") or not cfg_all["telegram"].get("admin_ids"):
        return []
    sent_days = []
    for day in _due_days(cfg, now):
        d = get_day(day)
        if not d:
            _mark_sent(day)
            continue
        bot.send_summary(format_html(d))
        _mark_sent(day)
        sent_days.append(day)
        log.info("أُرسل الملخص اليومي: {}".format(day))
    return sent_days
