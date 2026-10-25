# -*- coding: utf-8 -*-
"""الروتين اليومي الذكي: نشاط عشوائي طبيعي + تنفيذ تفاعلات المدير."""
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
    enabled_accounts, load_accounts, load_config,
    load_seen, load_state, save_state,
)
from .grow import crypto_activity
from .humanizer import (
    Human, is_rest_day, pick_daily_caps, pick_daily_follow_target,
    random_sessions, randint, randfloat,
)
from .logger import get_logger

log = get_logger("routine")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def ensure_daily_plan(acc_id, cfg):
    """إنشاء خطة يومية عشوائية للحساب إن لم تكن موجودة لليوم."""
    state = load_state(acc_id)
    if state.get("date") == _today():
        return state
    rest_day = is_rest_day(cfg, state)
    caps = pick_daily_caps(cfg, state, rest_day=rest_day)
    caps["follows"] = pick_daily_follow_target(cfg, state, rest_day=rest_day)
    state = {
        "date": _today(),
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
    tag = " | يوم راحة (تصفح فقط)" if rest_day else ""
    log.info(f"{acc_id} | خطة اليوم: {caps['likes']} لايك / {caps['retweets']} ريتويت / {caps['comments']} تعليق / {caps['follows']} متابعة | جلسات: {[s['start'] for s in state['sessions']]} | إحماء: {state['warmup_at']}{tag}")
    return state


def _random_warmup_time(cfg):
    """وقت عشوائي للإحماء اليومي ضمن ساعات النشاط (قبل نهايتها بثلاثين دقيقة على الأقل)."""
    ah = cfg.get("behavior", {}).get("active_hours", ["08:00", "23:00"])
    try:
        h0, m0 = map(int, ah[0].split(":"))
        h1, m1 = map(int, ah[1].split(":"))
    except Exception:
        h0, m0, h1, m1 = 8, 0, 23, 0
    start_min = h0 * 60 + m0
    end_min = h1 * 60 + m1
    if end_min <= start_min + 30:
        end_min = start_min + 60
    w = randint(start_min, end_min - 30)
    return f"{w // 60:02d}:{w % 60:02d}"


def _can_do(state, action):
    return state["done"][action] < state["caps"][action]


def _mark_done(state, action):
    state["done"][action] += 1


_CAP_KEYS = {"like": "likes", "retweet": "retweets", "comment": "comments", "follow": "follows"}


def _cap_key(action):
    return _CAP_KEYS.get(action, action)


def _account_en_ratio(acc):
    """نسبة التعليقات الإنجليزية للحساب (None => يتبع الإعداد العام comments.en_ratio)."""
    if not acc:
        return None
    v = acc.get("en_ratio")
    if v is None:
        return None
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


def run_session(acc_id, cfg, accounts, dry_run=False):
    """جلسة واحدة كاملة لحساب: تصفح + تفاعلات عشوائية + نشاط كريبتو + مهام المدير المستحقة."""
    acc = dict(accounts[acc_id])
    acc["id"] = acc_id
    state = ensure_daily_plan(acc_id, cfg)
    human = Human()

    log.info(f"===== بدء جلسة {acc_id} ({acc.get('handle','')}) =====")
    if dry_run:
        _dry_run_session(acc_id, cfg, state)
        return

    pw, ctx, page, pw_owned = None, None, None, False
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human.page = page

        ok_home, gs = resilience.goto_with_retry(page, "https://x.com/home", cfg)
        if not ok_home:
            log.warning(f"{acc_id} | تعذر فتح الصفحة الرئيسية ({gs}) - تُعاد المحاولة لاحقاً")
            return
        page.wait_for_timeout(3000)

        # تعمل dismiss تلقائي لأي modal/popup (Views, alerts, إلخ)
        actions.dismiss_all_modals(page, human)

        if browser.is_login_wall(page) or not browser.is_logged_in(page):
            state["needs_relogin"] = True
            save_state(acc_id, state)
            log.warning(f"{acc_id} | الجلسة منتهية - تحتاج إعادة تسجيل دخول: python main.py login {acc_id}")
            return

        minutes = cfg["behavior"].get("session_minutes", [15, 35])
        session_secs = randint(minutes[0], minutes[1]) * 60
        session_start = time.time()

        # 1) تصفح عشوائي وتفاعلات طبيعية
        _browse_and_interact(page, acc_id, cfg, state, human, session_secs, session_start, acc=acc)
        save_state(acc_id, state)  # حفظ تقدم الجلسة بعد التصفح (حماية من الانهيار)

        # 2) نشاط الكريبتو اليومي: تصفح بحث + متابعات بطيئة للمهتمين
        remaining = max(0, int(session_secs - (time.time() - session_start)))
        crypto_activity(page, acc_id, cfg, state, human, max(120, min(remaining, 900)))
        save_state(acc_id, state)  # حفظ تقدم الكريبتو (لايك/متابعات)

        # 3) تنفيذ مهام المدير المستحقة لهذا الحساب
        _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=acc)

        state["sessions_run"] += 1
        state["last_session_end"] = datetime.now().isoformat(timespec="seconds")
        state["needs_relogin"] = False
        save_state(acc_id, state)
        log.info(f"{acc_id} | نهاية الجلسة | لايك {state['done']['likes']} / ريتويت {state['done']['retweets']} / تعليق {state['done']['comments']}")
    except Exception as e:
        log.error(f"{acc_id} | خطأ في الجلسة: {e}")
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
    """جلسة إحماء خفيفة: فتح الحساب والتفاعل مع كريبتو حتى لو لم ينزل المدير شيئاً.

    لا تحسب ضمن جلسات الخطة اليومية (لا تزيد sessions_run) حتى لا تؤثر على جدول الجلسات.
    ترجع dict بحالة التنفيذ.
    """
    acc = dict(accounts[acc_id])
    acc["id"] = acc_id
    state = ensure_daily_plan(acc_id, cfg)
    human = Human()

    log.info(f"===== إحماء {acc_id} ({acc.get('handle','')}) =====")
    if dry_run:
        _dry_run_session(acc_id, cfg, state)
        return {"acc_id": acc_id, "status": "dry-run"}

    pw, ctx, page, pw_owned = None, None, None, False
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human.page = page

        ok_home, gs = resilience.goto_with_retry(page, "https://x.com/home", cfg)
        if not ok_home:
            log.warning(f"{acc_id} | تعذر فتح الصفحة الرئيسية أثناء الإحماء ({gs})")
            return {"acc_id": acc_id, "status": "network-error"}
        page.wait_for_timeout(3000)

        if browser.is_login_wall(page) or not browser.is_logged_in(page):
            state["needs_relogin"] = True
            save_state(acc_id, state)
            log.warning(f"{acc_id} | الإحماء متوقف - تحتاج إعادة تسجيل دخول: python main.py login {acc_id}")
            return {"acc_id": acc_id, "status": "relogin"}

        minutes = cfg.get("warmup", {}).get("session_minutes", [8, 15])
        budget_s = randint(minutes[0], minutes[1]) * 60

        # تصفح خفيف للرئيسية ثم نشاط كريبتو (بحث + متابعات بطيئة)
        scroll_home(page, human, steps=randint(1, 2))
        human.mouse_wander(page)
        crypto_activity(page, acc_id, cfg, state, human, budget_s)

        state["last_session_end"] = datetime.now().isoformat(timespec="seconds")
        state["needs_relogin"] = False
        state["warmup_done"] = True
        save_state(acc_id, state)
        log.info(f"{acc_id} | نهاية الإحماء | لايك {state['done']['likes']} / متابعات {state['done']['follows']}")
        return {"acc_id": acc_id, "status": "ok"}
    except Exception as e:
        log.error(f"{acc_id} | خطأ في الإحماء: {e}")
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
    """هل حان موعد إحماء اليوم لهذا الحساب؟"""
    if not state or not state.get("warmup_at"):
        return False
    if state.get("warmup_done"):
        return False
    if state.get("needs_relogin"):
        return False
    try:
        h, m = map(int, state["warmup_at"].split(":"))
    except Exception:
        return False
    now = datetime.now()
    return (now.hour, now.minute) >= (h, m)


