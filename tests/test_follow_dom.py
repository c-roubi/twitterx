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


        print("9) discovery: reposted-by lists + keywords + spam filters:")
        from app.grow import (DEFAULT_QUERIES, pick_query, search_url, _tweets_in_view,
                              collect_candidates)
        page.unroute_all()

        def rt_article(author, tid, n):
            label = f"{n} reposts. Repost" if n else "Repost"
            return (f'<article data-testid="tweet"><div data-testid="User-Name"><a href="/{author}">{author}</a></div>'
                    f'<a href="/{author}/status/{tid}">time</a>'
                    f'<button data-testid="retweet" aria-label="{label}">{n or ""}</button></article>')

        search_html = "<html><body><main>" + "".join([
            rt_article("popular", 111, 25), rt_article("midtweet", 222, 4),
            rt_article("lowtweet", 333, 1), rt_article("quiet", 444, 0)]) + "</main></body></html>"

        def cells(*hs):
            return "<html><body>" + "".join(
                f'<div data-testid="UserCell"><a href="/{h}">{h}</a></div>' for h in hs) + "</body></html>"

        pages = {
            "search": search_html,
            "popular/status/111/retweets": cells("rt_a", "rt_b", "ownacct", "rt_c"),
            "midtweet/status/222/retweets": cells("rt_d", "rt_a"),
            "home": "<html><body><main>" + tweet("tl_guy") + "</main></body></html>",
        }

        def handler2(route):
            path = route.request.url.split("x.com", 1)[1].split("?")[0].strip("/")
            route.fulfill(status=200, content_type="text/html",
                          body=pages.get(path, "<html><body></body></html>"))

        page.route("https://x.com/**", handler2)
        page.goto("https://x.com/search?q=x")
        rows = _tweets_in_view(page, 3)
        check("reposted tweets ranked from aria-label counts",
              sorted(rows, reverse=True) == [(25, "111", "popular"), (4, "222", "midtweet")], rows)
        cfg3 = {"follow": {"min_reposts": 3, "retweeter_tweets": 2, "queries": ["bitcoin"]},
                "resilience": {"network_retries": 0}}
        found = collect_candidates(page, "acc_x", cfg3, fh, {"ownacct"}, need=8)
        check("handles collected from reposted-by lists", {"rt_a", "rt_b", "rt_c", "rt_d"} <= set(found), found)
        check("excluded handles never returned", "ownacct" not in found, found)
        check("no duplicates", len(found) == len(set(found)), found)
        check("authors from live search/timeline also included", {"popular", "tl_guy"} & set(found), found)

        print("   keywords:")
        check("keyword list is broad (>=30 incl. cashtags + Arabic)",
              len(DEFAULT_QUERIES) >= 30 and any(q.startswith("$") for q in DEFAULT_QUERIES)
              and any("كريبتو" in q for q in DEFAULT_QUERIES))
        seq = [pick_query({"follow": {"queries": ["a", "b", "c", "d", "e", "f"]}}, avoid=[]) for _ in range(1)]
        recent = []
        picks = [pick_query({"follow": {"queries": list("abcdef")}}, avoid=recent) for _ in range(12)]
        check("no immediate keyword repeat", all(picks[i] != picks[i + 1] for i in range(len(picks) - 1)), picks)
        u = search_url("$BTC", True, 5)
        check("live search url: spam excludes + min_retweets", "-giveaway" in u.replace("%20-", " -").replace("%2D", "-") or "giveaway" in u)
        check("min_retweets operator present", "min_retweets%3A5" in u, u)
        check("people tab has no excludes", "giveaway" not in search_url("bitcoin", live=False))

        print("   spam filters:")
        base2 = {"bio": "crypto trader", "followers": 500, "following": 300, "verified": False, "protected": False}
        check("giveaway bio rejected", ineligible_reason({**base2, "bio": "Daily GIVEAWAY!! follow back"}, cfg) == "spam-bio")
        check("follow-bot ratio rejected", ineligible_reason({**base2, "followers": 400, "following": 7500}, cfg) == "follow-spam")
        check("normal profile still eligible", ineligible_reason(base2, cfg) is None)

        browser.close()

    print(f"\nResult: {PASS} passed / {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
