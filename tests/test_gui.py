# -*- coding: utf-8 -*-
"""اختبارات الواجهة: خادم API + بوت تيليجرام + التحقق من الإعدادات (بدون متصفح)."""
import json
import os
import sys
import tempfile
import threading
import time
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
        for x in (b, c):
            self.call("DELETE", "/api/accounts/" + x)

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

        def sched(tid, text, cfg, chat_id=None, **kw):
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

    def mark_ready(self, aid):
        os.makedirs(self.config.SESSIONS_DIR, exist_ok=True)
        with open(os.path.join(self.config.SESSIONS_DIR, aid + ".json"), "w") as f:
            f.write("{}")

    def test_instant_full_mix_and_skipped(self):
        from unittest import mock
        ids = [self.svc.add_account({"handle": "mix_%d" % i})["id"] for i in range(4)]
        for aid in ids[:3]:
            self.mark_ready(aid)           # الرابع بلا جلسة
        try:
            with mock.patch.object(self.svc.bot, "engine_running", return_value=True):
                r = self.svc.bot.handle_message(self.chat, "https://x.com/u/status/1112223334445")
            self.assertIn("تُخطّيت", r[0])
            self.assertIn("@mix_3", r[0])
            e = self.config.load_seen()["1112223334445"]
            actions = [sorted(q["actions"]) for q in e["queue"]]
            self.assertEqual(len(actions), 3)
            self.assertTrue(all(a == ["comment", "like", "retweet"] for a in actions), actions)  # كل حساب يفعل الثلاثة
            self.assertIn("@mix_3", e["skipped"])
        finally:
            for aid in ids:
                self.svc.delete_account(aid)

    def test_no_ready_accounts_message(self):
        a = self.svc.add_account({"handle": "noready"})
        try:
            r = self.svc.bot.handle_message(self.chat, "https://x.com/u/status/1119998887776")
            self.assertIn("لا توجد حسابات جاهزة", r[0])
        finally:
            self.svc.delete_account(a["id"])

    def test_link_reply_has_progress_button(self):
        from unittest import mock
        a = self.svc.add_account({"handle": "lnk_acc"})
        self.mark_ready(a["id"])
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


