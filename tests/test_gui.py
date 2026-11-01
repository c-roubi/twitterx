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

        def sched(tid, text, cfg, chat_id=None):
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
        self.assertIn("فوري", r[0])  # الوضع الافتراضي: فوري
        self.cfg["telegram"]["instant"] = False
        r = bot.handle_message(5, "https://x.com/u/status/1234567891")
        self.assertEqual(self.calls[1][2]["engagement"]["spread_hours"], self.cfg["telegram"]["spread_hours"])
        self.assertIn("متدرّج", r[0])

    def test_all_command_uses_enabled_accounts(self):
        bot = self.make([5])
        bot.handle_message(5, "/all https://x.com/u/status/1234567890")
        self.assertEqual(self.calls[0][2]["engagement"]["accounts_per_tweet"], [2, 2])

    def test_duplicate_and_no_link(self):
        bot = self.make([5], scheduled=False)
        self.assertIn("مجدولة مسبقاً", bot.handle_message(5, "https://x.com/u/status/1234567890")[0])
        self.assertIn("لم أجد", bot.handle_message(5, "مرحبا")[0])


class PriorityTests(unittest.TestCase):
    """تغريدات عاجلة (أدمن): فورية، بالتتابع، بلا التقيّد بالحدود اليومية."""

    def setUp(self):
        from app import config, monitor
        self.monitor = monitor
        config.save_seen({})
        self.cfg = load_config()
        self.cfg["engagement"]["accounts_per_tweet"] = [2, 2]
        self.cfg["engagement"]["action_mix"] = {"like": 1.0, "retweet": 1.0, "comment": 0.0}
        self.accounts = {"p1": {"enabled": True}, "p2": {"enabled": True}}

    def test_priority_due_now_and_separated(self):
        e = self.monitor.schedule_for_tweet("111111111", "t", self.cfg, self.accounts, source="telegram", priority=True)
        self.assertTrue(e["priority"])
        self.assertTrue(all(t["due"] <= __import__("time").time() for t in e["queue"]))
        self.assertEqual(len(self.monitor.due_tasks_for("p1", priority=True)), 1)
        self.assertEqual(self.monitor.due_tasks_for("p1"), [])  # الجلسة العادية لا تلتقطها
        self.assertTrue(self.monitor.priority_pending("p2"))

    def test_normal_not_priority(self):
        self.monitor.schedule_for_tweet("222222222", "t", self.cfg, self.accounts)
        self.assertFalse(self.monitor.priority_pending("p1"))
        self.assertEqual(self.monitor.due_tasks_for("p1", priority=True), [])

    def _state(self):
        z = {"likes": 0, "retweets": 0, "comments": 0, "follows": 0}
        return {"done": dict(likes=6, retweets=1, comments=1, follows=0), "caps": dict(likes=6, retweets=1, comments=1, follows=0), "rest_day": True}

    def test_priority_ignores_caps_and_rest_day(self):
        from unittest import mock
        from app import routine
        self.monitor.schedule_for_tweet("333333333", "t", self.cfg, self.accounts, priority=True)
        state, human = self._state(), mock.MagicMock()
        with mock.patch.object(routine, "open_tweet_with_retry", return_value=(True, "ok")), \
             mock.patch.object(routine, "like_tweet", return_value=(True, "ok")) as lk, \
             mock.patch.object(routine, "retweet_tweet", return_value=(True, "ok")) as rt, \
             mock.patch.object(routine, "save_state"):
            routine._run_due_manager_tasks(mock.MagicMock(), "p1", self.cfg, state, human, priority=True)
        self.assertEqual((lk.call_count, rt.call_count), (1, 1))
        self.assertEqual(state["done"]["likes"], 7)  # تجاوز الحد 6 ويُعدّ
        self.assertEqual(self.monitor.due_tasks_for("p1", priority=True), [])  # لا تُعاد
        waits = [c.args for c in human.pause.call_args_list]
        self.assertTrue(all(b <= 6 for a, b in waits), waits)  # تأخيرات قصيرة

    def test_normal_still_respects_caps(self):
        from unittest import mock
        from app import routine
        self.monitor.schedule_for_tweet("444444444", "t", self.cfg, self.accounts)
        for t in self.monitor.load_seen()["444444444"]["queue"]:
            t["due"] = 0
        seen = self.monitor.load_seen(); seen["444444444"]["queue"][0]["due"] = 0; seen["444444444"]["queue"][1]["due"] = 0
        self.monitor.save_seen(seen)
        state = self._state(); state["rest_day"] = False
        with mock.patch.object(routine, "open_tweet_with_retry", return_value=(True, "ok")), \
             mock.patch.object(routine, "like_tweet", return_value=(True, "ok")) as lk, \
             mock.patch.object(routine, "retweet_tweet", return_value=(True, "ok")) as rt, \
             mock.patch.object(routine, "save_state"):
            routine._run_due_manager_tasks(mock.MagicMock(), "p1", self.cfg, state, mock.MagicMock())
        self.assertEqual((lk.call_count, rt.call_count), (0, 0))

    def test_open_failure_gives_up_instead_of_looping(self):
        from unittest import mock
        from app import routine
        self.monitor.schedule_for_tweet("555555555", "t", self.cfg, self.accounts, priority=True)
        state = self._state()
        with mock.patch.object(routine, "open_tweet_with_retry", return_value=(False, "network")), \
             mock.patch.object(routine, "save_state"):
            for _ in range(4):
                routine._run_due_manager_tasks(mock.MagicMock(), "p1", self.cfg, state, mock.MagicMock(), priority=True)
        self.assertEqual(self.monitor.due_tasks_for("p1", priority=True), [])

    def test_check_priority_tasks_sequential_and_backoff(self):
        from unittest import mock
        from app import routine
        routine._retry_after.clear()
        self.monitor.schedule_for_tweet("666666666", "t", self.cfg, self.accounts, priority=True)
        order = []
        def fake(acc_id, *a, **k):
            order.append(acc_id); return True
        with mock.patch.object(routine, "run_priority_session", side_effect=fake):
            n = routine.check_priority_tasks(self.cfg, self.accounts)
            n2 = routine.check_priority_tasks(self.cfg, self.accounts)  # المهام ما زالت قائمة: انتظار 30ث
        self.assertEqual((n, sorted(order)), (2, ["p1", "p2"]))
        self.assertEqual(n2, 0)

    def test_busy_account_lock(self):
        from app import routine
        self.assertTrue(routine._acquire("lock_x"))
        self.assertFalse(routine._acquire("lock_x"))
        self.assertFalse(routine.run_priority_session("lock_x", self.cfg, {"lock_x": {}}))
        routine._release("lock_x")
        self.assertTrue(routine._acquire("lock_x")); routine._release("lock_x")


