# -*- coding: utf-8 -*-
"""النمو والإحماء اليومي: اكتشاف مهتمين بالكريبتو + متابعات بطيئة عشوائية + تصفح بحث كريبتو."""
import random
import re
import time
from urllib.parse import quote

from .actions import (
    TWEET_ARTICLE, LIKE_BTN, UNLIKE_BTN, USER_CELL,
    follow_user, open_profile, read_profile,
)
from .selectors import VERIFIED_ICON, RETWEET_BTN, STATUS_LINK
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
    """قائمة استعلامات الكريبتو من الإعدادات (أو الافتراضية)."""
    f = cfg.get("follow", {})
    qs = f.get("queries") or DEFAULT_QUERIES
    return [str(q).strip() for q in qs if q is not None and str(q).strip()]


def pick_query(cfg):
    """اختيار استعلام كريبتو عشوائي."""
    qs = crypto_queries(cfg)
    return random.choice(qs) if qs else "bitcoin"


def _handle_from_href(href):
    """استخراج اسم الحساب من رابط بروفايل (نقّي للاختبار). يتجاهل /status/ و /i/."""
    if not href:
        return None
    h = href.strip()
    for pre in ("https://x.com", "http://x.com", "https://twitter.com", "http://twitter.com"):
        if h.startswith(pre):
            h = h[len(pre):]
    m = re.match(r"^/([A-Za-z0-9_]{1,15})$", h)
    return m.group(1) if m else None


def _cell_is_verified(cell):
    """هل خلية المستخدم في نتائج البحث تخص حساباً موثّقاً؟ (شارة التوثيق داخل الخلية)."""
    try:
        return cell.locator(VERIFIED_ICON).first.count() > 0
    except Exception:
        return False


STRICT_USER_CELL = '[data-testid="UserCell"]'
_TWEET_IN_CELL = '[data-testid="tweet"], [data-testid="tweetText"], article'


def _cell_is_tweet(cell):
    """خلية تحتوي تغريدة (مثل اقتباس) وليست خلية مستخدم."""
    try:
        return cell.locator(_TWEET_IN_CELL).count() > 0
    except Exception:
        return False


def _handle_in_cell(cell, seen, handles):
    """استخراج أول اسم حساب صالح وجديد من خلية مستخدم."""
    try:
        for link in cell.locator("a[href]").all():
            handle = _handle_from_href(link.get_attribute("href") or "")
            if handle and handle not in seen and handle not in handles:
                return handle
    except Exception:
        pass
    return None


def find_candidates(page, human, seen_users=(), limit=30,
                    skip_verified=False, max_scrolls=0, strict_users=False):
    """جمع مرشحين من نتائج بحث الأشخاص (f=user) — أسماء فريدة لم نرها من قبل.

    skip_verified: تخطّي الحسابات الموثّقة مبكّراً (على مستوى الخلية) حتى لا تُهدر
                   ميزانية اليوم بزيارة بروفايلات موثّقة ثم تخطّيها لاحقاً. نتائج بحث
                   X تُظهر الموثّقين أولاً، لذا هذا الفلتر ضروري لإيجاد حسابات طبيعية.
    max_scrolls: عدد مرات التمرير لتحميل مزيد من النتائج أثناء الجمع (0 = بلا تمرير).
    strict_users: اعتماد خلايا المستخدمين الحقيقية (UserCell) فقط — لقوائم الريتويترز حتى
                  لا تُسحب تغريدات الاقتباس. وتُتخطّى دائماً أي خلية تحتوي تغريدة.
    """
    seen = set(seen_users or ())
    handles = []
    skipped_verified = 0
    rounds = max(1, int(max_scrolls) + 1)
    for r in range(rounds):
        try:
            for cell in page.locator(STRICT_USER_CELL if strict_users else USER_CELL).all():
                if len(handles) >= limit:
                    break
                if _cell_is_tweet(cell):
                    continue
                if skip_verified and _cell_is_verified(cell):
                    skipped_verified += 1
                    continue
                handle = _handle_in_cell(cell, seen, handles)
                if handle:
                    handles.append(handle)
        except Exception:
            pass
        if len(handles) >= limit or r >= rounds - 1:
            break
        # تمرير لتحميل مزيد من نتائج الأشخاص (للوصول لغير الموثّقين الأعمق في القائمة)
        try:
            page.mouse.wheel(0, random.randint(900, 1600))
            if human is not None:
                human.pause(1.5, 3.5)
            else:
                page.wait_for_timeout(1500)
        except Exception:
            break
    if skip_verified and skipped_verified:
        log.info(f"تخطّي {skipped_verified} حساب موثّق أثناء جمع المرشحين | غير موثّق: {len(handles)}")
    return handles


