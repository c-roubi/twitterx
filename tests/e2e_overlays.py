# -*- coding: utf-8 -*-
"""يختبر حارس النوافذ على صفحات X وهمية (بدون شبكة): PW_CHROMIUM=/path/to/chrome python tests/e2e_overlays.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402
from app import overlays  # noqa: E402

fails = []


def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        fails.append(name)


def html(body):
    return "<!doctype html><html><body style='margin:0'>%s</body></html>" % body


UNLOCK = """<div role="dialog" id="unlock" style="width:300px;height:200px"><h2>Unlock more on X</h2>
<button id="gotit" onclick="document.getElementById('unlock').remove()">Got it</button></div>"""
NOTNOW = """<div role="dialog" id="prem" style="width:300px;height:200px"><p>Upgrade to Premium</p>
<button onclick="document.getElementById('prem').remove()">Not now</button><button onclick="window.__bad=1">Subscribe</button></div>"""
CLOSEONLY = """<div role="dialog" id="cl" style="width:300px;height:200px"><button aria-label="Close" onclick="document.getElementById('cl').remove()">x</button></div>"""
PLUS = """<div role="dialog" id="plus" style="width:300px;height:200px"><h2>Want more people to see your reply?</h2><p>Subscribe to Plus to have your Replies boosted</p>
<button onclick="window.__bad=2">Upgrade to Plus</button><button onclick="document.getElementById('plus').remove()">Maybe later</button></div>"""
PLUS_AR = """<div role="dialog" id="plusar" style="width:300px;height:200px"><button onclick="window.__bad=3">ترقية</button><button onclick="document.getElementById('plusar').remove()">ربما لاحقًا</button></div>"""
COMPOSE = """<div role="dialog" id="cmp" style="width:300px;height:200px"><div data-testid="tweetTextarea_0" contenteditable>hi</div>
<button data-testid="tweetButton">Reply</button><button onclick="document.getElementById('cmp').remove()">Close</button></div>"""
PREMIUM_CARD = """<div data-testid="sidebarColumn"><aside role="complementary" id="card" style="height:50px">Subscribe to Premium</aside></div>"""
LOGIN = """<div role="dialog" id="lg" style="width:300px;height:200px"><input name="password"><button>Close</button></div>"""

PAGES = {
    "https://x.com/home": html(UNLOCK + NOTNOW + CLOSEONLY + COMPOSE + PREMIUM_CARD + PLUS + PLUS_AR),
    "https://x.com/i/flow/login": html(LOGIN + UNLOCK),
}

with sync_playwright() as p:
    kw = {"args": ["--no-sandbox"]}
    if os.environ.get("PW_CHROMIUM"):
        kw["executable_path"] = os.environ["PW_CHROMIUM"]
    b = p.chromium.launch(**kw)
    ctx = b.new_context()
    overlays.install(ctx)
    ctx.route("https://x.com/**", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGES[r.request.url]))
    pg = ctx.new_page()

    print("صفحة عادية")
    pg.goto("https://x.com/home")
    pg.wait_for_timeout(1800)  # الحارس يعمل كل 0.8 ثانية
    gone = lambda sel: pg.locator(sel).count() == 0
    check("أُغلقت «Unlock more on X» بزر Got it", gone("#unlock"))
    check("أُغلق عرض Premium بـ Not now", gone("#prem"))
    check("أُغلقت نافذة «Upgrade to Plus» بـ Maybe later", gone("#plus"))
    check("أُغلقت صيغتها العربية", gone("#plusar"))
    check("لم يضغط أي زر اشتراك/ترقية", pg.evaluate("window.__bad") is None)
    check("أُغلق حوار بزر Close فقط", gone("#cl"))
    check("لم يمسّ نافذة الكتابة/التعليق", pg.locator("#cmp").count() == 1)
    check("أُخفيت بطاقة Premium الجانبية", not pg.locator("#card").is_visible())

    print("استدعاء clear() المباشر")
    pg.evaluate("document.body.insertAdjacentHTML('beforeend', %r)" % UNLOCK.replace("\n", " "))
    check("clear() يغلق فوراً", overlays.clear(pg) >= 1 and gone("#unlock"))

    print("شاشة تسجيل الدخول")
    pg.goto("https://x.com/i/flow/login")
    pg.wait_for_timeout(1800)
    check("لا يلمس حوار تسجيل الدخول", pg.locator("#lg").count() == 1 and pg.locator("#unlock").count() == 1)
    check("clear() لا يعمل في صفحة الدخول", overlays.clear(pg) == 0)
    b.close()

print("\nالنتيجة:", "نجح كل شيء" if not fails else "فشل: " + ", ".join(fails))
sys.exit(1 if fails else 0)
