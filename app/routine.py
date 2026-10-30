# -*- coding: utf-8 -*-
"""Smart daily routine: natural random activity + execute manager engagement."""
import json
import os
import random
import time
from datetime import datetime

from . import browser, monitor, resilience
from . import selectors
from . import actions
from .actions import (
    TWEET_ARTICLE, LIKE_BTN, UNLIKE_BTN, RETWEET_BTN, UNRETWEET_BTN,
    comment_on_tweet, like_tweet, retweet_tweet, dismiss_compose_dialog,
    open_tweet, open_tweet_with_retry, dismiss_all_modals,
    scroll_home, extract_tweet_id,
)
from .selectors import RETWEET_CONFIRM_CANDIDATES, TWEET_TEXT, first as selectors_first
from .comments import build_comment_for
from .config import (
    SESSIONS_DIR, enabled_accounts, load_accounts, load_config,
    load_seen, load_state, save_seen, save_state, seen_lock,
)
from .grow import crypto_activity
from .humanizer import (
    Human, active_window, in_active_hours, is_rest_day, offset_minutes,
    pick_daily_caps, pick_daily_follow_target, plan_day,
    random_sessions, randint, randfloat,
)
from .logger import get_logger

log = get_logger("routine")


def _today(cfg):
    return plan_day(cfg)


def ensure_daily_plan(acc_id, cfg):
    """Create random daily plan for account if not already created for today."""
    state = load_state(acc_id)
    if state.get("date") == _today(cfg):
        return state
    rest_day = is_rest_day(cfg, state)
    caps = pick_daily_caps(cfg, state, rest_day=rest_day)
    caps["follows"] = pick_daily_follow_target(cfg, state, rest_day=rest_day)
    state = {
        "date": _today(cfg),
        "caps": caps,
        "rest_day": rest_day,
        "done": {"likes": 0, "retweets": 0, "comments": 0, "follows": 0},
        "sessions": random_sessions(cfg),
        "sessions_run": 0,
        "warmup_at": _random_warmup_time(cfg),
        "warmup_done": False,
        "needs_relogin": False,
        "last_session_end": None,
        "active_days": int(state.get("active_days", 0) or 0) + 1,
        "followed_handles": state.get("followed_handles", []),
        "seen_users": state.get("seen_users", []),
    }
    save_state(acc_id, state)
    tag = " | Rest day (browsing only)" if rest_day else ""
    log.info(f"{acc_id} | Today's plan: {caps['likes']} likes / {caps['retweets']} retweets / {caps['comments']} comments / {caps['follows']} follows | sessions: {[s['start'] for s in state['sessions']]} | warmup: {state['warmup_at']}{tag}")
    return state


def _random_warmup_time(cfg):
    """Random warmup time inside the active window (leaves at least 30 min before it closes)."""
    start, length = active_window(cfg)
    t = (start + randint(0, max(0, length - 30))) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def _can_do(state, action):
    return state["done"][action] < state["caps"][action]


def _mark_done(state, action):
    state["done"][action] += 1


_CAP_KEYS = {"like": "likes", "retweet": "retweets", "comment": "comments", "follow": "follows"}


def _cap_key(action):
    return _CAP_KEYS.get(action, action)


def _account_en_ratio(acc):
    """English comments ratio for account (None => follows global comments.en_ratio)."""
    if not acc:
        return None
    v = acc.get("en_ratio")
    if v is None:
        return None
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


def _begin_job(acc_id, state):
    """Provisional cooldown: any early return/crash still leaves a pause before the next try."""
    state["last_job_end"] = time.time()
    state["next_session_after"] = time.time() + 600
    save_state(acc_id, state)


def _end_job(acc_id, state, cfg, gap_key="session_gap_min", default=(20, 45)):
    """Human-like random rest before this account starts anything else."""
    lo, hi = cfg.get("behavior", {}).get(gap_key, list(default))
    state["last_job_end"] = time.time()
    state["next_session_after"] = time.time() + randint(int(lo), int(hi)) * 60
    state["last_session_end"] = datetime.now().isoformat(timespec="seconds")
    state["needs_relogin"] = False


