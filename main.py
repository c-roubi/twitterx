#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SmartEngage - Smart engagement management for multiple Twitter accounts.

Commands:
  python main.py init                       Create configuration files
  python main.py add --handle user1 ...     Add account
  python main.py list                       List accounts
  python main.py login <acc_id>             Manual login (first time to save session)
  python main.py check [--dry-run]          Check manager tweets once
  python main.py monitor [--once]           Single command: all tasks (monitoring + sessions + warmups + follows)
  python main.py daily <acc_id> [--dry-run] Run daily session for an account
  python main.py warmup <acc_id> [--dry-run] Light warmup session (open + crypto)
  python main.py status                     Account status and tables
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import (
    ACCOUNTS_PATH, CONFIG_PATH, ensure_initial_files, enabled_accounts,
    load_accounts, load_config, load_seen, load_state, save_accounts,
)
from app.logger import get_logger
from app import monitor, routine

log = get_logger("main")


def cmd_init(args):
    ensure_initial_files()
    print("✓ Configuration files created:")
    print(f"   - {CONFIG_PATH} (general settings)")
    print(f"   - {ACCOUNTS_PATH} (accounts)")
    print("   - data/comments.txt (comment templates)")
    print("⚠  Remember to configure manager_handle in config.json")


def cmd_add(args):
    accounts = load_accounts()
    acc_id = f"acc_{len(accounts) + 1:03d}"
    accounts[acc_id] = {
        "username": args.username or "",
        "handle": args.handle.lstrip("@"),
        "display_name": args.display_name or "",
        "timezone": args.timezone or "Asia/Riyadh",
        "enabled": True,
        "notes": args.notes or "",
    }
    if args.en_ratio is not None:
        accounts[acc_id]["en_ratio"] = min(1.0, max(0.0, args.en_ratio))
    save_accounts(accounts)
    print(f"✓ Added {acc_id} (@{accounts[acc_id]['handle']})")
    print(f"  Next step: python main.py login {acc_id}")


def cmd_list(args):
    accounts = load_accounts()
    if not accounts:
        print("No accounts yet. Add your first account:")
        print("  python main.py add --handle <username> --username <display_name>")
        return
    print(f"{'ID':<10}{'Name':<25}{'Status':<10}{'Session':<10}")
    print("-" * 55)
    for aid, a in accounts.items():
        sess = "saved" if os.path.exists(os.path.join("data", "sessions", f"{aid}.json")) else "—"
        state = load_state(aid)
        relog = "⚠ Relogin needed" if state.get("needs_relogin") else "✓"
        print(f"{aid:<10}@{a.get('handle',''):<24}{'enabled' if a.get('enabled', True) else 'disabled':<10}{sess:<10} {relog}")


def cmd_login(args):
    accounts = load_accounts()
    if args.account not in accounts:
        sys.exit(f"Account {args.account} not found - use: python main.py list")
    acc = dict(accounts[args.account])
    acc["id"] = args.account
    from app.browser import login_account
    login_account(acc, load_config())


def cmd_check(args):
    cfg = load_config()
    accounts = load_accounts()
    tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=args.dry_run)
    if args.dry_run:
        print("[DRY-RUN] Manager tweets will be checked without opening browser")
        return
    print(f"Check status: {status} | tweets detected: {len(tweets)}")
    seen = load_seen()
    for t in tweets[:10]:
        tid = t["id"] if isinstance(t, dict) else t
        known = "scheduled ✓" if seen.get(tid, {}).get("scheduled") else "new"
        snippet = (t.get("text", "") if isinstance(t, dict) else "").replace("\n", " ")[:70]
        print(f"  - {tid}  [{known}]" + (f"  | {snippet}" if snippet else ""))
    if tweets and not args.no_schedule:
        # تفاعل فوري مباشر على التغريدات الجديدة (بدون جدولة)
        for t in tweets:
            tid = t["id"] if isinstance(t, dict) else t
            text = t.get("text", "") if isinstance(t, dict) else ""
            if not seen.get(tid, {}).get("immediate_done"):
                monitor.execute_immediate_engagement(tid, text, cfg, accounts)


