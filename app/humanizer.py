# -*- coding: utf-8 -*-
"""محاكاة السلوك البشري: تأخيرات عشوائية، spintax، تخطيط يومي، بصمات متصفح."""
import random
import re
import time
from datetime import datetime, timedelta

# قوالب بصمات متصفح مختلفة لكل حساب (تم تحديث الإصدارات لتبدو طبيعية وحديثة لتجنب كشف البوت)
VIEWPORTS = [
    (1280, 800), (1366, 768), (1440, 900), (1536, 864),
    (1600, 900), (1280, 720), (1366, 768), (1920, 1080),
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

SUFFIX_POOL = ["", "", "", "", ".", "!", " 👏", " ❤️", " ✨", " 🙏", " 🔥", " ✓"]


class Human:
    """أداة تأخيرات بشرية بسيطة (يمكن تمرير page لاستخدام wait_for_timeout)."""

    def __init__(self, page=None):
        self.page = page

    def pause(self, lo=1.0, hi=3.0):
        t = self._lognormal(lo, hi)
        if self.page is not None:
            self.page.wait_for_timeout(int(t * 1000))
        else:
            time.sleep(t)
        return t

    @staticmethod
    def _lognormal(lo, hi):
        import math
        mu = math.log(max(0.01, (lo + hi) / 2))
        sigma = 0.3
        v = random.lognormvariate(mu, sigma)
        return max(lo * 0.8, min(v, hi * 1.2))

    def mouse_wander(self, page=None):
        """حركة مؤشر عشوائية صغيرة (8-22 خطوة) تعطي إحساسًا بشريًا أثناء التصفح."""
        page = page or self.page
        if page is None:
            return
        try:
            vp = page.viewport_size or {"width": 1280, "height": 800}
            x = randint(120, max(121, vp["width"] - 250))
            y = randint(120, max(121, vp["height"] - 250))
            for _ in range(randint(8, 22)):
                x = max(2, min(x + randint(-90, 90), vp["width"] - 2))
                y = max(2, min(y + randint(-60, 60), vp["height"] - 2))
                page.mouse.move(x, y, steps=randint(4, 10))
                page.wait_for_timeout(randint(30, 140))
        except Exception:
            pass


def randint(lo, hi):
    return random.randint(lo, hi)


def randfloat(lo, hi):
    return random.uniform(lo, hi)


def pick(seq):
    return random.choice(seq)


def spintax(text):
    """تحويل {خيار1|خيار2|خيار3} إلى خيار عشوائي (مع دعم تداخل)."""
    pattern = re.compile(r"\{([^{}]+)\}")

    def _replace(m):
        options = [o.strip() for o in m.group(1).split("|")]
        return random.choice(options)

    for _ in range(5):
        new = pattern.sub(_replace, text)
        if new == text:
            break
        text = new
    return text


def build_comment(template, used_texts=(), force_unique=True):
    """توليد تعليق من قالب spintax مع إضافة لاحقة عشوائية، وتجنب التكرار."""
    for _ in range(20):
        base = spintax(template)
        if random.random() < 0.45:
            base = base + random.choice(SUFFIX_POOL)
        if base in used_texts:
            continue
        if force_unique and len(base) < 5:
            continue
        return base
    
    base = spintax(template).strip()
    suffix = random.choice(SUFFIX_POOL)
    return base + suffix if suffix else base


def lighter_day_factor():
    """~30% من الأيام تكون «أيامًا أخف»: 50-85% فقط من سقف التفاعلات (إعجابات/ريتويت/تعليقات).
    المتابعات لا تتأثر بهذا — تبقى ضمن 5-10 كما طلب المستخدم."""
    if random.random() < 0.30:
        return randfloat(0.5, 0.85)
    return 1.0


def is_rest_day(cfg, state=None):
    """يوم راحة كامل: احتمال صغير لعدم القيام بأي تفاعل نشط (يبقى التصفح السلبي فقط)."""
    chance = 0.0
    try:
        chance = float(cfg.get("behavior", {}).get("rest_day_chance", 0.0))
    except (TypeError, ValueError):
        chance = 0.0
    if chance <= 0:
        return False
    return random.random() < chance


def pick_daily_follow_target(cfg, state=None, rest_day=False):
    """هدف المتابعات اليومي (5-10) مع تدرّج أمان على مدى ramp_days أيام.
    الحساب الجديد يبدأ أخف (40% من الهدف) ويصل للهدف الكامل بعد أسبوع.
    في يوم الراحة لا تتم أي متابعات."""
    f = cfg.get("follow", {})
    if rest_day or not f.get("enabled", True):
        return 0
    lo, hi = f.get("daily_target", [5, 10])
    ramp_days = max(1, int(f.get("ramp_days", 7)))
    active_days = 0
    if state:
        active_days = int(state.get("active_days", 0) or 0)
    progress = min(1.0, active_days / ramp_days)
    factor = 0.4 + 0.6 * progress
    return max(1, int(round(randint(lo, hi) * factor)))


def pick_daily_caps(cfg, state=None, rest_day=False):
    """سقوف التفاعلات اليومية مع تدرّج حسب عمر الحساب (نفس منحنى المتابعات).
    الحساب الجديد يبدأ أخف ويصل للسقف الكامل بعد أسبوع.
    في يوم الراحة => كل السقوف 0 (يبقى التصفح السلبي فقط)."""
    b = cfg["behavior"]
    if rest_day:
        return {"likes": 0, "retweets": 0, "comments": 0, "follows": 0}
    ramp_days = max(1, int(cfg.get("follow", {}).get("ramp_days", 7)))
    active_days = 0
    if state:
        active_days = int(state.get("active_days", 0) or 0)
    progress = min(1.0, active_days / ramp_days)
    age_factor = 0.4 + 0.6 * progress
    f = lighter_day_factor()
    return {
        "likes": max(3, int(randint(*b["max_likes_per_day"]) * f * age_factor)),
        "retweets": max(1, int(randint(*b["max_retweets_per_day"]) * f * age_factor)),
        "comments": max(1, int(randint(*b["max_comments_per_day"]) * f * age_factor)),
        "follows": 0,
    }


def random_sessions(cfg):
    """توليد جلسات يومية: أوقات بداية عشوائية داخل ساعات النشاط."""
    b = cfg["behavior"]
    start_h, start_m = map(int, b["active_hours"][0].split(":"))
    end_h, end_m = map(int, b["active_hours"][1].split(":"))
    day_start = datetime.now().replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    day_end = datetime.now().replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    
    # ✅ تم التعديل: حساب رياضي دقيق للعمل الليلي عبر منتصف الليل
    if day_end <= day_start:
        day_end = day_end + timedelta(days=1)

    n = randint(*b["sessions_per_day"])
    sessions = []
    for _ in range(n):
        span = (day_end - day_start).total_seconds()
        start = day_start + timedelta(seconds=randint(0, int(span)))
        minutes = randint(*b["session_minutes"])
        sessions.append({"start": start.strftime("%H:%M"), "minutes": minutes})
    
    sessions.sort(key=lambda s: s["start"])
    return sessions


def fingerprint_for(account):
    """بصمة متصفح ثابتة لكل حساب (مبنية على hash الحساب لتبقى ثابتة بين التشغيلات)."""
    fp = account.get("fingerprint")
    if fp:
        return fp
    import hashlib
    acc_id = account.get("id", account.get("handle", "default"))
    seed = int(hashlib.md5(str(acc_id).encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    fp = {
        "viewport": rng.choice(VIEWPORTS),
        "user_agent": rng.choice(USER_AGENTS),
        "locale": account.get("locale", "ar"),
        "timezone_id": account.get("timezone", "Asia/Riyadh"),
    }
    return fp


def minutes_until(target_hhmm):
    """كم دقيقة من الآن حتى وقت محدد (HH:MM) اليوم؟ إن فات يعيد -1."""
    now = datetime.now()
    h, m = map(int, target_hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    delta = (target - now).total_seconds() / 60
    return delta