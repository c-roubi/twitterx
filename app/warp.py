# -*- coding: utf-8 -*-
"""تدوير IP عبر Cloudflare WARP (warp-cli) بدون التأثير على باقي المشاريع على الجهاز.

مشكلة التعارض: وضع WARP الافتراضي (warp) يمرّر *كل* ترافيك الجهاز عبر النفق،
فأي مشروع آخر يتصل ببروكسي سيتأثر عند disconnect/connect (انقطاع + تغيّر مسار).

الحل: وضع البروكسي المحلي (warp-cli mode proxy). يفتح WARP منفذ SOCKS5 على
127.0.0.1:40000 ولا يلمس جدول التوجيه إطلاقاً؛ فقط ما يُوجَّه صراحةً لهذا
المنفذ (متصفحات هذه الأداة) يمرّ عبر WARP، وباقي المشاريع تبقى كما هي.
على أن disconnect/connect تعيد تشغيل النفق نفسه فيتغيّر IP هذا المنفذ فقط.

إضافة: قفل ملفي بين العمليات حتى لا تدوّر عمليتان من هذه الأداة بنفس الوقت.
"""
import os
import subprocess
import threading
import time

from .logger import get_logger

try:
    import fcntl  # لينكس/ماك
except ImportError:  # ويندوز
    fcntl = None

log = get_logger("warp")

WARP_CLI = "warp-cli"
DEFAULT_PROXY_PORT = 40000
_LOCK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "warp_rotate.lock")
_thread_lock = threading.Lock()

DEFAULTS = {
    "enabled": False,            # ENABLE_WARP_ROTATION
    "accounts_per_ip": 5,        # ACCOUNTS_PER_IP
    "isolate_proxy_mode": True,  # وضع البروكسي المحلي: لا تعارض مع مشاريع أخرى
    "proxy_port": DEFAULT_PROXY_PORT,
    "disconnect_wait_s": 2.5,
    "connect_wait_s": 4.5,
    "verify_retries": 3,
}


def _run(*args, timeout=20):
    """تنفيذ warp-cli؛ يعيد (returncode, output) ولا يرمي استثناء أبداً."""
    try:
        p = subprocess.run([WARP_CLI, *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except FileNotFoundError:
        return 127, "warp-cli غير مثبّت"
    except subprocess.TimeoutExpired:
        return 124, "انتهت مهلة warp-cli"
    except OSError as e:
        return 1, str(e)


def is_connected():
    rc, out = _run("status")
    return rc == 0 and "connected" in out.lower() and "disconnected" not in out.lower()


def ensure_proxy_mode(port=DEFAULT_PROXY_PORT):
    """تفعيل وضع البروكسي المحلي مرة واحدة (آمن للتكرار). يعيد True عند النجاح."""
    rc, out = _run("mode", "proxy")
    if rc != 0:
        log.warning("تعذّر ضبط وضع proxy: " + out)
        return False
    if port != DEFAULT_PROXY_PORT:
        _run("proxy", "port", str(port))
    return True


def proxy_url(cfg=None):
    """عنوان بروكسي WARP المحلي لتمريره للمتصفح (يتوافق مع app.proxies.parse_proxy)."""
    port = ((cfg or {}).get("warp") or {}).get("proxy_port", DEFAULT_PROXY_PORT)
    return "socks5://127.0.0.1:{}".format(port)


class _FileLock:
    def __enter__(self):
        os.makedirs(os.path.dirname(_LOCK_PATH), exist_ok=True)
        self._f = open(_LOCK_PATH, "w")
        if fcntl:
            fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl:
            fcntl.flock(self._f, fcntl.LOCK_UN)
        self._f.close()


def rotate_ip(settings=None, sleep=time.sleep):
    """disconnect -> انتظار -> connect -> انتظار -> تحقق. يعيد True إذا نجح التدوير."""
    s = dict(DEFAULTS)
    s.update(settings or {})
    if not s["enabled"]:
        return False
    with _thread_lock, _FileLock():
        if s["isolate_proxy_mode"]:
            ensure_proxy_mode(s["proxy_port"])
        rc, out = _run("disconnect")
        if rc == 127:
            log.error(out)
            return False
        sleep(s["disconnect_wait_s"])
        rc, out = _run("connect")
        if rc != 0:
            log.warning("فشل connect: " + out)
        sleep(s["connect_wait_s"])
        for _ in range(s["verify_retries"]):
            if is_connected():
                log.info("تم تدوير IP عبر WARP")
                return True
            sleep(2)
        log.error("WARP غير متصل بعد التدوير")
        return False


def maybe_rotate(index, settings=None, sleep=time.sleep):
    """للاستدعاء داخل حلقة الحسابات: يدوّر فقط عند index % accounts_per_ip == 0 (وindex > 0)."""
    s = dict(DEFAULTS)
    s.update(settings or {})
    n = int(s["accounts_per_ip"])
    if not s["enabled"] or n <= 0 or index == 0 or index % n != 0:
        return False
    return rotate_ip(s, sleep=sleep)
