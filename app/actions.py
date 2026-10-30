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
    followers_link_candidates, following_link_candidates,
    PROTECTED_ICON_CANDIDATES, PROFILE_NAME_BLOCK,
    FOLLOW_TESTID_SUFFIX, UNFOLLOW_TESTID_SUFFIX, PENDING_TESTID_SUFFIX,
    FOREIGN_BUTTON_CONTAINERS, FOLLOW_BLOCK_TEXT_PATTERNS, TOAST_CANDIDATES,
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
        # Sheet didn't clear or stuck in /compose: leave page
        try:
            if page.locator('[data-testid="confirmationSheet"]').count() > 0 or "/compose/" in (page.url or ""):
                # Add wait_until to avoid timeout
                page.goto("https://x.com/home", timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
        return False
    except Exception:
        return False
# FIX-SAVEPOST-V2-DISMISS


_DISMISS_LABELS = re.compile(
    r"^\s*(dismiss|close|ok|got it|not now|maybe later|skip for now|"
    r"تجاهل|إغلاق|حسنا|حسناً|فهمت|ليس الآن|ليس الان)\s*$", re.I)
_COOKIE_LABELS = re.compile(
    r"^\s*(refuse non-essential cookies|accept all cookies|"
    r"رفض ملفات تعريف الارتباط غير الضرورية|قبول جميع ملفات تعريف الارتباط)\s*$", re.I)
_DIALOGS = '[role="dialog"], [role="alertdialog"], [data-testid="sheetDialog"]'


def _count(loc):
    try:
        return loc.count()
    except Exception:
        return 0


def dismiss_all_modals(page, human):
    """Close blocking popups (Views, notifications prompt, alerts, cookie banner).

    Only buttons INSIDE an open dialog (matched by exact accessible name) are
    clicked, so nothing on the normal page can be hit by accident. Escape is
    pressed only when a non-composer dialog is actually open. Returns True when
    something was closed.
    """
    closed = False
    try:
        # Cookie banner (bottom bar, not a dialog) - refuse optional cookies.
        for _ in range(2):
            btn = page.get_by_role("button", name=_COOKIE_LABELS).first
            if _count(btn) and btn.is_visible():
                human.pause(0.3, 0.8)
                btn.click()
                page.wait_for_timeout(600)
                closed = True
            else:
                break

        for _ in range(3):  # stacked dialogs
            dialogs = page.locator(_DIALOGS)
            if _count(dialogs) == 0:
                break
            dlg = dialogs.last
            if _count(dlg.locator(TEXTAREA)) > 0:
                break  # composer: owned by dismiss_compose_dialog
            before = _count(dialogs)

            btn = dlg.get_by_role("button", name=_DISMISS_LABELS).first
            if not (_count(btn) and btn.is_visible()):
                btn = dlg.locator(
                    '[data-testid="app-bar-close"], button[aria-label="Close"], '
                    'button[aria-label="إغلاق"]').first
            if _count(btn) and btn.is_visible():
                human.pause(0.3, 0.8)
                btn.click()
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(700)
            if _count(page.locator(_DIALOGS)) < before:
                closed = True
            else:
                break  # could not close it; don't loop forever
    except Exception:
        pass
    return closed


def open_tweet(page, url):
    """Open a tweet. Returns (ok, status) where status is: ok/deleted/login-wall/oops-error/network-error/timeout/error."""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            if attempt < max_attempts - 1:
                log.warning(f"Error opening tweet (attempt {attempt + 2}): {_err_status(e)}")
                page.wait_for_timeout(1500)
                continue
            return False, _err_status(e)

        page.wait_for_timeout(2500)

        # Check for common errors
        if _detect_oops_error(page):
            if attempt < max_attempts - 1:
                log.warning(f"Oops error when opening tweet - reload (attempt {attempt + 2})")
                page.wait_for_timeout(1500)
                continue
            return False, "oops-error"

        # Wait for article to load
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
    """Open tweet with retry on network/timeout errors."""
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
                    f"Network issue opening tweet {url} - retrying ({i + 2}/{r + 1}) after {d:.1f} seconds"
                )
                time.sleep(d)
                continue
        last = status
    return False, last