def run_session(acc_id, cfg, accounts, dry_run=False):
    """One full session for account: browsing + random interactions + crypto activity + due manager tasks."""
    acc = dict(accounts[acc_id])
    acc["id"] = acc_id
    state = ensure_daily_plan(acc_id, cfg)
    human = Human()

    log.info(f"===== Starting session {acc_id} ({acc.get('handle','')}) =====")
    if dry_run:
        _dry_run_session(acc_id, cfg, state)
        return
    _begin_job(acc_id, state)

    pw, ctx, page, pw_owned = None, None, None, False
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human.page = page

        ok_home, gs = resilience.goto_with_retry(page, "https://x.com/home", cfg)
        if not ok_home:
            log.warning(f"{acc_id} | Failed to open home page ({gs}) - will retry later")
            return
        page.wait_for_timeout(3000)

        # تعمل dismiss تلقائي لأي modal/popup (Views, alerts, إلخ)
        actions.dismiss_all_modals(page, human)

        if browser.is_login_wall(page) or not browser.is_logged_in(page):
            state["needs_relogin"] = True
            save_state(acc_id, state)
            log.warning(f"{acc_id} | Session ended - needs re-login: python main.py login {acc_id}")
            return

        minutes = cfg["behavior"].get("session_minutes", [15, 35])
        session_secs = randint(minutes[0], minutes[1]) * 60
        session_start = time.time()

        # 1) Random browsing and natural interactions
        _browse_and_interact(page, acc_id, cfg, state, human, session_secs, session_start, acc=acc)
        save_state(acc_id, state)  # Saving session progress after browsing (crash protection)

        # 2) Daily crypto activity: search browsing + slow follows for interested
        remaining = max(0, int(session_secs - (time.time() - session_start)))
        crypto_activity(page, acc_id, cfg, state, human, max(120, min(remaining, 900)))
        save_state(acc_id, state)  # Saving crypto progress (likes/follows)

        # 3) Execute due manager tasks for this account
        _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=acc)

        state["sessions_run"] += 1
        _end_job(acc_id, state, cfg)
        save_state(acc_id, state)
        log.info(f"{acc_id} | Session ended | likes {state['done']['likes']} / retweets {state['done']['retweets']} / comments {state['done']['comments']}")
    except Exception as e:
        log.error(f"{acc_id} | Session error: {e}")
        if _is_fatal_session_error(e):
            state["last_session_crash"] = time.time()
            try:
                save_state(acc_id, state)
            except Exception:
                pass
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


def run_warmup(acc_id, cfg, accounts, dry_run=False):
    """Light warmup session: open account and crypto interaction even if manager posted nothing.

    Doesn't count toward daily session plan (doesn't increase sessions_run) to not affect session schedule. Returns dict with execution status.
    """
    acc = dict(accounts[acc_id])
    acc["id"] = acc_id
    state = ensure_daily_plan(acc_id, cfg)
    human = Human()

    log.info(f"===== Warmup {acc_id} ({acc.get('handle','')}) =====")
    if dry_run:
        _dry_run_session(acc_id, cfg, state)
        return {"acc_id": acc_id, "status": "dry-run"}
    _begin_job(acc_id, state)

    pw, ctx, page, pw_owned = None, None, None, False
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human.page = page

        ok_home, gs = resilience.goto_with_retry(page, "https://x.com/home", cfg)
        if not ok_home:
            log.warning(f"{acc_id} | Failed to open home page during warmup ({gs})")
            return {"acc_id": acc_id, "status": "network-error"}
        page.wait_for_timeout(3000)

        if browser.is_login_wall(page) or not browser.is_logged_in(page):
            state["needs_relogin"] = True
            save_state(acc_id, state)
            log.warning(f"{acc_id} | Warmup stopped - needs re-login: python main.py login {acc_id}")
            return {"acc_id": acc_id, "status": "relogin"}

        minutes = cfg.get("warmup", {}).get("session_minutes", [8, 15])
        budget_s = randint(minutes[0], minutes[1]) * 60

        # Light home browsing then crypto activity (search + slow follows)
        scroll_home(page, human, steps=randint(1, 2))
        human.mouse_wander(page)
        crypto_activity(page, acc_id, cfg, state, human, budget_s)

        _end_job(acc_id, state, cfg, default=(15, 30))
        state["warmup_done"] = True
        save_state(acc_id, state)
        log.info(f"{acc_id} | Warmup ended | likes {state['done']['likes']} / follows {state['done']['follows']}")
        return {"acc_id": acc_id, "status": "ok"}
    except Exception as e:
        log.error(f"{acc_id} | Warmup error: {e}")
        return {"acc_id": acc_id, "status": "error", "error": str(e)[:200]}
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


