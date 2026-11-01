# -*- coding: utf-8 -*-
"""خادم واجهة التطبيق: HTTP محلي (127.0.0.1) + JSON API بدون مكتبات خارجية.

الأمان: ربط على الحلقة المحلية فقط، رمز جلسة عشوائي في كل طلب API،
فحص ترويسة Host (ضد DNS-rebinding)، وإخفاء الأسرار عند إرسال الإعدادات للواجهة.
"""
import copy
import json
import os
import re
import secrets
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import telegram_bot, telegram_menu, warp
from .config import (
    COMMENTS_PATH, DEFAULT_CONFIG, LOGS_DIR, PROFILES_DIR, SESSIONS_DIR, STATE_DIR,
    _atomic_save, _deep_merge, ensure_initial_files, load_accounts, load_config,
    load_seen, load_state, save_accounts, save_config, save_seen, state_path,
)
from .engine import Engine
from .logger import get_logger

log = get_logger("server")

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
MASK = "••••••••"
SECRET_PATHS = (("telegram", "bot_token"), ("comments", "ai", "api_key"))
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
ACC_FIELDS = ("handle", "username", "display_name", "timezone", "enabled", "notes", "en_ratio")


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ---------------------------------------------------------------------------
# التحقق من الإعدادات
# ---------------------------------------------------------------------------
def _num(v, path):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ApiError("قيمة رقمية مطلوبة: " + path)
    return v


def _coerce(default, val, path):
    if isinstance(default, bool):
        if not isinstance(val, bool):
            raise ApiError("قيمة منطقية مطلوبة: " + path)
        return val
    if isinstance(default, (int, float)):
        v = _num(val, path)
        return int(round(v)) if isinstance(default, int) else float(v)
    if isinstance(default, str):
        if not isinstance(val, str):
            raise ApiError("نص مطلوب: " + path)
        return val.strip()
    if default is None:
        if val is None or isinstance(val, str):
            return val or None
        raise ApiError("قيمة غير صالحة: " + path)
    if isinstance(default, dict):
        if not isinstance(val, dict):
            raise ApiError("كائن مطلوب: " + path)
        return {k: _coerce(default[k], val[k], path + "." + k) for k in default if k in val}
    if isinstance(default, list):
        if not isinstance(val, list):
            raise ApiError("قائمة مطلوبة: " + path)
        if len(default) == 2 and all(isinstance(x, (int, float, str)) for x in default):  # مجال (من، إلى)
            if len(val) != 2:
                raise ApiError("مجال من قيمتين مطلوب: " + path)
            out = [_coerce(default[i], val[i], path) for i in range(2)]
            if isinstance(default[0], str):
                for x in out:
                    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", x):
                        raise ApiError("وقت غير صالح (HH:MM): " + path)
            elif out[0] > out[1]:
                raise ApiError("الحد الأدنى أكبر من الأعلى: " + path)
            elif out[0] < 0:
                raise ApiError("قيمة سالبة: " + path)
            return out
        if path.endswith("admin_ids"):
            try:
                return [int(x) for x in val]
            except (TypeError, ValueError):
                raise ApiError("معرّفات الأدمن أرقام فقط")
        return [str(x).strip() for x in val if str(x).strip()]
    return val


def _get_path(d, path):
    for k in path:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d


