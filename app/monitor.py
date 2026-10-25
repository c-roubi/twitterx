# -*- coding: utf-8 -*-
"""مراقبة تغريدات المدير وجدولة التفاعلات المتدرجة."""
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
    """جلب آخر تغريدات المدير. الطريقة: guest (بدون حساب) أو account (بأحد الحسابات)."""
    handle = cfg["manager_handle"].lstrip("@")
    if not handle:
        log.warning("manager_handle فارغ - اضبطه في config.json")
        return [], "no-manager"

    method = cfg["monitor"].get("method", "guest")
    if method == "account" and cfg["monitor"].get("use_account"):
        acc_id = cfg["monitor"]["use_account"]
        if acc_id not in accounts:
            log.warning(f"حساب المراقبة {acc_id} غير موجود - التحول إلى guest")
            method = "guest"

    if dry_run:
        return [], "dry-run"

    retries = int(cfg.get("resilience", {}).get("monitor_fetch_retries", 2))
    base = float(cfg.get("resilience", {}).get("retry_base_delay_s", 5.0))
    for attempt in range(retries + 1):
        if method == "account":
            ids, status = _fetch_with_account(cfg, accounts, acc_id)
        else:
            ids, status = _fetch_guest(cfg)
        if ids or not resilience.is_retryable(resilience.error_kind_from_msg(status)):
            return ids, status
        if attempt < retries:
            d = resilience.backoff_delay(attempt, base)
            log.warning(
                f"فشل جلب تغريدات المدير ({status}) - إعادة المحاولة ({attempt + 2}/{retries + 1}) بعد {d:.1f} ثانية"
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
                f"جلسة الضيف محجوبة ({status}) - فعّل monitor.method=account وحدد use_account في config.json "
                "ليتم الفحص بحساب مسجّل دخول، أو شغّل الأداة من شبكة منزلية/مكتبية"
            )
        return ids, status
    except Exception as e:
        log.error(f"فشل جلب تغريدات المدير (guest): {e}")
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
            log.warning(f"{acc_id} | حساب المراقبة غير مسجّل الدخول")
            browser.close_ctx(pw, ctx, False)
            return [], "not-logged-in"
        human = humanizer.Human(page)
        ids, status = scrape_profile_tweets(
            page, cfg["manager_handle"].lstrip("@"), max_scrolls=3, human=human
        )
        browser.close_ctx(pw, ctx, False)
        return ids, status
    except Exception as e:
        log.error(f"فشل جلب تغريدات المدير (account {acc_id}): {e}")
        return [], f"error:{e}"[:100]
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def _detect_consecutive_tweets(seen):
    """كشف التغريدات المتتالية: إرجاع (أحدث_tweet_id, أقدم_tweet_id) إذا كانتا متتاليتين خلال 5 دقايق."""
    scheduled = [
        (tid, entry["detected_at"])
        for tid, entry in seen.items()
        if entry.get("scheduled")
    ]
    if len(scheduled) < 2:
        return None, None

    # ترتيب حسب التاريخ (الأحدث أولاً)
    scheduled.sort(key=lambda x: x[1], reverse=True)

    latest_tid, latest_time = scheduled[0]
    prev_tid, prev_time = scheduled[1]

    # تحويل ISO timestamps إلى datetime objects
    from datetime import datetime
    try:
        latest_dt = datetime.fromisoformat(latest_time)
        prev_dt = datetime.fromisoformat(prev_time)
        time_diff = (latest_dt - prev_dt).total_seconds()

        # إذا الفرق أقل من 300 ثانية (5 دقايق) → متتاليتين
        if 0 < time_diff < 300:
            log.info(f"🔥 اكتشاف تغريدتين متتاليتين: {latest_tid} و {prev_tid} (فرق {int(time_diff)}s)")
            return latest_tid, prev_tid
    except Exception:
        pass

    return None, None


def execute_immediate_engagement(tid, text="", cfg=None, accounts=None):
    """تفاعل فوري مباشر على التغريدة (بدون جدولة).

    يختار حسابات عشوائية وينفذ التفاعل (like + retweet + comment) فوراً.
    مثالي للتفاعل السريع مع تغريدات المدير الجديدة.
    """
    from . import routine

    seen = load_seen()
    if tid in seen and seen[tid].get("immediate_done"):
        return None

    enabled = [
        aid for aid, a in accounts.items()
        if a.get("enabled", True)
    ]
    if not enabled:
        log.warning("لا حسابات مفعّلة للتفاعل الفوري")
        return None

    lo, hi = cfg["engagement"]["accounts_per_tweet"]
    lo = min(lo, len(enabled))
    n = random.randint(lo, min(hi, len(enabled)))
    chosen = random.sample(enabled, n)

    log.info(f"🚀 تفاعل فوري للتغريدة {tid}: اختيار {len(chosen)} حساب")

    # حفظ التغريدة كمفعولة فوراً
    now = time.time()
    seen[tid] = {
        "tweet_id": tid,
        "url": TWEET_URL.format(tid=tid),
        "text": (text or "")[:2000],
        "detected_at": datetime.now().isoformat(timespec="seconds"),
        "immediate_done": True,
        "mode": "immediate",
        "accounts": chosen,
    }
    save_seen(seen)

    # تنفيذ التفاعل الفوري (بدون جدولة طويلة)
    for idx, aid in enumerate(chosen):
        delay = random.uniform(5, 25) if idx == 0 else random.uniform(10, 45)
        try:
            routine.execute_single_account_immediate(aid, tid, text, cfg, accounts, delay)
        except Exception as e:
            log.warning(f"خطأ في التفاعل الفوري للحساب {aid}: {e}")
            continue

    return seen[tid]


