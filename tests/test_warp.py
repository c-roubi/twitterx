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


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.p1 = mock.patch.object(warp, "_STATE_PATH", os.path.join(self.tmp, "s.json"))
        self.p1.start()
        self.addCleanup(self.p1.stop)

    def test_rotates_every_n_accounts(self):
        cfg = {"warp": {"enabled": True, "accounts_per_ip": 2}}
        with mock.patch.object(warp, "ensure_ready", return_value=True), \
             mock.patch.object(warp, "rotate_ip", return_value=True) as rot:
            res = [warp.before_account(cfg, sleep=lambda s: None) for _ in range(6)]
        self.assertEqual(res, [False, False, True, False, True, False])
        self.assertEqual(rot.call_count, 2)

    def test_warp_overrides_proxy_list(self):
        from app import proxies
        cfg = {"warp": {"enabled": True}, "proxies": {"enabled": True, "list": ["http://1.2.3.4:80"]}}
        with mock.patch.object(proxies, "test_proxy", return_value=True):
            p = proxies.resolve_for("a1", cfg)
        self.assertEqual((p["host"], p["port"]), ("127.0.0.1", 40000))

    def test_warp_down_falls_back_direct(self):
        from app import proxies
        with mock.patch.object(proxies, "test_proxy", return_value=False):
            self.assertIsNone(proxies.resolve_for("a1", {"warp": {"enabled": True}}))