def profile_eligible(profile, cfg):
    """شروط أهلية حساب للمتابعة (دالة نقية للاختبار)."""
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
    """كم متابعة متبقية اليوم؟"""
    f = cfg.get("follow", {})
    if not f.get("enabled", True):
        return 0
    caps = state.get("caps", {})
    done = state.get("done", {})
    return max(0, int(caps.get("follows", 0)) - int(done.get("follows", 0)))


def _follow_one(page, human, handle, cfg, state, acc_id):
    """زيارة بروفايل، فحص الأهلية، وربما متابعة (نسبة «زيارة فقط» حسب الإعدادات)."""
    f = cfg.get("follow", {})
    visit_only = random.random() < float(f.get("visit_only_chance", 0.25))
    ok, status = open_profile(page, handle, cfg)
    if not ok:
        log.info(f"{acc_id} | تخطي @{handle}: {status}")
        return None, status
    human.pause(2.0, 5.0)
    page.mouse.wheel(0, random.randint(200, 700))
    human.pause(1.5, 4.0)
    profile = read_profile(page, handle)
    if visit_only:
        log.info(f"{acc_id} | زيارة فقط لـ @{handle} (بدون متابعة)")
        return None, "visit-only"
    if not profile_eligible(profile, cfg):
        reason = "verified" if profile.get("verified") else (
            "no-bio" if not (profile.get("bio") or "").strip() else "followers-range")
        log.info(f"{acc_id} | تخطي @{handle}: غير مؤهل ({reason})")
        return None, f"not-eligible:{reason}"
    human.mouse_wander(page)
    res, msg = follow_user(page, human, handle)
    if res:
        log.info(f"{acc_id} | ✓ متابعة @{handle}")
        return handle, msg
    log.warning(f"{acc_id} | ✗ متابعة @{handle} فشلت: {msg}")
    return None, f"follow-failed:{msg}"


def run_follow_pass(page, acc_id, cfg, state, human, max_follows):
    """متابعات بطيئة وعشوائية: بحث أشخاص → فحص → متابعة، ضمن ميزانية اليوم."""
    f = cfg.get("follow", {})
    budget = follow_budget(state, cfg)
    if budget <= 0 or max_follows <= 0:
        return 0, "no-budget"
    query = pick_query(cfg)
    log.info(f"{acc_id} | بحث كريبتو للمتابعة: \"{query}\" (متبقي اليوم: {budget})")
    ok, gstatus = resilience.goto_with_retry(
        page, f"https://x.com/search?q={quote(query)}&f=user", cfg
    )
    if not ok:
        log.warning(f"{acc_id} | تعذّر فتح بحث المتابعات: {gstatus}")
        return 0, f"goto-error:{gstatus}"
    page.wait_for_timeout(3500)
    for _ in range(20):
        if page.locator(USER_CELL).first.count() > 0:
            break
        if is_login_wall(page):
            return 0, "login-wall"
        page.wait_for_timeout(1000)
    human.pause(2.0, 5.0)
    seen_users = set(state.get("seen_users", []) or [])
    # تخطّي الموثّقين عند الجمع (حسب الإعداد) + تمرير لجلب حسابات طبيعية كافية
    skip_verified = bool(f.get("skip_verified", True))
    max_scrolls = int(f.get("candidate_scrolls", 6))
    candidates = find_candidates(
        page, human, seen_users=seen_users, limit=40,
        skip_verified=skip_verified, max_scrolls=max_scrolls,
    )
    if not candidates:
        log.warning(f"{acc_id} | لم يُعثر على حسابات غير موثّقة في \"{query}\" - جرّب كلمات بحث أضيق")
        return 0, "no-candidates"
    random.shuffle(candidates)
    followed = 0
    lo_delay, hi_delay = (int(x) for x in f.get("delay_between_s", [90, 300]))
    for handle in candidates:
        if followed >= max_follows or follow_budget(state, cfg) <= 0:
            break
        got, msg = _follow_one(page, human, handle, cfg, state, acc_id)
        seen_users.add(handle)
        if got:
            state.setdefault("followed_handles", []).append(handle)
            state["done"]["follows"] += 1
            followed += 1
            
        # ✅ تم التعديل: تقييد حجم قائمة seen_users لمنع تضخم ملف state.json وإبطاء البوت
        seen_list = list(seen_users)
        if len(seen_list) > 3000:
            seen_list = seen_list[-3000:]
        state["seen_users"] = sorted(seen_list)
        
        save_state(acc_id, state)
        human.pause(max(20, int(lo_delay * 0.6)), hi_delay)
    log.info(f"{acc_id} | جولة المتابعات: +{followed} (إجمالي اليوم {state['done']['follows']})")
    return followed, "ok"


