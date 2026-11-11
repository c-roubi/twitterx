# -*- coding: utf-8 -*-
"""إغلاق نوافذ X المزعجة تلقائياً (Unlock more on X، عروض Premium، الإشعارات، الكوكيز...).

حارس JavaScript يُحقَن في كل صفحة x.com عبر add_init_script ويعمل باستمرار، فلا نحتاج استدعاءً
في كل موضع. يتجاهل بذكاء: نوافذ الكتابة والتعليق وتأكيد الريتويت، وشاشات تسجيل الدخول (حتى لا نقطع الدخول اليدوي).
"""
from .logger import get_logger

log = get_logger("overlays")

GUARD_JS = r"""
(() => {
  const host = location.hostname;
  if (window.top !== window) return;
  if (!(/(^|\.)(x|twitter)\.com$/.test(host) || window.__seForce)) return;
  if (window.__seGuard) return;
  window.__seGuard = true;

  // نصوص أزرار «تخطّي/إغلاق» فقط. لا نضغط أزرار تؤكّد إجراءً (حذف، موافق...).
  const DISMISS = /^(got it|not now|maybe later|skip for now|skip|no thanks|dismiss|later|close|حسن[اًا]?|حسنًا|حسناً|فهمت|ليس الآن|ليس الان|لاحقًا|لاحقاً|ربما لاحقًا|ربما لاحقاً|تخطي|تخطّي|إغلاق|لا شكرا|لا، شكرًا|لا، شكراً)$/i;
  const KEEP = [
    '[data-testid^="tweetTextarea"]', '[data-testid="tweetButton"]', '[data-testid="tweetButtonInline"]',
    '[data-testid="confirmationSheetConfirm"]', '[data-testid="retweetConfirm"]', '[data-testid="unretweetConfirm"]',
    '[data-testid="LoginForm"]', 'input[name="text"]', 'input[name="password"]', 'input[autocomplete="username"]',
  ].join(',');
  const isLoginFlow = () => /^\/(login|i\/flow\/(login|signup))/.test(location.pathname);
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = el => ((el.innerText || el.getAttribute('aria-label') || '').trim());

  function tick() {
    let n = 0;
    if (isLoginFlow()) return 0;
    for (const d of document.querySelectorAll('[role="dialog"],[aria-modal="true"],[data-testid="sheetDialog"]')) {
      if (!visible(d) || d.querySelector(KEEP)) continue;
      const btns = [...d.querySelectorAll('button,[role="button"]')].filter(visible);
      const hit = btns.find(b => DISMISS.test(label(b)));
      if (hit) { hit.click(); n++; continue; }
      const close = d.querySelector('[data-testid="app-bar-close"],[aria-label="Close"],[aria-label="إغلاق"]');
      if (close && visible(close)) { close.click(); n++; }
    }
    // لافتات الكوكيز (خارج الحوارات)
    for (const b of document.querySelectorAll('[role="button"],button')) {
      if (/^(refuse non-essential cookies|رفض ملفات تعريف الارتباط غير الضرورية)$/i.test(label(b)) && visible(b)) { b.click(); n++; }
    }
    return n;
  }
  window.__seClear = tick;

  const css = document.createElement('style');
  css.textContent = [
    '[data-testid="sidebarColumn"] aside[role="complementary"]',   // بطاقة «اشترك في Premium» بالعمود الجانبي
    'aside[aria-label="Subscribe to Premium"]',
    'a[href="/i/premium_sign_up"]', 'a[href="/i/verified-orgs-signup"]',
    '[data-testid="premium-upsell"]', '[data-testid="BottomBar"]',
  ].join(',') + '{display:none !important}';
  const mount = () => { (document.head || document.documentElement).appendChild(css); setInterval(tick, 800); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
"""


def install(context):
    """حقن الحارس في كل صفحات السياق (يعمل مع كروم الحقيقي وChromium)."""
    try:
        context.add_init_script(GUARD_JS)
    except Exception as e:
        log.warning("تعذّر تفعيل حارس النوافذ: {}".format(e))


def clear(page):
    """إغلاق أي نافذة مزعجة ظاهرة الآن (آمن: لا يرمي استثناء). يرجع عدد ما أُغلق."""
    try:
        return int(page.evaluate("() => (window.__seClear ? window.__seClear() : 0)") or 0)
    except Exception:
        return 0
