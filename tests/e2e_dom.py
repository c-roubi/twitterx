# -*- coding: utf-8 -*-
"""اختبار already_replied على DOM مشابه لـ X: PW_CHROMIUM=/path/to/chrome python tests/e2e_dom.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402
from app.actions import already_replied  # noqa: E402

PAGE = """<!doctype html><meta charset=utf-8><body>
<article><div data-testid="User-Name"><a href="/CryptoBoss"><span>Boss</span></a><a href="/CryptoBoss"><span>@CryptoBoss</span></a></div></article>
<article><div data-testid="User-Name"><a href="/Tonystrak61"><span>Tony</span></a><a href="/Tonystrak61"><span>@Tonystrak61</span></a></div></article>
<article><div data-testid="User-Name"><a href="/aliy500"><span>Ali</span></a></div></article>
</body>"""
fails = []
with sync_playwright() as p:
    kw = {"args": ["--no-sandbox"]}
    if os.environ.get("PW_CHROMIUM"):
        kw["executable_path"] = os.environ["PW_CHROMIUM"]
    b = p.chromium.launch(**kw)
    pg = b.new_page()
    pg.set_content(PAGE)
    for name, got, want in [
        ("رد موجود (حالة أحرف مختلفة)", already_replied(pg, "tonystrak61"), True),
        ("رد موجود مع @", already_replied(pg, "@aliy500"), True),
        ("لا رد", already_replied(pg, "khalex"), False),
        ("اسم يحتوي جزءاً فقط", already_replied(pg, "Tony"), False),
        ("اسم فارغ", already_replied(pg, ""), False),
    ]:
        print(("  ✓ " if got == want else "  ✗ ") + name)
        if got != want:
            fails.append(name)
    b.close()
print("\nالنتيجة:", "نجح كل شيء" if not fails else "فشل: " + ", ".join(fails))
sys.exit(1 if fails else 0)