def schedule_for_tweet(tid, text="", cfg=None, accounts=None, is_priority=False):
    """اختيار مجموعة حسابية عشوائية وتوزيع مهام عليها بجدول متدرج.

    text: نص التغريدة (يُحفظ لاستخدامه في التوليد السياقي للتعليقات).
    is_priority: إذا True → التغريدة الأولى (متتالية) = full engagement (100% like/retweet).
    """
    seen = load_seen()
    if tid in seen and seen[tid].get("scheduled"):
        return None

    enabled = [
        aid for aid, a in accounts.items()
        if a.get("enabled", True)
    ]
    if not enabled:
        log.warning("لا حسابات مفعّلة لجدولة التفاعل")
        return None

    lo, hi = cfg["engagement"]["accounts_per_tweet"]
    # حماية: لو عدد الحسابات أقل من الحد الأدنى، نتفاعل مع كل المتاح
    lo = min(lo, len(enabled))
    n = random.randint(lo, min(hi, len(enabled)))
    chosen = random.sample(enabled, n)
    mix = cfg["engagement"]["action_mix"]
    max_comments = cfg["engagement"].get("max_comments_per_tweet", 8)
    spread = random.uniform(*cfg["engagement"]["spread_hours"]) * 3600

    # نسبة تعليق شبه ثابتة ~ نسبة المزيج (60%) مع تذبذب خفيف 55-65%
    comment_rate = mix.get("comment", 0.6)
    n_comments = round(len(chosen) * random.uniform(comment_rate - 0.05, comment_rate + 0.05))
    n_comments = max(0, min(n_comments, max_comments, len(chosen)))
    commenters = set(random.sample(chosen, n_comments)) if n_comments else set()

    now = time.time()
    queue = []

    # استراتيجية ذكية بناءً على ما إذا كانت priority (أول تغريدة من متتاليتين)
    if is_priority:
        # 🔥 التغريدة الأولى → full engagement (100% like + retweet)
        like_ratio = 1.0  # 100% يعملوا like
        retweet_ratio = 1.0  # 100% يعملوا retweet
        log.info(f"⚡ تغريدة الأولوية {tid}: تفاعل كامل (100% like/retweet)")
    else:
        # عادي: نسب عالية (80-95%) مع variation
        like_ratio = random.uniform(0.80, 0.95)  # 80-95% يعملوا like
        retweet_ratio = random.uniform(0.80, 0.95)  # 80-95% يعملوا retweet

    like_count = int(len(chosen) * like_ratio)
    retweet_count = int(len(chosen) * retweet_ratio)
    like_accounts = set(random.sample(chosen, like_count))
    retweet_accounts = set(random.sample(chosen, retweet_count))

    for idx, aid in enumerate(chosen):
        actions = []
        # Like: حسب النسبة
        if aid in like_accounts:
            actions.append("like")
        # Retweet: حسب النسبة
        if aid in retweet_accounts:
            actions.append("retweet")
        # Comment: كل الحسابات (100% - أقل suspicious)
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
    engagement_type = "🔥 كامل" if is_priority else "📊 متغير"
    log.info(f"جدولة تفاعل للتغريدة {tid}: {len(queue)} حساب | {engagement_type} | أول عملية بعد {int((queue[0]['due']-now))} ثانية")
    return seen[tid]


def due_tasks_for(acc_id, seen=None):
    """مهام الحساب المستحقة التنفيذ الآن (تتخطى المحذوفة والمتخلى عنها)."""
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
    """وسم التغريدة كمحذوفة من المدير وإزالتها من الجدول نهائياً."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    entry["deleted"] = True
    entry["deleted_at"] = datetime.now().isoformat(timespec="seconds")
    save_seen(seen)
    log.info(f"التغريدة {tid} محذوفة من المدير - أُزيلت من جدول المهام")


def record_tweet_open_attempt(tid):
    """زيادة عدّاد محاولات فتح التغريدة (لتشخيص المشاكل)."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    entry["open_attempts"] = entry.get("open_attempts", 0) + 1
    save_seen(seen)


def record_action_attempt(tid, acc_id, action):
    """تسجيل محاولة تنفيذ عملية معينة على تغريدة المدير لحساب معين."""
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
    """التخلي النهائي عن عملية بعد تجاوز حد المحاولات (تخطي واستمرار)."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    for task in entry.get("queue", []):
        if task["account"] == acc_id:
            if action not in task.setdefault("failed", []):
                task["failed"].append(action)
    save_seen(seen)
    log.info(f"{acc_id} | تخطّي نهائي للعملية {action} على التغريدة {tid}")