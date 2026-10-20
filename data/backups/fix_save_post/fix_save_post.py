# -*- coding: utf-8 -*-
"""
fix_save_post.py - patch twitter-engage:
  1) selectors.py : expand Discard button candidates
  2) actions.py   : rewrite dismiss_compose_dialog + comment_on_tweet
  3) routine.py   : rewrite _browse_and_interact with mixed random actions
Usage: python fix_save_post.py [base_dir]
"""
import io
import os
import re
import shutil
import sys
import py_compile
from datetime import datetime

BASE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.abspath(".")
APP = os.path.join(BASE, "app")
BACKUP_DIR = os.path.join(BASE, "data", "backups", "fix_save_post")

ok_count = 0
fail_list = []


def read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read()


def write(p, text):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def backup(rel):
    src = os.path.join(APP, rel)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, stamp + "_" + rel.replace(os.sep, "_"))
    shutil.copy2(src, dst)
    return dst


def patch(rel, regex, repl, marker, label):
    global ok_count
    path = os.path.join(APP, rel)
    text = read(path)
    if marker in text:
        print("[SKIP]", label, "- marker already present")
        return
    m = re.search(regex, text, re.S)
    if not m:
        print("[MISS]", label, "- pattern not found, aborting")
        fail_list.append(label)
        return
    backup(rel)
    fixed = text[:m.start()] + repl + text[m.end():]
    if marker not in fixed:
        print("[MISS]", label, "- marker missing in replacement")
        fail_list.append(label)
        return
    write(path, fixed)
    ok_count += 1
    print("[OK]", label)


# ---------------------------------------------------------------------------
# 1) selectors.py
# ---------------------------------------------------------------------------
NEW_DISCARD = (
    "DRAFT_DISCARD_BTN_CANDIDATES = [\n"
    "    # native <button> inside the 'Save post?' sheet (implicit role)\n"
    "    'button[data-testid=\"confirmationSheetCancel\"]',\n"
    "    '[data-testid=\"confirmationSheet\"] button:has-text(\"Discard\")',\n"
    "    '[data-testid=\"confirmationSheet\"] button:has-text(\"\u062a\u062c\u0627\u0647\u0644\")',\n"
    "    'div[data-testid=\"confirmationSheetCancel\"]',\n"
    "    '[role=\"button\"]:has-text(\"Discard\")',\n"
    "    '[role=\"button\"]:has-text(\"\u062a\u062c\u0627\u0647\u0644\")',\n"
    "    'button:has-text(\"Discard\")',\n"
    "    'button:has-text(\"\u062a\u062c\u0627\u0647\u0644\")',\n"
    "    '[data-testid=\"confirmationSheet\"] [role=\"button\"]:last-child',\n"
    "]\n"
    "# FIX-SAVEPOST-V2-SELECTORS\n"
)

RX_DISCARD = r"DRAFT_DISCARD_BTN_CANDIDATES = \[(?:[^\n]*\n)*?\]\n"