class AITests(unittest.TestCase):
    def setUp(self):
        from app import ai_comments
        self.ai = ai_comments
        ai_comments._last_call = 0.0
        self.cfg = {"provider": "gemini", "api_key": "KEY", "model": "", "min_interval_s": 30}

    def gem(self, text):
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    def test_success_and_headers(self):
        from unittest import mock
        seen = {}
        def fake(url, headers, body, ctx, timeout=20):
            seen.update(url=url, headers=headers)
            return self.gem('"Nice $BTC breakout!"')
        with mock.patch.object(self.ai, "_call_api", side_effect=fake):
            out = self.ai.generate_ai_comment("BTC breaks 70k", self.cfg)
        self.assertEqual(out, "Nice $BTC breakout!")
        self.assertNotIn("KEY", seen["url"])  # المفتاح في الترويسة لا في الرابط
        self.assertEqual(seen["headers"]["x-goog-api-key"], "KEY")
        self.assertIn("gemini-2.5-flash", seen["url"])

    def test_model_404_falls_back(self):
        from unittest import mock
        tried = []
        def fake(url, headers, body, ctx, timeout=20):
            tried.append(url.split("models/")[1].split(":")[0])
            if len(tried) == 1:
                raise self.ai.AIError("HTTP 404: model not found")
            return self.gem("ok comment")
        with mock.patch.object(self.ai, "_call_api", side_effect=fake):
            self.assertEqual(self.ai.generate_ai_comment("tweet", self.cfg), "ok comment")
        self.assertEqual(tried, self.ai.GEMINI_FALLBACKS[:2])

    def test_retired_model_name_ignored(self):
        from unittest import mock
        urls = []
        def fake(url, headers, body, ctx, timeout=20):
            urls.append(url); return self.gem("fine")
        with mock.patch.object(self.ai, "_call_api", side_effect=fake):
            self.assertEqual(self.ai.generate_ai_comment("x", dict(self.cfg, model="gemini-1.5-flash")), "fine")
        self.assertIn("gemini-2.5-flash", urls[0])

    def test_errors_are_reported_not_silent(self):
        from unittest import mock
        with self.assertRaises(self.ai.AIError) as c:
            self.ai.generate_ai_comment("", self.cfg, raise_errors=True)
        self.assertIn("فارغ", str(c.exception))
        with self.assertRaises(self.ai.AIError):
            self.ai.generate_ai_comment("x", dict(self.cfg, api_key=""), raise_errors=True)
        with mock.patch.object(self.ai, "_call_api", side_effect=self.ai.AIError("HTTP 403: API key not valid")):
            with self.assertRaises(self.ai.AIError) as c:
                self.ai.generate_ai_comment("x", self.cfg, raise_errors=True)
        self.assertIn("403", str(c.exception))
        with mock.patch.object(self.ai, "_call_api", side_effect=ValueError("[SSL: CERTIFICATE_VERIFY_FAILED]")):
            self.assertIsNone(self.ai.generate_ai_comment("x", self.cfg))  # العادي: None مع تسجيل

    def test_interval_skip_unless_ignored(self):
        from unittest import mock
        with mock.patch.object(self.ai, "_call_api", return_value=self.gem("one")):
            self.assertEqual(self.ai.generate_ai_comment("x", self.cfg), "one")
            self.assertIsNone(self.ai.generate_ai_comment("x", self.cfg))             # ضمن 30ث
            self.assertEqual(self.ai.generate_ai_comment("x", self.cfg, ignore_interval=True), "one")

    def test_priority_comment_reads_text_from_page_and_ignores_interval(self):
        from unittest import mock
        from app import config, monitor, routine
        config.save_seen({})
        cfg = config.load_config()
        cfg["engagement"]["accounts_per_tweet"] = [1, 1]
        cfg["engagement"]["action_mix"] = {"like": 0.0, "retweet": 0.0, "comment": 1.0}
        cfg["comments"]["ai"].update(enabled=True, api_key="K", min_interval_s=30)
        monitor.schedule_for_tweet("777888999", "", cfg, {"ai1": {"enabled": True}}, priority=True)
        got = {}
        def fake_build(text, **kw):
            got["text"], got["interval"] = text, kw["cfg"]["comments"]["ai"]["min_interval_s"]
            return "AI comment"
        with mock.patch.object(routine, "open_tweet_with_retry", return_value=(True, "ok")), \
             mock.patch.object(routine, "_extract_open_tweet_text", return_value="$ETH looks strong"), \
             mock.patch.object(routine, "build_comment_for", side_effect=fake_build), \
             mock.patch.object(routine, "comment_on_tweet", return_value=(True, "ok")), \
             mock.patch.object(routine, "save_state"):
            state = {"done": {"likes": 0, "retweets": 0, "comments": 0, "follows": 0}, "caps": {"likes": 9, "retweets": 9, "comments": 9, "follows": 9}}
            routine._run_due_manager_tasks(mock.MagicMock(), "ai1", cfg, state, mock.MagicMock(), priority=True)
        self.assertEqual(got, {"text": "$ETH looks strong", "interval": 0})
        self.assertEqual(config.load_seen()["777888999"]["text"], "$ETH looks strong")  # محفوظ للحسابات التالية

    def test_api_endpoint(self):
        from unittest import mock
        svc = server.AppService()
        with mock.patch.object(self.ai, "_call_api", return_value=self.gem("great")):
            r = svc.ai_test({"provider": "gemini", "api_key": "K"})
        self.assertEqual(r["comment"], "great")
        with self.assertRaises(server.ApiError):
            svc.ai_test({"provider": "gemini", "api_key": ""})  # لا مفتاح محفوظ


