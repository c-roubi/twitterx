#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Webhook Server - Receive tweets from IFTTT and trigger immediate engagement."""
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
    print("[!] ERROR: Flask not installed")
    print("[*] Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from pyngrok import ngrok
    NGROK_AVAILABLE = True
except ImportError:
    NGROK_AVAILABLE = False

app = Flask(__name__)

# Global state
cfg = None
accounts = None
webhook_log = []

def load_configs():
    """Load configuration and accounts."""
    global cfg, accounts
    try:
        cfg = load_config()
        accounts = load_accounts()
        if not accounts:
            log.error("[!] No accounts found. Run: python main.py add --handle ...")
            return False
        return True
    except Exception as e:
        log.error(f"[!] Config load error: {e}")
        return False

def extract_tweet_id(url):
    """Extract tweet ID from Twitter URL."""
    if not url:
        return None
    # Format: https://x.com/user/status/1234567890 or https://twitter.com/user/status/1234567890
    try:
        parts = url.split('/status/')
        if len(parts) == 2:
            return parts[1].split('?')[0]
    except:
        pass
    return None

@app.route('/webhook/twitter', methods=['POST'])
def twitter_webhook():
    """Receive new tweet from IFTTT."""
    try:
        data = request.json
        if not data:
            return {'error': 'empty body'}, 400

        # Support both tweet_id and tweet_url
        tid = data.get('tweet_id') or data.get('tweet_id_str')
        tweet_url = data.get('tweet_url') or data.get('url')

        if not tid and tweet_url:
            tid = extract_tweet_id(tweet_url)

        text = data.get('text', '')

        if not tid:
            log.warning(f"[!] Missing tweet_id/url in webhook payload: {data}")
            return {'error': 'missing tweet_id or tweet_url'}, 400

        tid = str(tid)
        text = str(text)[:2000]

        log.info(f"[+] WEBHOOK: New tweet {tid}")
        log.info(f"    Text: {text[:100]}...")

        webhook_log.append({
            'tid': tid,
            'text': text[:200],
            'received_at': datetime.now().isoformat(),
            'status': 'processing'
        })

        def engage():
            try:
                result = execute_immediate_engagement(tid, text, cfg, accounts)
                webhook_log[-1]['status'] = 'completed'
                log.info(f"[+] Engagement complete: {len(result.get('accounts', []))} accounts")
            except Exception as e:
                webhook_log[-1]['status'] = f'error: {str(e)[:50]}'
                log.error(f"[!] Engagement error: {e}")

        thread = Thread(target=engage, daemon=True)
        thread.start()

        return {
            'status': 'received',
            'tweet_id': tid,
            'message': 'Processing...'
        }, 202

    except Exception as e:
        log.error(f"[!] Webhook error: {e}")
        return {'error': str(e)[:100]}, 500

@app.route('/health', methods=['GET'])
def health():
    """Server health check."""
    return {
        'status': 'running',
        'timestamp': datetime.now().isoformat(),
        'webhooks_received': len(webhook_log),
        'has_accounts': bool(accounts),
        'has_config': bool(cfg)
    }, 200

@app.route('/status', methods=['GET'])
def status():
    """Display server status and recent webhooks."""
    return {
        'status': 'running',
        'accounts_count': len(accounts) if accounts else 0,
        'webhooks_log': webhook_log[-10:]
    }, 200

@app.route('/test', methods=['POST'])
def test_webhook():
    """Test webhook without IFTTT."""
    try:
        test_data = {
            'tweet_id': '1234567890',
            'text': 'Test webhook message'
        }

        response = twitter_webhook()
        log.info("[+] Webhook test successful")
        return {'test': 'ok', 'response': response[0]}, 200
    except Exception as e:
        log.error(f"[!] Test failed: {e}")
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

def setup_ngrok(port):
    """Auto-setup ngrok tunnel for external access."""
    if not NGROK_AVAILABLE:
        return None

    try:
        print("[*] Setting up ngrok tunnel...")
        tunnel = ngrok.connect(port)
        ngrok_url = str(tunnel).replace("http://", "https://")
        print(f"[+] ngrok tunnel: {tunnel}")
        print(f"[+] IFTTT Webhook URL: {ngrok_url}/webhook/twitter")
        return tunnel
    except Exception as e:
        print(f"[!] ngrok setup failed: {e}")
        return None

def print_startup_info(port, ngrok_url=None):
    """Print startup information."""
    print("\n" + "="*70)
    print(" TwitterX Webhook Server")
    print("="*70)
    print(f"[+] Server running: http://0.0.0.0:{port}")
    print(f"[+] Accounts: {len(accounts) if accounts else 0}")
    print(f"[+] Config: {'OK' if cfg else 'ERROR'}")
    print("\n[*] Endpoints:")
    print(f"    POST   http://localhost:{port}/webhook/twitter")
    print(f"    GET    http://localhost:{port}/health")
    print(f"    GET    http://localhost:{port}/status")
    print(f"    POST   http://localhost:{port}/test")

    if ngrok_url:
        print(f"\n[+] External Access (ngrok):")
        print(f"    {ngrok_url}/webhook/twitter")
    else:
        print(f"\n[*] For external access (ngrok):")
        print(f"    pip install pyngrok")
        print(f"    python webhook_server.py --ngrok")

    print(f"\n[*] Waiting for IFTTT notifications...")
    print("="*70 + "\n")

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='TwitterX Webhook Server')
    parser.add_argument('--port', type=int, default=8000, help='Port to run on')
    parser.add_argument('--ngrok', action='store_true', help='Enable ngrok tunnel')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    args = parser.parse_args()

    port = args.port
    print(f"\n[*] Initializing...")

    if not load_configs():
        print("[!] Config load failed!")
        sys.exit(1)

    ngrok_url = None
    if args.ngrok:
        ngrok_url = setup_ngrok(port)

    print_startup_info(port, ngrok_url)

    try:
        app.run(
            host=args.host,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"\n[!] Port {port} is already in use!")
            print(f"\n[*] Solutions:")
            print(f"    1. Use different port:")
            print(f"       python webhook_server.py --port 8001")
            print(f"\n    2. Kill existing process:")
            print(f"       lsof -i :{port}")
            print(f"       kill -9 <PID>")
        else:
            print(f"\n[!] Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[*] Server stopped")
        sys.exit(0)
    except Exception as e:
        print(f"\n[!] Unexpected error: {e}")
        sys.exit(1)