def warmup_due(state, cfg=None):
    """Is it warmup time for this account today? (local time, active window)"""
    if not state or not state.get("warmup_at"):
        return False
    if state.get("warmup_done") or state.get("needs_relogin"):
        return False
    cfg = cfg or load_config()
    try:
        return offset_minutes(cfg, state["warmup_at"]) <= offset_minutes(cfg)
    except Exception:
        return False


# FIX-MIX-V1-STATE
STATE_KEY = {"like": "likes", "retweet": "retweets", "comment": "comments"}


def _pick_browse_action(cfg, state):
    """Pick one random action per turn (like/retweet/comment) by weights."""
    mix = cfg.get("engagement", {}).get("action_mix", {})
    w = {
        "like": float(mix.get("like", 0.60)),
        "retweet": float(mix.get("retweet", 0.18)),
        "comment": float(mix.get("comment", 0.22)),
    }
    avail = [k for k in ("like", "retweet", "comment")
             if w[k] > 0 and _can_do(state, STATE_KEY[k])]
    if not avail:
        return None
    total = sum(w[k] for k in avail)
    r = random.random() * total
    acc = 0.0
    for k in avail:
        acc += w[k]
        if r <= acc:
            return k
    return avail[-1]


def _like_one_random(page, articles, acc_id, state, human, liked_ids=None):
    """Like one random tweet within daily limit."""
    for art in random.sample(articles, min(len(articles), 5)):
        try:
            tid = extract_tweet_id(art)
            if tid and liked_ids is not None and tid in liked_ids:
                continue
            if art.locator(UNLIKE_BTN).first.count() > 0:
                if tid and liked_ids is not None:
                    liked_ids.add(tid)
                continue
            like_btn = art.locator(LIKE_BTN).first
            if like_btn.count() == 0 or not like_btn.is_visible():
                continue
            like_btn.hover()
            human.pause(0.7, 2.0)
            like_btn.click()
            page.wait_for_timeout(1200)
            if tid and liked_ids is not None:
                liked_ids.add(tid)
            _mark_done(state, "likes")
            log.info(f"{acc_id} | Random like #{state['done']['likes']}")
            return True
        except Exception:
            continue
    return False


def _retweet_one_random(page, articles, acc_id, state, human, retweeted_ids=None):
    """Retweet one random tweet within daily limit."""
    for art in random.sample(articles, min(len(articles), 5)):
        try:
            tid = extract_tweet_id(art)
            if tid and retweeted_ids is not None and tid in retweeted_ids:
                continue
            if art.locator(UNRETWEET_BTN).first.count() > 0:
                if tid and retweeted_ids is not None:
                    retweeted_ids.add(tid)
                continue
            rt = art.locator(RETWEET_BTN).first
            if rt.count() == 0 or not rt.is_visible():
                continue
            rt.hover()
            human.pause(0.8, 2.2)
            rt.click()
            page.wait_for_timeout(1200)
            confirm = selectors_first(page, RETWEET_CONFIRM_CANDIDATES)
            if confirm is not None:
                confirm.click()
                page.wait_for_timeout(1500)
                if "/compose/" in (page.url or ""):
                    dismiss_compose_dialog(page, human)
                    log.warning(f"{acc_id} | Quote editor opened by mistake - closed without saving")
                    return False
                if tid and retweeted_ids is not None:
                    retweeted_ids.add(tid)
                _mark_done(state, "retweets")
                log.info(f"{acc_id} | Random retweet #{state['done']['retweets']}")
                return True
            page.keyboard.press("Escape")
        except Exception:
            continue
    return False


