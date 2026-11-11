# -*- coding: utf-8 -*-
"""تغريدة كريبتو مولّدة بالذكاء الاصطناعي كل يوم أو يومين لكل حساب، مع هاشتاغات كريبتو رائجة (ترند)."""
import json
import os
import random
import re
import time

from . import actions, ai_comments, overlays, resilience, selectors
from .config import DATA_DIR, save_state
from .logger import get_logger

log = get_logger("daily_post")

TRENDING_CACHE = os.path.join(DATA_DIR, "trending_crypto.json")
TRENDING_TTL_S = 6 * 3600
DEFAULT_FALLBACK_TAGS = ["#Bitcoin", "#BTC", "#Crypto", "#Ethereum", "#ETH", "#Altcoins", "#Solana", "#Web3", "#DeFi"]
CRYPTO_WORDS = (
    "bitcoin", "btc", "crypto", "eth", "ethereum", "sol", "solana", "xrp", "doge", "bnb", "altcoin", "altseason",
    "defi", "web3", "nft", "token", "coin", "binance", "airdrop", "memecoin", "blockchain", "satoshi", "halving",
    "etf", "stablecoin", "usdt", "usdc", "hodl", "bullrun", "pepe", "ton", "ada", "avax", "sui", "layer2",
)
_TAG_RE = re.compile(r"#\w{2,30}", re.UNICODE)

_SYSTEM = (
    "You write original tweets for a regular crypto enthusiast on X (Twitter). Sound like a real person, not a brand "
    "or a bot: casual, concise, one clear thought (a market observation, opinion, question or lesson). "
    "No financial advice, no price guarantees, no 'buy now', no links, no @mentions, no emojis spam (max 1-2). "
    "Write in English. Stay under {limit} characters INCLUDING the hashtags. Output only the tweet text."
)
_ANGLES = [
    "a short market observation", "a question that invites replies", "a lesson learned from a past cycle",
    "a take on patience and risk management", "an opinion about the current narrative", "a short bullish-but-realistic thought",
    "a cautious thought about volatility", "something about on-chain activity or adoption", "a quick tip for newcomers",
]


def _is_crypto(text):
    low = (text or "").lower()
    words = set(re.split(r"[^a-z0-9]+", low))
    return any(w in words or (len(w) > 4 and w in low) for w in CRYPTO_WORDS)


def _load_cache():
    try:
        with open(TRENDING_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("ts", 0)) < TRENDING_TTL_S and d.get("tags"):
            return d["tags"]
    except Exception:
        pass
    return None


def _save_cache(tags):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TRENDING_CACHE, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "tags": tags}, f, ensure_ascii=False)
    except Exception:
        pass


def scrape_trending_crypto(page, human=None):
    """يقرأ صفحة الترند في X ويرجع الهاشتاغات/المواضيع المتعلقة بالكريبتو فقط (قائمة وسوم)."""
    ok, why = resilience.goto_with_retry(page, "https://x.com/explore/tabs/trending", {})
    if not ok:
        raise RuntimeError("تعذّر فتح صفحة الترند: " + str(why))
    page.wait_for_timeout(3500)
    tags = []
    for _ in range(3):
        try:
            for cell in page.locator('[data-testid="trend"]').all():
                txt = (cell.inner_text() or "").strip()
                if not txt or not _is_crypto(txt):
                    continue
                for t in _TAG_RE.findall(txt):
                    tags.append(t)
                if not _TAG_RE.search(txt):          # موضوع بلا هاشتاغ (مثل Bitcoin): حوّله لوسم إن كان كلمة واحدة
                    for line in txt.splitlines():
                        w = line.strip()
                        if re.fullmatch(r"\$?[A-Za-z0-9]{2,20}", w) and _is_crypto(w):
                            tags.append("#" + w.lstrip("$"))
                            break
        except Exception:
            pass
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(1200)
    seen, out = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def get_hashtags(page, cfg, human=None):
    """هاشتاغات كريبتو رائجة (كاش مشترك 6 ساعات بين الحسابات) مع بدائل ثابتة عند الفشل."""
    dp = cfg.get("daily_post", {})
    tags = _load_cache()
    if not tags and dp.get("use_trending", True):
        try:
            tags = scrape_trending_crypto(page, human)
            if tags:
                _save_cache(tags)
                log.info("هاشتاغات كريبتو رائجة: " + " ".join(tags[:8]))
        except Exception as e:
            log.warning("تعذّر جلب الترند: {} - تُستخدم الهاشتاغات الاحتياطية".format(str(e)[:100]))
    return tags or list(dp.get("fallback_hashtags") or DEFAULT_FALLBACK_TAGS)


