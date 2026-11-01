# -*- coding: utf-8 -*-
"""قوائم بوت تيليجرام: أزرار داخل الرسالة (inline keyboard) وتعديل الإعدادات والكلمات المفتاحية من البوت.

الوحدة لا تعرف شيئاً عن الشبكة: كل دالة ترجع View (نص HTML + صفوف أزرار) يرسلها TelegramBot،
وهذا يجعلها قابلة للاختبار بالكامل. التعديلات تمر عبر svc.patch_config فتخضع لنفس تحقق الواجهة.
"""
import hashlib
import html
import re
import time

PENDING_TTL_S = 300
PAGE = 6          # عناصر في الصفحة (حسابات)
LIST_PAGE = 8     # عناصر في الصفحة (كلمات/تعليقات)
MAX_TEXT = 3900   # حد تيليجرام 4096


def esc(s):
    return html.escape(str(s), quote=False)


def bar(done, total, n=10):
    p = 0 if not total else max(0.0, min(1.0, done / total))
    k = round(p * n)
    return "▰" * k + "▱" * (n - k) + " {}%".format(round(p * 100))


def short_hash(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:6]


class View:
    """رسالة جاهزة: نص HTML + صفوف أزرار [(تسمية، callback_data)] + تنبيه اختياري لـ answerCallbackQuery."""

    def __init__(self, text, rows=None, notice=None):
        self.text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"
        self.rows = rows or []
        self.notice = notice


HOME_BTN = ("🏠 القائمة", "home")

