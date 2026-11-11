# -*- coding: utf-8 -*-
"""اختبارات محرك التعليقات السياقي + الذكاء الاصطناعي + جدولة النص.

التشغيل:  python3 tests/test_comments.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import comments, monitor
from app.ai_comments import generate_ai_comment
from app.comments import build_comment_for
from app.config import (
    COMMENTS_CRYPTO_PATH, load_accounts, load_config, load_seen, save_config, save_seen,
)
from app.crypto_comments import (
    DEFAULT_POOLS, EN_POOLS, KEYWORDS, TOPIC_PRIORITY, _render_template, detect_topics,
    extract_ticker, pools_for,
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


BASE_SAMPLES = [
    # (نص, المواضيع المتوقعة على الأقل)
    ("البيتكوين عند قمة تاريخية جديدة 🚀", ["btc", "bull"]),
    ("Bitcoin breaking all time highs again", ["btc", "bull"]),
    ("إيثيريوم يخترق مستوى المقاومة مع تحليل الشارت", ["eth", "analysis"]),
    ("السوق في تصحيح، هبوط متوقع خلال الأيام القادمة", ["crypto_general", "bear"]),
    ("فرصة شراء ممتازة لتجميع العملات البديلة", ["buy", "alt"]),
    ("شراكة جديدة مع منصة بينانس، إدراج قريب", ["news", "exchange"]),
    ("تحذير من احتيال، انتبهوا لإدارة المخاطر", ["advice"]),
    ("NFT والويب 3 مستقبل السوق", ["nft_web3"]),
    ("عوائد الستيكينغ في الديفاي تتزايد", ["defi"]),
    ("نصيحة: الاستثمار الطويل يحتاج صبراً", ["advice", "hold"]),
    ("تغريدة عادية عن الحياة اليومية", []),
    ("", []),
]

# تغريدات المدير الحقيقية (حساب إشارات ضخ بالإنجليزية) - المواضيع المتوقعة على الأقل
MANAGER_TWEETS = [
    ("$EPIC 💎\nA good coin with a meteoric rise.", ["token_pick", "pump_signal"]),
    ("I expect a strong pump from here.", ["pump_signal"]),
    ("$FARTCOIN 💎\nThe upward trend continues until a 70% profit is achieved 🤑", ["token_pick", "profit_take", "ta_pattern"]),
    ("It still has big targets, so there's no harm in securing some profits at these levels.", ["profit_take"]),
    ("$READY 💎\nSo far, a 20% profit 🤑 I still see significant upside potential.", ["token_pick", "profit_take"]),
    ("Place a stop loss upon entry.", ["ta_pattern"]),
    ("The downtrend line was broken and then successfully retested.", ["ta_pattern"]),
    ("I expect a large pump to come.", ["pump_signal", "bull"]),
    ("The call was cancelled after currency analysis.", ["risk_call"]),
    ("Anyone wishing to continue is at their own risk.", ["risk_call"]),
    ("We'll compensate with another currency, folks.", ["risk_call"]),
    ("I'm going to share one of #KUCOIN coins 👀", ["exchange", "token_pick"]),
    ("Get ready 💪", ["hype"]),
]

SAMPLE_TWEETS = BASE_SAMPLES + MANAGER_TWEETS


def test_topic_detection():
    print("1) كشف المواضيع من النصوص:")
    for text, expected in SAMPLE_TWEETS:
        topics = detect_topics(text)
        missing = [t for t in expected if t not in topics]
        check(f"«{text[:40]}» -> {topics}", not missing, f"ناقص: {missing}")


def test_pools():
    print("2) قوالب المواضيع:")
    all_topics = set(KEYWORDS) | set(DEFAULT_POOLS)
    check("كل موضوع له قوالب", all_topics == set(DEFAULT_POOLS))
    for t, pool in DEFAULT_POOLS.items():
        check(f"موضوع {t}: {len(pool)} قالب (5+)", len(pool) >= 5)
    p = pools_for(["btc", "bull"])
    check("دمج قوالب btc+bull غير فارغ", len(p) >= 8)
    check("دمج بدون تكرار", len(p) == len(set(p)))
    # القوالب الإنجليزية: كل موضوع له 5+ قوالب (أغلبها مع {TICKER})
    check("كل موضوع له قوالب إنجليزية", set(KEYWORDS) == set(EN_POOLS))
    for t, pool in EN_POOLS.items():
        check(f"موضوع EN {t}: {len(pool)} قالب (5+)", len(pool) >= 5)
    en_ticker = sum(1 for pool in EN_POOLS.values() for t in pool if "{TICKER}" in t)
    check(f"قوالب إنجليزية تحمل {{TICKER}}: {en_ticker} قالباً", en_ticker >= 40)


def test_build_comment_for():
    print("3) توليد تعليقات سياقية فريدة:")
    cfg = load_config()
    used = set()
    texts = []
    # تغريدات كريبتو متنوعة - كلها يجب أن تعطي تعليقات سياقية (إنجليزية غالباً)
    samples = [t for t, _ in SAMPLE_TWEETS if t and detect_topics(t)]
    for _ in range(60):
        t = random.choice(samples)
        c = build_comment_for(t, used_texts=used, cfg=cfg)
        texts.append((t, c))
        used.add(c)
    check("60 تعليقاً كلها فريدة", len(used) == 60)
    en = sum(1 for _, c in texts if not re_search_arabic(c))
    ar = sum(1 for _, c in texts if re_search_arabic(c))
    check(f"تعليقات إنجليزية: {en}/60 (>=45)", en >= 45)
    check(f"تعليقات عربية باقية: {ar}/60 (>=1)", ar >= 1)
    # طول معقول (أقل من 280 حرفاً و أكثر من 4)
    lengths_ok = all(5 <= len(c) <= 280 for _, c in texts)
    check("الأطوال ضمن الحدود", lengths_ok)
    # لا إيموجي مفرط ولا وسوم
    clean = all("#" not in c and c.count("🔥") <= 2 for _, c in texts)
    check("بدون وسوم وإيموجي مفرط", clean)

    # تغريدة غير كريبتو -> يجب أن تقع على القوالب العامة
    c = build_comment_for("تغريدة عادية عن الحياة اليومية", used_texts=used, cfg=cfg)
    check("تغريدة عادية -> تعليق عام", bool(c) and c not in used)
    c2 = build_comment_for("", used_texts=used, cfg=cfg)
    check("نص فارغ -> تعليق عام", bool(c2))


def re_search_arabic(c):
    import re

    return re.search(r"[\u0600-\u06FF]", c) is not None


def test_ticker_and_gem():
    print("7) $TICKER + لغة الجواهر (أسلوب المدير):")
    check("extract_ticker('$EPIC 💎 ...') == 'EPIC'", extract_ticker("$EPIC 💎 meteoric rise") == "EPIC")
    check("extract_ticker('$sol قادم') == 'SOL' (أحرف صغيرة)", extract_ticker("$sol قادم") == "SOL")
    check("بدون رمز -> ''", extract_ticker("لا رمز هنا") == "")
    check("أرقام فقط ($50) -> ''", extract_ticker("السعر $50") == "")
    check("extract_ticker('$BTC صاعد') == 'BTC'", extract_ticker("$BTC صاعد") == "BTC")
    t = detect_topics("$BTC عند قمة تاريخية")
    check("$BTC لا يُفعّل alt", "alt" not in t)
    t = detect_topics("$SOL رالي قوي")
    check("$SOL -> alt", "alt" in t)
    t = detect_topics("ماشاء الله، قمر عملة 🚀")
    check("🚀/قمر -> bull", "bull" in t)
    # gem خرج من الأولوية: كلمة gem مغطاة عبر token_pick والإيموجي 💎 عبر hype
    check("gem لم يعد في الأولوية (يغطيه token_pick/hype)", "gem" not in TOPIC_PRIORITY)
    check("كلمة gem في token_pick", "gem" in KEYWORDS["token_pick"])
    check("💎 يفعّل hype", "hype" in detect_topics("شيء 💎"))
    check("render مع رمز", _render_template("{TICKER} إلى القمر", "EPIC") == "EPIC إلى القمر")
    check("render بلا رمز -> None", _render_template("{TICKER} إلى القمر", "") is None)
    check("render قالب عادي يبقى كما هو", _render_template("صعود {قوي|مميز}", "EPIC") == "صعود {قوي|مميز}")
    check("render EN قالب يحفظ $", _render_template("${TICKER} to the moon", "EPIC") == "$EPIC to the moon")
    check("render EN بلا رمز -> None", _render_template("${TICKER} to the moon", "") is None)


def test_epic_probabilistic():
    print("8) ظهور EPIC في تعليقات تغريدة المدير (احتمالي):")
    cfg = load_config()
    text = "$EPIC 💎\nA good coin with a meteoric rise.\nI expect a strong pump from here."
    used = set()
    hits = 0
    for _ in range(150):
        c = build_comment_for(text, used_texts=used, cfg=cfg)
        if "EPIC" in c:
            hits += 1
        used.add(c)
    check(f"150 تعليقاً فريداً، {hits} ذكرت EPIC (>=1)", hits >= 1, f"EPIC: {hits}")
    en = sum(1 for c in used if not re_search_arabic(c))
    check(f"تعليقات إنجليزية: {en}/150 (>=125)", en >= 125, f"EN: {en}")


def test_ai_module():
    print("4) وحدة الذكاء الاصطناعي (بدون مفتاح):")
    ai_cfg = {"provider": "gemini", "api_key": "", "model": "gemini-1.5-flash", "min_interval_s": 0}
    check("بدون مفتاح -> None", generate_ai_comment("البيتكوين صاعد", ai_cfg) is None)
    ai_cfg = {"provider": "bad_provider", "api_key": "x", "model": "", "min_interval_s": 0}
    check("مزوّد غير معروف -> None", generate_ai_comment("البيتكوين صاعد", ai_cfg) is None)
    ai_cfg = {"provider": "gemini", "api_key": "x", "model": "gemini-1.5-flash", "min_interval_s": 0}
    check("فشل شبكة/مفتاح -> None (لا يكسر المحرك)", generate_ai_comment("البيتكوين صاعد", ai_cfg) is None)
    # المحرك العام يبقى يعمل حتى مع AI مفعّل لكن مفتاح خاطئ
    cfg = load_config()
    cfg["comments"]["ai"]["enabled"] = True
    cfg["comments"]["ai"]["api_key"] = "invalid-key-test"
    cfg["comments"]["ai"]["min_interval_s"] = 0
    c = build_comment_for("البيتكوين عند قمة تاريخية", cfg=cfg)
    check("AI معطوب -> يقع على البديل السياقي", bool(c) and 5 <= len(c) <= 280 and "#" not in c)


def test_scheduling_with_text():
    print("5) الجدولة تخزّن نص التغريدة وتمرره للمهام:")
    # حفظ/استعادة seen.json حفاظاً على بيانات المستخدم
    backup = load_seen()
    try:
        accounts = load_accounts()
        if len(accounts) < 3:
            accounts = {f"acc_{i:03d}": {"enabled": True, "handle": f"t{i}"} for i in range(1, 6)}
        cfg = load_config()
        tid = "9999999999999999999"
        text = "البيتكوين عند قمة تاريخية جديدة"
        entry = monitor.schedule_for_tweet(tid, text, cfg, accounts)
        check("تم إنشاء الجدولة", entry is not None and entry["scheduled"])
        check("النص محفوظ", entry.get("text") == text)
        # محاكاة استحقاق فوري (تعديل النسخة المحفوظة على القرص)
        saved = load_seen()
        for t in saved[tid]["queue"]:
            t["due"] = 0
        save_seen(saved)
        tasks = monitor.due_tasks_for("acc_001")
        mine = [t for t in tasks if t["tweet_id"] == tid]
        check("المهام تحمل النص", bool(mine) and mine[0]["text"] == text)
        # عدم تكرار الجدولة لنفس التغريدة
        again = monitor.schedule_for_tweet(tid, text, cfg, accounts)
        check("لا جدولة مكررة", again is None)
    finally:
        save_seen(backup)


def test_config_and_files():
    print("6) الإعدادات والملفات:")
    cfg = load_config()
    check("قسم comments موجود في الإعدادات", "comments" in cfg)
    check("contextual مفعّل افتراضياً", cfg["comments"].get("contextual") is True)
    check("en_ratio افتراضياً 0.9", cfg["comments"].get("en_ratio") == 0.9)
    check("ai معطّل افتراضياً", cfg["comments"]["ai"].get("enabled") is False)
    check("comments_crypto.json موجود", os.path.exists(COMMENTS_CRYPTO_PATH))
    # قوالب المستخدم القابلة للتعديل قابلة للقراءة
    from app.crypto_comments import load_crypto_pools_from_json

    pools = load_crypto_pools_from_json(COMMENTS_CRYPTO_PATH)
    check("ملف القوالب يُقرأ ويدمج", len(pools) >= len(DEFAULT_POOLS) and all(pools[t] for t in DEFAULT_POOLS))


def test_account_en_ratio():
    print("10) نسبة اللغة لكل حساب (en_ratio):")
    cfg = load_config()
    text = "Bitcoin breaking all time highs again"
    used = set()
    en_all = [build_comment_for(text, used_texts=used, cfg=cfg, en_ratio=1.0) for _ in range(30)]
    check("en_ratio=1 -> كلها إنجليزية", all(not re_search_arabic(c) for c in en_all), str(en_all[:3]))
    ar_all = [build_comment_for(text, used_texts=used, cfg=cfg, en_ratio=0.0) for _ in range(30)]
    check("en_ratio=0 -> كلها عربية", all(re_search_arabic(c) for c in ar_all), str(ar_all[:3]))


def test_manager_tweets_demo():
    print("9) تغريدات المدير الحقيقية (المواضيع + أمثلة تعليقات):")
    cfg = load_config()
    used = set()
    for text, expected in MANAGER_TWEETS:
        topics = detect_topics(text)
        missing = [t for t in expected if t not in topics]
        check(f"«{text[:45].replace(chr(10),' ')}» -> {topics}", not missing, f"ناقص: {missing}")
        c1 = build_comment_for(text, used_texts=used, cfg=cfg)
        used.add(c1)
        c2 = build_comment_for(text, used_texts=used, cfg=cfg)
        used.add(c2)
        print(f"      أمثلة: «{c1}» | «{c2}»")


def main():
    print("===== اختبارات محرك التعليقات =====")
    test_topic_detection()
    test_pools()
    test_build_comment_for()
    test_ai_module()
    test_scheduling_with_text()
    test_config_and_files()
    test_ticker_and_gem()
    test_epic_probabilistic()
    test_manager_tweets_demo()
    test_account_en_ratio()
    print(f"\n===== النتيجة: {PASS} نجح / {FAIL} فشل =====")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
