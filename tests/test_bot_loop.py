# -*- coding: utf-8 -*-
"""حلقة البوت الحقيقية ضد خادم تيليجرام وهمي: أزرار، ربط، قناة، webhook، أعطال، إعادة تشغيل."""
import os
import sys
import tempfile
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="se_botloop_")
os.environ.setdefault("SMARTENGAGE_DATA", _TMP)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_telegram import FakeTelegram  # noqa: E402
from app import ai_comments, config, server, telegram_bot  # noqa: E402

ai_comments.DISCOVERY_ENABLED = False
TOKEN = "123456789:" + "A" * 35


class BotLoopTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.httpd.shutdown)
        self._api = telegram_bot.API
        telegram_bot.API = self.fake.url
        self.addCleanup(setattr, telegram_bot, "API", self._api)
        if os.path.exists(telegram_bot.OFFSET_PATH):
            os.remove(telegram_bot.OFFSET_PATH)
        config.save_seen({})
        cfg = config.load_config()
        cfg["telegram"].update(enabled=True, bot_token=TOKEN, admin_ids=[5], notify=True)
        cfg["telegram"]["channel"].update(enabled=True, chat_id=-1001, instant=True)
        cfg["follow"]["queries"] = ["bitcoin"]
        cfg["follow"]["enabled"] = True
        config.save_config(cfg)
        self.svc = server.AppService()
        self.acc = self.svc.add_account({"handle": "loop_acc"})["id"]
        os.makedirs(config.SESSIONS_DIR, exist_ok=True)
        with open(os.path.join(config.SESSIONS_DIR, self.acc + ".json"), "w") as f:
            f.write("{}")
        self.addCleanup(self.svc.delete_account, self.acc)
        self.svc.bot.engine_running = lambda: True
        self.svc.bot.start()
        self.addCleanup(lambda: self.svc.bot.stop(join_timeout=3))

    def msg(self, text, chat=5, **extra):
        self.fake.push(message=dict({"chat": {"id": chat, "type": "private"}, "text": text, "from": {"username": "u"}}, **extra))

    def texts(self, method="sendMessage"):
        return [p.get("text", "") for p in self.fake.sent(method)]

    def test_menu_has_inline_keyboard(self):
        self.msg("/menu")
        self.assertTrue(self.fake.wait_for(lambda: self.fake.sent("sendMessage")))
        p = self.fake.sent("sendMessage")[0]
        self.assertEqual(p["parse_mode"], "HTML")
        self.assertIn("inline_keyboard", p["reply_markup"])
        self.assertTrue(self.fake.sent("setMyCommands"))               # القائمة الزرقاء للأوامر

    def test_button_click_edits_message_and_toggles_setting(self):
        self.fake.push(callback_query={"id": "cb1", "data": "set:i:fe", "message": {"chat": {"id": 5}, "message_id": 42}})
        self.assertTrue(self.fake.wait_for(lambda: self.fake.sent("editMessageText")))
        self.assertEqual(self.fake.sent("editMessageText")[0]["message_id"], "42")
        self.assertTrue(self.fake.sent("answerCallbackQuery"))
        self.assertFalse(config.load_config()["follow"]["enabled"])

    def test_unauthorized_user_blocked_and_button_denied(self):
        self.msg("/menu", chat=77)
        self.fake.push(callback_query={"id": "cb2", "data": "eng:start", "message": {"chat": {"id": 77}, "message_id": 1}})
        self.assertTrue(self.fake.wait_for(lambda: self.fake.sent("answerCallbackQuery") and self.fake.sent("sendMessage")))
        self.assertIn("غير مصرّح", self.texts()[0])
        self.assertEqual(self.fake.sent("answerCallbackQuery")[0]["text"], "غير مصرّح")
        self.assertFalse(self.fake.sent("editMessageText"))

    def test_pairing_flow_adds_admin(self):
        code = self.svc.bot.new_pair_code()
        self.msg("/pair " + code, chat=88)
        self.assertTrue(self.fake.wait_for(lambda: 88 in config.load_config()["telegram"]["admin_ids"]))
        self.assertTrue(any("تم الربط" in t for t in self.texts()))

    def test_tweet_link_from_admin_schedules_and_replies_with_progress_button(self):
        self.msg("https://x.com/boss/status/1234567890123 BTC strong")
        self.assertTrue(self.fake.wait_for(lambda: any("جُدول" in t for t in self.texts())))
        e = config.load_seen()["1234567890123"]
        self.assertTrue(e["priority"])
        self.assertEqual(e["source"], "telegram")
        self.assertIn("tw:r:1234567890123", [p for p in self.fake.sent("sendMessage") if "جُدول" in p["text"]][0]["reply_markup"])

    def test_channel_post_with_link_schedules_and_notifies_admin(self):
        self.fake.push(channel_post={"chat": {"id": -1001, "type": "channel", "title": "Boss"}, "date": int(time.time()),
                                     "text": "New https://x.com/boss/status/1234567890777"})
        self.assertTrue(self.fake.wait_for(lambda: "1234567890777" in config.load_seen()))
        self.assertEqual(config.load_seen()["1234567890777"]["source"], "channel")
        self.assertTrue(self.fake.wait_for(lambda: any("منشور جديد في القناة" in t for t in self.texts())))
        self.assertEqual(self.fake.sent("sendMessage")[-1]["chat_id"], "5")        # الإشعار للأدمن
        self.assertIn("جُدول التفاعل", self.svc.bot.channel_events[-1]["result"])

    def test_channel_post_from_other_channel_ignored(self):
        self.fake.push(channel_post={"chat": {"id": -999, "type": "channel", "title": "Other"}, "date": int(time.time()),
                                     "text": "https://x.com/boss/status/1234567890888"})
        self.assertTrue(self.fake.wait_for(lambda: self.svc.bot.channel_events))
        self.assertNotIn("1234567890888", config.load_seen())

    def test_old_channel_post_after_downtime_ignored(self):
        self.fake.push(channel_post={"chat": {"id": -1001, "type": "channel", "title": "Boss"}, "date": int(time.time()) - 7200,
                                     "text": "https://x.com/boss/status/1234567890999"})
        self.assertTrue(self.fake.wait_for(lambda: self.svc.bot.channel_events))
        self.assertIn("قديم", self.svc.bot.channel_events[-1]["result"])
        self.assertNotIn("1234567890999", config.load_seen())

    def test_webhook_left_by_another_project_is_removed(self):
        self.svc.bot.stop()
        self.fake.webhook_active = True
        bot2 = server.AppService().bot
        self.addCleanup(lambda: bot2.stop(join_timeout=3))
        bot2.start()
        self.msg("/status")
        self.assertTrue(self.fake.wait_for(lambda: self.fake.sent("sendMessage"), 8))
        self.assertTrue(self.fake.sent("deleteWebhook"))
        self.assertEqual(bot2.state, "running")

    def test_html_rejected_by_telegram_falls_back_to_plain_text(self):
        self.fake.fail["sendMessage"] = lambda p: (400, {"ok": False, "description": "Bad Request: can't parse entities"}) if p.get("parse_mode") else None
        self.svc.bot.send(5, "<b>مرحبا</b> &amp; <غريب>")
        plain = [p for p in self.fake.sent("sendMessage") if "parse_mode" not in p]
        self.assertEqual(len(plain), 1)
        self.assertNotIn("<b>", plain[0]["text"])

    def test_long_message_truncated_and_429_retried(self):
        self.svc.bot.send(5, "x" * 9000)
        self.assertLessEqual(len(self.fake.sent("sendMessage")[-1]["text"]), 4000)
        calls = {"n": 0}
        def flood(p):
            calls["n"] += 1
            return (429, {"ok": False, "description": "Too Many Requests: retry after 1"}) if calls["n"] == 1 else None
        self.fake.fail["sendMessage"] = flood
        self.assertIsNotNone(self.svc.bot.send(5, "after flood"))
        self.assertEqual(calls["n"], 2)

    def test_handler_exception_does_not_kill_loop_and_user_is_told(self):
        orig = self.svc.bot.handle_message
        self.svc.bot.handle_message = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom <x>"))
        self.msg("/status")
        self.assertTrue(self.fake.wait_for(lambda: any("خطأ داخلي" in t for t in self.texts())))
        self.svc.bot.handle_message = orig
        self.msg("/status")
        self.assertTrue(self.fake.wait_for(lambda: any("المحرك" in t for t in self.texts())))
        self.assertEqual(self.svc.bot.state, "running")

    def test_tweet_link_during_keyword_input_is_not_saved_as_keyword(self):
        self.svc.bot.ui.route(5, "kw:add")
        self.msg("https://x.com/u/status/1234567890123")
        self.assertTrue(self.fake.wait_for(lambda: "1234567890123" in config.load_seen()))
        self.assertEqual(config.load_config()["follow"]["queries"], ["bitcoin"])

    def test_restart_does_not_replay_processed_updates(self):
        self.msg("/menu")
        self.assertTrue(self.fake.wait_for(lambda: self.fake.sent("sendMessage")))
        time.sleep(1.3)                                                # ليُثبَّت الـoffset بالدورة التالية
        self.svc.bot.stop()
        time.sleep(0.4)
        n = len(self.fake.sent("sendMessage"))
        bot2 = server.AppService().bot
        self.addCleanup(lambda: bot2.stop(join_timeout=3))
        bot2.start()
        time.sleep(2.2)
        self.assertEqual(len(self.fake.sent("sendMessage")), n)
        self.assertIsNotNone(telegram_bot._load_offset(TOKEN))

    def test_disabled_bot_does_not_poll(self):
        self.svc.bot.stop()
        time.sleep(0.5)
        cfg = config.load_config(); cfg["telegram"]["enabled"] = False; config.save_config(cfg)
        n = len(self.fake.sent("getUpdates"))
        bot2 = server.AppService().bot
        self.addCleanup(lambda: bot2.stop(join_timeout=3))
        bot2.start()
        time.sleep(1.5)
        self.assertEqual(len(self.fake.sent("getUpdates")), n)
        self.assertEqual(bot2.state, "stopped")

    def test_forwarded_channel_post_returns_channel_id_with_confirm_button(self):
        cfg = config.load_config(); cfg["telegram"]["channel"]["chat_id"] = 0; config.save_config(cfg)
        self.msg("fwd", forward_origin={"type": "channel", "chat": {"id": -1005, "type": "channel", "title": "Fwd"}}, forward_date=1)
        self.assertTrue(self.fake.wait_for(lambda: any("-1005" in t for t in self.texts())))
        self.assertIn("ch:set:-1005", [p for p in self.fake.sent("sendMessage") if "-1005" in p["text"]][0]["reply_markup"])


