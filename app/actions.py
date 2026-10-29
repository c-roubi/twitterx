# -*- coding: utf-8 -*-
"""Twitter interaction operations: like, retweet, comment, fetch tweets."""
import random
import re
import time

from . import resilience, selectors
from .humanizer import Human
from .logger import get_logger
from .selectors import (
    TWEET_ARTICLE, TWEET_ARTICLE_CANDIDATES,
    LIKE_BTN, UNLIKE_BTN, RETWEET_BTN, UNRETWEET_BTN,
    RETWEET_CONFIRM, REPLY_BTN, TEXTAREA, SEND_BTN, DRAFT_DISCARD_BTN_CANDIDATES,
    LOGIN_BTN, LOGIN_WALL, FOLLOW_BTN, UNFOLLOW_BTN, USER_CELL,
    PROFILE_BIO, VERIFIED_ICON, FOLLOWING_LINK, FOLLOWERS_LINK,
    TWEET_TEXT, STATUS_LINK,
    LIKE_BTN_CANDIDATES, UNLIKE_BTN_CANDIDATES,
    RETWEET_BTN_CANDIDATES, UNRETWEET_BTN_CANDIDATES, RETWEET_CONFIRM_CANDIDATES,
    REPLY_BTN_CANDIDATES, TEXTAREA_CANDIDATES, SEND_BTN_CANDIDATES,
    FOLLOW_BTN_CANDIDATES, USER_CELL_CANDIDATES,
    PROFILE_BIO_CANDIDATES, VERIFIED_ICON_CANDIDATES,
    FOLLOWING_LINK_CANDIDATES, FOLLOWERS_LINK_CANDIDATES,
    TWEET_TEXT_CANDIDATES,
    follow_handle_candidates, unfollow_handle_candidates,
)

MAX_ATTEMPTS = 2

log = get_logger("actions")


def _err_status(e):
    """Convert exception to classifiable status text (network/timeout/other)."""
    kind = resilience.classify_error(e)
    if kind == "timeout":
        return "timeout"
    if kind == "network":
        return "network-error"
    return f"error:{e}"[:120]


def _detect_oops_error(page):
    """Detect Oops error page from Twitter (usually contains 'Oops' or 'Something went wrong')."""
    if not page:
        return False
    try:
        # Check page content for common errors
        content = page.content() or ""
        if "Oops" in content or "Something went wrong" in content or "error" in content.lower():
            # Ensure it's actually an error page (not regular content)
            if page.locator("text=/Oops|Something went wrong/i").count() > 0:
                return True
    except Exception:
        pass
    return False


def _recover_from_error(page, human, cfg=None, max_retries=3):
    """Recover from error page: reload and retry."""
    for attempt in range(max_retries):
        try:
            if _detect_oops_error(page):
                log.warning(f"Oops error detected - reload page (attempt {attempt + 1}/{max_retries})")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                if human:
                    human.pause(1.0, 3.0)
                if not _detect_oops_error(page):
                    return True  # Successfully recovered
            else:
                return True
        except Exception as e:
            log.warning(f"Error while recovering from error: {e}")
            if attempt < max_retries - 1:
                page.wait_for_timeout(1500)
    return False


def _safe_goto(page, url, cfg=None, human=None, timeout=45000):
    """Open URL with safety checks and error recovery."""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            # Check for Oops error
            if _detect_oops_error(page):
                if attempt < max_attempts - 1:
                    log.warning(f"Oops error after goto - retry (attempt {attempt + 2}/{max_attempts})")
                    if human:
                        human.pause(2.0, 4.0)
                    continue
                else:
                    return False, "oops-error"
            return True, "ok"
        except Exception as e:
            if attempt < max_attempts - 1:
                log.warning(f"Error in goto {url}: {e} - retrying")
                if human:
                    human.pause(1.5, 3.0)
            else:
                return False, _err_status(e)
    return False, "max-retries"