def generate_post(cfg, tags):
    """توليد نص التغريدة عبر الذكاء الاصطناعي ثم إلحاق الهاشتاغات. يرجع None عند الفشل."""
    dp = cfg.get("daily_post", {})
    ai = cfg.get("comments", {}).get("ai", {})
    if not ai.get("enabled") or not ai.get("api_key"):
        log.warning("التغريدة اليومية تحتاج تفعيل الذكاء الاصطناعي ومفتاح API في الإعدادات")
        return None
    n = random.randint(*[int(x) for x in (dp.get("hashtags_per_post") or [1, 2])])
    chosen = random.sample(tags, min(n, len(tags)))
    tail = " ".join(chosen)
    limit = max(60, 280 - len(tail) - 1)
    system = _SYSTEM.format(limit=limit)
    user = "Angle: {}.\nTrending topics right now (for context, do NOT include hashtags yourself): {}\nWrite one tweet.".format(
        random.choice(_ANGLES), ", ".join(tags[:6]))
    text = ai_comments.generate_ai_comment(None, ai, cfg.get("net"), ignore_interval=True, system_prompt=system,
                                           user_msg=user, max_len=limit, max_tokens=120)
    if not text:
        return None
    text = _TAG_RE.sub("", text).replace("  ", " ").strip()      # لا هاشتاغات من النموذج: نحن نختارها
    if not text:
        return None
    return "{} {}".format(text, tail)


DIALOG_CANDIDATES = ['[role="dialog"][aria-modal="true"]', '[aria-modal="true"]', '[role="dialog"]']
MODAL_SEND_CANDIDATES = ['[data-testid="tweetButton"]', 'button[data-testid="tweetButton"]']
INLINE_SEND_CANDIDATES = ['[data-testid="tweetButtonInline"]', 'button[data-testid="tweetButtonInline"]']
_ERROR_TOAST = ("already said", "something went wrong", "try again", "limit", "حدث خطأ", "سبق", "حاول")


def _composer_scope(page):
    """نطاق المحرر الصحيح: نافذة الإنشاء (dialog) إن وُجدت، وإلا محرر الرئيسية.

    مهم: صفحة compose/post تفتح نافذة فوق الرئيسية، ومحرر الرئيسية (tweetTextarea_0 + tweetButtonInline)
    يبقى موجوداً خلفها. البحث في الصفحة كلها كان يلتقط محرر الرئيسية وزرّه بدل محرر النافذة.
    يرجع (نطاق، مرشحات زر النشر، اسم).
    """
    for sel in DIALOG_CANDIDATES:
        try:
            dlg = page.locator(sel).filter(has=page.locator(selectors.TEXTAREA)).last
            if dlg.count() > 0 and dlg.is_visible():
                return dlg, MODAL_SEND_CANDIDATES, "modal"
        except Exception:
            continue
    return page, INLINE_SEND_CANDIDATES, "inline"


def _send_button(scope, candidates, page, timeout_ms=5000):
    """زر النشر داخل النطاق فقط، بانتظار تفعيله."""
    deadline = time.time() + timeout_ms / 1000.0
    btn = None
    while time.time() < deadline:
        btn = selectors.first(scope, candidates)
        if btn is not None:
            try:
                if btn.is_enabled() and btn.get_attribute("aria-disabled") != "true":
                    return btn, True
            except Exception:
                pass
        page.wait_for_timeout(250)
    return btn, False


def _error_toast(page):
    try:
        for t in page.locator('[data-testid="toast"], [role="alert"]').all():
            txt = (t.inner_text() or "").lower()
            if any(w in txt for w in _ERROR_TOAST):
                return txt.strip()[:80]
    except Exception:
        pass
    return None


