# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import warp


class WarpTests(unittest.TestCase):
    def test_disabled_does_nothing(self):
        with mock.patch.object(warp, "_run") as r:
            self.assertFalse(warp.maybe_rotate(5, {"enabled": False, "accounts_per_ip": 5}))
            r.assert_not_called()

    def test_trigger_condition(self):
        cfg = {"enabled": True, "accounts_per_ip": 3}
        with mock.patch.object(warp, "rotate_ip", return_value=True) as rot:
            hits = [i for i in range(10) if warp.maybe_rotate(i, cfg)]
        self.assertEqual(hits, [3, 6, 9])
        self.assertEqual(rot.call_count, 3)

    def test_sequence(self):
        calls, sleeps = [], []
        def fake(*a, **k):
            calls.append(a)
            return 0, "Status update: Connected" if a == ("status",) else "ok"
        cfg = {"enabled": True, "isolate_proxy_mode": False}
        with mock.patch.object(warp, "_run", side_effect=fake):
            self.assertTrue(warp.rotate_ip(cfg, sleep=sleeps.append))
        self.assertEqual(calls[:2], [("disconnect",), ("connect",)])
        self.assertEqual(sleeps[:2], [2.5, 4.5])

    def test_disconnected_not_counted(self):
        with mock.patch.object(warp, "_run", return_value=(0, "Status update: Disconnected")):
            self.assertFalse(warp.is_connected())

    def test_missing_cli(self):
        with mock.patch.object(warp, "_run", return_value=(127, "x")):
            self.assertFalse(warp.rotate_ip({"enabled": True, "isolate_proxy_mode": False}, sleep=lambda s: None))


if __name__ == "__main__":
    unittest.main()
