# -*- coding: utf-8 -*-
"""Scheduler / runner tests: Gaza time window, session spacing, parallel limit, independent watcher.

Run:  python3 tests/test_runner.py
"""
import os
import sys
import tempfile
import threading
import time
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, humanizer, monitor, routine
from app.runner import Runner, check_interval_s

PASS = FAIL = 0
GAZA = pytz.timezone("Asia/Gaza")


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


NOW = {"dt": None}


def set_clock(y, mo, d, h, mi=0):
    NOW["dt"] = GAZA.localize(datetime(y, mo, d, h, mi))


humanizer.get_local_time = lambda cfg: NOW["dt"]
tmp = tempfile.mkdtemp()
config.STATE_DIR = os.path.join(tmp, "state")
config.SEEN_PATH = os.path.join(tmp, "seen.json")
os.makedirs(config.STATE_DIR, exist_ok=True)

CFG = config.load_config()
CFG["behavior"].update({"timezone": "Asia/Gaza", "active_hours": ["08:00", "01:00"],
                        "sessions_per_day": [3, 3], "session_minutes": [5, 10],
                        "session_gap_min": [20, 45]})
CFG["monitor"].update({"interval_min": [5, 5], "max_parallel": 2, "max_tweet_age_hours": 3})
ACCS = {f"acc_{i}": {"handle": f"h{i}", "enabled": True} for i in (1, 2, 3)}


def reset_state(acc_id, **over):
    set_clock(2026, 10, 30, 9, 0)
    st = routine.ensure_daily_plan(acc_id, CFG)
    st.update({"sessions": [{"start": "09:00", "minutes": 5}, {"start": "12:00", "minutes": 5},
                            {"start": "15:00", "minutes": 5}],
               "sessions_run": 0, "warmup_at": "20:00", "warmup_done": False,
               "needs_relogin": False, "next_session_after": 0, "last_job_end": 0})
    st.update(over)
    config.save_state(acc_id, st)
    return st


