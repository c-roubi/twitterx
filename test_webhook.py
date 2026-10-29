#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Webhook testing utility - test without IFTTT."""
import requests
import json
import time
import sys

def test_webhook(url, tweet_id="1234567890", text="Test webhook message"):
    """Test webhook endpoint."""
    print(f"\n[*] Testing Webhook")
    print(f"    URL: {url}")
    print(f"    Tweet ID: {tweet_id}")
    print(f"    Text: {text}\n")

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
        print(f"[+] Response: {response.status_code}")
        print(f"    {json.dumps(response.json(), indent=2)}")
        return True
    except requests.exceptions.ConnectionError:
        print(f"[!] Connection error: Cannot reach {url}")
        print(f"    Ensure server is running")
        return False
    except Exception as e:
        print(f"[!] Error: {e}")
        return False

def check_health(url):
    """Check server health."""
    print(f"\n[*] Health Check")
    try:
        response = requests.get(f"{url}/health", timeout=5)
        if response.status_code == 200:
            print(f"[+] Server is running")
            data = response.json()
            print(f"    Accounts: {data.get('has_accounts')}")
            print(f"    Config: {data.get('has_config')}")
            return True
        else:
            print(f"[!] Server responded: {response.status_code}")
            return False
    except Exception as e:
        print(f"[!] Error: {e}")
        return False

def check_status(url):
    """Display server status."""
    print(f"\n[*] Server Status")
    try:
        response = requests.get(f"{url}/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"[+] Accounts: {data.get('accounts_count')}")
            print(f"[+] Webhooks: {len(data.get('webhooks_log', []))}")
            if data.get('webhooks_log'):
                print(f"\n    Last webhook:")
                last = data['webhooks_log'][-1]
                print(f"    - ID: {last.get('tid')}")
                print(f"    - Status: {last.get('status')}")
                print(f"    - Time: {last.get('received_at')}")
            return True
        else:
            print(f"[!] Server responded: {response.status_code}")
            return False
    except Exception as e:
        print(f"[!] Error: {e}")
        return False

if __name__ == '__main__':
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

    print("\n" + "="*70)
    print(" Webhook Server Test")
    print("="*70)

    if not check_health(url):
        print("\n[!] Server is not running!")
        print("\n[*] Start server with:")
        print("    python webhook_server.py")
        sys.exit(1)

    check_status(url)

    time.sleep(1)
    if test_webhook(url):
        print("\n[+] Webhook test successful!")
        print("    Ready to configure IFTTT")
    else:
        print("\n[!] Webhook test failed!")
        sys.exit(1)

    time.sleep(2)
    check_status(url)

    print("\n[+] All systems OK!\n")
