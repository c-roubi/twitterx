# -*- coding: utf-8 -*-
"""توليد تعليقات عبر الذكاء الاصطناعي (اختياري) - بدون أي مكتبات خارجية.

المزوّدون المدعومون (الطبقات المجانية):
  - gemini : Google AI Studio -> https://aistudio.google.com/apikey
  - openai : OpenAI API (يوجد حد مجاني) -> https://platform.openai.com
  - groq   : Groq (مجاني سخي) -> https://console.groq.com/keys

أي خطأ في الشبكة أو المفتاح -> إرجاع None ليقع المحرك على البديل السياقي المحلي.
"""
import hashlib
import http.client
import json
import os
import re
import random
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from . import net
from .config import DATA_DIR, _atomic_save
from .logger import get_logger

log = get_logger("ai")

# نماذج Gemini القديمة (1.5) أُوقفت؛ نجرّب بالترتيب عند خطأ 404 إن لم يحدّد المستخدم نموذجاً
RETIRED_GEMINI = ("gemini-1.5", "gemini-pro", "gemini-1.0")
# الأسرع أولاً (flash-lite لا «يفكّر» طويلاً: مناسب لتعليق من سطر واحد)؛ ننتقل للتالي عند 404 أو مهلة أو ازدحام
GEMINI_FALLBACKS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-flash-latest"]
RETRY_NEXT_MODEL = ("HTTP 404", "HTTP 429", "HTTP 500", "HTTP 503", "TIMEOUT")
REQUEST_TIMEOUT_S = 40

DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.1-8b-instant",
}

_PERSONAS = [
    "an excited crypto follower since years",
    "a retail trader learning from the analysis",
    "a beginner analyst who loves charts and indicators",
    "a calm follower who values clear calls",
    "a daily crypto trader who knows the market well",
]

_SYSTEM_PROMPT = (
    "You are a crypto follower replying to a Twitter signal account. "
    "Reply with one very short sentence (10-60 characters) in English in ~90% of cases, "
    "and rarely in simple Arabic. When the tweet mentions a coin ticker, include it with "
    "a dollar sign (e.g. $EPIC) in most of your English replies. "
    "React positively, ask a smart question, or add a quick TA note. "
    "The reply MUST be about something concrete that this specific tweet says "
    "(its coin, event, chart level or question) - never a generic line that could fit any tweet. "
    "Never invent prices, targets or gains, never claim you bought, sold or profited, "
    "and never tell people to buy. "
    "Forbidden: hashtags (#), repeated emojis, mentioning the account owner, or quotes. "
    "Output only the comment."
)

_last_call = 0.0


_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u2060\ufeff"))
PROVIDER_NAMES = {"gemini": "Google Gemini", "groq": "Groq", "openai": "OpenAI"}


def clean_key(key):
    """ينظّف المفتاح الملصوق: مسافات، علامات اقتباس، أسطر، ورموز اتجاه مخفية."""
    k = (key or "").translate(_ZW).strip().strip("\"'`").strip()
    return "".join(k.split())


def detect_key_provider(key):
    """يستنتج مزوّد المفتاح من بادئته (AIza = Gemini، gsk_ = Groq، sk- = OpenAI) أو None."""
    k = clean_key(key)
    if k.startswith(("AIza", "AQ.")):  # AIza = الصيغة التقليدية، AQ. = صيغة Google الأحدث
        return "gemini"
    if k.startswith("gsk_"):
        return "groq"
    if k.startswith("sk-"):
        return "openai"
    return None


