# -*- coding: utf-8 -*-
"""إدارة متصفح Playwright: بروفايل دائم لكل حساب + جلسة ضيف للمراقبة."""
import os
import shutil
import time

from playwright.sync_api import sync_playwright

from . import proxies, selectors
from .config import PROFILES_DIR, SESSIONS_DIR
from .humanizer import fingerprint_for, Human
from .logger import get_logger

log = get_logger("browser")


def _find_chromium_executable():
    """البحث عن ملف chromium التنفيذي: أولاً بالمسار المعتاد لـ Playwright، ثم بالنظام."""
    pw_base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for entry in sorted(os.listdir(pw_base), reverse=True) if os.path.isdir(pw_base) else []:
        if entry.startswith("chromium"):
            for sub in ("chrome-linux64/chrome", "chrome-linux/chrome"):
                p = os.path.join(pw_base, entry, sub)
                if os.path.isfile(p):
                    return p
    return shutil.which("chromium") or shutil.which("chromium-browser")


LOGIN_OK_SELECTORS = [
    'a[href="/home"]',
    '[data-testid="SideNav_AccountSwitcher_Button"]',
    '[data-testid="AppTabBar_Home_Link"]',
    'a[data-testid="AppTabBar_Home_Link"]',
    'a[aria-label="Profile"]',
    '[data-testid="SideNav_NewTweet_Button"]',
]
LOGIN_WALL_SELECTORS = selectors.LOGIN_WALL_CANDIDATES


def _apply_fingerprint(context, fp, real_chrome=False):
    """إخفاء آثار الأتمتة داخل صفحات Chromium الاحتياطي فقط.
    أما كروم الحقيقي (channel=chrome) فيمتلك بصمة سليمة أصلاً، فلا نحقن عليه أي تعديل — لأن أي تباين مصطنع (لغة/أنوية/منصة) يزيد الشبهة بدل ما يقللها.
    """
    if real_chrome:
        return
    try:
        context.add_init_script(
            """
            const hide = (obj, key, val) => {
                try { Object.defineProperty(obj, key, { get: () => val, configurable: true }); }
                catch (e) {}
            };
            hide(navigator, 'webdriver', false);
            hide(navigator, 'languages', ['ar-SA', 'ar', 'en-US', 'en']);
            hide(navigator, 'platform', 'Win32');
            hide(navigator, 'maxTouchPoints', 0);
            hide(navigator, 'hardwareConcurrency', 8);
            window.chrome = window.chrome || { runtime: {} };
            window.navigator.chrome = window.navigator.chrome || { runtime: {} };
            const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
            if (originalQuery) {
                window.navigator.permissions.query = (params) => (
                    params && params.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(params)
                );
            }
            """
        )
    except Exception:
        pass


def _new_fp(account):
    fp = dict(fingerprint_for(account))
    # لا نعيد توليد عشوائي لكل فتحة - البصمة تبقى ثابتة
    return fp


def _launch_with_fallback(launcher, headless_cfg):
    """محاولة النافذة المرئية أولاً؛ إن لم تتوفر شاشة يتحول تلقائياً للوضع المخفي."""
    try:
        return launcher(headless_cfg)
    except Exception as e:
        if "XServer" in str(e) or "no display" in str(e).lower() or "DISPLAY" in str(e):
            import logging
            logging.getLogger("smartengage.browser").warning(
                "لا توجد شاشة متاحة - التحول إلى الوضع المخفي headless"
            )
            return launcher(True)
        raise


def _browser_launchers(cfg):
    """ترتيب محاولات التشغيل: كروم الحقيقي أولاً (بصمة حقيقية) ثم Chromium المرفق احتياطاً."""
    order = []
    if cfg.get("browser", {}).get("use_real_chrome", True):
        order.append(("chrome", True))
    order.append(("chromium", False))
    return order


