#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""اختبار الـ webhook بدون الحاجة لـ IFTTT."""
import requests
import json
import time
import sys

def test_webhook(url, tweet_id="1234567890", text="🧪 تغريدة تجريبية"):
    """اختبار الـ webhook."""
    print(f"\n🧪 اختبار الـ Webhook")
    print(f"URL: {url}")
    print(f"Tweet ID: {tweet_id}")
    print(f"Text: {text}\n")

    data = {
        'tweet_id': tweet_id,
        'text': text
    }

    try:
        response = requests.post(
            f"{url}/webhook/twitter",
            json=data,
            timeout=10
        )
        print(f"✅ Response: {response.status_code}")
        print(f"   {json.dumps(response.json(), indent=2)}")
        return True
    except requests.exceptions.ConnectionError:
        print(f"❌ خطأ: لا يمكن الاتصال بـ {url}")
        print(f"   تأكد أن الـ server شغّال")
        return False
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False

def check_health(url):
    """فحص صحة الـ server."""
    print(f"\n🏥 فحص صحة الـ Server")
    try:
        response = requests.get(f"{url}/health", timeout=5)
        if response.status_code == 200:
            print(f"✅ Server شغّال!")
            data = response.json()
            print(f"   Accounts: {data.get('has_accounts')}")
            print(f"   Config: {data.get('has_config')}")
            return True
        else:
            print(f"❌ Server رجع: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False

def check_status(url):
    """عرض الحالة."""
    print(f"\n📊 حالة الـ Server")
    try:
        response = requests.get(f"{url}/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Accounts: {data.get('accounts_count')}")
            print(f"✅ Webhooks: {len(data.get('webhooks_log', []))}")
            if data.get('webhooks_log'):
                print(f"\n   آخر webhook:")
                last = data['webhooks_log'][-1]
                print(f"   - ID: {last.get('tid')}")
                print(f"   - Status: {last.get('status')}")
                print(f"   - Time: {last.get('received_at')}")
            return True
        else:
            print(f"❌ Server رجع: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False

if __name__ == '__main__':
    # الـ URL (قدّمه كـ argument أو استخدم default)
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

    print("\n" + "="*60)
    print("🧪 Webhook Testing")
    print("="*60)

    # فحص الصحة
    if not check_health(url):
        print("\n❌ Server مش شغّال!")
        print("\nالحل:")
        print("1. شغّل: python webhook_server.py")
        print("2. بعدين شغّل: python test_webhook.py")
        sys.exit(1)

    # عرض الحالة
    check_status(url)

    # اختبار الـ webhook
    time.sleep(1)
    if test_webhook(url):
        print("\n✅ اختبار الـ webhook نجح!")
        print("   يمكنك الآن إعداد IFTTT")
    else:
        print("\n❌ اختبار الـ webhook فشل!")
        sys.exit(1)

    # عرض الحالة مرة أخرى
    time.sleep(2)
    check_status(url)

    print("\n✅ كل شي تمام!\n")