def check_due_warmups(cfg, accounts, dry_run=False):
    """تشغيل الإحماء اليومي للحسابات التي حان موعدها. ترجع dict بالنتائج."""
    results = {"warmups": 0}
    if not cfg.get("warmup", {}).get("enabled", True):
        return results
    for acc_id in enabled_accounts(accounts):
        state = ensure_daily_plan(acc_id, cfg)
        if warmup_due(state, cfg):
            res = run_warmup(acc_id, cfg, accounts, dry_run=dry_run)
            results["warmups"] += 1
            results.setdefault("details", []).append(res)
    return results


# FIX-MIX-V1-STATE
STATE_KEY = {"like": "likes", "retweet": "retweets", "comment": "comments"}


def _pick_browse_action(cfg, state):
    """اختيار تفاعل عشوائي واحد للدور (like/retweet/comment) حسب الأوزان."""
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
    """لايك لتغريدة عشوائية واحدة ضمن الحد اليومي."""
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
            log.info(f"{acc_id} | لايك عشوائي #{state['done']['likes']}")
            return True
        except Exception:
            continue
    return False


def _retweet_one_random(page, articles, acc_id, state, human, retweeted_ids=None):
    """ريتويت لتغريدة عشوائية واحدة ضمن الحد اليومي."""
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
                    log.warning(f"{acc_id} | فُتح محرر الاقتباس بالخطأ - أُغلق بدون حفظ")
                    return False
                if tid and retweeted_ids is not None:
                    retweeted_ids.add(tid)
                _mark_done(state, "retweets")
                log.info(f"{acc_id} | ريتويت عشوائي #{state['done']['retweets']}")
                return True
            page.keyboard.press("Escape")
        except Exception:
            continue
    return False