if __name__ == "__main__":
    unittest.main()


class DailyReportLoopTests(unittest.TestCase):
    """حلقة البوت الحقيقية ترسل التقرير اليومي عند حلول الموعد (بتوقيت غزة)."""

    def test_loop_sends_due_summary_once_with_followed_people(self):
        import datetime as _d
        from app import ledger, summary, tz
        fake = FakeTelegram()
        self.addCleanup(fake.httpd.shutdown)
        old_api = telegram_bot.API
        telegram_bot.API = fake.url
        self.addCleanup(setattr, telegram_bot, "API", old_api)
        for p in (ledger.LEDGER_PATH, summary.SENT_PATH, telegram_bot.OFFSET_PATH):
            if os.path.exists(p):
                os.remove(p)
        cfg = config.load_config()
        cfg["telegram"].update(enabled=True, bot_token=TOKEN, admin_ids=[5])
        cfg["telegram"]["daily_summary"] = {"enabled": True, "time": "00:30", "timezone": "Asia/Gaza"}
        config.save_config(cfg)
        config.save_accounts({"rp1": {"handle": "rep_acc", "enabled": True}})
        # نشاط «أمس» بتوقيت غزة مسجّل في الدفتر، والآن بعد 00:30 بتوقيت غزة ← التقرير مستحق
        gaza = tz.tzinfo("Asia/Gaza")
        now = tz.now("Asia/Gaza")
        yday = (now - _d.timedelta(days=1)).strftime("%Y-%m-%d")
        ledger._save({"days": {yday: {"accounts": {"rp1": {"likes": 7, "retweets": 1, "comments": 1, "follows": 2}},
                                      "followed": [{"acc": "rp1", "handle": "newguy1", "t": 1}, {"acc": "rp1", "handle": "newguy2", "t": 2}]}}, "hw": {}})
        real_due = summary.due_days
        summary.due_days = lambda cfg=None, now_=None: real_due(cfg, now=(now.replace(hour=1, minute=0)))   # 01:00 غزة
        self.addCleanup(setattr, summary, "due_days", real_due)
        svc = server.AppService()
        self.addCleanup(lambda: svc.bot.stop(join_timeout=3))
        svc.bot.start()
        self.assertTrue(fake.wait_for(lambda: any("ملخص يوم" in p.get("text", "") for p in fake.sent("sendMessage"))))
        text = [p["text"] for p in fake.sent("sendMessage") if "ملخص يوم" in p["text"]][0]
        self.assertIn("أشخاص جدد تمت متابعتهم اليوم: 2", text)
        self.assertIn("@newguy1 @newguy2", text)
        self.assertIn(yday, text)
        time.sleep(2.5)                                                  # دورات إضافية: لا تكرار
        self.assertEqual(sum(1 for p in fake.sent("sendMessage") if "ملخص يوم" in p.get("text", "")), 1)
