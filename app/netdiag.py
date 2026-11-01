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


def _issuer(ssock):
    """جهة إصدار شهادة الخادم. مع truststore يعيد getpeercert() قاموساً فارغاً فنفكّ الشهادة الثنائية بنفسنا. None إن تعذّر."""
    cert = ssock.getpeercert() or {}
    if cert.get("issuer"):
        rdns = cert["issuer"]
    else:
        import os
        import tempfile
        der = ssock.getpeercert(True)
        if not der:
            return None
        fd, path = tempfile.mkstemp(suffix=".pem")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(ssl.DER_cert_to_PEM_cert(der))
            rdns = ssl._ssl._test_decode_cert(path).get("issuer", ())
        except Exception:
            return None
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    return " ".join(v for rdn in rdns for k, v in rdn if k in ("organizationName", "commonName")) or None


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
        issuer = _issuer(ss)
        ss.close()
        if not issuer:
            return True, "التشفير ناجح (تعذّر قراءة جهة إصدار الشهادة، وهذا لا يعني مشكلة)", False
        low = issuer.lower()
        if not any(x in low for x in EXPECTED_ISSUERS[provider]):
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

    def count_tokens():
        """طلب POST خفيف لا يولّد شيئاً: يفصل «POST لا يعمل» عن «التوليد وحده بطيء/محجوب»."""
        m = model or ai_comments.GEMINI_FALLBACKS[0]
        url = "https://{}/v1beta/models/{}:countTokens".format(host, m)
        body = {"contents": [{"parts": [{"text": "hello"}]}]}
        r = ai_comments._gemini_call(url, key, body, ctx, timeout=15)
        return True, "POST يعمل (عدّ {} رمزاً بالنموذج {})".format(r.get("totalTokens", "؟"), m), False

    def make_generate(m):
        def generate():
            t0 = time.time()
            ai = {"provider": provider, "api_key": key, "model": m, "min_interval_s": 0}
            out = ai_comments.generate_ai_comment("BTC is breaking resistance with strong volume", ai, net_cfg=net_cfg,
                                                  raise_errors=True, ignore_interval=True, lang="en", timeout=20, single_model=True)
            return True, "ردّ بعد {:.1f} ث: «{}»".format(time.time() - t0, out), (time.time() - t0) > 15
        return generate

    gen_models = ([model] if model else ai_comments.GEMINI_FALLBACKS) if provider == "gemini" else [model or ai_comments.DEFAULT_MODELS.get(provider, "")]
    plan = [("بروكسي", proxies), ("DNS", dns), ("اتصال TCP (IPv4)", tcp4), ("اتصال IPv6", tcp6),
            ("تشفير TLS والشهادة", tls), ("فحص المفتاح", listing)]
    if provider == "gemini":
        plan.append(("POST خفيف", count_tokens))
    plan += [("توليد ({})".format(m or "افتراضي"), make_generate(m)) for m in gen_models]

    for name, fn in plan:
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
                      ("فحص المفتاح", "المزوّد رفض المفتاح: أنشئ مفتاحاً جديداً.")):
        if name in by and not by[name]["ok"]:
            return msg
    gens = [s for s in steps if s["name"].startswith("توليد")]
    if gens and not any(s["ok"] for s in gens):
        post_ok = by.get("POST خفيف", {}).get("ok")
        timed_out = all("TIMEOUT" in s["detail"] for s in gens)
        if post_ok and timed_out:
            return ("الاتصال والمفتاح وطلبات POST سليمة، لكن التوليد نفسه لا يردّ: المشكلة عند Google/حسابك وليست في شبكتك "
                    "(تأكد أن المفتاح من مشروع مفعّل في Google AI Studio وغير مقيّد بالمنطقة)، أو جرّب مفتاح Groq المجاني.")
        if post_ok is False and timed_out:
            return "طلبات POST لا تعود (تنتهي مهلتها) بينما GET يعمل: غالباً جدار ناري/برنامج حماية يعطّل الطلبات الكبيرة أو الطويلة."
        return "الاتصال سليم لكن التوليد فشل: انظر تفاصيل خطوات التوليد."
    if by.get("تشفير TLS والشهادة", {}).get("warn"):
        return "الاتصال يعمل لكن الشهادة مبدّلة: على الأرجح برنامج حماية يعترض HTTPS وقد يُبطئ الطلبات."
    if any(s["warn"] for s in gens):
        return "التوليد يعمل لكنه بطيء (أكثر من 15 ثانية)."
    return "كل شيء يعمل ✓"
