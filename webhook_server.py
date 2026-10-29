#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Webhook Server - استقبال تويتات جديدة من IFTTT و تفاعل فوري."""
import json
import os
import sys
import time
from datetime import datetime
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import load_config, load_accounts, load_seen, save_seen
from app.monitor import execute_immediate_engagement
from app.logger import get_logger

log = get_logger("webhook")

try:
    from flask import Flask, request
except ImportError:
    print("❌ Flask مش مثبت!")
    print("شغّل: pip install flask")
    sys.exit(1)

app = Flask(__name__)

# Global state
cfg = None
accounts = None
webhook_log = []

def load_configs():
    """تحميل الإعدادات."""
    global cfg, accounts
    try:
        cfg = load_config()
        accounts = load_accounts()
        if not accounts:
            log.error("❌ لا حسابات موجودة! استخدم: python main.py add --handle ...")
            return False
        return True
    except Exception as e:
        log.error(f"❌ خطأ في تحميل الإعدادات: {e}")
        return False

@app.route('/webhook/twitter', methods=['POST'])
def twitter_webhook():
    """استقبال تويتة جديدة من IFTTT."""
    try:
        data = request.json
        if not data:
            return {'error': 'empty body'}, 400

        tid = data.get('tweet_id') or data.get('tweet_id_str')
        text = data.get('text', '')

        if not tid:
            log.warning(f"⚠️ Missing tweet_id في الـ webhook payload: {data}")
            return {'error': 'missing tweet_id'}, 400

        # تحويل لـ string إذا كان number
        tid = str(tid)
        text = str(text)[:2000]

        log.info(f"⚡ WEBHOOK تويتة جديدة: {tid}")
        log.info(f"   Text: {text[:100]}...")

        # تسجيل في الـ log
        webhook_log.append({
            'tid': tid,
            'text': text[:200],
            'received_at': datetime.now().isoformat(),
            'status': 'processing'
        })

        # تفاعل فوري (non-blocking في thread منفصل)
        def engage():
            try:
                result = execute_immediate_engagement(tid, text, cfg, accounts)
                webhook_log[-1]['status'] = 'completed'
                log.info(f"✅ تفاعل مكتمل: {len(result.get('accounts', []))} حساب")
            except Exception as e:
                webhook_log[-1]['status'] = f'error: {str(e)[:50]}'
                log.error(f"❌ خطأ في التفاعل: {e}")

        thread = Thread(target=engage, daemon=True)
        thread.start()

        return {
            'status': 'received',
            'tweet_id': tid,
            'message': 'Processing...'
        }, 202

    except Exception as e:
        log.error(f"❌ خطأ في webhook: {e}")
        return {'error': str(e)[:100]}, 500

@app.route('/health', methods=['GET'])
def health():
    """صحة الـ server."""
    return {
        'status': 'running',
        'timestamp': datetime.now().isoformat(),
        'webhooks_received': len(webhook_log),
        'has_accounts': bool(accounts),
        'has_config': bool(cfg)
    }, 200

@app.route('/status', methods=['GET'])
def status():
    """عرض الحالة و آخر الـ webhooks."""
    return {
        'status': 'running',
        'accounts_count': len(accounts) if accounts else 0,
        'webhooks_log': webhook_log[-10:]  # آخر 10
    }, 200

@app.route('/test', methods=['POST'])
def test_webhook():
    """تجربة الـ webhook (بدون IFTTT)."""
    try:
        # بيانات تجريبية
        test_data = {
            'tweet_id': '1234567890',
            'text': '🧪 تجربة webhook'
        }

        # استدعاء الـ webhook
        response = twitter_webhook()
        log.info("✅ اختبار webhook نجح")
        return {'test': 'ok', 'response': response[0]}, 200
    except Exception as e:
        log.error(f"❌ فشل الاختبار: {e}")
        return {'error': str(e)}, 500

@app.errorhandler(404)
def not_found(e):
    return {
        'error': 'not found',
        'available_endpoints': [
            '/webhook/twitter (POST)',
            '/health (GET)',
            '/status (GET)',
            '/test (POST)'
        ]
    }, 404

def print_startup_info(port):
    """طباعة معلومات البدء."""
    print("\n" + "="*60)
    print("🚀 Webhook Server")
    print("="*60)
    print(f"✅ Server شغّال على: http://0.0.0.0:{port}")
    print(f"✅ Accounts: {len(accounts) if accounts else 0}")
    print(f"✅ Config: {'✓' if cfg else '✗'}")
    print("\n📍 Endpoints:")
    print(f"  POST   http://localhost:{port}/webhook/twitter  ← IFTTT يرسل هنا")
    print(f"  GET    http://localhost:{port}/health            ← فحص الحالة")
    print(f"  GET    http://localhost:{port}/status            ← عرض الوضع")
    print(f"  POST   http://localhost:{port}/test              ← تجربة محلية")
    print("\n🔗 للـ external access (من الإنترنت):")
    print(f"  pip install pyngrok")
    print(f"  python -c \"from pyngrok import ngrok; print(ngrok.connect(8000))\"")
    print("\n⏳ استنى على IFTTT notifications...")
    print("="*60 + "\n")

if __name__ == '__main__':
    port = 8000

    print("\n📦 جاري التحميل...")

    # تحميل الإعدادات
    if not load_configs():
        print("❌ فشل تحميل الإعدادات!")
        sys.exit(1)

    # طباعة معلومات البدء
    print_startup_info(port)

    # شغّل الـ server
    try:
        app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"\n❌ Port {port} مستخدم بالفعل!")
            print(f"\nالحلول:")
            print(f"1️⃣  استخدم port مختلف:")
            print(f"    python webhook_server.py 8001")
            print(f"\n2️⃣  اغلق البرنامج الآخر:")
            print(f"    lsof -i :{port}")
            print(f"    kill -9 <PID>")
        else:
            print(f"\n❌ خطأ: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⏹ Server توقف")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ خطأ غير متوقع: {e}")
        sys.exit(1)
