# -*- coding: utf-8 -*-
"""نقطة صيانة واحدة لمحددات واجهة X/Twitter.

كل ثابت هنا يُعرَّف كقائمة مرشّحات مرتبة حسب الأولوية (الأحدث/الأدق أولاً)،
ومنها تُبنى سلسلة CSS مفصولة بفواصل صالحة لاستخدامها مباشرة مع page.locator().

الهدف: مقاومة تغييرات X المتكررة في DOM عبر إضافة بدائل بدلاً من استبدال
المحدد القديم، بحيث يبقى البرنامج يعمل مع الواجهة القديمة والجديدة معاً.
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
# ريتويت / إعادة نشر (تغيّرت التسمية من Retweet إلى Repost أواخر 2024)
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
    # native <button> inside the 'Save post?' sheet (implicit role)
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
# FIX-SAVEPOST-V2-SELECTORS
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

# صندوق النص تحوّل من <textarea> إلى <div contenteditable> في الواجهة الجديدة
# FIX-SENDBTN-V1: المحدد الأدق (tweetTextarea_0) أولاً - حتى لا يلتقط البرنامج
# صندوق نص مخفياً/آخر (مثل مربع البحث أو محرر آخر) ويظن أنه محرر التعليق.
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
    'button[data-testid$="-follow"]',
    'button[aria-label="Follow"]',
    'button[aria-label^="Follow "]',
    'div[role="button"][data-testid$="-follow"]',
]
FOLLOW_BTN = ", ".join(FOLLOW_BTN_CANDIDATES)

UNFOLLOW_BTN_CANDIDATES = [
    'button[data-testid$="-unfollow"]',
    'button[aria-label^="Following "]',
    'div[role="button"][data-testid$="-unfollow"]',
]
UNFOLLOW_BTN = ", ".join(UNFOLLOW_BTN_CANDIDATES)


def follow_handle_candidates(handle):
    """محددات زر المتابعة الخاص بحساب معيّن (الأدق أولاً)."""
    return [
        f'button[data-testid="{handle}-follow"]',
        f'div[role="button"][data-testid="{handle}-follow"]',
        f'button[aria-label*="@{handle}"]',
    ]


def unfollow_handle_candidates(handle):
    """محددات زر إلغاء المتابعة الخاص بحساب معيّن (الأدق أولاً)."""
    return [
        f'button[data-testid="{handle}-unfollow"]',
        f'div[role="button"][data-testid="{handle}-unfollow"]',
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
    'a[href$="/following"]',
    'a[href*="/following"]',
]
FOLLOWING_LINK = ", ".join(FOLLOWING_LINK_CANDIDATES)

FOLLOWERS_LINK_CANDIDATES = [
    'a[href$="/followers"]',
    'a[href*="/followers"]',
]
FOLLOWERS_LINK = ", ".join(FOLLOWERS_LINK_CANDIDATES)

TWEET_TEXT_CANDIDATES = [
    'div[data-testid="tweetText"]',
    '[data-testid="tweetText"]',
]
TWEET_TEXT = ", ".join(TWEET_TEXT_CANDIDATES)

# رابط فريد للتغريدة (يحتوي /status/<id>)
STATUS_LINK = 'a[href*="/status/"]'


# ---------------------------------------------------------------------------
# دوال مساعدة للتعامل مع القوائم بترتيب الأولوية
# ---------------------------------------------------------------------------
def first(page, candidates, visible=True):
    """أول عنصر موجود (وظاهر إن طُلب) عبر المرشحات، بترتيب الأولوية.

    تُعيد Locator جاهزاً للـ click/hover/is_enabled، أو None إن لم يوجد شيء.
    """
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
    seen_boxes = []
    out = []
    for sel in candidates:
        try:
            for el in page.locator(sel).all():
                if visible and not el.is_visible():
                    continue
                try:
                    box = el.bounding_box()
                    if box:
                        key = (round(box["x"]), round(box["y"]), round(box["width"]), round(box["height"]))
                        if key in seen_boxes:
                            continue
                        seen_boxes.append(key)
                    out.append(el)
                except Exception:
                    out.append(el)
        except Exception:
            continue
    return out