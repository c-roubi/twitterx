# -*- coding: utf-8 -*-
"""محرك التشغيل المستمر كخيط خلفي (بديل حلقة `monitor` في الطرفية) مع تحكم بدء/إيقاف من الواجهة."""
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
        return any(t and t.is_alive() for t in (self._thread, self._prio_thread))

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
            self._thread.start()
            self._prio_thread.start()
            log.info("بدء المحرك" + (" (وضع المحاكاة)" if dry_run else ""))
            return True

    def stop(self, wait=0):
        """يطلب الإيقاف؛ تنتهي الجلسة الجارية أولاً حتى لا يُقطع متصفح في منتصف عملية."""
        if not self.running():
            return False
        self._stop.set()
        self._wake.set()
        log.info("طلب إيقاف المحرك - ستنتهي الجلسة الجارية أولاً")
        if wait:
            for t in (self._thread, self._prio_thread):
                if t:
                    t.join(wait)
        return True

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
        while not self._stop.is_set():
            try:
                cfg, accounts = load_config(), load_accounts()
                now = time.time()
                if cfg.get("manager_handle") and now >= self.next_manager_check:
                    tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=self.dry_run)
                    log.info("فحص المدير: {} | جديد: {}".format(status, len(tweets)))
                    seen = load_seen()
                    for t in tweets:
                        tid = t["id"] if isinstance(t, dict) else t
                        if not seen.get(tid, {}).get("scheduled"):
                            monitor.schedule_for_tweet(tid, t.get("text", "") if isinstance(t, dict) else "", cfg, accounts)
                    lo, hi = cfg["monitor"]["interval_min"]
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
