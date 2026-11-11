# -*- coding: utf-8 -*-
"""الروتين اليومي الذكي: نشاط عشوائي طبيعي + تنفيذ تفاعلات المدير."""
import random
import threading
import time
from datetime import datetime

from . import browser, monitor, resilience
from . import selectors
from .actions import (
    TWEET_ARTICLE, LIKE_BTN, UNLIKE_BTN, RETWEET_BTN, UNRETWEET_BTN,
    comment_on_tweet, like_tweet, retweet_tweet, dismiss_compose_dialog,
    already_replied, open_tweet, open_tweet_with_retry,
    scroll_home,
)
from .selectors import RETWEET_CONFIRM_CANDIDATES, TWEET_TEXT
from .comments import build_comment_for
from .config import (
    enabled_accounts, load_accounts, load_config,
    load_seen, load_state, locked_seen, save_state,
)
from .grow import crypto_activity
from .humanizer import (
    Human, is_rest_day, pick_daily_caps, pick_daily_follow_target,
    random_sessions, randint, randfloat,
)
from .logger import get_logger

log = get_logger("routine")


def _now():
    """الوقت الحالي بمنطقة التقارير (افتراضياً غزة) - نفس المرجع الذي يستخدمه الملخص اليومي.

    كانت الخطط تتبع ساعة الجهاز بينما الملخص يتبع غزة، فإن اختلفا جمع «يوم غزة» نشاط يومين من الخطط.
    """
    try:
        from . import ledger, tz
        return tz.now(ledger.tz_name())
    except Exception:
        return datetime.now()


def _today():
    return _now().strftime("%Y-%m-%d")


RELOGIN_RECHECK_S = 30 * 60      # حساب معلَّم «يحتاج إعادة دخول» يُعاد فحصه كل 30 دقيقة بدل أن يعلق
SESSION_MIN_GAP_S = 20 * 60      # أقل فاصل بين نهاية جلسة وبداية التالية
WARMUP_RETRY_S = 30 * 60         # إعادة محاولة الإحماء الفاشل بعد 30 دقيقة
WARMUP_MAX_FAILS = 3
OPEN_MAX_ATTEMPTS = 5            # حد محاولات فتح تغريدة المدير قبل التخلي عنها لهذا الحساب


def _hhmm_ts(hhmm, date_str):
    """طابع زمني لوقت HH:MM في يوم date_str بمنطقة التقارير (للخطط القديمة التي بلا ts)."""
    try:
        h, m = map(int, str(hhmm).split(":"))
        base = _now()
        d = datetime.strptime(date_str, "%Y-%m-%d") if date_str else base
        return base.replace(year=d.year, month=d.month, day=d.day, hour=h, minute=m,
                            second=0, microsecond=0).timestamp()
    except Exception:
        return None


def _session_ts(sess, state):
    ts = sess.get("ts")
    if ts:
        return float(ts)
    return _hhmm_ts(sess.get("start"), state.get("date"))


def _relogin_blocked(state):
    """هل نتخطى الحساب لأنه يحتاج إعادة دخول؟ (يُسمح بإعادة فحص كل RELOGIN_RECHECK_S)."""
    if not state.get("needs_relogin"):
        return False
    last = float(state.get("relogin_checked_at") or 0)
    return time.time() - last < RELOGIN_RECHECK_S


def _mark_relogin(acc_id, state):
    state["needs_relogin"] = True
    state["relogin_checked_at"] = time.time()
    save_state(acc_id, state)


def _clear_relogin(state):
    state["needs_relogin"] = False
    state.pop("relogin_checked_at", None)