def _gemini_call(url, api_key, body, ctx, timeout=REQUEST_TIMEOUT_S):
    """نداء Gemini: المفتاح في الترويسة أولاً (لا يظهر في الرابط)، وإن رُفض نعيد عبر ?key= (تعمل بها بعض صيغ المفاتيح الحديثة)."""
    base = {"Content-Type": "application/json"}
    try:
        return _call_api(url, dict(base, **{"x-goog-api-key": api_key}), body, ctx, timeout=timeout)
    except AIError as e:
        s = str(e)
        if not (s.startswith(("HTTP 400", "HTTP 401", "HTTP 403")) and "key" in s.lower()):
            raise
    log.info("Gemini رفض المفتاح في الترويسة - إعادة المحاولة عبر معامل key")
    return _call_api(url + "?key=" + urllib.parse.quote(api_key, safe=""), base, body, ctx, timeout=timeout)


def check_key_format(provider, key):
    """يرمي AIError برسالة مفهومة إن بدا المفتاح لمزوّد آخر. البادئة غير المعروفة لا تُرفض (قد تتغير صيغ المفاتيح)."""
    k = clean_key(key)
    seen = detect_key_provider(k)
    if seen and seen != provider:
        raise AIError("هذا المفتاح يبدو مفتاح {} وأنت مختار المزوّد «{}». غيّر المزوّد إلى «{}» أو ضع مفتاحاً من المزوّد المختار.".format(
            PROVIDER_NAMES[seen], PROVIDER_NAMES.get(provider, provider), PROVIDER_NAMES[seen]))


# ------------------------------------------------------------------ اكتشاف نماذج Gemini المتاحة للحساب
# Google تُقاعد النماذج للمستخدمين الجدد (404) فلا نعتمد على أسماء ثابتة: نسأل حسابنا عمّا هو متاح ونرتّبه (الأسرع أولاً).
STATE_PATH = os.path.join(DATA_DIR, "ai_state.json")
DISCOVERY_ENABLED = True
DISC_TTL_S = 6 * 3600
_PLAIN = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash(-lite)?$")
_ALIASES = ("gemini-flash-lite-latest", "gemini-flash-latest")
_disc_cache = {}   # بصمة المفتاح -> (وقت، قائمة)
_good_model = {}   # بصمة المفتاح -> آخر نموذج نجح


def _fp(key):
    return hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:12]


def reset_cache():
    _disc_cache.clear()
    _good_model.clear()


def _recall(key):
    fp = _fp(key)
    if fp in _good_model:
        return _good_model[fp]
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get(fp)
    except (OSError, ValueError):
        return None


def _remember(key, model):
    fp = _fp(key)
    if _good_model.get(fp) == model:
        return
    _good_model[fp] = model
    try:
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
        data[fp] = model
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        _atomic_save(data, STATE_PATH)
    except OSError:
        pass


def rank_models(names):
    """يرتّب أسماء النماذج الصالحة للتعليقات: flash-lite الأحدث أولاً ثم flash الأحدث ثم الأسماء المستعارة (latest)."""
    names = set(names)
    lite, flash = [], []
    for n in names:
        m = _PLAIN.match(n)
        if m:
            (lite if m.group(2) else flash).append((float(m.group(1)), n))
    return [n for _, n in sorted(lite, reverse=True)] + [n for _, n in sorted(flash, reverse=True)] + [a for a in _ALIASES if a in names]


def _gemini_get(url, api_key, ctx, timeout):
    try:
        return _call_api(url, {"x-goog-api-key": api_key}, None, ctx, timeout=timeout, method="GET")
    except AIError as e:
        if not str(e).startswith(("HTTP 400", "HTTP 401", "HTTP 403")):
            raise
    sep = "&" if "?" in url else "?"
    return _call_api(url + sep + "key=" + urllib.parse.quote(api_key, safe=""), {}, None, ctx, timeout=timeout, method="GET")


def discover_gemini_models(api_key, ctx, timeout=15):
    """النماذج التي تدعم generateContent عند هذا المفتاح، مرتّبة بالأفضلية."""
    found, token = [], None
    for _ in range(4):
        url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100"
        if token:
            url += "&pageToken=" + urllib.parse.quote(token, safe="")
        r = _gemini_get(url, api_key, ctx, timeout)
        for m in r.get("models") or []:
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                found.append((m.get("name") or "").replace("models/", ""))
        token = r.get("nextPageToken")
        if not token:
            break
    return rank_models(found)


