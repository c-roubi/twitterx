# -*- coding: utf-8 -*-
"""تشخيص مفصّل للاتصال بمزوّد الذكاء الاصطناعي: DNS ← TCP ← TLS ← قائمة النماذج ← توليد.

يقيس كل مرحلة بالمللي ثانية ويكشف الأسباب الشائعة للتعليق: بروكسي النظام، مسار IPv6 معطّل،
اعتراض TLS (برنامج حماية يبدّل الشهادة)، أو بطء النموذج نفسه. لا يطبع المفتاح في أي ناتج.
"""
import socket
import ssl
import time
import urllib.request

from . import ai_comments, net

HOSTS = {
    "gemini": "generativelanguage.googleapis.com",
    "groq": "api.groq.com",
    "openai": "api.openai.com",
}
# جهات الإصدار المتوقعة (جزء من الاسم) لكشف اعتراض TLS
EXPECTED_ISSUERS = {
    "gemini": ("google", "gts"),
    "groq": ("cloudflare", "google", "let's encrypt", "digicert", "sectigo"),
    "openai": ("cloudflare", "google", "let's encrypt", "digicert"),
}


def _step(name, fn):
    t = time.time()
    try:
        res = fn()
        ok, detail, warn = (res if isinstance(res, tuple) else (True, res, False))
    except Exception as e:
        ok, detail, warn = False, "{}: {}".format(type(e).__name__, str(e)[:160]), False
    return {"name": name, "ok": bool(ok), "warn": bool(warn), "ms": int((time.time() - t) * 1000), "detail": detail}


def _redact(text, key):
    return text.replace(key, "***") if key else text


