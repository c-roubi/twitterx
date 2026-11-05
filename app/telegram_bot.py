# -*- coding: utf-8 -*-
"""بوت تيليجرام: الأدمن يرسل رابط تغريدة فتتفاعل معها الحسابات تلقائياً.

بدون أي مكتبات خارجية (urllib فقط) - استعلام طويل getUpdates في خيط خلفي.
الأمان: لا يُنفَّذ شيء إلا لمعرّفات (chat id) المدرجة في telegram.admin_ids،
والربط يتم برمز لمرة واحدة يُنشأ من الواجهة (/pair CODE) وينتهي بعد 10 دقائق.
"""
import copy
import hashlib
import html as _html
import json
import os
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import net as _net
from .config import DATA_DIR, _atomic_save
from .logger import get_logger

log = get_logger("telegram")

API = "https://api.telegram.org/bot{token}/{method}"
PAIR_TTL_S = 600
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")

_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.|m\.)?(?:x|twitter|fxtwitter|vxtwitter|fixupx|fixvx)\.com/"
    r"(?:[A-Za-z0-9_]{1,50}|i(?:/web)?)/status(?:es)?/(\d{5,25})(?:[/?#][^\s]*)?",
    re.IGNORECASE,
)


def extract_tweet_ids(text):
    """معرّفات التغريدات (بترتيب الظهور وبدون تكرار) من نص رسالة."""
    out = []
    for m in _URL_RE.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def strip_links(text):
    return re.sub(r"\s+", " ", _URL_RE.sub("", text or "")).strip()


ssl_context = _net.ssl_context  # للتوافق مع الاستخدامات السابقة


OFFSET_PATH = os.path.join(DATA_DIR, "tg_offset.json")
MAX_MSG = 4000   # حد تيليجرام 4096


def _tok_fp(token):
    return hashlib.sha1((token or "").encode("utf-8")).hexdigest()[:10]


def _load_offset(token):
    try:
        with open(OFFSET_PATH, encoding="utf-8") as f:
            v = (json.load(f) or {}).get(_tok_fp(token))
        return int(v) if v else None
    except (OSError, ValueError, TypeError):
        return None