def like_tweet(page, human):
    """Like the currently open tweet."""
    dismiss_compose_dialog(page, human)  # Safe exit from any previous quote draft
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
    """Retweet/share the currently open tweet."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            # If stuck in quote composer/draft from previous round: exit first
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
            # Wait for popup menu (up to 3 seconds)
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
                        if any(k in txt for k in ("repost", "retweet")):
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
            # If quote composer opened by mistake: reject draft immediately and retry
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
    """Text currently in comment composer (or empty on any error)."""
    try:
        return (ta.inner_text() or "").strip()
    except Exception:
        return ""


def _norm_spaces(t):
    """Normalize spaces for comparison (X may convert line breaks/spaces)."""
    return " ".join((t or "").split())


def _type_text_verified(page, ta, text):
    """Type comment text and verify it actually settled in the box (not lost keystrokes).

    Stages: character by character (twice with pre-clear) ← insert_text batch ← fill().
    Returns text actually in composer, or empty string if writing didn't stick.
    """
    if not text:
        return ""
    target = _norm_spaces(text)
    # 1) Type character by character after confirmed focus and composer clear
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
    # 2) Simulate paste text batch (more stable even if focus lags)
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
    # 3) fill(): sets contenteditable content directly without keyboard simulation
    try:
        ta.fill(text)
        page.wait_for_timeout(450)
        cur = _composer_text(page, ta)
        return cur if _norm_spaces(cur) == target else cur
    except Exception:
        return ""


def _wait_send_enabled(page, timeout_ms=4000):
    """Wait for send button to appear and be enabled (until timeout_ms)."""
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
    """Did comment actually send? (composer empty or disappeared from page)."""
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
    """Text comment on currently open tweet (with cleanup of any stuck draft).

    FIX-SENDBTN-V2-COMMENT: Writing verified (fallback: insert_text/fill),
    send button waited until enabled up to 4 seconds, on click fail we use official
    Ctrl+Enter shortcut in X. Success check = composer empty/disappeared.
    """
    dismiss_compose_dialog(page, human)  # Safe exit from any previous draft/dialog
    for attempt in range(MAX_ATTEMPTS):
        try:
            # Light scroll to show reply area on tweet page
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
                # Click didn't register send: fallback to Ctrl+Enter publish shortcut
                try:
                    page.keyboard.press("Control+Enter")
                except Exception:
                    pass
                if _composer_cleared(page, ta, timeout_ms=6000):
                    dismiss_compose_dialog(page, human)
                    return True, "commented"
            else:
                # No send button visible/enabled even after wait: try direct shortcut
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
    """Extract tweet text from article element (or empty if not found)."""
    try:
        el = art.locator(TWEET_TEXT).first
        if el.count() > 0:
            return (el.inner_text() or "").strip()[:500]
    except Exception:
        pass
    return ""


def extract_tweet_id(art):
    """Extract tweet ID from article element (or None)."""
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
    """Fetch latest tweets from profile: list of {id, text} (works with guest or logged session).

    Text needed to generate smart contextual comments based on tweet topic.
    """
    human = human or Human(page)
    tweets = []
    try:
        # Avoid timeout when opening profile
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
    # Empty or blocked page (like 403 from datacenter headers)
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
        # Give browser time to load new tweets in DOM after scroll
        page.wait_for_timeout(1500) 
    return tweets[:60], "ok"


def scroll_home(page, human, steps=4):
    """Browse home page with human-like scrolling."""
    for _ in range(steps):
        human.pause(1.5, 5.0)
        page.mouse.wheel(0, random.randint(500, 1500))
        human.pause(0.8, 3.0)


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬،", "0123456789.,,")
_COUNT_SUFFIX = {
    "K": 1_000, "ألف": 1_000, "الف": 1_000,
    "M": 1_000_000, "مليون": 1_000_000,
    "B": 1_000_000_000, "مليار": 1_000_000_000,
}


def _parse_count(text):
    """Convert '1.2M Following', '12,345', '٣٫٢ ألف متابع' ... to int (0 if unparsable)."""
    n = _parse_count_or_none(text)
    return n if n is not None else 0


def _parse_count_or_none(text):
    """Like _parse_count but returns None when no number can be found (unknown != 0)."""
    if not text:
        return None
    t = str(text).translate(_ARABIC_DIGITS)
    # Drop label words first: adjacent spans give "350Following" with no separator.
    t = re.sub(r"(?i)followers?|following|likes?|views?|posts?|monthly|"
               r"متابعون|متابعين|متابع|يتابع|مشاهدات|إعجابات", " ", t)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(K|M|B|ألف|الف|مليون|مليار)?(?![A-Za-z])", t, re.I)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suf = m.group(2) or ""
    mult = _COUNT_SUFFIX.get(suf.upper() if suf.isascii() else suf, 1)
    return int(num * mult)


def _profile_loaded(page):
    """Is the profile rendered? (header name block, bio, or follower counts)."""
    for cands in ([PROFILE_NAME_BLOCK], PROFILE_BIO_CANDIDATES,
                  FOLLOWERS_LINK_CANDIDATES, FOLLOWING_LINK_CANDIDATES):
        try:
            if selectors.has(page, cands):
                return True
        except Exception:
            continue
    return False


def _profile_unavailable(page):
    """Deleted / suspended / non-existent profile (X shows an empty state)."""
    try:
        return page.locator('[data-testid="emptyState"]').first.count() > 0
    except Exception:
        return False


def open_profile(page, handle, cfg=None):
    """Open x.com/<handle>. Returns (ok, status); status explains every failure."""
    ok, status = resilience.goto_with_retry(
        page, f"https://x.com/{handle}", cfg or {"resilience": {}}
    )
    if not ok:
        return False, f"goto-error:{status}"
    page.wait_for_timeout(2500)
    try:
        dismiss_all_modals(page, Human(page))
    except Exception:
        pass
    for _ in range(20):
        url = (page.url or "").lower()
        if "/i/flow/login" in url or url.rstrip("/").endswith("/login"):
            return False, "login-wall"
        if "/account/access" in url or "/i/flow/" in url:
            return False, "challenge"
        if _profile_unavailable(page):
            return False, "no-profile"
        if _profile_loaded(page):
            return True, "ok"
        if selectors.has(page, LOGIN_WALL):
            return False, "login-wall"
        page.wait_for_timeout(1000)
    return False, "no-profile"


def _read_count(page, handle, handle_cands, generic_cands):
    """Count from the profile's own link; None when it cannot be read."""
    for cands in (handle_cands, generic_cands):
        el = selectors.first(page, cands, visible=False)
        if el is None:
            continue
        for raw in ((el.inner_text() or ""), (el.get_attribute("aria-label") or "")):
            n = _parse_count_or_none(raw)
            if n is not None:
                return n
    return None


