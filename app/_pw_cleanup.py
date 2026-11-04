# -*- coding: utf-8 -*-
"""تنظيف حلقة asyncio العالقة قبل تشغيل Playwright Sync API."""
import asyncio
import sys


def cleanup_asyncio_loop():
    """إزالة أي حلقة asyncio عالقة في الخيط الحالي لمنع خطأ Playwright 'Sync API inside the asyncio loop'."""
    try:
        loop = asyncio._get_running_loop()
        if loop is not None:
            loop.stop()
    except Exception:
        pass
    if sys.version_info >= (3, 12):
        return
    try:
        loop = asyncio._get_running_loop()
        if loop is None:
            asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception:
        pass