def run(provider, api_key, model="", net_cfg=None):
    provider = provider if provider in HOSTS else "gemini"
    host = HOSTS[provider]
    key = ai_comments.clean_key(api_key)
    ctx = net.ssl_context(net_cfg)
    steps = []
    state = {}

    def proxies():
        env = {k: v for k, v in urllib.request.getproxies_environment().items() if v and k in ("http", "https", "all")}
        allp = urllib.request.getproxies()
        reg_only = {k: v for k, v in allp.items() if k not in env}
        if env:
            return True, "متغيرات بيئة: " + ", ".join("{}={}".format(k, v.split("@")[-1]) for k, v in env.items()) + " (يُستخدم للاتصال بالمزوّد)", False
        if reg_only:
            return True, "بروكسي نظام ويندوز موجود ({}) لكن التطبيق يتجاهله عمداً ويتصل مباشرة مثل curl".format(", ".join(reg_only)), True
        return True, "لا يوجد بروكسي - اتصال مباشر", False

    def dns():
        t = time.time()
        infos = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
        v4 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET})
        v6 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET6})
        state["v4"], state["v6"] = v4, v6
        return True, "IPv4: {} عنوان · IPv6: {} عنوان".format(len(v4), len(v6)), not v4

    def tcp4():
        if not state.get("v4"):
            return False, "لا عنوان IPv4", False
        s = socket.create_connection((state["v4"][0], 443), 8)
        state["sock4"] = s
        return True, "اتصال TCP ناجح مع " + state["v4"][0], False

    def tcp6():
        if not state.get("v6"):
            return True, "لا IPv6 (لا مشكلة)", False
        try:
            socket.create_connection((state["v6"][0], 443), 5).close()
            return True, "IPv6 يعمل", False
        except OSError as e:
            return True, "IPv6 معطّل ({}) — التطبيق يستخدم IPv4 فقط فلا يتأثر".format(str(e)[:60]), True

    def tls():
        s = state.get("sock4")
        if not s:
            return False, "تخطّي (لا TCP)", False
        # سياق للقراءة فقط: نحتاج الشهادة للعرض
        ss = ctx.wrap_socket(s, server_hostname=host)
        state["tls"] = ss
        cert = ss.getpeercert() or {}
        issuer = " ".join(v for rdn in cert.get("issuer", ()) for k, v in rdn if k in ("organizationName", "commonName")) or "غير معروف"
        low = issuer.lower()
        intercept = not any(x in low for x in EXPECTED_ISSUERS[provider])
        ss.close()
        if intercept:
            return True, "صادرة عن: {} ← ⚠️ ليست من {} المتوقعة: على الأرجح برنامج حماية/جدار ناري يعترض HTTPS (قد يُبطئ الطلبات أو يعطّلها)".format(issuer, PROVIDER_LABEL[provider]), True
        return True, "الشهادة صادرة عن: {} (سليمة)".format(issuer), False

    def listing():
        if provider == "gemini":
            url, hdr = "https://{}/v1beta/models?pageSize=1".format(host), {"x-goog-api-key": key}
        else:
            url = "https://{}/{}models".format(host, "openai/v1/" if provider == "groq" else "v1/")
            hdr = {"Authorization": "Bearer " + key}
        try:
            r = ai_comments._call_api(url, hdr, None, ctx, timeout=20, method="GET")
        except ai_comments.AIError as e:
            s = str(e)
            if provider == "gemini" and s.startswith(("HTTP 400", "HTTP 401", "HTTP 403")):  # صيغ AQ. تعمل عبر ?key=
                r = ai_comments._call_api(url + "&key=" + urllib.request.quote(key, safe=""), {}, None, ctx, timeout=20, method="GET")
            else:
                raise
        n = len(r.get("models") or r.get("data") or [])
        return True, "المفتاح مقبول ✓ ({})".format("نماذج متاحة" if n else "ردّ سليم"), False

    def generate():
        t0 = time.time()
        ai = {"provider": provider, "api_key": key, "model": model, "min_interval_s": 0}
        out = ai_comments.generate_ai_comment("BTC is breaking resistance with strong volume", ai, net_cfg=net_cfg,
                                              raise_errors=True, ignore_interval=True, lang="en")
        return True, "ردّ بعد {:.1f} ث: «{}»".format(time.time() - t0, out), (time.time() - t0) > 15

    for name, fn in (("بروكسي", proxies), ("DNS", dns), ("اتصال TCP (IPv4)", tcp4), ("اتصال IPv6", tcp6),
                     ("تشفير TLS والشهادة", tls), ("فحص المفتاح", listing), ("توليد تعليق", generate)):
        s = _step(name, fn)
        steps.append(s)
        if not s["ok"] and name in ("DNS", "اتصال TCP (IPv4)"):
            break   # لا فائدة من متابعة المراحل التالية
    s4 = state.get("sock4")
    try:
        s4 and s4.close()
    except OSError:
        pass
    return steps


PROVIDER_LABEL = {"gemini": "Google", "groq": "Groq/Cloudflare", "openai": "OpenAI/Cloudflare"}


def verdict(steps):
    """جملة قصيرة تلخّص أول مشكلة."""
    by = {s["name"]: s for s in steps}
    for name, msg in (("DNS", "تعذّر حلّ اسم المزوّد: مشكلة DNS أو لا إنترنت."),
                      ("اتصال TCP (IPv4)", "لا يصل السيرفر للمزوّد (جدار ناري أو شبكة محجوبة)."),
                      ("تشفير TLS والشهادة", "فشل التشفير: راجع خيارات SSL في تبويب تيليجرام."),
                      ("فحص المفتاح", "المزوّد رفض المفتاح: أنشئ مفتاحاً جديداً."),
                      ("توليد تعليق", "الاتصال سليم لكن التوليد فشل: انظر تفاصيل الخطوة الأخيرة.")):
        if name in by and not by[name]["ok"]:
            return msg
    if by.get("تشفير TLS والشهادة", {}).get("warn"):
        return "الاتصال يعمل لكن الشهادة مبدّلة: على الأرجح برنامج حماية يعترض HTTPS وقد يُبطئ الطلبات."
    if by.get("توليد تعليق", {}).get("warn"):
        return "التوليد يعمل لكنه بطيء جداً (أكثر من 15 ثانية)."
    return "كل شيء يعمل ✓"
