# -*- coding: utf-8 -*-
"""DOM-level follow tests on static fixtures (headless Chromium, no network).

Run:  python3 tests/test_follow_dom.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from app import actions
from app.actions import (
    _parse_count, dismiss_all_modals, follow_user, is_following, read_profile,
)
from app.grow import ineligible_reason, _authors_in_view, _handle_from_href
from app.humanizer import Human

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


VERIFIED_SVG = '<svg data-testid="icon-verified" aria-label="Verified account"></svg>'


def profile_html(handle="alice", verified=False, protected=False, following=False,
                 followers_href="verified_followers", followers_txt="1.2K Followers",
                 ar=False, with_dialog=False, block_toast=False):
    badge = VERIFIED_SVG if verified else ""
    lock = '<svg data-testid="icon-lock" aria-label="Protected account"></svg>' if protected else ""
    label_follow = f"متابعة @{handle}" if ar else f"Follow @{handle}"
    label_following = f"تتم المتابعة @{handle}" if ar else f"Following @{handle}"
    btn = (f'<button data-testid="111-unfollow" aria-label="{label_following}">Following</button>'
           if following else
           f'<button id="fb" data-testid="111-follow" aria-label="{label_follow}">Follow</button>')
    dialog = ('<div role="dialog"><h2>Views</h2><button id="dis">Dismiss</button></div>'
              if with_dialog else "")
    toast = ('<div data-testid="toast">You are unable to follow more people at this time</div>'
             if block_toast else "")
    return f"""
<html><body>
<main>
  <div data-testid="UserName"><span>Alice</span>{badge}{lock}</div>
  {btn}
  <div data-testid="UserDescription">crypto trader</div>
  <a href="/{handle}/following"><span>350</span><span>Following</span></a>
  <a href="/{handle}/{followers_href}"><span>{followers_txt}</span></a>
  <a href="/{handle}/followers_you_follow">9 Followers you know</a>
  <article data-testid="tweet">
    <div data-testid="User-Name"><a href="/bigverified">Big</a>{VERIFIED_SVG}</div>
    <button data-testid="999-follow" aria-label="Follow @bigverified">Follow</button>
    <button aria-label="Bookmark">Bookmark</button>
  </article>
</main>
<aside data-testid="sidebarColumn">
  <div data-testid="UserCell">{VERIFIED_SVG}
    <button data-testid="555-follow" aria-label="Follow @whale">Follow</button></div>
</aside>
{dialog}{toast}
<script>
 var b=document.getElementById('fb');
 if(b) b.onclick=function(){{
   if(!document.querySelector('[data-testid="toast"]')) {{
     b.outerHTML='<button data-testid="111-unfollow" aria-label="{label_following}">Following</button>';
   }}
 }};
 var d=document.getElementById('dis');
 if(d) d.onclick=function(){{ d.closest('[role=dialog]').remove(); }};
 window.__bookmarked=0;
 document.querySelector('[aria-label=Bookmark]').onclick=function(){{window.__bookmarked++;}};