# FIX-WEDGE-V1: Fatal Playwright error patterns that break the entire monitor process.
# If they occur, there's no fix in the same process - restart automatically (os.execv) with new handler.
_FATAL_DEBUG_SIGNS = (
    "Sync API inside the asyncio loop",
    "Task was destroyed but it is pending",
    "Target page, context or browser has been closed",
    "browser has been closed",
    "Connection closed",
    "asyncio loop",
)


def _is_deadlock_error(msg):
    m = str(msg or "")
    return any(s.lower() in m.lower() for s in _FATAL_DEBUG_SIGNS)


_RESTART_MARKER = os.path.join("data", "logs", ".restarted_" + str(os.getpid()))


def _self_restart(reason):
    """Restart the current process with same arguments (required to exit asyncio deadlock)."""
    try:
        log.error(f"Monitor self-restart due to: {reason}")
        try:
            if os.path.exists(_RESTART_MARKER):
                os.remove(_RESTART_MARKER)
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e2:
        # execv failed (rare) - treat as normal error and continue cycle
        log.error(f"Self-restart failed ({e2}) - continuing current cycle")


def cmd_monitor(args):
    """Single command that runs all tasks: manager monitoring + sessions + warmups + follows + Webhook Server."""
    cfg = load_config()
    accounts = load_accounts()
    if not cfg.get("manager_handle"):
        sys.exit("⚠ manager_handle is empty in config.json - configure it first then restart")
    if not accounts:
        sys.exit("⚠ No accounts - add your accounts first: python main.py add --handle ...")

    # 🚀 شغّل Webhook Server في background (thread منفصل)
    def run_webhook():
        try:
            from flask import Flask, request
            from datetime import datetime
            from threading import Thread as ThreadObj

            app = Flask(__name__)
            webhook_log = []

            @app.route('/webhook/twitter', methods=['POST'])
            def twitter_webhook():
                try:
                    data = request.json
                    if not data:
                        return {'error': 'empty body'}, 400

                    tid = data.get('tweet_id') or data.get('tweet_id_str')
                    text = data.get('text', '')

                    if not tid:
                        return {'error': 'missing tweet_id'}, 400

                    tid = str(tid)
                    text = str(text)[:2000]

                    log.info(f"⚡ WEBHOOK new tweet: {tid}")
                    webhook_log.append({'tid': tid, 'received_at': datetime.now().isoformat()})

                    def engage():
                        try:
                            monitor.execute_immediate_engagement(tid, text, cfg, accounts)
                            log.info(f"✅ Webhook engagement completed")
                        except Exception as e:
                            log.error(f"❌ Engagement error: {e}")

                    thread = ThreadObj(target=engage, daemon=True)
                    thread.start()

                    return {'status': 'received', 'tweet_id': tid}, 202
                except Exception as e:
                    log.error(f"❌ Webhook error: {e}")
                    return {'error': str(e)[:100]}, 500

            @app.route('/health', methods=['GET'])
            def health():
                return {'status': 'running', 'webhooks': len(webhook_log)}, 200

            log.info("🚀 Webhook Server running on http://0.0.0.0:8000")
            app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            log.error(f"❌ Webhook Server error: {e}")

    # Run webhook in background
    webhook_thread = __import__('threading').Thread(target=run_webhook, daemon=True)
    webhook_thread.start()
    log.info("✅ Webhook Server started in background")

    # Wait a moment for it to be ready
    time.sleep(2)

    if args.once:
        print("🚀 Running one complete cycle (all tasks)")
        if args.dry_run:
            print("   DRY-RUN mode: no browser will be opened and no engagement will be executed")
        res = _run_full_pass(cfg, accounts, dry_run=args.dry_run)
        print(f"✓ Cycle executed: {res['sessions']} sessions | {res['warmups']} warmups | {res['scheduled']} tweets scheduled")
        return

    print("🚀 Starting continuous operation (Ctrl+C to stop) - runs all tasks automatically")
    if args.dry_run:
        print("   DRY-RUN mode: no browser will be opened and no engagement will be executed")
    next_check_at = 0
    while True:
        try:
            now = time.time()
            # 1) Manager tweet check (smart exponential backoff)
            if now >= next_check_at:
                tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=args.dry_run)
                had_tweets = len(tweets) > 0
                log.info(f"Manager check: {status} | new: {len(tweets)}")

                if had_tweets:
                    seen = load_seen()
                    # Direct immediate engagement (no scheduling)
                    for t in tweets:
                        tid = t["id"] if isinstance(t, dict) else t
                        if not seen.get(tid, {}).get("immediate_done"):
                            monitor.execute_immediate_engagement(tid, t.get("text", "") if isinstance(t, dict) else "", cfg, accounts)

                # Smart backoff: 1-2-4-8-15-30 minutes if no new tweets
                next_check_at = now + _next_interval(cfg, last_had_tweets=had_tweets)
                interval_min = int(_next_interval(cfg, last_had_tweets=had_tweets) / 60)
                log.info(f"⏱️ Next check in {interval_min} minute(s)")
            # 2) Daily sessions + due manager tasks
            res = routine.check_due_sessions(cfg, accounts, dry_run=args.dry_run)
            if res["sessions"]:
                log.info(f"Sessions executed: {res['sessions']}")
            # 3) Due daily warmups
            wres = routine.check_due_warmups(cfg, accounts, dry_run=args.dry_run)
            if wres["warmups"]:
                log.info(f"Warmups executed: {wres['warmups']}")
        except KeyboardInterrupt:
            print("\n⏹ Stopping")
            break
        except Exception as e:
            log.error(f"Monitor cycle error: {e}")
            if _is_deadlock_error(e):
                _self_restart(str(e)[:200])
        time.sleep(60)