def candidate_models(api_key, ctx, user_model=""):
    """ترتيب التجربة: اختيار المستخدم، ثم آخر نموذج نجح، ثم المكتشف من الحساب، ثم الاحتياطي الثابت."""
    order = [user_model] if user_model else []
    good = _recall(api_key)
    if good:
        order.append(good)
    if DISCOVERY_ENABLED:
        fp = _fp(api_key)
        hit = _disc_cache.get(fp)
        if hit and time.time() - hit[0] < DISC_TTL_S:
            found = hit[1]
        else:
            try:
                found = discover_gemini_models(api_key, ctx)
                _disc_cache[fp] = (time.time(), found)
            except Exception as e:
                log.info("تعذّر اكتشاف نماذج Gemini ({}) - يُستخدم الاحتياطي".format(str(e)[:80]))
                found = []
        order += found[:4]
    order += GEMINI_FALLBACKS
    seen, out = set(), []
    for m in order:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _thinking_cfg(model):
    """تعطيل/تقليل «التفكير» لأن التعليق سطر واحد (نماذج 2.5 تأخذ ميزانية، و3.x تأخذ مستوى)."""
    if model.startswith("gemini-2.5-flash"):
        return {"thinkingBudget": 0}
    if re.match(r"gemini-(3|flash|flash-lite)", model):
        return {"thinkingLevel": "minimal"}
    return None


class AIError(Exception):
    """خطأ بسبب مفهوم للمستخدم (يُعرض في الاختبار ويُسجَّل في السجلات)."""


USER_AGENT = "SmartEngage/1.0 (+https://github.com/c-roubi/twitterx)"


class _V4HTTPSConnection(http.client.HTTPSConnection):
    """اتصال HTTPS عبر IPv4 فقط: كثير من الخوادم (وWARP) لا تملك مسار IPv6 فيبقى الاتصال معلّقاً حتى المهلة."""

    def connect(self):
        host = self.host
        infos = socket.getaddrinfo(host, self.port, socket.AF_INET, socket.SOCK_STREAM)
        last = None
        for fam, typ, proto, _, addr in infos:
            try:
                self.sock = socket.create_connection(addr, self.timeout)
                break
            except OSError as e:
                last = e
        else:
            raise last or OSError("no IPv4 address for " + host)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._tunnel_host:
            self._tunnel()
            host = self._tunnel_host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=host)


def _env_proxy(host):
    """بروكسي من متغيرات البيئة فقط (مثل curl) - لا نقرأ إعدادات بروكسي ويندوز التي قد تعطّل الطلبات."""
    try:
        if urllib.request.proxy_bypass_environment(host):
            return None
        p = urllib.request.getproxies_environment().get("https")
    except Exception:
        return None
    if not p:
        return None
    u = urllib.parse.urlsplit(p if "//" in p else "http://" + p)
    if not u.hostname:
        return None
    hdr = {}
    if u.username:
        import base64
        tok = base64.b64encode("{}:{}".format(urllib.parse.unquote(u.username), urllib.parse.unquote(u.password or "")).encode()).decode()
        hdr["Proxy-Authorization"] = "Basic " + tok
    return u.hostname, u.port or 8080, hdr


def _http(method, url, headers, data, ctx, timeout):
    """طلب HTTPS واحد عبر http.client. يرجع (status, bytes). يرفع TimeoutError/OSError كما هي."""
    u = urllib.parse.urlsplit(url)
    path = u.path + ("?" + u.query if u.query else "")
    proxy = _env_proxy(u.hostname)
    if proxy:
        conn = _V4HTTPSConnection(proxy[0], proxy[1], timeout=timeout, context=ctx)
        conn.set_tunnel(u.hostname, u.port or 443, headers=proxy[2])
    else:
        conn = _V4HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ctx)
    hdrs = dict(headers)
    hdrs.setdefault("User-Agent", USER_AGENT)
    hdrs.setdefault("Accept", "application/json")
    hdrs["Connection"] = "close"
    try:
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _http_post(url, headers, data, ctx, timeout):
    return _http("POST", url, headers, data, ctx, timeout)