def _browse_and_interact(page, acc_id, cfg, state, human, session_secs, session_start, acc=None):
    """Browse home with random mix: alternating likes/retweets/comments like a human."""
    en_ratio = _account_en_ratio(acc)
    last_action = None
    scrolls = 0
    liked_ids = set()
    retweeted_ids = set()
    dismiss_compose_dialog(page, human)  # Clean up any editor/dialog from previous round
    while time.time() - session_start < session_secs:
        save_state(acc_id, state)  # Save progress on each turn
        # Early exit if engagement quota complete
        if (not _can_do(state, "likes") and not _can_do(state, "retweets")
                and not _can_do(state, "comments")):
            break
        # If quote editor still open: close it and skip turn
        if "/compose/" in (page.url or ""):
            dismiss_compose_dialog(page, human)
            if "/compose/" in (page.url or ""):
                log.warning(f"{acc_id} | Quote editor stuck - skipping turn")
                human.pause(4, 8)
                continue
        # Scroll and pause
        human.pause(2.0, 6.0)
        page.mouse.wheel(0, random.randint(500, 1500))
        scrolls += 1
        # Fix: give browser time to fetch new tweets during random scrolling
        page.wait_for_timeout(1000)

        if scrolls % 3 == 0:
            human.pause(4.0, 12.0)  # pause for "reading"

        articles = page.locator(TWEET_ARTICLE).all()
        if not articles:
            human.pause(3, 6)
            continue

        action = _pick_browse_action(cfg, state)
        if action is None:
            break
        # كسر التكرار: إن تكرر نفس التفاعل، أعد الاختيار بنسبة 75%
        if action == last_action and random.random() < 0.75:
            other = [a for a in ("like", "retweet", "comment")
                     if a != last_action and _can_do(state, STATE_KEY[a])]
            if other:
                action = random.choice(other)
        last_action = action

        if action == "like":
            _like_one_random(page, articles, acc_id, state, human, liked_ids)
        elif action == "retweet":
            _retweet_one_random(page, articles, acc_id, state, human, retweeted_ids)
        else:  # comment
            try:
                art = random.choice(articles)
                link = art.locator('a[href*="/status/"]').first
                if link.count() > 0:
                    href = link.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = "https://x.com" + href
                    if _open_and_comment(page, href, acc_id, state, human, cfg=cfg, en_ratio=en_ratio):
                        log.info(f"{acc_id} | Random comment #{state['done']['comments']}")
                # Return to home after comment (success or failure)
                try:
                    # Fix: add wait_until to prevent session crash on slow connection
                    page.goto("https://x.com/home", timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            except Exception:
                pass
        save_state(acc_id, state)


# FIX-MIX-V1
def _open_and_comment(page, url, acc_id, state, human, cfg=None, en_ratio=None):
    """Open tweet and comment with smart contextual comment (from tweet text)."""
    ok, status = open_tweet(page, url)
    if not ok:
        return False
    tweet_text = _extract_open_tweet_text(page)
    used = {c for t, e in load_seen().items() for c in e.get("comments_used", [])}
    text = build_comment_for(tweet_text, used_texts=used, cfg=cfg, en_ratio=en_ratio)
    res, msg = comment_on_tweet(page, text, human)
    if res:
        _mark_done(state, "comments")
        _record_comment(url, text)
        return True
    return False


def _extract_open_tweet_text(page):
    """Extract text of currently open tweet (for contextual comment generation)."""
    try:
        el = page.locator(TWEET_TEXT).first
        if el.count() > 0:
            return (el.inner_text() or "").strip()[:500]
    except Exception:
        pass
    return ""


def _action_attempts(tid, acc_id, action):
    """Number of attempts to execute action on manager tweet for specific account."""
    seen = load_seen()
    entry = seen.get(tid, {})
    for task in entry.get("queue", []):
        if task["account"] == acc_id:
            return int(task.get("attempts", {}).get(action, 0))
    return 0


def _handle_action_failure(cfg, tid, acc_id, action, msg):
    """Handle single action failure: count attempts then final skip after limit."""
    monitor.record_action_attempt(tid, acc_id, action)
    max_attempts = int(cfg.get("resilience", {}).get("action_max_attempts", 3))
    attempts = _action_attempts(tid, acc_id, action)
    kind = resilience.error_kind_from_msg(msg)
    if kind in ("network", "timeout") and attempts < max_attempts:
        log.warning(f"{acc_id} | {action} on {tid} failed temporarily ({msg}) - attempt {attempts}/{max_attempts}")
        return
    if attempts >= max_attempts:
        monitor.give_up_action(tid, acc_id, action)
        log.warning(f"{acc_id} | {action} on {tid} final skip after {attempts} attempts")
    else:
        log.warning(f"{acc_id} | {action} on {tid} failed: {msg}")


def _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=None):
    """Execute all due manager tasks (with deletion handling and action failure skip)."""
    if state.get("rest_day"):
        return
    en_ratio = _account_en_ratio(acc)
    seen = load_seen()
    tasks = monitor.due_tasks_for(acc_id, seen)
    if not tasks:
        return
    for task in tasks:
        ok, status = open_tweet_with_retry(page, task["url"], cfg)
        if not ok:
            if status == "deleted":
                monitor.mark_tweet_deleted(task["tweet_id"])
                log.warning(f"{acc_id} | Manager tweet {task['tweet_id']} deleted - removed from schedule")
            elif status == "login-wall":
                log.warning(f"{acc_id} | Login wall when opening {task['tweet_id']} - skipping")
            else:
                monitor.record_tweet_open_attempt(task["tweet_id"])
                log.warning(f"{acc_id} | Failed to open manager tweet {task['tweet_id']}: {status}")
            continue
        # قراءة قصيرة قبل التفاعل
        human.pause(3.0, 8.0)
        page.mouse.wheel(0, random.randint(100, 400))
        human.pause(1.5, 4.0)
        for action in task["remaining"]:
            key = _cap_key(action)
            if not _can_do(state, key):
                continue
            if action == "like":
                res, msg = like_tweet(page, human)
            elif action == "retweet":
                res, msg = retweet_tweet(page, human)
            elif action == "comment":
                used = _comments_used_for(task["tweet_id"])
                text = build_comment_for(task.get("text") or "", used_texts=used, cfg=cfg, en_ratio=en_ratio)
                res, msg = comment_on_tweet(page, text, human)
                if res:
                    _record_comment(task["url"], text, task["tweet_id"])
            else:
                continue
            if res:
                monitor.mark_done(task["tweet_id"], acc_id, action)
                _mark_done(state, key)
                save_state(acc_id, state)  # Save manager task progress after each successful action
                log.info(f"{acc_id} | ✓ {action} on manager tweet {task['tweet_id']}")
            else:
                _handle_action_failure(cfg, task["tweet_id"], acc_id, action, msg)
            human.pause(randint(*cfg["behavior"]["min_action_delay_s"]),
                        randint(*cfg["behavior"]["max_action_delay_s"]))