# ---------------------------------------------------------------------------
# مصدر متابعة إضافي: الناس الذين أعادوا نشر (ريتويت) تغريدات كريبتو متفاعلة
# ---------------------------------------------------------------------------
def _leading_count(text):
    """استخراج أول رقم (مع لاحقة K/M/B) من نص مثل «123 reposts» أو «2.4K»."""
    m = re.search(r"(\d[\d,\.]*)\s*([KMB])?", text or "", re.I)
    if not m:
        return 0
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((m.group(2) or "").upper(), 1)
    return int(num * mult)


def _retweet_count_of(article):
    """عدد إعادات النشر لتغريدة من زر إعادة النشر (aria-label)."""
    try:
        btn = article.locator(RETWEET_BTN).first
        if btn.count() == 0:
            return 0
        return _leading_count(btn.get_attribute("aria-label") or "")
    except Exception:
        return 0


def _status_ref_of(article):
    """استخراج (المقبض، معرّف التغريدة) من رابط /<handle>/status/<id> داخل المقال."""
    try:
        for link in article.locator(STATUS_LINK).all():
            href = link.get_attribute("href") or ""
            m = re.search(r"^/?(?:https?://(?:x|twitter)\.com)?/([A-Za-z0-9_]{1,15})/status/(\d+)", href)
            if m:
                return m.group(1), m.group(2)
    except Exception:
        pass
    return None, None


def _collect_engaged_tweets(page, cfg, seen_ids, limit=6):
    """جمع تغريدات كريبتو ظاهرة لديها عدد إعادات نشر >= الحد الأدنى (لم تُعالَج سابقاً)."""
    f = cfg.get("follow", {})
    min_rt = int(f.get("min_retweets", 2))
    found = []
    try:
        for art in page.locator(TWEET_ARTICLE).all():
            if len(found) >= limit:
                break
            handle, tid = _status_ref_of(art)
            if not tid or tid in seen_ids:
                continue
            if _retweet_count_of(art) < min_rt:
                continue
            found.append((handle, tid))
            seen_ids.add(tid)
    except Exception:
        pass
    return found


def run_retweeter_follow_pass(page, acc_id, cfg, state, human, max_follows):
    """متابعة الأشخاص الذين أعادوا نشر تغريدات كريبتو متفاعلة أثناء التصفح.

    الفكرة: التغريدات التي لها إعادات نشر = محتوى كريبتو متفاعل، ومن أعادوا نشرها
    حسابات مهتمة فعلاً — مصدر متابعة أدق من البحث العشوائي. نطبّق نفس شروط الأهلية
    (تخطّي الموثّقين + بايو + مدى المتابعين + عدم التكرار + الميزانية اليومية).
    """
    f = cfg.get("follow", {})
    budget = follow_budget(state, cfg)
    if budget <= 0 or max_follows <= 0:
        return 0, "no-budget"

    # نبدأ من نتائج بحث كريبتو مباشر (f=live) — تغريدات كريبتو حديثة بتفاعل
    ok, gstatus = resilience.goto_with_retry(
        page, f"https://x.com/search?q={quote(pick_query(cfg))}&f=live", cfg
    )
    if not ok:
        log.warning(f"{acc_id} | تعذّر فتح بحث الريتويترز: {gstatus}")
        return 0, f"goto-error:{gstatus}"
    page.wait_for_timeout(3500)
    for _ in range(15):
        if page.locator(TWEET_ARTICLE).first.count() > 0:
            break
        if is_login_wall(page):
            return 0, "login-wall"
        page.wait_for_timeout(1000)

    rt_seen = set(state.get("rt_seen_tweets", []) or [])
    tweets_per_pass = int(f.get("retweeter_tweets_per_pass", 3))
    # نمرّر قليلاً لجمع تغريدات متفاعلة
    engaged = []
    for _ in range(max(1, tweets_per_pass)):
        engaged += _collect_engaged_tweets(page, cfg, rt_seen, limit=tweets_per_pass)
        if len(engaged) >= tweets_per_pass:
            break
        try:
            page.mouse.wheel(0, random.randint(900, 1600))
            human.pause(1.5, 3.5)
        except Exception:
            break
    if not engaged:
        log.info(f"{acc_id} | لا تغريدات كريبتو بإعادات نشر كافية الآن (ريتويترز)")
        return 0, "no-engaged-tweets"

    seen_users = set(state.get("seen_users", []) or [])
    skip_verified = bool(f.get("skip_verified", True))
    max_scrolls = int(f.get("candidate_scrolls", 6))
    lo_delay, hi_delay = (int(x) for x in f.get("delay_between_s", [90, 300]))
    followed = 0

    for handle, tid in engaged[:tweets_per_pass]:
        if followed >= max_follows or follow_budget(state, cfg) <= 0:
            break
        rt_url = f"https://x.com/{handle or 'i'}/status/{tid}/retweets"
        ok, gstatus = resilience.goto_with_retry(page, rt_url, cfg)
        if not ok:
            log.info(f"{acc_id} | تخطّي ريتويترز {tid}: {gstatus}")
            continue
        page.wait_for_timeout(2500)
        for _ in range(12):
            if page.locator(STRICT_USER_CELL).first.count() > 0:
                break
            if is_login_wall(page):
                return followed, "login-wall"
            page.wait_for_timeout(1000)
        human.pause(1.5, 4.0)
        candidates = find_candidates(
            page, human, seen_users=seen_users, limit=25,
            skip_verified=skip_verified, max_scrolls=max_scrolls,
            strict_users=True,
        )
        random.shuffle(candidates)
        log.info(f"{acc_id} | ريتويترز التغريدة {tid}: {len(candidates)} مرشح غير موثّق")
        for cand in candidates:
            if followed >= max_follows or follow_budget(state, cfg) <= 0:
                break
            got, msg = _follow_one(page, human, cand, cfg, state, acc_id)
            seen_users.add(cand)
            if got:
                state.setdefault("followed_handles", []).append(cand)
                state["done"]["follows"] += 1
                followed += 1
            seen_list = list(seen_users)
            if len(seen_list) > 3000:
                seen_list = seen_list[-3000:]
            state["seen_users"] = sorted(seen_list)
            save_state(acc_id, state)
            human.pause(max(20, int(lo_delay * 0.6)), hi_delay)

    # تقييد حجم قائمة التغريدات المعالَجة في الحالة
    rt_list = list(rt_seen)
    if len(rt_list) > 500:
        rt_list = rt_list[-500:]
    state["rt_seen_tweets"] = rt_list
    save_state(acc_id, state)
    log.info(f"{acc_id} | جولة الريتويترز: +{followed} (إجمالي اليوم {state['done']['follows']})")
    return followed, "ok"