def ensure_daily_plan(acc_id, cfg):
    """إنشاء خطة يومية عشوائية للحساب إن لم تكن موجودة لليوم."""
    state = load_state(acc_id)
    if state.get("date") == _today():
        return state
    rest_day = is_rest_day(cfg, state)
    caps = pick_daily_caps(cfg, state, rest_day=rest_day)
    caps["follows"] = pick_daily_follow_target(cfg, state, rest_day=rest_day)
    now = _now()
    warmup_at = _random_warmup_time(cfg)
    prev = state
    state = {
        "date": _today(),
        "caps": caps,
        "rest_day": rest_day,
        "done": {"likes": 0, "retweets": 0, "comments": 0, "follows": 0},
        "sessions": random_sessions(cfg, now=now),
        "sessions_run": 0,
        "warmup_at": warmup_at,
        "warmup_ts": _hhmm_ts(warmup_at, now.strftime("%Y-%m-%d")),
        "warmup_done": False,
        "warmup_fails": 0,
        # حالة الدخول ومهلة الانهيار تنتقل لليوم الجديد (كانت تُمسح فتضيع مهلة الأمان ويُخفى حساب منتهي الجلسة)
        "needs_relogin": bool(prev.get("needs_relogin")),
        "relogin_checked_at": prev.get("relogin_checked_at"),
        "last_session_crash": prev.get("last_session_crash"),
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


def _mark_manager_done(state, action):
    """عملية على تغريدة المدير: تُعدّ في done (للملخص) ويُرفع السقف بنفس القدر،
    حتى لا تستهلك تفاعلات المدير حصة التصفح العادي لبقية اليوم."""
    state["done"][action] = int(state["done"].get(action, 0)) + 1
    state["caps"][action] = int(state["caps"].get(action, 0)) + 1


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


# قفل لكل حساب: لا يُفتح بروفايل المتصفح نفسه في جلستين متزامنتين (جلسة عادية + مهمة عاجلة)
_busy = set()
_busy_lock = threading.Lock()
_retry_after = {}


def _acquire(acc_id):
    with _busy_lock:
        if acc_id in _busy:
            return False
        _busy.add(acc_id)
        return True


def _release(acc_id):
    with _busy_lock:
        _busy.discard(acc_id)


def run_session(acc_id, cfg, accounts, dry_run=False):
    """ترجع True إن بدأت الجلسة فعلاً، False إن كان الحساب مشغولاً."""
    if not _acquire(acc_id):
        log.info(f"{acc_id} | مشغول بجلسة أخرى - تخطي")
        return False
    try:
        _run_session(acc_id, cfg, accounts, dry_run)
        return True
    finally:
        _release(acc_id)


def _run_session(acc_id, cfg, accounts, dry_run=False):
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
        from ._pw_cleanup import cleanup_asyncio_loop

        cleanup_asyncio_loop()
        pw = sync_playwright().start()
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human.page = page

        ok_home, gs = resilience.goto_with_retry(page, "https://x.com/home", cfg)
        if not ok_home:
            log.warning(f"{acc_id} | تعذر فتح الصفحة الرئيسية ({gs}) - تُعاد المحاولة لاحقاً")
            return
        page.wait_for_timeout(3000)

        if browser.is_login_wall(page) or not browser.is_logged_in(page):
            _mark_relogin(acc_id, state)
            log.warning(f"{acc_id} | الجلسة منتهية - تحتاج إعادة تسجيل دخول: python main.py login {acc_id}")
            return
        _clear_relogin(state)
        save_state(acc_id, state)

        minutes = cfg["behavior"].get("session_minutes", [15, 35])
        session_secs = randint(minutes[0], minutes[1]) * 60
        session_start = time.time()

        # 1) مهام المدير أولاً: لا تنتظر التصفح ولا يستهلك التصفح حصتها
        _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=acc)
        save_state(acc_id, state)

        # 2) تصفح عشوائي وتفاعلات طبيعية
        _browse_and_interact(page, acc_id, cfg, state, human, session_secs, session_start, acc=acc)
        save_state(acc_id, state)

        # 3) نشاط الكريبتو اليومي: تصفح بحث + متابعات بطيئة للمهتمين (يتوقف فوراً عند وصول مهمة عاجلة)
        remaining = max(0, int(session_secs - (time.time() - session_start)))
        if not monitor.priority_pending(acc_id) and state.get("date") == _today():
            crypto_activity(page, acc_id, cfg, state, human, max(120, min(remaining, 900)),
                            should_stop=lambda: monitor.priority_pending(acc_id))
        save_state(acc_id, state)

        # 4) مهام مدير استحقت أثناء الجلسة
        _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=acc)

        state["sessions_run"] += 1
        state["last_session_end"] = datetime.now().isoformat(timespec="seconds")
        state["last_session_end_ts"] = time.time()
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
            pw = None


