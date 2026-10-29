# -*- coding: utf-8 -*-
"""Daily growth and warmup: discover crypto enthusiasts + slow random follows + crypto search browse."""
import random
import re
import time
from urllib.parse import quote

from .actions import (
    TWEET_ARTICLE, LIKE_BTN, UNLIKE_BTN, USER_CELL,
    follow_user, open_profile, read_profile, extract_tweet_id,
)
from .browser import is_login_wall
from .config import save_state
from .humanizer import Human, randint
from .logger import get_logger
from . import resilience

log = get_logger("grow")

DEFAULT_QUERIES = [
    "bitcoin", "crypto", "altcoins", "ethereum", "solana",
    "crypto trading", "#crypto", "diamond hands", "altseason",
]


def crypto_queries(cfg):
    """Get crypto queries from config (or defaults)."""
    f = cfg.get("follow", {})
    qs = f.get("queries") or DEFAULT_QUERIES
    return [str(q).strip() for q in qs if q is not None and str(q).strip()]


def pick_query(cfg):
    """Pick a random crypto query."""
    qs = crypto_queries(cfg)
    return random.choice(qs) if qs else "bitcoin"


def _handle_from_href(href):
    """Extract account name from profile link (pure for testing). Ignores /status/ and /i/."""
    if not href:
        return None
    h = href.strip()
    for pre in ("https://x.com", "http://x.com", "https://twitter.com", "http://twitter.com"):
        if h.startswith(pre):
            h = h[len(pre):]
    m = re.match(r"^/([A-Za-z0-9_]{1,15})$", h)
    return m.group(1) if m else None


def find_candidates(page, human, seen_users=(), limit=30):
    """Collect candidates from people search results (f=user) — unique handles we haven't seen."""
    seen = set(seen_users or ())
    handles = []
    try:
        for cell in page.locator(USER_CELL).all():
            if len(handles) >= limit:
                break
            try:
                for link in cell.locator("a[href]").all():
                    handle = _handle_from_href(link.get_attribute("href") or "")
                    if handle and handle not in seen and handle not in handles:
                        handles.append(handle)
                        break
            except Exception:
                continue
    except Exception:
        pass
    return handles


def profile_eligible(profile, cfg):
    """Profile eligibility conditions for following (pure function for testing)."""
    f = cfg.get("follow", {})
    if not f.get("enabled", True):
        return False
    if f.get("skip_verified", True) and profile.get("verified"):
        return False
    if f.get("require_bio", True) and not (profile.get("bio") or "").strip():
        return False
    followers = profile.get("followers", 0) or 0
    if followers < int(f.get("min_followers", 50)):
        return False
    if followers > int(f.get("max_followers", 150000)):
        return False
    return True


def follow_budget(state, cfg):
    """How many follows are left today?"""
    f = cfg.get("follow", {})
    if not f.get("enabled", True):
        return 0
    caps = state.get("caps", {})
    done = state.get("done", {})
    return max(0, int(caps.get("follows", 0)) - int(done.get("follows", 0)))


def _follow_one(page, human, handle, cfg, state, acc_id):
    """Visit profile, check eligibility, and maybe follow (visit-only ratio from config)."""
    f = cfg.get("follow", {})
    visit_only = random.random() < float(f.get("visit_only_chance", 0.25))
    ok, status = open_profile(page, handle, cfg)
    if not ok:
        log.info(f"{acc_id} | Skip @{handle}: {status}")
        return None, status
    human.pause(2.0, 5.0)
    page.mouse.wheel(0, random.randint(200, 700))
    human.pause(1.5, 4.0)
    profile = read_profile(page, handle)
    if visit_only:
        log.info(f"{acc_id} | Visit only @{handle} (no follow)")
        return None, "visit-only"
    if not profile_eligible(profile, cfg):
        reason = "verified" if profile.get("verified") else (
            "no-bio" if not (profile.get("bio") or "").strip() else "followers-range")
        log.info(f"{acc_id} | Skip @{handle}: not eligible ({reason})")
        return None, f"not-eligible:{reason}"
    human.mouse_wander(page)
    res, msg = follow_user(page, human, handle)
    if res:
        log.info(f"{acc_id} | ✓ Followed @{handle}")
        return handle, msg
    log.warning(f"{acc_id} | ✗ Follow @{handle} failed: {msg}")
    return None, f"follow-failed:{msg}"


def _get_likers_on_tweet(page, human):
    """Fetch people who liked the currently open tweet."""
    likers = []
    try:
        # Find the like count and click it
        like_count = page.locator('a[href*="/likes"], [role="button"]:has-text("Like")').first
        if like_count.count() > 0:
            like_count.click()
            page.wait_for_timeout(2000)

            # Fetch people from the likes list
            user_cells = page.locator(USER_CELL).all()
            for cell in user_cells[:15]:  # First 15 people
                try:
                    link = cell.locator("a[href]").first
                    if link.count() > 0:
                        href = link.get_attribute("href") or ""
                        handle = _handle_from_href(href)
                        if handle:
                            likers.append(handle)
                except:
                    pass

        # Close the list
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
    except:
        pass
    return likers