def browse_crypto_search(page, acc_id, cfg, state, human, budget_s, start):
    """تصفح أحدث تغريدات كريبتو (f=live) مع لايك خفيف ضمن حدود اليوم."""
    ok, gstatus = resilience.goto_with_retry(
        page, f"https://x.com/search?q={quote(pick_query(cfg))}&f=live", cfg
    )
    if not ok:
        log.warning(f"{acc_id} | تعذّر فتح بحث الكريبتو: {gstatus}")
        return
    page.wait_for_timeout(3500)
    scrolls = 0
    while time.time() - start < budget_s:
        human.pause(2.0, 6.0)
        page.mouse.wheel(0, random.randint(500, 1500))
        
        # ✅ تم التعديل: إعطاء مهلة لتويتر لجلب التغريدات الجديدة بعد التمرير
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
                
                # ✅ تم التعديل: التأكد أن التغريدة لم يتم الإعجاب بها مسبقاً لمنع إلغاء اللايك بالخطأ
                if art.locator(UNLIKE_BTN).first.count() > 0:
                    continue
                    
                like_btn = art.locator(LIKE_BTN).first
                if like_btn.count() > 0 and like_btn.is_visible():
                    like_btn.hover()
                    human.pause(0.7, 2.0)
                    like_btn.click()
                    page.wait_for_timeout(1200)
                    state["done"]["likes"] += 1
                    save_state(acc_id, state)
                    log.info(f"{acc_id} | لايك كريبتو #{state['done']['likes']}")
            except Exception:
                pass
            human.pause(3.0, 9.0)
            
        if not likes_left and time.time() - start > budget_s * 0.5:
            break


def crypto_activity(page, acc_id, cfg, state, human, budget_s):
    """النشاط الكريبتو اليومي: تصفح بحث + متابعات بطيئة، ضمن ميزانية وقت."""
    f = cfg.get("follow", {})
    w = cfg.get("warmup", {})
    start = time.time()
    if w.get("browse_probability", 0.8) > random.random():
        browse_crypto_search(page, acc_id, cfg, state, human, budget_s * 0.5, start)
    if f.get("enabled", True) and follow_budget(state, cfg) > 0:
        per_session = f.get("per_session", [1, 3])
        target = randint(*per_session)
        # توزيع مناصفة تقريباً: نصف من الريتويترز ونصف من بحث الأشخاص
        rt_target = (target + random.randint(0, 1)) // 2
        done = 0
        if f.get("from_retweeters", True) and rt_target > 0 and follow_budget(state, cfg) > 0:
            got, _ = run_retweeter_follow_pass(page, acc_id, cfg, state, human, rt_target)
            done += got
        # الباقي من البحث (يشمل ما لم تُكمله جولة الريتويترز)
        remaining = max(0, target - done)
        if remaining > 0 and follow_budget(state, cfg) > 0:
            run_follow_pass(page, acc_id, cfg, state, human, remaining)