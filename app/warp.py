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
import json
import os
import shutil
import ssl
import subprocess
import sys
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
_STATE_PATH = os.path.join(os.path.dirname(_LOCK_PATH), "warp_state.json")
_thread_lock = threading.Lock()

DEFAULTS = {
    "enabled": False,            # ENABLE_WARP_ROTATION
    "accounts_per_ip": 5,        # ACCOUNTS_PER_IP
    "isolate_proxy_mode": True,  # وضع البروكسي المحلي: لا تعارض مع مشاريع أخرى
    "proxy_port": DEFAULT_PROXY_PORT,
    "disconnect_wait_s": 2.5,
    "connect_wait_s": 4.5,
    "verify_retries": 3,
    "auto_install": True,        # تثبيت warp-cli تلقائياً إن لم يكن موجوداً
    "ensure_new_ip": True,       # تأكيد أن IP تغيّر فعلاً بعد كل تدوير
    "max_ip_attempts": 3,        # محاولات الحصول على IP مختلف
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


def _sh(cmd, timeout=600):
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def is_installed():
    return shutil.which(WARP_CLI) is not None


def install_warp():
    """تثبيت Cloudflare WARP حسب النظام. يعيد True إذا أصبح warp-cli متاحاً."""
    if is_installed():
        return True
    sudo = "" if (hasattr(os, "geteuid") and os.geteuid() == 0) else "sudo "
    plat = sys.platform
    log.info("warp-cli غير مثبّت - بدء التثبيت التلقائي")
    if plat.startswith("linux"):
        if shutil.which("apt-get"):
            steps = [
                "{s}apt-get update -y && {s}apt-get install -y curl gpg lsb-release ca-certificates",
                "curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | {s}gpg --yes --dearmor "
                "--output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg",
                'echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] '
                'https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | '
                "{s}tee /etc/apt/sources.list.d/cloudflare-client.list",
                "{s}apt-get update -y && {s}apt-get install -y cloudflare-warp",
            ]
        elif shutil.which("dnf") or shutil.which("yum"):
            pm = "dnf" if shutil.which("dnf") else "yum"
            steps = [
                "curl -fsSL https://pkg.cloudflareclient.com/cloudflare-warp-ascii.repo | "
                "{s}tee /etc/yum.repos.d/cloudflare-warp.repo",
                "{s}" + pm + " install -y cloudflare-warp",
            ]
        else:
            log.error("توزيعة لينكس غير مدعومة للتثبيت التلقائي - ثبّت cloudflare-warp يدوياً")
            return False
        for st in steps:
            ok, out = _sh(st.format(s=sudo))
            if not ok:
                log.error("فشل خطوة التثبيت: {} | {}".format(st[:60], out[-300:]))
                return False
        _sh("{s}systemctl enable --now warp-svc".format(s=sudo), 60)
    elif plat == "darwin":
        ok, out = _sh("brew install --cask cloudflare-warp")
        if not ok:
            log.error("فشل brew: " + out[-300:])
            return False
    elif plat.startswith("win"):
        ok, out = _sh("winget install -e --id Cloudflare.Warp --accept-source-agreements --accept-package-agreements")
        if not ok:
            log.error("فشل winget: " + out[-300:])
            return False
    else:
        return False
    return is_installed()


def _ensure_service():
    """تشغيل خدمة warp-svc على لينكس إن كانت متوقفة."""
    if not sys.platform.startswith("linux"):
        return
    rc, out = _run("status")
    if rc != 0 and ("daemon" in out.lower() or "connect" in out.lower() or "ipc" in out.lower()):
        sudo = "" if os.geteuid() == 0 else "sudo "
        _sh(sudo + "systemctl start warp-svc", 60)
        time.sleep(3)


def _ensure_registered():
    rc, _ = _run("registration", "show")
    if rc == 0:
        return True
    rc, out = _run("--accept-tos", "registration", "new")
    if rc != 0:  # نسخ قديمة
        rc, out = _run("--accept-tos", "register")
    if rc != 0:
        log.error("تعذّر تسجيل WARP: " + out)
    return rc == 0


def get_exit_ip(port=DEFAULT_PROXY_PORT, timeout=10):
    """عنوان IP الخارجي كما يراه الإنترنت عبر بروكسي WARP المحلي (أو None)."""
    try:
        import socks
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, "127.0.0.1", port, rdns=True)
        s.settimeout(timeout)
        s.connect(("www.cloudflare.com", 443))
        ss = ssl.create_default_context().wrap_socket(s, server_hostname="www.cloudflare.com")
        ss.sendall(b"GET /cdn-cgi/trace HTTP/1.1\r\nHost: www.cloudflare.com\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            chunk = ss.recv(4096)
            if not chunk:
                break
            data += chunk
        ss.close()
        for line in data.decode("utf-8", "replace").splitlines():
            if line.startswith("ip="):
                return line[3:].strip()
    except Exception as e:
        log.debug("تعذّر قراءة IP الخارجي: {}".format(e))
    return None


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


def _cycle(s, sleep):
    """دورة واحدة: disconnect -> انتظار -> connect -> انتظار -> تحقق. (ok, خطأ_قاتل)"""
    rc, out = _run("disconnect")
    if rc == 127:
        return False, True
    sleep(s["disconnect_wait_s"])
    rc, out = _run("connect")
    if rc != 0:
        log.warning("فشل connect: " + out)
    sleep(s["connect_wait_s"])
    for _ in range(s["verify_retries"]):
        if is_connected():
            return True, False
        sleep(2)
    return False, False


def rotate_ip(settings=None, sleep=time.sleep):
    """تدوير IP مع ضمان تغيّره فعلاً: إن بقي نفس العنوان نعيد المحاولة، وفي الأخيرة نجدّد تسجيل WARP."""
    s = dict(DEFAULTS)
    s.update(settings or {})
    if not s["enabled"]:
        return False
    with _thread_lock, _FileLock():
        if not is_installed() and not (s["auto_install"] and install_warp()):
            log.error("warp-cli غير مثبّت")
            return False
        if s["isolate_proxy_mode"]:
            ensure_proxy_mode(s["proxy_port"])
        check = s["ensure_new_ip"]
        old_ip = get_exit_ip(s["proxy_port"]) if check else None
        attempts = max(1, int(s["max_ip_attempts"])) if check else 1
        for n in range(attempts):
            if n == attempts - 1 and n > 0:
                # آخر محاولة: هوية تسجيل جديدة تعطي عنواناً جديداً غالباً
                log.info("IP لم يتغيّر - تجديد تسجيل WARP")
                _run("registration", "delete")
                _run("--accept-tos", "registration", "new")
                if s["isolate_proxy_mode"]:
                    ensure_proxy_mode(s["proxy_port"])
            ok, fatal = _cycle(s, sleep)
            if fatal:
                return False
            if not ok:
                continue
            new_ip = get_exit_ip(s["proxy_port"]) if check else None
            if not check or old_ip is None or new_ip is None or new_ip != old_ip:
                log.info("تم تدوير IP عبر WARP: {} -> {}".format(old_ip or "?", new_ip or "?"))
                return True
            log.warning("نفس IP ({}) - محاولة {}/{}".format(new_ip, n + 1, attempts))
        log.error("تعذّر الحصول على IP جديد / WARP غير متصل")
        return False


def maybe_rotate(index, settings=None, sleep=time.sleep):
    """للاستدعاء داخل حلقة الحسابات: يدوّر فقط عند index % accounts_per_ip == 0 (وindex > 0)."""
    s = dict(DEFAULTS)
    s.update(settings or {})
    n = int(s["accounts_per_ip"])
    if not s["enabled"] or n <= 0 or index == 0 or index % n != 0:
        return False
    return rotate_ip(s, sleep=sleep)


def _settings(cfg):
    s = dict(DEFAULTS)
    s.update((cfg or {}).get("warp") or {})
    return s


def enabled(cfg):
    return bool(_settings(cfg)["enabled"])


def _load_count():
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            return int(json.load(f).get("count", 0))
    except (OSError, ValueError, TypeError):
        return 0


def _save_count(n):
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"count": n}, f)
    os.replace(tmp, _STATE_PATH)