def read_profile(page, handle):
    """Read profile info. followers/following are None when unknown (not 0).

    Verified/protected badges are looked up ONLY inside the profile header
    name block, never on the whole page.
    """
    info = {"bio": "", "followers": None, "following": None,
            "verified": False, "protected": False}
    # Counts render slightly after the header; give them a moment.
    for _ in range(6):
        if selectors.has(page, followers_link_candidates(handle) + FOLLOWERS_LINK_CANDIDATES):
            break
        page.wait_for_timeout(1000)
    try:
        bio = selectors.first(page, PROFILE_BIO_CANDIDATES)
        if bio is not None:
            info["bio"] = (bio.inner_text() or "").strip()[:300]
    except Exception:
        pass
    try:
        info["followers"] = _read_count(page, handle, followers_link_candidates(handle), FOLLOWERS_LINK_CANDIDATES)
        info["following"] = _read_count(page, handle, following_link_candidates(handle), FOLLOWING_LINK_CANDIDATES)
    except Exception:
        pass
    try:
        info["verified"] = selectors.has(page, VERIFIED_ICON_CANDIDATES)
        info["protected"] = selectors.has(page, PROTECTED_ICON_CANDIDATES)
    except Exception:
        pass
    return info


def _action_button(page, handle, kind):
    """Follow / unfollow / pending button that belongs to the OPEN profile.

    X's testid is "<user id>-follow" (not the handle), so: try the handle-bearing
    aria-label first, then the testid suffix, skipping buttons inside user cells,
    the sidebar ("Who to follow") and tweets, which belong to other accounts.
    """
    named = {"follow": follow_handle_candidates(handle),
             "unfollow": unfollow_handle_candidates(handle),
             "pending": []}[kind]
    btn = selectors.first(page, named)
    if btn is not None:
        return btn
    suffix = {"follow": FOLLOW_TESTID_SUFFIX, "unfollow": UNFOLLOW_TESTID_SUFFIX,
              "pending": PENDING_TESTID_SUFFIX}[kind]
    try:
        for el in page.locator(f"button{suffix}, div[role='button']{suffix}").all():
            try:
                if not el.is_visible():
                    continue
                if el.evaluate("(e, sel) => !!e.closest(sel)", FOREIGN_BUTTON_CONTAINERS):
                    continue
                return el
            except Exception:
                continue
    except Exception:
        pass
    return None


def is_following(page, handle):
    """Are we currently following the open profile?"""
    return _action_button(page, handle, "unfollow") is not None


def _follow_block_reason(page):
    """Text of a rate-limit / restriction toast or dialog shown after a follow click."""
    url = (page.url or "").lower()
    if "/account/access" in url or "/i/flow/" in url:
        return "challenge-page"
    for sel in TOAST_CANDIDATES:
        try:
            for el in page.locator(sel).all()[:3]:
                txt = (el.inner_text() or "").strip().lower()
                if txt and any(p in txt for p in FOLLOW_BLOCK_TEXT_PATTERNS):
                    return txt[:80]
        except Exception:
            continue
    return None


def follow_user(page, human, handle):
    """Follow the open profile and verify it. Returns (ok, status).

    Statuses: followed | already-following | pending-request | no-follow-button |
    follow-blocked | unconfirmed | timeout | network-error | error:...
    A follow button is never clicked twice blindly: state is re-read first.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            dismiss_all_modals(page, human)
            if is_following(page, handle):
                return True, "already-following"
            if _action_button(page, handle, "pending") is not None:
                return False, "pending-request"
            btn = _action_button(page, handle, "follow")
            if btn is None:
                return False, "no-follow-button"
            human.mouse_wander(page)
            btn.hover()
            human.pause(1.0, 2.6)
            btn.click()
            page.wait_for_timeout(2200)
            reason = _follow_block_reason(page)
            if reason:
                log.warning(f"Follow blocked by X after clicking @{handle}: {reason}")
                return False, "follow-blocked"
            for _ in range(3):
                if is_following(page, handle):
                    return True, "followed"
                if _action_button(page, handle, "pending") is not None:
                    return False, "pending-request"
                page.wait_for_timeout(800)
        except Exception as e:
            if attempt == MAX_ATTEMPTS - 1:
                return False, _err_status(e)
            human.pause(2, 4)
    return False, "unconfirmed"
