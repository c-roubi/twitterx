# -*- coding: utf-8 -*-
"""Browser management: Camoufox (anti-detection) + Playwright fallback."""
import os
import shutil
import time

# Try Camoufox first (stronger anti-detection), fallback to Playwright
try:
    from camoufox.sync_api import Camoufox as sync_playwright
    BROWSER_ENGINE = "camoufox"
except ImportError:
    from playwright.sync_api import sync_playwright
    BROWSER_ENGINE = "playwright"

from . import proxies, selectors
from .config import PROFILES_DIR, SESSIONS_DIR
from .humanizer import fingerprint_for, Human
from .logger import get_logger

log = get_logger("browser")


def _find_chromium_executable():
    """Search for chromium executable: first at the usual Playwright path, then system-wide."""
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
    """Hide automation traces in fallback Chromium pages only.
    Real Chrome (channel=chrome) has correct fingerprinting already, so we don't inject any modification — as any artificial variation (language/cores/platform) increases suspicion instead of reducing it.
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
    # We don't regenerate random fingerprint per session - fingerprint stays constant
    return fp


def _launch_with_fallback(launcher, headless_cfg):
    """Try visible window first; if no display is available, automatically switch to headless mode."""
    try:
        return launcher(headless_cfg)
    except Exception as e:
        if "XServer" in str(e) or "no display" in str(e).lower() or "DISPLAY" in str(e):
            import logging
            logging.getLogger("smartengage.browser").warning(
                "No display available - switching to headless mode"
            )
            return launcher(True)
        raise


def _browser_launchers(cfg):
    """Launch attempt order: real Chrome first (real fingerprint) then bundled Chromium as fallback."""
    order = []
    if cfg.get("browser", {}).get("use_real_chrome", True):
        order.append(("chrome", True))
    order.append(("chromium", False))
    return order


def launch_guest(cfg, pw):
    """Temporary context without login - for monitoring manager tweets (with proxy if enabled)."""
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
                    f"Failed to open guest context ({name}) with proxy - retrying with direct connection"
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
                f"Failed to launch {name} ({last_err}) - falling back to bundled Chromium"
            )
    raise last_err


def launch_account(account, cfg, pw=None, owned_pw=True):
    """Open persistent profile for account (already logged in) with fixed proxy for account.

    Prefers launching real installed Chrome (channel=chrome) for correct browser fingerprint;
    if it fails, automatically falls back to bundled Chromium. If proxy connection fails,
    retries with direct connection to ensure accounts never get stuck.
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
                    f"{acc_id} | Failed to open browser ({name}) with proxy - retrying with direct connection"
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
                f"{acc_id} | Failed to launch {name} ({last_err}) - falling back to bundled Chromium"
            )
    if pw_owned:
        try:
            pw.stop()
        except Exception:
            pass
    raise last_err


def is_logged_in(page):
    """Check login status from current page."""
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
    """Manual login (first time): opens browser, waits for user, then saves session."""
    acc_id = account.get("id", "unknown")
    pw = sync_playwright().start()
    try:
        login_cfg = dict(cfg)
        login_cfg["browser"] = dict(cfg.get("browser", {}))
        login_cfg["browser"]["headless"] = False
        _, context, page, _ = launch_account(account, login_cfg, pw=pw, owned_pw=False)

        # ✅ Modification made here to speed up loading and avoid Timeout
        page.goto("https://x.com/login", timeout=60000, wait_until="domcontentloaded")

        print("=" * 60)
        print("  Log in manually now in the opened window (username + password / verification code).")
        print("  After login completes and you reach the home page, return to terminal and press Enter.")
        print("=" * 60)
        input("  Press Enter after logging in... ")
        for _ in range(30):
            if is_logged_in(page):
                break
            time.sleep(2)
        if not is_logged_in(page):
            print("! Login status not detected. Are you sure you logged in?")
            ans = input("  Type yes to continue and save the session anyway: ").strip().lower()
            if ans != "yes":
                print("Cancelled.")
                return False
        state_path = os.path.join(SESSIONS_DIR, f"{acc_id}.json")
        context.storage_state(path=state_path)
        print(f"✓ Session saved for account {acc_id} at {state_path}")
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