class BotMenuTests(unittest.TestCase):
    """قوائم البوت: أزرار + تعديل الإعدادات والكلمات المفتاحية والتعليقات من تيليجرام."""

    @classmethod
    def setUpClass(cls):
        from app import config
        cls.config = config
        cls.svc = server.AppService()
        cls.ui = cls.svc.bot.ui
        cls.chat = 77

    def setUp(self):
        self.svc.save_cfg = None
        cfg = self.config.load_config()
        cfg["telegram"]["admin_ids"] = [self.chat]
        cfg["follow"]["queries"] = ["bitcoin", "ethereum", "solana"]
        cfg["follow"]["enabled"] = True
        cfg["engagement"]["accounts_per_tweet"] = [6, 12]
        self.config.save_config(cfg)
        self.ui.cancel(self.chat)
        self.calls = []
        self.svc.bot._api = lambda method, params=None, **k: self.calls.append((method, params)) or {"message_id": 1}

    def cfg(self):
        return self.config.load_config()

    def labels(self, view):
        return [l for row in view.rows for l, _ in row]

    def test_home_buttons(self):
        v = self.ui.home()
        self.assertTrue(all(len(d.encode()) <= 64 for row in v.rows for _, d in row))
        self.assertIn("🛠 الإعدادات", self.labels(v))
        self.assertIn("🔑 الكلمات المفتاحية", self.labels(v))

    def test_toggle_setting(self):
        v = self.ui.route(self.chat, "set:i:fe")
        self.assertFalse(self.cfg()["follow"]["enabled"])
        self.assertEqual(v.notice, "تم الحفظ ✅")
        self.ui.route(self.chat, "set:i:fe")
        self.assertTrue(self.cfg()["follow"]["enabled"])

    def test_range_pct_input_flow(self):
        v = self.ui.route(self.chat, "set:i:ap")
        self.assertTrue(self.ui.has_pending(self.chat))
        self.assertIn("أرسل القيمة", v.text)
        bad = self.ui.on_text(self.chat, "9-5")
        self.assertIn("❌", bad.text)
        self.assertEqual(self.cfg()["engagement"]["accounts_per_tweet"], [6, 12])
        self.assertTrue(self.ui.has_pending(self.chat))  # يبقى منتظراً لقيمة صحيحة
        ok = self.ui.on_text(self.chat, "٥-٩")  # أرقام عربية
        self.assertEqual(self.cfg()["engagement"]["accounts_per_tweet"], [5, 9])
        self.assertFalse(self.ui.has_pending(self.chat))
        self.ui.route(self.chat, "set:i:ml"); self.ui.on_text(self.chat, "80%")
        self.assertAlmostEqual(self.cfg()["engagement"]["action_mix"]["like"], 0.8)
        self.ui.route(self.chat, "set:i:ml"); self.assertIn("❌", self.ui.on_text(self.chat, "150").text)
        self.ui.route(self.chat, "set:i:ah"); self.ui.on_text(self.chat, "9:30-22:00")
        self.assertEqual(self.cfg()["behavior"]["active_hours"], ["09:30", "22:00"])

    def test_server_validation_surfaces(self):
        self.ui.route(self.chat, "set:i:ap")
        v = self.ui.on_text(self.chat, "9999-99999")  # خارج النطاق المسموح
        self.assertIn("❌", v.text)
        self.assertEqual(self.cfg()["engagement"]["accounts_per_tweet"], [6, 12])

    def test_navigation_cancels_pending(self):
        self.ui.route(self.chat, "set:i:ap")
        self.ui.route(self.chat, "home")
        self.assertFalse(self.ui.has_pending(self.chat))

    def test_keywords(self):
        self.ui.route(self.chat, "kw:add")
        self.ui.on_text(self.chat, "Bitcoin، doge\nsui, ")
        self.assertEqual(self.cfg()["follow"]["queries"], ["bitcoin", "ethereum", "solana", "doge", "sui"])  # بلا تكرار
        v = self.ui.route(self.chat, "kw:del:0")
        rm = [d for row in v.rows for _, d in row if d.startswith("kw:rm:")]
        self.ui.route(self.chat, rm[0])
        self.assertEqual(len(self.cfg()["follow"]["queries"]), 4)
        self.ui.route(self.chat, "kw:rm:0:ffffff")  # بصمة قديمة: لا يحذف شيئاً
        self.assertEqual(len(self.cfg()["follow"]["queries"]), 4)
        self.ui.route(self.chat, "kw:rep"); self.ui.on_text(self.chat, "a\nb")
        self.assertEqual(self.cfg()["follow"]["queries"], ["a", "b"])
        self.ui.route(self.chat, "kw:clr2")
        self.assertEqual(self.cfg()["follow"]["queries"], [])

    def test_comments(self):
        before = len(self.ui.svc.comments_list())
        self.ui.route(self.chat, "cm:add"); self.ui.on_text(self.chat, "قالب جديد ١\nقالب جديد ٢")
        self.assertEqual(len(self.ui.svc.comments_list()), before + 2)
        v = self.ui.route(self.chat, "cm:del:0")
        self.assertTrue(any(d.startswith("cm:rm:") for row in v.rows for _, d in row))

    def test_accounts_toggle(self):
        a = self.svc.add_account({"handle": "menu_acc"})
        v = self.ui.route(self.chat, "acc:0")
        self.assertTrue(any(l.endswith("@menu_acc") for l in self.labels(v)))
        self.ui.route(self.chat, "acc:t:%s:0" % a["id"])
        self.assertFalse(self.svc.account_view(a["id"], self.config.load_accounts()[a["id"]])["enabled"])
        self.svc.delete_account(a["id"])

    def test_engine_buttons(self):
        from unittest import mock
        with mock.patch.object(self.svc, "_engine_start") as st, mock.patch.object(self.svc.engine, "stop") as sp:
            self.ui.route(self.chat, "eng:dry")
            st.assert_called_once_with(True)
            self.ui.route(self.chat, "eng:stop")
            sp.assert_called_once()

    def test_callback_auth_and_edit(self):
        self.svc.bot.handle_callback(999, 5, "cb1", "set")  # غير أدمن
        self.assertEqual(self.calls[-1][0], "answerCallbackQuery")
        self.assertEqual(self.calls[-1][1]["text"], "غير مصرّح")
        self.calls.clear()
        self.svc.bot.handle_callback(self.chat, 5, "cb2", "set")
        methods = [c[0] for c in self.calls]
        self.assertEqual(methods, ["answerCallbackQuery", "editMessageText"])
        edit = self.calls[1][1]
        self.assertEqual(edit["parse_mode"], "HTML")
        self.assertIn("inline_keyboard", edit["reply_markup"])

    def test_messages_with_buttons(self):
        bot = self.svc.bot
        r = bot.handle_message(self.chat, "/menu")
        self.assertTrue(r[0].markup)
        bot.handle_message(self.chat, "/menu")
        self.ui.route(self.chat, "kw:add")
        r = bot.handle_message(self.chat, "pepe")  # نص عادي أثناء الانتظار ← يُعالَج كإدخال
        self.assertIn("pepe", self.cfg()["follow"]["queries"])
        self.ui.route(self.chat, "kw:add")
        bot.handle_message(self.chat, "/cancel")
        self.assertFalse(self.ui.has_pending(self.chat))
        r = bot.handle_message(self.chat, "hello")
        self.assertTrue(r[0].markup)  # لا رابط: زر القائمة

    def test_link_reply_has_progress_button(self):
        from unittest import mock
        a = self.svc.add_account({"handle": "lnk_acc"})
        try:
            with mock.patch.object(self.svc.bot, "engine_running", return_value=True):
                r = self.svc.bot.handle_message(self.chat, "https://x.com/u/status/9876543210123")
            self.assertTrue(any(d.startswith("tw:r:9876543210123") for row in r[0].markup for _, d in row), r[0])
            d = self.svc.tweet_detail("9876543210123")
            self.assertTrue(d["priority"] and d["total"] >= 1)
            v = self.ui.route(self.chat, "tw:r:9876543210123")
            self.assertIn("9876543210123", v.text)
            self.assertEqual(self.svc.completion_notices(), [])  # لم تكتمل بعد
        finally:
            self.svc.delete_account(a["id"])

    def test_completion_notice_once(self):
        from app import config
        seen = config.load_seen()
        seen["5550001"] = {"scheduled": True, "url": "u", "notify_chat": self.chat, "completed_at": "2026-01-01T00:00:00",
                           "queue": [{"account": "x", "actions": ["like"], "done": ["like"]}]}
        config.save_seen(seen)
        n = self.svc.completion_notices()
        self.assertEqual((len(n), n[0][0]), (1, self.chat))
        self.assertIn("اكتمل", n[0][1])
        self.assertEqual(self.svc.completion_notices(), [])


if __name__ == "__main__":
    unittest.main()