def _save_offset(token, offset):
    """نحفظ آخر تحديث عولج: بعد إعادة تشغيل التطبيق لا تُعاد معالجة أوامر قديمة (مثل تبديل إعداد مرتين)."""
    try:
        try:
            with open(OFFSET_PATH, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
        data[_tok_fp(token)] = int(offset)
        os.makedirs(os.path.dirname(OFFSET_PATH), exist_ok=True)
        _atomic_save(data, OFFSET_PATH)
    except OSError:
        pass


def _plain(text):
    """نسخة نصية بلا وسوم HTML (للإرسال البديل إن رفض تيليجرام التنسيق)."""
    return _html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def valid_token(token):
    return bool(TOKEN_RE.match((token or "").strip()))


class Reply(str):
    """نص رد + أزرار اختيارية (markup = صفوف [(تسمية، callback_data)])."""
    markup = None

    @classmethod
    def of(cls, text, rows=None):
        r = cls(text)
        r.markup = rows
        return r


def _kb(rows):
    if not rows:
        return None
    return json.dumps({"inline_keyboard": [[{"text": l, "callback_data": d} for l, d in row] for row in rows]}, ensure_ascii=False)


COMMANDS = [("menu", "القائمة الرئيسية"), ("status", "حالة التطبيق"), ("summary", "ملخص اليوم"), ("help", "المساعدة"), ("cancel", "إلغاء الإدخال الحالي")]


class TelegramBot:
    """get_cfg(): الإعدادات | schedule(tid, text, cfg, chat_id=None) -> entry|None | save_admins(ids).

    ui: كائن BotUI (قوائم وأزرار) | notices(): قائمة (chat_id, text) لإشعارات اكتمال التفاعل.
    """

    def __init__(self, get_cfg, get_accounts, schedule, save_admins, engine_running=lambda: True, sender=None, ui=None, notices=None,
                 request_check=None):
        self.get_cfg = get_cfg
        self.get_accounts = get_accounts
        self.schedule = schedule
        self.save_admins = save_admins
        self.engine_running = engine_running
        self._send_override = sender
        self.ui = ui
        self.notices = notices
        self.daily_hook = None               # callable() : يستدعي الملخص اليومي في كل دورة (يضعها الخادم)
        self.request_check = request_check   # callable(ts) -> bool : يطلب فحص حساب المدير في X (يضعها الخادم)
        self.channels_seen = {}              # chat_id -> {title, username, ts} للتعرّف على القناة من الواجهة
        self.channel_events = []             # آخر منشورات القنوات التي وصلت البوت ونتيجة التعامل معها (للتشخيص)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._pair = None  # (code, expires_at)
        self.state = "stopped"  # stopped | running | error
        self.error = ""
        self.username = ""
        self.handled = 0

    # ---------- واجهة تيليجرام ----------
    def _api(self, method, params=None, timeout=35, token=None):
        tcfg = self.get_cfg()["telegram"]
        token = token or tcfg["bot_token"].strip()
        data = urllib.parse.urlencode(params or {}).encode()
        req = urllib.request.Request(API.format(token=token, method=method), data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context(tcfg)) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {"ok": False, "description": "HTTP {}".format(e.code)}
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "telegram error"))
        return body["result"]

    def send(self, chat_id, text, markup=None):
        if self._send_override:
            return self._send_override(chat_id, text, markup)
        text = (text or "")[:MAX_MSG]
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        kb = _kb(markup)
        if kb:
            params["reply_markup"] = kb
        return self._deliver("sendMessage", params, lambda r: r.get("message_id"))

    def _deliver(self, method, params, pick=lambda r: r):
        """يرسل/يعدّل مع معالجة الأعطال الشائعة: تنسيق HTML مرفوض ← نص عادي، وازدحام 429 ← انتظار قصير ثم إعادة."""
        for attempt in range(3):
            try:
                return pick(self._api(method, params, timeout=15))
            except Exception as e:
                msg = str(e)
                if "not modified" in msg:
                    return None
                if "parse entities" in msg and params.get("parse_mode"):
                    params = dict(params, text=_plain(params["text"])[:MAX_MSG])
                    params.pop("parse_mode", None)
                    continue
                m = re.search(r"retry after (\d+)", msg)
                if m and int(m.group(1)) <= 10 and attempt < 2:
                    time.sleep(int(m.group(1)) + 0.5)
                    continue
                log.warning("تعذّر {} في تيليجرام: {}".format("الإرسال" if method == "sendMessage" else "التعديل", msg))
                return None
        return None

    def edit(self, chat_id, message_id, text, markup=None):
        params = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        params["text"] = (text or "")[:MAX_MSG]
        kb = _kb(markup)
        params["reply_markup"] = kb if kb else json.dumps({"inline_keyboard": []})
        self._deliver("editMessageText", params)

    def answer(self, cb_id, text=""):
        try:
            self._api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:180]}, timeout=10)
        except Exception as e:
            log.debug("answerCallbackQuery: {}".format(e))

    def resolve_channel(self, name):
        """معرّف قناة عامة من اسمها (@name أو رابط t.me/name) عبر getChat."""
        name = (name or "").strip()
        name = name.replace("https://", "").replace("http://", "")
        if name.startswith("t.me/"):
            name = name[5:]
        name = name.split("?")[0].strip("/").lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name):
            raise RuntimeError("اسم القناة غير صالح (مثال: @mychannel). القنوات الخاصة: حوّل منها منشوراً للبوت")
        chat = self._api("getChat", {"chat_id": "@" + name}, timeout=15)
        if chat.get("type") != "channel":
            raise RuntimeError("هذا ليس اسم قناة")
        return {"id": chat["id"], "title": chat.get("title", ""), "username": chat.get("username", name)}

    def test_token(self, token):
        """يعيد معلومات البوت (username) أو يرمي استثناء برسالة واضحة."""
        if not valid_token(token):
            raise RuntimeError("صيغة التوكن غير صحيحة")
        me = self._api("getMe", timeout=15, token=token.strip())
        return {"username": me.get("username", ""), "name": me.get("first_name", "")}

    # ---------- الربط ----------
    def new_pair_code(self):
        with self._lock:
            code = "{:06d}".format(secrets.randbelow(1000000))
            self._pair = (code, time.time() + PAIR_TTL_S)
            return code

    def _consume_pair(self, code):
        with self._lock:
            if not self._pair:
                return False
            real, exp = self._pair
            if time.time() > exp or not secrets.compare_digest(real, code.strip()):
                return False
            self._pair = None
            return True

    # ---------- معالجة الرسائل (قابلة للاختبار) ----------
    def _is_admin(self, chat_id):
        return chat_id in [int(x) for x in self.get_cfg()["telegram"].get("admin_ids", [])]

    @staticmethod
    def _view(v):
        return Reply.of(v.text, v.rows)

    def handle_message(self, chat_id, text, from_name=""):
        """يعيد قائمة ردود (Reply: نص + أزرار اختيارية)."""
        cfg = self.get_cfg()
        text = (text or "").strip()
        admins = [int(x) for x in cfg["telegram"].get("admin_ids", [])]
        low = text.lower()

        if low.startswith("/pair"):
            code = text[5:].strip()
            if chat_id in admins:
                return [Reply.of("✅ هذا الحساب مربوط مسبقاً.", [[("🏠 القائمة", "home")]])]
            if code and self._consume_pair(code):
                admins.append(int(chat_id))
                self.save_admins(admins)
                log.info("تم ربط أدمن تيليجرام جديد: {} ({})".format(chat_id, from_name))
                out = [Reply("✅ تم الربط بنجاح. أرسل رابط أي تغريدة لتتفاعل معها الحسابات.")]
                if self.ui:
                    out.append(self._view(self.ui.home(greeting=True)))
                return out
            return [Reply("❌ رمز الربط غير صحيح أو منتهي. أنشئ رمزاً جديداً من التطبيق.")]

        if chat_id not in admins:
            return [Reply("🔒 غير مصرّح. معرّف محادثتك: {}\nللربط أنشئ رمزاً من التطبيق (تبويب تيليجرام) ثم أرسل: /pair الرمز".format(chat_id))]

        if self.ui:
            if low.startswith("/cancel"):
                self.ui.cancel(chat_id)
                return [Reply("تم الإلغاء."), self._view(self.ui.home())]
            if self.ui.has_pending(chat_id):
                if text.startswith("/") or extract_tweet_ids(text):
                    self.ui.cancel(chat_id)  # أمر أو رابط تغريدة جديد يلغي الإدخال المعلّق (لا يُحفظ الرابط ككلمة مفتاحية)
                else:
                    v = self.ui.on_text(chat_id, text)
                    if v:
                        return [self._view(v)]
            if low.startswith(("/start", "/menu")):
                return [self._view(self.ui.home(greeting=low.startswith("/start")))]
            if low.startswith("/status"):
                return [self._view(self.ui.status())]
            if low.startswith("/summary"):
                return [self._view(self.ui.summary())]
        if low.startswith(("/start", "/help")):
            return [Reply.of(
                "مرحباً 👋 أنا بوت SmartEngage.\n"
                "• أرسل رابط تغريدة ← تتفاعل معها الحسابات (لايك/ريتويت/تعليق).\n"
                "• /all رابط ← كل الحسابات المفعّلة.\n"
                "• /menu ← القائمة بالأزرار (الإعدادات والكلمات المفتاحية والحسابات والمحرك).\n"
                "• /status ← حالة التطبيق.\n"
                "يمكنك إضافة نص بعد الرابط ليُستخدم في توليد تعليقات مناسبة.", [[("🏠 القائمة", "home")]])]
        if low.startswith("/status"):
            return [Reply(self._status_text())]

        ids = extract_tweet_ids(text)
        if not ids:
            return [Reply.of("لم أجد رابط تغريدة في رسالتك. أرسل رابطاً مثل:\nhttps://x.com/user/status/123456789", [[("🏠 القائمة", "home")]])]

        everyone = low.startswith("/all")
        tweet_text = strip_links(text[4:] if everyone else text)
        replies = []
        for tid in ids[:5]:
            replies.append(self._schedule_one(tid, tweet_text, cfg, everyone, chat_id))
        if not self.engine_running():
            replies.append(Reply.of("⚠️ المحرك متوقف حالياً — شغّله ليبدأ التنفيذ.", [[("▶️ تشغيل المحرك", "eng:start")]]))
        return replies

    def _schedule_one(self, tid, text, cfg, everyone, chat_id=None):
        accounts = self.get_accounts()
        enabled = [a for a, v in accounts.items() if v.get("enabled", True)]
        if not enabled:
            return Reply("❌ لا توجد حسابات مفعّلة.")
        c = copy.deepcopy(cfg)
        if not cfg["telegram"].get("instant", True):
            c["engagement"]["spread_hours"] = list(cfg["telegram"].get("spread_hours") or c["engagement"]["spread_hours"])
        if everyone:
            c["engagement"]["accounts_per_tweet"] = [len(enabled), len(enabled)]
        try:
            entry = self.schedule(tid, text, c, chat_id=chat_id, source="telegram")
        except Exception as e:
            log.error("فشل جدولة {}: {}".format(tid, e))
            return Reply("❌ فشلت الجدولة: {}".format(str(e).replace("<", "&lt;")))
        self.handled += 1
        rows = [[("🔎 التقدّم", "tw:r:" + tid), ("🏠 القائمة", "home")]]
        if not entry:
            return Reply.of("ℹ️ التغريدة <code>{}</code> مجدولة مسبقاً (قيد التنفيذ الفوري أو اكتملت).".format(tid), rows)
        st = entry.get("_status")
        if st in ("upgraded", "revived"):
            what = "♻️ أُعيد تفعيل التغريدة الملغاة" if st == "revived" else "⚡ كانت مجدولة مسبقاً بتوزيع متدرّج"
            return Reply.of("{} <code>{}</code>\n• حُوّلت العمليات المتبقية (<b>{}</b>) إلى تنفيذ فوري\n• ما أُنجز سابقاً لا يُكرَّر".format(what, tid, entry.get("_remaining", "?")), rows)
        q = entry.get("queue", [])
        acts = sum(len(t["actions"]) for t in q)
        mode = "فوري ⚡ بالتتابع (بلا التقيّد بالحدود اليومية)" if cfg["telegram"].get("instant", True) else "متدرّج"
        txt = "✅ <b>جُدول التفاعل</b> مع التغريدة <code>{}</code>\n• الحسابات: <b>{}</b>\n• العمليات: <b>{}</b>\n• التنفيذ: {}".format(tid, len(q), acts, mode)
        if entry.get("skipped"):
            txt += "\n⚠️ تُخطّيت (غير جاهزة): {}".format(" ".join(str(s).replace("<", "&lt;") for s in entry["skipped"]))
        return Reply.of(txt, rows)

    # ---------- تحديد القناة بدون نشر ----------
    @staticmethod
    def forwarded_channel(msg):
        """القناة التي حُوّل منها المنشور (إن وُجدت): يدعم forward_origin الجديد وforward_from_chat القديم."""
        origin = msg.get("forward_origin") or {}
        chat = origin.get("chat") if origin.get("type") == "channel" else None
        chat = chat or msg.get("forward_from_chat")
        if chat and chat.get("type") == "channel":
            return chat
        return None

    def handle_forward(self, chat_id, msg):
        """أدمن حوّل منشوراً من قناة إلى البوت: نعرض المعرّف وزر اعتماده. يرجع Reply أو None."""
        if not self._is_admin(chat_id):
            return None
        ch = self.forwarded_channel(msg)
        if not ch:
            return Reply("ℹ️ لم أستطع معرفة القناة من هذا المنشور (قد تكون القناة تخفي مصدر التحويل). جرّب إدخال اسمها العام في التطبيق.")
        self.channels_seen[ch["id"]] = {"title": ch.get("title", ""), "username": ch.get("username", ""), "ts": time.time()}
        cur = int((self.get_cfg()["telegram"].get("channel") or {}).get("chat_id") or 0)
        txt = "📣 <b>{}</b>\nالمعرّف: <code>{}</code>{}".format(
            str(ch.get("title", "قناة")).replace("<", "&lt;"), ch["id"], "\n✅ هذه هي القناة المعتمدة حالياً." if cur == ch["id"] else "")
        rows = [] if cur == ch["id"] else [[("✅ اعتماد كقناة الأدمن وتفعيل المراقبة", "ch:set:{}".format(ch["id"]))]]
        rows.append([("🏠 القائمة", "home")])
        return Reply.of(txt, rows)

    # ---------- قناة الأدمن ----------
    def notify_admins(self, text, rows=None):
        cfg = self.get_cfg()["telegram"]
        if not cfg.get("notify", True):
            return
        for cid in cfg.get("admin_ids", []):
            self.send(int(cid), text, rows)

    def _chan_event(self, cid, title, result):
        self.channel_events.append({"ts": time.time(), "chat": cid, "title": title or "", "result": result})
        del self.channel_events[:-10]
        log.info("منشور قناة [{}] ({}): {}".format(title or "؟", cid, result))

    def handle_channel_post(self, post):
        """منشور جديد في قناة الأدمن: رابط تغريدة ← جدولة فورية، وإلا ← طلب فحص حساب المدير في X.

        يعيد وصفاً لما حدث (للاختبار). لا يتصرف إلا إذا كانت القناة هي المحددة في الإعدادات.
        """
        chat = post.get("chat") or {}
        cid = chat.get("id")
        self.channels_seen[cid] = {"title": chat.get("title", ""), "username": chat.get("username", ""), "ts": time.time()}
        cfg = self.get_cfg()
        ch = cfg["telegram"].get("channel") or {}
        try:
            wanted = int(ch.get("chat_id") or 0)
        except (TypeError, ValueError):
            wanted = 0
        title = chat.get("title", "")
        if not ch.get("enabled"):
            self._chan_event(cid, title, "تُجوهل: «مراقبة القناة» غير مفعّلة في الإعدادات")
            return "ignored"
        if not wanted:
            self._chan_event(cid, title, "تُجوهل: لم يُحفظ معرّف القناة بعد (اضغط «اعتماد» ثم «حفظ»)")
            return "ignored"
        if wanted != cid:
            self._chan_event(cid, title, "تُجوهل: هذه قناة أخرى (المعتمدة {})".format(wanted))
            return "ignored"
        age_min = (time.time() - float(post.get("date") or time.time())) / 60.0
        limit = max(5.0, float(ch.get("max_age_min", 30) or 30))
        if age_min > limit:   # وصل متأخراً (التطبيق كان مغلقاً): لا ننفّذ تفاعلاً مع منشور قديم
            self._chan_event(cid, title, "تُجوهل: المنشور قديم (قبل {} دقيقة) - وصل متأخراً".format(int(age_min)))
            return "stale"
        text = post.get("text") or post.get("caption") or ""
        ents = (post.get("entities") or []) + (post.get("caption_entities") or [])
        urls = " ".join(e.get("url", "") for e in ents if e.get("type") == "text_link")
        ids = extract_tweet_ids(text + " " + urls)
        if ids:
            body = strip_links(text)
            done = []
            for tid in ids[:5]:
                try:
                    entry = self.schedule(tid, body, copy.deepcopy(cfg), chat_id=None, source="channel")
                except Exception as e:
                    log.error("فشل جدولة منشور القناة {}: {}".format(tid, e))
                    self.notify_admins("❌ فشلت جدولة تغريدة القناة <code>{}</code>: {}".format(tid, str(e).replace("<", "&lt;")))
                    continue
                if entry:
                    done.append(tid)
                    q = entry.get("queue", [])
                    st = entry.get("_status")
                    what = "⚡ حُوّلت المتبقية إلى فوري" if st in ("upgraded", "revived") else "جُدول التفاعل"
                    self.notify_admins("📣 <b>منشور جديد في القناة</b>\n{} مع <code>{}</code> · الحسابات: <b>{}</b>".format(what, tid, len(q)),
                                       [[("🔎 التقدّم", "tw:r:" + tid), ("🏠 القائمة", "home")]])
            self.handled += len(done)
            self._chan_event(cid, title, "جُدول التفاعل مع {} تغريدة".format(len(done)) if done else "فيه رابط لكن لم يُجدول (مكرر أو لا حسابات جاهزة أو فشل)")
            return "scheduled:" + ",".join(done)
        # بلا رابط: نفحص صفحة المدير في X (مع إعادة محاولة لأن التغريدة قد تتأخر)
        ok = bool(self.request_check and self.request_check(time.time()))
        if ok:
            self._chan_event(cid, title, "بلا رابط: طُلب فحص حساب المدير في X")
            self.notify_admins("📣 منشور جديد في القناة بلا رابط — أفحص حساب المدير في X الآن…")
            return "check-requested"
        self._chan_event(cid, title, "بلا رابط لكن المحرك متوقف: لم يُفحص شيء")
        self.notify_admins("⚠️ منشور جديد في القناة لكن المحرك متوقف — شغّله ليُفحص حساب المدير.", [[("▶️ تشغيل المحرك", "eng:start")]])
        return "engine-stopped"

    def _status_text(self):
        accounts = self.get_accounts()
        en = [a for a, v in accounts.items() if v.get("enabled", True)]
        from . import harvest
        return "المحرك: {}\nالحسابات المفعّلة: {} / {}\nالتغريدات المستلمة عبر البوت: {}\nمخزن المرشّحين للمتابعة: {}".format(
            "يعمل ✅" if self.engine_running() else "متوقف ⏸", len(en), len(accounts), self.handled, harvest.pool_size())

    # ---------- الأزرار ----------
    def handle_callback(self, chat_id, message_id, cb_id, data):
        """يعالج ضغطة زر. الأزرار البطيئة (WARP) تُنفَّذ في خيط خلفي وتُحدّث الرسالة عند الانتهاء."""
        if not self.ui or not self._is_admin(chat_id):
            self.answer(cb_id, "غير مصرّح")
            return
        try:
            if data in ("wp:st", "wp:rot"):
                self.answer(cb_id, "جارٍ التنفيذ…")
                v = self.ui.warp(busy=True)
                self.edit(chat_id, message_id, v.text, v.rows)
                threading.Thread(target=self._warp_job, args=(chat_id, message_id, data), daemon=True).start()
                return
            v = self.ui.route(chat_id, data)
        except Exception as e:
            log.exception("خطأ في زر البوت %s", data)
            v = None
            self.answer(cb_id, "حدث خطأ: {}".format(str(e)[:100]))
            return
        self.answer(cb_id, (v.notice or "") if v else "")
        if v:
            self.edit(chat_id, message_id, v.text, v.rows)

    def _warp_job(self, chat_id, message_id, data):
        try:
            if data == "wp:st":
                r = self.ui.svc.warp_status()
                info = ("متصل ✅" if r["connected"] else "غير متصل ⚠️") + " · IP: <code>{}</code>".format(r.get("ip") or "—") if r["installed"] else "⚠️ WARP غير مثبّت"
            else:
                r = self.ui.svc.warp_rotate()
                info = "✅ تم التدوير: <code>{}</code> ← <code>{}</code>".format(r.get("before") or "?", r.get("after") or "?") if r["ok"] else "❌ فشل التدوير — راجع السجلات"
        except Exception as e:
            info = "❌ {}".format(str(e)[:120].replace("<", "&lt;"))
        v = self.ui.warp(info=info)
        self.edit(chat_id, message_id, v.text, v.rows)

    def send_summary(self, html, rows=None):
        """الملخص اليومي يُرسل للأدمن حتى لو أُوقفت إشعارات الاكتمال (له مفتاحه الخاص)."""
        rows = rows or [[("🔄 تحديث ملخص اليوم", "sum"), ("🏠 القائمة", "home")]]
        for cid in self.get_cfg()["telegram"].get("admin_ids", []):
            self.send(int(cid), html, rows)

    def send_notices(self):
        if not self.notices or not self.get_cfg()["telegram"].get("notify", True):
            return
        for chat_id, text, rows in self.notices():
            self.send(chat_id, text, rows)

    # ---------- الحلقة ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="telegram-bot", daemon=True)
        self._thread.start()

    def stop(self, join_timeout=0):
        self._stop.set()
        self.state = "stopped"
        if join_timeout and self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(join_timeout)

    def running(self):
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _process_update(self, upd):
        """معالجة تحديث واحد. أي استثناء هنا لا يوقف الحلقة: نُبلغ صاحب الرسالة ونكمل."""
        post = upd.get("channel_post")
        if post:
            self.handle_channel_post(post)
            return
        cq = upd.get("callback_query")
        if cq and cq.get("message"):
            self.handle_callback(cq["message"]["chat"]["id"], cq["message"]["message_id"], cq["id"], cq.get("data", ""))
            return
        msg = upd.get("message") or {}
        if msg.get("chat", {}).get("type") == "private" and (msg.get("forward_origin") or msg.get("forward_from_chat") or msg.get("forward_date")):
            r = self.handle_forward(msg["chat"]["id"], msg)
            if r:
                self.send(msg["chat"]["id"], str(r), r.markup)
            return
        if msg.get("chat", {}).get("type") != "private" or "text" not in msg:
            return
        who = (msg.get("from") or {}).get("username", "")
        for r in self.handle_message(msg["chat"]["id"], msg["text"], who):
            self.send(msg["chat"]["id"], str(r), r.markup)

    def _loop(self):
        offset = None
        token_used = None
        backoff = 3
        while not self._stop.is_set():
            cfg = self.get_cfg()["telegram"]
            token = cfg["bot_token"].strip()
            if not cfg["enabled"] or not valid_token(token):
                self.state = "stopped"
                self._stop.wait(2)
                continue
            try:
                if token != token_used:
                    me = self._api("getMe", timeout=15)
                    self.username = me.get("username", "")
                    try:  # بوت كان عليه webhook (مشروع آخر): getUpdates يفشل بـ409 حتى يُحذف
                        self._api("deleteWebhook", {"drop_pending_updates": "false"}, timeout=15)
                    except Exception as e:
                        log.debug("deleteWebhook: {}".format(e))
                    token_used, offset = token, _load_offset(token)
                    log.info("بوت تيليجرام يعمل: @{}".format(self.username))
                    try:
                        self._api("setMyCommands", {"commands": json.dumps([{"command": c, "description": d} for c, d in COMMANDS], ensure_ascii=False)}, timeout=15)
                    except Exception as e:
                        log.debug("setMyCommands: {}".format(e))
                self.state, self.error, backoff = "running", "", 3
                params = {"timeout": 10, "allowed_updates": json.dumps(["message", "callback_query", "channel_post"])}
                if offset is not None:
                    params["offset"] = offset
                for upd in self._api("getUpdates", params, timeout=25):
                    offset = upd["update_id"] + 1
                    try:
                        self._process_update(upd)
                    except Exception as e:
                        log.exception("خطأ في معالجة تحديث تيليجرام")
                        cid = ((upd.get("message") or {}).get("chat") or {}).get("id")
                        if cid is not None and self._is_admin(cid):
                            self.send(cid, "⚠️ حدث خطأ داخلي أثناء تنفيذ طلبك: {}".format(str(e)[:150].replace("<", "&lt;")))
                    _save_offset(token, offset)
                self.send_notices()
                if self.daily_hook:
                    try:
                        self.daily_hook()
                    except Exception:
                        log.exception("خطأ في الملخص اليومي")
            except Exception as e:
                err_msg = str(e)
                is_network = any(s in err_msg for s in ("WinError", "urlopen error", "Connection", "timed out", "EOF", "RemoteDisconnected"))
                if is_network:
                    log.debug("خطأ شبكة بوت تيليجرام: {}".format(e))
                else:
                    self.state = "error"
                    self.error = err_msg[:160]
                    log.warning("خطأ بوت تيليجرام: {}".format(e))
                if "Conflict" in err_msg and "webhook" in err_msg.lower():
                    try:
                        self._api("deleteWebhook", {"drop_pending_updates": "false"}, timeout=15)
                    except Exception:
                        pass
                token_used = None if "Unauthorized" in err_msg else token_used
                wait = min(backoff, 10) if is_network else backoff
                self._stop.wait(wait)
                backoff = min(backoff * 2, 60) if not is_network else min(backoff + 1, 10)