def run_warmup(acc_id, cfg, accounts, dry_run=False):
    if not _acquire(acc_id):
        log.info(f"{acc_id} | مشغول بجلسة أخرى - تأجيل الإحماء")
        return {"account": acc_id, "skipped": "busy"}
    try:
        return _run_warmup(acc_id, cfg, accounts, dry_run)
    finally:
        _release(acc_id)


def _run_warmup(acc_id, cfg, accounts, dry_run=False):
    """جلسة إحماء خفيفة: فتح الحساب والتفاعل مع كريبتو حتى لو لم ينزل المدير شيئاً."""
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
        from ._pw_cleanup import cleanup_asyncio_loop

        cleanup_asyncio_loop()
        pw = sync_playwright().start()
        _, ctx, page, pw_owned = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human.page = page

        ok_home, gs = resilience.goto_with_retry(page, "https://x.com/home", cfg)
        if not ok_home:
            log.warning(f"{acc_id} | تعذر فتح الصفحة الرئيسية أثناء الإحماء ({gs})")
            _warmup_failed(acc_id, state, gs)
            return {"acc_id": acc_id, "status": "network-error"}
        page.wait_for_timeout(3000)

        if browser.is_login_wall(page) or not browser.is_logged_in(page):
            _mark_relogin(acc_id, state)
            log.warning(f"{acc_id} | الإحماء متوقف - تحتاج إعادة تسجيل دخول: python main.py login {acc_id}")
            return {"acc_id": acc_id, "status": "relogin"}
        _clear_relogin(state)

        minutes = cfg.get("warmup", {}).get("session_minutes", [8, 15])
        budget_s = randint(minutes[0], minutes[1]) * 60

        scroll_home(page, human, steps=randint(1, 2))
        human.mouse_wander(page)
        crypto_activity(page, acc_id, cfg, state, human, budget_s,
                        should_stop=lambda: monitor.priority_pending(acc_id))

        state["last_session_end"] = datetime.now().isoformat(timespec="seconds")
        state["warmup_done"] = True
        state.pop("warmup_retry_at", None)
        save_state(acc_id, state)
        log.info(f"{acc_id} | نهاية الإحماء | لايك {state['done']['likes']} / متابعات {state['done']['follows']}")
        return {"acc_id": acc_id, "status": "ok"}
    except Exception as e:
        log.error(f"{acc_id} | خطأ في الإحماء: {e}")
        _warmup_failed(acc_id, state, e)
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
            pw = None


def _warmup_failed(acc_id, state, why):
    """إحماء فاشل: مهلة قبل إعادة المحاولة، وبعد WARMUP_MAX_FAILS يُعتبر منتهياً لليوم (بدل فتح متصفح كل دقيقة)."""
    try:
        state["warmup_fails"] = int(state.get("warmup_fails", 0) or 0) + 1
        if state["warmup_fails"] >= WARMUP_MAX_FAILS:
            state["warmup_done"] = True
            log.warning(f"{acc_id} | فشل الإحماء {state['warmup_fails']} مرات ({str(why)[:80]}) - إلغاء إحماء اليوم")
        else:
            state["warmup_retry_at"] = time.time() + WARMUP_RETRY_S
            log.warning(f"{acc_id} | فشل الإحماء ({str(why)[:80]}) - إعادة المحاولة بعد {WARMUP_RETRY_S // 60} دقيقة")
        save_state(acc_id, state)
    except Exception:
        pass


def warmup_due(state, cfg=None):
    """هل حان موعد إحماء اليوم لهذا الحساب؟ (لا إحماء في يوم الراحة ولا قبل انتهاء مهلة الفشل)."""
    if not state or not state.get("warmup_at"):
        return False
    if state.get("warmup_done"):
        return False
    if state.get("rest_day"):
        return False
    if state.get("needs_relogin"):
        return False   # check_due_sessions يعيد فحص الدخول كل 30 دقيقة ويزيل العلامة
    if time.time() < float(state.get("warmup_retry_at") or 0):
        return False
    ts = state.get("warmup_ts")
    if ts:
        return time.time() >= float(ts)
    try:
        h, m = map(int, state["warmup_at"].split(":"))
    except Exception:
        return False
    now = _now()
    return (now.hour, now.minute) >= (h, m)


