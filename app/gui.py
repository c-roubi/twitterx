# -*- coding: utf-8 -*-
"""تشغيل واجهة التطبيق: نافذة سطح مكتب (pywebview/WebView2 على ويندوز 11) أو المتصفح كحل بديل."""
import threading
import webbrowser

from .logger import get_logger
from .server import create_server

log = get_logger("gui")

DEFAULT_PORT = 8765


def run(port=DEFAULT_PORT, no_window=False):
    try:
        httpd, svc, _ = create_server(port)
    except OSError:
        httpd, svc, _ = create_server(0)  # المنفذ مشغول: أي منفذ حر
    url = "http://127.0.0.1:{}/".format(httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    svc.start()
    print("SmartEngage ->", url)
    try:
        if no_window:
            raise ImportError("no-window")
        import webview  # pywebview (اختياري)
        webview.create_window("SmartEngage", url, width=1360, height=860, min_size=(1000, 680))
        webview.start()
    except ImportError:
        if not no_window:
            log.info("pywebview غير مثبّت - فتح الواجهة في المتصفح")
            webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    finally:
        svc.shutdown()
        httpd.shutdown()