def _set_path(d, path, value):
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def sanitize_config(incoming, current):
    """يتحقق من الإعدادات الواردة ويدمجها فوق المحفوظة. يرمي ApiError عند خطأ."""
    if not isinstance(incoming, dict):
        raise ApiError("صيغة الإعدادات غير صالحة")
    clean = _coerce(DEFAULT_CONFIG, incoming, "config")
    for sp in SECRET_PATHS:  # القيمة المقنّعة = لا تغيير
        if _get_path(clean, sp) == MASK:
            _set_path(clean, sp, _get_path(current, sp))
    if "manager_handle" in clean:
        h = clean["manager_handle"].lstrip("@")
        if h and not HANDLE_RE.match(h):
            raise ApiError("اسم حساب المدير غير صالح")
        clean["manager_handle"] = h
    chk = (
        (("engagement", "accounts_per_tweet"), 1, 500), (("monitor", "interval_min"), 1, 1440),
        (("warp", "proxy_port"), 1024, 65535), (("warp", "accounts_per_ip"), 1, 1000),
        (("warp", "max_ip_attempts"), 1, 10),
    )
    for path, lo, hi in chk:
        v = _get_path(clean, path)
        for x in (v if isinstance(v, list) else [v] if v != {} else []):
            if not lo <= x <= hi:
                raise ApiError("القيمة خارج النطاق ({}–{}): {}".format(lo, hi, ".".join(path)))
    for path in (("engagement", "action_mix", "like"), ("engagement", "action_mix", "retweet"),
                 ("engagement", "action_mix", "comment"), ("behavior", "rest_day_chance"),
                 ("follow", "visit_only_chance"), ("warmup", "browse_probability"), ("comments", "en_ratio")):
        v = _get_path(clean, path)
        if v != {} and not 0 <= v <= 1:
            raise ApiError("النسبة يجب أن تكون بين 0 و100%: " + ".".join(path))
    return _deep_merge(current, clean)


def masked_config(cfg):
    out = copy.deepcopy(cfg)
    for sp in SECRET_PATHS:
        if _get_path(out, sp):
            _set_path(out, sp, MASK)
    return out


# ---------------------------------------------------------------------------
# مهمة تسجيل الدخول
# ---------------------------------------------------------------------------
class LoginJob:
    def __init__(self, acc_id):
        self.acc_id, self.status, self.error = acc_id, "starting", ""
        self._go = threading.Event()
        self._cancel = False

    def confirm(self):
        self._go.set()

    def cancel(self):
        self._cancel = True
        self._go.set()

    def _wait(self):
        self.status = "waiting_user"
        self._go.wait(900)
        return not self._cancel and self._go.is_set()

    def run(self, account, cfg):
        try:
            from .browser import login_account
            ok = login_account(account, cfg, confirm=self._wait, on_status=lambda s: setattr(self, "status", s))
            self.status = "done" if ok else ("cancelled" if self._cancel else "failed")
            if self.status == "failed":
                self.error = "لم يُرصد تسجيل الدخول - تأكد من الوصول للصفحة الرئيسية ثم أعد المحاولة"
        except Exception as e:
            self.status, self.error = "failed", str(e)[:200]
            log.error("فشل تسجيل الدخول {}: {}".format(self.acc_id, e))

    def active(self):
        return self.status in ("starting", "waiting_user", "saving")

    def info(self):
        return {"account": self.acc_id, "status": self.status, "error": self.error}