def dismiss_compose_dialog(page, human):
    """Safe exit from quote composer/draft save dialog (Discard only, never Save).

    Closes any 'Save post?'/'Discard' dialog and exits /compose page if stuck.
    Returns True if handled something, else False.
    """
    try:
        for _ in range(3):
            sheets = page.locator('[data-testid="confirmationSheet"]')
            if sheets.count() > 0:
                discard = selectors.first(page, DRAFT_DISCARD_BTN_CANDIDATES)
                if discard is not None:
                    try:
                        discard.hover()
                    except Exception:
                        pass
                    human.pause(0.4, 1.2)
                    discard.click()
                    page.wait_for_timeout(1200)
                    if sheets.count() == 0:
                        return True
                    continue
            if "/compose/" in (page.url or ""):
                # Composer open with no dialog: Escape closes it or shows save dialog we reject
                page.keyboard.press("Escape")
                page.wait_for_timeout(900)
                continue
            break
        # لم تزل الورقة أو عالقون في /compose: غادر الصفحة
        try:
            if page.locator('[data-testid="confirmationSheet"]').count() > 0 or "/compose/" in (page.url or ""):
                # ✅ تم التعديل: إضافة wait_until لتجنب Timeout
                page.goto("https://x.com/home", timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False
# FIX-SAVEPOST-V2-DISMISS


def dismiss_all_modals(page, human):
    """تعمل dismiss تلقائي لأي modal/popup على الصفحة (Views, alerts, إلخ)."""
    try:
        # البحث عن زرار Dismiss
        dismiss_btns = page.locator('button:has-text("Dismiss"), button:contains("Dismiss")')
        if dismiss_btns.count() > 0:
            try:
                human.pause(0.2, 0.5)
                dismiss_btns.first.click()
                page.wait_for_timeout(600)
                return True
            except Exception:
                pass

        # محاولة X button للـ close
        close_btn = page.locator('button[aria-label="Close"]')
        if close_btn.count() > 0:
            try:
                human.pause(0.2, 0.5)
                close_btn.first.click()
                page.wait_for_timeout(600)
                return True
            except Exception:
                pass
    except Exception:
        pass

    return False


def open_tweet(page, url):
    """فتح تغريدة. ترجع (ok, status) حيث status من: ok/deleted/login-wall/oops-error/network-error/timeout/error."""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            if attempt < max_attempts - 1:
                log.warning(f"خطأ في فتح التغريدة (محاولة {attempt + 2}): {_err_status(e)}")
                page.wait_for_timeout(1500)
                continue
            return False, _err_status(e)

        page.wait_for_timeout(2500)

        # تشيك الأخطاء الشائعة
        if _detect_oops_error(page):
            if attempt < max_attempts - 1:
                log.warning(f"Oops error عند فتح التغريدة - reload (محاولة {attempt + 2})")
                page.wait_for_timeout(1500)
                continue
            return False, "oops-error"

        # انتظار تحميل المقال
        for _ in range(20):
            if selectors.has(page, TWEET_ARTICLE_CANDIDATES):
                return True, "ok"
            if selectors.has(page, LOGIN_WALL):
                return False, "login-wall"
            if resilience.is_tweet_deleted(page):
                return False, "deleted"
            page.wait_for_timeout(1000)

        if resilience.is_tweet_deleted(page):
            return False, "deleted"
        return True, "ok"

    return False, "max-retries"


def open_tweet_with_retry(page, url, cfg):
    """فتح تغريدة مع إعادة محاولة عند أخطاء الشبكة/المهلة."""
    r = int(cfg.get("resilience", {}).get("network_retries", 2))
    base = float(cfg.get("resilience", {}).get("retry_base_delay_s", 5.0))
    last = "error"
    for i in range(r + 1):
        ok, status = open_tweet(page, url)
        if ok:
            return True, "ok"
        if status in ("deleted", "login-wall"):
            return False, status
        if status in ("network-error", "timeout"):
            if i < r:
                d = resilience.backoff_delay(i, base)
                log.warning(
                    f"مشكلة شبكة في فتح التغريدة {url} - إعادة المحاولة ({i + 2}/{r + 1}) بعد {d:.1f} ثانية"
                )
                time.sleep(d)
                continue
        last = status
    return False, last


def like_tweet(page, human):
    """لايك للتغريدة المفتوحة حالياً."""
    dismiss_compose_dialog(page, human)  # خروج آمن من أي مسودة اقتباس سابقة
    for attempt in range(MAX_ATTEMPTS):
        try:
            if selectors.has(page, UNLIKE_BTN_CANDIDATES):
                return True, "already-liked"
            btn = selectors.first(page, LIKE_BTN_CANDIDATES)
            if btn is None:
                return False, "no-like-button"
            btn.hover()
            human.pause(0.8, 2.2)
            btn.click()
            page.wait_for_timeout(1800)
            if selectors.has(page, UNLIKE_BTN_CANDIDATES):
                return True, "liked"
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1:
                return False, _err_status(e)
            human.pause(2, 4)
    return False, "unconfirmed"


def retweet_tweet(page, human):
    """ريتويت/إعادة نشر للتغريدة المفتوحة حالياً."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            # لو كنا عالقين في محرر اقتباس/مسودة من جولة سابقة: اخرج أولاً
            dismiss_compose_dialog(page, human)
            if "/compose/" in (page.url or ""):
                return False, "in-compose"
            if selectors.has(page, UNRETWEET_BTN_CANDIDATES):
                return True, "already-retweeted"
            btn = selectors.first(page, RETWEET_BTN_CANDIDATES)
            if btn is None:
                return False, "no-retweet-button"
            btn.hover()
            human.pause(0.8, 2.5)
            btn.click()
            # انتظار ظهور القائمة المنبثقة (حتى 3 ثوانٍ)
            confirm = None
            for _ in range(6):
                confirm = selectors.first(page, RETWEET_CONFIRM_CANDIDATES)
                if confirm is not None:
                    break
                page.wait_for_timeout(500)
            if confirm is None:
                try:
                    for mi in page.locator('[role="menuitem"]').all()[:3]:
                        txt = (mi.inner_text() or "").lower()
                        if any(k in txt for k in ("repost", "retweet", "إعادة نشر", "إعادة تغريد")):
                            confirm = mi
                            break
                except Exception:
                    pass
            if confirm is None:
                page.keyboard.press("Escape")
                return False, "menu-not-found"
            confirm.hover()
            human.pause(0.5, 1.5)
            confirm.click()
            page.wait_for_timeout(2200)
            # إن فُتح محرر اقتباس بالخطأ: نرفض المسودة فوراً ونعيد المحاولة
            if "/compose/" in (page.url or ""):
                dismiss_compose_dialog(page, human)
                if attempt < MAX_ATTEMPTS - 1:
                    human.pause(2, 4)
                    continue
                return False, "landed-in-compose"
            if selectors.has(page, UNRETWEET_BTN_CANDIDATES):
                return True, "retweeted"
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1:
                return False, _err_status(e)
            human.pause(2, 4)
    return False, "unconfirmed"


def _composer_text(page, ta):
    """النص الموجود حالياً في محرر التعليق (أو فارغ عند أي خطأ)."""
    try:
        return (ta.inner_text() or "").strip()
    except Exception:
        return ""


def _norm_spaces(t):
    """تطبيع المسافات للمقارنة (X قد يحوّل فواصل الأسطر/المسافات)."""
    return " ".join((t or "").split())


def _type_text_verified(page, ta, text):
    """كتابة نص التعليق والتحقق أنه استقر فعلًا في الصندوق (وليس ضغطات ضائعة).

    المراحل: حرفاً حرفاً (مرتين مع مسح مسبق) ← insert_text دفعة واحدة ← fill().
    يرجع النص الموجود فعلياً في المحرر، أو سلسلة فارغة إن لم تستقر الكتابة.
    """
    if not text:
        return ""
    target = _norm_spaces(text)
    # 1) كتابة حرفاً حرفاً بعد تركيز مؤكد وتفريغ المحرر
    for _ in range(2):
        try:
            ta.click()
            page.wait_for_timeout(450)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(250)
            for ch in text:
                page.keyboard.type(ch, delay=random.randint(25, 90))
            page.wait_for_timeout(600)
            cur = _composer_text(page, ta)
            if _norm_spaces(cur) == target:
                return cur
        except Exception:
            pass
    # 2) محاكاة لصق النص دفعة واحدة (أكثر ثباتاً حتى لو تأخر التركيز)
    try:
        ta.click()
        page.wait_for_timeout(400)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(text)
        page.wait_for_timeout(500)
        cur = _composer_text(page, ta)
        if _norm_spaces(cur) == target:
            return cur
    except Exception:
        pass
    # 3) fill(): يضبط محتوى contenteditable مباشرة دون محاكاة لوحة مفاتيح
    try:
        ta.fill(text)
        page.wait_for_timeout(450)
        cur = _composer_text(page, ta)
        return cur if _norm_spaces(cur) == target else cur
    except Exception:
        return ""


def _wait_send_enabled(page, timeout_ms=4000):
    """انتظار زر النشر حتى يظهر ويكون مفعّلاً (حتى timeout_ms)."""
    deadline = time.time() + timeout_ms / 1000.0
    send = None
    while time.time() < deadline:
        send = selectors.first(page, SEND_BTN_CANDIDATES)
        if send is not None:
            try:
                if send.is_enabled():
                    return send
            except Exception:
                pass
        page.wait_for_timeout(250)
    return send


def _composer_cleared(page, ta, timeout_ms=6000):
    """هل أُرسل التعليق فعلاً؟ (فارغ المحرر أو اختفى من الصفحة)."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            cur = (ta.inner_text() or "").strip()
        except Exception:
            return True  # المحرر اختفى = أُرسل التعليق
        if cur == "":
            return True
        page.wait_for_timeout(300)
    return False


def comment_on_tweet(page, text, human):
    """تعليق نصي على التغريدة المفتوحة حالياً (مع تنظيف أي مسودة عالقة).

    FIX-SENDBTN-V2-COMMENT: الكتابة مُتحققٌ منها (fallback: insert_text/fill)،
    وزر النشر يُنتظر حتى يُفعَّل حتى 4 ثوانٍ، وعند فشل النقر نستخدم اختصار
    Ctrl+Enter الرسمي في X. التحقق من النجاح = محرر فارغ/مختفٍ.
    """
    dismiss_compose_dialog(page, human)  # خروج آمن من أي مسودة/حوار سابق
    for attempt in range(MAX_ATTEMPTS):
        try:
            # تمرير خفيف لإظهار منطقة الرد على صفحة التغريدة
            try:
                page.mouse.wheel(0, 300)
                page.wait_for_timeout(600)
            except Exception:
                pass
            ta = selectors.first(page, TEXTAREA_CANDIDATES)
            if ta is None:
                reply = selectors.first(page, REPLY_BTN_CANDIDATES)
                if reply is not None:
                    reply.hover()
                    human.pause(0.5, 1.6)
                    reply.click()
                    for _w in range(8):
                        ta = selectors.first(page, TEXTAREA_CANDIDATES)
                        if ta is not None:
                            break
                        page.wait_for_timeout(500)
                if ta is None:
                    ta = selectors.first(page, TEXTAREA_CANDIDATES)
            if ta is None:
                dismiss_compose_dialog(page, human)
                if attempt == MAX_ATTEMPTS - 1:
                    return False, "no-textarea"
                human.pause(2, 4)
                continue
            typed = _type_text_verified(page, ta, text)
            if not typed:
                dismiss_compose_dialog(page, human)
                if attempt == MAX_ATTEMPTS - 1:
                    return False, "typing-failed"
                human.pause(2, 4)
                continue
            send = _wait_send_enabled(page, timeout_ms=4000)
            if send is not None:
                try:
                    send.click()
                except Exception:
                    pass
                if _composer_cleared(page, ta, timeout_ms=6000):
                    dismiss_compose_dialog(page, human)
                    return True, "commented"
                # النقر لم يُسجّل إرسالاً: بديل اختصار النشر Ctrl+Enter
                try:
                    page.keyboard.press("Control+Enter")
                except Exception:
                    pass
                if _composer_cleared(page, ta, timeout_ms=6000):
                    dismiss_compose_dialog(page, human)
                    return True, "commented"
            else:
                # لا زر نشر ظاهر/مفعّل حتى بعد الانتظار: جرّب الاختصار المباشر
                try:
                    page.keyboard.press("Control+Enter")
                except Exception:
                    pass
                if _composer_cleared(page, ta, timeout_ms=6000):
                    dismiss_compose_dialog(page, human)
                    return True, "commented"
            dismiss_compose_dialog(page, human)
            if attempt == MAX_ATTEMPTS - 1:
                return False, ("send-disabled" if send is not None else "no-send-button")
            human.pause(2, 4)
        except Exception as e:
            dismiss_compose_dialog(page, human)
            if attempt == MAX_ATTEMPTS - 1:
                return False, _err_status(e)
            human.pause(2, 4)
    dismiss_compose_dialog(page, human)
    return False, "unconfirmed"
# FIX-SENDBTN-V2-COMMENT


def _tweet_text_of(art):
    """استخراج نص التغريدة من عنصر المقال (أو فارغ إن لم يوجد)."""
    try:
        el = art.locator(TWEET_TEXT).first
        if el.count() > 0:
            return (el.inner_text() or "").strip()[:500]
    except Exception:
        pass
    return ""


def extract_tweet_id(art):
    """استخراج معرّف التغريدة من عنصر المقال (أو None)."""
    try:
        link = art.locator(STATUS_LINK).first
        if link.count() > 0:
            href = link.get_attribute("href") or ""
            m = re.search(r"/status/(\d+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def scrape_profile_tweets(page, handle, max_scrolls=5, human=None):
    """جلب آخر تغريدات من بروفايل: قائمة {id, text} (يعمل بجلسة ضيف أو مسجّل).

    النص ضروري لتوليد تعليقات سياقية ذكية حسب موضوع التغريدة.
    """
    human = human or Human(page)
    tweets = []
    try:
        # ✅ تم التعديل: تجنب الـ Timeout أثناء فتح البروفايل
        resp = page.goto(f"https://x.com/{handle}", timeout=60000, wait_until="domcontentloaded")
    except Exception as e:
        return tweets, f"goto-error:{e}"[:100]
    page.wait_for_timeout(3500)
    wall = False
    for _ in range(20):
        if selectors.has(page, TWEET_ARTICLE_CANDIDATES):
            break
        if selectors.has(page, LOGIN_WALL):
            wall = True
            break
        page.wait_for_timeout(1000)
    if wall:
        return tweets, "login-wall"
    # صفحة فارغة أو محجوبة (مثل 403 من عناوين مراكز البيانات)
    try:
        body_len = len((page.locator("body").inner_text() or "").strip())
    except Exception:
        body_len = 0
    if body_len < 50:
        status = "blocked-empty" if (resp and getattr(resp, "status", 200) and resp.status in (401, 403, 429)) else "empty-page"
        return tweets, status
    seen_ids = set()
    for _ in range(max_scrolls):
        for art in page.locator(TWEET_ARTICLE).all():
            link = art.locator(STATUS_LINK).first
            try:
                if link.count() > 0:
                    href = link.get_attribute("href") or ""
                    m = re.search(r"/status/(\d+)", href)
                    if m and m.group(1) not in seen_ids:
                        seen_ids.add(m.group(1))
                        tweets.append({"id": m.group(1), "text": _tweet_text_of(art)})
            except Exception:
                continue
        human.pause(2.0, 4.5)
        page.mouse.wheel(0, random.randint(600, 1400))
        # ✅ تم التعديل: إعطاء المتصفح مهلة لتحميل التغريدات الجديدة في DOM بعد التمرير
        page.wait_for_timeout(1500) 
    return tweets[:60], "ok"


def scroll_home(page, human, steps=4):
    """تصفح الصفحة الرئيسية بتمرير بشري."""
    for _ in range(steps):
        human.pause(1.5, 5.0)
        page.mouse.wheel(0, random.randint(500, 1500))
        human.pause(0.8, 3.0)


def _parse_count(text):
    """تحويل نصوص مثل '1.2M Following' أو '12,345' إلى رقم صحيح."""
    if not text:
        return 0
    t = (text or "").strip()
    t = re.sub(r"(?i)(followers|following|likes|views|posts|monthly|\bx\b)", "", t)
    t = t.replace(",", "").replace(" ", "")
    m = re.match(r"^([\d.]+)\s*([KMB])?$", t, re.I)
    if not m:
        try:
            return int(float(t))
        except Exception:
            return 0
    num = float(m.group(1))
    suf = (m.group(2) or "").upper()
    if suf == "K":
        num *= 1_000
    elif suf == "M":
        num *= 1_000_000
    elif suf == "B":
        num *= 1_000_000_000
    return int(num)


def _profile_loaded(page):
    """هل اكتمل تحميل البروفايل؟ (أي مؤشر من: رابط المتابعين/يتّبع، زر متابعة، أو البايو)."""
    for cands in (FOLLOWERS_LINK_CANDIDATES, FOLLOWING_LINK_CANDIDATES,
                  FOLLOW_BTN_CANDIDATES, PROFILE_BIO_CANDIDATES):
        try:
            if selectors.has(page, cands):
                return True
        except Exception:
            continue
    return False


def _profile_unavailable(page):
    """هل البروفايل غير متاح/محذوف؟ (حالة فارغة ظاهرة بوضوح)."""
    for cands in ('[data-testid="emptyState"]', 'div[data-testid="emptyState"]'):
        try:
            if page.locator(cands).first.count() > 0:
                return True
        except Exception:
            continue
    return False


def open_profile(page, handle, cfg=None):
    """فتح بروفايل مستخدم (x.com/<handle>) مع إعادة محاولة عند أخطاء الشبكة."""
    ok, status = resilience.goto_with_retry(
        page, f"https://x.com/{handle}", cfg or {"resilience": {}}
    )
    if not ok:
        return False, f"goto-error:{status}"
    page.wait_for_timeout(3000)
    for _ in range(20):
        if _profile_loaded(page):
            return True, "ok"
        if _profile_unavailable(page):
            return False, "no-profile"
        if selectors.has(page, LOGIN_WALL):
            return False, "login-wall"
        page.wait_for_timeout(1000)
    return False, "no-profile"


def read_profile(page, handle):
    """قراءة معلومات البروفايل: بايو، متابعين، يتّبع، موثّق؟"""
    info = {"bio": "", "followers": 0, "following": 0, "verified": False}
    try:
        bio = selectors.first(page, PROFILE_BIO_CANDIDATES)
        if bio is not None:
            info["bio"] = (bio.inner_text() or "").strip()[:300]
    except Exception:
        pass
    for key, cands in (("followers", FOLLOWERS_LINK_CANDIDATES), ("following", FOLLOWING_LINK_CANDIDATES)):
        try:
            el = selectors.first(page, cands, visible=False)
            if el is not None:
                label = el.get_attribute("aria-label") or ""
                info[key] = _parse_count(label or (el.inner_text() or ""))
        except Exception:
            pass
    try:
        if selectors.has(page, VERIFIED_ICON_CANDIDATES):
            info["verified"] = True
    except Exception:
        pass
    return info


def is_following(page, handle):
    """هل نتابع هذا المستخدم حالياً؟"""
    try:
        if selectors.has(page, unfollow_handle_candidates(handle)):
            return True
    except Exception:
        pass
    return False


def follow_user(page, human, handle):
    """متابعة مستخدم (بروفايله مفتوح): hover → click → تحقق من زر إلغاء المتابعة."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            if is_following(page, handle):
                return True, "already-following"
            btn = selectors.first(page, follow_handle_candidates(handle))
            if btn is None:
                btn = selectors.first(page, FOLLOW_BTN_CANDIDATES)
            if btn is None:
                return False, "no-follow-button"
            human.mouse_wander(page)
            btn.hover()
            human.pause(1.0, 2.6)
            btn.click()
            page.wait_for_timeout(2200)
            if is_following(page, handle):
                return True, "followed"
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1:
                return False, _err_status(e)
            human.pause(2, 4)
    return False, "unconfirmed"