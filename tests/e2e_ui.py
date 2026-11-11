# -*- coding: utf-8 -*-
"""اختبار شامل للواجهة بمتصفح حقيقي (Playwright). تشغيل يدوي:

    python tests/e2e_ui.py            # يستخدم Chromium المثبّت مع playwright
    PW_CHROMIUM=/path/to/chrome python tests/e2e_ui.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request

os.environ["SMARTENGAGE_DATA"] = tempfile.mkdtemp(prefix="se_e2e_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

httpd, svc, token = server.create_server(0)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % port
fails = []


def api(m, p, b=None):
    r = urllib.request.Request(BASE + p, method=m, data=json.dumps(b).encode() if b is not None else None,
                               headers={"X-Token": token, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())["data"]


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        fails.append(name)


with sync_playwright() as p:
    kw = {"args": ["--no-sandbox"]}
    if os.environ.get("PW_CHROMIUM"):
        kw["executable_path"] = os.environ["PW_CHROMIUM"]
    b = p.chromium.launch(**kw)
    pg = b.new_page(viewport={"width": 1360, "height": 860})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)))
    pg.goto(BASE + "/")
    pg.wait_for_selector("#hero")

    print("الحسابات")
    pg.goto(BASE + "/#/accounts"); pg.wait_for_selector("#acc-list")
    pg.click("[data-act=acc-add]"); pg.fill("#f-handle", "@bad handle"); pg.click("[data-dlg='0']")
    pg.wait_for_selector(".toast.err"); check("رفض اسم مستخدم غير صالح", True)
    pg.fill("#f-handle", "alice_x"); pg.fill("#f-name", "Alice"); pg.click("[data-dlg='0']")
    pg.wait_for_selector(".acc"); check("إضافة حساب", pg.locator(".acc").count() == 1)
    pg.click("[data-act=acc-toggle]"); pg.wait_for_timeout(300)
    check("تعطيل الحساب", api("GET", "/api/accounts")[0]["enabled"] is False)
    pg.click("[data-act=acc-toggle]"); pg.wait_for_timeout(300)
    pg.click("[data-act=acc-edit]"); pg.fill("#f-notes", "ملاحظة"); pg.fill("#f-en", "40"); pg.click("[data-dlg='0']"); pg.wait_for_timeout(300)
    a = api("GET", "/api/accounts")[0]
    check("تعديل الحساب", a["notes"] == "ملاحظة" and a["en_ratio"] == 0.4 and a["enabled"])

    from app.config import SESSIONS_DIR  # الحساب يلزمه جلسة محفوظة ليُدرَج في التفاعل العاجل
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    open(os.path.join(SESSIONS_DIR, api("GET", "/api/accounts")[0]["id"] + ".json"), "w").write("{}")
    print("التغريدات")
    pg.goto(BASE + "/#/tweets"); pg.wait_for_selector("#t-url")
    pg.fill("#t-url", "not a link"); pg.click("[data-act=tweet-add]"); pg.wait_for_selector(".toast.err")
    pg.fill("#t-url", "https://x.com/boss/status/1850000000000000009"); pg.fill("#t-text", "Bitcoin up")
    pg.click("[data-act=tweet-add]"); pg.wait_for_selector("#tw-list .listrow")
    check("جدولة تغريدة", pg.locator("#tw-list .listrow").count() == 1)
    pg.fill("#t-url", "https://x.com/boss/status/1850000000000000009"); pg.click("[data-act=tweet-add]")
    check("رفض التكرار", pg.wait_for_selector(".toast.err") is not None)

    print("الإعدادات")
    pg.goto(BASE + "/#/settings"); pg.wait_for_selector("#set-body")
    pg.fill("[data-p='manager_handle']", "boss"); check("شريط الحفظ يظهر", pg.locator("#savebar.show").count() == 1)
    pg.click("[data-act=set-tab][data-id=behavior]")
    pg.fill("[data-p='behavior.max_likes_per_day'] .r0", "99")  # أكبر من الأعلى
    pg.click("[data-act=set-save]"); pg.wait_for_selector(".toast.err"); check("رفض مجال معكوس", api("GET", "/api/config")["manager_handle"] == "")
    pg.fill("[data-p='behavior.max_likes_per_day'] .r0", "10"); pg.fill("[data-p='behavior.rest_day_chance']", "20")
    pg.click("[data-act=set-tab][data-id=comments]"); pg.wait_for_selector("#tpl")
    pg.fill("#tpl", "تعليق ١\nتعليق ٢")
    pg.click("[data-act=set-save]"); pg.wait_for_timeout(600)
    c = api("GET", "/api/config")
    check("حفظ عبر عدة تبويبات", c["manager_handle"] == "boss" and c["behavior"]["max_likes_per_day"][0] == 10 and abs(c["behavior"]["rest_day_chance"] - 0.2) < 1e-9)
    check("حفظ قوالب التعليقات", api("GET", "/api/comments")["text"] == "تعليق ١\nتعليق ٢\n")

    print("تيليجرام")
    pg.goto(BASE + "/#/telegram"); pg.wait_for_selector("#tg-token")
    pg.click("[data-act=tg-test]"); pg.wait_for_selector("#tg-test .err-t"); check("رفض توكن فارغ/خاطئ", True)
    pg.click("[data-act=tg-pair]"); pg.wait_for_selector(".code"); code = pg.inner_text(".code").strip()
    check("رمز ربط من 6 أرقام", len(code) == 6 and code.isdigit())
    pg.fill("#tg-token", "123456789:" + "B" * 35); pg.click("#tg-en"); pg.click("[data-act=tg-save]"); pg.wait_for_timeout(500)
    cfg = api("GET", "/api/config")["telegram"]
    check("حفظ تيليجرام وإخفاء التوكن", cfg["enabled"] and cfg["bot_token"] == server.MASK)

    print("المحرك")
    pg.goto(BASE + "/#/home"); pg.wait_for_selector("[data-act=engine-dry]")
    pg.click("[data-act=engine-dry]"); pg.wait_for_selector("[data-act=engine-stop]")
    check("بدء المحاكاة", api("GET", "/api/state")["engine"]["status"] in ("running", "stopping"))
    pg.click("[data-act=engine-stop]"); pg.wait_for_selector("[data-act=engine-start]", timeout=15000)
    check("إيقاف المحرك", api("GET", "/api/state")["engine"]["status"] == "stopped")

    print("حذف الحساب")
    pg.goto(BASE + "/#/accounts"); pg.wait_for_selector(".acc")
    pg.click("[data-act=acc-del]"); pg.click("[data-dlg='0']"); pg.wait_for_selector(".empty")
    check("حذف الحساب", api("GET", "/api/accounts") == [])

    print("السجلات")
    pg.goto(BASE + "/#/logs"); pg.wait_for_selector(".log, .empty")
    errs = [e for e in errs if "status of 400" not in e]  # 400 متوقعة من اختبارات التحقق
    check("لا أخطاء في الكونسول", not errs)
    if errs:
        print("   ", errs)
    b.close()

svc.shutdown()
print("\nالنتيجة:", "نجح كل شيء" if not fails else "فشل: " + ", ".join(fails))
sys.exit(1 if fails else 0)
