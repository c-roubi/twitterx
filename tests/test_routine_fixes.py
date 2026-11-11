# -*- coding: utf-8 -*-
"""اختبارات إصلاحات الروتين: الجلسات، مهام المدير، الإحماء، المتابعات، حماية الحالة، الملخص.

التشغيل:  python3 tests/test_routine_fixes.py
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="se_test_rf_")
os.environ["SMARTENGAGE_DATA"] = _TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, grow, humanizer, ledger, monitor, routine, summary  # noqa: E402
config.ensure_initial_files()


class LognormalTests(unittest.TestCase):
    def test_spread_inside_bounds(self):
        vals = [humanizer.Human._lognormal(15, 35) for _ in range(3000)]
        self.assertGreaterEqual(min(vals), 15)
        self.assertLessEqual(max(vals), 35)
        self.assertGreater(len({round(v) for v in vals}), 10)      # ليست قيمة ثابتة
        self.assertLess(sorted(vals)[1500], 30)                    # الوسيط ليس عند الحد الأعلى


class SessionScheduleTests(unittest.TestCase):
    def _state(self, offsets_min, run=0, last_end=0):
        now = time.time()
        return {"date": routine._today(), "sessions_run": run, "last_session_end_ts": last_end,
                "sessions": [{"start": "00:00", "minutes": 30, "ts": now + m * 60} for m in offsets_min]}

    def test_only_one_session_per_slot(self):
        st = self._state([-10, 120, 300])
        self.assertTrue(routine._next_session_due(st))
        st["sessions_run"] = 1
        st["last_session_end_ts"] = time.time()
        self.assertFalse(routine._next_session_due(st))           # الجلسة الثانية موعدها لم يأتِ

    def test_missed_slots_merge_into_one(self):
        st = self._state([-300, -200, -100])                       # البرنامج كان مغلقاً
        self.assertTrue(routine._next_session_due(st))
        self.assertEqual(st["sessions_run"], 2)                     # تُدمج: جلسة واحدة فقط ستعمل

    def test_min_gap_after_previous_session(self):
        st = self._state([-30, -5], run=1, last_end=time.time() - 60)
        self.assertFalse(routine._next_session_due(st))

    def test_random_sessions_spread_and_have_ts(self):
        cfg = config.load_config()
        cfg["behavior"]["sessions_per_day"] = [3, 3]
        ss = humanizer.random_sessions(cfg)
        self.assertEqual(len(ss), 3)
        self.assertTrue(all("ts" in s for s in ss))
        self.assertEqual([s["ts"] for s in ss], sorted(s["ts"] for s in ss))

    def test_midnight_window(self):
        cfg = config.load_config()
        cfg["behavior"]["active_hours"] = ["20:00", "02:00"]
        cfg["behavior"]["sessions_per_day"] = [3, 3]
        ss = humanizer.random_sessions(cfg)
        span = ss[-1]["ts"] - ss[0]["ts"]
        self.assertLess(span, 6 * 3600 + 60)                      # كل الجلسات داخل نافذة الست ساعات


class DuePlanTests(unittest.TestCase):
    def test_plan_keeps_relogin_and_crash(self):
        config.save_state("cp1", {"date": "2000-01-01", "needs_relogin": True, "last_session_crash": 123.0})
        st = routine.ensure_daily_plan("cp1", config.load_config())
        self.assertTrue(st["needs_relogin"])
        self.assertEqual(st["last_session_crash"], 123.0)

    def test_relogin_rechecked_after_cooldown(self):
        self.assertFalse(routine._relogin_blocked({"needs_relogin": True, "relogin_checked_at": time.time() - 3600}))
        self.assertTrue(routine._relogin_blocked({"needs_relogin": True, "relogin_checked_at": time.time()}))


class StateGuardTests(unittest.TestCase):
    def test_older_day_does_not_overwrite_newer_plan(self):
        config.save_state("g1", {"date": "2026-11-12", "done": {"likes": 1}})
        config.save_state("g1", {"date": "2026-11-11", "done": {"likes": 40}})
        self.assertEqual(config.load_state("g1")["date"], "2026-11-12")

    def test_ledger_ignores_stale_day(self):
        day = "2030-01-02"
        ledger.record_state("l1", {"date": "2026-11-12", "done": {"likes": 0}}, when=day)
        ledger.record_state("l1", {"date": "2026-11-12", "done": {"likes": 5}}, when=day)
        ledger.record_state("l1", {"date": "2026-11-11", "done": {"likes": 20}}, when=day)   # كتابة قديمة
        ledger.record_state("l1", {"date": "2026-11-12", "done": {"likes": 6}}, when=day)
        self.assertEqual(ledger.day(day)["accounts"]["l1"]["likes"], 6)

    def test_ledger_rest_day_per_day(self):
        day = routine._today()
        ledger.record_state("r1", {"date": day, "rest_day": True, "done": {}}, when=day)
        self.assertTrue(ledger.day(day)["accounts"]["r1"]["rest_day"])


class WarmupTests(unittest.TestCase):
    base = {"warmup_at": "00:00", "warmup_ts": 1.0, "warmup_done": False}

    def test_not_on_rest_day(self):
        self.assertFalse(routine.warmup_due(dict(self.base, rest_day=True)))
        self.assertTrue(routine.warmup_due(dict(self.base)))

    def test_retry_delay_and_give_up(self):
        st = dict(self.base, date=routine._today())
        with mock.patch.object(routine, "save_state"):
            routine._warmup_failed("w1", st, "net")
            self.assertFalse(routine.warmup_due(st))                # مهلة 30 دقيقة
            routine._warmup_failed("w1", st, "net")
            routine._warmup_failed("w1", st, "net")
        self.assertTrue(st["warmup_done"])                          # بعد 3 محاولات: لا إعادة اليوم


class FollowTests(unittest.TestCase):
    def test_visit_only_keeps_eligible_candidate(self):
        cfg = {"follow": {"enabled": True, "delay_between_s": [1, 1], "harvest": {"enabled": True, "refill_below": 0}}}
        state = {"caps": {"follows": 5}, "done": {"follows": 0}, "seen_users": ["a", "b"], "followed_handles": []}
        with mock.patch.object(grow.harvest, "take_candidates", return_value=["v1", "f1"]), \
             mock.patch.object(grow.harvest, "remove") as rm, \
             mock.patch.object(grow.harvest, "pool_size", return_value=10), \
             mock.patch.object(grow, "_follow_one", side_effect=lambda p, h, handle, *a: (None, "visit-only") if handle == "v1" else (handle, "ok")), \
             mock.patch.object(grow, "save_state"):
            n, _ = grow.run_follow_pass(mock.MagicMock(), "x", cfg, state, mock.MagicMock(), 5)
        self.assertEqual(n, 1)
        self.assertEqual([c.args[0] for c in rm.call_args_list], ["f1"])   # v1 بقي في المخزن
        self.assertEqual(state["seen_users"], ["a", "b", "f1"])              # ترتيب الإدراج محفوظ

    def test_remember_trims_oldest(self):
        lst = [str(i) for i in range(5)]
        grow._remember(lst, "new", 5)
        self.assertEqual(lst, ["1", "2", "3", "4", "new"])

    def test_no_harvest_without_budget(self):
        cfg = {"follow": {"enabled": True}, "warmup": {"browse_probability": 0}}
        state = {"caps": {"follows": 0}, "done": {"follows": 0}}
        with mock.patch.object(grow.harvest, "run_harvest") as hv:
            grow.crypto_activity(mock.MagicMock(), "x", cfg, state, mock.MagicMock(), 60)
        hv.assert_not_called()


class ManagerCommentOrderTests(unittest.TestCase):
    def _cfg(self):
        cfg = config.load_config()
        cfg["comments"]["ai"]["enabled"] = True
        return cfg

    def test_manager_prefers_template_then_ai(self):
        from app import comments
        with mock.patch.object(comments, "_contextual_comment", return_value="template reply here"), \
             mock.patch("app.ai_comments.generate_ai_comment", return_value="ai reply here") as ai:
            t = comments.build_comment_for("btc breakout", cfg=self._cfg(), manager=True, reserve=False)
        self.assertEqual(t, "template reply here")
        ai.assert_not_called()

    def test_manager_falls_back_to_ai(self):
        from app import comments
        with mock.patch.object(comments, "_contextual_comment", return_value=None), \
             mock.patch("app.ai_comments.generate_ai_comment", return_value="ai reply about btc"):
            t = comments.build_comment_for("btc breakout", cfg=self._cfg(), manager=True, reserve=False)
        self.assertEqual(t, "ai reply about btc")

    def test_other_tweets_keep_ai_first(self):
        from app import comments
        with mock.patch.object(comments, "_contextual_comment", return_value="template reply here"), \
             mock.patch("app.ai_comments.generate_ai_comment", return_value="ai reply about btc"):
            t = comments.build_comment_for("btc breakout", cfg=self._cfg(), reserve=False)
        self.assertEqual(t, "ai reply about btc")


class SummaryTests(unittest.TestCase):
    def test_cancelled_counts_consistent(self):
        d = {"date": "2026-11-07", "totals": {"likes": 0, "retweets": 0, "comments": 0, "follows": 0},
             "accounts": [], "followed_unique": [], "attention": [],
             "tweets": [{"id": "1732272480", "priority": True, "source": "telegram", "accounts": 4,
                         "done": 9, "total": 12, "failed": 3, "deleted": False, "cancelled": True, "completed": False}]}
        html = summary.format_html(d)
        self.assertIn("9 من 9", html)
        self.assertNotIn("9 من 0", html)


if __name__ == "__main__":
    unittest.main()