# ---------------------------------------------------------------------------
# 2) actions.py : dismiss_compose_dialog
# ---------------------------------------------------------------------------
NEW_DISMISS = (
    "def dismiss_compose_dialog(page, human):\n"
    "    \"\"\"\u0627\u0644\u062e\u0631\u0648\u062c \u0627\u0644\u0622\u0645\u0646 \u0645\u0646 \u0645\u062d\u0631\u0631 \u0627\u0644\u0627\u0642\u062a\u0628\u0627\u0633/\u062d\u0648\u0627\u0631 \u062d\u0641\u0638 \u0627\u0644\u0645\u0633\u0648\u062f\u0629 (Discard \u0641\u0642\u0637\u060c \u0644\u0627 Save \u0623\u0628\u062f\u0627\u064b).\n"
    "\n"
    "    \u064a\u063a\u0644\u0642 \u0623\u064a \u062d\u0648\u0627\u0631 'Save post?'/'\u062a\u062c\u0627\u0647\u0644' \u0648\u064a\u062e\u0631\u062c \u0645\u0646 \u0635\u0641\u062d\u0629 /compose \u0625\u0646 \u0643\u0646\u0627 \u0639\u0627\u0644\u0642\u064a\u0646 \u0641\u064a\u0647\u0627.\n"
    "    \u064a\u0631\u062c\u0639 True \u0625\u0630\u0627 \u062a\u0639\u0627\u0645\u0644 \u0645\u0639 \u0634\u064a\u0621\u060c \u0648\u0625\u0644\u0627 False.\n"
    "    \"\"\"\n"
    "    try:\n"
    "        for _ in range(3):\n"
    "            sheets = page.locator('[data-testid=\"confirmationSheet\"]')\n"
    "            if sheets.count() > 0:\n"
    "                discard = selectors.first(page, DRAFT_DISCARD_BTN_CANDIDATES)\n"
    "                if discard is not None:\n"
    "                    try:\n"
    "                        discard.hover()\n"
    "                    except Exception:\n"
    "                        pass\n"
    "                    human.pause(0.4, 1.2)\n"
    "                    discard.click()\n"
    "                    page.wait_for_timeout(1200)\n"
    "                    if sheets.count() == 0:\n"
    "                        return True\n"
    "                    continue\n"
    "            if \"/compose/\" in (page.url or \"\"):\n"
    "                # \u0645\u062d\u0631\u0631 \u0645\u0641\u062a\u0648\u062d \u0628\u0644\u0627 \u062d\u0648\u0627\u0631: Escape \u064a\u063a\u0644\u0642\u0647 \u0623\u0648 \u064a\u0638\u0647\u0631 \u062d\u0648\u0627\u0631 \u0627\u0644\u062d\u0641\u0638 \u0641\u0646\u0631\u0641\u0636\u0647\n"
    "                page.keyboard.press(\"Escape\")\n"
    "                page.wait_for_timeout(900)\n"
    "                continue\n"
    "            break\n"
    "        # \u0644\u0645 \u062a\u0632\u0644 \u0627\u0644\u0648\u0631\u0642\u0629 \u0623\u0648 \u0639\u0627\u0644\u0642\u0648\u0646 \u0641\u064a /compose: \u063a\u0627\u062f\u0631 \u0627\u0644\u0635\u0641\u062d\u0629\n"
    "        try:\n"
    "            if page.locator('[data-testid=\"confirmationSheet\"]').count() > 0 or \"/compose/\" in (page.url or \"\"):\n"
    "                page.goto(\"https://x.com/home\", timeout=45000)\n"
    "                page.wait_for_timeout(1500)\n"
    "                return True\n"
    "        except Exception:\n"
    "            pass\n"
    "        return False\n"
    "    except Exception:\n"
    "        return False\n"
    "# FIX-SAVEPOST-V2-DISMISS\n"
)

RX_DISMISS = r"def dismiss_compose_dialog\(page, human\):.*?(?=def open_tweet\(page, url\):)"