# ------------------------------------------------------------------ كتالوج الإعدادات
CATS = [
    ("gen", "🎛 عام"), ("eng", "🎯 التفاعل"), ("beh", "🕒 السلوك اليومي"), ("fol", "➕ المتابعة"),
    ("wrm", "🔥 الإحماء"), ("cmt", "💬 التعليقات"), ("net", "🌐 الشبكة WARP"), ("tg", "🤖 البوت"),
]
# key, cat, path, kind, label, مثال
ITEMS = [
    ("mh", "gen", "manager_handle", "text", "حساب المدير", "username"),
    ("mi", "gen", "monitor.interval_min", "range", "فاصل فحص المدير (دقيقة)", "6-14"),
    ("ma", "gen", "monitor.max_tweet_age_hours", "int", "تجاهل تغريدات أقدم من (ساعة)", "12"),
    ("hl", "beh", "browser.headless", "bool", "تشغيل بدون نافذة", ""),
    ("ap", "eng", "engagement.accounts_per_tweet", "range", "عدد الحسابات لكل تغريدة", "6-12"),
    ("sh", "eng", "engagement.spread_hours", "range", "مدة التوزيع (ساعة)", "0.5-4"),
    ("mc", "eng", "engagement.max_comments_per_tweet", "int", "أقصى تعليقات للتغريدة", "8"),
    ("ml", "eng", "engagement.action_mix.like", "pct", "احتمال الإعجاب", "75"),
    ("mr", "eng", "engagement.action_mix.retweet", "pct", "احتمال الريتويت", "18"),
    ("mm", "eng", "engagement.action_mix.comment", "pct", "احتمال التعليق", "30"),
    ("ah", "beh", "behavior.active_hours", "timerange", "ساعات النشاط", "08:00-23:00"),
    ("sd", "beh", "behavior.sessions_per_day", "range", "الجلسات يومياً", "1-3"),
    ("ld", "beh", "behavior.max_likes_per_day", "range", "حد الإعجابات يومياً", "25-45"),
    ("rd", "beh", "behavior.max_retweets_per_day", "range", "حد الريتويت يومياً", "2-6"),
    ("cd", "beh", "behavior.max_comments_per_day", "range", "حد التعليقات يومياً", "1-4"),
    ("rc", "beh", "behavior.rest_day_chance", "pct", "احتمال يوم راحة", "12"),
    ("fe", "fol", "follow.enabled", "bool", "المتابعة التلقائية", ""),
    ("ft", "fol", "follow.daily_target", "range", "الهدف اليومي", "5-10"),
    ("fs", "fol", "follow.per_session", "range", "متابعات كل جلسة", "1-3"),
    ("fb", "fol", "follow.require_bio", "bool", "اشتراط وجود نبذة", ""),
    ("fv", "fol", "follow.skip_verified", "bool", "تخطي الموثّقين", ""),
    ("fn", "fol", "follow.min_followers", "int", "أقل عدد متابعين", "50"),
    ("fx", "fol", "follow.max_followers", "int", "أكثر عدد متابعين", "150000"),
    ("we", "wrm", "warmup.enabled", "bool", "الإحماء اليومي", ""),
    ("wm", "wrm", "warmup.session_minutes", "range", "مدة جلسة الإحماء (دقيقة)", "8-15"),
    ("ce", "cmt", "comments.en_ratio", "pct", "نسبة التعليقات الإنجليزية", "90"),
    ("cc", "cmt", "comments.contextual", "bool", "تعليقات سياقية", ""),
    ("cl", "cmt", "comments.match_language", "bool", "اللغة تتبع لغة التغريدة", ""),
    ("ca", "cmt", "comments.ai.enabled", "bool", "التوليد بالذكاء الاصطناعي", ""),
    ("we2", "net", "warp.enabled", "bool", "تدوير IP عبر WARP", ""),
    ("wa", "net", "warp.accounts_per_ip", "int", "الحسابات لكل IP", "5"),
    ("wn", "net", "warp.ensure_new_ip", "bool", "التأكد من تغيّر IP", ""),
    ("ti", "tg", "telegram.instant", "bool", "تنفيذ فوري ⚡ للروابط", ""),
    ("tl", "tg", "telegram.action_mix.like", "pct", "⚡ احتمال الإعجاب (فوري)", "100"),
    ("tr", "tg", "telegram.action_mix.retweet", "pct", "⚡ احتمال الريتويت (فوري)", "100"),
    ("tc", "tg", "telegram.action_mix.comment", "pct", "⚡ احتمال التعليق (فوري)", "100"),
    ("td", "tg", "telegram.action_delay_s", "range", "تأخير بين عمليات الحساب (ثانية)", "2-6"),
    ("tn", "tg", "telegram.notify", "bool", "إشعار عند اكتمال التفاعل", ""),
    ("ce2", "tg", "telegram.channel.enabled", "bool", "📣 مراقبة قناة الأدمن", ""),
    ("ci", "tg", "telegram.channel.instant", "bool", "تنفيذ فوري لتغريدات القناة", ""),
    ("cf", "tg", "telegram.channel.fallback_interval_min", "range", "الفحص الاحتياطي لـX (دقيقة)", "60-90"),
    ("ts", "tg", "telegram.spread_hours", "range", "مدة التوزيع عند إيقاف الفوري (ساعة)", "0.1-1"),
]
BY_KEY = {i[0]: i for i in ITEMS}
# تعديل الإعدادات الحساسة (التوكن/مفاتيح API) غير متاح من البوت عمداً.


def get_path(d, path):
    for k in path.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def nest(path, value):
    out = value
    for k in reversed(path.split(".")):
        out = {k: out}
    return out


def fmt_value(kind, v):
    if v is None or v == "":
        return "—"
    if kind == "bool":
        return "✅ مفعّل" if v else "⛔ معطّل"
    if kind == "pct":
        return "{}%".format(round(float(v) * 100))
    if kind == "range":
        return "{}–{}".format(*[("%g" % x) for x in v])
    if kind == "timerange":
        return "{} ← {}".format(*v)
    return esc(v)