def _browse_and_interact(page, acc_id, cfg, state, human, session_secs, session_start, acc=None):
    """تصفح الرئيسية بخلط عشوائي: لايك/ريتويت/تعليق متناوبين مثل الإنسان."""
    en_ratio = _account_en_ratio(acc)
    last_action = None
    scrolls = 0
    liked_ids = set()
    retweeted_ids = set()
    dismiss_compose_dialog(page, human)  # تنظيف أي محرر/حوار من جولة سابقة
    while time.time() - session_start < session_secs:
        save_state(acc_id, state)  # حفظ التقدم عند كل دور
        # الخروج المبكر إذا اكتملت حصة التفاعلات
        if (not _can_do(state, "likes") and not _can_do(state, "retweets")
                and not _can_do(state, "comments")):
            break
        # إن بقي محرر الاقتباس مفتوحاً: أغلقه ونؤجل الدور
        if "/compose/" in (page.url or ""):
            dismiss_compose_dialog(page, human)
            if "/compose/" in (page.url or ""):
                log.warning(f"{acc_id} | محرر الاقتباس عالق - تخطي جولة")
                human.pause(4, 8)
                continue
        # تمرير وتوقف
        human.pause(2.0, 6.0)
        page.mouse.wheel(0, random.randint(500, 1500))
        scrolls += 1
        # ✅ التعديل: إعطاء المتصفح مهلة لجلب تغريدات جديدة أثناء التمرير العشوائي
        page.wait_for_timeout(1000)

        if scrolls % 3 == 0:
            human.pause(4.0, 12.0)  # توقف "للقراءة"

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
                        log.info(f"{acc_id} | تعليق عشوائي #{state['done']['comments']}")
                # الرجوع للرئيسية بعد التعليق (نجاحاً أو فشلاً)
                try:
                    # ✅ التعديل: إضافة wait_until لمنع انهيار الجلسة عند بطء النت
                    page.goto("https://x.com/home", timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            except Exception:
                pass
        save_state(acc_id, state)
# FIX-MIX-V1
def _open_and_comment(page, url, acc_id, state, human, cfg=None, en_ratio=None):
    """فتح تغريدة والتعليق عليها بتعليق سياقي ذكي (من نص التغريدة)."""
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
    """استخراج نص التغريدة المفتوحة حالياً (للتوليد السياقي للتعليقات)."""
    try:
        el = page.locator(TWEET_TEXT).first
        if el.count() > 0:
            return (el.inner_text() or "").strip()[:500]
    except Exception:
        pass
    return ""


def _action_attempts(tid, acc_id, action):
    """عدد محاولات تنفيذ عملية معينة على تغريدة المدير لحساب معين."""
    seen = load_seen()
    entry = seen.get(tid, {})
    for task in entry.get("queue", []):
        if task["account"] == acc_id:
            return int(task.get("attempts", {}).get(action, 0))
    return 0


def _handle_action_failure(cfg, tid, acc_id, action, msg):
    """معالجة فشل عملية واحدة: عدّ المحاولات ثم تخطي نهائي بعد تجاوز الحد."""
    monitor.record_action_attempt(tid, acc_id, action)
    max_attempts = int(cfg.get("resilience", {}).get("action_max_attempts", 3))
    attempts = _action_attempts(tid, acc_id, action)
    kind = resilience.error_kind_from_msg(msg)
    if kind in ("network", "timeout") and attempts < max_attempts:
        log.warning(f"{acc_id} | {action} على {tid} فشل مؤقتاً ({msg}) - محاولة {attempts}/{max_attempts}")
        return
    if attempts >= max_attempts:
        monitor.give_up_action(tid, acc_id, action)
        log.warning(f"{acc_id} | {action} على {tid} تخطّي نهائي بعد {attempts} محاولات")
    else:
        log.warning(f"{acc_id} | {action} على {tid} فشل: {msg}")


def _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=None):
    """تنفيذ كل مهام المدير المستحقة (مع معالجة الحذف وفشل العملية بالتخطي)."""
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
                log.warning(f"{acc_id} | تغريدة المدير {task['tweet_id']} محذوفة - أُزيلت من الجدول")
            elif status == "login-wall":
                log.warning(f"{acc_id} | جدار تسجيل دخول عند فتح {task['tweet_id']} - تخطي")
            else:
                monitor.record_tweet_open_attempt(task["tweet_id"])
                log.warning(f"{acc_id} | تعذر فتح تغريدة المدير {task['tweet_id']}: {status}")
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
                save_state(acc_id, state)  # حفظ تقدم مهام المدير فور نجاح كل عملية
                log.info(f"{acc_id} | ✓ {action} على تغريدة المدير {task['tweet_id']}")
            else:
                _handle_action_failure(cfg, task["tweet_id"], acc_id, action, msg)
            human.pause(randint(*cfg["behavior"]["min_action_delay_s"]),
                        randint(*cfg["behavior"]["max_action_delay_s"]))


