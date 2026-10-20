# -*- coding: utf-8 -*-
"""اختبارات طبقة المرونة: تصنيف الأخطاء + إعادة المحاولة + كشف الحذف + تخطي الفشل.

التشغيل:  python3 tests/test_resilience.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.config as config
import app.resilience as resilience
from app import monitor

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


# ---------------------------------------------------------------------------
# مزيفات لكشف التغريدة المحذوفة (is_tweet_deleted)
# ---------------------------------------------------------------------------
class FakeLocator:
    def __init__(self, text):
        self._text = text

    def inner_text(self, timeout=None):
        return self._text


class FakePage:
    def __init__(self, text):
        self._text = text

    def locator(self, sel):
        return FakeLocator(self._text)


class RaisingLocator:
    def inner_text(self, timeout=None):
        raise RuntimeError("boom")


class RaisingPage:
    def locator(self, sel):
        return RaisingLocator()


def test_classify_error():
    print("1) تصنيف الأخطاء (classify_error):")
    check("ConnectionError -> network", resilience.classify_error(ConnectionError("x")) == "network")
    check("net::err -> network", resilience.classify_error(Exception("net::ERR_CONNECTION_RESET")) == "network")
    check("TimeoutError -> timeout", resilience.classify_error(TimeoutError("x")) == "timeout")
    check("timed out -> timeout", resilience.classify_error(Exception("request timed out")) == "timeout")
    check("429 -> rate_limit", resilience.classify_error(Exception("HTTP 429 Too Many Requests")) == "rate_limit")
    check("قيمة عادية -> other", resilience.classify_error(ValueError("boom")) == "other")


def test_is_retryable():
    print("2) هل الخطأ قابل لإعادة المحاولة (is_retryable):")
    check("network -> True", resilience.is_retryable("network") is True)
    check("timeout -> True", resilience.is_retryable("timeout") is True)
    check("rate_limit -> False", resilience.is_retryable("rate_limit") is False)
    check("other -> False", resilience.is_retryable("other") is False)


def test_error_kind_from_msg():
    print("3) تصنيف رسالة الخطأ النصية (error_kind_from_msg):")
    check("network-error -> network", resilience.error_kind_from_msg("network-error") == "network")
    check("timeout -> timeout", resilience.error_kind_from_msg("timeout") == "timeout")
    check("error:net::err -> network", resilience.error_kind_from_msg("error:net::err_connection_reset") == "network")
    check("error:timed out -> timeout", resilience.error_kind_from_msg("error:request timed out") == "timeout")
    check("error:429 -> rate_limit", resilience.error_kind_from_msg("error:429 too many requests") == "rate_limit")
    check("'' -> other", resilience.error_kind_from_msg("") == "other")
    check("None -> other", resilience.error_kind_from_msg(None) == "other")
    check("login-wall -> other", resilience.error_kind_from_msg("login-wall") == "other")


def test_backoff_delay():
    print("4) فترة الانتظار المتدرجة (backoff_delay):")
    d0 = resilience.backoff_delay(0, base=5.0)
    d1 = resilience.backoff_delay(1, base=5.0)
    d2 = resilience.backoff_delay(2, base=5.0)
    check("القيم موجبة", d0 > 0 and d1 > 0 and d2 > 0)
    check("تتزايد", d0 < d1 < d2, f"{d0:.2f} {d1:.2f} {d2:.2f}")


def test_retry():
    print("5) إعادة المحاولة (retry):")
    orig_sleep = resilience.time.sleep
    resilience.time.sleep = lambda *a, **k: None
    try:
        calls = {"n": 0}

        def flaky_ok():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("net::err")
            return "done"

        ok, val = resilience.retry(flaky_ok, attempts=2, base_delay=0.0)
        check("نجاح بعد فشلين", ok is True and val == "done" and calls["n"] == 3, f"ok={ok} val={val} n={calls['n']}")

        calls2 = {"n": 0}

        def always_fail():
            calls2["n"] += 1
            raise TimeoutError("timed out")

        ok2, err = resilience.retry(always_fail, attempts=2, base_delay=0.0)
        check("استسلام بعد المحاولات", ok2 is False and calls2["n"] == 3 and isinstance(err, TimeoutError), f"n={calls2['n']}")

        calls3 = {"n": 0}

        def non_retryable():
            calls3["n"] += 1
            raise ValueError("bad input")

        ok3, err3 = resilience.retry(non_retryable, attempts=2, base_delay=0.0)
        check("غير قابل للمحاولة -> فوراً", ok3 is False and calls3["n"] == 1, f"n={calls3['n']}")
    finally:
        resilience.time.sleep = orig_sleep


def test_is_tweet_deleted():
    print("6) كشف التغريدة المحذوفة (is_tweet_deleted):")
    check("EN 'this post was deleted'", resilience.is_tweet_deleted(FakePage("This post was deleted by author")) is True)
    check("EN 'page doesn't exist'", resilience.is_tweet_deleted(FakePage("Hmm...this page doesn't exist")) is True)
    check("AR 'تم حذف هذه التغريدة'", resilience.is_tweet_deleted(FakePage("تم حذف هذه التغريدة")) is True)
    check("AR 'تغريدة غير متوفرة'", resilience.is_tweet_deleted(FakePage("هذه التغريدة غير متوفرة")) is True)
    check("نص عادي -> False", resilience.is_tweet_deleted(FakePage("Bitcoin at all time high")) is False)
    check("نص فارغ -> False", resilience.is_tweet_deleted(FakePage("")) is False)
    check("استثناء -> False", resilience.is_tweet_deleted(RaisingPage()) is False)


# ---------------------------------------------------------------------------
# أدوات اختبار دوال المراقبة عبر توجيه SEEN_PATH إلى مجلد مؤقت
# ---------------------------------------------------------------------------
def _seen_entry(tid="123", extra=None):
    entry = {
        "tweet_id": tid,
        "url": f"https://x.com/twitter/status/{tid}",
        "text": "tweet",
        "scheduled": True,
        "queue": [
            {"account": "acc_001", "actions": ["like", "retweet"], "due": 0, "done": []},
            {"account": "acc_002", "actions": ["like"], "due": 0, "done": []},
        ],
    }
    if extra:
        entry.update(extra)
    return {tid: entry}


def _with_seen(entries, fn):
    old = config.SEEN_PATH
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "seen.json")
    config.SEEN_PATH = path
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    try:
        fn()
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    finally:
        config.SEEN_PATH = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_tweet_deleted():
    print("7) وسم التغريدة المحذوفة (mark_tweet_deleted):")
    def run():
        monitor.mark_tweet_deleted("123")
    data = _with_seen(_seen_entry(), run)
    e = data["123"]
    check("deleted=True", e.get("deleted") is True)
    check("deleted_at محفوظ", bool(e.get("deleted_at")))
    check("الجدولة باقية (إزالة منطقية)", e.get("scheduled") is True)


def test_give_up_action():
    print("8) التخلي النهائي عن عملية (give_up_action):")
    def run():
        monitor.give_up_action("123", "acc_001", "like")
    data = _with_seen(_seen_entry(), run)
    t = data["123"]["queue"][0]
    check("like في failed", "like" in t.get("failed", []), str(t.get("failed")))


def test_record_action_attempt():
    print("9) عدّ محاولات العملية (record_action_attempt):")
    def run():
        monitor.record_action_attempt("123", "acc_001", "like")
        monitor.record_action_attempt("123", "acc_001", "like")
        monitor.record_action_attempt("123", "acc_001", "retweet")
    data = _with_seen(_seen_entry(), run)
    t = data["123"]["queue"][0]
    check("like=2", t.get("attempts", {}).get("like") == 2, str(t.get("attempts")))
    check("retweet=1", t.get("attempts", {}).get("retweet") == 1, str(t.get("attempts")))


def test_due_tasks_for_skips():
    print("10) due_tasks_for يتخطى المحذوفة والمتخلى عنها:")
    deleted = _seen_entry("111", extra={"deleted": True})
    tasks = monitor.due_tasks_for("acc_001", deleted)
    check("محذوفة -> لا مهام", len(tasks) == 0, str(tasks))

    entry = _seen_entry("222")
    entry["222"]["queue"][0]["failed"] = ["like"]
    tasks = monitor.due_tasks_for("acc_001", entry)
    mine = [t for t in tasks if t["tweet_id"] == "222"]
    check("تخطي failed: يبقى retweet فقط", bool(mine) and mine[0]["remaining"] == ["retweet"], str(mine))


def test_config_resilience_defaults():
    print("11) افتراضيات قسم resilience في الإعدادات:")
    d = config.DEFAULT_CONFIG["resilience"]
    check("network_retries=2", d.get("network_retries") == 2)
    check("retry_base_delay_s=5.0", d.get("retry_base_delay_s") == 5.0)
    check("action_max_attempts=3", d.get("action_max_attempts") == 3)
    check("page_load_timeout_s=60000", d.get("page_load_timeout_s") == 60000)
    check("goto_timeout_s=60000", d.get("goto_timeout_s") == 60000)
    check("monitor_fetch_retries=2", d.get("monitor_fetch_retries") == 2)
    cfg = config.load_config()
    check("load_config يضمّ resilience", "resilience" in cfg and cfg["resilience"].get("network_retries") == 2)


def main():
    test_classify_error()
    test_is_retryable()
    test_error_kind_from_msg()
    test_backoff_delay()
    test_retry()
    test_is_tweet_deleted()
    test_mark_tweet_deleted()
    test_give_up_action()
    test_record_action_attempt()
    test_due_tasks_for_skips()
    test_config_resilience_defaults()
    print(f"\nالنتيجة: {PASS} نجحت / {FAIL} فشلت")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
