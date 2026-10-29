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


def dismiss_all_modals(page, human):
    """Auto-dismiss any modal/popup on page (Views, alerts, etc)."""
    try:
        # Search for Dismiss button
        dismiss_btns = page.locator('button:has-text("Dismiss"), button:contains("Dismiss")')
        if dismiss_btns.count() > 0:
            try:
                human.pause(0.2, 0.5)
                dismiss_btns.first.click()
                page.wait_for_timeout(600)
                return True
            except Exception:
                pass

        # Try X button for close
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


def _parse_count(text):
    """Convert text like '1.2M Following' or '12,345' to integer."""
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
    """Is profile fully loaded? (any indicator: followers/following link, follow button, or bio)."""
    for cands in (FOLLOWERS_LINK_CANDIDATES, FOLLOWING_LINK_CANDIDATES,
                  FOLLOW_BTN_CANDIDATES, PROFILE_BIO_CANDIDATES):
        try:
            if selectors.has(page, cands):
                return True
        except Exception:
            continue
    return False


def _profile_unavailable(page):
    """Is profile unavailable/deleted? (clearly empty state)."""
    for cands in ('[data-testid="emptyState"]', 'div[data-testid="emptyState"]'):
        try:
            if page.locator(cands).first.count() > 0:
                return True
        except Exception:
            continue
    return False


def open_profile(page, handle, cfg=None):
    """Open user profile (x.com/<handle>) with retry on network errors."""
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
    """Read profile info: bio, followers, following, verified?"""
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
    """Are we currently following this user?"""
    try:
        if selectors.has(page, unfollow_handle_candidates(handle)):
            return True
    except Exception:
        pass
    return False


def follow_user(page, human, handle):
    """Follow user (profile open): hover → click → verify unfollow button."""
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