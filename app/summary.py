# -*- coding: utf-8 -*-
"""ملخص يومي يصل أدمن تيليجرام بتوقيت غزة (افتراضياً بعد منتصف الليل): ما فعلته الحسابات + من تُوبع من أشخاص جدد.

المصدر: دفتر النشاط اليومي (ledger) — مستقل عن توقيت جهاز التشغيل — + سجل التغريدات (seen).
اليوم المُغطّى: إن كان وقت الإرسال بعد منتصف الليل وقبل السادسة (مثل 00:30) فالتقرير لليوم السابق،
وإلا فلليوم نفسه. إن كان التطبيق مغلقاً وقت الإرسال يصل التقرير عند التشغيل التالي.
"""
import json
import os
from datetime import datetime, timedelta

from . import ledger, tz
from .config import DATA_DIR, _atomic_save, load_accounts, load_config, load_seen, load_state
from .logger import get_logger

log = get_logger("summary")

SENT_PATH = os.path.join(DATA_DIR, "summary_sent.json")
AFTER_MIDNIGHT_BEFORE_H = 6     # وقت إرسال قبل هذه الساعة = تقرير اليوم السابق
FOLLOWED_LIST_MAX = 25
MAX_CATCHUP_DAYS = 2


def _cfg():
    return load_config()["telegram"].get("daily_summary", {})


def _tzname():
    return _cfg().get("timezone") or tz.DEFAULT_TZ


def _today():
    return tz.today(_tzname())


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def collect(day=None):
    """أرقام يوم معيّن (تاريخ بتوقيت التقارير؛ افتراضياً اليوم الحالي) من الدفتر."""
    day = day or _today()
    rec = ledger.day(day) or {"accounts": {}, "followed": []}
    accounts = load_accounts()
    rows, totals = [], {k: 0 for k in ledger.KINDS}
    attention = []
    for aid, a in accounts.items():
        counts = rec["accounts"].get(aid, {})
        row = {"id": aid, "handle": a.get("handle", aid), "enabled": a.get("enabled", True),
               **{k: int(counts.get(k, 0) or 0) for k in ledger.KINDS},
               "rest_day": bool(load_state(aid).get("rest_day"))}
        for k in totals:
            totals[k] += row[k]
        rows.append(row)
        if a.get("enabled", True) and load_state(aid).get("needs_relogin"):
            attention.append("@{} يحتاج إعادة تسجيل دخول".format(a.get("handle", aid)))
    handle_of = {aid: a.get("handle", aid) for aid, a in accounts.items()}
    followed = [{"acc": f["acc"], "by": handle_of.get(f["acc"], f["acc"]), "handle": f["handle"]} for f in rec.get("followed", [])]
    unique = []
    for f in followed:
        if f["handle"].lower() not in [u.lower() for u in unique]:
            unique.append(f["handle"])
    tweets = []
    tzinfo = tz.tzinfo(_tzname())
    for tid, e in load_seen().items():
        if not _detected_on(e.get("detected_at"), day, tzinfo):
            continue
        q = e.get("queue", [])
        total = sum(len(t.get("actions", [])) for t in q)
        done = sum(len([x for x in t.get("done", []) if x in t.get("actions", [])]) for t in q)
        failed = sum(len(t.get("failed", [])) for t in q)
        tweets.append({"id": tid, "priority": bool(e.get("priority")), "source": e.get("source", "manager"),
                       "accounts": len(q), "done": done, "total": total, "failed": failed,
                       "deleted": bool(e.get("deleted")), "cancelled": bool(e.get("cancelled")),
                       "completed": bool(e.get("completed_at"))})
    return {"date": day, "accounts": rows, "totals": totals, "tweets": tweets, "attention": attention,
            "followed": followed, "followed_unique": unique, "has_data": bool(rec["accounts"] or rec.get("followed") or tweets)}


def _detected_on(iso, day, tzinfo):
    """تاريخ اكتشاف التغريدة (يُحفظ بتوقيت الجهاز) محوّلاً لتوقيت التقارير."""
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return (iso or "")[:10] == day
    if dt.tzinfo is None:
        dt = dt.astimezone()          # توقيت الجهاز
    return dt.astimezone(tzinfo).strftime("%Y-%m-%d") == day


def current_day():
    """اليوم الذي يُعرض افتراضياً عند الطلب اليدوي: بعد منتصف الليل وقبل السادسة (وقت إرسال بعد المنتصف) نعرض اليوم الذي انتهى للتو."""
    cfg = _cfg()
    now = tz.now(cfg.get("timezone") or tz.DEFAULT_TZ)
    h, _ = _parse_time(cfg.get("time", "00:30"))
    if h < AFTER_MIDNIGHT_BEFORE_H and now.hour < AFTER_MIDNIGHT_BEFORE_H:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def get_day(day):
    return collect(day)


