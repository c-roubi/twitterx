#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SmartEngage - إدارة تفاعل ذكية لعدة حسابات تويتر.

الأوامر:
  python main.py init                       إنشاء ملفات الإعدادات
  python main.py add --handle user1 ...     إضافة حساب
  python main.py list                       عرض الحسابات
  python main.py login <acc_id>             تسجيل دخول يدوي أول مرة لحفظ الجلسة
  python main.py check [--dry-run]          فحص تغريدات المدير مرة واحدة
  python main.py monitor [--once]           أمر واحد يشغّل كل المهام (مراقبة + جلسات + إحماء + متابعات)
  python main.py daily <acc_id> [--dry-run] تشغيل جلسة يومية لحساب معين
  python main.py warmup <acc_id> [--dry-run] جلسة إحماء خفيفة (فتح + كريبتو)
  python main.py status                     حالة الحسابات والجداول
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
    print("✓ تم إنشاء ملفات الإعدادات:")
    print(f"   - {CONFIG_PATH} (الإعدادات العامة)")
    print(f"   - {ACCOUNTS_PATH} (الحسابات)")
    print("   - data/comments.txt (قوالب التعليقات)")
    print("⚠  لا تنسَ ضبط manager_handle في config.json")


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
    print(f"✓ تمت إضافة {acc_id} (@{accounts[acc_id]['handle']})")
    print(f"  الخطوة التالية: python main.py login {acc_id}")


def cmd_list(args):
    accounts = load_accounts()
    if not accounts:
        print("لا حسابات بعد. أضف أول حساب:")
        print("  python main.py add --handle <اسم_المستخدم> --username <الاسم_الظاهر>")
        return
    print(f"{'المعرف':<10}{'الاسم':<25}{'الحالة':<10}{'الجلسة':<10}")
    print("-" * 55)
    for aid, a in accounts.items():
        sess = "محفوظة" if os.path.exists(os.path.join("data", "sessions", f"{aid}.json")) else "—"
        state = load_state(aid)
        relog = "⚠ إعادة دخول" if state.get("needs_relogin") else "✓"
        print(f"{aid:<10}@{a.get('handle',''):<24}{'مفعّل' if a.get('enabled', True) else 'موقوف':<10}{sess:<10} {relog}")


def cmd_login(args):
    accounts = load_accounts()
    if args.account not in accounts:
        sys.exit(f"الحساب {args.account} غير موجود - استخدم: python main.py list")
    acc = dict(accounts[args.account])
    acc["id"] = args.account
    from app.browser import login_account
    login_account(acc, load_config())


def cmd_check(args):
    cfg = load_config()
    accounts = load_accounts()
    tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=args.dry_run)
    if args.dry_run:
        print("[DRY-RUN] سيتم فحص تغريدات المدير بدون فتح متصفح")
        return
    print(f"حالة الفحص: {status} | تغريدات مكتشفة: {len(tweets)}")
    seen = load_seen()
    for t in tweets[:10]:
        tid = t["id"] if isinstance(t, dict) else t
        known = "جدولت ✓" if seen.get(tid, {}).get("scheduled") else "جديدة"
        snippet = (t.get("text", "") if isinstance(t, dict) else "").replace("\n", " ")[:70]
        print(f"  - {tid}  [{known}]" + (f"  | {snippet}" if snippet else ""))
    if tweets and not args.no_schedule:
        # تفاعل فوري مباشر على التغريدات الجديدة (بدون جدولة)
        for t in tweets:
            tid = t["id"] if isinstance(t, dict) else t
            text = t.get("text", "") if isinstance(t, dict) else ""
            if not seen.get(tid, {}).get("immediate_done"):
                monitor.execute_immediate_engagement(tid, text, cfg, accounts)


# FIX-WEDGE-V1: أنماط أخطاء Playwright القاتلة التي تُفسد عملية المونيتور بالكامل.
# إذا ظهرت، لا علاج في نفس العملية - نعيد تشغيلها ذاتياً (os.execv) بمعالِج جديد.
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
    """إعادة تشغيل العملية الحالية بنفس الوسائط (مطلوب للخروج من انغلاق asyncio)."""
    try:
        log.error(f"إعادة تشغيل المونيتور ذاتياً بسبب: {reason}")
        try:
            if os.path.exists(_RESTART_MARKER):
                os.remove(_RESTART_MARKER)
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e2:
        # execv فشل (نادر) - نتعامل معه كخطأ عادي ونكمل الدورة
        log.error(f"فشلت إعادة التشغيل الذاتي ({e2}) - سنكمل الدورة الحالية")


