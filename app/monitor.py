# -*- coding: utf-8 -*-
"""مراقبة تغريدات المدير وجدولة التفاعلات المتدرجة."""
import random
import time
from datetime import datetime

from . import browser, humanizer, resilience
from .actions import scrape_profile_tweets
from .config import load_accounts, load_seen, locked_seen, save_seen
from .logger import get_logger

log = get_logger("monitor")

TWEET_URL = "https://x.com/twitter/status/{tid}"


TWITTER_EPOCH_MS = 1288834974657


def tweet_age_hours(tid):
    """عمر التغريدة بالساعات من معرّفها (Snowflake)، أو None إن لم يكن المعرّف صالحاً."""
    try:
        n = int(tid)
        if n < 10 ** 15:  # ليس معرّف Snowflake حديثاً
            return None
        ts = ((n >> 22) + TWITTER_EPOCH_MS) / 1000.0
    except (TypeError, ValueError):
        return None
    if ts < 1.2e9 or ts > time.time() + 86400:
        return None
    return max(0.0, (time.time() - ts) / 3600.0)


def filter_recent(items, cfg):
    """يُسقط تغريدات المدير الأقدم من monitor.max_tweet_age_hours (0 = بلا حد) حتى لا يُتفاعل مع القديم."""
    hours = float(cfg.get("monitor", {}).get("max_tweet_age_hours", 12) or 0)
    if not hours or not items:
        return items
    keep, skipped = [], 0
    for it in items:
        tid = it["id"] if isinstance(it, dict) else it
        age = tweet_age_hours(tid)
        if age is None or age <= hours:
            keep.append(it)
        else:
            skipped += 1
    if skipped:
        log.info(f"تخطّي {skipped} تغريدة أقدم من {hours:g} ساعة")
    return keep


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
            return filter_recent(ids, cfg), status
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
        if not browser.is_logged_in(page):
            # ✅ تم التعديل هنا: إضافة wait_until="domcontentloaded" لتسريع التحميل وتجنب الـ Timeout
            page.goto("https://x.com/home", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
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


@locked_seen
def schedule_for_tweet(tid, text="", cfg=None, accounts=None, source="manager", priority=False, notify_chat=None, skipped=None):
    """اختيار مجموعة حسابية عشوائية وتوزيع مهام عليها بجدول متدرج.

    priority=True: تغريدة عاجلة (مثل أوامر الأدمن): تُنفَّذ فوراً وبالتتابع حساباً بعد حساب،
    بدون انتظار جلسة الحساب ومن غير التقيّد بالحدود اليومية أو يوم الراحة.

    text: نص التغريدة (يُحفظ لاستخدامه في التوليد السياقي للتعليقات).
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
    for aid in chosen:
        actions = []
        if random.random() <= mix.get("like", 1.0):
            actions.append("like")
        if random.random() <= mix.get("retweet", 1.0):
            actions.append("retweet")
        if aid in commenters:
            actions.append("comment")
        if not actions:
            actions = ["like"]
        due = now if priority else now + random.uniform(120, spread)  # العاجلة فوراً، العادية بعد دقيقتين على الأقل
        queue.append({"account": aid, "actions": actions, "due": due, "done": []})

    seen[tid] = {
        "tweet_id": tid,
        "url": TWEET_URL.format(tid=tid),
        "text": (text or "")[:2000],
        "detected_at": datetime.now().isoformat(timespec="seconds"),
        "scheduled": True,
        "source": source,
        "priority": bool(priority),
        "notify_chat": notify_chat,
        "skipped": list(skipped or []),
        "queue": queue,
    }
    save_seen(seen)
    log.info(f"جدولة تفاعل للتغريدة {tid}: {len(queue)} حساب | أول عملية بعد {int((queue[0]['due']-now)/60)} دقيقة")
    return seen[tid]


def due_tasks_for(acc_id, seen=None, priority=False):
    """مهام الحساب المستحقة التنفيذ الآن (تتخطى المحذوفة والمتخلى عنها)."""
    seen = seen or load_seen()
    tasks = []
    for tid, entry in seen.items():
        if not entry.get("scheduled"):
            continue
        if entry.get("deleted"):
            continue
        if entry.get("cancelled"):
            continue
        if bool(entry.get("priority")) != bool(priority):
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


def priority_pending(acc_id):
    """هل لدى الحساب مهام عاجلة مستحقة؟ (تُستخدم لإنهاء التصفح العادي مبكراً)."""
    try:
        return bool(due_tasks_for(acc_id, priority=True))
    except Exception:
        return False


@locked_seen
def cancel_tweet(tid):
    """إلغاء تغريدة معلّقة: لا تُنفَّذ عملياتها المتبقية."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry or entry.get("cancelled"):
        return False
    entry["cancelled"] = True
    entry["cancelled_at"] = datetime.now().isoformat(timespec="seconds")
    save_seen(seen)
    log.info(f"أُلغيت التغريدة {tid}")
    return True


@locked_seen
def cancel_stale(cfg):
    """يلغي تغريدات المدير المعلّقة الأقدم من الحد المسموح (تنظيف تراكمات قديمة). يرجع عددها."""
    hours = float(cfg.get("monitor", {}).get("max_tweet_age_hours", 12) or 0)
    if not hours:
        return 0
    seen = load_seen()
    n = 0
    for tid, e in seen.items():
        if e.get("source", "manager") != "manager" or e.get("completed_at") or e.get("deleted") or e.get("cancelled"):
            continue
        age = tweet_age_hours(tid)
        if age is not None and age > hours:
            e["cancelled"] = True
            e["cancelled_at"] = datetime.now().isoformat(timespec="seconds")
            n += 1
    if n:
        save_seen(seen)
        log.info(f"أُلغيت {n} تغريدة معلّقة أقدم من {hours:g} ساعة")
    return n


@locked_seen
def set_tweet_text(tid, text):
    """حفظ نص التغريدة المقروء من الصفحة (لتعليقات الذكاء الاصطناعي والتعليقات السياقية)."""
    seen = load_seen()
    entry = seen.get(tid)
    if entry is not None and not entry.get("text") and text:
        entry["text"] = text[:2000]
        save_seen(seen)


@locked_seen
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


@locked_seen
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


@locked_seen
def record_tweet_open_attempt(tid):
    """زيادة عدّاد محاولات فتح التغريدة (لتشخيص المشاكل)."""
    seen = load_seen()
    entry = seen.get(tid)
    if not entry:
        return
    entry["open_attempts"] = entry.get("open_attempts", 0) + 1
    save_seen(seen)


@locked_seen
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


@locked_seen
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