def _comments_used_for(tid):
    seen = load_seen()
    return seen.get(tid, {}).get("comments_used", [])


def _record_comment(url, text, tid=None):
    seen = load_seen()
    key = None
    for k, v in seen.items():
        if v.get("url") == url or (tid and k == tid):
            key = k
            break
    if key and key in seen:
        seen[key].setdefault("comments_used", []).append(text)
        from .config import save_seen
        save_seen(seen)


def _dry_run_session(acc_id, cfg, state):
    """محاكاة الجلسة بدون فتح متصفح - لعرض ما سيجري."""
    caps = state["caps"]
    done = state.get("done", {})
    print(f"[DRY-RUN] {acc_id}:")
    print(f"  - الخطة: {caps['likes']} لايك | {caps['retweets']} ريتويت | {caps['comments']} تعليق | {caps['follows']} متابعة كريبتو")
    print(f"  - المنجز: {done.get('likes', 0)} لايك | {done.get('retweets', 0)} ريتويت | {done.get('comments', 0)} تعليق | {done.get('follows', 0)} متابعة")
    print(f"  - الجلسات اليوم: {[s['start'] + ' (' + str(s['minutes']) + 'د)' for s in state['sessions']]}")
    wu = state.get('warmup_at', '-')
    print(f"  - الإحماء: {wu} {'(تم)' if state.get('warmup_done') else '(معلق)'}")
    if state.get('rest_day'):
        print("  - اليوم: يوم راحة (تصفح فقط بدون تفاعلات)")
    tasks = monitor.due_tasks_for(acc_id)
    if tasks:
        print(f"  - مهام المدير المستحقة: {len(tasks)}")
        for t in tasks:
            print(f"      * تغريدة {t['tweet_id']}: {', '.join(t['remaining'])}")
    else:
        print("  - لا مهام مستحقة للمدير حالياً")


