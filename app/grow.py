# -*- coding: utf-8 -*-
"""Daily growth and warmup: discover crypto enthusiasts + slow random follows + crypto search browse."""
import random
import re
import threading
import time
from urllib.parse import quote

from .actions import (
    TWEET_ARTICLE, LIKE_BTN, UNLIKE_BTN, STATUS_LINK, RETWEET_BTN, _parse_count_or_none,
    follow_user, open_profile, read_profile, extract_tweet_id, dismiss_all_modals,
)
from .browser import is_login_wall
from .config import (
    load_accounts, load_global_followed, save_global_followed, save_state,
)
from .humanizer import Human, randint
from .logger import get_logger
from . import resilience

log = get_logger("grow")

DEFAULT_QUERIES = [
    # majors / cashtags
    "bitcoin", "ethereum", "solana", "$BTC", "$ETH", "$SOL", "$BNB", "$XRP", "$LINK", "$SUI",
    # general
    "crypto", "altcoins", "altseason", "#crypto", "#bitcoin", "#altcoins", "crypto market",
    # trading
    "crypto trading", "technical analysis crypto", "price action crypto", "support and resistance crypto",
    "crypto breakout", "swing trading crypto", "crypto futures", "bitcoin ETF",
    # defi / web3 / on-chain
    "defi", "web3", "onchain analysis", "layer 2 crypto", "memecoin", "airdrop hunters",
    # community
    "diamond hands", "hodl", "bull run crypto", "crypto twitter",
    # arabic
    "كريبتو", "عملات رقمية", "بيتكوين", "تداول العملات الرقمية", "تحليل فني كريبتو", "العملات البديلة",
]

# Spam terms excluded from every live search.
_SEARCH_EXCLUDES = ' -giveaway -"follow back" -f4f'
_recent_queries = []

DEFAULT_BIO_BLOCK_WORDS = [
    "giveaway", "follow back", "followback", "f4f", "dm for", "dm me for", "vip signals",
    "guaranteed profit", "100x guaranteed", "onlyfans", "18+", "casino", "betting", "forex signals",
]
_STATUS_RE = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)")


def crypto_queries(cfg):
    """Get crypto queries from config (or defaults)."""
    f = cfg.get("follow", {})
    qs = f.get("queries") or DEFAULT_QUERIES
    return [str(q).strip() for q in qs if q is not None and str(q).strip()]


