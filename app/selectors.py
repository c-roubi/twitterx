# -*- coding: utf-8 -*-
"""نقطة صيانة واحدة لمحددات واجهة X/Twitter.

كل ثابت هنا يُعرَّف كقائمة مرشّحات مرتبة حسب الأولوية (الأحدث/الأدق أولاً)،
ومنها تُبنى سلسلة CSS مفصولة بفواصل صالحة لاستخدامها مباشرة مع page.locator().
"""

# ---------------------------------------------------------------------------
# التغريدة (article)
# ---------------------------------------------------------------------------
TWEET_ARTICLE_CANDIDATES = [
    'article[data-testid="tweet"]',
    'article[data-testid="Tweet"]',
    'div[data-testid="tweet"]',
]
TWEET_ARTICLE = ", ".join(TWEET_ARTICLE_CANDIDATES)

# ---------------------------------------------------------------------------
# لايك / إلغاء لايك
# ---------------------------------------------------------------------------
LIKE_BTN_CANDIDATES = [
    'button[data-testid="like"]',
    'button[aria-label="Like"]',
    'button[aria-label$=". Like"]',
    'button[data-testid="Like"]',
]
LIKE_BTN = ", ".join(LIKE_BTN_CANDIDATES)

UNLIKE_BTN_CANDIDATES = [
    'button[data-testid="unlike"]',
    'button[aria-label="Unlike"]',
    'button[aria-label$=". Unlike"]',
    'button[data-testid="Unlike"]',
]
UNLIKE_BTN = ", ".join(UNLIKE_BTN_CANDIDATES)

# ---------------------------------------------------------------------------
# ريتويت / إعادة نشر
# ---------------------------------------------------------------------------
RETWEET_BTN_CANDIDATES = [
    'button[data-testid="retweet"]',
    'button[data-testid="repost"]',
    'button[aria-label*="Repost"]',
    'button[aria-label*="Retweet"]',
    'button[aria-label*="إعادة نشر"]',
    'button[aria-label*="إعادة تغريد"]',
]
RETWEET_BTN = ", ".join(RETWEET_BTN_CANDIDATES)

UNRETWEET_BTN_CANDIDATES = [
    'button[data-testid="unretweet"]',
    'button[data-testid="unrepost"]',
    'button[aria-label*="Undo Repost"]',
    'button[aria-label*="Undo retweet"]',
    'button[aria-label*="تراجع عن إعادة"]',
]
UNRETWEET_BTN = ", ".join(UNRETWEET_BTN_CANDIDATES)

RETWEET_CONFIRM_CANDIDATES = [
    '[data-testid="repostConfirm"]',
    '[data-testid="retweetConfirm"]',
    '[role="menuitem"][data-testid*="Repost"]',
    '[role="menuitem"]:has-text("Repost")',
    '[role="menuitem"]:has-text("Retweet")',
]
RETWEET_CONFIRM = ", ".join(RETWEET_CONFIRM_CANDIDATES)

# ---------------------------------------------------------------------------
# محرر الاقتباس / نافذة "حفظ المسودة" (Save post?) - نرفضها بـ Discard
# ---------------------------------------------------------------------------
DRAFT_DISCARD_BTN_CANDIDATES = [
    'button[data-testid="confirmationSheetCancel"]',
    '[data-testid="confirmationSheet"] button:has-text("Discard")',
    '[data-testid="confirmationSheet"] button:has-text("تجاهل")',
    'div[data-testid="confirmationSheetCancel"]',
    '[role="button"]:has-text("Discard")',
    '[role="button"]:has-text("تجاهل")',
    'button:has-text("Discard")',
    'button:has-text("تجاهل")',
    '[data-testid="confirmationSheet"] [role="button"]:last-child',
]
DRAFT_DISCARD_BTN = ", ".join(DRAFT_DISCARD_BTN_CANDIDATES)

# ---------------------------------------------------------------------------
# تعليق / صندوق الكتابة / زر الإرسال
# ---------------------------------------------------------------------------
REPLY_BTN_CANDIDATES = [
    'button[data-testid="reply"]',
    'button[aria-label*="Reply"]',
    'button[aria-label*="رد"]',
    'button[data-testid="Reply"]',
]
REPLY_BTN = ", ".join(REPLY_BTN_CANDIDATES)

TEXTAREA_CANDIDATES = [
    '[data-testid="tweetTextarea_0"]',
    'div[data-testid="tweetTextarea_0"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[data-testid*="tweetTextarea"]',
    'textarea[data-testid*="tweetTextarea"]',
    'div[role="textbox"][data-testid*="tweetTextarea"]',
]
TEXTAREA = ", ".join(TEXTAREA_CANDIDATES)

SEND_BTN_CANDIDATES = [
    '[data-testid="tweetButtonInline"]',
    '[data-testid="tweetButton"]',
    'button[data-testid="tweetButtonInline"]',
    'button[data-testid="tweetButton"]',
]
SEND_BTN = ", ".join(SEND_BTN_CANDIDATES)

MENU_ITEM_CANDIDATES = [
    '[role="menuitem"]',
    'div[role="menuitem"]',
]
MENU_ITEM = ", ".join(MENU_ITEM_CANDIDATES)

# ---------------------------------------------------------------------------
# تسجيل الدخول / جدار تسجيل الدخول
# ---------------------------------------------------------------------------
LOGIN_BTN_CANDIDATES = [
    '[data-testid="loginButton"]',
    '[data-testid="login"]',
    'a[href="/login"]',
    'a[data-testid="login"]',
]
LOGIN_BTN = ", ".join(LOGIN_BTN_CANDIDATES)