def cmd_monitor(args):
    """الأمر الواحد الذي يشغّل كل المهام: مراقبة المدير + الجلسات + الإحماء + المتابعات + Webhook Server."""
    cfg = load_config()
    accounts = load_accounts()
    if not cfg.get("manager_handle"):
        sys.exit("⚠ manager_handle فارغ في config.json - اضبطه أولاً ثم أعد التشغيل")
    if not accounts:
        sys.exit("⚠ لا حسابات - أضف حساباتك أولاً: python main.py add --handle ...")

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

                    log.info(f"⚡ WEBHOOK تويتة جديدة: {tid}")
                    webhook_log.append({'tid': tid, 'received_at': datetime.now().isoformat()})

                    def engage():
                        try:
                            monitor.execute_immediate_engagement(tid, text, cfg, accounts)
                            log.info(f"✅ تفاعل webhook مكتمل")
                        except Exception as e:
                            log.error(f"❌ خطأ في التفاعل: {e}")

                    thread = ThreadObj(target=engage, daemon=True)
                    thread.start()

                    return {'status': 'received', 'tweet_id': tid}, 202
                except Exception as e:
                    log.error(f"❌ خطأ في webhook: {e}")
                    return {'error': str(e)[:100]}, 500

            @app.route('/health', methods=['GET'])
            def health():
                return {'status': 'running', 'webhooks': len(webhook_log)}, 200

            log.info("🚀 Webhook Server شغّال على http://0.0.0.0:8000")
            app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False, threaded=True)
        except Exception as e:
            log.error(f"❌ خطأ في Webhook Server: {e}")

    # شغّل Webhook في background
    webhook_thread = __import__('threading').Thread(target=run_webhook, daemon=True)
    webhook_thread.start()
    log.info("✅ Webhook Server بدأ في background")

    # استنى شوي عشان يكون جاهز
    time.sleep(2)

    if args.once:
        print("🚀 تنفيذ دورة واحدة كاملة (كل المهام)")
        if args.dry_run:
            print("   وضع المحاكاة DRY-RUN: لن يفتح أي متصفح ولن ينفذ أي تفاعل")
        res = _run_full_pass(cfg, accounts, dry_run=args.dry_run)
        print(f"✓ تم تنفيذ الدورة: {res['sessions']} جلسة | {res['warmups']} إحماء | {res['scheduled']} تغريدة مجدولة")
        return

    print("🚀 بدء التشغيل المستمر (Ctrl+C للإيقاف) - يشغّل كل المهام تلقائياً")
    if args.dry_run:
        print("   وضع المحاكاة DRY-RUN: لن يفتح أي متصفح ولن ينفذ أي تفاعل")
    next_check_at = 0
    while True:
        try:
            now = time.time()
            # 1) فحص تغريدات المدير (smart exponential backoff)
            if now >= next_check_at:
                tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=args.dry_run)
                had_tweets = len(tweets) > 0
                log.info(f"فحص المدير: {status} | جديد: {len(tweets)}")

                if had_tweets:
                    seen = load_seen()
                    # تفاعل فوري مباشر (بدون جدولة)
                    for t in tweets:
                        tid = t["id"] if isinstance(t, dict) else t
                        if not seen.get(tid, {}).get("immediate_done"):
                            monitor.execute_immediate_engagement(tid, t.get("text", "") if isinstance(t, dict) else "", cfg, accounts)

                # Smart backoff: 1-2-4-8-15-30 دقايق إذا ما في تويتات جديدة
                next_check_at = now + _next_interval(cfg, last_had_tweets=had_tweets)
                interval_min = int(_next_interval(cfg, last_had_tweets=had_tweets) / 60)
                log.info(f"⏱️ الفحص التالي بعد {interval_min} دقيقة")
            # 2) الجلسات اليومية + مهام المدير المستحقة
            res = routine.check_due_sessions(cfg, accounts, dry_run=args.dry_run)
            if res["sessions"]:
                log.info(f"جلسات منفذة: {res['sessions']}")
            # 3) الإحماء اليومي المستحق
            wres = routine.check_due_warmups(cfg, accounts, dry_run=args.dry_run)
            if wres["warmups"]:
                log.info(f"إحماءات منفذة: {wres['warmups']}")
        except KeyboardInterrupt:
            print("\n⏹ إيقاف التشغيل")
            break
        except Exception as e:
            log.error(f"خطأ في دورة المراقبة: {e}")
            if _is_deadlock_error(e):
                _self_restart(str(e)[:200])
        time.sleep(60)


