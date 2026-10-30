# -*- coding: utf-8 -*-
"""إدارة ملفات الإعدادات والحسابات (مع حماية البيانات بالكتابة الذرية)."""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.json")
COMMENTS_PATH = os.path.join(DATA_DIR, "comments.txt")
COMMENTS_CRYPTO_PATH = os.path.join(DATA_DIR, "comments_crypto.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")
FOLLOWED_GLOBAL_PATH = os.path.join(DATA_DIR, "followed_global.json")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
STATE_DIR = os.path.join(DATA_DIR, "state")
LOGS_DIR = os.path.join(DATA_DIR, "logs")

DEFAULT_CONFIG = {
    "manager_handle": "",
    "monitor": {
        "method": "guest",
        "interval_min": [6, 14],
        "use_account": None
    },
    "resilience": {
        "network_retries": 2,
        "retry_base_delay_s": 5.0,
        "action_max_attempts": 3,
        "page_load_timeout_s": 60000,
        "goto_timeout_s": 60000,
        "monitor_fetch_retries": 2
    },
    "engagement": {
        "accounts_per_tweet": [6, 12],
        "spread_hours": [0.5, 4.0],
        "action_mix": {"like": 0.75, "retweet": 0.18, "comment": 0.3},
        "max_comments_per_tweet": 8
    },
    "comments": {
        "contextual": True,
        "en_ratio": 0.9,
        "ai": {
            "enabled": False,
            "provider": "gemini",
            "api_key": "",
            "model": "gemini-1.5-flash",
            "min_interval_s": 30
        }
    },
    "behavior": {
        "active_hours": ["08:00", "23:00"],
        "max_likes_per_day": [25, 45],
        "max_retweets_per_day": [2, 6],
        "max_comments_per_day": [1, 4],
        "min_action_delay_s": [8, 25],
        "max_action_delay_s": [45, 120],
        "session_minutes": [15, 35],
        "sessions_per_day": [1, 3],
        "rest_day_chance": 0.12
    },
    "follow": {
        "enabled": True,
        "daily_target": [5, 10],
        "per_session": [1, 3],
        "delay_between_s": [90, 300],
        "ramp_days": 7,
        "require_bio": True,
        "min_followers": 50,
        "max_followers": 150000,
        "skip_verified": True,
        "visit_only_chance": 0.25,
        "queries": ["bitcoin", "crypto", "altcoins", "ethereum", "solana", "$BTC", "crypto trading", "#crypto", "diamond hands", "altseason"]
    },
    "warmup": {
        "enabled": True,
        "session_minutes": [8, 15],
        "browse_probability": 0.8,
        "view_profiles": [1, 2],
        "max_search_scrolls": 3
    },
    "proxies": {
        "enabled": False,
        "list": [],
        "fallback_to_direct": True,
        "test_timeout": 10,
        "rotate": False
    },
    "browser": {
        "headless": False,
        "slow_mo": 30,
        "max_context_minutes": 20
    }
}

DEFAULT_COMMENTS = """\
الله يعطيك العافية على هذا الطرح المميز
ماشاء الله تبارك الله، موضوع يستحق القراءة
أفكار رائعة كالعادة، شكراً لك
محتوى مفيد جداً، استمر
مقال موفق، استفدت منه كثيراً
نقاط مهمة، يعطيك العافية
ماشاء الله، شرح واضح ومبسط
شكراً على المجهود الواضح في كل تغريدة
معلومات قيّمة، بورك فيك
طرح جميل ومميز كالعادة
هذا ما نحتاجه فعلاً، جزاك الله خيراً
رؤية واضحة، شكراً على المشاركة
كلام في الصميم
أسلوب مميز في الطرح، يعطيك العافية
استفدت من التغريدة، شكراً لك
أفكار تستحق الانتشار
ماشاء الله، توفيق مستمر
موضوع شيق، ننتظر المزيد
عبارات مؤثرة وصادقة
تغريدة في قمة الروعة
نحتاج مثل هذه الطروحات، شكراً
مجهود مقدر، الله يوفقك
ملخص رائع ومفيد
وصلت الفكرة بوضوح، شكراً لك
أبدعت كالعادة
محتوى يلامس الواقع، بوركت
شكراً على هذه الإضافة القيمة
طرح راقٍ وواضح
كلمات لها وقع جميل
تغريدة مميزة بكل المقاييس
"""


def ensure_dirs():
    for d in (DATA_DIR, PROFILES_DIR, SESSIONS_DIR, STATE_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)


def ensure_initial_files():
    """إنشاء ملفات الإعدادات الافتراضية إن لم تكن موجودة."""
    ensure_dirs()
    if not os.path.exists(CONFIG_PATH):
        _atomic_save(DEFAULT_CONFIG, CONFIG_PATH)
    if not os.path.exists(ACCOUNTS_PATH):
        _atomic_save({}, ACCOUNTS_PATH)
    if not os.path.exists(COMMENTS_PATH):
        with open(COMMENTS_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_COMMENTS)
    if not os.path.exists(COMMENTS_CRYPTO_PATH):
        try:
            from .crypto_comments import DEFAULT_POOLS
            _atomic_save(DEFAULT_POOLS, COMMENTS_CRYPTO_PATH)
        except Exception:
            pass


def _deep_merge(base, override):
    import copy
    out = copy.deepcopy(base) if base is not None else {}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _atomic_save(data, path):
    """
    الكتابة الذرية لحماية ملفات JSON من التلف.
    يحفظ البيانات في ملف مؤقت أولاً، ثم يستبدل الملف الأصلي فجأة.
    بهذه الطريقة لو انقطعت الكهرباء أو تم إيقاف السكربت، لا يتلف الملف الأصلي.
    """
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # إجبار نظام التشغيل على حفظ الملف في القرص فوراً
        os.replace(tmp_path, path) # استبدال الملف الأصلي بالجديد بأمان
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise e


def load_config():
    ensure_initial_files()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
    except Exception:
        user = {}
    return _deep_merge(DEFAULT_CONFIG, user)


def save_config(cfg):
    ensure_initial_files()
    _atomic_save(cfg, CONFIG_PATH)


def load_accounts():
    ensure_initial_files()
    try:
        with open(ACCOUNTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_accounts(accounts):
    ensure_initial_files()
    _atomic_save(accounts, ACCOUNTS_PATH)


def load_comments():
    ensure_initial_files()
    try:
        with open(COMMENTS_PATH, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        return lines or DEFAULT_COMMENTS.strip().splitlines()
    except Exception:
        return DEFAULT_COMMENTS.strip().splitlines()


def load_seen():
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen(seen):
    _atomic_save(seen, SEEN_PATH)


def load_global_followed():
    """Lower-cased handles already followed by ANY of our accounts (avoid identical follow patterns)."""
    try:
        with open(FOLLOWED_GLOBAL_PATH, encoding="utf-8") as f:
            return {str(h).lower() for h in json.load(f)}
    except Exception:
        return set()


def save_global_followed(handles):
    _atomic_save(sorted({str(h).lower() for h in handles})[-20000:], FOLLOWED_GLOBAL_PATH)


def state_path(acc_id):
    return os.path.join(STATE_DIR, f"{acc_id}.json")


def load_state(acc_id):
    try:
        with open(state_path(acc_id), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(acc_id, state):
    _atomic_save(state, state_path(acc_id))


def enabled_accounts(accounts):
    return [aid for aid, a in accounts.items() if a.get("enabled", True)]