def _run_full_pass(cfg, accounts, dry_run=False):
    """One complete cycle: manager check + schedule new + due sessions + due warmups."""
    res = {"sessions": 0, "warmups": 0, "scheduled": 0}
    tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=dry_run)
    log.info(f"Manager check: {status} | new: {len(tweets)}")
    if tweets:
        seen = load_seen()
        # Direct immediate engagement (no scheduling)
        for t in tweets:
            tid = t["id"] if isinstance(t, dict) else t
            if not seen.get(tid, {}).get("immediate_done"):
                monitor.execute_immediate_engagement(tid, t.get("text", "") if isinstance(t, dict) else "", cfg, accounts)
                res["scheduled"] += 1
    sres = routine.check_due_sessions(cfg, accounts, dry_run=dry_run)
    wres = routine.check_due_warmups(cfg, accounts, dry_run=dry_run)
    res["sessions"] = sres["sessions"]
    res["warmups"] = wres["warmups"]
    return res


def _next_interval(cfg, last_had_tweets=False):
    """Smart exponential backoff: reduce polling when there are no new tweets."""
    state_file = os.path.join(os.path.dirname(__file__), "data", "monitor_state.json")
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"checks_without_tweets": 0}

    if last_had_tweets:
        state["checks_without_tweets"] = 0  # reset when new tweets arrive
    else:
        state["checks_without_tweets"] = state.get("checks_without_tweets", 0) + 1

    # Save state
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(state, f)
    except Exception:
        pass

    # Use config interval as base (60-120 minutes)
    # Without exponential backoff (webhook = primary)
    lo, hi = cfg["monitor"]["interval_min"]

    # If last check had tweets → use base interval
    if state["checks_without_tweets"] == 0:
        return (lo + (hi - lo) * __import__("random").random()) * 60

    # If no tweets → increase interval (but not more than 120 minutes)
    multiplier = min(2, state["checks_without_tweets"] * 0.5)  # max 2x
    base_minutes = (lo + hi) / 2
    final_minutes = min(120, base_minutes * multiplier)

    return final_minutes * 60