def check_due_warmups(cfg, accounts, dry_run=False):
    """تشغيل الإحماء اليومي للحسابات التي حان موعدها."""
    results = {"warmups": 0}
    if not cfg.get("warmup", {}).get("enabled", True):
        return results
    for acc_id in enabled_accounts(accounts):
        state = ensure_daily_plan(acc_id, cfg)
        if warmup_due(state, cfg):
            res = run_warmup(acc_id, cfg, accounts, dry_run=dry_run)
            results.setdefault("details", []).append(res)
            if not (isinstance(res, dict) and res.get("skipped")):
                results["warmups"] += 1
    return results


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


def _like_one_random(page, articles, acc_id, state, human):
    """لايك لتغريدة عشوائية واحدة ضمن الحد اليومي."""
    for art in random.sample(articles, min(len(articles), 5)):
        try:
            if art.locator(UNLIKE_BTN).first.count() > 0:
                continue
            like_btn = art.locator(LIKE_BTN).first
            if like_btn.count() == 0 or not like_btn.is_visible():
                continue
            like_btn.hover()
            human.pause(0.7, 2.0)
            like_btn.click()
            page.wait_for_timeout(1200)
            _mark_done(state, "likes")
            log.info(f"{acc_id} | لايك عشوائي #{state['done']['likes']}")
            return True
        except Exception:
            continue
    return False