def _comments_used_for(tid):
    seen = load_seen()
    return seen.get(tid, {}).get("comments_used", [])


def _record_comment(url, text, tid=None):
    with seen_lock:
        seen = load_seen()
        key = None
        for k, v in seen.items():
            if v.get("url") == url or (tid and k == tid):
                key = k
                break
        if key and key in seen:
            seen[key].setdefault("comments_used", []).append(text)
            save_seen(seen)


def _dry_run_session(acc_id, cfg, state):
    """Simulate session without opening browser - show what will happen."""
    caps = state["caps"]
    done = state.get("done", {})
    print(f"[DRY-RUN] {acc_id}:")
    print(f"  - Plan: {caps['likes']} likes | {caps['retweets']} retweets | {caps['comments']} comments | {caps['follows']} crypto follows")
    print(f"  - Completed: {done.get('likes', 0)} likes | {done.get('retweets', 0)} retweets | {done.get('comments', 0)} comments | {done.get('follows', 0)} follows")
    print(f"  - Sessions today: {[s['start'] + ' (' + str(s['minutes']) + 'm)' for s in state['sessions']]}")
    wu = state.get('warmup_at', '-')
    print(f"  - Warmup: {wu} {'(done)' if state.get('warmup_done') else '(pending)'}")
    if state.get('rest_day'):
        print("  - Today: rest day (browsing only without interactions)")
    tasks = monitor.due_tasks_for(acc_id)
    if tasks:
        print(f"  - Due manager tasks: {len(tasks)}")
        for t in tasks:
            print(f"      * Tweet {t['tweet_id']}: {', '.join(t['remaining'])}")
    else:
        print("  - No manager tasks due currently")