def _http_get(url, headers, ctx, timeout):
    return _http("GET", url, headers, None, ctx, timeout)


def _call_api(url, headers, body, ctx, timeout=REQUEST_TIMEOUT_S, method="POST"):
    try:
        if method == "GET":
            status, raw = _http_get(url, headers, ctx, timeout)
        else:
            status, raw = _http_post(url, headers, json.dumps(body).encode("utf-8"), ctx, timeout)
    except (TimeoutError, socket.timeout):
        raise AIError("TIMEOUT: لم يردّ المزوّد خلال {} ثانية (بطء شبكة أو ازدحام عند المزوّد أو جدار ناري/بروكسي يعطّل الاتصال)".format(timeout)) from None
    except ssl.SSLError as e:
        raise AIError("خطأ شهادة TLS: {} — راجع خيارات SSL في تبويب تيليجرام (ملف شهادات مخصّص)".format(str(e)[:120])) from None
    except OSError as e:
        raise AIError("تعذّر الاتصال بالمزوّد: {}".format(str(e)[:140])) from None
    if status >= 400:
        try:
            msg = json.loads(raw.decode("utf-8", "replace")).get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else str(msg)
        except Exception:
            msg = raw.decode("utf-8", "replace")[:120]
        hint = ""
        if status in (400, 401, 403) and "key" in (msg or "").lower():
            hint = " — المفتاح مرفوض من المزوّد: أنشئ مفتاحاً جديداً وانسخه كاملاً (Gemini: aistudio.google.com/apikey، Groq: console.groq.com/keys)"
        elif status == 403 and "1010" in (msg or ""):
            hint = " — الجدار الحامي للمزوّد رفض هوية البرنامج (غير مفتاح)"
        raise AIError("HTTP {}: {}{}".format(status, (msg or "")[:200], hint))
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise AIError("ردّ غير مفهوم من المزوّد: {}".format(raw[:80].decode("utf-8", "replace"))) from None


