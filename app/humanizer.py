# -*- coding: utf-8 -*-
"""محاكاة السلوك البشري: تأخيرات عشوائية، spintax، تخطيط يومي، بصمات متصفح."""
import random
import re
import time
from datetime import datetime, timedelta
import pytz

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


def get_local_time(cfg):
    """Get current time in configured timezone (Gaza by default)."""
    tz_name = cfg.get("behavior", {}).get("timezone", "Asia/Gaza")
    try:
        tz = pytz.timezone(tz_name)
        return datetime.now(tz)
    except Exception:
        return datetime.now()


def _hm_to_min(hhmm):
    h, m = map(int, str(hhmm).split(":"))
    return h * 60 + m


def active_window(cfg):
    """(start_minute_of_day, length_minutes) of the daily active window (may cross midnight)."""
    ah = cfg.get("behavior", {}).get("active_hours", ["08:00", "23:00"])
    try:
        start, end = _hm_to_min(ah[0]), _hm_to_min(ah[1])
    except Exception:
        start, end = 8 * 60, 23 * 60
    return start, ((end - start) % 1440) or 1440


def offset_minutes(cfg, hhmm=None):
    """Minutes since the active window opened (configured timezone). hhmm=None means now."""
    start, _ = active_window(cfg)
    if hhmm is None:
        n = get_local_time(cfg)
        cur = n.hour * 60 + n.minute + n.second / 60.0
    else:
        cur = _hm_to_min(hhmm)
    return (cur - start) % 1440


def in_active_hours(cfg):
    return offset_minutes(cfg) <= active_window(cfg)[1]


def plan_day(cfg):
    """Date key of the current plan: the day rolls over when the active window opens, not at midnight."""
    start, _ = active_window(cfg)
    return (get_local_time(cfg) - timedelta(minutes=start)).strftime("%Y-%m-%d")


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
    """Daily sessions spread across the active window: one random start inside each equal slice."""
    b = cfg["behavior"]
    start, length = active_window(cfg)
    n = max(1, randint(*b["sessions_per_day"]))
    slice_len = length / n
    sessions = []
    for i in range(n):
        minutes = randint(*b["session_minutes"])
        lo = int(i * slice_len)
        hi = max(lo, int((i + 1) * slice_len) - minutes)
        off = min(randint(lo, hi), max(0, length - minutes))
        t = (start + off) % 1440
        sessions.append({"start": f"{t // 60:02d}:{t % 60:02d}", "minutes": minutes})
    sessions.sort(key=lambda x: offset_minutes(cfg, x["start"]))
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