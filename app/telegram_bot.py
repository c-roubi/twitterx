# -*- coding: utf-8 -*-
"""بوت تيليجرام: الأدمن يرسل رابط تغريدة فتتفاعل معها الحسابات تلقائياً.

بدون أي مكتبات خارجية (urllib فقط) - استعلام طويل getUpdates في خيط خلفي.
الأمان: لا يُنفَّذ شيء إلا لمعرّفات (chat id) المدرجة في telegram.admin_ids،
والربط يتم برمز لمرة واحدة يُنشأ من الواجهة (/pair CODE) وينتهي بعد 10 دقائق.
"""
import copy
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


def ssl_context(tcfg):
    """سياق TLS: ملف شهادات مخصّص > مخزن النظام (truststore) > الافتراضي. التعطيل خيار أخير صريح."""
    if tcfg.get("insecure_ssl"):
        return ssl._create_unverified_context()
    ca = (tcfg.get("ca_bundle") or os.environ.get("SSL_CERT_FILE") or "").strip()
    if ca and os.path.isfile(ca):
        return ssl.create_default_context(cafile=ca)
    try:
        import truststore  # يستخدم التحقق الأصلي في ويندوز (يجلب شهادات الجذر الناقصة عند الحاجة)
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


def valid_token(token):
    return bool(TOKEN_RE.match((token or "").strip()))


class TelegramBot:
    """get_cfg(): الإعدادات الحالية | schedule(tid, text, cfg) -> entry|None | save_admins(ids)."""

    def __init__(self, get_cfg, get_accounts, schedule, save_admins, engine_running=lambda: True, sender=None):
        self.get_cfg = get_cfg
        self.get_accounts = get_accounts
        self.schedule = schedule
        self.save_admins = save_admins
        self.engine_running = engine_running
        self._send_override = sender
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

    def send(self, chat_id, text):
        if self._send_override:
            return self._send_override(chat_id, text)
        try:
            self._api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}, timeout=15)
        except Exception as e:
            log.warning("تعذّر إرسال رد تيليجرام: {}".format(e))

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
    def handle_message(self, chat_id, text, from_name=""):
        """يعيد قائمة الردود النصية."""
        cfg = self.get_cfg()
        text = (text or "").strip()
        admins = [int(x) for x in cfg["telegram"].get("admin_ids", [])]
        low = text.lower()

        if low.startswith("/pair"):
            code = text[5:].strip()
            if chat_id in admins:
                return ["✅ هذا الحساب مربوط مسبقاً."]
            if code and self._consume_pair(code):
                admins.append(int(chat_id))
                self.save_admins(admins)
                log.info("تم ربط أدمن تيليجرام جديد: {} ({})".format(chat_id, from_name))
                return ["✅ تم الربط بنجاح. أرسل رابط أي تغريدة لتتفاعل معها الحسابات.\nاكتب /help للمساعدة."]
            return ["❌ رمز الربط غير صحيح أو منتهي. أنشئ رمزاً جديداً من التطبيق."]

        if chat_id not in admins:
            return ["🔒 غير مصرّح. معرّف محادثتك: {}\nللربط أنشئ رمزاً من التطبيق (تبويب تيليجرام) ثم أرسل: /pair الرمز".format(chat_id)]

        if low.startswith(("/start", "/help")):
            return [
                "مرحباً 👋 أنا بوت SmartEngage.\n"
                "• أرسل رابط تغريدة ← تتفاعل معها مجموعة من الحسابات (لايك/ريتويت/تعليق).\n"
                "• /all رابط ← كل الحسابات المفعّلة.\n"
                "• /status ← حالة التطبيق.\n"
                "يمكنك إضافة نص بعد الرابط ليُستخدم في توليد تعليقات مناسبة."
            ]
        if low.startswith("/status"):
            return [self._status_text()]

        ids = extract_tweet_ids(text)
        if not ids:
            return ["لم أجد رابط تغريدة في رسالتك. أرسل رابطاً مثل:\nhttps://x.com/user/status/123456789"]

        everyone = low.startswith("/all")
        tweet_text = strip_links(text[4:] if everyone else text)
        replies = []
        for tid in ids[:5]:
            replies.append(self._schedule_one(tid, tweet_text, cfg, everyone))
        if not self.engine_running():
            replies.append("⚠ المحرك متوقف حالياً — شغّله من التطبيق ليبدأ التنفيذ.")
        return replies

    def _schedule_one(self, tid, text, cfg, everyone):
        accounts = self.get_accounts()
        enabled = [a for a, v in accounts.items() if v.get("enabled", True)]
        if not enabled:
            return "❌ لا توجد حسابات مفعّلة."
        c = copy.deepcopy(cfg)
        if not cfg["telegram"].get("instant", True):
            c["engagement"]["spread_hours"] = list(cfg["telegram"].get("spread_hours") or c["engagement"]["spread_hours"])
        if everyone:
            c["engagement"]["accounts_per_tweet"] = [len(enabled), len(enabled)]
        try:
            entry = self.schedule(tid, text, c)
        except Exception as e:
            log.error("فشل جدولة {}: {}".format(tid, e))
            return "❌ فشلت الجدولة: {}".format(e)
        self.handled += 1
        if not entry:
            return "ℹ️ هذه التغريدة مجدولة مسبقاً ({}).".format(tid)
        q = entry.get("queue", [])
        acts = sum(len(t["actions"]) for t in q)
        mode = "فوري ⚡ بالتتابع (بلا التقيّد بالحدود اليومية)" if cfg["telegram"].get("instant", True) else "متدرّج"
        return "✅ جُدول التفاعل مع التغريدة {}\n• الحسابات: {}\n• العمليات: {}\n• التنفيذ: {}".format(tid, len(q), acts, mode)

    def _status_text(self):
        accounts = self.get_accounts()
        en = [a for a, v in accounts.items() if v.get("enabled", True)]
        return "المحرك: {}\nالحسابات المفعّلة: {} / {}\nالتغريدات المستلمة عبر البوت: {}".format(
            "يعمل ✅" if self.engine_running() else "متوقف ⏸", len(en), len(accounts), self.handled)

    # ---------- الحلقة ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="telegram-bot", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.state = "stopped"

    def running(self):
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

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
                    token_used, offset = token, None
                    log.info("بوت تيليجرام يعمل: @{}".format(self.username))
                self.state, self.error, backoff = "running", "", 3
                params = {"timeout": 25, "allowed_updates": json.dumps(["message"])}
                if offset is not None:
                    params["offset"] = offset
                for upd in self._api("getUpdates", params, timeout=35):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    if msg.get("chat", {}).get("type") != "private" or "text" not in msg:
                        continue
                    who = (msg.get("from") or {}).get("username", "")
                    for r in self.handle_message(msg["chat"]["id"], msg["text"], who):
                        self.send(msg["chat"]["id"], r)
            except Exception as e:
                self.state, self.error = "error", str(e)[:160]
                log.warning("خطأ بوت تيليجرام: {}".format(e))
                token_used = None if "Unauthorized" in str(e) else token_used
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