class StaleTweetTests(unittest.TestCase):
    OLD = "1995205343681413626"  # تغريدة قديمة (أكثر من سنة)

    @staticmethod
    def fresh(hours_ago=0.5):
        import time
        return str((int((time.time() - hours_ago * 3600) * 1000) - 1288834974657) << 22)

    def setUp(self):
        from app import config, monitor
        self.m, self.config = monitor, config
        config.save_seen({})
        self.cfg = config.load_config()
        self.cfg["engagement"]["accounts_per_tweet"] = [1, 1]
        self.acc = {"st1": {"enabled": True}}

    def test_age_from_id(self):
        self.assertGreater(self.m.tweet_age_hours(self.OLD), 24 * 30)
        self.assertLess(self.m.tweet_age_hours(self.fresh(0.5)), 0.6)
        self.assertIsNone(self.m.tweet_age_hours("12345"))
        self.assertIsNone(self.m.tweet_age_hours("abc"))

    def test_filter_recent(self):
        items = [{"id": self.OLD, "text": "a"}, {"id": self.fresh(1), "text": "b"}, self.fresh(10), "123"]
        out = self.m.filter_recent(items, {"monitor": {"max_tweet_age_hours": 6}})
        ids = [i["id"] if isinstance(i, dict) else i for i in out]
        self.assertEqual(ids, [items[1]["id"], "123"])  # القديم والعمر 10 ساعات سقطا، والمعرّف غير الصالح يبقى
        self.assertEqual(len(self.m.filter_recent(items, {"monitor": {"max_tweet_age_hours": 0}})), 4)

    def test_cancel_and_stale_cleanup(self):
        for tid, src in ((self.OLD, "manager"), (self.fresh(1), "manager"), ("1996000000000000001", "telegram")):
            self.m.schedule_for_tweet(tid, "t", self.cfg, self.acc, source=src, priority=(src == "telegram"))
        n = self.m.cancel_stale(self.cfg)
        self.assertEqual(n, 1)  # القديمة من المدير فقط (التلقائية فقط، لا يدوي/تيليجرام)
        seen = self.config.load_seen()
        self.assertTrue(seen[self.OLD]["cancelled"])
        self.assertFalse(seen["1996000000000000001"].get("cancelled"))
        self.assertEqual(self.m.due_tasks_for("st1"), [t for t in self.m.due_tasks_for("st1") if t["tweet_id"] != self.OLD])
        fresh_id = [k for k in seen if k not in (self.OLD, "1996000000000000001")][0]
        self.assertTrue(self.m.cancel_tweet(fresh_id))
        self.assertFalse(self.m.cancel_tweet(fresh_id))  # مرة واحدة فقط

    def test_cancelled_not_pending_and_api(self):
        svc = server.AppService()
        self.m.schedule_for_tweet(self.fresh(1), "t", self.cfg, self.acc)
        tid = next(iter(self.config.load_seen()))
        self.assertGreater(svc.state()["stats"]["pending"], 0)
        svc.cancel_tweet(tid)
        self.assertEqual(svc.state()["stats"]["pending"], 0)
        self.assertTrue(svc.tweets_view()[0]["cancelled"])
        with self.assertRaises(server.ApiError):
            svc.cancel_tweet(tid)

    def test_bot_cancel_button(self):
        svc = server.AppService()
        self.m.schedule_for_tweet(self.fresh(1), "t", self.cfg, self.acc)
        tid = next(iter(self.config.load_seen()))
        v = svc.bot.ui.route(1, "tw:r:" + tid)
        self.assertTrue(any(d == "tw:x:" + tid for row in v.rows for _, d in row))
        v = svc.bot.ui.route(1, "tw:x:" + tid)
        self.assertIn("ملغاة", v.text)
        self.assertFalse(any(d.startswith("tw:x:") for row in v.rows for _, d in row))


