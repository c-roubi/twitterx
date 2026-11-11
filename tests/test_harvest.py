# -*- coding: utf-8 -*-
"""اختبارات طبقة بيانات مخزن الحصاد (بلا متصفح)."""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("SMARTENGAGE_DATA", tempfile.mkdtemp(prefix="se_harvest_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import harvest  # noqa: E402


class HarvestDataTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(harvest.POOL_PATH):
            os.remove(harvest.POOL_PATH)

    def test_handle_from_href(self):
        f = harvest.handle_from_href
        self.assertEqual(f("https://x.com/alice"), "alice")
        self.assertEqual(f("/bob_crypto"), "bob_crypto")
        self.assertIsNone(f("/alice/status/123"))
        self.assertIsNone(f("/i/web/status/1"))
        self.assertIsNone(f("/home"))
        self.assertIsNone(f("/search"))
        self.assertIsNone(f("https://x.com/hashtag/btc"))
        self.assertIsNone(f(""))

    def test_parse_count(self):
        self.assertEqual(harvest.parse_count("25 reposts. Repost"), 25)
        self.assertEqual(harvest.parse_count("1,234"), 1234)
        self.assertEqual(harvest.parse_count("1.2K reposts"), 1200)
        self.assertEqual(harvest.parse_count("3.4M"), 3400000)
        self.assertEqual(harvest.parse_count("Repost"), 0)
        self.assertEqual(harvest.parse_count(None), 0)

    def test_add_dedup_and_skip_own_and_bad(self):
        self.assertEqual(harvest.add_candidates(["a", "b", "a"], "reposts"), 2)   # مكرر
        self.assertEqual(harvest.add_candidates(["b", "c"], "reposts"), 1)         # b موجود
        self.assertEqual(harvest.add_candidates(["@me"], own_handle="me"), 0)      # حساب صاحب الجلسة
        harvest.mark_bad("x")
        self.assertEqual(harvest.add_candidates(["x", "d"]), 1)                    # x غير مؤهّل سابقاً
        self.assertEqual(harvest.pool_size(), 4)                                   # a b c d

    def test_take_excludes_and_newest_first(self):
        import time
        harvest.add_candidates(["old1", "old2"], "reposts")
        time.sleep(0.01)
        harvest.add_candidates(["new1"], "reposts")
        got = harvest.take_candidates(exclude=["old1"], limit=10)
        self.assertNotIn("old1", got)
        self.assertEqual(got[0], "new1")                                          # الأحدث أولاً
        self.assertEqual(set(got), {"new1", "old2"})
        self.assertEqual(harvest.take_candidates(exclude=["OLD1", "OLD2", "NEW1"]), [])  # غير حساس لحالة الأحرف

    def test_remove_and_mark_bad(self):
        harvest.add_candidates(["a", "b"])
        harvest.remove("a")
        self.assertEqual(harvest.take_candidates(), ["b"])
        harvest.mark_bad("b")
        self.assertEqual(harvest.pool_size(), 0)
        self.assertEqual(harvest.add_candidates(["b"]), 0)                         # يبقى محظوراً

    def test_tweet_dedup_and_recently_harvested(self):
        self.assertFalse(harvest.recently_harvested("111"))
        harvest.add_candidates(["a"], "reposts", tweet_id="111")
        self.assertTrue(harvest.recently_harvested("111"))
        harvest.add_candidates([], tweet_id="222")                                # حتى بلا مرشّحين تُعلَّم
        self.assertTrue(harvest.recently_harvested("222"))

    def test_bad_and_tweet_ttl_prune(self):
        import time
        harvest.add_candidates(["a"], "reposts", tweet_id="t1")
        d = harvest._load()
        d["bad"]["oldbad"] = time.time() - harvest.BAD_TTL_S - 10
        d["tweets"]["t1"] = time.time() - harvest.TWEET_TTL_S - 10
        harvest._save(d)
        harvest.add_candidates(["b"])                                             # يُشغّل prune
        self.assertFalse(harvest.recently_harvested("t1"))
        self.assertEqual(harvest.add_candidates(["oldbad"]), 1)                   # انتهى حظره

    def test_pool_cap(self):
        orig = harvest.POOL_MAX
        harvest.POOL_MAX = 5
        try:
            for i in range(10):
                harvest.add_candidates(["u%d" % i])
            self.assertLessEqual(harvest.pool_size(), 5)
            kept = set(harvest.take_candidates(limit=99))
            self.assertIn("u9", kept)                                             # الأحدث محفوظ
            self.assertNotIn("u0", kept)                                          # الأقدم أُسقط
        finally:
            harvest.POOL_MAX = orig

    def test_corrupt_file_recovers(self):
        with open(harvest.POOL_PATH, "w") as f:
            f.write("{ broken")
        self.assertEqual(harvest.pool_size(), 0)
        self.assertEqual(harvest.add_candidates(["a"]), 1)

    def test_stats(self):
        harvest.add_candidates(["a", "b"], tweet_id="t")
        harvest.mark_bad("c")
        s = harvest.stats()
        self.assertEqual((s["pool"], s["bad"], s["tweets"]), (2, 1, 1))


if __name__ == "__main__":
    unittest.main()


import unittest.mock as _mock


class FollowPassPoolTests(unittest.TestCase):
    """run_follow_pass يستهلك المخزن أولاً ويعود للبحث عند فراغه."""

    def setUp(self):
        from app import grow
        self.grow = grow
        if os.path.exists(harvest.POOL_PATH):
            os.remove(harvest.POOL_PATH)
        self.cfg = {"follow": {"enabled": True, "delay_between_s": [1, 1], "visit_only_chance": 0.0,
                               "harvest": {"enabled": True, "refill_below": 8}}}
        self.state = {"caps": {"follows": 5}, "done": {"follows": 0}, "seen_users": [], "followed_handles": []}

    def human(self):
        h = _mock.MagicMock()
        return h

    def test_prefers_pool_no_search_when_pool_full(self):
        harvest.add_candidates(["pool%d" % i for i in range(12)], "reposts")   # مخزن ممتلئ (> refill_below)
        followed = []
        def fake_follow(page, human, handle, cfg, state, acc_id):
            followed.append(handle); return handle, "ok"   # الحلقة تتولّى زيادة العدّاد
        with _mock.patch.object(self.grow, "_follow_one", side_effect=fake_follow), \
             _mock.patch.object(self.grow, "_search_candidates") as search, \
             _mock.patch.object(harvest, "run_harvest") as rh, \
             _mock.patch.object(self.grow, "save_state"):
            n, st = self.grow.run_follow_pass(_mock.MagicMock(), "acc_001", self.cfg, self.state, self.human(), 5)
        self.assertTrue(followed and all(h.startswith("pool") for h in followed))
        search.assert_not_called()          # لم يلجأ للبحث
        rh.assert_not_called()              # المخزن ممتلئ: لا حصاد
        self.assertEqual(len(followed), 5)  # تابع حتى حدّ max_follows

    def test_harvests_to_refill_when_pool_low(self):
        def fake_harvest(page, acc_id, cfg, state, human, budget_s, **k):
            harvest.add_candidates(["fresh1", "fresh2"], "reposts"); return 2
        followed = []
        with _mock.patch.object(harvest, "run_harvest", side_effect=fake_harvest) as rh, \
             _mock.patch.object(self.grow, "_follow_one", side_effect=lambda *a, **k: (a[2], "ok")) as fo, \
             _mock.patch.object(self.grow, "_search_candidates") as search, \
             _mock.patch.object(self.grow, "save_state"):
            n, st = self.grow.run_follow_pass(_mock.MagicMock(), "acc_001", self.cfg, self.state, self.human(), 5)
        rh.assert_called_once()
        search.assert_not_called()
        self.assertTrue(fo.called)

    def test_falls_back_to_search_when_harvest_empty(self):
        with _mock.patch.object(harvest, "run_harvest", return_value=0), \
             _mock.patch.object(self.grow, "_search_candidates", return_value=(["searchX"], "ok")) as search, \
             _mock.patch.object(self.grow, "_follow_one", side_effect=lambda *a, **k: (a[2], "ok")), \
             _mock.patch.object(self.grow, "save_state"):
            n, st = self.grow.run_follow_pass(_mock.MagicMock(), "acc_001", self.cfg, self.state, self.human(), 5)
        search.assert_called_once()

    def test_excludes_already_seen_and_followed(self):
        harvest.add_candidates(["dup", "newguy"], "reposts")
        self.state["seen_users"] = ["dup"]
        taken = []
        with _mock.patch.object(harvest, "run_harvest", return_value=0), \
             _mock.patch.object(self.grow, "_follow_one", side_effect=lambda *a, **k: taken.append(a[2]) or (a[2], "ok")), \
             _mock.patch.object(self.grow, "_search_candidates", return_value=([], "ok")), \
             _mock.patch.object(self.grow, "save_state"):
            self.grow.run_follow_pass(_mock.MagicMock(), "acc_001", self.cfg, self.state, self.human(), 5)
        self.assertNotIn("dup", taken)
        self.assertIn("newguy", taken)

    def test_disabled_harvest_uses_search_only(self):
        harvest.add_candidates(["ignored"], "reposts")
        self.cfg["follow"]["harvest"]["enabled"] = False
        with _mock.patch.object(harvest, "run_harvest") as rh, \
             _mock.patch.object(self.grow, "_search_candidates", return_value=(["s1"], "ok")) as search, \
             _mock.patch.object(self.grow, "_follow_one", side_effect=lambda *a, **k: (a[2], "ok")), \
             _mock.patch.object(self.grow, "save_state"):
            self.grow.run_follow_pass(_mock.MagicMock(), "acc_001", self.cfg, self.state, self.human(), 5)
        rh.assert_not_called()
        search.assert_called_once()
