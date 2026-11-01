# -*- coding: utf-8 -*-
"""محرك التشغيل المستمر كخيط خلفي (بديل حلقة `monitor` في الطرفية) مع تحكم بدء/إيقاف من الواجهة."""
import copy
import random
import threading
import time

from .config import load_accounts, load_config, load_seen
from .logger import get_logger

log = get_logger("engine")

TICK_S = 60


class Engine:
    def __init__(self):
        self._thread = None
        self._prio_thread = None
        self._check_thread = None
        self._check_ev = threading.Event()   # طلب فحص حساب المدير (من منشور قناة تيليجرام)
        self._check_ts = None
        self._req_lock = threading.Lock()
        self._last_fetch = 0.0
        self.schedule_cb = None   # (tid, text, cfg, instant) -> يجدول عبر الخادم (يوقظ العامل العاجل)
        self.notify_cb = None     # (text) -> إشعار الأدمن
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.dry_run = False
        self.started_at = None
        self.last_cycle = None
        self.last_error = ""
        self.next_manager_check = 0.0
        self.cycles = 0

    # ---------- الحالة ----------
    def running(self):
        return any(t and t.is_alive() for t in (self._thread, self._prio_thread, self._check_thread))

    def wake(self):
        """إيقاظ عامل المهام العاجلة فوراً (عند وصول تغريدة عاجلة)."""
        self._wake.set()

    @property
    def status(self):
        if not self.running():
            return "stopped"
        return "stopping" if self._stop.is_set() else "running"

    def info(self):
        return {
            "status": self.status,
            "dry_run": self.dry_run,
            "started_at": self.started_at,
            "last_cycle": self.last_cycle,
            "last_error": self.last_error,
            "cycles": self.cycles,
        }

    # ---------- تحكم ----------
    def start(self, dry_run=False):
        with self._lock:
            if self.running():
                return False
            self._stop.clear()
            self.dry_run = bool(dry_run)
            self.started_at = time.time()
            self.last_error = ""
            self.next_manager_check = 0.0
            self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
            self._prio_thread = threading.Thread(target=self._run_priority, name="engine-priority", daemon=True)
            self._check_thread = threading.Thread(target=self._run_channel, name="engine-channel", daemon=True)
            self._thread.start()
            self._prio_thread.start()
            self._check_thread.start()
            log.info("بدء المحرك" + (" (وضع المحاكاة)" if dry_run else ""))
            return True

    def stop(self, wait=0):
        """يطلب الإيقاف؛ تنتهي الجلسة الجارية أولاً حتى لا يُقطع متصفح في منتصف عملية."""
        if not self.running():
            return False
        self._stop.set()
        self._wake.set()
        self._check_ev.set()
        log.info("طلب إيقاف المحرك - ستنتهي الجلسة الجارية أولاً")
        if wait:
            for t in (self._thread, self._prio_thread, self._check_thread):
                if t:
                    t.join(wait)
        return True

    # ---------- فحص المدير عند منشور قناة تيليجرام ----------
    def request_check(self, ts=None):
        """يطلب فحص حساب المدير في X (يُدمج مع الطلبات القريبة). يرجع False إن كان المحرك متوقفاً."""
        if self.status != "running":
            return False
        ts = float(ts or time.time())
        with self._req_lock:
            self._check_ts = ts if self._check_ts is None else min(self._check_ts, ts)
        self._check_ev.set()
        return True

    def _notify(self, text):
        if self.notify_cb:
            try:
                self.notify_cb(text)
            except Exception as e:
                log.warning("تعذّر إرسال إشعار: {}".format(e))

    def _run_channel(self):
        try:
            from . import monitor
        except Exception as e:
            log.error("تعذّر تحميل وحدة الفحص: {}".format(e))
            return
        while not self._stop.is_set():
            if not self._check_ev.wait(3):
                continue
            self._check_ev.clear()
            with self._req_lock:
                ts, self._check_ts = (self._check_ts or time.time()), None
            try:
                self._handle_check(monitor, ts)
            except Exception as e:
                log.error("خطأ في فحص المدير بعد منشور القناة: {}".format(e))
                self._notify("❌ تعذّر فحص حساب المدير بعد منشور القناة: {}".format(str(e)[:120].replace("<", "&lt;")))

    def _handle_check(self, monitor, ts):
        """يفحص بعد first_check_s ثم يعيد كل retry_every_s حتى تظهر تغريدة جديدة أو تنتهي المحاولات."""
        ch = load_config()["telegram"]["channel"]
        attempts = 1 + max(0, int(ch.get("retries", 3)))
        first, every = float(ch.get("first_check_s", 30)), float(ch.get("retry_every_s", 60))
        for i in range(attempts):
            if self._stop.wait(max(0.0, ts + first + i * every - time.time())):
                return
            found = self._check_once(monitor)
            if found:
                self._notify("🔎 وُجدت <b>{}</b> تغريدة جديدة للمدير وجُدول التفاعل معها.".format(len(found)))
                return
        self._notify("ℹ️ لم أجد تغريدة جديدة لحساب المدير بعد منشور القناة (جرّبت {} مرات). إن نُشرت لاحقاً فسيلتقطها الفحص الدوري.".format(attempts))

    def _check_once(self, monitor):
        cfg, accounts = load_config(), load_accounts()
        ch = cfg["telegram"]["channel"]
        if not cfg.get("manager_handle"):
            return []
        gap = 30 - (time.time() - self._last_fetch)  # لا نزور X أكثر من مرة كل 30 ثانية
        if gap > 0 and self._stop.wait(gap):
            return []
        acc = cfg["monitor"].get("use_account") if cfg["monitor"].get("method") == "account" else None
        held = False
        if acc and acc in accounts:
            try:
                from . import routine
                for _ in range(30):  # حساب المراقبة قد يكون في جلسة: ننتظر حتى 90 ثانية ثم نستخدم الزائر
                    if routine._acquire(acc):
                        held = True
                        break
                    if self._stop.wait(3):
                        return []
            except Exception:
                pass
            if not held:
                cfg = copy.deepcopy(cfg)
                cfg["monitor"]["method"] = "guest"
        try:
            tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=self.dry_run)
        finally:
            if held:
                routine._release(acc)
        self._last_fetch = time.time()
        max_age_h = float(ch.get("max_age_min", 30)) / 60.0
        seen, found = load_seen(), []
        for t in tweets:
            tid = t["id"] if isinstance(t, dict) else t
            text = t.get("text", "") if isinstance(t, dict) else ""
            if seen.get(tid, {}).get("scheduled"):
                continue
            age = monitor.tweet_age_hours(tid)
            if age is not None and age > max_age_h:
                continue
            if self.schedule_cb:
                entry = self.schedule_cb(tid, text, cfg, bool(ch.get("instant", True)))
            else:
                entry = monitor.schedule_for_tweet(tid, text, cfg, accounts, source="channel")
            if entry:
                found.append(tid)
        log.info("فحص المدير بعد منشور القناة: {} | جديد مجدول: {}".format(status, len(found)))
        return found

    def _run_priority(self):
        """عامل مستقل للمهام العاجلة: يعمل بالتوازي مع الجلسات العادية ولا ينتظرها."""
        try:
            from . import routine
        except Exception as e:
            log.error("تعذّر تحميل وحدة المهام العاجلة: {}".format(e))
            return
        while not self._stop.is_set():
            try:
                routine.check_priority_tasks(load_config(), load_accounts(), dry_run=self.dry_run,
                                             should_stop=self._stop.is_set)
            except Exception as e:
                log.error("خطأ في المهام العاجلة: {}".format(e))
            self._wake.wait(5)
            self._wake.clear()

    # ---------- الحلقة ----------
    def _run(self):
        try:
            from . import monitor, routine  # استيراد متأخر: يتطلب playwright
        except Exception as e:
            self.last_error = "تعذّر تحميل وحدات التشغيل: {}".format(e)
            log.error(self.last_error)
            return
        try:
            monitor.cancel_stale(load_config())  # تنظيف تراكمات قديمة مجدولة سابقاً
        except Exception as e:
            log.warning("تعذّر تنظيف التغريدات القديمة: {}".format(e))
        while not self._stop.is_set():
            try:
                cfg, accounts = load_config(), load_accounts()
                now = time.time()
                if cfg.get("manager_handle") and now >= self.next_manager_check:
                    tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=self.dry_run)
                    log.info("فحص المدير: {} | جديد: {}".format(status, len(tweets)))
                    seen = load_seen()
                    ch = cfg["telegram"]["channel"]
                    channel_mode = bool(ch.get("enabled") and ch.get("chat_id"))
                    for t in tweets:
                        tid = t["id"] if isinstance(t, dict) else t
                        text = t.get("text", "") if isinstance(t, dict) else ""
                        if seen.get(tid, {}).get("scheduled"):
                            continue
                        age = monitor.tweet_age_hours(tid)
                        if channel_mode and self.schedule_cb and age is not None and age <= 2:
                            # وضع القناة: الفحص الاحتياطي يلتقط تغريدة حديثة فاتت القناة → نفّذها فوراً مثل غيرها
                            self.schedule_cb(tid, text, cfg, bool(ch.get("instant", True)))
                        else:
                            monitor.schedule_for_tweet(tid, text, cfg, accounts)
                    # وضع القناة: الفحص الدوري صار «شبكة أمان» أبطأ بكثير (افتراضياً 60-90 دقيقة)
                    lo, hi = ch["fallback_interval_min"] if channel_mode else cfg["monitor"]["interval_min"]
                    self.next_manager_check = now + (lo + (hi - lo) * random.random()) * 60
                if self._stop.is_set():
                    break
                routine.check_due_sessions(cfg, accounts, dry_run=self.dry_run)
                if self._stop.is_set():
                    break
                routine.check_due_warmups(cfg, accounts, dry_run=self.dry_run)
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)[:200]
                log.error("خطأ في دورة المحرك: {}".format(e))
            self.cycles += 1
            self.last_cycle = time.time()
            self._stop.wait(TICK_S)
        log.info("توقف المحرك")