class CommentQualityTests(unittest.TestCase):
    """لغة مطابقة للتغريدة + منع التكرار والتشابه (سبام)."""

    def setUp(self):
        from app import comments, config
        self.c, self.config = comments, config
        if os.path.exists(comments.HISTORY_PATH):
            os.remove(comments.HISTORY_PATH)
        self.cfg = config.load_config()
        self.cfg["comments"]["ai"]["enabled"] = False
        self.cfg["comments"]["contextual"] = False   # نختبر الطبقة العامة هنا
        with open(config.COMMENTS_PATH, "w", encoding="utf-8") as f:   # ملف قوالب عربي فقط (الوضع الافتراضي)
            f.write("الأسواق هيك، صعود وهبوط، المهم الاستراتيجية\nرؤية واضحة، شكراً على المشاركة\nكلام في الصميم\nطرح راقٍ وواضح\n")

    def test_detect_lang(self):
        d = self.c.detect_lang
        self.assertEqual(d("Bitcoin is breaking resistance today"), "en")
        self.assertEqual(d("البيتكوين يكسر المقاومة اليوم"), "ar")
        self.assertEqual(d("$BTC https://x.com/a/status/1 @user #crypto"), None)
        self.assertEqual(d(""), None)
        self.assertEqual(d("Bitcoin breaks 70k البيتكوين"), "en")  # الأغلب إنجليزي

    def test_similarity_catches_emoji_and_diacritics_variants(self):
        n = self.c.norm_text
        a = "الأسواق هيك، صعود وهبوط، المهم الاستراتيجية"
        self.assertTrue(self.c.similar(n(a), n(a + " 🔥")))                 # مثل حالة Tony في لقطتك
        self.assertTrue(self.c.similar(n("Great call!!"), n("great call 🔥")))
        self.assertTrue(self.c.similar(n("الاسواق هيك"), n("الأسواق هَيك")))
        self.assertFalse(self.c.similar(n("Great call"), n("Clean chart, levels look clear")))

    def test_english_tweet_never_gets_arabic_even_with_arabic_only_templates(self):
        for _ in range(25):
            t = self.c.build_comment_for("Bitcoin breaks above resistance with strong volume", cfg=self.cfg, reserve=False)
            self.assertIsNotNone(t)
            self.assertFalse(self.c.has_arabic(t), t)

    def test_arabic_tweet_gets_arabic(self):
        for _ in range(15):
            t = self.c.build_comment_for("البيتكوين يكسر المقاومة بحجم تداول قوي", cfg=self.cfg, reserve=False)
            self.assertTrue(self.c.has_arabic(t), t)

    def test_unknown_language_follows_en_ratio(self):
        self.cfg["comments"]["en_ratio"] = 1.0
        self.assertFalse(self.c.has_arabic(self.c.build_comment_for("", cfg=self.cfg, reserve=False)))
        self.cfg["comments"]["en_ratio"] = 0.0
        self.assertTrue(self.c.has_arabic(self.c.build_comment_for("", cfg=self.cfg, reserve=False)))

    def test_match_language_can_be_disabled(self):
        self.cfg["comments"]["match_language"] = False
        self.cfg["comments"]["en_ratio"] = 0.0
        self.assertTrue(self.c.has_arabic(self.c.build_comment_for("Bitcoin is pumping hard today", cfg=self.cfg, reserve=False)))

    def test_no_near_duplicates_across_accounts_and_tweets(self):
        out = []
        for i in range(14):
            t = self.c.build_comment_for("Bitcoin is pumping hard today", cfg=self.cfg, acc_id="acc_%d" % (i % 4))
            if t:
                out.append(self.c.norm_text(t))
        self.assertGreaterEqual(len(out), 10)
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                self.assertFalse(self.c.similar(out[i], out[j]), (out[i], out[j]))

    def test_used_texts_block_variants_and_exhaustion_returns_none(self):
        used = list(self.c.DEFAULT_EN_GENERIC) + ["x 🔥"]
        self.assertIsNone(self.c.build_comment_for("Bitcoin is pumping hard today", used_texts=used, cfg=self.cfg, reserve=False))
        self.assertEqual(self.c.norm_text("a 🔥!"), "a")

    def test_reservation_blocks_concurrent_pick(self):
        first = self.c.build_comment_for("Bitcoin is pumping hard today", cfg=self.cfg, acc_id="a1")
        for _ in range(20):
            nxt = self.c.build_comment_for("Bitcoin is pumping hard today", cfg=self.cfg, acc_id="a2", reserve=False)
            self.assertFalse(self.c.similar(self.c.norm_text(first), self.c.norm_text(nxt)))

    def test_ai_prompt_forces_language(self):
        from unittest import mock
        from app import ai_comments
        ai_comments._last_call = 0
        seen = {}
        def fake(url, headers, body, ctx, timeout=20):
            seen["p"] = body["contents"][0]["parts"][0]["text"]
            return {"candidates": [{"content": {"parts": [{"text": "nice"}]}}]}
        with mock.patch.object(ai_comments, "_call_api", side_effect=fake):
            ai_comments.generate_ai_comment("BTC up", {"provider": "gemini", "api_key": "k", "min_interval_s": 0}, lang="en")
        self.assertIn("ONLY in English", seen["p"])

    def test_task_skips_comment_when_no_unique_text(self):
        from unittest import mock
        from app import monitor, routine
        self.config.save_seen({})
        cfg = self.config.load_config()
        cfg["engagement"]["accounts_per_tweet"] = [1, 1]
        cfg["engagement"]["action_mix"] = {"like": 0.0, "retweet": 0.0, "comment": 1.0}
        monitor.schedule_for_tweet("888777666", "", cfg, {"cq1": {"enabled": True}}, priority=True)
        state = {"done": {"likes": 0, "retweets": 0, "comments": 0, "follows": 0}, "caps": {"likes": 9, "retweets": 9, "comments": 9, "follows": 9}}
        with mock.patch.object(routine, "open_tweet_with_retry", return_value=(True, "ok")), \
             mock.patch.object(routine, "_extract_open_tweet_text", return_value="Bitcoin"), \
             mock.patch.object(routine, "build_comment_for", return_value=None), \
             mock.patch.object(routine, "comment_on_tweet") as cm, mock.patch.object(routine, "save_state"):
            routine._run_due_manager_tasks(mock.MagicMock(), "cq1", cfg, state, mock.MagicMock(), priority=True)
        cm.assert_not_called()
        self.assertEqual(monitor.due_tasks_for("cq1", priority=True), [])  # لا تُعاد المحاولة