def _retweet_one_random(page, articles, acc_id, state, human):
    """ريتويت لتغريدة عشوائية واحدة ضمن الحد اليومي."""
    for art in random.sample(articles, min(len(articles), 5)):
        try:
            if art.locator(UNRETWEET_BTN).first.count() > 0:
                continue
            rt = art.locator(RETWEET_BTN).first
            if rt.count() == 0 or not rt.is_visible():
                continue
            rt.hover()
            human.pause(0.8, 2.2)
            rt.click()
            page.wait_for_timeout(1200)
            confirm = selectors.first(page, RETWEET_CONFIRM_CANDIDATES)
            if confirm is not None:
                confirm.click()
                page.wait_for_timeout(1500)
                if "/compose/" in (page.url or ""):
                    dismiss_compose_dialog(page, human)
                    log.warning(f"{acc_id} | فُتح محرر الاقتباس بالخطأ - أُغلق بدون حفظ")
                    return False
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
    dismiss_compose_dialog(page, human)  # تنظيف أي محرر/حوار من جولة سابقة
    while time.time() - session_start < session_secs:
        if monitor.priority_pending(acc_id):
            log.info(f"{acc_id} | مهمة عاجلة مستحقة - إنهاء التصفح مبكراً")
            break
        if state.get("date") != _today():
            log.info(f"{acc_id} | بدأ يوم جديد - إنهاء الجلسة (خطة اليوم الجديد تُنشأ في الدورة التالية)")
            break
        save_state(acc_id, state)
        if (not _can_do(state, "likes") and not _can_do(state, "retweets")
                and not _can_do(state, "comments")):
            break
        if "/compose/" in (page.url or ""):
            dismiss_compose_dialog(page, human)
            if "/compose/" in (page.url or ""):
                log.warning(f"{acc_id} | محرر الاقتباس عالق - تخطي جولة")
                human.pause(4, 8)
                continue
        human.pause(2.0, 6.0)
        page.mouse.wheel(0, random.randint(500, 1500))
        scrolls += 1
        page.wait_for_timeout(1000)

        if scrolls % 3 == 0:
            human.pause(4.0, 12.0)

        articles = page.locator(TWEET_ARTICLE).all()
        if not articles:
            human.pause(3, 6)
            continue

        action = _pick_browse_action(cfg, state)
        if action is None:
            break
        if action == last_action and random.random() < 0.75:
            other = [a for a in ("like", "retweet", "comment")
                     if a != last_action and _can_do(state, STATE_KEY[a])]
            if other:
                action = random.choice(other)
        last_action = action

        if action == "like":
            _like_one_random(page, articles, acc_id, state, human)
        elif action == "retweet":
            _retweet_one_random(page, articles, acc_id, state, human)
        else:
            try:
                art = random.choice(articles)
                link = art.locator('a[href*="/status/"]').first
                if link.count() > 0:
                    href = link.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = "https://x.com" + href
                    if _open_and_comment(page, href, acc_id, state, human, cfg=cfg, en_ratio=en_ratio):
                        log.info(f"{acc_id} | تعليق عشوائي #{state['done']['comments']}")
                try:
                    page.goto("https://x.com/home", timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            except Exception:
                pass
        save_state(acc_id, state)


def _open_and_comment(page, url, acc_id, state, human, cfg=None, en_ratio=None):
    """فتح تغريدة والتعليق عليها بتعليق سياقي ذكي (من نص التغريدة)."""
    ok, status = open_tweet(page, url)
    if not ok:
        return False
    tweet_text = _extract_open_tweet_text(page)
    used = {c for t, e in load_seen().items() for c in e.get("comments_used", [])}
    text = build_comment_for(tweet_text, used_texts=used, cfg=cfg, en_ratio=en_ratio, acc_id=acc_id)
    if not text:
        log.warning(f"{acc_id} | لا يوجد تعليق غير مكرر متاح - تخطّي التعليق")
        return False
        
    # إعادة استخدام صفحة كاملة (page) لأن الحماية الفعلية تم نقلها لـ actions.py
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


def _open_failed(cfg, task, acc_id, status):
    """فشل فتح تغريدة المدير: يُحسب لكل عملية متبقية، وبعد OPEN_MAX_ATTEMPTS يُتخلّى عنها لهذا الحساب
    (كانت المحاولة تتكرر في كل جلسة بلا حد)."""
    monitor.record_tweet_open_attempt(task["tweet_id"])
    for action in task["remaining"]:
        monitor.record_action_attempt(task["tweet_id"], acc_id, action)
        if _action_attempts(task["tweet_id"], acc_id, action) >= OPEN_MAX_ATTEMPTS:
            monitor.give_up_action(task["tweet_id"], acc_id, action)
            log.warning(f"{acc_id} | تعذّر فتح {task['tweet_id']} {OPEN_MAX_ATTEMPTS} مرات - تخطّي {action} نهائياً")


def _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=None, priority=False):
    """تنفيذ كل مهام المدير المستحقة (مع معالجة الحذف وفشل العملية بالتخطي).

    تغريدات المدير لا تتقيد بحصة اليوم ولا بيوم الراحة: هي سبب وجود الأداة. تُعدّ في done
    ويُرفع السقف بنفس القدر، فلا تأكل حصة التصفح العادي.
    """
    en_ratio = _account_en_ratio(acc)
    seen = load_seen()
    tasks = monitor.due_tasks_for(acc_id, seen, priority=priority)
    if not tasks:
        return 0
    executed = 0
    fast = cfg.get("telegram", {}).get("action_delay_s", [2, 6])
    if priority:
        import copy as _copy
        cfg = _copy.deepcopy(cfg)
        cfg.setdefault("comments", {}).setdefault("ai", {})["min_interval_s"] = 0
    for task in tasks:
        if priority is False and monitor.priority_pending(acc_id):
            log.info(f"{acc_id} | مهمة عاجلة وصلت - تأجيل باقي المهام العادية")
            break
        ok, status = open_tweet_with_retry(page, task["url"], cfg)
        if not ok:
            if status == "deleted":
                monitor.mark_tweet_deleted(task["tweet_id"])
                log.warning(f"{acc_id} | تغريدة المدير {task['tweet_id']} محذوفة - أُزيلت من الجدول")
                continue
            # جدار دخول على صفحة تغريدة واحدة لا يعني أن جلسة الحساب انتهت (الدخول فُحص في بداية الجلسة)
            log.warning(f"{acc_id} | تعذر فتح تغريدة المدير {task['tweet_id']}: {status}")
            if priority:
                for action in task["remaining"]:
                    _handle_action_failure(cfg, task["tweet_id"], acc_id, action, str(status))
            else:
                _open_failed(cfg, task, acc_id, status)
            continue
        if priority:
            human.pause(1.0, 3.0)
        else:
            human.pause(3.0, 8.0)
            page.mouse.wheel(0, random.randint(100, 400))
            human.pause(1.5, 4.0)
        for action in task["remaining"]:
            key = _cap_key(action)
            if action == "like":
                res, msg = like_tweet(page, human)
            elif action == "retweet":
                res, msg = retweet_tweet(page, human)
            elif action == "comment":
                if already_replied(page, (acc or {}).get("handle", "")):
                    monitor.mark_done(task["tweet_id"], acc_id, action)
                    log.info(f"{acc_id} | سبق أن علّق على {task['tweet_id']} - تخطّي")
                    continue
                used = _comments_used_for(task["tweet_id"])
                tweet_text = task.get("text") or ""
                if not tweet_text:
                    tweet_text = _extract_open_tweet_text(page)
                    if tweet_text:
                        task["text"] = tweet_text
                        monitor.set_tweet_text(task["tweet_id"], tweet_text)
                text = build_comment_for(tweet_text, used_texts=used, cfg=cfg, en_ratio=en_ratio, acc_id=acc_id)
                if not text:
                    log.warning(f"{acc_id} | لا يوجد تعليق غير مكرر لـ {task['tweet_id']} - تخطّي التعليق")
                    monitor.give_up_action(task["tweet_id"], acc_id, "comment")
                    continue
                res, msg = comment_on_tweet(page, text, human)
                if res:
                    _record_comment(task["url"], text, task["tweet_id"])
            else:
                continue
            if res:
                monitor.mark_done(task["tweet_id"], acc_id, action)
                _mark_manager_done(state, key)
                save_state(acc_id, state)
                executed += 1
                log.info(f"{acc_id} | ✓ {action} على تغريدة المدير {task['tweet_id']}")
            else:
                _handle_action_failure(cfg, task["tweet_id"], acc_id, action, msg)
            if priority:
                human.pause(float(fast[0]), float(fast[1]))
            else:
                human.pause(randint(*cfg["behavior"]["min_action_delay_s"]),
                            randint(*cfg["behavior"]["max_action_delay_s"]))
    return executed