# ---------------------------------------------------------------------------
# 2b) actions.py : comment_on_tweet
# ---------------------------------------------------------------------------
NEW_COMMENT = (
    "def comment_on_tweet(page, text, human):\n"
    "    \"\"\"\u062a\u0639\u0644\u064a\u0642 \u0646\u0635\u064a \u0639\u0644\u0649 \u0627\u0644\u062a\u063a\u0631\u064a\u062f\u0629 \u0627\u0644\u0645\u0641\u062a\u0648\u062d\u0629 \u062d\u0627\u0644\u064a\u0627\u064b (\u0645\u0639 \u062a\u0646\u0638\u064a\u0641 \u0623\u064a \u0645\u0633\u0648\u062f\u0629 \u0639\u0627\u0644\u0642\u0629).\"\"\"\n"
    "    dismiss_compose_dialog(page, human)  # \u062e\u0631\u0648\u062c \u0622\u0645\u0646 \u0645\u0646 \u0623\u064a \u0645\u0633\u0648\u062f\u0629/\u062d\u0648\u0627\u0631 \u0633\u0627\u0628\u0642\n"
    "    for attempt in range(MAX_ATTEMPTS):\n"
    "        try:\n"
    "            reply = selectors.first(page, REPLY_BTN_CANDIDATES)\n"
    "            if reply is not None:\n"
    "                reply.hover()\n"
    "                human.pause(0.5, 1.6)\n"
    "                reply.click()\n"
    "                page.wait_for_timeout(900)\n"
    "            ta = selectors.first(page, TEXTAREA_CANDIDATES)\n"
    "            if ta is None:\n"
    "                dismiss_compose_dialog(page, human)\n"
    "                return False, \"no-textarea\"\n"
    "            ta.click()\n"
    "            human.pause(0.6, 1.8)\n"
    "            # \u0643\u062a\u0627\u0628\u0629 \u0628\u0637\u064a\u0626\u0629 \u062d\u0631\u0641\u0627\u064b \u062d\u0631\u0641\u0627\u064b\n"
    "            for ch in text:\n"
    "                page.keyboard.type(ch, delay=random.randint(30, 110))\n"
    "            human.pause(1.2, 3.0)\n"
    "            send = selectors.first(page, SEND_BTN_CANDIDATES)\n"
    "            if send is None:\n"
    "                dismiss_compose_dialog(page, human)  # Escape \u0642\u062f \u064a\u0641\u062a\u062d \u062d\u0648\u0627\u0631 Save post?\n"
    "                return False, \"no-send-button\"\n"
    "            if not send.is_enabled():\n"
    "                # \u0631\u0628\u0645\u0627 \u0641\u0642\u062f \u0627\u0644\u0646\u0635 \u0627\u0644\u062a\u0631\u0643\u064a\u0632: \u0623\u0639\u062f \u0627\u0644\u0646\u0642\u0631 \u0648\u0623\u0639\u062f \u0627\u0644\u0643\u062a\u0627\u0628\u0629\n"
    "                try:\n"
    "                    ta.click()\n"
    "                    human.pause(0.5, 1.2)\n"
    "                    page.keyboard.press(\"Control+A\")\n"
    "                    page.keyboard.press(\"Delete\")\n"
    "                    for ch in text:\n"
    "                        page.keyboard.type(ch, delay=random.randint(30, 110))\n"
    "                    human.pause(1.0, 2.2)\n"
    "                except Exception:\n"
    "                    pass\n"
    "                send = selectors.first(page, SEND_BTN_CANDIDATES)\n"
    "                if send is None or not send.is_enabled():\n"
    "                    dismiss_compose_dialog(page, human)\n"
    "                    return False, \"send-disabled\"\n"
    "            send.click()\n"
    "            page.wait_for_timeout(2500)\n"
    "            # \u062a\u062d\u0642\u0642: \u0635\u0646\u062f\u0648\u0642 \u0627\u0644\u0646\u0635 \u0623\u0635\u0628\u062d \u0641\u0627\u0631\u063a\u0627\u064b\n"
    "            try:\n"
    "                val = (ta.inner_text() or \"\").strip()\n"
    "            except Exception:\n"
    "                val = \"\"\n"
    "            if val == \"\":\n"
    "                dismiss_compose_dialog(page, human)  # \u062a\u0646\u0638\u064a\u0641 \u0623\u064a \u062d\u0648\u0627\u0631 \u0645\u062a\u0628\u0642\u064d \u0642\u0628\u0644 \u0627\u0644\u0639\u0648\u062f\u0629\n"
    "                return True, \"commented\"\n"
    "        except Exception as e:\n"
    "            dismiss_compose_dialog(page, human)\n"
    "            if attempt == MAX_ATTEMPTS - 1:\n"
    "                return False, _err_status(e)\n"
    "            human.pause(2, 4)\n"
    "    dismiss_compose_dialog(page, human)\n"
    "    return False, \"unconfirmed\"\n"
    "# FIX-SAVEPOST-V2-COMMENT\n"
)

RX_COMMENT = r"def comment_on_tweet\(page, text, human\):.*?(?=def _tweet_text_of)"

# ---------------------------------------------------------------------------
# 3) routine.py : _browse_and_interact (mixed actions)
# ---------------------------------------------------------------------------
STATE_KEY = {"like": "likes", "retweet": "retweets", "comment": "comments"}