class ExpediteTests(unittest.TestCase):
    """رابط الأدمن لتغريدة معروفة: تسريع المتبقي، إعادة الملغاة، بلا تكرار ما أُنجز."""

    def setUp(self):
        from app import config, monitor
        self.m, self.config = monitor, config
        config.save_seen({})
        self.cfg = config.load_config()
        self.cfg["engagement"]["accounts_per_tweet"] = [2, 2]
        self.cfg["engagement"]["action_mix"] = {"like": 1.0, "retweet": 1.0, "comment": 0.0}
        self.accs = {"e1": {"enabled": True}, "e2": {"enabled": True}}

    def test_manager_tweet_then_admin_link_speeds_up(self):
        e = self.m.schedule_for_tweet("910000000000000001", "t", self.cfg, self.accs, source="manager")
        self.assertEqual(e["_status"], "new")
        self.assertEqual(self.m.due_tasks_for("e1"), [])  # موزّعة على ساعات
        self.m.mark_done("910000000000000001", "e1", "like")  # حساب أنجز عملية
        r = self.m.schedule_for_tweet("910000000000000001", "t", self.cfg, self.accs, source="telegram", priority=True, notify_chat=5)
        self.assertEqual(r["_status"], "upgraded")
        self.assertEqual(r["_remaining"], 3)  # 4 عمليات - المنجزة
        self.assertEqual(len(self.m.due_tasks_for("e1", priority=True)), 1)
        self.assertEqual(self.m.due_tasks_for("e1", priority=True)[0]["remaining"], ["retweet"])  # الإعجاب لا يُكرَّر
        self.assertEqual(self.m.due_tasks_for("e1"), [])  # لم تعد عادية
        self.assertEqual(self.config.load_seen()["910000000000000001"]["notify_chat"], 5)

    def test_second_admin_link_while_running_is_ignored(self):
        self.m.schedule_for_tweet("910000000000000002", "t", self.cfg, self.accs, priority=True)
        self.assertIsNone(self.m.schedule_for_tweet("910000000000000002", "t", self.cfg, self.accs, priority=True))

    def test_normal_duplicate_still_ignored(self):
        self.m.schedule_for_tweet("910000000000000003", "t", self.cfg, self.accs)
        self.assertIsNone(self.m.schedule_for_tweet("910000000000000003", "t", self.cfg, self.accs))

    def test_cancelled_tweet_revived_by_admin_but_not_by_manager(self):
        tid = "910000000000000004"
        self.m.schedule_for_tweet(tid, "t", self.cfg, self.accs)
        self.m.cancel_tweet(tid)
        self.assertIsNone(self.m.schedule_for_tweet(tid, "t", self.cfg, self.accs))  # المدير لا يُحييها
        r = self.m.schedule_for_tweet(tid, "t", self.cfg, self.accs, priority=True)
        self.assertEqual(r["_status"], "revived")
        e = self.config.load_seen()[tid]
        self.assertFalse(e.get("cancelled"))
        self.assertTrue(self.m.due_tasks_for("e1", priority=True))

    def test_completed_or_deleted_not_rerun(self):
        tid = "910000000000000005"
        self.m.schedule_for_tweet(tid, "t", self.cfg, self.accs)
        for acc in self.accs:
            for a in ("like", "retweet"):
                self.m.mark_done(tid, acc, a)
        self.assertIsNone(self.m.schedule_for_tweet(tid, "t", self.cfg, self.accs, priority=True))
        tid2 = "910000000000000006"
        self.m.schedule_for_tweet(tid2, "t", self.cfg, self.accs)
        self.m.mark_tweet_deleted(tid2)
        self.assertIsNone(self.m.schedule_for_tweet(tid2, "t", self.cfg, self.accs, priority=True))

    def test_bot_replies_for_upgrade_and_already_running(self):
        from unittest import mock
        svc = server.AppService()
        svc._save_admins([5])
        acc = svc.add_account({"handle": "upg_acc"})
        self.addCleanup(svc.delete_account, acc["id"])
        self.m.schedule_for_tweet("910000000000000007", "t", self.cfg, self.accs)   # جدولها المدير
        with mock.patch.object(svc.bot, "engine_running", return_value=True):
            r = svc.bot.handle_message(5, "https://x.com/boss/status/910000000000000007")
            self.assertIn("حُوّلت", r[0])
            self.assertIn("لا يُكرَّر", r[0])
            r2 = svc.bot.handle_message(5, "https://x.com/boss/status/910000000000000007")
            self.assertIn("مجدولة مسبقاً", r2[0])
            self.assertTrue(self.config.load_seen()["910000000000000007"]["priority"])
            self.assertEqual(self.config.load_seen()["910000000000000007"]["notify_chat"], 5)


