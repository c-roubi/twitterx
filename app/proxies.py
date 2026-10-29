# -*- coding: utf-8 -*-
"""إدارة البروكسيات: توزيع ثابت لكل حساب + اختبار مسبق + رجوع تلقائي للاتصال المباشر."""
import base64
import hashlib
import socket
import time

from .logger import get_logger

log = get_logger("proxies")

TEST_HOST = "x.com"
TEST_PORT = 443
TEST_TTL = 900  # إعادة اختبار نفس البروكسي بعد 15 دقيقة

_cache = {}  # proxy_key -> (ok, tested_at)


def parse_proxy(raw):
    """تحويل نص بروكسي إلى dict منظم، أو None إن كان غير صالح.

    الأشكال المقبولة:
      socks5://user:pass@host:port
      socks5h://host:port
      http://host:port
      host:port
    """
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    if "://" in s:
        scheme, rest = s.split("://", 1)
        scheme = scheme.lower()
    else:
        scheme, rest = "http", s
    if scheme not in ("http", "https", "socks4", "socks5", "socks5h"):
        return None
    user = password = None
    if "@" in rest:
        creds, hostport = rest.rsplit("@", 1)
        if ":" in creds:
            user, password = creds.split(":", 1)
        else:
            user = creds
    else:
        hostport = rest
    if ":" not in hostport:
        return None
    host, _, port_s = hostport.rpartition(":")
    if not host or not port_s.isdigit():
        return None
    port = int(port_s)
    if not (1 <= port <= 65535):
        return None
    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "username": user or None,
        "password": password or None,
    }


def proxy_key(proxy):
    """مفتاح فريد للبروكسي (للتخزين المؤقت لنتائج الاختبار)."""
    if not proxy:
        return ""
    return "{}://{}@{}:{}".format(
        proxy["scheme"],
        proxy.get("username") or "",
        proxy["host"],
        proxy["port"],
    )


def server_string(proxy):
    """سلسلة الخادم المطلوبة من Playwright."""
    if not proxy:
        return ""
    scheme = proxy["scheme"]
    if scheme == "socks5h":
        scheme = "socks5"
    elif scheme == "https":
        scheme = "http"
    return "{}://{}:{}".format(scheme, proxy["host"], proxy["port"])


def proxy_args(proxy):
    """وسائط Playwright للبروكسي (تستخدم داخل new_context / launch_persistent_context)."""
    if not proxy:
        return {}
    args = {"server": server_string(proxy)}
    if proxy.get("username"):
        args["username"] = proxy["username"]
        args["password"] = proxy.get("password") or ""
    return args


def _probe(proxy, timeout, host, port):
    """اختبار حقيقي: فتح اتصال عبر البروكسي إلى host:port."""
    try:
        if proxy["scheme"].startswith("socks"):
            import socks

            s = socks.socksocket()
            stype = socks.SOCKS5 if proxy["scheme"] in ("socks5", "socks5h") else socks.SOCKS4
            s.set_proxy(
                stype,
                proxy["host"],
                proxy["port"],
                username=proxy.get("username"),
                password=proxy.get("password"),
            )
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            return True
        # http/https: طلب CONNECT مباشر
        s = socket.create_connection((proxy["host"], proxy["port"]), timeout=timeout)
        s.settimeout(timeout)
        auth = b""
        if proxy.get("username"):
            token = base64.b64encode(
                "{}:{}".format(proxy["username"], proxy.get("password") or "").encode()
            ).decode()
            auth = "Proxy-Authorization: Basic {}\r\n".format(token).encode()
        req = (
            "CONNECT {}:{} HTTP/1.1\r\nHost: {}:{}\r\n".format(host, port, host, port).encode()
            + auth
            + b"\r\n"
        )
        s.sendall(req)
        resp = s.recv(4096).decode("latin1", "replace")
        s.close()
        return resp.startswith("HTTP/1.1 200") or resp.startswith("HTTP/1.0 200")
    except Exception as e:
        log.debug("Proxy test failed: {} - {}".format(proxy_key(proxy), e))
        return False


def test_proxy(proxy, timeout=10, test_host=TEST_HOST, test_port=TEST_PORT):
    """اختبار صلاحية البروكسي مع تخزين مؤقت للنتيجة."""
    if not proxy:
        return False
    key = proxy_key(proxy)
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[1] < TEST_TTL:
        return cached[0]
    ok = _probe(proxy, timeout, test_host, test_port)
    _cache[key] = (ok, now)
    return ok


def _list_proxies(cfg):
    """قائمة البروكسيات الصالحة من الإعدادات."""
    pcfg = cfg.get("proxies", {})
    if not pcfg.get("enabled"):
        return []
    parsed = []
    for raw in pcfg.get("list") or []:
        p = parse_proxy(raw)
        if p:
            parsed.append(p)
    return parsed


def pick_proxy(acc_id, cfg):
    """اختيار بروكسي ثابت للحساب (تجزئة مستقرة لا تتغير بين التشغيلات) أو None."""
    parsed = _list_proxies(cfg)
    if not parsed:
        return None
    pcfg = cfg.get("proxies", {})
    if pcfg.get("rotate"):
        idx = int(time.time() // 3600) % len(parsed)
    else:
        digest = hashlib.md5(str(acc_id).encode("utf-8")).digest()[:4]
        idx = int.from_bytes(digest, "big") % len(parsed)
    return parsed[idx]


def resolve_for(acc_id, cfg):
    """البروكسي الفعلي للحساب: ثابت + اختبار مسبق + رجوع تلقائي للمباشر عند الفشل.

    FIX-PROXY-V1: البروكسي الذي يفشل في الاختبار لا يُعاد أبداً مهما كانت قيمة
    fallback_to_direct — كان يُعاد ويعلّق إطلاق المتصفح لدقائق قبل التراجع.
    """
    pcfg = cfg.get("proxies", {})
    proxy = pick_proxy(acc_id, cfg)
    if proxy is None:
        return None
    ok = test_proxy(proxy, pcfg.get("test_timeout", 10))
    if ok:
        return proxy
    log.warning("{} | Proxy {} unavailable - falling back to direct connection".format(acc_id, proxy_key(proxy)))
    return None


def resolve_guest(cfg):
    """بروكسي لجلسة الضيف (الراقبة): أول بروكسي سليم في القائمة، أو مباشر.

    FIX-PROXY-V1: لا تُعاد أبداً بروكسي ميت - الاتصال المباشر أفضل من التعليق.
    """
    parsed = _list_proxies(cfg)
    if not parsed:
        return None
    pcfg = cfg.get("proxies", {})
    timeout = pcfg.get("test_timeout", 10)
    for p in parsed[:3]:
        if test_proxy(p, timeout):
            return p
    log.warning("No healthy proxy available - guest session using direct connection")
    return None
