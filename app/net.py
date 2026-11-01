# -*- coding: utf-8 -*-
"""إعدادات TLS المشتركة (تيليجرام + الذكاء الاصطناعي): مخزن النظام أولاً لتفادي أخطاء الشهادات على ويندوز."""
import os
import ssl


def ssl_context(net_cfg=None):
    """سياق TLS: تعطيل صريح > ملف شهادات مخصّص > مخزن النظام (truststore) > الافتراضي.

    net_cfg: قسم telegram من الإعدادات (ca_bundle / insecure_ssl) - يُستخدم لكل اتصالات التطبيق الصادرة.
    """
    net_cfg = net_cfg or {}
    if net_cfg.get("insecure_ssl"):
        return ssl._create_unverified_context()
    ca = (net_cfg.get("ca_bundle") or os.environ.get("SSL_CERT_FILE") or "").strip()
    if ca and os.path.isfile(ca):
        return ssl.create_default_context(cafile=ca)
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()