def run_follow_pass(page, acc_id, cfg, state, human, max_follows):
    """Smart follows: follow people who like crypto tweets (engaged users)."""
    f = cfg.get("follow", {})
    budget = follow_budget(state, cfg)
    if budget <= 0 or max_follows <= 0:
        return 0, "no-budget"

    log.info(f"{acc_id} | Follow active people (likes/comments) (remaining: {budget})")

    # New strategy: fetch crypto tweets and search for active people
    query = pick_query(cfg)
    ok, gstatus = resilience.goto_with_retry(
        page, f"https://x.com/search?q={quote(query)}", cfg
    )
    if not ok:
        log.warning(f"{acc_id} | Failed to open search: {gstatus}")
        return 0, f"goto-error:{gstatus}"
    page.wait_for_timeout(3500)

    human.pause(2.0, 5.0)
    seen_users = set(state.get("seen_users", []) or [])
    candidates = []

    # Fetch people from search results (likes and comments)
    try:
        articles = page.locator(TWEET_ARTICLE).all()
        for art in articles[:8]:  # First 8 tweets
            try:
                link = art.locator('a[href*="/status/"]').first
                if link.count() > 0:
                    art.click()
                    page.wait_for_timeout(2000)

                    # Fetch people who liked
                    likers = _get_likers_on_tweet(page, human)
                    candidates.extend(likers)

                    page.keyboard.press("Escape")
                    page.wait_for_timeout(1500)
            except:
                continue
    except:
        pass

    # Clean from duplicates and previously seen
    candidates = list(set(c for c in candidates if c not in seen_users))
    random.shuffle(candidates)
    followed = 0
    lo_delay, hi_delay = (int(x) for x in f.get("delay_between_s", [90, 300]))

    if not candidates:
        log.info(f"{acc_id} | No active people found to follow - fallback to regular search")
        # fallback للاستراتيجية القديمة إذا ما اشتغلت الجديدة
        return 0, "no-active-users"

    log.info(f"{acc_id} | Found {len(candidates)} active people - starting follows")

    for handle in candidates[:max_follows]:
        if follow_budget(state, cfg) <= 0:
            break
        try:
            got, msg = _follow_one(page, human, handle, cfg, state, acc_id)
            seen_users.add(handle)
            if got:
                state.setdefault("followed_handles", []).append(handle)
                state["done"]["follows"] += 1
                followed += 1
                log.info(f"{acc_id} | ✓ Followed @{handle} (active)")

            # Save state
            seen_list = list(seen_users)
            if len(seen_list) > 3000:
                seen_list = seen_list[-3000:]
            state["seen_users"] = sorted(seen_list)
            save_state(acc_id, state)
            human.pause(max(20, int(lo_delay * 0.6)), hi_delay)
        except Exception as e:
            log.warning(f"{acc_id} | Error following @{handle}: {e}")
            continue

    log.info(f"{acc_id} | Smart follow pass: +{followed} (total today {state['done']['follows']})")
    return followed, "ok"


def browse_crypto_search(page, acc_id, cfg, state, human, budget_s, start):
    """Browse latest crypto tweets (f=live) with light liking within daily limits."""
    ok, gstatus = resilience.goto_with_retry(
        page, f"https://x.com/search?q={quote(pick_query(cfg))}&f=live", cfg
    )
    if not ok:
        log.warning(f"{acc_id} | Failed to open crypto search: {gstatus}")
        return
    page.wait_for_timeout(3500)
    scrolls = 0
    liked_ids = set()
    while time.time() - start < budget_s:
        human.pause(2.0, 6.0)
        page.mouse.wheel(0, random.randint(500, 1500))

        # Give time for Twitter to fetch new tweets after scrolling
        page.wait_for_timeout(1500) 
        
        scrolls += 1
        if scrolls % 3 == 0:
            human.pause(4.0, 10.0)
            
        articles = page.locator(TWEET_ARTICLE).all()
        if not articles:
            human.pause(3, 6)
            continue
            
        likes_left = state["done"]["likes"] < state["caps"]["likes"]
        if likes_left and random.random() < 0.5:
            try:
                art = random.choice(articles)
                tid = extract_tweet_id(art)
                if tid and tid in liked_ids:
                    continue
                if art.locator(UNLIKE_BTN).first.count() > 0:
                    if tid:
                        liked_ids.add(tid)
                    continue
                like_btn = art.locator(LIKE_BTN).first
                if like_btn.count() > 0 and like_btn.is_visible():
                    like_btn.hover()
                    human.pause(0.7, 2.0)
                    like_btn.click()
                    page.wait_for_timeout(1200)
                    if tid:
                        liked_ids.add(tid)
                    state["done"]["likes"] += 1
                    save_state(acc_id, state)
                    log.info(f"{acc_id} | Crypto like #{state['done']['likes']}")
            except Exception:
                pass
            human.pause(3.0, 9.0)
            
        if not likes_left and time.time() - start > budget_s * 0.5:
            break


