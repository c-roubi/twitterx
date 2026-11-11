# -*- coding: utf-8 -*-
"""حصاد مرشّحين للمتابعة: مخزن مشترك يُملأ من المتفاعلين الحقيقيين بدل بحث الأشخاص السيّئ.

لماذا؟ بحث x.com/search?f=user يُظهر الحسابات الموثّقة والكبيرة أولاً، فمعظم المرشّحين يُرفضون.
الأفضل: من يُعيد تغريد (reposts) تغريدة كريبتو شعبية = جمهور حقيقي صغير مناسب للمتابعة.
ومن متابعي حساب مؤثّر كريبتو = نفس الفكرة. نجمعهم في مخزن مشترك يستهلكه كل الحسابات.

طبقتان:
  - بيانات (مخزن JSON) قابلة للاختبار بالكامل: add/take/mark_bad/mark_followed/prune.
  - متصفح (سحب من الصفحة): scrape_handles_on_page/retweet_count_of/harvest_*.
"""
import json
import os
import re
import threading
import time
from urllib.parse import quote

from . import verify_cache
from .config import DATA_DIR, _atomic_save
from .logger import get_logger

log = get_logger("harvest")

POOL_PATH = os.path.join(DATA_DIR, "follow_pool.json")
POOL_MAX = 5000
BAD_TTL_S = 30 * 86400        # نتذكّر غير المؤهّل شهراً حتى لا نعيد زيارته بكل الحسابات
TWEET_TTL_S = 7 * 86400       # لا نعيد حصاد نفس التغريدة قبل أسبوع
_lock = threading.RLock()

# محدّدات بسيطة (مستقلة عن selectors.py لتبقى الوحدة قابلة للاختبار بصفحات وهمية)
_USER_CELL = '[data-testid="UserCell"]'
_TWEET_ARTICLE = 'article[data-testid="tweet"], article[role="article"]'
_RETWEET_BTN = '[data-testid="retweet"], [data-testid="repost"]'
_HANDLE_RE = re.compile(r"^/([A-Za-z0-9_]{1,15})$")
# أسماء مسارات ليست حسابات
_RESERVED = {"home", "explore", "search", "notifications", "messages", "i", "settings",
             "compose", "hashtag", "status", "login", "intent", "tos", "privacy"}


def handle_from_href(href):
    """اسم حساب من رابط بروفايل، أو None (يتجاهل /status و/i والمسارات المحجوزة)."""
    if not href:
        return None
    h = href.strip()
    for pre in ("https://x.com", "http://x.com", "https://twitter.com", "http://twitter.com"):
        if h.startswith(pre):
            h = h[len(pre):]
    m = _HANDLE_RE.match(h)
    if not m:
        return None
    name = m.group(1)
    return None if name.lower() in _RESERVED else name


def parse_count(text):
    """'25', '1.2K', '3.4M' → عدد صحيح، أو 0."""
    if not text:
        return 0
    m = re.search(r"([\d][\d.,]*)\s*([KMB])?", str(text), re.I)
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    return int(num * {"k": 1e3, "m": 1e6, "b": 1e9}.get((m.group(2) or "").lower(), 1))


