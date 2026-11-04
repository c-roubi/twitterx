# -*- coding: utf-8 -*-
"""حصاد المتصفح على صفحات X وهمية: PW_CHROMIUM=/opt/pw-browsers/chromium python tests/e2e_harvest.py"""
import os
import sys
import tempfile

os.environ.setdefault("SMARTENGAGE_DATA", tempfile.mkdtemp(prefix="se_hv_e2e_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402
from app import harvest  # noqa: E402

fails = []
def check(name, cond):
    print(("  ✓ " if cond else "  ✗ ") + name)
    if not cond:
        fails.append(name)

USERCELLS = "".join(
    '<div data-testid="UserCell"><a href="/%s"><span>@%s</span></a>'
    '<a href="/%s/photo"><img></a></div>' % (h, h, h) for h in ["alice", "bob_x", "carol7"])
# صفحة بها خلايا مستخدمين + روابط غير حسابات يجب تجاهلها
PAGE_CELLS = "<!doctype html><meta charset=utf-8><body>" + USERCELLS + \
    '<div data-testid="UserCell"><a href="/home">Home</a><a href="/dave"><span>@dave</span></a></div></body>'

def article(author, tid, reposts):
    return ('<article data-testid="tweet"><a href="/%s/status/%s"><time></time></a>'
            '<button data-testid="retweet" aria-label="%d reposts. Repost"></button></article>' % (author, tid, reposts))

PAGE_SEARCH = "<!doctype html><meta charset=utf-8><body>" + article("whale", "900001", 55) + \
    article("small", "900002", 3) + article("boss", "900003", 120) + "</body>"

with sync_playwright() as p:
    kw = {"args": ["--no-sandbox"]}
    if os.environ.get("PW_CHROMIUM"):
        kw["executable_path"] = os.environ["PW_CHROMIUM"]
    b = p.chromium.launch(**kw)
    pg = b.new_page()

    print("سحب خلايا المستخدمين")
    pg.set_content(PAGE_CELLS)
    handles = harvest.scrape_handles_on_page(pg, limit=10)
    check("يسحب الحسابات فقط (يتجاهل /home والصور)", handles == ["alice", "bob_x", "carol7", "dave"])
    check("يحترم الحد", len(harvest.scrape_handles_on_page(pg, limit=2)) == 2)

    print("قراءة عدد الريتويت")
    pg.set_content(PAGE_SEARCH)
    arts = pg.locator('article[data-testid="tweet"]').all()
    counts = [harvest.retweet_count_of(a) for a in arts]
    check("يقرأ عدد الريتويت من aria-label", counts == [55, 3, 120])

    print("حصاد معيدي التغريد")
    import app.resilience as resil
    orig_goto = resil.goto_with_retry
    def fake_goto(page, url, cfg):
        page.set_content(PAGE_CELLS if "/retweets" in url else PAGE_SEARCH)
        return True, "ok"
    resil.goto_with_retry = fake_goto
    try:
        if os.path.exists(harvest.POOL_PATH):
            os.remove(harvest.POOL_PATH)
        class H:
            def pause(self, *a): pass
        n = harvest.harvest_reposters(pg, H(), "boss", "900003", {"follow": {"harvest": {"per_target": 10}}})
        check("يضيف المعيدين للمخزن", n == 4 and harvest.pool_size() == 4)
        check("يعلّم التغريدة محصودة", harvest.recently_harvested("900003"))
        n2 = harvest.harvest_reposters(pg, H(), "boss", "900003b", {"follow": {"harvest": {"per_target": 10}}})
        check("لا يكرّر نفس الأسماء", n2 == 0)

        print("المنتِج الرئيسي run_harvest")
        if os.path.exists(harvest.POOL_PATH):
            os.remove(harvest.POOL_PATH)
        cfg = {"follow": {"queries": ["bitcoin"], "harvest": {"enabled": True, "min_reposts": 20, "targets_per_session": [1, 1], "per_target": 10}}}
        added = harvest.run_harvest(pg, "acc_001", cfg, {}, H(), budget_s=30)
        check("run_harvest يحصد من تغريدة شعبية فقط", added == 4 and harvest.pool_size() == 4)
    finally:
        resil.goto_with_retry = orig_goto
    b.close()

print("\nالنتيجة:", "نجح كل شيء" if not fails else "فشل: " + ", ".join(fails))
sys.exit(1 if fails else 0)
