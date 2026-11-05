# -*- coding: utf-8 -*-
"""استرجاع data/accounts.json بعد ضياعه (مثلاً بعد git reset --hard).

يستنتج الحسابات من:
  - مجلدات data/profiles/acc_XXX و data/sessions و data/state (معرّفات الحسابات)
  - سجلات data/logs/*.log (أسطر "بدء جلسة acc_XXX (handle)" و "إحماء acc_XXX (handle)")

التشغيل (من مجلد المشروع):
  python tools/recover_accounts.py            # عرض ما سيُستعاد فقط
  python tools/recover_accounts.py --apply    # كتابة accounts.json (مع نسخة احتياطية .bak)
"""
import glob
import json
import os
import re
import shutil
import sys

def _find_data():
    """مجلد data: بجانب السكربت، أو بمجلده الأب، أو بمجلد التشغيل الحالي."""
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.dirname(here), os.getcwd()):
        d = os.path.join(base, "data")
        if os.path.isdir(d):
            return d
    return os.path.join(os.getcwd(), "data")


DATA = _find_data()
ACC_RE = re.compile(r"^acc_\d+$")
LOG_RE = re.compile(r"(?:بدء جلسة|إحماء)\s+(acc_\d+)\s+\(([^)]*)\)")


def ids_on_disk():
    ids = set()
    for pat in ("profiles/*", "sessions/*.json", "state/*.json"):
        for p in glob.glob(os.path.join(DATA, pat)):
            name = os.path.splitext(os.path.basename(p))[0]
            if ACC_RE.match(name):
                ids.add(name)
    return ids


def handles_from_logs():
    found = {}
    for path in sorted(glob.glob(os.path.join(DATA, "logs", "*.log"))):   # الأحدث يغلب
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = LOG_RE.search(line)
                    if m and m.group(2).strip():
                        found[m.group(1)] = m.group(2).strip().lstrip("@")
        except OSError:
            pass
    return found


def main():
    apply = "--apply" in sys.argv
    path = os.path.join(DATA, "accounts.json")
    try:
        with open(path, encoding="utf-8") as f:
            accounts = json.load(f)
    except Exception:
        accounts = {}
    print(f"مجلد البيانات: {DATA}")
    logs = handles_from_logs()
    ids = ids_on_disk() | set(logs)
    print(f"معرّفات الحسابات الموجودة على القرص/السجلات: {sorted(ids)}")
    changed = 0
    for aid in sorted(ids):
        handle = logs.get(aid, "")
        cur = accounts.get(aid)
        # حساب المستودع الوهمي (account1) يُستبدل بالحقيقي إن عرفناه
        if cur and (not handle or cur.get("handle") == handle):
            continue
        accounts[aid] = {
            "username": (cur or {}).get("username", ""),
            "handle": handle or (cur or {}).get("handle", ""),
            "display_name": (cur or {}).get("display_name", ""),
            "timezone": (cur or {}).get("timezone", "Asia/Riyadh"),
            "enabled": True,
            "notes": "recovered",
        }
        changed += 1
        print(f"  {aid}: handle={accounts[aid]['handle'] or '(غير معروف - عدّله يدوياً)'}")
    if not changed:
        print("لا شيء لتغييره.")
        return
    if not apply:
        print("\n(عرض فقط) أعد التشغيل مع --apply للكتابة.")
        return
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)
    print(f"\n✓ كُتب {path} ({len(accounts)} حساب). أعد تشغيل التطبيق.")


if __name__ == "__main__":
    main()