class ChannelTests(unittest.TestCase):
    """قناة الأدمن: منشور بلا رابط ← فحص X؛ برابط ← جدولة مباشرة؛ والفحص الدوري صار احتياطياً."""
    CH = -1001234567890

    def setUp(self):
        from app import config
        self.config = config
        config.save_seen({})
        cfg = config.load_config()
        cfg["telegram"]["admin_ids"] = [5]
        cfg["telegram"]["notify"] = True
        cfg["telegram"]["channel"].update(enabled=True, chat_id=self.CH, instant=True)
        cfg["manager_handle"] = "boss"
        config.save_config(cfg)
        self.sent, self.sched, self.checks = [], [], []
        self.bot = telegram_bot.TelegramBot(
            get_cfg=config.load_config, get_accounts=lambda: {"a": {"enabled": True}},
            schedule=lambda tid, text, cfg, chat_id=None, source="telegram": self.sched.append((tid, text, source)) or {"queue": [{"actions": ["like"]}]},
            save_admins=lambda ids: None, sender=lambda cid, text, markup=None: self.sent.append((cid, text)),
            request_check=lambda ts: self.checks.append(ts) or True)

    def post(self, text="", chat=None, **extra):
        return dict({"chat": {"id": chat or self.CH, "title": "Boss channel", "type": "channel"}, "text": text}, **extra)

    def test_other_channel_ignored_but_remembered(self):
        self.assertEqual(self.bot.handle_channel_post(self.post("hi", chat=-100999)), "ignored")
        self.assertIn(-100999, self.bot.channels_seen)
        self.assertEqual((self.sched, self.checks, self.sent), ([], [], []))

    def test_disabled_channel_ignored(self):
        cfg = self.config.load_config(); cfg["telegram"]["channel"]["enabled"] = False; self.config.save_config(cfg)
        self.assertEqual(self.bot.handle_channel_post(self.post("https://x.com/b/status/1234567890123")), "ignored")
        self.assertEqual(self.sched, [])

    def test_post_with_link_schedules_directly_as_channel(self):
        r = self.bot.handle_channel_post(self.post("New call 🚀 https://x.com/b/status/1234567890123 $BTC"))
        self.assertEqual(r, "scheduled:1234567890123")
        self.assertEqual(self.sched, [("1234567890123", "New call 🚀 $BTC", "channel")])  # النص يُمرَّر للتعليقات
        self.assertEqual(self.checks, [])                                                    # بلا زيارة لـX
        self.assertIn("📣", self.sent[0][1])

    def test_link_hidden_in_text_link_entity(self):
        post = self.post("Read this", entities=[{"type": "text_link", "offset": 0, "length": 4, "url": "https://x.com/b/status/1234567890999"}])
        self.assertEqual(self.bot.handle_channel_post(post), "scheduled:1234567890999")

    def test_post_without_link_requests_x_check(self):
        self.assertEqual(self.bot.handle_channel_post(self.post("Big news coming today")), "check-requested")
        self.assertEqual(len(self.checks), 1)
        self.assertEqual(self.sched, [])

    def test_engine_stopped_warns_admin(self):
        self.bot.request_check = lambda ts: False
        self.assertEqual(self.bot.handle_channel_post(self.post("photo post")), "engine-stopped")
        self.assertIn("المحرك متوقف", self.sent[-1][1])

    def test_photo_caption_with_link(self):
        post = {"chat": {"id": self.CH, "title": "t"}, "caption": "chart https://twitter.com/b/status/1234567890555"}
        self.assertEqual(self.bot.handle_channel_post(post), "scheduled:1234567890555")

    # ---- المحرك: الفحص بعد منشور بلا رابط ----
    def make_engine(self):
        from unittest import mock
        from app.engine import Engine
        e = Engine()
        e.schedule_cb = lambda tid, text, cfg, instant: self.sched.append((tid, text, instant)) or {"queue": []}
        e.notify_cb = lambda text: self.sent.append(("n", text))
        e._stop = mock.MagicMock(); e._stop.wait.return_value = False; e._stop.is_set.return_value = False
        return e

    @staticmethod
    def fresh(minutes_ago):
        return str((int((time.time() - minutes_ago * 60) * 1000) - 1288834974657) << 22)

    def test_check_retries_until_found(self):
        from unittest import mock
        from app import monitor
        e = self.make_engine()
        new = self.fresh(2)
        results = iter([([], "empty"), ([{"id": new, "text": "BTC up"}], "ok")])
        with mock.patch.object(monitor, "fetch_manager_tweets", side_effect=lambda *a, **k: next(results)) as f:
            e._handle_check(monitor, time.time())
        self.assertEqual(f.call_count, 2)                      # الثانية وجدت فتوقفت إعادة المحاولة
        self.assertEqual(self.sched, [(new, "BTC up", True)])
        self.assertTrue(any("وُجدت" in s[1] for s in self.sent))

    def test_check_gives_up_and_tells_admin(self):
        from unittest import mock
        from app import monitor
        e = self.make_engine()
        with mock.patch.object(monitor, "fetch_manager_tweets", return_value=([], "empty")) as f:
            e._handle_check(monitor, time.time())
        self.assertEqual(f.call_count, 4)                      # 1 + 3 إعادات (الافتراضي)
        self.assertTrue(any("لم أجد" in s[1] for s in self.sent))

    def test_check_ignores_old_and_already_scheduled(self):
        from unittest import mock
        from app import monitor
        e = self.make_engine()
        old, done = self.fresh(300), self.fresh(5)
        seen = self.config.load_seen(); seen[done] = {"scheduled": True, "queue": []}; self.config.save_seen(seen)
        with mock.patch.object(monitor, "fetch_manager_tweets", return_value=([{"id": old, "text": ""}, {"id": done, "text": ""}], "ok")):
            self.assertEqual(e._check_once(monitor), [])
        self.assertEqual(self.sched, [])

    def test_request_check_only_when_running_and_merges(self):
        from app.engine import Engine
        e = Engine()
        self.assertFalse(e.request_check())
        e._thread = type("T", (), {"is_alive": lambda s: True})()
        e._stop.clear()
        self.assertTrue(e.request_check(100.0)); self.assertTrue(e.request_check(50.0)); self.assertTrue(e.request_check(200.0))
        self.assertEqual(e._check_ts, 50.0)                    # تُدمج الطلبات المتقاربة بأقدمها

    def test_config_validation_for_channel(self):
        svc = server.AppService()
        with self.assertRaises(server.ApiError):
            svc.set_config({"telegram": {"channel": {"fallback_interval_min": [90, 60]}}})
        out = svc.set_config({"telegram": {"channel": {"chat_id": -1009876543210, "fallback_interval_min": [60, 90]}}})
        self.assertEqual(out["telegram"]["channel"]["chat_id"], -1009876543210)

    # ---- تحديد القناة بدون نشر ----
    def test_resolve_public_channel_name(self):
        calls = []
        self.bot._api = lambda m, params=None, **k: calls.append((m, params)) or {"id": self.CH, "type": "channel", "title": "Boss", "username": "boss_ch"}
        for raw in ("@boss_ch", "https://t.me/boss_ch", "t.me/boss_ch?x=1", "boss_ch"):
            self.assertEqual(self.bot.resolve_channel(raw)["id"], self.CH)
        self.assertEqual(calls[0], ("getChat", {"chat_id": "@boss_ch"}))
        with self.assertRaises(RuntimeError):
            self.bot.resolve_channel("a b!")                      # اسم غير صالح
        self.bot._api = lambda *a, **k: {"id": 5, "type": "private"}
        with self.assertRaises(RuntimeError):
            self.bot.resolve_channel("someuser")                   # ليست قناة

    def test_forwarded_channel_post_shows_id_with_confirm_button(self):
        new_style = {"forward_origin": {"type": "channel", "chat": {"id": self.CH, "type": "channel", "title": "Boss"}}}
        old_style = {"forward_from_chat": {"id": self.CH, "type": "channel", "title": "Boss"}}
        cfg = self.config.load_config(); cfg["telegram"]["channel"]["chat_id"] = 0; self.config.save_config(cfg)  # لم تُعتمد قناة بعد
        for msg in (new_style, old_style):
            r = self.bot.handle_forward(5, msg)
            self.assertIn(str(self.CH), r)
            self.assertTrue(any(d == "ch:set:%d" % self.CH for row in r.markup for _, d in row))
            self.assertTrue(all(len(d.encode()) <= 64 for row in r.markup for _, d in row))
        self.assertIn(self.CH, self.bot.channels_seen)

    def test_forward_from_non_admin_or_hidden_origin(self):
        msg = {"forward_origin": {"type": "channel", "chat": {"id": self.CH, "type": "channel", "title": "x"}}}
        self.assertIsNone(self.bot.handle_forward(999, msg))                       # غير أدمن
        self.assertIn("لم أستطع", self.bot.handle_forward(5, {"forward_origin": {"type": "hidden_user"}}))
        self.assertIn("لم أستطع", self.bot.handle_forward(5, {"forward_origin": {"type": "user"}}))

    def test_confirm_button_saves_channel(self):
        svc = server.AppService()
        cfg = self.config.load_config(); cfg["telegram"]["channel"].update(enabled=False, chat_id=0); self.config.save_config(cfg)
        v = svc.bot.ui.route(5, "ch:set:%d" % self.CH)
        got = self.config.load_config()["telegram"]["channel"]
        self.assertEqual((got["enabled"], got["chat_id"]), (True, self.CH))
        self.assertIn("اعتُمدت", v.text)
        r = svc.bot.handle_forward(5, {"forward_from_chat": {"id": self.CH, "type": "channel", "title": "B"}})
        self.assertIn("المعتمدة حالياً", r)                                          # لا زر بعد الاعتماد
        self.assertFalse(any(d.startswith("ch:set") for row in r.markup for _, d in row))

    def test_resolve_api_error_is_readable(self):
        svc = server.AppService()
        svc.bot._api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Bad Request: chat not found"))
        with self.assertRaises(server.ApiError) as c:
            svc.tg_resolve_channel("@nochannel")
        self.assertIn("chat not found", str(c.exception))

    def test_channels_api(self):
        svc = server.AppService()
        svc.bot.channels_seen[-100777] = {"title": "T", "username": "u", "ts": time.time() - 5}
        row = [c for c in svc.tg_channels() if c["id"] == -100777][0]
        self.assertEqual((row["title"], row["username"]), ("T", "u"))


if __name__ == "__main__":
    unittest.main()