def launch_guest(cfg, pw):
    """سياق مؤقت بدون تسجيل دخول - لمراقبة تغريدات المدير (مع بروكسي إن كان مفعّلاً)."""
    fp = {
        "viewport": [1366, 768],
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }
    headless_cfg = cfg["browser"].get("headless", False)
    guest_proxy = proxies.resolve_guest(cfg)

    def _launch(headless, use_proxy, use_chrome):
        launch_kwargs = {
            "headless": headless,
            "slow_mo": cfg["browser"].get("slow_mo", 0),
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if use_chrome:
            launch_kwargs["channel"] = "chrome"
        else:
            exe = _find_chromium_executable()
            if exe:
                launch_kwargs["executable_path"] = exe
        browser = pw.chromium.launch(**launch_kwargs)
        ctx_kwargs = {
            "viewport": {"width": fp["viewport"][0], "height": fp["viewport"][1]},
            "locale": "ar",
            "timezone_id": "Asia/Riyadh",
        }
        if not use_chrome:
            ctx_kwargs["user_agent"] = fp["user_agent"]
        if use_proxy:
            ctx_kwargs["proxy"] = proxies.proxy_args(use_proxy)
        context = browser.new_context(**ctx_kwargs)
        _apply_fingerprint(context, fp, use_chrome)
        return context

    last_err = None
    for name, use_chrome in _browser_launchers(cfg):
        for attempt_proxy in (guest_proxy, None):
            if attempt_proxy is None and guest_proxy is not None:
                log.warning(
                    f"فشل فتح سياق الضيف ({name}) بالبروكسي - إعادة المحاولة باتصال مباشر"
                )
            try:
                return _launch_with_fallback(
                    lambda h, p=attempt_proxy, c=use_chrome: _launch(h, p, c),
                    headless_cfg,
                )
            except Exception as e:
                last_err = e
                if attempt_proxy is None:
                    break
        if name != "chromium":
            log.warning(
                f"تعذّر تشغيل {name} ({last_err}) - الرجوع إلى Chromium المرفق"
            )
    raise last_err


def launch_account(account, cfg, pw=None, owned_pw=True):
    """فتح بروفايل دائم للحساب (مسجّل الدخول مسبقاً) مع بروكسي ثابت للحساب.

    يفضّل تشغيل كروم الحقيقي المثبّت (channel=chrome) لبصمة متصفح سليمة؛
    إن فشل يتراجع تلقائياً إلى Chromium المرفق. وإن فشل الاتصال بالبروكسي
    تُعاد المحاولة باتصال مباشر حتى لا تتعطل الحسابات أبداً.
    """
    acc_id = account.get("id", "unknown")
    profile_dir = os.path.join(PROFILES_DIR, acc_id)
    os.makedirs(profile_dir, exist_ok=True)
    fp = _new_fp(account)
    pw_owned = pw is None
    if pw is None:
        pw = sync_playwright().start()
    headless_cfg = cfg["browser"].get("headless", False)
    account_proxy = proxies.resolve_for(acc_id, cfg)

    def _launch(headless, use_proxy, use_chrome):
        ctx_kwargs = {
            "user_data_dir": profile_dir,
            "headless": headless,
            "slow_mo": cfg["browser"].get("slow_mo", 0),
            "viewport": {"width": fp["viewport"][0], "height": fp["viewport"][1]},
            "locale": fp.get("locale", "ar"),
            "timezone_id": fp.get("timezone_id", "Asia/Riyadh"),
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if use_chrome:
            ctx_kwargs["channel"] = "chrome"
        else:
            ctx_kwargs["user_agent"] = fp["user_agent"]
            exe = _find_chromium_executable()
            if exe:
                ctx_kwargs["executable_path"] = exe
        if use_proxy:
            ctx_kwargs["proxy"] = proxies.proxy_args(use_proxy)
        context = pw.chromium.launch_persistent_context(**ctx_kwargs)
        _apply_fingerprint(context, fp, use_chrome)
        page = context.pages[0] if context.pages else context.new_page()
        return pw, context, page, pw_owned

    last_err = None
    for name, use_chrome in _browser_launchers(cfg):
        for attempt_proxy in (account_proxy, None):
            if attempt_proxy is None and account_proxy is not None:
                log.warning(
                    f"{acc_id} | فشل فتح المتصفح ({name}) بالبروكسي - إعادة المحاولة باتصال مباشر"
                )
            try:
                return _launch_with_fallback(
                    lambda h, p=attempt_proxy, c=use_chrome: _launch(h, p, c),
                    headless_cfg,
                )
            except Exception as e:
                last_err = e
                if attempt_proxy is None:
                    break
        if name != "chromium":
            log.warning(
                f"{acc_id} | تعذّر تشغيل {name} ({last_err}) - الرجوع إلى Chromium المرفق"
            )
    if pw_owned:
        try:
            pw.stop()
        except Exception:
            pass
    raise last_err


def is_logged_in(page):
    """فحص حالة تسجيل الدخول من الصفحة الحالية."""
    for sel in LOGIN_OK_SELECTORS:
        try:
            if page.locator(sel).first.count() > 0:
                return True
        except Exception:
            pass
    return False


def is_login_wall(page):
    for sel in LOGIN_WALL_SELECTORS:
        try:
            if page.locator(sel).first.count() > 0:
                return True
        except Exception:
            pass
    return False


def login_account(account, cfg):
    """تسجيل دخول يدوي أول مرة: يفتح المتصفح ويستلم من المستخدم، ثم يحفظ الجلسة."""
    acc_id = account.get("id", "unknown")
    pw = sync_playwright().start()
    try:
        login_cfg = dict(cfg)
        login_cfg["browser"] = dict(cfg.get("browser", {}))
        login_cfg["browser"]["headless"] = False
        _, context, page, _ = launch_account(account, login_cfg, pw=pw, owned_pw=False)
        
        # ✅ التعديل تم هنا لتسريع التحميل وتجنب الـ Timeout
        page.goto("https://x.com/login", timeout=60000, wait_until="domcontentloaded")
        
        print("=" * 60)
        print("  سجّل الدخول يدوياً الآن في النافذة المفتوحة (اسم مستخدم + كلمة مرور / رمز تحقق).")
        print("  بعد اكتمال الدخول والوصول للصفحة الرئيسية، ارجع للطرفية واضغط Enter.")
        print("=" * 60)
        input("  اضغط Enter بعد تسجيل الدخول... ")
        for _ in range(30):
            if is_logged_in(page):
                break
            time.sleep(2)
        if not is_logged_in(page):
            print("! لم يتم رصد حالة الدخول. هل أنت متأكد أنك سجّلت الدخول؟")
            ans = input("  اكتب yes للمتابعة وحفظ الجلسة رغم ذلك: ").strip().lower()
            if ans != "yes":
                print("تم الإلغاء.")
                return False
        state_path = os.path.join(SESSIONS_DIR, f"{acc_id}.json")
        context.storage_state(path=state_path)
        print(f"✓ تم حفظ الجلسة للحساب {acc_id} في {state_path}")
        return True
    finally:
        pw.stop()


def close_ctx(pw, context, pw_owned):
    try:
        context.close()
    except Exception:
        pass
    if pw_owned:
        try:
            pw.stop()
        except Exception:
            pass