NEW_MIX = (
    "# FIX-MIX-V1-STATE\n"
    "STATE_KEY = {\"like\": \"likes\", \"retweet\": \"retweets\", \"comment\": \"comments\"}\n"
    "\n"
    "\n"
    "def _pick_browse_action(cfg, state):\n"
    "    \"\"\"\u0627\u062e\u062a\u064a\u0627\u0631 \u062a\u0641\u0627\u0639\u0644 \u0639\u0634\u0648\u0627\u0626\u064a \u0648\u0627\u062d\u062f \u0644\u0644\u062f\u0648\u0631 (like/retweet/comment) \u062d\u0633\u0628 \u0627\u0644\u0623\u0648\u0632\u0627\u0646.\"\"\"\n"
    "    mix = cfg.get(\"engagement\", {}).get(\"action_mix\", {})\n"
    "    w = {\n"
    "        \"like\": float(mix.get(\"like\", 0.60)),\n"
    "        \"retweet\": float(mix.get(\"retweet\", 0.18)),\n"
    "        \"comment\": float(mix.get(\"comment\", 0.22)),\n"
    "    }\n"
    "    avail = [k for k in (\"like\", \"retweet\", \"comment\")\n"
    "             if w[k] > 0 and _can_do(state, STATE_KEY[k])]\n"
    "    if not avail:\n"
    "        return None\n"
    "    total = sum(w[k] for k in avail)\n"
    "    r = random.random() * total\n"
    "    acc = 0.0\n"
    "    for k in avail:\n"
    "        acc += w[k]\n"
    "        if r <= acc:\n"
    "            return k\n"
    "    return avail[-1]\n"
    "\n"
    "\n"
    "def _like_one_random(page, articles, acc_id, state, human):\n"
    "    \"\"\"\u0644\u0627\u064a\u0643 \u0644\u062a\u063a\u0631\u064a\u062f\u0629 \u0639\u0634\u0648\u0627\u0626\u064a\u0629 \u0648\u0627\u062d\u062f\u0629 \u0636\u0645\u0646 \u0627\u0644\u062d\u062f \u0627\u0644\u064a\u0648\u0645\u064a.\"\"\"\n"
    "    for art in random.sample(articles, min(len(articles), 5)):\n"
    "        try:\n"
    "            if art.locator(UNLIKE_BTN).first.count() > 0:\n"
    "                continue\n"
    "            like_btn = art.locator(LIKE_BTN).first\n"
    "            if like_btn.count() == 0 or not like_btn.is_visible():\n"
    "                continue\n"
    "            like_btn.hover()\n"
    "            human.pause(0.7, 2.0)\n"
    "            like_btn.click()\n"
    "            page.wait_for_timeout(1200)\n"
    "            _mark_done(state, \"likes\")\n"
    "            log.info(f\"{acc_id} | \u0644\u0627\u064a\u0643 \u0639\u0634\u0648\u0627\u0626\u064a #{state['done']['likes']}\")\n"
    "            return True\n"
    "        except Exception:\n"
    "            continue\n"
    "    return False\n"
    "\n"
    "\n"
    "def _retweet_one_random(page, articles, acc_id, state, human):\n"
    "    \"\"\"\u0631\u064a\u062a\u0648\u064a\u062a \u0644\u062a\u063a\u0631\u064a\u062f\u0629 \u0639\u0634\u0648\u0627\u0626\u064a\u0629 \u0648\u0627\u062d\u062f\u0629 \u0636\u0645\u0646 \u0627\u0644\u062d\u062f \u0627\u0644\u064a\u0648\u0645\u064a.\"\"\"\n"
    "    for art in random.sample(articles, min(len(articles), 5)):\n"
    "        try:\n"
    "            if art.locator(UNRETWEET_BTN).first.count() > 0:\n"
    "                continue\n"
    "            rt = art.locator(RETWEET_BTN).first\n"
    "            if rt.count() == 0 or not rt.is_visible():\n"
    "                continue\n"
    "            rt.hover()\n"
    "            human.pause(0.8, 2.2)\n"
    "            rt.click()\n"
    "            page.wait_for_timeout(1200)\n"
    "            confirm = selectors.first(page, RETWEET_CONFIRM_CANDIDATES)\n"
    "            if confirm is not None:\n"
    "                confirm.click()\n"
    "                page.wait_for_timeout(1500)\n"
    "                if \"/compose/\" in (page.url or \"\"):\n"
    "                    dismiss_compose_dialog(page, human)\n"
    "                    log.warning(f\"{acc_id} | \u0641\u064f\u062a\u062d \u0645\u062d\u0631\u0631 \u0627\u0644\u0627\u0642\u062a\u0628\u0627\u0633 \u0628\u0627\u0644\u062e\u0637\u0623 - \u0623\u064f\u063a\u0644\u0642 \u0628\u062f\u0648\u0646 \u062d\u0641\u0638\")\n"
    "                    return False\n"
    "                _mark_done(state, \"retweets\")\n"
    "                log.info(f\"{acc_id} | \u0631\u064a\u062a\u0648\u064a\u062a \u0639\u0634\u0648\u0627\u0626\u064a #{state['done']['retweets']}\")\n"
    "                return True\n"
    "            page.keyboard.press(\"Escape\")\n"
    "        except Exception:\n"
    "            continue\n"
    "    return False\n"
    "\n"
    "\n"
    "def _browse_and_interact(page, acc_id, cfg, state, human, session_secs, session_start, acc=None):\n"
    "    \"\"\"\u062a\u0635\u0641\u062d \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629 \u0628\u062e\u0644\u0637 \u0639\u0634\u0648\u0627\u0626\u064a: \u0644\u0627\u064a\u0643/\u0631\u064a\u062a\u0648\u064a\u062a/\u062a\u0639\u0644\u064a\u0642 \u0645\u062a\u0646\u0627\u0648\u0628\u064a\u0646 \u0645\u062b\u0644 \u0627\u0644\u0625\u0646\u0633\u0627\u0646.\"\"\"\n"
    "    en_ratio = _account_en_ratio(acc)\n"
    "    last_action = None\n"
    "    scrolls = 0\n"
    "    dismiss_compose_dialog(page, human)  # \u062a\u0646\u0638\u064a\u0641 \u0623\u064a \u0645\u062d\u0631\u0631/\u062d\u0648\u0627\u0631 \u0645\u0646 \u062c\u0648\u0644\u0629 \u0633\u0627\u0628\u0642\u0629\n"
    "    while time.time() - session_start < session_secs:\n"
    "        save_state(acc_id, state)  # \u062d\u0641\u0638 \u0627\u0644\u062a\u0642\u062f\u0645 \u0639\u0646\u062f \u0643\u0644 \u062f\u0648\u0631\n"
    "        # \u0627\u0644\u062e\u0631\u0648\u062c \u0627\u0644\u0645\u0628\u0643\u0631 \u0625\u0630\u0627 \u0627\u0643\u062a\u0645\u0644\u062a \u062d\u0635\u0629 \u0627\u0644\u062a\u0641\u0627\u0639\u0644\u0627\u062a\n"
    "        if (not _can_do(state, \"likes\") and not _can_do(state, \"retweets\")\n"
    "                and not _can_do(state, \"comments\")):\n"
    "            break\n"
    "        # \u0625\u0646 \u0628\u0642\u064a \u0645\u062d\u0631\u0631 \u0627\u0644\u0627\u0642\u062a\u0628\u0627\u0633 \u0645\u0641\u062a\u0648\u062d\u0627\u064b: \u0623\u063a\u0644\u0642\u0647 \u0648\u0646\u0624\u062c\u0644 \u0627\u0644\u062f\u0648\u0631\n"
    "        if \"/compose/\" in (page.url or \"\"):\n"
    "            dismiss_compose_dialog(page, human)\n"
    "            if \"/compose/\" in (page.url or \"\"):\n"
    "                log.warning(f\"{acc_id} | \u0645\u062d\u0631\u0631 \u0627\u0644\u0627\u0642\u062a\u0628\u0627\u0633 \u0639\u0627\u0644\u0642 - \u062a\u062e\u0637\u064a \u062c\u0648\u0644\u0629\")\n"
    "                human.pause(4, 8)\n"
    "                continue\n"
    "        # \u062a\u0645\u0631\u064a\u0631 \u0648\u062a\u0648\u0642\u0641\n"
    "        human.pause(2.0, 6.0)\n"
    "        page.mouse.wheel(0, random.randint(500, 1500))\n"
    "        scrolls += 1\n"
    "        if scrolls % 3 == 0:\n"
    "            human.pause(4.0, 12.0)  # \u062a\u0648\u0642\u0641 \"\u0644\u0644\u0642\u0631\u0627\u0621\u0629\"\n"
    "\n"
    "        articles = page.locator(TWEET_ARTICLE).all()\n"
    "        if not articles:\n"
    "            human.pause(3, 6)\n"
    "            continue\n"
    "\n"
    "        action = _pick_browse_action(cfg, state)\n"
    "        if action is None:\n"
    "            break\n"
    "        # \u0643\u0633\u0631 \u0627\u0644\u062a\u0643\u0631\u0627\u0631: \u0625\u0646 \u062a\u0643\u0631\u0631 \u0646\u0641\u0633 \u0627\u0644\u062a\u0641\u0627\u0639\u0644\u060c \u0623\u0639\u062f \u0627\u0644\u0627\u062e\u062a\u064a\u0627\u0631 \u0628\u0646\u0633\u0628\u0629 75%\n"
    "        if action == last_action and random.random() < 0.75:\n"
    "            other = [a for a in (\"like\", \"retweet\", \"comment\")\n"
    "                     if a != last_action and _can_do(state, STATE_KEY[a])]\n"
    "            if other:\n"
    "                action = random.choice(other)\n"
    "        last_action = action\n"
    "\n"
    "        if action == \"like\":\n"
    "            _like_one_random(page, articles, acc_id, state, human)\n"
    "        elif action == \"retweet\":\n"
    "            _retweet_one_random(page, articles, acc_id, state, human)\n"
    "        else:  # comment\n"
    "            try:\n"
    "                art = random.choice(articles)\n"
    "                link = art.locator('a[href*=\"/status/\"]').first\n"
    "                if link.count() > 0:\n"
    "                    href = link.get_attribute(\"href\") or \"\"\n"
    "                    if href.startswith(\"/\"):\n"
    "                        href = \"https://x.com\" + href\n"
    "                    if _open_and_comment(page, href, acc_id, state, human, cfg=cfg, en_ratio=en_ratio):\n"
    "                        log.info(f\"{acc_id} | \u062a\u0639\u0644\u064a\u0642 \u0639\u0634\u0648\u0627\u0626\u064a #{state['done']['comments']}\")\n"
    "                # \u0627\u0644\u0631\u062c\u0648\u0639 \u0644\u0644\u0631\u0626\u064a\u0633\u064a\u0629 \u0628\u0639\u062f \u0627\u0644\u062a\u0639\u0644\u064a\u0642 (\u0646\u062c\u0627\u062d\u0627\u064b \u0623\u0648 \u0641\u0634\u0644\u0627\u064b)\n"
    "                try:\n"
    "                    page.goto(\"https://x.com/home\", timeout=45000)\n"
    "                    page.wait_for_timeout(1500)\n"
    "                except Exception:\n"
    "                    pass\n"
    "            except Exception:\n"
    "                pass\n"
    "        save_state(acc_id, state)\n"
    "# FIX-MIX-V1\n"
)

