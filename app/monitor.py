# -*- coding: utf-8 -*-
"""Monitoring manager tweets and scheduling graduated engagement."""
import json
import os
import random
import time
from datetime import datetime

from . import browser, humanizer, resilience
from .actions import scrape_profile_tweets
from .config import load_accounts, load_seen, save_seen
from .logger import get_logger

log = get_logger("monitor")

TWEET_URL = "https://x.com/i/web/status/{tid}"


def fetch_manager_tweets(cfg, accounts, dry_run=False):
    """Fetch latest manager tweets with rotation between accounts (don't use same account)."""
    handle = cfg["manager_handle"].lstrip("@")
    if not handle:
        log.warning("manager_handle is empty - set it in config.json")
        return [], "no-manager"

    if dry_run:
        return [], "dry-run"

    # Fetch enabled accounts
    from .config import enabled_accounts
    acc_list = [aid for aid in enabled_accounts(accounts)]
    if not acc_list:
        log.warning("No enabled accounts - using guest context")
        return _fetch_guest(cfg)

    # Rotation: استخدم حساب مختلف كل مرة
    state_file = os.path.join(os.path.dirname(__file__), "..", "data", "monitor_state.json")
    try:
        with open(state_file, "r") as f:
            monitor_state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        monitor_state = {"last_account_idx": -1}

    last_idx = monitor_state.get("last_account_idx", -1)
    next_idx = (last_idx + 1) % len(acc_list)
    acc_id = acc_list[next_idx]

    # Save the next index
    monitor_state["last_account_idx"] = next_idx
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(monitor_state, f)
    except Exception:
        pass

    log.info(f"Checking manager: using account {acc_id} (rotation {next_idx + 1}/{len(acc_list)})")

    retries = int(cfg.get("resilience", {}).get("monitor_fetch_retries", 2))
    base = float(cfg.get("resilience", {}).get("retry_base_delay_s", 5.0))

    # جرب الحساب الحالي أولاً، بعدها الحسابات الأخرى إذا فشل
    for attempt in range(retries + 1):
        ids, status = _fetch_with_account(cfg, accounts, acc_id)
        if ids or not resilience.is_retryable(resilience.error_kind_from_msg(status)):
            return ids, status

        # If failed: try another account
        if attempt < retries:
            next_idx = (next_idx + 1) % len(acc_list)
            acc_id = acc_list[next_idx]
            d = resilience.backoff_delay(attempt, base)
            log.warning(
                f"Failed to fetch manager tweets ({status}) - trying account {acc_id} after {d:.1f} seconds"
            )
            time.sleep(d)

    return [], status


