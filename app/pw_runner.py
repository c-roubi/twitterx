# -*- coding: utf-8 -*-
"""تشغيل جلسات Playwright Sync داخل خيط (thread) مستقل بحلقة asyncio نظيفة.

السبب الجذري لخطأ:
    "It looks like you are using Playwright Sync API inside the asyncio loop."
هو أن كل الجلسات كانت تُشغّل Playwright على نفس الخيط الرئيسي. عند انعلاق جلسة
(انهيار متصفح، مهلة، بروكسي معلّق...) تبقى حلقة asyncio الخاصة بها بحالة "تعمل"
(is_running=True) مثبّتةً على الخيط. الجلسة التالية حين تستدعي
`sync_playwright().start()` تلتقط هذه الحلقة العالقة فيُرفع الخطأ، ويتكرر الفشل
لكل الجلسات بعدها.

الحل: تشغيل كل جلسة داخل خيط جديد خاص بها. الخيط الجديد لا يملك حلقة asyncio، لذا
ينشئ Playwright حلقة نظيفة خاصة به (own_loop) ويغلقها عند `pw.stop()`. وعند انتهاء
الخيط تُنظّف الحلقة نهائياً، فلا تتسرب أي حلقة عالقة إلى الجلسة التالية.
"""
import threading

from .logger import get_logger

log = get_logger("pw_runner")


def run_isolated(fn, *args, **kwargs):
    """تشغيل دالة تستخدم Playwright Sync داخل خيط مستقل وإرجاع نتيجتها.

    - كل الكائنات (pw/context/page) تُنشأ وتُستخدم وتُغلق داخل نفس الخيط (شرط Playwright).
    - أي استثناء داخل الخيط يُعاد رفعه في الخيط المستدعي كما لو نُفّذت الدالة مباشرة.
    - الخيط daemon حتى لا يمنع إغلاق البرنامج إن علق المتصفح؛ ومع ذلك ننتظره (join)
      ليبقى السلوك مطابقاً للتشغيل المتسلسل السابق.
    """
    box = {}

    def _target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - نُعيد رفعه في الخيط الأصلي
            box["error"] = e

    t = threading.Thread(target=_target, name="pw-session", daemon=True)
    t.start()
    t.join()

    if "error" in box:
        raise box["error"]
    return box.get("value")
