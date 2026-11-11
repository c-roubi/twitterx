# -*- coding: utf-8 -*-
"""اختبار نشر التغريدة اليومية على DOM مشابه لـ X: PW_CHROMIUM=/path/to/chrome python tests/e2e_post.py

يحاكي الحالة الحقيقية: نافذة الإنشاء (compose/post) فوق الرئيسية، ومحرر الرئيسية وزرّه tweetButtonInline خلفها.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402
from app import daily_post  # noqa: E402
from app.humanizer import Human  # noqa: E402

PAGE = r"""<!doctype html><html><body>
<main>
 <div id="inline"><div data-testid="tweetTextarea_0" role="textbox" contenteditable="true" style="min-height:20px;border:1px solid"></div>
 <button data-testid="tweetButtonInline" disabled>Post</button></div>
</main>
<div id="layers"></div>
<script>
window.__posts=[];
function wire(ta,btn,onSend){ta.addEventListener('input',()=>{btn.disabled=!ta.innerText.trim()});
 btn.addEventListener('click',()=>{if(btn.disabled)return; onSend(ta.innerText.trim()); ta.innerText='';btn.disabled=true;});}
wire(document.querySelector('#inline [data-testid=tweetTextarea_0]'),document.querySelector('[data-testid=tweetButtonInline]'),t=>window.__posts.push(['inline',t]));
if(location.pathname.startsWith('/compose/') && !window.__NODLG){
 const d=document.createElement('div');d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');
 d.innerHTML='<div data-testid="tweetTextarea_0" role="textbox" contenteditable="true" style="min-height:20px;border:1px solid"></div><button data-testid="tweetButton" disabled>Post</button>';
 document.getElementById('layers').appendChild(d);
 wire(d.querySelector('[data-testid=tweetTextarea_0]'),d.querySelector('[data-testid=tweetButton]'),t=>{window.__posts.push(['modal',t]);
   if(window.__FAIL){const s=document.createElement('div');s.setAttribute('data-testid','toast');s.innerText='Whoops! You already said that.';document.body.appendChild(s);return;}
   d.remove();history.replaceState({},'','/home');const s=document.createElement('div');s.setAttribute('data-testid','toast');s.innerText='Your post was sent.';document.body.appendChild(s);});
}
</script></body></html>"""
TEXT = "BTC looks strong today #Bitcoin"
fails = []
with sync_playwright() as p:
    kw = {"args": ["--no-sandbox"]}
    if os.environ.get("PW_CHROMIUM"):
        kw["executable_path"] = os.environ["PW_CHROMIUM"]
    b = p.chromium.launch(**kw)
    for name, init, want_ok, want_box in [
        ("النشر من نافذة الإنشاء وليس محرر الرئيسية خلفها", "", True, "modal"),
        ("رسالة خطأ من X => فشل وليس نجاحاً", "window.__FAIL=true", False, None),
        ("النافذة لم تفتح => محرر الرئيسية", "window.__NODLG=true", True, "inline"),
    ]:
        pg = b.new_page()
        pg.route("https://x.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body=PAGE))
        if init:
            pg.add_init_script(init)
        h = Human()
        h.page = pg
        h.pause = lambda *a, **k: None
        ok, status = daily_post.post_tweet(pg, TEXT, h)
        posts = pg.evaluate("window.__posts")
        good = ok == want_ok and (want_box is None or posts == [[want_box, TEXT]])
        print(("  ✓ " if good else "  ✗ ") + name + "  ({}, {}, {})".format(ok, status, posts))
        if not good:
            fails.append(name)
        pg.close()
    b.close()
print("فشل: {}".format(fails) if fails else "كل الحالات نجحت")
sys.exit(1 if fails else 0)