def _extract_text(resp, provider):
    if provider == "gemini":
        try:
            return resp["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return None
    # openai / groq (متوافقان مع نفس الشكل)
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _clean(text):
    if not text:
        return None
    text = text.strip().strip('"').strip("'").strip("«»")
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    text = text[:280]
    return text or None


def generate_ai_comment(tweet_text, ai_cfg, net_cfg=None, raise_errors=False, ignore_interval=False, lang=None,
                        timeout=REQUEST_TIMEOUT_S, single_model=False):
    """توليد تعليق (إنجليزي غالباً مع $TICKER) من نص التغريدة عبر المزوّد المحدد.

    يرجع None عند أي خطأ (مع تسجيل السبب في السجلات)؛ raise_errors=True يرفع AIError لعرضه في الاختبار.
    """
    global _last_call

    def fail(msg):
        log.warning("الذكاء الاصطناعي: " + msg)
        if raise_errors:
            raise AIError(msg)
        return None

    if not tweet_text or not tweet_text.strip():
        return fail("نص التغريدة فارغ - لا يمكن تحليلها (تعذّر قراءة النص من الصفحة)")
    api_key = clean_key(ai_cfg.get("api_key"))
    if not api_key:
        return fail("مفتاح API فارغ")
    try:
        check_key_format(ai_cfg.get("provider", "gemini"), api_key)
    except AIError as e:
        return fail(str(e))

    # تباعد زمني بين الطلبات (حماية الطبقة المجانية). لا يُطبَّق على الاختبار ولا على التنفيذ العاجل.
    min_interval = float(ai_cfg.get("min_interval_s", 30) or 0)
    now = time.time()
    if not ignore_interval and now - _last_call < min_interval:
        log.info("الذكاء الاصطناعي: تخطّي (أقل من {} ثانية عن الطلب السابق) - يُستخدم تعليق محلي".format(int(min_interval)))
        return None
    _last_call = now

    provider = ai_cfg.get("provider", "gemini")
    user_model = (ai_cfg.get("model") or "").strip()
    if provider != "gemini" and user_model.lower().startswith("gemini"):
        log.warning("اسم النموذج «{}» خاص بـ Gemini وليس لـ {} - سيُستخدم النموذج الافتراضي".format(user_model, provider))
        user_model = ""
    if provider == "gemini" and user_model.startswith(RETIRED_GEMINI):
        log.warning("النموذج {} متقاعد - سيُستخدم نموذج حديث تلقائياً (فرّغ حقل النموذج في الإعدادات)".format(user_model))
        user_model = ""
    persona = random.choice(_PERSONAS)
    user_msg = "Persona: {}.\n\nTweet:\n{}\n\nWrite only the comment.".format(persona, tweet_text[:500])
    system = _SYSTEM_PROMPT
    if lang == "en":
        system += " IMPORTANT: the tweet is in English, so reply ONLY in English."
    elif lang == "ar":
        system += " IMPORTANT: the tweet is in Arabic, so reply ONLY in simple Arabic (no English sentences)."
    system += " Never repeat common stock phrases; make the reply specific to this tweet."
    ctx = net.ssl_context(net_cfg)

    try:
        if provider == "gemini":
            models = [user_model] if single_model and user_model else candidate_models(api_key, ctx, user_model)
            if single_model and not user_model:
                models = models[:1]
            resp = None
            for i, model in enumerate(models):
                url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent".format(model)
                gen = {"temperature": 0.9, "maxOutputTokens": 400}
                think = _thinking_cfg(model)
                if think:
                    gen["thinkingConfig"] = think

                def post():
                    return _gemini_call(url, api_key, {"contents": [{"parts": [{"text": "{}\n\n{}".format(system, user_msg)}]}],
                                                       "generationConfig": gen}, ctx, timeout=timeout)
                try:
                    try:
                        resp = post()
                    except AIError as e:
                        if think and str(e).startswith("HTTP 400") and "think" in str(e).lower():
                            gen.pop("thinkingConfig", None)   # النموذج لا يقبل هذا الإعداد: أعد بدونه
                            resp = post()
                        else:
                            raise
                    _remember(api_key, model)
                    break
                except AIError as e:
                    msg = str(e)
                    strict_user = bool(user_model) and i == 0          # اختيار المستخدم: لا نستبدله إلا إن لم يعد موجوداً (404)
                    retry = msg.startswith("HTTP 404") if strict_user else msg.startswith(RETRY_NEXT_MODEL)
                    if single_model or i == len(models) - 1 or not retry:
                        raise
                    log.info("نموذج {} لم ينجح ({}) - تجربة التالي".format(model, msg[:70]))
        else:
            url = {
                "openai": "https://api.openai.com/v1/chat/completions",
                "groq": "https://api.groq.com/openai/v1/chat/completions",
            }.get(provider)
            if not url:
                return fail("مزوّد غير مدعوم: {}".format(provider))
            body = {
                "model": user_model or DEFAULT_MODELS.get(provider),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
                "temperature": 0.9,
                "max_tokens": 60,
            }
            resp = _call_api(url, {"Content-Type": "application/json", "Authorization": "Bearer " + api_key}, body, ctx, timeout=timeout)
    except AIError as e:
        return fail(str(e))
    except Exception as e:  # شبكة/شهادات/وقت
        return fail("{}: {}".format(type(e).__name__, str(e)[:160]))

    text = _clean(_extract_text(resp, provider))
    if not text:
        return fail("ردّ المزوّد فارغ أو محجوب")
    return text
