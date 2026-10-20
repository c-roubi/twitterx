# -*- coding: utf-8 -*-
"""اختبارات النمو والإحماء: متابعات الكريبتو + الأهلية + التدرج اليومي.

التشغيل:  python3 tests/test_grow.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.actions import _parse_count
from app.config import STATE_DIR, load_config
from app.grow import (
    DEFAULT_QUERIES, crypto_queries, follow_budget, _handle_from_href,
    pick_query, profile_eligible,
)
from app.humanizer import pick_daily_follow_target
from app.routine import ensure_daily_plan

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


def test_parse_count():
    print("1) تحليل أعداد المتابعين (_parse_count):")
    cases = [
        ("1.2M Following", 1_200_000),
        ("12,345", 12_345),
        ("12K", 12_000),
        ("2.4K Followers", 2_400),
        ("850", 850),
        ("1.5M", 1_500_000),
        ("2B", 2_000_000_000),
        ("1.2M monthly views", 1_200_000),
        ("", 0),
        (None, 0),
        ("abc", 0),
    ]
    for raw, expected in cases:
        got = _parse_count(raw)
        check(f"«{raw}» -> {got}", got == expected, f"متوقع {expected}")


def test_handle_from_href():
    print("2) استخراج أسماء الحسابات من الروابط:")
    cases = [
        ("/elonmusk", "elonmusk"),
        ("https://x.com/elonmusk", "elonmusk"),
        ("https://twitter.com/a_b1", "a_b1"),
        ("/status/123456", None),
        ("/i/flow/login", None),
        ("/jack/status/999", None),
        ("", None),
        (None, None),
        ("/abcdefghijklmnop", None),  # أطول من 15 حرفاً
        ("/user name", None),         # مسافات غير مسموحة
    ]
    for raw, expected in cases:
        got = _handle_from_href(raw)
        check(f"«{raw}» -> {got}", got == expected, f"متوقع {expected}")


def test_profile_eligible():
    print("3) شروط أهلية الحساب للمتابعة:")
    base = {"follow": {
        "enabled": True, "require_bio": True, "skip_verified": True,
        "min_followers": 50, "max_followers": 150000,
    }}
    good = {"bio": "crypto trader", "followers": 5000, "verified": False}
    check("مؤهل عادي", profile_eligible(good, base))

    v = dict(good, verified=True)
    check("موثّق يُتخطى", not profile_eligible(v, base))
    cfg2 = {"follow": dict(base["follow"], skip_verified=False)}
    check("موثّق مقبول عند تعطيل التجاهل", profile_eligible(v, cfg2))

    nb = dict(good, bio="")
    check("بدون بايو يُتخطى", not profile_eligible(nb, base))
    cfg3 = {"follow": dict(base["follow"], require_bio=False)}
    check("بدون بايو مقبول عند تعطيل الشرط", profile_eligible(nb, cfg3))

    low = dict(good, followers=10)
    check("متابعين أقل من الحد الأدنى", not profile_eligible(low, base))
    high = dict(good, followers=200000)
    check("متابعين أكثر من الحد الأقصى", not profile_eligible(high, base))
    edge_min = dict(good, followers=50)
    edge_max = dict(good, followers=150000)
    check("عند الحد الأدنى تماماً", profile_eligible(edge_min, base))
    check("عند الحد الأقصى تماماً", profile_eligible(edge_max, base))

    cfg_off = {"follow": dict(base["follow"], enabled=False)}
    check("المتابعة معطّلة -> غير مؤهل", not profile_eligible(good, cfg_off))


def test_follow_target_ramp():
    print("4) هدف المتابعات اليومي مع التدرج (pick_daily_follow_target):")
    cfg = {"follow": {"enabled": True, "daily_target": [5, 10], "ramp_days": 7}}
    random.seed(42)
    ok = True
    for _ in range(300):
        t = pick_daily_follow_target(cfg, {"active_days": 7})
        if not (5 <= t <= 10):
            ok = False
            break
    check("بعد أسبوع كامل: ضمن 5-10", ok)

    random.seed(7)
    ok = True
    for _ in range(300):
        t = pick_daily_follow_target(cfg, {"active_days": 0})
        if not (1 <= t <= 4):
            ok = False
            break
    check("اليوم الأول: أخف (1-4)", ok)

    random.seed(3)
    ok = True
    for _ in range(100):
        t = pick_daily_follow_target(cfg)
        if not (1 <= t <= 10):
            ok = False
            break
    check("بدون حالة: دائماً ضمن 1-10", ok)

    off = {"follow": {"enabled": False}}
    check("المتابعة معطّلة -> 0", pick_daily_follow_target(off, {"active_days": 30}) == 0)


def test_follow_budget():
    print("5) الميزانية اليومية المتبقية (follow_budget):")
    state = {"caps": {"follows": 7}, "done": {"follows": 3}}
    cfg = {"follow": {"enabled": True}}
    check("7-3 = 4", follow_budget(state, cfg) == 4)
    state2 = {"caps": {"follows": 5}, "done": {"follows": 5}}
    check("اكتمل الهدف -> 0", follow_budget(state2, cfg) == 0)
    cfg_off = {"follow": {"enabled": False}}
    check("معطّلة -> 0", follow_budget(state, cfg_off) == 0)


def test_crypto_queries():
    print("6) استعلامات الكريبتو (تجاوز/افتراضي):")
    custom = {"follow": {"queries": ["bitcoin", "  solana  ", "", None]}}
    qs = crypto_queries(custom)
    check("استعلامات مخصصة (بدون فارغ)", qs == ["bitcoin", "solana"], str(qs))

    fallback = crypto_queries({})
    check("بدون إعدادات -> افتراضية غير فارغة", len(fallback) > 0 and fallback == DEFAULT_QUERIES)

    q = pick_query(custom)
    check("اختيار عشوائي من المخصصة", q in ("bitcoin", "solana"), q)


def test_daily_plan_integration():
    print("7) دمج خطة اليوم (ensure_daily_plan):")
    aid = "acc_grow_test"
    sp = os.path.join(STATE_DIR, f"{aid}.json")
    if os.path.exists(sp):
        os.remove(sp)
    try:
        cfg = load_config()
        cfg["behavior"]["rest_day_chance"] = 0
        state = ensure_daily_plan(aid, cfg)
        ok = (
            state.get("active_days") == 1
            and state["done"]["follows"] == 0
            and 1 <= state["caps"]["follows"] <= 10
            and state["caps"]["likes"] >= 3
            and isinstance(state.get("followed_handles"), list)
            and isinstance(state.get("seen_users"), list)
        )
        check("خطة اليوم: active_days=1 + متابعات 1-10 + لايك>=3", ok, str({k: state.get(k) for k in ("active_days", "caps", "done")}))
        state2 = ensure_daily_plan(aid, cfg)
        check("نفس اليوم: لا تُنشأ خطة ثانية", state2.get("active_days") == 1)
    finally:
        if os.path.exists(sp):
            os.remove(sp)


def main():
    test_parse_count()
    test_handle_from_href()
    test_profile_eligible()
    test_follow_target_ramp()
    test_follow_budget()
    test_crypto_queries()
    test_daily_plan_integration()
    print(f"\nالنتيجة: {PASS} نجحت / {FAIL} فشلت")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