def execute_single_account_immediate(acc_id, tid, tweet_text, cfg, accounts, delay):
    """تنفيذ تفاعل فوري لحساب واحد على تغريدة (مباشرة بدون جدولة).

    يفتح الحساب، ينتظر delay، ثم يعمل like + retweet + comment على التغريدة.
    """
    from playwright.sync_api import sync_playwright

    acc = dict(accounts[acc_id])
    acc["id"] = acc_id

    pw = sync_playwright().start()
    try:
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human = Human(page)

        # انتظر قليلاً (10-45 ثانية عشوائي)
        time.sleep(delay)

        # فتح التغريدة
        tweet_url = f"https://x.com/i/web/status/{tid}"
        ok, status = open_tweet_with_retry(page, tweet_url, cfg)
        if not ok:
            log.warning(f"{acc_id} | فتح التغريدة {tid} فشل: {status}")
            return

        human.pause(1.5, 3.0)

        # البحث عن التغريدة على الصفحة
        articles = page.locator(TWEET_ARTICLE).all()
        if not articles:
            log.warning(f"{acc_id} | لم نجد التغريدة {tid} على الصفحة")
            return

        article = articles[0]
        state = load_state(acc_id)

        # Like
        try:
            if article.locator(UNLIKE_BTN).first.count() == 0:
                like_btn = article.locator(LIKE_BTN).first
                if like_btn.count() > 0:
                    like_btn.hover()
                    human.pause(0.5, 1.5)
                    like_btn.click()
                    state["done"]["likes"] = state["done"].get("likes", 0) + 1
                    log.info(f"{acc_id} | ❤️ لايك التغريدة {tid}")
                    human.pause(1.0, 2.5)
        except Exception as e:
            log.warning(f"{acc_id} | خطأ في like: {e}")

        # Retweet
        try:
            if article.locator(UNRETWEET_BTN).first.count() == 0:
                rt_btn = article.locator(RETWEET_BTN).first
                if rt_btn.count() > 0:
                    rt_btn.hover()
                    human.pause(0.5, 1.5)
                    rt_btn.click()
                    page.wait_for_timeout(1200)

                    # confirm retweet
                    confirm = selectors_first(page, RETWEET_CONFIRM_CANDIDATES)
                    if confirm:
                        human.pause(0.3, 0.8)
                        confirm.click()
                        state["done"]["retweets"] = state["done"].get("retweets", 0) + 1
                        log.info(f"{acc_id} | 🔄 ريتويت التغريدة {tid}")
                    human.pause(1.0, 2.5)
        except Exception as e:
            log.warning(f"{acc_id} | خطأ في retweet: {e}")

        # Comment
        try:
            reply_btn = article.locator('[data-testid="reply"]').first
            if reply_btn.count() > 0:
                reply_btn.hover()
                human.pause(0.5, 1.5)
                reply_btn.click()
                page.wait_for_timeout(1500)

                # ابحث عن textarea
                textarea = selectors_first(page, ['[data-testid="tweetTextarea"]'])
                if textarea:
                    textarea.hover()
                    human.pause(0.3, 0.8)
                    textarea.click()

                    # توليد تعليق
                    comment = build_comment_for(tweet_text, cfg=cfg)
                    if comment:
                        textarea.fill(comment)
                        human.pause(0.5, 1.2)

                        # ابحث عن send button
                        send_btn = page.locator('button[data-testid="Tweet"]').first
                        if send_btn.count() > 0 and send_btn.is_enabled():
                            send_btn.click()
                            page.wait_for_timeout(1200)
                            state["done"]["comments"] = state["done"].get("comments", 0) + 1
                            log.info(f"{acc_id} | 💬 تعليق على التغريدة {tid}")
        except Exception as e:
            log.warning(f"{acc_id} | خطأ في comment: {e}")

        save_state(acc_id, state)

    except Exception as e:
        log.error(f"{acc_id} | خطأ في التفاعل الفوري: {e}")
    finally:
        try:
            browser.close_ctx(pw, ctx, pw_owned)
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


_CRASH_COOLDOWN_S = 300  # مهلة الأمان بعد انهيار جلسة: لا نعيد المحاولة خلالها


def _is_fatal_session_error(msg):
    """أخطاء تعني أن المتصفح/العملية انهارت (لا فائدة من إعادة المحاولة فوراً)."""
    m = str(msg or "").lower()
    return any(s in m for s in (
        "has been closed", "asyncio loop", "sync api inside the asyncio loop",
        "task was destroyed", "connection closed",
    ))


def check_due_sessions(cfg, accounts, dry_run=False):
    """تشغيل جلسات الحسابات المستحقة + مهام المدير المستحقة.

    FIX-WEDGE-V1: بعد انهيار جلسة بخطأ قاتل ننتظر _CRASH_COOLDOWN_S ثانية
    بدلاً من إعادة المحاولة كل دقيقة (الانهيار المتكرر كان يكرر فشل النشر).
    """
    results = {"sessions": 0, "tasks": 0}
    for acc_id in enabled_accounts(accounts):
        state = ensure_daily_plan(acc_id, cfg)
        crash_at = float(state.get("last_session_crash") or 0)
        if crash_at and time.time() - crash_at < _CRASH_COOLDOWN_S:
            log.warning(f"{acc_id} | الجلسة السابقة انهارت - إعادة محاولة بعد {int(_CRASH_COOLDOWN_S - (time.time() - crash_at))} ثانية")
            continue
        due_tasks = monitor.due_tasks_for(acc_id)
        session_due = False
        for s in state["sessions"]:
            h, m = map(int, s["start"].split(":"))
            now = datetime.now()
            if (now.hour, now.minute) >= (h, m) and state["sessions_run"] < len(state["sessions"]):
                session_due = True
                break
        if session_due or due_tasks:
            run_session(acc_id, cfg, accounts, dry_run=dry_run)
            results["sessions"] += 1
        elif dry_run:
            run_session(acc_id, cfg, accounts, dry_run=True)
            results["sessions"] += 1
    return results