def format_html(d):
    t = d["totals"]
    acts = sum(t.values())
    active = [r for r in d["accounts"] if r["enabled"]]
    uniq = d.get("followed_unique", [])
    lines = ["📈 <b>ملخص يوم {}</b> (بتوقيت غزة)".format(d["date"]), "━━━━━━━━━━━━",
             "👥 الحسابات المفعّلة: <b>{}</b> من {}".format(len(active), len(d["accounts"])),
             "❤️ إعجابات: <b>{}</b>  🔁 ريتويت: <b>{}</b>  💬 تعليقات: <b>{}</b>".format(t["likes"], t["retweets"], t["comments"]),
             "الإجمالي: <b>{}</b> عملية".format(acts), "",
             "➕ <b>أشخاص جدد تمت متابعتهم اليوم: {}</b>".format(len(uniq) if uniq else t["follows"])]
    if uniq:
        shown = uniq[:FOLLOWED_LIST_MAX]
        lines.append("  " + " ".join("@{}".format(h) for h in shown) + (" … و{} آخرون".format(len(uniq) - len(shown)) if len(uniq) > len(shown) else ""))
    elif t["follows"]:
        lines.append("  (الأسماء غير متوفرة لأن التسجيل بدأ بعد هذه المتابعات)")
    tw = d["tweets"]
    if tw:
        live = [x for x in tw if not (x["deleted"] or x["cancelled"])]
        ops_done = sum(x["done"] for x in tw)
        ops_total = sum(x["total"] for x in live)
        failed = sum(x["failed"] for x in tw)
        skipped = len(tw) - len(live)
        lines += ["", "🗂 <b>التغريدات المجدولة: {}</b> ({} فورية ⚡{})".format(
            len(tw), sum(1 for x in tw if x["priority"]), " · {} ملغاة/محذوفة".format(skipped) if skipped else ""),
                  "العمليات المنفّذة: {} من {} مطلوبة{}".format(ops_done, ops_total, " · فاشلة: {}".format(failed) if failed else "")]
        for x in sorted(tw, key=lambda y: -y["done"])[:5]:
            tag = "🗑" if x["deleted"] else "🛑" if x["cancelled"] else "✅" if x["completed"] else "⏳"
            lines.append("{} <code>{}</code> {}{}/{}".format(tag, x["id"][-10:], "⚡ " if x["priority"] else "", x["done"], x["total"]))
        if len(tw) > 5:
            lines.append("… و{} أخرى".format(len(tw) - 5))
    else:
        lines += ["", "🗂 لا تغريدات جديدة."]
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
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except (ValueError, TypeError):
        return 0, 30


def send_moment(day, time_str, tzinfo):
    """لحظة إرسال تقرير اليوم day: بعد منتصف الليل (يوم+1) إن كان الوقت قبل السادسة، وإلا في اليوم نفسه."""
    h, m = _parse_time(time_str)
    base = datetime.strptime(day, "%Y-%m-%d")
    if h < AFTER_MIDNIGHT_BEFORE_H:
        base += timedelta(days=1)
    return base.replace(hour=h, minute=m, tzinfo=tzinfo)


def _last_sent():
    return _read(SENT_PATH, {}).get("last", "")


def _mark_sent(day):
    try:
        _atomic_save({"last": day}, SENT_PATH)
    except OSError:
        pass


def due_days(cfg=None, now=None):
    """أيام تقارير حان وقتها ولم تُرسل (آخر 3 أيام فقط، وآخر يومين كحدّ أقصى)."""
    cfg = cfg if cfg is not None else _cfg()
    tzinfo = tz.tzinfo(cfg.get("timezone") or tz.DEFAULT_TZ)
    now = now or datetime.now(tzinfo)
    sent = _last_sent()
    if not sent:
        # أول تشغيل للميزة: لا نُغرق الأدمن بتقارير أيام سبقت تفعيلها؛ أقدم تقرير ممكن هو تقرير أمس
        sent = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    out = []
    for back in (3, 2, 1, 0):
        day = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        if day > sent and now >= send_moment(day, cfg.get("time", "00:30"), tzinfo):
            out.append(day)
    return out[-MAX_CATCHUP_DAYS:]


def tick(bot, now=None):
    """يُستدعى كل دورة: يرسل الملخص المستحق (مرة لكل يوم). يرجع الأيام المرسلة."""
    cfg = _cfg()
    if not cfg.get("enabled") or not load_config()["telegram"].get("admin_ids"):
        return []
    sent_days = []
    for day in due_days(cfg, now):
        d = collect(day)
        if d["has_data"]:           # يوم بلا أي نشاط (التطبيق كان مغلقاً): لا ضجيج
            bot.send_summary(format_html(d))
            sent_days.append(day)
            log.info("أُرسل الملخص اليومي: {}".format(day))
        _mark_sent(day)
    return sent_days