LOGIN_WALL_CANDIDATES = LOGIN_BTN_CANDIDATES
LOGIN_WALL = LOGIN_BTN

# ---------------------------------------------------------------------------
# المتابعة / إلغاء المتابعة
# ---------------------------------------------------------------------------
FOLLOW_BTN_CANDIDATES = [
    '[data-testid="primaryColumn"] [data-testid="placementTracking"] button[data-testid$="-follow"]',
    '[data-testid="primaryColumn"] button[data-testid$="-follow"]',
    '[data-testid="primaryColumn"] div[role="button"][data-testid$="-follow"]',
    'button[data-testid$="-follow"]',
    'div[role="button"][data-testid$="-follow"]',
]
FOLLOW_BTN = ", ".join(FOLLOW_BTN_CANDIDATES)

UNFOLLOW_BTN_CANDIDATES = [
    '[data-testid="primaryColumn"] [data-testid="placementTracking"] button[data-testid$="-unfollow"]',
    '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]',
    '[data-testid="primaryColumn"] div[role="button"][data-testid$="-unfollow"]',
    'button[data-testid$="-unfollow"]',
    'div[role="button"][data-testid$="-unfollow"]',
]
UNFOLLOW_BTN = ", ".join(UNFOLLOW_BTN_CANDIDATES)


def follow_handle_candidates(handle):
    """محددات زر المتابعة الخاص بحساب معيّن (الأدق أولاً)."""
    h = handle.lstrip("@")
    return [
        f'[data-testid="primaryColumn"] button[aria-label*="Follow @{h}" i]',
        f'[data-testid="primaryColumn"] div[role="button"][aria-label*="Follow @{h}" i]',
        f'[data-testid="primaryColumn"] button[aria-label*="@{h}" i][data-testid$="-follow"]',
        f'button[aria-label*="Follow @{h}" i]',
        '[data-testid="primaryColumn"] [data-testid="placementTracking"] button[data-testid$="-follow"]',
    ]


def unfollow_handle_candidates(handle):
    """محددات زر إلغاء المتابعة الخاص بحساب معيّن (الأدق أولاً)."""
    h = handle.lstrip("@")
    return [
        f'[data-testid="primaryColumn"] button[aria-label*="Following @{h}" i]',
        f'[data-testid="primaryColumn"] div[role="button"][aria-label*="Following @{h}" i]',
        f'[data-testid="primaryColumn"] button[aria-label*="@{h}" i][data-testid$="-unfollow"]',
        '[data-testid="primaryColumn"] [data-testid="placementTracking"] button[data-testid$="-unfollow"]',
        '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]',
    ]


# ---------------------------------------------------------------------------
# البروفايل
# ---------------------------------------------------------------------------
USER_CELL_CANDIDATES = [
    '[data-testid="UserCell"]',
    '[data-testid="TypeaheadUser"]',
    'div[data-testid="cellInnerDiv"]',
]
USER_CELL = ", ".join(USER_CELL_CANDIDATES)

PROFILE_BIO_CANDIDATES = [
    'div[data-testid="UserDescription"]',
    '[data-testid="UserDescription"]',
]
PROFILE_BIO = ", ".join(PROFILE_BIO_CANDIDATES)

VERIFIED_ICON_CANDIDATES = [
    '[data-testid="icon-verified"]',
    '[data-testid="icon-verified-badge"]',
    'svg[aria-label="Verified account"]',
]
VERIFIED_ICON = ", ".join(VERIFIED_ICON_CANDIDATES)

FOLLOWING_LINK_CANDIDATES = [
    '[data-testid="primaryColumn"] a[href$="/following"]',
    'a[href$="/following"]',
]
FOLLOWING_LINK = ", ".join(FOLLOWING_LINK_CANDIDATES)

# استبعاد روابط followers_you_follow تماماً ودعم verified_followers
FOLLOWERS_LINK_CANDIDATES = [
    '[data-testid="primaryColumn"] a[href$="/verified_followers"]',
    '[data-testid="primaryColumn"] a[href$="/followers"]',
    'a[href$="/verified_followers"]',
    'a[href$="/followers"]',
]
FOLLOWERS_LINK = ", ".join(FOLLOWERS_LINK_CANDIDATES)

TWEET_TEXT_CANDIDATES = [
    'div[data-testid="tweetText"]',
    '[data-testid="tweetText"]',
]
TWEET_TEXT = ", ".join(TWEET_TEXT_CANDIDATES)

STATUS_LINK = 'a[href*="/status/"]'


def first(page, candidates, visible=True):
    """أول عنصر موجود (وظاهر إن طُلب) عبر المرشحات، بترتيب الأولوية."""
    if isinstance(candidates, str):
        candidates = [candidates]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if not visible or loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def has(page, candidates):
    """هل يوجد أي عنصر يطابق أحد المرشحات (بغض النظر عن الظهور)؟"""
    return first(page, candidates, visible=False) is not None


def all_of(page, candidates, visible=False):
    """كل العناصر المطابقة لأي من المرشحات (بدون تكرار حسب المقبض الفعلي)."""
    if isinstance(candidates, str):
        candidates = [candidates]
    seen = set()
    out = []
    for sel in candidates:
        try:
            for el in page.locator(sel).all():
                if visible and not el.is_visible():
                    continue
                try:
                    handle = el.element_handle()
                    if handle:
                        if handle in seen:
                            continue
                        seen.add(handle)
                        out.append(el)
                except Exception:
                    pass
        except Exception:
            continue
    return out