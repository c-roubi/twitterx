# -*- coding: utf-8 -*-
"""اختبارات البروكسيات والإحماء اليومي: تحليل + توزيع ثابت + رجوع تلقائي + جدولة.

التشغيل:  python3 tests/test_proxies.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import proxies
from app.config import load_config, save_state, state_path
from app.routine import (
    _random_warmup_time, check_due_warmups, ensure_daily_plan, warmup_due,
)

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


def test_parse_proxy():
    print("1) تحليل نصوص البروكسي (parse_proxy):")
    cases = [
        ("socks5://user:pass@1.2.3.4:1080",
         {"scheme": "socks5", "host": "1.2.3.4", "port": 1080, "username": "user", "password": "pass"}),
        ("socks5h://1.2.3.4:1080",
         {"scheme": "socks5h", "host": "1.2.3.4", "port": 1080, "username": None, "password": None}),
        ("http://proxy.example.com:8080",
         {"scheme": "http", "host": "proxy.example.com", "port": 8080, "username": None, "password": None}),
        ("socks4://1.2.3.4:1080",
         {"scheme": "socks4", "host": "1.2.3.4", "port": 1080, "username": None, "password": None}),
        ("5.6.7.8:3128",
         {"scheme": "http", "host": "5.6.7.8", "port": 3128, "username": None, "password": None}),
        ("http://a:b@1.2.3.4:80",
         {"scheme": "http", "host": "1.2.3.4", "port": 80, "username": "a", "password": "b"}),
    ]
    for raw, expected in cases:
        got = proxies.parse_proxy(raw)
        ok = got is not None and all(got.get(k) == v for k, v in expected.items())
        check(f"«{raw}»", ok, f"حصلت على {got}")

    bad = ["", None, "   ", "host", "host:abc", "ftp://h:1", "socks5://h:99999", "socks5://:1080", "http://user@host"]
    for raw in bad:
        got = proxies.parse_proxy(raw)
        check(f"غير صالح «{raw}» -> None", got is None, str(got))


def test_proxy_args():
    print("2) وسائط Playwright (proxy_args / server_string):")
    p = proxies.parse_proxy("socks5://user:pass@1.2.3.4:1080")
    args = proxies.proxy_args(p)
    check("server صحيح", args.get("server") == "socks5://1.2.3.4:1080", str(args))
    check("username/password", args.get("username") == "user" and args.get("password") == "pass", str(args))
    check("بدون بروكسي -> {} فارغ", proxies.proxy_args(None) == {})
    h = proxies.parse_proxy("http://1.2.3.4:3128")
    check("http server", proxies.server_string(h) == "http://1.2.3.4:3128", proxies.server_string(h))
    sh = proxies.parse_proxy("socks5h://1.2.3.4:1080")
    check("socks5h -> socks5", proxies.server_string(sh) == "socks5://1.2.3.4:1080", proxies.server_string(sh))


def _cfg_with(proxies_cfg):
    cfg = load_config()
    cfg["proxies"] = copy.deepcopy(proxies_cfg)
    return cfg


def test_pick_proxy_stable():
    print("3) التوزيع الثابت للحسابات (pick_proxy):")
    proxies._cache.clear()
    cfg = _cfg_with({"enabled": True, "list": [
        "socks5://1.1.1.1:1080", "http://2.2.2.2:8080", "socks5://3.3.3.3:1080",
    ], "fallback_to_direct": True, "test_timeout": 1, "rotate": False})
    a1 = proxies.pick_proxy("acc_001", cfg)
    a2 = proxies.pick_proxy("acc_001", cfg)
    check("نفس الحساب -> نفس البروكسي دائماً", a1 == a2 and a1 is not None, f"{a1} vs {a2}")
    valid = {proxies.server_string(proxies.parse_proxy(x)) for x in cfg["proxies"]["list"]}
    check("البروكسي المختار من القائمة", proxies.server_string(a1) in valid, proxies.server_string(a1))
    others = {proxies.server_string(proxies.pick_proxy(f"acc_{i:03d}", cfg)) for i in range(1, 31)}
    check("توزيع على أكثر من بروكسي", len(others) > 1, str(others))

    off = _cfg_with({"enabled": False, "list": ["socks5://1.1.1.1:1080"]})
    check("معطل -> None", proxies.pick_proxy("acc_001", off) is None)
    empty = _cfg_with({"enabled": True, "list": []})
    check("قائمة فارغة -> None", proxies.pick_proxy("acc_001", empty) is None)
    junk = _cfg_with({"enabled": True, "list": ["bad", "also bad"]})
    check("قائمة غير صالحة -> None", proxies.pick_proxy("acc_001", junk) is None)


def test_fallback():
    print("4) الرجوع التلقائي عند فشل البروكسي (resolve_for):")
    proxies._cache.clear()
    dead = ["socks5://127.0.0.1:1"]
    cfg = _cfg_with({"enabled": True, "list": dead, "fallback_to_direct": True, "test_timeout": 1})
    got = proxies.resolve_for("acc_001", cfg)
    check("fallback=true + بروكسي ميت -> مباشر (None)", got is None, str(got))

    cfg2 = _cfg_with({"enabled": True, "list": dead, "fallback_to_direct": False, "test_timeout": 1})
    got2 = proxies.resolve_for("acc_001", cfg2)
    check("fallback=false -> يبقى مع البروكسي", got2 is not None, str(got2))

    off = _cfg_with({"enabled": False, "list": dead, "fallback_to_direct": True, "test_timeout": 1})
    check("معطل -> مباشر", proxies.resolve_for("acc_001", off) is None)

    check("test_proxy فشل سريع", proxies.test_proxy(proxies.parse_proxy("socks5://127.0.0.1:1"), 1) is False)
    check("test_proxy بلا بروكسي -> False", proxies.test_proxy(None, 1) is False)


def test_guest_proxy():
    print("5) بروكسي جلسة الضيف (resolve_guest):")
    proxies._cache.clear()
    dead = _cfg_with({"enabled": True, "list": ["socks5://127.0.0.1:1"], "fallback_to_direct": True, "test_timeout": 1})
    check("كل البروكسيات ميتة -> مباشر", proxies.resolve_guest(dead) is None)
    off = _cfg_with({"enabled": False, "list": ["socks5://127.0.0.1:1"]})
    check("معطل -> مباشر", proxies.resolve_guest(off) is None)
    empty = _cfg_with({"enabled": True, "list": []})
    check("قائمة فارغة -> مباشر", proxies.resolve_guest(empty) is None)


def test_warmup_time():
    print("6) وقت الإحماء العشوائي (_random_warmup_time):")
    cfg = load_config()
    for _ in range(30):
        t = _random_warmup_time(cfg)
        h, m = map(int, t.split(":"))
        total = h * 60 + m
        ok = 8 * 60 <= total <= 22 * 60 + 30
        if not ok:
            check(f"الوقت {t} ضمن ساعات النشاط", False)
            return
    check("30 عينة ضمن ساعات النشاط [08:00-22:30]", True)


def test_warmup_due():
    print("7) استحقاق الإحماء (warmup_due):")
    check("لا موعد -> غير مستحق", not warmup_due({}, None))
    check("تم سابقاً -> غير مستحق", not warmup_due({"warmup_at": "00:00", "warmup_done": True}, None))
    check("وقت في الماضي -> مستحق", warmup_due({"warmup_at": "00:00", "warmup_done": False, "needs_relogin": False}, None))
    check("يحتاج إعادة دخول -> غير مستحق",
          not warmup_due({"warmup_at": "00:00", "warmup_done": False, "needs_relogin": True}, None))
    check("صيغة خاطئة -> غير مستحق", not warmup_due({"warmup_at": "xyz", "warmup_done": False}, None))
    from datetime import datetime
    now = datetime.now()
    mins = now.hour * 60 + now.minute
    future = min(mins + 30, 23 * 60 + 59)
    later = f"{future // 60:02d}:{future % 60:02d}"
    check("وقت مستقبلي -> غير مستحق",
          not warmup_due({"warmup_at": later, "warmup_done": False, "needs_relogin": False}, None))


def test_daily_plan_warmup():
    print("8) خطة اليوم تتضمن الإحماء (ensure_daily_plan):")
    aid = "acc_test_warmup"
    sp = state_path(aid)
    if os.path.exists(sp):
        os.remove(sp)
    try:
        cfg = load_config()
        state = ensure_daily_plan(aid, cfg)
        ok = isinstance(state.get("warmup_at"), str) and state.get("warmup_done") is False
        check("warmup_at + warmup_done موجودان", ok, str({k: state.get(k) for k in ("warmup_at", "warmup_done")}))
    finally:
        if os.path.exists(sp):
            os.remove(sp)


def test_check_due_warmups():
    print("9) تشغيل الإحماء المستحق (check_due_warmups):")
    aid = "acc_test_warmup2"
    sp = state_path(aid)
    if os.path.exists(sp):
        os.remove(sp)
    try:
        cfg = load_config()
        state = ensure_daily_plan(aid, cfg)
        state["warmup_at"] = "00:00"
        state["warmup_done"] = False
        save_state(aid, state)
        accounts = {aid: {"enabled": True, "handle": "test"}}
        res = check_due_warmups(cfg, accounts, dry_run=True)
        check("إحماء واحد منفّذ (محاكاة)", res["warmups"] == 1, str(res))

        cfg2 = copy.deepcopy(cfg)
        cfg2["warmup"]["enabled"] = False
        res2 = check_due_warmups(cfg2, accounts, dry_run=True)
        check("معطل -> صفر", res2["warmups"] == 0, str(res2))
    finally:
        if os.path.exists(sp):
            os.remove(sp)


def main():
    test_parse_proxy()
    test_proxy_args()
    test_pick_proxy_stable()
    test_fallback()
    test_guest_proxy()
    test_warmup_time()
    test_warmup_due()
    test_daily_plan_warmup()
    test_check_due_warmups()
    print(f"\nالنتيجة: {PASS} نجحت / {FAIL} فشلت")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