def _run_full_pass(cfg, accounts, dry_run=False):
    """دورة كاملة واحدة: فحص المدير + جدولة جديد + جلسات مستحقة + إحماء مستحق."""
    res = {"sessions": 0, "warmups": 0, "scheduled": 0}
    tweets, status = monitor.fetch_manager_tweets(cfg, accounts, dry_run=dry_run)
    log.info(f"فحص المدير: {status} | جديد: {len(tweets)}")
    if tweets:
        seen = load_seen()
        # تفاعل فوري مباشر (بدون جدولة)
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
    """Smart exponential backoff: قلل الـ polling لما ما تكون تويتات جديدة."""
    state_file = os.path.join(os.path.dirname(__file__), "data", "monitor_state.json")
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"checks_without_tweets": 0}

    if last_had_tweets:
        state["checks_without_tweets"] = 0  # reset لما يكون في تويتات جديدة
    else:
        state["checks_without_tweets"] = state.get("checks_without_tweets", 0) + 1

    # Save state
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w") as f:
            json.dump(state, f)
    except Exception:
        pass

    # استخدم config interval كـ base (60-120 دقيقة)
    # بدون exponential backoff (webhook = primary)
    lo, hi = cfg["monitor"]["interval_min"]

    # إذا آخر check كان في tweets → استخدم الـ base interval
    if state["checks_without_tweets"] == 0:
        return (lo + (hi - lo) * __import__("random").random()) * 60

    # إذا ما في tweets → ضاعف الـ interval (لكن ليس أكثر من 120 دقيقة)
    multiplier = min(2, state["checks_without_tweets"] * 0.5)  # max 2x
    base_minutes = (lo + hi) / 2
    final_minutes = min(120, base_minutes * multiplier)

    return final_minutes * 60


def cmd_daily(args):
    cfg = load_config()
    accounts = load_accounts()
    if args.account not in accounts:
        sys.exit(f"الحساب {args.account} غير موجود")
    routine.run_session(args.account, cfg, accounts, dry_run=args.dry_run)


def cmd_warmup(args):
    """جلسة إحماء: فتح الحساب والتفاعل مع مهتمين بالكريبتو حتى بدون تغريدات مدير."""
    cfg = load_config()
    accounts = load_accounts()
    if args.account not in accounts:
        sys.exit(f"الحساب {args.account} غير موجود")
    routine.run_warmup(args.account, cfg, accounts, dry_run=args.dry_run)


def cmd_status(args):
    cfg = load_config()
    accounts = load_accounts()
    seen = load_seen()
    print(f"المدير: @{cfg.get('manager_handle','')}")
    print(f"الحسابات المفعّلة: {len(enabled_accounts(accounts))} / {len(accounts)}")
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
    print(f"مهام تفاعل معلّقة: {pending}")
    print(f"تغريدات محذوفة (مُهمَلة): {deleted}")
    print(f"عمليات متخطّاة (فشل نهائي): {failed_actions}")
    print()
    for aid in enabled_accounts(accounts):
        st = load_state(aid)
        d = st.get("done", {})
        c = st.get("caps", {})
        relog = "⚠ إعادة دخول" if st.get("needs_relogin") else ""
        followed = len(st.get("followed_handles", []))
        wu = st.get("warmup_at", "-")
        wud = "تم ✓" if st.get("warmup_done") else "معلق"
        print(f"  {aid:<10} لايك {d.get('likes',0)}/{c.get('likes','?')} | ريتويت {d.get('retweets',0)}/{c.get('retweets','?')} | تعليق {d.get('comments',0)}/{c.get('comments','?')} | متابعة {d.get('follows',0)}/{c.get('follows','?')}{' (أمس: ' + str(followed) + ')' if followed else ''} | إحماء {wu} {wud} {relog}")


def main():
    p = argparse.ArgumentParser(description="SmartEngage - إدارة تفاعل ذكية لتويتر")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", help="إنشاء ملفات الإعدادات")

    a = sub.add_parser("add", help="إضافة حساب")
    a.add_argument("--handle", required=True, help="اسم المستخدم بدون @")
    a.add_argument("--username", default="", help="الاسم الظاهر")
    a.add_argument("--display-name", default="")
    a.add_argument("--timezone", default="Asia/Riyadh")
    a.add_argument("--notes", default="")
    a.add_argument("--en-ratio", type=float, default=None, help="نسبة التعليقات الإنجليزية 0-1 (0=عربي، 1=إنجليزي؛ الافتراضي يتبع comments.en_ratio)")

    sub.add_parser("list", help="عرض الحسابات")

    a = sub.add_parser("login", help="تسجيل دخول يدوي أول مرة")
    a.add_argument("account")

    a = sub.add_parser("check", help="فحص تغريدات المدير مرة واحدة")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--no-schedule", action="store_true")

    a = sub.add_parser("monitor", help="الأمر الواحد: مراقبة + جلسات + إحماء + متابعات")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--once", action="store_true", help="تنفيذ دورة واحدة فقط ثم الخروج")

    a = sub.add_parser("daily", help="تشغيل جلسة يومية لحساب")
    a.add_argument("account")
    a.add_argument("--dry-run", action="store_true")

    a = sub.add_parser("warmup", help="جلسة إحماء خفيفة (تفاعل كريبتو يومي)")
    a.add_argument("account")
    a.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="حالة الحسابات")

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