def smart_follow_engaged_users(page, acc_id, cfg, state, human, max_follows):
    """Follow people interested in crypto who liked or commented on crypto tweets.

    Conditions:
    ✅ Engaged users (liked or commented)
    ❌ Not verified
    ❌ Followers < 6000
    ❌ Not already followed (globally)
    """
    f = cfg.get("follow", {})
    budget = follow_budget(state, cfg)
    if budget <= 0 or max_follows <= 0:
        return 0, "no-budget"

    log.info(f"{acc_id} | Follow interested users (engaged users)")

    # Fetch interested people from Home Timeline (recent, not top)
    engaged_candidates = []
    try:
        # Open Home Timeline
        ok, _ = resilience.goto_with_retry(
            page, "https://x.com/home", cfg
        )
        if not ok:
            return 0, "goto-failed"

        page.wait_for_timeout(3000)
        human.pause(1.0, 3.0)

        # Scroll a bit to fetch recent tweets
        page.mouse.wheel(0, 500)
        page.wait_for_timeout(1500)

        # Fetch recent tweets
        articles = page.locator(TWEET_ARTICLE).all()[:8]  # First 8 recent tweets

        for article in articles:
            try:
                # Fetch from likes
                like_btn = article.locator('[role="button"][aria-label*="Like"]').first
                if like_btn.count() > 0:
                    like_btn.click()
                    page.wait_for_timeout(1500)

                    # Fetch people from likes list
                    user_cells = page.locator(USER_CELL).all()[:10]
                    for cell in user_cells:
                        try:
                            link = cell.locator('a[href]').first
                            if link.count() > 0:
                                href = link.get_attribute("href") or ""
                                handle = _handle_from_href(href)
                                if handle and handle not in engaged_candidates:
                                    engaged_candidates.append(handle)
                        except:
                            pass

                    # Close the list
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(1000)
            except:
                pass

    except Exception as e:
        log.warning(f"{acc_id} | Error fetching engaged users: {e}")
        return 0, f"error:{e}"[:50]

    # Filter and Follow
    followed = 0
    lo_delay, hi_delay = (int(x) for x in f.get("delay_between_s", [90, 300]))

    # Fetch previously followed people (global tracking)
    seen_handles = set(state.get("seen_users", []) or [])

    for handle in engaged_candidates[:max_follows]:
        if follow_budget(state, cfg) <= 0:
            break

        # Skip if already followed from another account
        if handle in seen_handles:
            log.info(f"{acc_id} | {handle} - already followed from another account")
            continue

        try:
            # Open profile
            ok, status = open_profile(page, handle, cfg)
            if not ok:
                continue

            human.pause(1.5, 3.0)

            # Read profile
            profile = read_profile(page, handle)

            # Check: verified?
            if profile.get("verified"):
                log.info(f"{acc_id} | {handle} - verified, skip")
                continue

            # Check: followers > 6000?
            followers = profile.get("followers", 0) or 0
            if followers > 6000:
                log.info(f"{acc_id} | {handle} - {followers} followers (> 6000), skip")
                continue

            # OK - Follow!
            res, msg = follow_user(page, human, handle)
            if res:
                state.setdefault("followed_handles", []).append(handle)
                state["done"]["follows"] = state["done"].get("follows", 0) + 1
                seen_handles.add(handle)
                state["seen_users"] = list(seen_handles)
                followed += 1
                log.info(f"{acc_id} | ✅ Followed {handle} ({followers} followers)")

            save_state(acc_id, state)
            human.pause(lo_delay, hi_delay)

        except Exception as e:
            log.warning(f"{acc_id} | Error following {handle}: {e}")
            continue

    return followed, "ok"


def crypto_activity(page, acc_id, cfg, state, human, budget_s):
    """Daily crypto activity: search browse + slow follows, within time budget."""
    f = cfg.get("follow", {})
    w = cfg.get("warmup", {})
    start = time.time()
    if w.get("browse_probability", 0.8) > random.random():
        browse_crypto_search(page, acc_id, cfg, state, human, budget_s * 0.5, start)

    # Smart Follow (interested users) - instead of random follow
    if f.get("enabled", True) and follow_budget(state, cfg) > 0:
        per_session = f.get("per_session", [1, 3])
        smart_follow_engaged_users(page, acc_id, cfg, state, human, randint(*per_session))