_CRASH_COOLDOWN_S = 300  # Safety cooldown after session crash: don't retry during it


def _is_fatal_session_error(msg):
    """Errors meaning browser/process crashed (no point in retrying immediately)."""
    m = str(msg or "").lower()
    return any(s in m for s in (
        "has been closed", "asyncio loop", "sync api inside the asyncio loop",
        "task was destroyed", "connection closed",
    ))


def _tasks_runnable(state, tasks):
    """Manager tasks that can still make progress today (caps not exhausted, not a rest day)."""
    if state.get("rest_day"):
        return False
    return any(_can_do(state, _cap_key(a)) for t in tasks for a in t["remaining"])


def due_jobs(cfg, accounts, busy=()):
    """Work due right now as [(acc_id, kind, reason)], most urgent first.

    kind is "session" or "warmup". Nothing is due outside the active hours
    (configured timezone). Each account rests between jobs, a missed planned
    session is skipped instead of replayed in a burst, and busy accounts are never returned.
    """
    if not in_active_hours(cfg):
        return []
    now = time.time()
    cur = offset_minutes(cfg)
    manager_jobs, planned, warmups = [], [], []
    for acc_id in enabled_accounts(accounts):
        if acc_id in busy:
            continue
        state = ensure_daily_plan(acc_id, cfg)
        if state.get("needs_relogin"):
            continue
        crash_at = float(state.get("last_session_crash") or 0)
        if crash_at and now - crash_at < _CRASH_COOLDOWN_S:
            continue
        since_last = now - float(state.get("last_job_end") or 0)

        tasks = monitor.due_tasks_for(acc_id)
        if tasks and since_last >= 300 and _tasks_runnable(state, tasks):
            manager_jobs.append((acc_id, "session", "manager-tweet tasks"))
            continue
        if now < float(state.get("next_session_after") or 0):
            continue

        sessions = state.get("sessions", [])
        offs = [offset_minutes(cfg, x["start"]) for x in sessions]
        idx = int(state.get("sessions_run", 0))
        moved = False
        while idx + 1 < len(sessions) and offs[idx + 1] <= cur:
            idx += 1  # skip missed sessions, run only the latest passed one
            moved = True
        if moved:
            state["sessions_run"] = idx
            save_state(acc_id, state)
        if idx < len(sessions) and offs[idx] <= cur:
            planned.append((acc_id, "session", "planned session"))
        elif cfg.get("warmup", {}).get("enabled", True) and warmup_due(state, cfg):
            warmups.append((acc_id, "warmup", "daily warmup"))
    random.shuffle(planned)
    random.shuffle(warmups)
    return manager_jobs + planned + warmups


def check_due_sessions(cfg, accounts, dry_run=False):
    """Run every due session one after another (used by `monitor --once`)."""
    results = {"sessions": 0, "tasks": 0}
    for acc_id, kind, _ in due_jobs(cfg, accounts):
        if kind == "session":
            run_session(acc_id, cfg, accounts, dry_run=dry_run)
            results["sessions"] += 1
    return results


def check_due_warmups(cfg, accounts, dry_run=False):
    """Run every due warmup one after another (used by `monitor --once`)."""
    results = {"warmups": 0}
    for acc_id, kind, _ in due_jobs(cfg, accounts):
        if kind == "warmup":
            res = run_warmup(acc_id, cfg, accounts, dry_run=dry_run)
            results["warmups"] += 1
            results.setdefault("details", []).append(res)
    return results