RX_MIX = r"def _browse_and_interact\(.*?(?=def _open_and_comment)"


def main():
    print("=== fix_save_post: patching", BASE, "===")
    patch("selectors.py", RX_DISCARD, NEW_DISCARD, "FIX-SAVEPOST-V2-SELECTORS", "selectors.discard-candidates")
    patch("actions.py", RX_DISMISS, NEW_DISMISS, "FIX-SAVEPOST-V2-DISMISS", "actions.dismiss-compose-dialog")
    patch("actions.py", RX_COMMENT, NEW_COMMENT, "FIX-SAVEPOST-V2-COMMENT", "actions.comment-on-tweet")
    patch("routine.py", RX_MIX, NEW_MIX, "FIX-MIX-V1", "routine.mixed-browse")

    if fail_list:
        print("[FAIL] Missing patterns:", ", ".join(fail_list))
        print("       No changes were applied for those files. Restore manually from", BACKUP_DIR)
        sys.exit(1)

    # compile check
    bad = []
    for rel in ("selectors.py", "actions.py", "routine.py"):
        p = os.path.join(APP, rel)
        try:
            py_compile.compile(p, doraise=True)
            print("[COMPILED]", rel)
        except Exception as e:
            bad.append((rel, str(e).encode("ascii", "replace").decode()))
    if bad:
        print("[FAIL] compile errors:")
        for rel, err in bad:
            print("   ", rel, "-", err)
        sys.exit(1)

    print("=== DONE: apply count =", ok_count, "===")
    print("=== Backups:", BACKUP_DIR, "===")
    sys.exit(0)


if __name__ == "__main__":
    main()