# ---------------------------------------------------------------------------
# منطق التطبيق
# ---------------------------------------------------------------------------
class AppService:
    def __init__(self):
        ensure_initial_files()
        self.engine = Engine()
        self.login = None
        self._lock = threading.Lock()
        self.bot = telegram_bot.TelegramBot(
            get_cfg=load_config, get_accounts=load_accounts,
            schedule=self._bot_schedule,
            save_admins=self._save_admins,
            engine_running=lambda: self.engine.status == "running",
            notices=self.completion_notices,
        )
        self.bot.ui = telegram_menu.BotUI(self)
        self.bot.request_check = self.engine.request_check
        self.engine.schedule_cb = lambda tid, text, cfg, instant: self._schedule(tid, text, cfg, "channel", instant)
        self.engine.notify_cb = self.bot.notify_admins

    def start(self):
        self.bot.start()
        if load_config().get("ui", {}).get("autostart_engine"):
            try:
                self._engine_start(False)
            except ApiError as e:
                log.warning("تعذّر التشغيل التلقائي: {}".format(e))

    def shutdown(self):
        self.bot.stop()
        self.engine.stop()

    # ---- مساعدات ----
    def _save_admins(self, ids):
        cfg = load_config()
        cfg["telegram"]["admin_ids"] = ids
        save_config(cfg)

    def _bot_schedule(self, tid, text, cfg, chat_id=None, source="telegram"):
        if source == "channel":
            return self._schedule(tid, text, cfg, "channel", cfg["telegram"]["channel"].get("instant", True))
        return self._schedule(tid, text, cfg, "telegram", cfg["telegram"].get("instant", True), notify_chat=chat_id)

    def _schedule(self, tid, text, cfg, source, priority=False, notify_chat=None):
        from . import monitor  # استيراد متأخر (playwright)
        accounts, skipped = load_accounts(), []
        if priority and tid in load_seen():
            pass  # تغريدة معروفة: التسريع/الإحياء لا يحتاج اختيار حسابات جديدة
        elif priority:
            # العاجلة: فقط الحسابات الجاهزة (جلسة محفوظة ولا تحتاج إعادة دخول) حتى لا تُهدر محاولات فاشلة
            ready = {}
            for aid, a in accounts.items():
                if not a.get("enabled", True):
                    continue
                v = self.account_view(aid, a)
                if v["has_session"] and not v["needs_relogin"]:
                    ready[aid] = a
                else:
                    skipped.append("@" + a.get("handle", aid))
            if not ready:
                raise ApiError("لا توجد حسابات جاهزة (سجّل دخول حساب واحد على الأقل)")
            accounts = ready
            cfg = copy.deepcopy(cfg)
            cfg["engagement"]["action_mix"] = dict(cfg["telegram"]["action_mix"])  # مزيج التفاعل العاجل
            n = len(ready)
            cfg["engagement"]["accounts_per_tweet"] = [min(cfg["engagement"]["accounts_per_tweet"][0], n), min(cfg["engagement"]["accounts_per_tweet"][1], n)]
        entry = monitor.schedule_for_tweet(tid, text, cfg, accounts, source=source, priority=priority, notify_chat=notify_chat, skipped=skipped)
        if entry and priority:
            self.engine.wake()
        return entry

    def _engine_start(self, dry_run):
        try:
            from . import monitor, routine  # noqa: F401
        except Exception as e:
            raise ApiError("مكتبات التشغيل غير جاهزة: {} — ثبّت المتطلبات (pip install -r requirements.txt && python -m playwright install chromium)".format(e), 500)
        return self.engine.start(dry_run)

    # ---- واجهة مبسّطة للبوت (قوائم تيليجرام) ----
    def cfg(self):
        return load_config()

    def patch_config(self, partial):
        return self.set_config(partial)

    def accounts_view(self):
        return [self.account_view(k, v) for k, v in load_accounts().items()]

    def set_account_enabled(self, aid, on):
        return self.update_account(aid, {"enabled": bool(on)})

    def engine_start(self, dry_run=False):
        return self._engine_start(bool(dry_run))

    def engine_stop(self):
        return self.engine.stop()

    def comments_list(self):
        return [l.strip() for l in self.get_comments().splitlines() if l.strip()]

    def set_comments_list(self, items):
        return self.set_comments("\n".join(items))

    def tweet_detail(self, tid):
        e = load_seen().get(tid)
        if not e:
            return None
        by = {"like": [0, 0], "retweet": [0, 0], "comment": [0, 0]}
        failed = done = total = 0
        for t in e.get("queue", []):
            for a in t.get("actions", []):
                total += 1
                by.setdefault(a, [0, 0])[1] += 1
                if a in t.get("done", []):
                    done += 1
                    by[a][0] += 1
            failed += len(t.get("failed", []))
        return {"done": done, "total": total, "failed": failed, "by": by, "accounts": len(e.get("queue", [])),
                "priority": bool(e.get("priority")), "deleted": bool(e.get("deleted")),
                "completed": bool(e.get("completed_at")), "cancelled": bool(e.get("cancelled")), "url": e.get("url", "")}

    def completion_notices(self):
        """إشعارات لمن أرسل الرابط عبر تيليجرام عند اكتمال/حذف التغريدة (تُرسَل مرة واحدة)."""
        path = os.path.join(os.path.dirname(COMMENTS_PATH), "notified.json")
        try:
            with open(path, encoding="utf-8") as f:
                sent = set(json.load(f))
        except (OSError, ValueError):
            sent = set()
        out = []
        for tid, e in load_seen().items():
            chat = e.get("notify_chat")
            if not chat or tid in sent or e.get("cancelled") or not (e.get("completed_at") or e.get("deleted")):
                continue
            d = self.tweet_detail(tid)
            text = ("🗑 التغريدة <code>{}</code> حُذفت — أُلغي التفاعل.".format(tid) if e.get("deleted") else
                    "✅ <b>اكتمل التفاعل</b> مع <code>{}</code>\n{} ({}/{})\n👥 حسابات: {} · فاشلة: {}".format(
                        tid, telegram_menu.bar(d["done"], d["total"]), d["done"], d["total"], d["accounts"], d["failed"]))
            out.append((int(chat), text, [[("🔎 التفاصيل", "tw:r:" + tid), ("🏠 القائمة", "home")]]))
            sent.add(tid)
        if out:
            _atomic_save(sorted(sent), path)
        return out

    # ---- الحالة ----
    def account_view(self, aid, a):
        st = load_state(aid)
        return {
            "id": aid,
            "handle": a.get("handle", ""),
            "username": a.get("username", ""),
            "display_name": a.get("display_name", ""),
            "timezone": a.get("timezone", "Asia/Riyadh"),
            "enabled": a.get("enabled", True),
            "notes": a.get("notes", ""),
            "en_ratio": a.get("en_ratio"),
            "has_session": os.path.exists(os.path.join(SESSIONS_DIR, aid + ".json")),
            "needs_relogin": bool(st.get("needs_relogin")),
            "done": st.get("done", {}),
            "caps": st.get("caps", {}),
            "warmup_done": bool(st.get("warmup_done")),
        }

    def tweets_view(self, limit=100):
        out = []
        for tid, e in load_seen().items():
            q = e.get("queue", [])
            total = sum(len(t.get("actions", [])) for t in q)
            done = sum(len([x for x in t.get("done", []) if x in t.get("actions", [])]) for t in q)
            failed = sum(len(t.get("failed", [])) for t in q)
            out.append({
                "id": tid, "url": e.get("url", ""), "text": (e.get("text") or "")[:140],
                "detected_at": e.get("detected_at", ""), "source": e.get("source", "manager"),
                "accounts": len(q), "total": total, "done": done, "failed": failed,
                "deleted": bool(e.get("deleted")), "completed": bool(e.get("completed_at")),
                "priority": bool(e.get("priority")), "cancelled": bool(e.get("cancelled")),
            })
        out.sort(key=lambda x: x["detected_at"], reverse=True)
        return out[:limit]

    def state(self):
        cfg, accounts = load_config(), load_accounts()
        views = [self.account_view(k, v) for k, v in accounts.items()]
        tweets = self.tweets_view(200)
        pending = sum(max(0, t["total"] - t["done"] - t["failed"]) for t in tweets if not t["deleted"] and not t["completed"] and not t["cancelled"])
        tg = cfg["telegram"]
        return {
            "engine": self.engine.info(),
            "stats": {
                "accounts": len(views),
                "enabled": sum(1 for v in views if v["enabled"]),
                "ready": sum(1 for v in views if v["enabled"] and v["has_session"] and not v["needs_relogin"]),
                "pending": pending,
                "tweets": len(tweets),
            },
            "telegram": {
                "enabled": tg["enabled"], "configured": telegram_bot.valid_token(tg["bot_token"]),
                "state": self.bot.state, "error": self.bot.error, "username": self.bot.username,
                "admins": tg["admin_ids"],
            },
            "manager_handle": cfg.get("manager_handle", ""),
            "login": self.login.info() if self.login else None,
            "recent": tweets[:6],
        }

    # ---- الحسابات ----
    def _clean_account(self, body, partial=False):
        out = {}
        if "handle" in body or not partial:
            h = str(body.get("handle", "")).strip().lstrip("@")
            if not HANDLE_RE.match(h):
                raise ApiError("اسم المستخدم غير صالح (حروف إنجليزية وأرقام و_ حتى 15)")
            out["handle"] = h
        for k in ("username", "display_name", "notes"):
            if k in body:
                out[k] = str(body[k]).strip()[:200]
        if "timezone" in body:
            tz = str(body["timezone"]).strip() or "Asia/Riyadh"
            if not re.match(r"^[A-Za-z_]+(/[A-Za-z_+\-0-9]+){0,2}$", tz):
                raise ApiError("المنطقة الزمنية غير صالحة")
            out["timezone"] = tz
        if "enabled" in body:
            out["enabled"] = bool(body["enabled"])
        if "en_ratio" in body:
            r = body["en_ratio"]
            if r in (None, ""):
                out["en_ratio"] = None
            else:
                if isinstance(r, bool) or not isinstance(r, (int, float)) or not 0 <= r <= 1:
                    raise ApiError("نسبة الإنجليزية بين 0 و100%")
                out["en_ratio"] = float(r)
        return out

    def add_account(self, body):
        data = self._clean_account(body)
        with self._lock:
            accounts = load_accounts()
            if any(a.get("handle", "").lower() == data["handle"].lower() for a in accounts.values()):
                raise ApiError("هذا الحساب مضاف مسبقاً")
            n = 1
            while "acc_{:03d}".format(n) in accounts:
                n += 1
            aid = "acc_{:03d}".format(n)
            acc = {"username": "", "display_name": "", "timezone": "Asia/Riyadh", "enabled": True, "notes": ""}
            acc.update({k: v for k, v in data.items() if v is not None})
            accounts[aid] = acc
            save_accounts(accounts)
        return self.account_view(aid, acc)

    def update_account(self, aid, body):
        data = self._clean_account(body, partial=True)
        with self._lock:
            accounts = load_accounts()
            if aid not in accounts:
                raise ApiError("الحساب غير موجود", 404)
            if "handle" in data and any(k != aid and a.get("handle", "").lower() == data["handle"].lower() for k, a in accounts.items()):
                raise ApiError("هذا الحساب مضاف مسبقاً")
            for k, v in data.items():
                if v is None:
                    accounts[aid].pop(k, None)
                else:
                    accounts[aid][k] = v
            save_accounts(accounts)
        return self.account_view(aid, accounts[aid])

    def delete_account(self, aid):
        if self.login and self.login.active() and self.login.acc_id == aid:
            raise ApiError("أنهِ تسجيل الدخول لهذا الحساب أولاً")
        with self._lock:
            accounts = load_accounts()
            if aid not in accounts:
                raise ApiError("الحساب غير موجود", 404)
            del accounts[aid]
            save_accounts(accounts)
        for p in (state_path(aid), os.path.join(SESSIONS_DIR, aid + ".json")):
            if os.path.exists(p):
                os.remove(p)
        shutil.rmtree(os.path.join(PROFILES_DIR, aid), ignore_errors=True)

    def start_login(self, aid):
        accounts = load_accounts()
        if aid not in accounts:
            raise ApiError("الحساب غير موجود", 404)
        with self._lock:
            if self.login and self.login.active():
                raise ApiError("هناك عملية تسجيل دخول جارية - أكملها أو ألغها أولاً")
            try:
                import playwright  # noqa: F401
            except Exception:
                raise ApiError("مكتبة playwright غير مثبّتة", 500)
            job = LoginJob(aid)
            acc = dict(accounts[aid], id=aid)
            threading.Thread(target=job.run, args=(acc, load_config()), name="login", daemon=True).start()
            self.login = job
        return job.info()

    # ---- التغريدات ----
    def cancel_tweet(self, tid):
        from . import monitor
        if not monitor.cancel_tweet(str(tid)):
            raise ApiError("التغريدة غير موجودة أو ملغاة مسبقاً", 404)

    def cancel_stale(self):
        from . import monitor
        return monitor.cancel_stale(load_config())

    def add_tweet(self, body):
        raw = str(body.get("url", "")).strip()
        ids = telegram_bot.extract_tweet_ids(raw) or ([raw] if re.fullmatch(r"\d{5,25}", raw) else [])
        if not ids:
            raise ApiError("رابط التغريدة غير صالح — مثال: https://x.com/user/status/123456789")
        cfg, accounts = load_config(), load_accounts()
        enabled = [a for a, v in accounts.items() if v.get("enabled", True)]
        if not enabled:
            raise ApiError("لا توجد حسابات مفعّلة")
        if body.get("all"):
            cfg["engagement"]["accounts_per_tweet"] = [len(enabled), len(enabled)]
        text = str(body.get("text", ""))[:2000]
        entry = self._schedule(ids[0], text, cfg, "manual", bool(body.get("fast")))
        if not entry:
            raise ApiError("هذه التغريدة مجدولة مسبقاً (قيد التنفيذ أو اكتملت)")
        return {"id": ids[0], "accounts": len(entry["queue"]), "skipped": entry.get("skipped", []),
                "status": entry.get("_status", "new"), "remaining": entry.get("_remaining")}

    # ---- الإعدادات والتعليقات ----
    def get_config(self):
        return masked_config(load_config())

    def set_config(self, body):
        cfg = sanitize_config(body, load_config())
        save_config(cfg)
        return masked_config(cfg)

    def get_comments(self):
        try:
            with open(COMMENTS_PATH, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def set_comments(self, text):
        lines = [l.strip() for l in str(text).splitlines() if l.strip()]
        if not lines:
            raise ApiError("أضف تعليقاً واحداً على الأقل")
        if len(lines) > 2000 or any(len(l) > 280 for l in lines):
            raise ApiError("حد التعليق 280 حرفاً و2000 تعليق")
        tmp = COMMENTS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, COMMENTS_PATH)
        return len(lines)

    # ---- السجلات ----
    def logs(self, n=300):
        n = max(10, min(int(n), 1000))
        files = sorted(f for f in os.listdir(LOGS_DIR) if f.startswith("app_") and f.endswith(".log")) if os.path.isdir(LOGS_DIR) else []
        if not files:
            return []
        path = os.path.join(LOGS_DIR, files[-1])
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            raw = f.read().decode("utf-8", "replace")
        rx = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| (\w+)\s*\| ([\w.]+) \| (.*)$")
        out = []
        for line in raw.splitlines():
            m = rx.match(line)
            if m:
                out.append({"t": m.group(1), "level": m.group(2), "src": m.group(3).replace("smartengage.", ""), "msg": m.group(4)})
            elif out and line.strip():
                out[-1]["msg"] += "\n" + line
        return out[-n:]

    # ---- تيليجرام ----
    def tg_test(self, token):
        token = (token or "").strip()
        if token in ("", MASK):
            token = load_config()["telegram"]["bot_token"]
        try:
            return self.bot.test_token(token)
        except Exception as e:
            raise ApiError("فشل الاتصال: {}".format(e))

    def tg_remove_admin(self, chat_id):
        cfg = load_config()
        ids = [i for i in cfg["telegram"]["admin_ids"] if int(i) != int(chat_id)]
        cfg["telegram"]["admin_ids"] = ids
        save_config(cfg)

    # ---- الذكاء الاصطناعي ----
    def ai_test(self, body):
        from . import ai_comments
        cfg = load_config()
        ai = dict(cfg["comments"]["ai"])
        for k in ("provider", "model", "api_key"):
            if isinstance(body.get(k), str):
                ai[k] = body[k].strip()
        if ai.get("api_key") in ("", MASK):
            ai["api_key"] = cfg["comments"]["ai"]["api_key"]
        ai["api_key"] = ai_comments.clean_key(ai.get("api_key"))
        text = str(body.get("text") or "").strip() or "$BTC breaks above 70k resistance with strong volume. Next target 75k if it holds this level."
        try:
            out = ai_comments.generate_ai_comment(text, ai, net_cfg=cfg["telegram"], raise_errors=True, ignore_interval=True)
        except ai_comments.AIError as e:
            raise ApiError(str(e))
        return {"comment": out, "tweet": text}

    def tg_resolve_channel(self, name):
        try:
            return self.bot.resolve_channel(name)
        except Exception as e:
            raise ApiError("تعذّر تحديد القناة: {}".format(e))

    def tg_channels(self):
        """القنوات التي رآها البوت مؤخراً (للتعرّف على معرّف القناة من الواجهة)."""
        return [{"id": cid, "title": v["title"], "username": v["username"], "age_s": int(time.time() - v["ts"])}
                for cid, v in sorted(self.bot.channels_seen.items(), key=lambda kv: -kv[1]["ts"]) if cid is not None]

    def ai_diagnose(self, body):
        from . import ai_comments, netdiag
        cfg = load_config()
        ai = dict(cfg["comments"]["ai"])
        for k in ("provider", "model", "api_key"):
            if isinstance(body.get(k), str):
                ai[k] = body[k].strip()
        if ai.get("api_key") in ("", MASK):
            ai["api_key"] = cfg["comments"]["ai"]["api_key"]
        steps = netdiag.run(ai.get("provider", "gemini"), ai.get("api_key", ""), ai.get("model", ""), cfg["telegram"])
        key = ai_comments.clean_key(ai.get("api_key"))
        for s in steps:  # لا يظهر المفتاح في أي ناتج
            s["detail"] = s["detail"].replace(key, "***") if key else s["detail"]
        return {"steps": steps, "verdict": netdiag.verdict(steps)}

    # ---- WARP ----
    def warp_status(self):
        cfg = load_config()["warp"]
        installed = warp.is_installed()
        return {
            "installed": installed,
            "connected": warp.is_connected() if installed else False,
            "ip": warp.get_exit_ip(cfg["proxy_port"]) if installed else None,
        }

    def warp_setup(self):
        cfg = load_config()
        cfg["warp"]["enabled"] = True
        return {"ready": warp.ensure_ready(cfg)}

    def warp_rotate(self):
        s = dict(load_config()["warp"], enabled=True)
        before = warp.get_exit_ip(s["proxy_port"]) if warp.is_installed() else None
        ok = warp.rotate_ip(s)
        return {"ok": ok, "before": before, "after": warp.get_exit_ip(s["proxy_port"]) if ok else None}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def make_handler(svc, token, port):
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    routes = []

    def route(method, pattern):
        def deco(fn):
            routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    @route("GET", "/api/state")
    def _(b, **k): return svc.state()

    @route("GET", "/api/config")
    def _(b, **k): return svc.get_config()

    @route("POST", "/api/config")
    def _(b, **k): return svc.set_config(b)

    @route("GET", "/api/accounts")
    def _(b, **k): return [svc.account_view(a, v) for a, v in load_accounts().items()]

    @route("POST", "/api/accounts")
    def _(b, **k): return svc.add_account(b)

    @route("PATCH", r"/api/accounts/(?P<aid>acc_\d+)")
    def _(b, aid, **k): return svc.update_account(aid, b)

    @route("DELETE", r"/api/accounts/(?P<aid>acc_\d+)")
    def _(b, aid, **k): svc.delete_account(aid); return {}

    @route("POST", r"/api/accounts/(?P<aid>acc_\d+)/login")
    def _(b, aid, **k): return svc.start_login(aid)

    @route("POST", "/api/login/confirm")
    def _(b, **k):
        if not svc.login or not svc.login.active():
            raise ApiError("لا توجد عملية تسجيل دخول جارية")
        svc.login.confirm()
        return svc.login.info()

    @route("POST", "/api/login/cancel")
    def _(b, **k):
        if svc.login and svc.login.active():
            svc.login.cancel()
        return {}

    @route("GET", "/api/tweets")
    def _(b, **k): return svc.tweets_view()

    @route("POST", "/api/tweets")
    def _(b, **k): return svc.add_tweet(b)

    @route("POST", "/api/tweets/cancel")
    def _(b, **k):
        if b.get("stale"):
            return {"cancelled": svc.cancel_stale()}
        svc.cancel_tweet(b.get("id", ""))
        return {"cancelled": 1}

    @route("POST", "/api/engine/start")
    def _(b, **k):
        svc._engine_start(bool(b.get("dry_run")))
        return svc.engine.info()

    @route("POST", "/api/engine/stop")
    def _(b, **k):
        svc.engine.stop()
        return svc.engine.info()

    @route("GET", "/api/comments")
    def _(b, **k): return {"text": svc.get_comments()}

    @route("POST", "/api/comments")
    def _(b, **k): return {"count": svc.set_comments(b.get("text", ""))}

    @route("GET", "/api/logs")
    def _(b, q=None, **k): return svc.logs((q or {}).get("n", 300))

    @route("POST", "/api/telegram/test")
    def _(b, **k): return svc.tg_test(b.get("token", ""))

    @route("GET", "/api/telegram/channels")
    def _(b, **k): return svc.tg_channels()

    @route("POST", "/api/telegram/channel/resolve")
    def _(b, **k): return svc.tg_resolve_channel(b.get("name", ""))

    @route("POST", "/api/telegram/pair")
    def _(b, **k): return {"code": svc.bot.new_pair_code(), "ttl_min": telegram_bot.PAIR_TTL_S // 60}

    @route("DELETE", r"/api/telegram/admins/(?P<cid>-?\d+)")
    def _(b, cid, **k): svc.tg_remove_admin(cid); return {}

    @route("POST", "/api/ai/test")
    def _(b, **k): return svc.ai_test(b)

    @route("POST", "/api/ai/diagnose")
    def _(b, **k): return svc.ai_diagnose(b)

    @route("GET", "/api/warp/status")
    def _(b, **k): return svc.warp_status()

    @route("POST", "/api/warp/setup")
    def _(b, **k): return svc.warp_setup()

    @route("POST", "/api/warp/rotate")
    def _(b, **k): return svc.warp_rotate()

    class Handler(BaseHTTPRequestHandler):
        server_version = "SmartEngage"
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, status, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False))

        def _dispatch(self, method):
            if self.headers.get("Host", "") not in allowed_hosts:
                return self._json(403, {"ok": False, "error": "host"})
            url = urlparse(self.path)
            if method == "GET" and url.path in ("/", "/index.html"):
                try:
                    with open(os.path.join(UI_DIR, "index.html"), encoding="utf-8") as f:
                        html = f.read().replace("__TOKEN__", token)
                except OSError:
                    return self._send(500, "UI missing", "text/plain; charset=utf-8")
                return self._send(200, html, "text/html; charset=utf-8")
            if not url.path.startswith("/api/"):
                return self._json(404, {"ok": False, "error": "غير موجود"})
            if not secrets.compare_digest(self.headers.get("X-Token", ""), token):
                return self._json(403, {"ok": False, "error": "رمز الجلسة غير صالح"})
            body = {}
            if method in ("POST", "PATCH"):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 1_000_000:
                        return self._json(413, {"ok": False, "error": "الطلب كبير جداً"})
                    body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, UnicodeDecodeError):
                    return self._json(400, {"ok": False, "error": "JSON غير صالح"})
            query = {}
            if url.query:
                from urllib.parse import parse_qs
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
            for m, rx, fn in routes:
                if m == method and (mm := rx.match(url.path)):
                    try:
                        data = fn(body, q=query, **mm.groupdict())
                        return self._json(200, {"ok": True, "data": data})
                    except ApiError as e:
                        return self._json(e.status, {"ok": False, "error": str(e)})
                    except Exception as e:
                        log.exception("API %s %s", method, url.path)
                        return self._json(500, {"ok": False, "error": "خطأ داخلي: {}".format(e)})
            return self._json(404, {"ok": False, "error": "غير موجود"})

        def do_GET(self): self._dispatch("GET")
        def do_POST(self): self._dispatch("POST")
        def do_PATCH(self): self._dispatch("PATCH")
        def do_DELETE(self): self._dispatch("DELETE")

    return Handler


def create_server(port=0):
    """ينشئ الخادم ويعيد (httpd, service, token). port=0 = أي منفذ حر."""
    svc = AppService()
    token = secrets.token_urlsafe(24)
    # الاسم ثابت لنفحص Host بعد معرفة المنفذ الفعلي
    httpd = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    real_port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(svc, token, real_port)
    httpd.daemon_threads = True
    return httpd, svc, token