def pick_query(cfg, avoid=None):
    """Pick a random crypto query, avoiding the ones used most recently."""
    qs = crypto_queries(cfg)
    if not qs:
        return "bitcoin"
    recent = _recent_queries if avoid is None else avoid
    pool = [q for q in qs if q not in recent] or qs
    q = random.choice(pool)
    recent.append(q)
    del recent[:-max(1, min(8, len(qs) // 2))]
    return q


def search_url(query, live=True, min_reposts=0):
    """X search URL; live tab excludes spam terms, optional min_retweets operator."""
    q = query + _SEARCH_EXCLUDES if live else query
    if min_reposts:
        q += f" min_retweets:{int(min_reposts)}"
    return f"https://x.com/search?q={quote(q)}" + ("&f=live" if live else "")


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
    """Handles from a people-search page (f=user): UserCell entries we haven't seen."""
    seen = {str(s).lower() for s in (seen_users or ())}
    handles = []
    try:
        for cell in page.locator('[data-testid="UserCell"]').all():
            if len(handles) >= limit:
                break
            try:
                for link in cell.locator("a[href]").all():
                    handle = _handle_from_href(link.get_attribute("href") or "")
                    if handle and handle.lower() not in seen and handle not in handles:
                        handles.append(handle)
                        break
            except Exception:
                continue
    except Exception:
        pass
    return handles


def ineligible_reason(profile, cfg):
    """Why a profile must not be followed (None = eligible). Pure function for testing."""
    f = cfg.get("follow", {})
    if not f.get("enabled", True):
        return "follow-disabled"
    if profile.get("protected"):
        return "protected"
    if f.get("skip_verified", True) and profile.get("verified"):
        return "verified"
    if f.get("require_bio", True) and not (profile.get("bio") or "").strip():
        return "no-bio"
    bio = (profile.get("bio") or "").lower()
    blocked = f.get("bio_block_words", DEFAULT_BIO_BLOCK_WORDS)
    if any(str(w).lower() in bio for w in blocked if str(w).strip()):
        return "spam-bio"
    followers = profile.get("followers")
    following = profile.get("following")
    if followers is not None and following is not None and following > 5000 and followers * 5 < following:
        return "follow-spam"
    if followers is not None:  # unknown count is not treated as 0
        if followers < int(f.get("min_followers", 50)):
            return "followers-too-few"
        if followers > int(f.get("max_followers", 150000)):
            return "followers-too-many"
    return None


def profile_eligible(profile, cfg):
    """Profile eligibility conditions for following (pure function for testing)."""
    return ineligible_reason(profile, cfg) is None


def follow_budget(state, cfg):
    """How many follows are left today? 0 while X has recently blocked following."""
    f = cfg.get("follow", {})
    if not f.get("enabled", True):
        return 0
    if time.time() < float(state.get("follow_blocked_until", 0) or 0):
        return 0
    caps = state.get("caps", {})
    done = state.get("done", {})
    return max(0, int(caps.get("follows", 0)) - int(done.get("follows", 0)))


# ---------------------------------------------------------------------------
# Candidate discovery
# X made likes private (2024), so "who liked this tweet" is no longer visible.
# Public signals we can use: authors on the home timeline / live crypto search,
# people replying under crypto tweets, and the people-search tab.
# ---------------------------------------------------------------------------
_registry_lock = threading.Lock()
_TRANSIENT = ("goto-error", "timeout", "network-error", "error:")


def _authors_in_view(page):
    """Ordered handles of tweet authors currently in the DOM (ads skipped)."""
    out = []
    try:
        for art in page.locator(TWEET_ARTICLE).all():
            try:
                if art.locator('xpath=.//span[normalize-space(text())="Ad"]').count() > 0:
                    continue
                link = art.locator('[data-testid="User-Name"] a[href^="/"]').first
                handle = _handle_from_href(link.get_attribute("href") or "") if link.count() else None
                if handle and handle not in out:
                    out.append(handle)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _scroll_collect(page, human, found, exclude, need, scrolls, skip_first=False):
    """Scroll the current page, appending new eligible-looking handles to `found`."""
    first_seen = None
    for _ in range(scrolls):
        authors = _authors_in_view(page)
        if skip_first and authors and first_seen is None:
            first_seen = authors[0]  # original poster of the conversation
        for h in authors:
            if h == first_seen or h.lower() in exclude or h in found:
                continue
            found.append(h)
        if len(found) >= need:
            return
        human.pause(1.5, 3.5)
        page.mouse.wheel(0, random.randint(700, 1400))
        page.wait_for_timeout(1500)


def _repost_count(art):
    """Repost count shown on a tweet (0 if none/unreadable)."""
    try:
        btn = art.locator(RETWEET_BTN).first
        if btn.count() == 0:
            return 0
        for raw in (btn.get_attribute("aria-label") or "", btn.inner_text() or ""):
            n = _parse_count_or_none(raw)
            if n is not None:
                return n
    except Exception:
        pass
    return 0


def _tweets_in_view(page, min_reposts=0):
    """[(reposts, tweet_id, author)] for tweets in the DOM with at least min_reposts reposts."""
    rows = set()
    try:
        for art in page.locator(TWEET_ARTICLE).all():
            try:
                m = None
                for a in art.locator(STATUS_LINK).all()[:3]:
                    m = _STATUS_RE.match((a.get_attribute("href") or "").split("?")[0])
                    if m:
                        break
                if not m:
                    continue
                n = _repost_count(art)
                if n >= min_reposts:
                    rows.add((n, m.group(2), m.group(1)))
            except Exception:
                continue
    except Exception:
        pass
    return rows


def _collect_user_cells(page, human, found, exclude, target, scrolls=4, cap=40):
    """Scroll a user list (reposted-by / people search) and append new handles to `found`."""
    for _ in range(scrolls):
        skip = set(exclude) | {h.lower() for h in found}
        for h in find_candidates(page, human, skip, limit=cap):
            if h not in found:
                found.append(h)
        if len(found) >= target:
            return
        human.pause(1.2, 2.5)
        page.mouse.wheel(0, random.randint(600, 1200))
        page.wait_for_timeout(1300)


def collect_candidates(page, acc_id, cfg, human, exclude, need):
    """Gather candidate handles from several public sources, a quota from each.

    1. live crypto search: authors, plus the most reposted tweets and reply threads
    2. reposted-by lists of those tweets (people who actively share crypto content)
    3. home timeline authors
    4. repliers under crypto tweets
    5. people-search tab
    """
    f = cfg.get("follow", {})
    min_reposts = int(f.get("min_reposts", 3))
    repost_tweets = int(f.get("retweeter_tweets", 3))
    quota = max(4, -(-need // 4))
    found = []
    reposted, reply_urls = set(), []

    def open_page(label, url):
        ok, st = resilience.goto_with_retry(page, url, cfg)
        if not ok:
            log.warning(f"{acc_id} | Candidates: cannot open {label} ({st})")
            return False
        page.wait_for_timeout(3000)
        dismiss_all_modals(page, human)
        return True

    def report(label, before):
        log.info(f"{acc_id} | Candidates from {label}: +{len(found) - before}")

    # 1) live crypto search (operator query first, plain query as fallback)
    query = pick_query(cfg)
    log.info(f"{acc_id} | Discovery keyword: {query!r}")
    for url in (search_url(query, True, min_reposts), search_url(query, True, 0)):
        if not open_page("live search", url):
            continue
        before = len(found)
        for _ in range(3):
            _scroll_collect(page, human, found, exclude, len(found) + quota, 1)
            reposted |= _tweets_in_view(page, min_reposts)
            if len(found) >= before + quota:
                break
        try:
            for art in page.locator(TWEET_ARTICLE).all()[:6]:
                href = art.locator(STATUS_LINK).first.get_attribute("href") or ""
                if re.search(r"/status/\d+", href):
                    reply_urls.append("https://x.com" + href.split("?")[0])
        except Exception:
            pass
        report("live search", before)
        if found or reposted:
            break

    # 2) reposted-by lists of the most reposted tweets
    for n, tid, author in sorted(reposted, reverse=True)[:repost_tweets]:
        if len(found) >= need:
            break
        if not open_page(f"reposts of {tid}", f"https://x.com/{author}/status/{tid}/retweets"):
            continue
        before = len(found)
        _collect_user_cells(page, human, found, exclude, len(found) + quota)
        log.info(f"{acc_id} | Candidates from reposts of @{author}'s tweet ({n} reposts): +{len(found) - before}")

    # 3) home timeline
    if len(found) < need and open_page("home timeline", "https://x.com/home"):
        before = len(found)
        _scroll_collect(page, human, found, exclude, len(found) + quota, 3)
        report("home timeline", before)

    # 4) repliers
    for url in random.sample(reply_urls, min(2, len(reply_urls))):
        if len(found) >= need:
            break
        if not open_page("replies", url):
            continue
        before = len(found)
        _scroll_collect(page, human, found, exclude, len(found) + quota, 3, skip_first=True)
        report("replies", before)

    # 5) people search
    if len(found) < need and open_page("people search", search_url(pick_query(cfg), live=False) + "&f=user"):
        before = len(found)
        _collect_user_cells(page, human, found, exclude, need, scrolls=2)
        report("people search", before)

    random.shuffle(found)
    return found


def _exclusions(cfg, state):
    """Handles that must never be followed: our own accounts, the manager, already seen/followed."""
    ex = {str(h).lower() for h in (state.get("seen_users") or [])}
    ex |= {str(h).lower() for h in (state.get("followed_handles") or [])}
    try:
        ex |= {str(a.get("handle", "")).lstrip("@").lower() for a in load_accounts().values()}
    except Exception:
        pass
    ex.add(str(cfg.get("manager_handle", "")).lstrip("@").lower())
    with _registry_lock:
        ex |= load_global_followed()
    ex.discard("")
    return ex


# ---------------------------------------------------------------------------
# Follow pass
# ---------------------------------------------------------------------------
def _follow_one(page, human, handle, cfg, acc_id):
    """Visit a profile, check eligibility, follow. Returns (followed_handle|None, status)."""
    f = cfg.get("follow", {})
    ok, status = open_profile(page, handle, cfg)
    if not ok:
        log.info(f"{acc_id} | Skip @{handle}: {status}")
        return None, status
    human.pause(2.0, 5.0)
    page.mouse.wheel(0, random.randint(200, 700))
    human.pause(1.5, 4.0)
    profile = read_profile(page, handle)
    reason = ineligible_reason(profile, cfg)
    if reason:
        fol = profile.get("followers")
        log.info(f"{acc_id} | Skip @{handle}: {reason} (followers={'?' if fol is None else fol})")
        return None, f"skip:{reason}"
    if random.random() < float(f.get("visit_only_chance", 0.25)):
        log.info(f"{acc_id} | Visit only @{handle} (no follow)")
        return None, "visit-only"
    human.mouse_wander(page)
    res, msg = follow_user(page, human, handle)
    if res and msg == "followed":
        log.info(f"{acc_id} | ✓ Followed @{handle} (followers={profile.get('followers')})")
        return handle, msg
    if res:  # already-following: counts as seen, not as a new follow
        log.info(f"{acc_id} | @{handle}: {msg}")
        return None, msg
    log.warning(f"{acc_id} | ✗ Follow @{handle} failed: {msg}")
    return None, msg


def follow_pass(page, acc_id, cfg, state, human, max_follows):
    """Find active crypto accounts and follow up to `max_follows` of them. Returns (followed, status)."""
    f = cfg.get("follow", {})
    budget = follow_budget(state, cfg)
    want = min(int(max_follows), budget)
    if want <= 0:
        return 0, "no-budget"
    log.info(f"{acc_id} | Follow pass: target {want} (budget left today: {budget})")

    exclude = _exclusions(cfg, state)
    candidates = collect_candidates(page, acc_id, cfg, human, exclude, need=max(want * 6, 15))
    if not candidates:
        log.info(f"{acc_id} | No follow candidates found")
        return 0, "no-candidates"
    log.info(f"{acc_id} | {len(candidates)} candidates collected")

    lo_delay, hi_delay = (int(x) for x in f.get("delay_between_s", [90, 300]))
    stats, followed = {}, 0
    seen = set(state.get("seen_users") or [])
    max_visits = max(want * 6, 12)  # hard cap on profile visits per pass

    for visits, handle in enumerate(candidates):
        if followed >= want or follow_budget(state, cfg) <= 0 or visits >= max_visits:
            break
        try:
            got, status = _follow_one(page, human, handle, cfg, acc_id)
        except Exception as e:
            log.warning(f"{acc_id} | Error following @{handle}: {e}")
            got, status = None, "error"
        key = status if status.startswith("skip:") else status.split(":")[0]
        stats[key] = stats.get(key, 0) + 1

        if not status.startswith(_TRANSIENT) and status != "error":
            seen.add(handle)
        if status == "follow-blocked":
            state["follow_blocked_until"] = time.time() + 6 * 3600
            log.error(f"{acc_id} | X is blocking follows - pausing follows for 6h")
            save_state(acc_id, state)
            break
        if status in ("login-wall", "challenge"):
            log.error(f"{acc_id} | Session problem while following ({status}) - stopping pass")
            break
        if got:
            followed += 1
            state.setdefault("followed_handles", []).append(got)
            state["done"]["follows"] = state["done"].get("follows", 0) + 1
            with _registry_lock:
                reg = load_global_followed()
                reg.add(got.lower())
                save_global_followed(reg)
            human.pause(max(20, int(lo_delay * 0.6)), hi_delay)
        else:
            human.pause(3, 9)  # cheap pause after skipped profiles
        state["seen_users"] = sorted(seen)[-3000:]
        save_state(acc_id, state)

    summary = ", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "none"
    log.info(f"{acc_id} | Follow pass done: +{followed} (today {state['done'].get('follows', 0)}) | {summary}")
    return followed, "ok"


def browse_crypto_search(page, acc_id, cfg, state, human, budget_s, start):
    """Browse latest crypto tweets (f=live) with light liking within daily limits."""
    ok, gstatus = resilience.goto_with_retry(
        page, search_url(pick_query(cfg), True), cfg
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


def crypto_activity(page, acc_id, cfg, state, human, budget_s):
    """Daily crypto activity: search browse + slow follows, within time budget."""
    f = cfg.get("follow", {})
    w = cfg.get("warmup", {})
    start = time.time()
    if w.get("browse_probability", 0.8) > random.random():
        browse_crypto_search(page, acc_id, cfg, state, human, budget_s * 0.5, start)

    if f.get("enabled", True) and follow_budget(state, cfg) > 0:
        per_session = f.get("per_session", [1, 3])
        follow_pass(page, acc_id, cfg, state, human, randint(*per_session))
