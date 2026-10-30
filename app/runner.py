# -*- coding: utf-8 -*-
"""Continuous runner.

- watcher thread: checks the manager account on its own schedule, so a long
  session can never delay it.
- dispatcher (main thread): starts due sessions/warmups in worker threads,
  at most `monitor.max_parallel` at once, with a random pause between launches.
Config and accounts are re-read every loop, so accounts added while running are picked up.
"""
import random
import threading
import time

from . import monitor, routine
from .config import load_accounts, load_config
from .humanizer import in_active_hours
from .logger import get_logger
from .runtime import account_lock

log = get_logger("runner")


def check_interval_s(cfg):
    """Seconds until the next manager check: monitor.interval_min range plus a little jitter."""
    lo, hi = cfg.get("monitor", {}).get("interval_min", [5, 5])
    return max(60.0, random.uniform(float(lo), float(hi)) * 60 + random.uniform(-30, 30))


class Runner:
    def __init__(self, dry_run=False, on_fatal=None):
        self.dry_run = dry_run
        self.on_fatal = on_fatal
        self.stop = threading.Event()
        self.running = {}
        self._lock = threading.Lock()
        self._last_launch = 0.0
        self._launch_gap = 0.0
        self._idle_logged = False

    # ---- manager watcher -------------------------------------------------
    def _watch_once(self, cfg, accounts):
        tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=self.dry_run)
        res = {"scheduled": 0, "stale": 0, "known": 0}
        if tweets and not self.dry_run:
            res = monitor.process_manager_tweets(tweets, cfg, accounts)
        log.info(f"Manager check: {status} | scraped {len(tweets)} | new {res['scheduled']} "
                 f"(already known {res['known']}, too old {res['stale']})")
        return status

    def watcher(self):
        next_at = 0.0
        while not self.stop.is_set():
            try:
                cfg, accounts = load_config(), load_accounts()
                if time.time() >= next_at:
                    if in_active_hours(cfg):
                        status = self._watch_once(cfg, accounts)
                        # busy accounts: retry soon instead of waiting a full interval
                        wait = 60.0 if status in ("accounts-busy", "account-busy") else check_interval_s(cfg)
                    else:
                        wait = 300.0
                    next_at = time.time() + wait
                    log.info(f"Next manager check in {wait / 60:.1f} minute(s)")
            except Exception as e:
                log.error(f"Manager watcher error: {e}")
                if self.on_fatal:
                    self.on_fatal(e)
                next_at = time.time() + 120
            self.stop.wait(5)

    # ---- sessions --------------------------------------------------------
    def _job(self, acc_id, kind, cfg, accounts):
        lock = account_lock(acc_id)
        if not lock.acquire(blocking=False):
            with self._lock:
                self.running.pop(acc_id, None)
            return
        try:
            if kind == "warmup":
                routine.run_warmup(acc_id, cfg, accounts)
            else:
                routine.run_session(acc_id, cfg, accounts)
        except Exception as e:
            log.error(f"{acc_id} | {kind} crashed: {e}")
            if self.on_fatal:
                self.on_fatal(e)
        finally:
            lock.release()
            with self._lock:
                self.running.pop(acc_id, None)

    def dispatch_once(self, cfg, accounts):
        if not in_active_hours(cfg):
            if not self._idle_logged:
                log.info("Outside active hours - idle until the window opens")
                self._idle_logged = True
            return
        self._idle_logged = False
        max_parallel = int(cfg.get("monitor", {}).get("max_parallel", 2))
        with self._lock:
            busy = set(self.running)
        if len(busy) >= max_parallel or time.time() - self._last_launch < self._launch_gap:
            return
        jobs = routine.due_jobs(cfg, accounts, busy)
        if not jobs:
            return
        acc_id, kind, reason = jobs[0]
        if self.dry_run:
            log.info(f"[dry-run] would start {kind} for {acc_id} ({reason})")
            self._last_launch, self._launch_gap = time.time(), 3600.0
            return
        log.info(f"{acc_id} | Starting {kind} ({reason}) | running now: {len(busy) + 1}/{max_parallel}")
        with self._lock:
            self.running[acc_id] = kind
        self._last_launch = time.time()
        self._launch_gap = random.uniform(60, 240)  # never open browsers back to back
        threading.Thread(target=self._job, args=(acc_id, kind, cfg, accounts),
                         name=f"job-{acc_id}", daemon=True).start()

    def run(self):
        threading.Thread(target=self.watcher, name="manager-watcher", daemon=True).start()
        try:
            while True:
                try:
                    self.dispatch_once(load_config(), load_accounts())
                except Exception as e:
                    log.error(f"Dispatcher error: {e}")
                    if self.on_fatal:
                        self.on_fatal(e)
                time.sleep(20)
        finally:
            self.stop.set()