# ---------------------------------------------------------------- طبقة البيانات
def _load():
    try:
        with open(POOL_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("handles", {})
            d.setdefault("bad", {})
            d.setdefault("tweets", {})
            return d
    except (OSError, ValueError):
        pass
    return {"handles": {}, "bad": {}, "tweets": {}, "v": 1}


def _save(d):
    try:
        os.makedirs(os.path.dirname(POOL_PATH), exist_ok=True)
        _atomic_save(d, POOL_PATH)
    except OSError as e:
        log.warning("تعذّر حفظ مخزن المرشّحين: {}".format(e))


def _prune(d, now=None):
    now = now or time.time()
    d["bad"] = {h: t for h, t in d["bad"].items() if now - t < BAD_TTL_S}
    d["tweets"] = {t: ts for t, ts in d["tweets"].items() if now - ts < TWEET_TTL_S}
    if len(d["handles"]) > POOL_MAX:   # أسقط الأقدم
        for h in sorted(d["handles"], key=lambda k: d["handles"][k].get("at", 0))[:len(d["handles"]) - POOL_MAX]:
            d["handles"].pop(h, None)
    return d


def add_candidates(handles, source="", tweet_id=None, own_handle=None):
    """يضيف أسماء للمخزن (يتخطّى المكرّر وغير المؤهّل سابقاً وحساب صاحب الجلسة). يرجع عدد المضاف."""
    now = time.time()
    own = (own_handle or "").lower()
    added = 0
    with _lock:
        d = _load()
        for h in handles:
            if not h:
                continue
            h = h.lstrip("@")
            low = h.lower()
            if low == own or h in d["handles"] or low in {k.lower() for k in d["bad"]}:
                continue
            d["handles"][h] = {"src": source, "tweet": tweet_id, "at": now}
            added += 1
        if added or tweet_id:
            if tweet_id:
                d["tweets"][str(tweet_id)] = now
            _prune(d, now)
            _save(d)
    return added


def take_candidates(exclude=(), limit=40):
    """أسماء جاهزة للمتابعة (الأحدث أولاً) ليست في exclude ولا في قائمة غير المؤهّل. لا تحذف من المخزن."""
    exclude = {str(x).lower() for x in (exclude or ())}
    with _lock:
        d = _load()
        bad = {k.lower() for k in d["bad"]}
        items = sorted(d["handles"].items(), key=lambda kv: kv[1].get("at", 0), reverse=True)
    out = [h for h, _ in items if h.lower() not in exclude and h.lower() not in bad]
    return out[:limit]


def remove(handle):
    """يحذف اسماً من المخزن (استُهلك: تُوبع أو أصبح معروفاً)."""
    with _lock:
        d = _load()
        if d["handles"].pop(handle, None) is not None:
            _save(d)


def mark_bad(handle, source=""):
    """يسجّل اسماً كغير مؤهّل (موثّق/خارج النطاق/بلا بايو) فلا يزوره أي حساب شهراً."""
    with _lock:
        d = _load()
        d["handles"].pop(handle, None)
        d["bad"][handle] = time.time()
        _prune(d)
        _save(d)


def recently_harvested(tweet_id):
    with _lock:
        return str(tweet_id) in _load()["tweets"]


def pool_size():
    with _lock:
        return len(_load()["handles"])


def stats():
    with _lock:
        d = _load()
        return {"pool": len(d["handles"]), "bad": len(d["bad"]), "tweets": len(d["tweets"])}


# ---------------------------------------------------------------- طبقة المتصفح
_VERIFIED_ICON = '[data-testid="icon-verified"], [data-testid="icon-verified-badge"], svg[aria-label="Verified account"]'


def _cell_verified(cell, handle):
    """موثّق؟ بيانات X (GraphQL) أولاً، وإلا شارة اسم الكاتب داخل الخلية (احتياط)."""
    v = verify_cache.lookup(handle)
    if v is not None:
        return v
    try:
        name = cell.locator('[data-testid="User-Name"]').first
        scope = name if name.count() > 0 else cell
        return scope.locator(_VERIFIED_ICON).count() > 0
    except Exception:
        return False


def scrape_handles_on_page(page, limit=30, skip_verified=False, cell_selector=None):
    """أسماء الحسابات من الخلايا الظاهرة حالياً (نتائج/متابعون/معيدو تغريد، أو كتّاب الاقتباسات).

    skip_verified: تخطّي الموثّقين وقت الجمع كي لا يمتلئ المخزن بحسابات ستُرفض لاحقاً.
    cell_selector: محدد الخلايا (الافتراضي خلايا المستخدمين؛ للاقتباسات خلايا التغريدات).
    """
    out = []
    try:
        cells = page.locator(cell_selector or _USER_CELL).all()
    except Exception:
        return out
    for cell in cells:
        if len(out) >= limit:
            break
        try:
            for link in cell.locator('a[href]').all():
                h = handle_from_href(link.get_attribute("href") or "")
                if h and h not in out:
                    if not (skip_verified and _cell_verified(cell, h)):
                        out.append(h)
                    break
        except Exception:
            continue
    return out


def _skip_verified(cfg):
    return bool((cfg.get("follow", {}) or {}).get("skip_verified", True))


def retweet_count_of(article):
    """عدد الريتويت لتغريدة (من aria-label لزرّ إعادة النشر)، أو 0."""
    try:
        btn = article.locator(_RETWEET_BTN).first
        if btn.count() == 0:
            return 0
        return parse_count(btn.get_attribute("aria-label") or btn.inner_text() or "")
    except Exception:
        return 0


def _wait_cells(page, timeout_ms=8000):
    end = time.time() + timeout_ms / 1000.0
    while time.time() < end:
        try:
            if page.locator(_USER_CELL).first.count() > 0:
                return True
        except Exception:
            pass
        page.wait_for_timeout(500)
    return False


def harvest_reposters(page, human, author, tweet_id, cfg, own_handle=None):
    """يفتح صفحة معيدي التغريد ويسحب أسماءهم إلى المخزن. يرجع عدد المضاف."""
    hv = (cfg.get("follow", {}).get("harvest") or {})
    per = int(hv.get("per_target", 15))
    try:
        from . import resilience
        ok, st = resilience.goto_with_retry(page, "https://x.com/{}/status/{}/retweets".format(author, tweet_id), cfg)
    except Exception as e:
        return 0
    if not ok:
        log.info("حصاد: تعذّر فتح معيدي {} ({})".format(tweet_id, st))
        add_candidates([], tweet_id=tweet_id, own_handle=own_handle)  # علّمها حتى لا نعيد المحاولة فوراً
        return 0
    page.wait_for_timeout(1500)
    if not _wait_cells(page):
        add_candidates([], tweet_id=tweet_id, own_handle=own_handle)
        return 0
    for _ in range(3):   # تمرير لجلب المزيد
        page.mouse.wheel(0, 1200)
        human.pause(1.0, 2.5)
    handles = scrape_handles_on_page(page, limit=per, skip_verified=_skip_verified(cfg))
    n = add_candidates(handles, source="reposts", tweet_id=tweet_id, own_handle=own_handle)
    log.info("حصاد معيدي التغريد {}: +{} مرشّح (المخزن {})".format(tweet_id, n, pool_size()))
    return n


def harvest_quoters(page, human, author, tweet_id, cfg, own_handle=None):
    """يفتح صفحة الاقتباسات ويسحب كتّاب تغريدات الاقتباس إلى المخزن. يرجع عدد المضاف."""
    hv = (cfg.get("follow", {}).get("harvest") or {})
    per = int(hv.get("per_target", 15))
    try:
        from . import resilience
        ok, st = resilience.goto_with_retry(page, "https://x.com/{}/status/{}/quotes".format(author, tweet_id), cfg)
    except Exception:
        return 0
    if not ok:
        return 0
    page.wait_for_timeout(2000)
    end = time.time() + 8
    while time.time() < end:
        try:
            if page.locator(_TWEET_ARTICLE).first.count() > 0:
                break
        except Exception:
            pass
        page.wait_for_timeout(500)
    for _ in range(3):
        page.mouse.wheel(0, 1200)
        human.pause(1.0, 2.5)
    handles = scrape_handles_on_page(page, limit=per, skip_verified=_skip_verified(cfg),
                                     cell_selector=_TWEET_ARTICLE)
    n = add_candidates(handles, source="quotes", own_handle=own_handle)
    log.info("حصاد كتّاب الاقتباس {}: +{} مرشّح (المخزن {})".format(tweet_id, n, pool_size()))
    return n


def harvest_followers(page, human, handle, cfg, own_handle=None):
    """يسحب أسماءً من متابعي حساب مؤثّر. يرجع عدد المضاف."""
    hv = (cfg.get("follow", {}).get("harvest") or {})
    per = int(hv.get("per_target", 15))
    try:
        from . import resilience
        ok, st = resilience.goto_with_retry(page, "https://x.com/{}/followers".format(handle), cfg)
    except Exception:
        return 0
    if not ok or not _wait_cells(page):
        return 0
    for _ in range(3):
        page.mouse.wheel(0, 1200)
        human.pause(1.0, 2.5)
    handles = scrape_handles_on_page(page, limit=per, skip_verified=_skip_verified(cfg))
    n = add_candidates(handles, source="followers:" + handle, own_handle=own_handle)
    log.info("حصاد متابعي @{}: +{} مرشّح (المخزن {})".format(handle, n, pool_size()))
    return n


def run_harvest(page, acc_id, cfg, state, human, budget_s, own_handle=None):
    """المنتِج الرئيسي: يبحث عن تغريدات كريبتو شعبية ويحصد معيديها (وربما متابعي مؤلّفيها).

    يعمل على صفحة بحث مباشر للكريبتو، يفحص التغريدات، ويفتح معيدي الشعبية منها.
    """
    from .grow import pick_query
    from .browser import is_login_wall
    hv = (cfg.get("follow", {}).get("harvest") or {})
    if not hv.get("enabled", True):
        return 0
    min_rt = int(hv.get("min_reposts", 20))
    lo, hi = hv.get("targets_per_session", [1, 2])
    import random
    max_targets = random.randint(int(lo), int(hi)) if hi >= lo else 1
    start = time.time()
    try:
        from . import resilience
        ok, st = resilience.goto_with_retry(page, "https://x.com/search?q={}&f=live".format(quote(pick_query(cfg))), cfg)
    except Exception:
        return 0
    if not ok:
        return 0
    page.wait_for_timeout(3000)
    added = targets = 0
    scrolls = 0
    while targets < max_targets and time.time() - start < budget_s:
        if is_login_wall(page):
            return added
        try:
            articles = page.locator(_TWEET_ARTICLE).all()
        except Exception:
            articles = []
        picked = None
        for art in articles:
            try:
                if retweet_count_of(art) < min_rt:
                    continue
                link = art.locator('a[href*="/status/"]').first
                href = link.get_attribute("href") if link.count() else ""
                m = re.search(r"/([A-Za-z0-9_]{1,15})/status/(\d+)", href or "")
                if not m or recently_harvested(m.group(2)):
                    continue
                picked = (m.group(1), m.group(2))
                break
            except Exception:
                continue
        if picked:
            added += harvest_reposters(page, human, picked[0], picked[1], cfg, own_handle=own_handle)
            if hv.get("include_quoters", True):
                added += harvest_quoters(page, human, picked[0], picked[1], cfg, own_handle=own_handle)
            targets += 1
            human.pause(2.0, 5.0)
            # ارجع للبحث للبحث عن هدف آخر
            if targets < max_targets:
                try:
                    resilience.goto_with_retry(page, "https://x.com/search?q={}&f=live".format(quote(pick_query(cfg))), cfg)
                    page.wait_for_timeout(2500)
                except Exception:
                    break
        else:
            page.mouse.wheel(0, random.randint(900, 1600))
            human.pause(1.5, 3.5)
            scrolls += 1
            if scrolls > 12:
                break
    return added