def run_priority_session(acc_id, cfg, accounts, dry_run=False, priority=True):
    """جلسة قصيرة: تفتح الحساب، تتأكد من تسجيل الدخول، وتنفّذ مهامه فقط (عاجلة أو عادية مستحقة)."""
    if not _acquire(acc_id):
        return False
    pw = ctx = None
    try:
        acc = dict(accounts[acc_id])
        acc["id"] = acc_id
        state = ensure_daily_plan(acc_id, cfg)
        human = Human()
        log.info(f"{'⚡' if priority else '▶'} {acc_id} ({acc.get('handle','')}) | تنفيذ مهام {'عاجلة' if priority else 'المدير'}")
        if dry_run:
            return True
        from playwright.sync_api import sync_playwright
        from ._pw_cleanup import cleanup_asyncio_loop
        cleanup_asyncio_loop()
        pw = sync_playwright().start()
        _, ctx, page, _ = browser.launch_account(acc, cfg, pw=pw, owned_pw=False)
        human.page = page
        # تحقق الدخول قبل التنفيذ: جلسة منتهية كانت تُحرق كل المحاولات وتُعلَّم العمليات «فشل نهائي»
        ok_home, gs = resilience.goto_with_retry(page, "https://x.com/home", cfg)
        if not ok_home:
            log.warning(f"{acc_id} | تعذّر فتح الرئيسية قبل المهام ({gs}) - إعادة لاحقاً")
            return False
        page.wait_for_timeout(2500)
        if browser.is_login_wall(page) or not browser.is_logged_in(page):
            _mark_relogin(acc_id, state)
            log.warning(f"{acc_id} | الجلسة منتهية - المهام تنتظر إعادة الدخول: python main.py login {acc_id}")
            return False
        if state.get("needs_relogin"):
            _clear_relogin(state)
            save_state(acc_id, state)
        _run_due_manager_tasks(page, acc_id, cfg, state, human, acc=acc, priority=priority)
        state["last_task_session_ts"] = time.time()
        save_state(acc_id, state)
        return True
    except Exception as e:
        log.error(f"{acc_id} | خطأ في جلسة المهام: {e}")
        return False
    finally:
        for closer in ((lambda: ctx.close()) if ctx is not None else None,
                       (lambda: pw.stop()) if pw is not None else None):
            if closer:
                try:
                    closer()
                except Exception:
                    pass
        _release(acc_id)