def _fetch_guest(cfg):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        ctx = browser.launch_guest(cfg, pw)
        page = ctx.new_page()
        human = humanizer.Human(page)
        ids, status = scrape_profile_tweets(
            page, cfg["manager_handle"].lstrip("@"), max_scrolls=3, human=human
        )
        ctx.close()
        if status in ("login-wall", "blocked-empty", "empty-page"):
            log.warning(
                f"Guest session blocked ({status}) - enable monitor.method=account and set use_account in config.json "
                "for logged-in account scanning, or run the tool from a home/office network"
            )
        return ids, status
    except Exception as e:
        log.error(f"Failed to fetch manager tweets (guest): {e}")
        return [], f"error:{e}"[:100]
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def _fetch_with_account(cfg, accounts, acc_id):
    from playwright.sync_api import sync_playwright

    acc = dict(accounts[acc_id])
    acc["id"] = acc_id
    pw = sync_playwright().start()
    try:
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        page.goto("https://x.com/home", timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if not browser.is_logged_in(page):
            log.warning(f"{acc_id} | Monitor account not logged in")
            browser.close_ctx(pw, ctx, False)
            return [], "not-logged-in"
        human = humanizer.Human(page)
        ids, status = scrape_profile_tweets(
            page, cfg["manager_handle"].lstrip("@"), max_scrolls=3, human=human
        )
        browser.close_ctx(pw, ctx, False)
        return ids, status
    except Exception as e:
        log.error(f"Failed to fetch manager tweets (account {acc_id}): {e}")
        return [], f"error:{e}"[:100]
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def _detect_consecutive_tweets(seen):
    """Detect consecutive tweets: return (latest_tweet_id, oldest_tweet_id) if consecutive within 5 minutes."""
    scheduled = [
        (tid, entry["detected_at"])
        for tid, entry in seen.items()
        if entry.get("scheduled")
    ]
    if len(scheduled) < 2:
        return None, None

    # Sort by date (newest first)
    scheduled.sort(key=lambda x: x[1], reverse=True)

    latest_tid, latest_time = scheduled[0]
    prev_tid, prev_time = scheduled[1]

    # Convert ISO timestamps to datetime objects
    from datetime import datetime
    try:
        latest_dt = datetime.fromisoformat(latest_time)
        prev_dt = datetime.fromisoformat(prev_time)
        time_diff = (latest_dt - prev_dt).total_seconds()

        # If difference is less than 300 seconds (5 minutes) → consecutive
        if 0 < time_diff < 300:
            log.info(f"🔥 Detected consecutive tweets: {latest_tid} and {prev_tid} (diff {int(time_diff)}s)")
            return latest_tid, prev_tid
    except Exception:
        pass

    return None, None


def execute_immediate_engagement(tid, text="", cfg=None, accounts=None):
    """Immediate direct engagement on tweet (non-blocking, queue-based).

    Add tweet to queue with high priority.
    Each account engages from its own session (non-blocking).
    """
    seen = load_seen()
    if tid in seen and seen[tid].get("immediate_done"):
        return None

    enabled = [
        aid for aid, a in accounts.items()
        if a.get("enabled", True)
    ]
    if not enabled:
        log.warning("No enabled accounts for immediate engagement")
        return None

    lo, hi = cfg["engagement"]["accounts_per_tweet"]
    lo = min(lo, len(enabled))
    n = random.randint(lo, min(hi, len(enabled)))
    chosen = random.sample(enabled, n)

    log.info(f"⚡ Immediate engagement for tweet {tid}: added to queue ({len(chosen)} accounts)")

    # Save tweet as immediately engaged
    seen[tid] = {
        "tweet_id": tid,
        "url": TWEET_URL.format(tid=tid),
        "text": (text or "")[:2000],
        "detected_at": datetime.now().isoformat(timespec="seconds"),
        "immediate_done": True,
        "mode": "immediate",
        "accounts": chosen,
        "priority": True,  # High priority flag
    }
    save_seen(seen)
    return seen[tid]


def schedule_for_tweet(tid, text="", cfg=None, accounts=None, is_priority=False):
    """Select random account group and distribute tasks with graduated schedule.

    text: Tweet text (saved for contextual comment generation).
    is_priority: If True → first tweet (consecutive) = full engagement (100% like/retweet).
    """
    seen = load_seen()
    if tid in seen and seen[tid].get("scheduled"):
        return None

    enabled = [
        aid for aid, a in accounts.items()
        if a.get("enabled", True)
    ]
    if not enabled:
        log.warning("No enabled accounts for scheduling engagement")
        return None

    lo, hi = cfg["engagement"]["accounts_per_tweet"]
    # Protection: if account count less than minimum, engage with all available
    lo = min(lo, len(enabled))
    n = random.randint(lo, min(hi, len(enabled)))
    chosen = random.sample(enabled, n)
    mix = cfg["engagement"]["action_mix"]
    max_comments = cfg["engagement"].get("max_comments_per_tweet", 8)
    spread = random.uniform(*cfg["engagement"]["spread_hours"]) * 3600

    # Comment ratio fairly constant ~ mix ratio (60%) with light variation 55-65%
    comment_rate = mix.get("comment", 0.6)
    n_comments = round(len(chosen) * random.uniform(comment_rate - 0.05, comment_rate + 0.05))
    n_comments = max(0, min(n_comments, max_comments, len(chosen)))
    commenters = set(random.sample(chosen, n_comments)) if n_comments else set()

    now = time.time()
    queue = []

    # Smart strategy based on whether priority (first tweet of consecutive pair)
    if is_priority:
        # 🔥 First tweet → full engagement (100% like + retweet)
        like_ratio = 1.0  # 100% will like
        retweet_ratio = 1.0  # 100% will retweet
        log.info(f"⚡ Priority tweet {tid}: full engagement (100% like/retweet)")
    else:
        # Normal: high ratios (80-95%) with variation
        like_ratio = random.uniform(0.80, 0.95)  # 80-95% will like
        retweet_ratio = random.uniform(0.80, 0.95)  # 80-95% will retweet

    like_count = int(len(chosen) * like_ratio)
    retweet_count = int(len(chosen) * retweet_ratio)
    like_accounts = set(random.sample(chosen, like_count))
    retweet_accounts = set(random.sample(chosen, retweet_count))

    for idx, aid in enumerate(chosen):
        actions = []
        # Like: based on ratio
        if aid in like_accounts:
            actions.append("like")
        # Retweet: based on ratio
        if aid in retweet_accounts:
            actions.append("retweet")
        # Comment: all accounts (100% - less suspicious)
        actions.append("comment")

        delay = random.uniform(10, 90) if idx == 0 else random.uniform(120, spread)
        due = now + delay
        queue.append({"account": aid, "actions": actions, "due": due, "done": []})

    seen[tid] = {
        "tweet_id": tid,
        "url": TWEET_URL.format(tid=tid),
        "text": (text or "")[:2000],
        "detected_at": datetime.now().isoformat(timespec="seconds"),
        "scheduled": True,
        "is_priority": is_priority,
        "queue": queue,
    }
    save_seen(seen)
    engagement_type = "🔥 Full" if is_priority else "📊 Variable"
    log.info(f"Scheduled engagement for tweet {tid}: {len(queue)} accounts | {engagement_type} | first action after {int((queue[0]['due']-now))} seconds")
    return seen[tid]


def due_tasks_for(acc_id, seen=None):
    """Account tasks due for execution now (skipping deleted and abandoned)."""
    seen = seen or load_seen()
    tasks = []
    for tid, entry in seen.items():
        if not entry.get("scheduled"):
            continue
        if entry.get("deleted"):
            continue
        for task in entry.get("queue", []):
            if task["account"] != acc_id:
                continue
            if task["due"] > time.time():
                continue
            failed = set(task.get("failed", []))
            remaining = [a for a in task["actions"] if a not in task["done"] and a not in failed]
            if remaining:
                tasks.append({
                    "tweet_id": tid,
                    "url": entry["url"],
                    "text": entry.get("text", ""),
                    "task": task,
                    "remaining": remaining,
                })
    return tasks


def mark_done(tid, acc_id, action):
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    for task in entry.get("queue", []):
        if task["account"] == acc_id and action not in task["done"]:
            task["done"].append(action)
    if all(
        all(a in t["done"] or a in t.get("failed", []) for a in t["actions"])
        for t in entry.get("queue", [])
    ):
        entry["completed_at"] = datetime.now().isoformat(timespec="seconds")
    save_seen(seen)


def mark_tweet_deleted(tid):
    """Mark tweet as deleted by manager and remove from schedule permanently."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    entry["deleted"] = True
    entry["deleted_at"] = datetime.now().isoformat(timespec="seconds")
    save_seen(seen)
    log.info(f"Tweet {tid} deleted by manager - removed from task schedule")


def record_tweet_open_attempt(tid):
    """Increment tweet open attempt counter (for troubleshooting)."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    entry["open_attempts"] = entry.get("open_attempts", 0) + 1
    save_seen(seen)


def record_action_attempt(tid, acc_id, action):
    """Record attempt to execute action on manager tweet for specific account."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    for task in entry.get("queue", []):
        if task["account"] == acc_id:
            attempts = task.setdefault("attempts", {})
            attempts[action] = int(attempts.get(action, 0)) + 1
    save_seen(seen)


def give_up_action(tid, acc_id, action):
    """Final abandonment of action after exceeding retry limit (skip and continue)."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    for task in entry.get("queue", []):
        if task["account"] == acc_id:
            if action not in task.setdefault("failed", []):
                task["failed"].append(action)
    save_seen(seen)
    log.info(f"{acc_id} | Final skip of action {action} on tweet {tid}")