def parse_value(kind, text):
    """يحوّل نص المستخدم إلى قيمة الإعداد، أو يرمي ValueError برسالة عربية."""
    t = text.strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٪", "0123456789.%"))
    if kind == "text":
        return t.lstrip("@")
    if kind == "pct":
        n = float(t.rstrip("%").strip())
        if not 0 <= n <= 100:
            raise ValueError("النسبة بين 0 و100")
        return n / 100
    if kind == "int":
        n = float(t)
        if n != int(n) or n < 0:
            raise ValueError("أرسل رقماً صحيحاً موجباً")
        return int(n)
    if kind == "range":
        nums = [float(x) for x in re.split(r"[\s\-–—،,؛;]+|\bto\b", t) if x]
        if len(nums) == 1:
            nums = [nums[0], nums[0]]
        if len(nums) != 2 or nums[0] > nums[1] or nums[0] < 0:
            raise ValueError("أرسل مجالاً صحيحاً مثل: 6-12")
        return [int(n) if n == int(n) else n for n in nums]
    if kind == "timerange":
        m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*[-–—\s]+\s*(\d{1,2}):(\d{2})", t)
        if not m:
            raise ValueError("أرسل مجال الوقت مثل: 08:00-23:00")
        return ["{:02d}:{}".format(int(m.group(1)), m.group(2)), "{:02d}:{}".format(int(m.group(3)), m.group(4))]
    raise ValueError("نوع غير مدعوم")


def split_words(text):
    """كلمات مفتاحية من نص: سطر أو فاصلة (، , ؛) لكل كلمة، بدون تكرار."""
    out = []
    for w in re.split(r"[\n,،؛;]+", text):
        w = w.strip()
        if w and w.lower() not in [x.lower() for x in out]:
            out.append(w[:60])
    return out