def cmd_daily(args):
    cfg = load_config()
    accounts = load_accounts()
    if args.account not in accounts:
        sys.exit(f"Account {args.account} not found")
    routine.run_session(args.account, cfg, accounts, dry_run=args.dry_run)


def cmd_warmup(args):
    """Warmup session: open account and engage with crypto enthusiasts even without manager tweets."""
    cfg = load_config()
    accounts = load_accounts()
    if args.account not in accounts:
        sys.exit(f"Account {args.account} not found")
    routine.run_warmup(args.account, cfg, accounts, dry_run=args.dry_run)


def cmd_status(args):
    cfg = load_config()
    accounts = load_accounts()
    seen = load_seen()
    print(f"Manager: @{cfg.get('manager_handle','')}")
    print(f"Enabled accounts: {len(enabled_accounts(accounts))} / {len(accounts)}")
    pending = 0
    deleted = 0
    failed_actions = 0
    for tid, e in seen.items():
        if e.get("deleted"):
            deleted += 1
            continue
        if e.get("completed_at"):
            continue
        for t in e.get("queue", []):
            failed = set(t.get("failed", []))
            failed_actions += len(failed)
            remaining = [a for a in t["actions"] if a not in t["done"] and a not in failed]
            pending += len(remaining)
    print(f"Pending engagement tasks: {pending}")
    print(f"Deleted tweets (abandoned): {deleted}")
    print(f"Skipped operations (final failure): {failed_actions}")
    print()
    for aid in enabled_accounts(accounts):
        st = load_state(aid)
        d = st.get("done", {})
        c = st.get("caps", {})
        relog = "⚠ Relogin needed" if st.get("needs_relogin") else ""
        followed = len(st.get("followed_handles", []))
        wu = st.get("warmup_at", "-")
        wud = "Done ✓" if st.get("warmup_done") else "Pending"
        print(f"  {aid:<10} Likes {d.get('likes',0)}/{c.get('likes','?')} | Retweets {d.get('retweets',0)}/{c.get('retweets','?')} | Comments {d.get('comments',0)}/{c.get('comments','?')} | Follows {d.get('follows',0)}/{c.get('follows','?')}{' (yesterday: ' + str(followed) + ')' if followed else ''} | Warmup {wu} {wud} {relog}")


def main():
    p = argparse.ArgumentParser(description="SmartEngage - Smart engagement management for Twitter")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", help="Create configuration files")

    a = sub.add_parser("add", help="Add account")
    a.add_argument("--handle", required=True, help="username without @")
    a.add_argument("--username", default="", help="display name")
    a.add_argument("--display-name", default="")
    a.add_argument("--timezone", default="Asia/Riyadh")
    a.add_argument("--notes", default="")
    a.add_argument("--en-ratio", type=float, default=None, help="English comment ratio 0-1 (0=Arabic, 1=English; default follows comments.en_ratio)")

    sub.add_parser("list", help="List accounts")

    a = sub.add_parser("login", help="Manual login (first time)")
    a.add_argument("account")

    a = sub.add_parser("check", help="Check manager tweets once")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--no-schedule", action="store_true")

    a = sub.add_parser("monitor", help="Single command: monitoring + sessions + warmups + follows")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--once", action="store_true", help="Execute one cycle only then exit")

    a = sub.add_parser("daily", help="Run daily session for account")
    a.add_argument("account")
    a.add_argument("--dry-run", action="store_true")

    a = sub.add_parser("warmup", help="Light warmup session (daily crypto engagement)")
    a.add_argument("account")
    a.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="Account status")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    ensure_initial_files()
    handlers = {
        "init": cmd_init, "add": cmd_add, "list": cmd_list, "login": cmd_login,
        "check": cmd_check, "monitor": cmd_monitor, "daily": cmd_daily,
        "warmup": cmd_warmup, "status": cmd_status,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
