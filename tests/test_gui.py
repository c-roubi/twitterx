# -*- coding: utf-8 -*-
"""اختبارات الواجهة: خادم API + بوت تيليجرام + التحقق من الإعدادات (بدون متصفح)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="se_test_")
os.environ["SMARTENGAGE_DATA"] = _TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import server, telegram_bot  # noqa: E402
from app.config import load_config  # noqa: E402


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.svc, cls.token = server.create_server(0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, method, path, body=None, token=None, host=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("X-Token", self.token if token is None else token)
        req.add_header("Content-Type", "application/json")
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_page_served_with_token(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port) as r:
            html = r.read().decode()
        self.assertIn(self.token, html)
        self.assertNotIn("__TOKEN__", html)

    def test_auth_and_host(self):
        self.assertEqual(self.call("GET", "/api/state", token="bad")[0], 403)
        self.assertEqual(self.call("GET", "/api/state", host="evil.com")[0], 403)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)

    def test_account_lifecycle(self):
        s, r = self.call("POST", "/api/accounts", {"handle": "@Alice_1", "username": "Alice"})
        self.assertEqual(s, 200)
        aid = r["data"]["id"]
        self.assertEqual(r["data"]["handle"], "Alice_1")
        self.assertEqual(self.call("POST", "/api/accounts", {"handle": "alice_1"})[0], 400)  # مكرر
        self.assertEqual(self.call("POST", "/api/accounts", {"handle": "bad handle!"})[0], 400)
        s, r = self.call("PATCH", "/api/accounts/" + aid, {"enabled": False, "en_ratio": 0.5})
        self.assertFalse(r["data"]["enabled"])
        self.assertEqual(r["data"]["en_ratio"], 0.5)
        self.assertEqual(self.call("PATCH", "/api/accounts/" + aid, {"en_ratio": 3})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/accounts/" + aid)[0], 200)
        self.assertEqual(self.call("DELETE", "/api/accounts/" + aid)[0], 404)

    def test_id_not_reused_after_gap(self):
        a = self.call("POST", "/api/accounts", {"handle": "gap_a"})[1]["data"]["id"]
        b = self.call("POST", "/api/accounts", {"handle": "gap_b"})[1]["data"]["id"]
        self.call("DELETE", "/api/accounts/" + a)
        c = self.call("POST", "/api/accounts", {"handle": "gap_c"})[1]["data"]["id"]
        self.assertNotEqual(b, c)

    def test_config_validation_and_masking(self):
        s, r = self.call("POST", "/api/config", {"engagement": {"accounts_per_tweet": [9, 3]}})
        self.assertEqual(s, 400)
        self.assertEqual(self.call("POST", "/api/config", {"warp": {"proxy_port": 80}})[0], 400)
        self.assertEqual(self.call("POST", "/api/config", {"behavior": {"rest_day_chance": 2}})[0], 400)
        self.assertEqual(self.call("POST", "/api/config", {"behavior": {"active_hours": ["8am", "23:00"]}})[0], 400)
        self.assertEqual(self.call("POST", "/api/config", {"manager_handle": "no good"})[0], 400)
        tok = "123456789:" + "A" * 35
        s, r = self.call("POST", "/api/config", {"manager_handle": "@boss", "telegram": {"bot_token": tok, "admin_ids": [5]}})
        self.assertEqual(s, 200)
        self.assertEqual(r["data"]["telegram"]["bot_token"], server.MASK)
        self.assertEqual(load_config()["telegram"]["bot_token"], tok)
        self.assertEqual(load_config()["manager_handle"], "boss")
        # إرسال القيمة المقنّعة لا يغيّر التوكن
        self.call("POST", "/api/config", {"telegram": {"bot_token": server.MASK}})
        self.assertEqual(load_config()["telegram"]["bot_token"], tok)
        # قيم غير معروفة تُهمل
        self.call("POST", "/api/config", {"hacker": 1})
        self.assertNotIn("hacker", load_config())

    def test_comments(self):
        self.assertEqual(self.call("POST", "/api/comments", {"text": "   \n "})[0], 400)
        s, r = self.call("POST", "/api/comments", {"text": "a\nb\n\nc"})
        self.assertEqual(r["data"]["count"], 3)
        self.assertEqual(self.call("GET", "/api/comments")[1]["data"]["text"], "a\nb\nc\n")

    def test_tweet_validation(self):
        self.assertEqual(self.call("POST", "/api/tweets", {"url": "hello"})[0], 400)

    def test_pair_and_state(self):
        s, r = self.call("POST", "/api/telegram/pair")
        self.assertRegex(r["data"]["code"], r"^\d{6}$")
        s, r = self.call("GET", "/api/state")
        self.assertIn("engine", r["data"])
        self.assertEqual(r["data"]["engine"]["status"], "stopped")


class TelegramTests(unittest.TestCase):
    def make(self, admins, scheduled=None):
        self.cfg = load_config()
        self.cfg["telegram"]["admin_ids"] = list(admins)
        self.saved = []
        self.calls = []

        def sched(tid, text, cfg):
            self.calls.append((tid, text, cfg))
            return scheduled if scheduled is not None else {"queue": [{"actions": ["like", "retweet"]}, {"actions": ["like"]}]}
        return telegram_bot.TelegramBot(
            get_cfg=lambda: self.cfg,
            get_accounts=lambda: {"a": {"enabled": True}, "b": {"enabled": True}, "c": {"enabled": False}},
            schedule=sched, save_admins=self.saved.append, engine_running=lambda: True, sender=lambda *a: None)

    def test_url_extraction(self):
        ids = telegram_bot.extract_tweet_ids("x https://x.com/foo/status/1234567890?s=20 and https://twitter.com/i/web/status/9999999999 https://x.com/foo/status/1234567890")
        self.assertEqual(ids, ["1234567890", "9999999999"])
        self.assertEqual(telegram_bot.extract_tweet_ids("https://x.com/foo"), [])
        self.assertEqual(telegram_bot.extract_tweet_ids("https://evil.com/x.com/foo/status/1234567890"), [])

    def test_unauthorized(self):
        bot = self.make([])
        r = bot.handle_message(42, "https://x.com/u/status/1234567890")
        self.assertIn("غير مصرّح", r[0])
        self.assertEqual(self.calls, [])

    def test_pairing_one_time(self):
        bot = self.make([])
        code = bot.new_pair_code()
        self.assertIn("❌", bot.handle_message(7, "/pair 000000")[0])
        self.assertIn("تم الربط", bot.handle_message(7, "/pair " + code)[0])
        self.assertEqual(self.saved[-1], [7])
        self.cfg["telegram"]["admin_ids"] = [7]
        self.assertIn("❌", bot.handle_message(8, "/pair " + code)[0])  # الرمز استُهلك

    def test_schedule_link(self):
        bot = self.make([5])
        r = bot.handle_message(5, "تغريدة رائعة https://x.com/u/status/1234567890")
        self.assertIn("✅", r[0])
        tid, text, cfg = self.calls[0]
        self.assertEqual((tid, text), ("1234567890", "تغريدة رائعة"))
        self.assertEqual(cfg["engagement"]["spread_hours"], self.cfg["telegram"]["spread_hours"])

    def test_all_command_uses_enabled_accounts(self):
        bot = self.make([5])
        bot.handle_message(5, "/all https://x.com/u/status/1234567890")
        self.assertEqual(self.calls[0][2]["engagement"]["accounts_per_tweet"], [2, 2])

    def test_duplicate_and_no_link(self):
        bot = self.make([5], scheduled=False)
        self.assertIn("مجدولة مسبقاً", bot.handle_message(5, "https://x.com/u/status/1234567890")[0])
        self.assertIn("لم أجد", bot.handle_message(5, "مرحبا")[0])


if __name__ == "__main__":
    unittest.main()