def main():
    print("1) Gaza window 08:00-01:00:")
    set_clock(2026, 10, 30, 3, 0)
    check("03:00 is outside the window", not humanizer.in_active_hours(CFG))
    set_clock(2026, 10, 30, 0, 30)
    check("00:30 is inside the window", humanizer.in_active_hours(CFG))
    check("00:30 still belongs to the previous plan day", humanizer.plan_day(CFG) == "2026-10-29")
    set_clock(2026, 10, 30, 8, 0)
    check("08:00 opens a new plan day", humanizer.plan_day(CFG) == "2026-10-30")
    check("offset of 01:00 = window end", humanizer.offset_minutes(CFG, "01:00") == 17 * 60)

    print("2) random_sessions spread:")
    ok_sorted = ok_inside = True
    for _ in range(200):
        ss = humanizer.random_sessions(CFG)
        offs = [humanizer.offset_minutes(CFG, x["start"]) for x in ss]
        ok_sorted &= offs == sorted(offs)
        ok_inside &= all(0 <= o <= 17 * 60 for o in offs)
    check("sorted by time of day (midnight-safe)", ok_sorted)
    check("all inside the 17h window", ok_inside)
    ss = humanizer.random_sessions(CFG)
    offs = [humanizer.offset_minutes(CFG, x["start"]) for x in ss]
    check("3 sessions fall in 3 different thirds of the day", [int(o // 340) for o in offs] == [0, 1, 2], offs)

    print("3) due_jobs:")
    for a in ACCS:
        reset_state(a)
    set_clock(2026, 10, 30, 10, 0)
    jobs = routine.due_jobs(CFG, ACCS)
    check("only accounts with a passed planned session are due", {j[0] for j in jobs} == set(ACCS), jobs)
    check("busy accounts are not returned", all(j[0] != "acc_1" for j in routine.due_jobs(CFG, ACCS, busy={"acc_1"})))
    set_clock(2026, 10, 30, 17, 0)
    jobs = routine.due_jobs(CFG, {"acc_1": ACCS["acc_1"]})
    st = config.load_state("acc_1")
    check("missed sessions are skipped, one catch-up only", len(jobs) == 1 and st["sessions_run"] == 2, (jobs, st["sessions_run"]))
    st["sessions_run"] += 1
    routine._end_job("acc_1", st, CFG)
    config.save_state("acc_1", st)
    check("no replay burst after the catch-up session", routine.due_jobs(CFG, {"acc_1": ACCS["acc_1"]}) == [])
    gap_min = (st["next_session_after"] - time.time()) / 60
    check("random rest 20-45 min before the next job", 19 <= gap_min <= 46, gap_min)
    set_clock(2026, 10, 30, 3, 0)
    reset_state("acc_2")
    set_clock(2026, 10, 30, 3, 0)
    check("nothing due outside active hours", routine.due_jobs(CFG, ACCS) == [])
    reset_state("acc_3", needs_relogin=True)
    set_clock(2026, 10, 30, 10, 0)
    check("accounts needing re-login are skipped", all(j[0] != "acc_3" for j in routine.due_jobs(CFG, ACCS)))
    reset_state("acc_2", warmup_at="09:30")
    set_clock(2026, 10, 30, 9, 10)
    st = config.load_state("acc_2"); st["sessions_run"] = 3; config.save_state("acc_2", st)
    check("warmup waits for its time", routine.due_jobs(CFG, {"acc_2": ACCS["acc_2"]}) == [])
    set_clock(2026, 10, 30, 9, 40)
    j = routine.due_jobs(CFG, {"acc_2": ACCS["acc_2"]})
    check("warmup due at its time", j and j[0][1] == "warmup", j)

    print("4) manager tasks:")
    reset_state("acc_1", next_session_after=time.time() + 3000, last_job_end=time.time() - 400)
    config.save_seen({"111": {"scheduled": True, "url": "u", "text": "t", "queue": [
        {"account": "acc_1", "actions": ["like"], "due": time.time() - 5, "done": []}]}})
    set_clock(2026, 10, 30, 10, 0)
    j = routine.due_jobs(CFG, {"acc_1": ACCS["acc_1"]})
    check("due manager task bypasses the long rest (5 min min gap)", j and j[0][2].startswith("manager"), j)
    st = config.load_state("acc_1"); st["last_job_end"] = time.time() - 30; config.save_state("acc_1", st)
    check("but not right after a job", routine.due_jobs(CFG, {"acc_1": ACCS["acc_1"]}) == [])
    st["last_job_end"] = time.time() - 400; st["done"]["likes"] = st["caps"]["likes"]; config.save_state("acc_1", st)
    check("tasks ignored when daily cap is exhausted",
          not any(x[2].startswith("manager") for x in routine.due_jobs(CFG, {"acc_1": ACCS["acc_1"]})))

    print("5) process_manager_tweets:")
    config.save_seen({})
    new_id = str(((int(time.time() * 1000) - 1288834974657) << 22))
    old_id = str((((int(time.time() * 1000) - 10 * 3600_000) - 1288834974657) << 22))
    res = monitor.process_manager_tweets([{"id": new_id, "text": "BTC"}, {"id": old_id, "text": "old"}], CFG, ACCS)
    seen = config.load_seen()
    check("fresh scheduled, old marked stale", res["scheduled"] == 1 and res["stale"] == 1, res)
    check("stale tweet has no queue", "queue" not in seen[old_id])
    spread = sorted(t["due"] - time.time() for t in seen[new_id]["queue"])
    check("engagement times are staggered", len(spread) >= 2 and spread[-1] - spread[0] > 60, spread)
    res = monitor.process_manager_tweets([{"id": new_id, "text": "BTC"}], CFG, ACCS)
    check("same tweet is not scheduled twice", res["scheduled"] == 0 and res["known"] == 1, res)

    print("6) Runner:")
    check("check interval ~5 min with jitter", all(270 <= check_interval_s(CFG) <= 330 for _ in range(100)))
    started, release = [], threading.Event()

    def fake_session(acc_id, cfg, accounts, dry_run=False):
        started.append(acc_id)
        release.wait(10)

    routine.run_session = fake_session
    routine.due_jobs = lambda cfg, accounts, busy=(): [(a, "session", "t") for a in ACCS if a not in busy]
    set_clock(2026, 10, 30, 10, 0)
    r = Runner()
    for _ in range(4):
        r._launch_gap = 0
        r.dispatch_once(CFG, ACCS)
        time.sleep(0.2)
    check("max 2 sessions at once", len(started) == 2 and len(r.running) == 2, (started, r.running))
    release.set(); time.sleep(0.5)
    check("finished jobs free their slot", len(r.running) == 0, r.running)
    release.clear()
    n0 = len(started)
    r._launch_gap = 0
    r.dispatch_once(CFG, ACCS)
    time.sleep(0.2)
    check("launch sets a random 1-4 min gap", 60 <= r._launch_gap <= 240, r._launch_gap)
    r.dispatch_once(CFG, ACCS)
    time.sleep(0.2)
    check("gap blocks a back-to-back second launch", len(started) == n0 + 1, (n0, len(started)))
    release.set(); time.sleep(0.3)

    calls = []
    monitor.fetch_manager_tweets = lambda cfg, accounts, dry_run=False: (calls.append(time.time()) or ([], "ok"))
    release2 = threading.Event()
    r2 = Runner()
    r2.running["acc_1"] = "session"
    t = threading.Thread(target=lambda: release2.wait(10), daemon=True); t.start()
    wt = threading.Thread(target=r2.watcher, daemon=True); wt.start()
    time.sleep(7)
    r2.stop.set()
    check("manager check runs while a session is in progress", len(calls) >= 1, calls)

    print(f"\nResult: {PASS} passed / {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