def check_priority_tasks(cfg, accounts, dry_run=False, should_stop=lambda: False):
    """ينفّذ المهام العاجلة المستحقة حساباً بعد حساب."""
    ran = 0
    for acc_id in enabled_accounts(accounts):
        if should_stop():
            break
        if time.time() < _retry_after.get(acc_id, 0):
            continue
        if not monitor.due_tasks_for(acc_id, priority=True):
            continue
        if _relogin_blocked(load_state(acc_id)):
            continue
        if run_priority_session(acc_id, cfg, accounts, dry_run=dry_run):
            ran += 1
        if monitor.due_tasks_for(acc_id, priority=True):
            _retry_after[acc_id] = time.time() + 30
    return ran


def _comments_used_for(tid):
    seen = load_seen()
    return seen.get(tid, {}).get("comments_used", [])


@locked_seen
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
    """محاكاة الجلسة بدون فتح متصفح."""
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


_CRASH_COOLDOWN_S = 300


def _is_fatal_session_error(msg):
    m = str(msg or "").lower()
    return any(s in m for s in (
        "has been closed", "asyncio loop", "sync api inside the asyncio loop",
        "task was destroyed", "connection closed",
    ))


def _next_session_due(state, now_ts=None):
    """هل حان موعد الجلسة التالية (رقم sessions_run) فعلاً؟

    كان الشرط يكتفي بأن *أي* جلسة فات موعدها، فتُشغَّل جلسات اليوم الثلاث متتالية بعد أول موعد
    وتُستهلك حصة اليوم كلها صباحاً. الآن: جلسة واحدة لكل موعد، مع فاصل أدنى بعد الجلسة السابقة،
    والمواعيد الفائتة (البرنامج كان مغلقاً) تُدمج في جلسة واحدة بدل تشغيلها كلها.
    """
    now_ts = now_ts or time.time()
    sessions = state.get("sessions") or []
    idx = int(state.get("sessions_run", 0) or 0)
    if idx >= len(sessions):
        return False
    passed = [i for i, s in enumerate(sessions) if (_session_ts(s, state) or 0) <= now_ts]
    if not passed or passed[-1] < idx:
        return False
    if passed[-1] > idx:
        log.info(f"تخطّي {passed[-1] - idx} موعد جلسة فائت (يُدمج في جلسة واحدة)")
        state["sessions_run"] = passed[-1]
    last_end = float(state.get("last_session_end_ts") or 0)
    return now_ts - last_end >= SESSION_MIN_GAP_S


def check_due_sessions(cfg, accounts, dry_run=False):
    """تشغيل جلسات الحسابات المستحقة + مهام المدير المستحقة.

    مهام مدير مستحقة بلا موعد جلسة => جلسة مهام قصيرة فقط (كانت تفتح جلسة تصفح كاملة 25-45 دقيقة).
    """
    results = {"sessions": 0, "tasks": 0}
    for acc_id in enabled_accounts(accounts):
        state = ensure_daily_plan(acc_id, cfg)
        crash_at = float(state.get("last_session_crash") or 0)
        if crash_at and time.time() - crash_at < _CRASH_COOLDOWN_S:
            log.warning(f"{acc_id} | الجلسة السابقة انهارت - إعادة محاولة بعد {int(_CRASH_COOLDOWN_S - (time.time() - crash_at))} ثانية")
            continue
        if _relogin_blocked(state):
            continue
        due_tasks = monitor.due_tasks_for(acc_id)
        before = state.get("sessions_run")
        session_due = _next_session_due(state)
        if state.get("sessions_run") != before:
            save_state(acc_id, state)
        if state.get("needs_relogin") and not session_due and not dry_run:
            # إعادة فحص دورية بجلسة قصيرة: إن عاد الدخول صالحاً تُزال العلامة فوراً وتُنفّذ المهام
            log.info(f"{acc_id} | إعادة فحص تسجيل الدخول")
            run_priority_session(acc_id, cfg, accounts, priority=False)
            continue
        if session_due:
            if run_session(acc_id, cfg, accounts, dry_run=dry_run):
                results["sessions"] += 1
        elif due_tasks:
            if run_priority_session(acc_id, cfg, accounts, dry_run=dry_run, priority=False):
                results["tasks"] += 1
        elif dry_run:
            run_session(acc_id, cfg, accounts, dry_run=True)
            results["sessions"] += 1
    return results