class BotUI:
    def __init__(self, svc):
        self.svc = svc
        self.pending = {}  # chat_id -> {"kind":..., "key":..., "exp":...}

    # ------------------------------------------------------------------ مساعدات
    def _set_pending(self, chat_id, **kw):
        self.pending[chat_id] = dict(kw, exp=time.time() + PENDING_TTL_S)

    def has_pending(self, chat_id):
        p = self.pending.get(chat_id)
        if p and p["exp"] < time.time():
            self.pending.pop(chat_id, None)
            return False
        return bool(p)

    def cancel(self, chat_id):
        self.pending.pop(chat_id, None)

    @staticmethod
    def _nav(*rows):
        return [list(r) for r in rows]

    # ------------------------------------------------------------------ القائمة الرئيسية
    def home(self, greeting=False):
        st = self.svc.state()
        e, s = st["engine"], st["stats"]
        running = e["status"] == "running"
        head = "👋 أهلاً بك في <b>SmartEngage</b>\n\n" if greeting else "<b>🏠 القائمة الرئيسية</b>\n\n"
        text = (head +
                "⚙️ المحرك: {}\n👥 الحسابات: <b>{}</b> مفعّلة · <b>{}</b> جاهزة\n📌 عمليات معلّقة: <b>{}</b>\n\n"
                "أرسل رابط أي تغريدة لتتفاعل معها الحسابات، أو اختر من القائمة:").format(
                    ("يعمل ✅" + (" (محاكاة)" if e.get("dry_run") else "")) if running else ("جارٍ الإيقاف…" if e["status"] == "stopping" else "متوقف ⏸"),
                    s["enabled"], s["ready"], s["pending"])
        rows = self._nav(
            [("📊 الحالة", "st"), ("⚙️ المحرك", "eng")],
            [("👥 الحسابات", "acc:0"), ("🗂 التغريدات", "tw")],
            [("🔑 الكلمات المفتاحية", "kw"), ("💬 التعليقات", "cm")],
            [("🛠 الإعدادات", "set"), ("🌐 WARP", "wp")],
        )
        return View(text, rows)

    def status(self):
        st = self.svc.state()
        e, s, tg = st["engine"], st["stats"], st["telegram"]
        lines = ["<b>📊 حالة التطبيق</b>", ""]
        lines.append("⚙️ المحرك: " + ("يعمل ✅" if e["status"] == "running" else "متوقف ⏸"))
        if e.get("last_error"):
            lines.append("⚠️ آخر خطأ: <code>{}</code>".format(esc(e["last_error"][:120])))
        lines += ["👥 الحسابات: {} من {} مفعّلة".format(s["enabled"], s["accounts"]),
                  "🟢 جاهزة للعمل: {}".format(s["ready"]),
                  "📌 عمليات معلّقة: {}".format(s["pending"]),
                  "🗂 تغريدات في السجل: {}".format(s["tweets"]), ""]
        lines.append("📡 المصادر: " + ("المدير @{}".format(esc(st["manager_handle"])) if st["manager_handle"] else "المدير غير محدد") +
                     " · تيليجرام " + ("✅" if tg["state"] == "running" else "⏸"))
        return View("\n".join(lines), self._nav([("🔄 تحديث", "st"), HOME_BTN]))

    # ------------------------------------------------------------------ المحرك
    def engine(self, notice=None):
        e = self.svc.state()["engine"]
        run = e["status"] == "running"
        stopping = e["status"] == "stopping"
        text = "<b>⚙️ المحرك</b>\n\nالحالة: {}{}".format(
            "يعمل ✅" if run else "جارٍ الإيقاف بعد الجلسة الجارية…" if stopping else "متوقف ⏸",
            " · محاكاة" if run and e.get("dry_run") else "")
        if e.get("last_error"):
            text += "\n⚠️ آخر خطأ: <code>{}</code>".format(esc(e["last_error"][:150]))
        btns = [("⏸ إيقاف", "eng:stop")] if run or stopping else [("▶️ تشغيل", "eng:start"), ("🧪 محاكاة", "eng:dry")]
        return View(text, self._nav(btns, [("🔄 تحديث", "eng"), HOME_BTN]), notice)

    # ------------------------------------------------------------------ الحسابات
    def accounts(self, page=0, notice=None):
        accs = self.svc.accounts_view()
        pages = max(1, -(-len(accs) // PAGE))
        page = max(0, min(page, pages - 1))
        chunk = accs[page * PAGE:(page + 1) * PAGE]
        lines = ["<b>👥 الحسابات</b> ({} · صفحة {}/{})".format(len(accs), page + 1, pages), ""]
        rows = []
        for a in chunk:
            d, c = a.get("done") or {}, a.get("caps") or {}
            state = "⚠️ يحتاج دخول" if a["needs_relogin"] else "🟢" if a["has_session"] else "⚪ غير مسجّل"
            lines.append("{} <b>@{}</b> {}\n    ❤️ {}/{} · 🔁 {}/{} · 💬 {}/{}".format(
                "✅" if a["enabled"] else "⛔", esc(a["handle"]), state,
                d.get("likes", 0), c.get("likes", "—"), d.get("retweets", 0), c.get("retweets", "—"),
                d.get("comments", 0), c.get("comments", "—")))
            rows.append([("{} @{}".format("✅" if a["enabled"] else "⛔", a["handle"]), "acc:t:{}:{}".format(a["id"], page))])
        if not accs:
            lines.append("لا توجد حسابات. أضفها من التطبيق على الكمبيوتر (تسجيل الدخول يتم هناك).")
        lines.append("\nاضغط على حساب لتفعيله أو إيقافه.")
        nav = []
        if page > 0:
            nav.append(("◀️ السابق", "acc:{}".format(page - 1)))
        if page < pages - 1:
            nav.append(("التالي ▶️", "acc:{}".format(page + 1)))
        if nav:
            rows.append(nav)
        rows.append([HOME_BTN])
        return View("\n".join(lines), rows, notice)

    def toggle_account(self, aid, page):
        accs = {a["id"]: a for a in self.svc.accounts_view()}
        if aid not in accs:
            return self.accounts(page, "الحساب غير موجود")
        new = not accs[aid]["enabled"]
        self.svc.set_account_enabled(aid, new)
        return self.accounts(page, "{} @{}".format("تم التفعيل ✅" if new else "تم الإيقاف ⛔", accs[aid]["handle"]))

    # ------------------------------------------------------------------ التغريدات
    def tweets(self):
        items = self.svc.tweets_view(6)
        lines = ["<b>🗂 آخر التغريدات</b>", ""]
        rows = []
        for t in items:
            tag = "⚡" if t.get("priority") else "🕒"
            st = "🗑 محذوفة" if t["deleted"] else "🛑 ملغاة" if t.get("cancelled") else "✅ مكتملة" if t["completed"] else "⏳"
            lines.append("{} <code>{}</code> {}\n    {} · {}/{} عملية · {} حساب".format(
                tag, t["id"][-10:], st, bar(t["done"], t["total"]), t["done"], t["total"], t["accounts"]))
            rows.append([("🔎 {}".format(t["id"][-10:]), "tw:r:{}".format(t["id"]))])
        if not items:
            lines.append("لا توجد تغريدات بعد. أرسل رابطاً لتبدأ.")
        rows.append([("🔄 تحديث", "tw"), HOME_BTN])
        return View("\n".join(lines), rows)

    def tweet_detail(self, tid, notice=None):
        d = self.svc.tweet_detail(tid)
        if not d:
            return View("التغريدة غير موجودة.", self._nav([HOME_BTN]))
        lines = ["<b>🔎 تغريدة</b> <code>{}</code> {}".format(esc(tid), "⚡" if d["priority"] else ""), "",
                 bar(d["done"], d["total"]) + "  ({}/{})".format(d["done"], d["total"])]
        for name, key in (("❤️ إعجاب", "like"), ("🔁 ريتويت", "retweet"), ("💬 تعليق", "comment")):
            if d["by"].get(key, [0, 0])[1]:
                lines.append("{}: {}/{}".format(name, *d["by"][key]))
        lines.append("👥 الحسابات: {} · فاشلة: {}".format(d["accounts"], d["failed"]))
        lines.append("الحالة: " + ("🗑 محذوفة" if d["deleted"] else "🛑 ملغاة" if d.get("cancelled") else "✅ مكتملة" if d["completed"] else "⏳ قيد التنفيذ"))
        if d["url"]:
            lines.append('<a href="{}">فتح التغريدة</a>'.format(esc(d["url"])))
        open_ = not (d["deleted"] or d["completed"] or d.get("cancelled"))
        rows = [[("🔄 تحديث", "tw:r:{}".format(tid)), ("🗂 التغريدات", "tw")]]
        if open_:
            rows.insert(0, [("🛑 إلغاء هذه التغريدة", "tw:x:{}".format(tid))])
        rows.append([HOME_BTN])
        return View("\n".join(lines), rows, notice)

    # ------------------------------------------------------------------ الإعدادات
    def settings(self):
        rows = [[(label, "set:c:" + key)] for key, label in CATS]
        rows = [[rows[i][0], rows[i + 1][0]] if i + 1 < len(rows) else [rows[i][0]] for i in range(0, len(rows), 2)]
        rows.append([HOME_BTN])
        return View("<b>🛠 الإعدادات</b>\n\nاختر القسم. الأزرار تبدّل القيم فوراً، والقيم الرقمية تطلب منك إرسال القيمة الجديدة.\n"
                    "<i>التوكن ومفاتيح API تُعدَّل من التطبيق فقط.</i>", rows)

    def settings_cat(self, cat, notice=None):
        cfg = self.svc.cfg()
        label = dict(CATS).get(cat, "")
        lines = ["<b>{}</b>".format(label), ""]
        rows = []
        for key, c, path, kind, name, ex in ITEMS:
            if c != cat:
                continue
            v = get_path(cfg, path)
            lines.append("• {}: <b>{}</b>".format(esc(name), fmt_value(kind, v)))
            rows.append([("{} — {}".format(name, html.unescape(re.sub(r"<[^>]+>", "", fmt_value(kind, v)))), "set:i:" + key)])
        lines.append("\nاضغط على الإعداد لتعديله.")
        rows.append([("◀️ الأقسام", "set"), HOME_BTN])
        return View("\n".join(lines), rows, notice)

    def setting_item(self, key, chat_id):
        it = BY_KEY.get(key)
        if not it:
            return self.settings()
        _, cat, path, kind, name, ex = it
        if kind == "bool":
            cur = bool(get_path(self.svc.cfg(), path))
            return self._apply(chat_id, it, not cur, back=cat)
        self._set_pending(chat_id, kind="setting", key=key)
        cur = fmt_value(kind, get_path(self.svc.cfg(), path))
        hint = {"range": "مجال من رقمين مثل <code>{}</code>", "pct": "نسبة مئوية مثل <code>{}</code>", "int": "رقم صحيح مثل <code>{}</code>",
                "timerange": "مجال وقت مثل <code>{}</code>", "text": "نص مثل <code>{}</code> (أرسل <code>-</code> لتفريغه)"}.get(kind, "{}").format(esc(ex))
        return View("✏️ <b>{}</b>\nالقيمة الحالية: <b>{}</b>\n\nأرسل القيمة الجديدة — {}".format(esc(name), cur, hint),
                    self._nav([("❌ إلغاء", "set:c:" + cat)]))

    def _apply(self, chat_id, it, value, back):
        _, cat, path, kind, name, ex = it
        try:
            self.svc.patch_config(nest(path, value))
        except Exception as e:  # ApiError وغيرها: رسالة عربية واضحة
            return View("❌ لم يُحفظ: {}".format(esc(e)), self._nav([("🔁 حاول مجدداً", "set:i:" + it[0]), ("◀️ رجوع", "set:c:" + cat)]))
        self.cancel(chat_id)
        v = self.settings_cat(back, notice="تم الحفظ ✅")
        v.text = "✅ تم حفظ <b>{}</b>: <b>{}</b>\n\n".format(esc(name), fmt_value(kind, value)) + v.text
        return v

    # ------------------------------------------------------------------ الكلمات المفتاحية
    def keywords(self, notice=None):
        words = self.svc.cfg()["follow"]["queries"]
        lines = ["<b>🔑 الكلمات المفتاحية</b>", "تُستخدم للبحث عن المهتمين ومتابعتهم ({} كلمة)".format(len(words)), ""]
        lines += ["{}. <code>{}</code>".format(i + 1, esc(w)) for i, w in enumerate(words)] or ["لا توجد كلمات بعد."]
        return View("\n".join(lines), self._nav([("➕ إضافة", "kw:add"), ("🗑 حذف", "kw:del:0")],
                                                 [("🧹 مسح الكل", "kw:clr"), ("✏️ استبدال الكل", "kw:rep")], [HOME_BTN]), notice)

    def _save_words(self, words):
        self.svc.patch_config({"follow": {"queries": words}})

    def keyword_delete_list(self, page=0):
        words = self.svc.cfg()["follow"]["queries"]
        pages = max(1, -(-len(words) // LIST_PAGE))
        page = max(0, min(page, pages - 1))
        rows = []
        chunk = list(enumerate(words))[page * LIST_PAGE:(page + 1) * LIST_PAGE]
        for i in range(0, len(chunk), 2):
            rows.append([("🗑 " + w[:22], "kw:rm:{}:{}".format(idx, short_hash(w))) for idx, w in chunk[i:i + 2]])
        nav = []
        if page > 0:
            nav.append(("◀️", "kw:del:{}".format(page - 1)))
        if page < pages - 1:
            nav.append(("▶️", "kw:del:{}".format(page + 1)))
        if nav:
            rows.append(nav)
        rows.append([("◀️ رجوع", "kw"), HOME_BTN])
        return View("<b>🗑 حذف كلمة</b>\nاضغط على الكلمة لحذفها ({}/{})".format(page + 1, pages), rows)

    def keyword_remove(self, idx, h):
        words = list(self.svc.cfg()["follow"]["queries"])
        if idx >= len(words) or short_hash(words[idx]) != h:
            return self.keyword_delete_list(0)
        gone = words.pop(idx)
        self._save_words(words)
        v = self.keyword_delete_list(0)
        v.notice = "حُذفت: " + gone
        return v

    # ------------------------------------------------------------------ قوالب التعليقات
    def comments(self, notice=None):
        items = self.svc.comments_list()
        lines = ["<b>💬 قوالب التعليقات</b>", "العدد: <b>{}</b> — آخر 8:".format(len(items)), ""]
        lines += ["• {}".format(esc(c[:80])) for c in items[-8:]] or ["لا توجد قوالب."]
        return View("\n".join(lines), self._nav([("➕ إضافة", "cm:add"), ("🗑 حذف", "cm:del:0")], [HOME_BTN]), notice)

    def comment_delete_list(self, page=0):
        items = self.svc.comments_list()
        pages = max(1, -(-len(items) // LIST_PAGE))
        page = max(0, min(page, pages - 1))
        rows = [[("🗑 " + c[:30], "cm:rm:{}:{}".format(i, short_hash(c)))] for i, c in list(enumerate(items))[page * LIST_PAGE:(page + 1) * LIST_PAGE]]
        nav = []
        if page > 0:
            nav.append(("◀️", "cm:del:{}".format(page - 1)))
        if page < pages - 1:
            nav.append(("▶️", "cm:del:{}".format(page + 1)))
        if nav:
            rows.append(nav)
        rows.append([("◀️ رجوع", "cm"), HOME_BTN])
        return View("<b>🗑 حذف قالب</b> ({}/{})".format(page + 1, pages), rows)

    def comment_remove(self, idx, h):
        items = list(self.svc.comments_list())
        if idx >= len(items) or short_hash(items[idx]) != h:
            return self.comment_delete_list(0)
        if len(items) == 1:
            v = self.comments()
            v.notice = "لا يمكن حذف آخر قالب"
            return v
        items.pop(idx)
        self.svc.set_comments_list(items)
        v = self.comment_delete_list(0)
        v.notice = "تم الحذف"
        return v

    # ------------------------------------------------------------------ WARP
    def warp(self, info=None, busy=False):
        cfg = self.svc.cfg()["warp"]
        lines = ["<b>🌐 WARP</b>", "", "التدوير: " + ("✅ مفعّل" if cfg["enabled"] else "⛔ معطّل") + " · كل <b>{}</b> حساب".format(cfg["accounts_per_ip"])]
        if busy:
            lines.append("⏳ جارٍ التنفيذ… قد يستغرق حتى دقيقة.")
        elif info:
            lines.append(info)
        return View("\n".join(lines), self._nav([("🔍 فحص الحالة", "wp:st"), ("🔄 تدوير الآن", "wp:rot")], [HOME_BTN]))

    # ------------------------------------------------------------------ المدخلات النصية
    def on_text(self, chat_id, text):
        """يعالج رسالة نصية أثناء انتظار إدخال. يرجع View أو None إن لم يكن هناك انتظار."""
        if not self.has_pending(chat_id):
            return None
        p = self.pending[chat_id]
        kind = p["kind"]
        if kind == "setting":
            it = BY_KEY[p["key"]]
            try:
                value = "" if (it[3] == "text" and text.strip() == "-") else parse_value(it[3], text)
            except ValueError as e:
                return View("❌ {}\nأرسل قيمة صحيحة أو اضغط إلغاء.".format(esc(e)), self._nav([("❌ إلغاء", "set:c:" + it[1])]))
            return self._apply(chat_id, it, value, back=it[1])
        if kind in ("kw_add", "kw_rep"):
            words = split_words(text)
            if not words:
                return View("❌ لم أجد كلمات. أرسل كلمة في كل سطر أو افصل بفاصلة.", self._nav([("❌ إلغاء", "kw")]))
            cur = list(self.svc.cfg()["follow"]["queries"])
            new = words if kind == "kw_rep" else cur + [w for w in words if w.lower() not in [c.lower() for c in cur]]
            try:
                self._save_words(new)
            except Exception as e:
                return View("❌ لم يُحفظ: {}".format(esc(e)), self._nav([("◀️ رجوع", "kw")]))
            self.cancel(chat_id)
            v = self.keywords("تم الحفظ ✅")
            v.text = "✅ {} <b>{}</b> كلمة\n\n".format("استُبدلت القائمة بـ" if kind == "kw_rep" else "أُضيفت", len(words) if kind == "kw_rep" else len(new) - len(cur)) + v.text
            return v
        if kind == "cm_add":
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if not lines or any(len(l) > 280 for l in lines):
                return View("❌ أرسل تعليقاً في كل سطر (بحد أقصى 280 حرفاً).", self._nav([("❌ إلغاء", "cm")]))
            try:
                self.svc.set_comments_list(self.svc.comments_list() + lines)
            except Exception as e:
                return View("❌ لم يُحفظ: {}".format(esc(e)), self._nav([("◀️ رجوع", "cm")]))
            self.cancel(chat_id)
            v = self.comments("تم الحفظ ✅")
            v.text = "✅ أُضيف <b>{}</b> قالب\n\n".format(len(lines)) + v.text
            return v
        self.cancel(chat_id)
        return None

    # ------------------------------------------------------------------ توجيه الأزرار
    def route(self, chat_id, data):
        """يعالج callback_data ويرجع View. الأخطاء تُحوَّل لرسالة بدل أن تُسقط البوت."""
        parts = data.split(":")
        head = parts[0]
        if head != "set" or len(parts) < 2 or parts[1] != "i":
            if head in ("home", "st", "eng", "acc", "tw", "set", "kw", "cm", "wp"):
                self.cancel(chat_id)  # أي تنقّل يلغي الإدخال المعلّق
        if data == "home":
            return self.home()
        if data == "cancel":
            return self.home()
        if data == "st":
            return self.status()
        if head == "eng":
            if len(parts) == 1:
                return self.engine()
            if parts[1] in ("start", "dry"):
                try:
                    self.svc.engine_start(parts[1] == "dry")
                    return self.engine("تم التشغيل ▶️")
                except Exception as e:
                    return View("❌ تعذّر التشغيل: {}".format(esc(e)), self._nav([("◀️ رجوع", "eng")]))
            if parts[1] == "stop":
                self.svc.engine_stop()
                return self.engine("طُلب الإيقاف")
        if head == "acc":
            if len(parts) >= 4 and parts[1] == "t":
                return self.toggle_account(parts[2], int(parts[3]))
            return self.accounts(int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
        if head == "tw":
            if len(parts) >= 3 and parts[1] == "r":
                return self.tweet_detail(parts[2], "تم التحديث")
            if len(parts) >= 3 and parts[1] == "x":
                self.svc.cancel_tweet(parts[2])
                return self.tweet_detail(parts[2], "أُلغيت التغريدة 🛑")
            return self.tweets()
        if head == "set":
            if len(parts) == 1:
                return self.settings()
            if parts[1] == "c":
                return self.settings_cat(parts[2])
            if parts[1] == "i":
                return self.setting_item(parts[2], chat_id)
        if head == "kw":
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "":
                return self.keywords()
            if sub in ("add", "rep"):
                self._set_pending(chat_id, kind="kw_" + sub)
                return View("{}\n\nأرسل الكلمات — كلمة في كل سطر أو افصل بينها بفاصلة.".format("➕ <b>إضافة كلمات</b>" if sub == "add" else "✏️ <b>استبدال كل الكلمات</b>"), self._nav([("❌ إلغاء", "kw")]))
            if sub == "del":
                return self.keyword_delete_list(int(parts[2]))
            if sub == "rm":
                return self.keyword_remove(int(parts[2]), parts[3])
            if sub == "clr":
                return View("⚠️ سيتم حذف <b>كل</b> الكلمات المفتاحية. متأكد؟", self._nav([("✅ نعم، امسح", "kw:clr2"), ("❌ لا", "kw")]))
            if sub == "clr2":
                self._save_words([])
                return self.keywords("تم المسح")
        if head == "cm":
            sub = parts[1] if len(parts) > 1 else ""
            if sub == "":
                return self.comments()
            if sub == "add":
                self._set_pending(chat_id, kind="cm_add")
                return View("➕ <b>إضافة قوالب تعليقات</b>\n\nأرسل تعليقاً في كل سطر (حتى 280 حرفاً).", self._nav([("❌ إلغاء", "cm")]))
            if sub == "del":
                return self.comment_delete_list(int(parts[2]))
            if sub == "rm":
                return self.comment_remove(int(parts[2]), parts[3])
        if head == "wp":
            if len(parts) == 1:
                return self.warp()
            return None  # wp:st / wp:rot تُعالَج بخيط خلفي في TelegramBot
        return self.home()