</script>
</body></html>"""


def main():
    print("1) _parse_count (Arabic digits / suffixes):")
    for raw, exp in [("٣٫٢ ألف", 3200), ("١٢٠٠", 1200), ("1.2K\nFollowers", 1200),
                     ("2 مليون متابع", 2_000_000), ("12,345", 12345), ("abc", 0)]:
        got = _parse_count(raw)
        check(f"«{raw!r}» -> {got}", got == exp, f"expected {exp}")

    print("2) ineligible_reason:")
    cfg = {"follow": {"enabled": True, "skip_verified": True, "require_bio": True,
                      "min_followers": 50, "max_followers": 150000}}
    base = {"bio": "x", "followers": 500, "verified": False, "protected": False}
    check("eligible", ineligible_reason(base, cfg) is None)
    check("unknown followers is not 0", ineligible_reason({**base, "followers": None}, cfg) is None)
    check("protected", ineligible_reason({**base, "protected": True}, cfg) == "protected")
    check("verified", ineligible_reason({**base, "verified": True}, cfg) == "verified")
    check("too few", ineligible_reason({**base, "followers": 3}, cfg) == "followers-too-few")

    print("3) _handle_from_href:")
    check("handle", _handle_from_href("/alice") == "alice")
    check("status link ignored", _handle_from_href("/alice/status/1") is None)

    with sync_playwright() as pw:
        from app.browser import _find_chromium_executable
        browser = pw.chromium.launch(executable_path=_find_chromium_executable(), args=["--no-sandbox"])
        page = browser.new_page()
        human = Human(page)

        print("4) read_profile (verified badge scoped to header):")
        page.set_content(profile_html(verified=False))
        p = read_profile(page, "alice")
        check("NOT verified although timeline/sidebar have badges", p["verified"] is False, p)
        check("followers from /verified_followers link", p["followers"] == 1200, p)
        check("following parsed", p["following"] == 350, p)
        check("bio", p["bio"] == "crypto trader", p)
        page.set_content(profile_html(verified=True))
        check("verified when badge in header", read_profile(page, "alice")["verified"] is True)
        page.set_content(profile_html(protected=True))
        check("protected detected", read_profile(page, "alice")["protected"] is True)
        page.set_content(profile_html(followers_href="followers", followers_txt="٣٫٢ ألف"))
        check("plain /followers + Arabic count", read_profile(page, "alice")["followers"] == 3200)
        page.set_content("<html><body><div data-testid='UserName'>x</div></body></html>")
        p = read_profile(page, "alice")
        check("unknown followers -> None (not 0)", p["followers"] is None, p)

        print("5) follow_user:")
        page.set_content(profile_html())
        ok, st = follow_user(page, human, "alice")
        check("follows profile button (not sidebar/tweet)", ok and st == "followed", (ok, st))
        check("bookmark never clicked", page.evaluate("window.__bookmarked") == 0)
        check("sidebar button untouched", page.locator('[data-testid="555-follow"]').count() == 1)
        page.set_content(profile_html(following=True))
        check("is_following true", is_following(page, "alice"))
        ok, st = follow_user(page, human, "alice")
        check("already following -> no click", ok and st == "already-following", (ok, st))
        page.set_content(profile_html(ar=True))
        ok, st = follow_user(page, human, "alice")
        check("Arabic UI labels", ok and st == "followed", (ok, st))
        page.set_content(profile_html(block_toast=True))
        ok, st = follow_user(page, human, "alice")
        check("rate-limit toast -> follow-blocked", (not ok) and st == "follow-blocked", (ok, st))
        page.set_content("<html><body><aside data-testid='sidebarColumn'>"
                         "<div data-testid='UserCell'><button data-testid='5-follow'>Follow</button></div>"
                         "</aside></body></html>")
        ok, st = follow_user(page, human, "alice")
        check("only sidebar buttons -> no-follow-button", (not ok) and st == "no-follow-button", (ok, st))

        print("6) dismiss_all_modals:")
        page.set_content(profile_html(with_dialog=True))
        check("Views dialog dismissed", dismiss_all_modals(page, human) is True
              and page.locator('[role=dialog]').count() == 0)
        page.set_content(profile_html())
        check("no dialog -> nothing clicked", dismiss_all_modals(page, human) is False
              and page.evaluate("window.__bookmarked") == 0)

        print("7) _authors_in_view:")
        page.set_content(profile_html())
        check("author handle read from User-Name", _authors_in_view(page) == ["bigverified"])

        print("8) follow_pass end-to-end (routed fake x.com):")
        import tempfile, time
        from app import config as _cfg, grow
        _cfg.FOLLOWED_GLOBAL_PATH = os.path.join(tempfile.mkdtemp(), "g.json")

        class FastHuman(Human):
            def pause(self, lo=1.0, hi=3.0):
                return 0

        def tweet(h, verified=False):
            return (f'<article data-testid="tweet"><div data-testid="User-Name">'
                    f'<a href="/{h}">{h}</a>{VERIFIED_SVG if verified else ""}</div>'
                    f'<a href="/{h}/status/1">t</a></article>')

        timeline = "<html><body><main>" + "".join(
            [tweet("whaleverified", True), tweet("goodguy"), tweet("lowfol"),
             tweet("ownacct"), tweet("lockedguy"), tweet("goodguy2")]) + "</main></body></html>"
        profiles = {
            "whaleverified": profile_html("whaleverified", verified=True),
            "goodguy": profile_html("goodguy"),
            "goodguy2": profile_html("goodguy2"),
            "lowfol": profile_html("lowfol", followers_txt="12 Followers"),
            "lockedguy": profile_html("lockedguy", protected=True),
        }

        def handler(route):
            path = route.request.url.split("x.com", 1)[1].split("?")[0].strip("/")
            body = timeline if path in ("home", "search") else profiles.get(path, "<html><body></body></html>")
            route.fulfill(status=200, content_type="text/html", body=body)

        page.route("https://x.com/**", handler)
        fh = FastHuman(page)
        cfg2 = {"follow": {"enabled": True, "skip_verified": True, "require_bio": True,
                           "min_followers": 50, "max_followers": 150000,
                           "visit_only_chance": 0, "delay_between_s": [1, 1],
                           "queries": ["bitcoin"]},
                "manager_handle": "mgr", "resilience": {"network_retries": 0}}
        state = {"caps": {"follows": 5}, "done": {"follows": 0}, "seen_users": [],
                 "followed_handles": []}
        grow.load_accounts = lambda: {"acc_x": {"handle": "ownacct"}}
        grow.save_state = lambda *_a, **_k: None
        n, st = grow.follow_pass(page, "acc_x", cfg2, state, fh, 5)
        check("followed exactly the two good profiles", n == 2 and
              sorted(state["followed_handles"]) == ["goodguy", "goodguy2"],
              (n, st, state["followed_handles"]))
        check("verified/protected/low/own never followed",
              not {"whaleverified", "lowfol", "lockedguy", "ownacct"} & set(state["followed_handles"]))
        check("all processed handles marked seen",
              {"whaleverified", "lowfol", "lockedguy"} <= set(state["seen_users"]))
        check("global registry updated", grow.load_global_followed() == {"goodguy", "goodguy2"})
        state2 = {"caps": {"follows": 5}, "done": {"follows": 0}, "seen_users": [], "followed_handles": []}
        n2, _ = grow.follow_pass(page, "acc_y", cfg2, state2, fh, 5)
        check("second account skips globally-followed handles", "goodguy" not in state2["followed_handles"], state2["followed_handles"])
        state3 = {"caps": {"follows": 5}, "done": {"follows": 0}, "follow_blocked_until": time.time() + 600}
        check("blocked-until pauses follows", grow.follow_budget(state3, cfg2) == 0)

        browser.close()

    print(f"\nResult: {PASS} passed / {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