def _sent(page, ta, scope_kind, timeout_ms=10000):
    """تأكيد الإرسال: النافذة أُغلقت أو المحرر فرغ، ولا توجد رسالة خطأ من X."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        err = _error_toast(page)
        if err:
            return False, "x-error: " + err
        try:
            gone = ta.count() == 0 or not ta.is_visible()
            empty = not gone and not (ta.inner_text() or "").strip()
        except Exception:
            gone, empty = True, False
        if gone or empty:
            page.wait_for_timeout(800)          # مهلة قصيرة لظهور رسالة خطأ متأخرة
            err = _error_toast(page)
            return (False, "x-error: " + err) if err else (True, "posted")
        page.wait_for_timeout(300)
    return False, "unconfirmed"


def post_tweet(page, text, human):
    """نشر تغريدة جديدة. يكتب في نافذة الإنشاء ويضغط زرها هي (لا محرر/زر الرئيسية خلفها). يرجع (نجح، حالة)."""
    actions.dismiss_compose_dialog(page, human)
    ok, why = resilience.goto_with_retry(page, "https://x.com/compose/post", {})
    if not ok:
        return False, "open-failed"
    page.wait_for_timeout(2500)
    overlays.clear(page)
    scope, send_candidates, kind = _composer_scope(page)
    if kind == "inline" and "/home" not in (page.url or ""):
        # لم تظهر النافذة: الرجوع لمحرر الرئيسية بدل الكتابة في مكان غير معروف
        resilience.goto_with_retry(page, "https://x.com/home", {})
        page.wait_for_timeout(2500)
        overlays.clear(page)
        scope, send_candidates, kind = _composer_scope(page)
    ta = selectors.first(scope, selectors.TEXTAREA_CANDIDATES)
    if ta is None:
        return False, "no-textarea"
    try:
        if not actions._type_text_verified(page, ta, text):
            raise RuntimeError("typing")
    except Exception:
        actions.dismiss_compose_dialog(page, human)
        return False, "typing-failed"
    human.pause(0.8, 2.0)
    send, enabled = _send_button(scope, send_candidates, page)
    status = None
    if send is not None and enabled:
        try:
            send.hover()
            human.pause(0.3, 0.9)
            send.click()
        except Exception:
            status = "click-failed"
    else:
        status = "no-send-button" if send is None else "send-disabled"
    if status:                                   # بديل: Ctrl+Enter والتركيز داخل محرر النافذة
        try:
            ta.click()
            page.keyboard.press("Control+Enter")
        except Exception:
            pass
    ok, why = _sent(page, ta, kind)
    if not ok:
        log.warning("نشر التغريدة لم يتأكد ({}, {}{})".format(kind, why, ", " + status if status else ""))
        actions.dismiss_compose_dialog(page, human)    # تجاهل المسودة (Discard) حتى لا تبقى معلقة
    return ok, why


def is_due(state, cfg):
    dp = cfg.get("daily_post", {})
    if not dp.get("enabled", False):
        return False
    due = state.get("next_post_ts")
    if due is None:
        return True      # أول مرة: انشر في أول جلسة (الجلسات نفسها موزّعة عشوائياً بين الحسابات)
    return time.time() >= float(due)


def _schedule_next(state, cfg):
    lo, hi = (cfg.get("daily_post", {}).get("interval_days") or [1, 2])[:2]
    days = random.uniform(float(lo), float(hi))
    state["next_post_ts"] = time.time() + days * 86400 - random.uniform(0, 3 * 3600)


def maybe_post(page, acc_id, cfg, state, human):
    """تُستدعى داخل جلسة الحساب: تنشر تغريدة الكريبتو إن حان موعدها (كل 1-2 يوم)."""
    if not is_due(state, cfg):
        return False
    if state.get("rest_day"):
        return False
    tags = get_hashtags(page, cfg, human)
    text = generate_post(cfg, tags)
    if not text:
        state["next_post_ts"] = time.time() + 3600      # فشل التوليد: أعد المحاولة بعد ساعة
        return False
    human.pause(2, 5)
    ok, status = post_tweet(page, text, human)
    if ok:
        log.info("{} | نُشرت تغريدة كريبتو: {}".format(acc_id, text))
        state.setdefault("posts", []).append({"ts": time.time(), "text": text})
        state["posts"] = state["posts"][-20:]
        _schedule_next(state, cfg)
    else:
        log.warning("{} | فشل نشر التغريدة اليومية ({})".format(acc_id, status))
        state["next_post_ts"] = time.time() + 2 * 3600
    save_state(acc_id, state)
    try:
        page.goto("https://x.com/home", timeout=45000, wait_until="domcontentloaded")
    except Exception:
        pass
    return ok