def ensure_ready(cfg):
    """تجهيز كامل: تثبيت -> خدمة -> تسجيل -> وضع البروكسي -> اتصال."""
    s = _settings(cfg)
    if not s["enabled"]:
        return False
    with _thread_lock, _FileLock():
        if not is_installed() and not (s["auto_install"] and install_warp()):
            log.error("WARP غير مثبّت وتعذّر تثبيته")
            return False
        _ensure_service()
        _ensure_registered()
        if s["isolate_proxy_mode"]:
            ensure_proxy_mode(s["proxy_port"])
        if not is_connected():
            _run("connect")
            time.sleep(s["connect_wait_s"])
        return is_connected()


def before_account(cfg, sleep=time.sleep):
    """يُستدعى قبل فتح كل حساب: يعدّ الحسابات ويدوّر IP كل accounts_per_ip حساب.

    العدّاد محفوظ على القرص فيبقى صحيحاً عبر إعادة التشغيل. يعيد True إذا دُوِّر IP.
    """
    s = _settings(cfg)
    if not s["enabled"]:
        return False
    with _thread_lock:
        index = _load_count()
        _save_count(index + 1)
    if index == 0:
        ensure_ready(cfg)
        return False
    rotated = maybe_rotate(index, s, sleep=sleep)
    if rotated:
        from . import proxies
        proxies._cache.clear()  # أعد اختبار منفذ WARP بعد التدوير
    return rotated
