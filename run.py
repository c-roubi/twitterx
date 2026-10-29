#!/usr/bin/env python3
"""
TwitterX Bot - Unified Startup with ngrok Auto-Setup
Single command to run everything: webhook + monitor + ngrok
"""
import subprocess
import sys
import os
import time
import shutil
from datetime import datetime

# ========== CONFIGURATION ==========
NGROK_TOKEN = "3LMjg3cmh8s21sXWwfBpd0lAzLE_5MXqg1tnaX32waZktDJK8"
DEFAULT_PORT = 8000
# ===================================

def print_header():
    """Print startup header."""
    print("\n" + "="*70)
    print(" TwitterX Bot - Complete Startup")
    print("="*70 + "\n")

def cleanup_ngrok_config():
    """Clean up old ngrok config to avoid path issues."""
    try:
        old_paths = [
            os.path.expanduser('~/.ngrok2'),
            os.path.expanduser('~/.config/ngrok')
        ]
        for path in old_paths:
            if os.path.exists(path):
                shutil.rmtree(path)
                print(f"[+] Cleaned up old ngrok config: {path}")
    except Exception as e:
        print(f"[*] Could not clean ngrok config: {e}")

def setup_ngrok_token(token=None):
    """Setup ngrok authentication token."""
    auth_token = token or os.getenv('NGROK_AUTHTOKEN') or NGROK_TOKEN

    if not auth_token:
        print("[!] No ngrok token available!")
        return False

    try:
        # Clean up old config first
        cleanup_ngrok_config()

        from pyngrok import ngrok
        ngrok.set_auth_token(auth_token)
        print("[+] ngrok authenticated successfully")
        return True
    except Exception as e:
        print(f"[!] ngrok setup failed: {str(e)[:100]}")
        return False

def start_ngrok_tunnel(port=DEFAULT_PORT):
    """Start ngrok tunnel."""
    try:
        from pyngrok import ngrok
        print(f"[*] Starting ngrok tunnel on port {port}...")

        tunnel = ngrok.connect(port)
        url = str(tunnel).split(" -> ")[0].strip()
        webhook_url = f"{url}/webhook/twitter"

        print(f"[+] ngrok tunnel: {url}")
        print(f"[+] Webhook URL: {webhook_url}")

        return tunnel, webhook_url
    except Exception as e:
        print(f"[!] ngrok tunnel failed: {str(e)[:100]}")
        return None, None

def check_accounts():
    """Check if accounts exist."""
    try:
        from app.config import load_accounts
        accounts = load_accounts()
        if not accounts:
            print("[!] No accounts found!")
            print("[*] Add accounts with: python main.py add --handle @account1 --username 'Name'")
            return False
        print(f"[+] {len(accounts)} accounts loaded")
        return True
    except Exception as e:
        print(f"[!] Account check failed: {e}")
        return False

def check_dependencies():
    """Check if all dependencies are available."""
    required = ['flask', 'pyngrok', 'playwright']
    missing = []

    for module in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)

    if missing:
        print(f"[!] Missing dependencies: {', '.join(missing)}")
        print(f"[*] Install with: pip install -r requirements.txt")
        return False

    print("[+] All dependencies OK")
    return True

def start_webhook_server(port=DEFAULT_PORT):
    """Start webhook server in background."""
    print(f"\n[*] Starting Webhook Server (port {port})...")
    cmd = [sys.executable, 'webhook_server.py', '--port', str(port)]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2)

        if process.poll() is not None:
            stderr = process.stderr.read().decode()
            print(f"[!] Webhook server failed: {stderr[:200]}")
            return None

        print(f"[+] Webhook Server running (PID: {process.pid})")
        return process
    except Exception as e:
        print(f"[!] Failed to start webhook: {e}")
        return None

def start_monitor():
    """Start monitor in foreground."""
    print(f"\n[*] Starting Monitor...")
    print("[*] Press Ctrl+C to stop\n")

    try:
        subprocess.run([sys.executable, 'main.py', 'monitor'], check=True)
    except KeyboardInterrupt:
        print("\n[*] Shutting down...")
    except subprocess.CalledProcessError as e:
        print(f"[!] Monitor error: {e}")
        sys.exit(1)

def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='TwitterX Bot - Complete Startup')
    parser.add_argument('--ngrok-token', help='ngrok authentication token (overrides default)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='Webhook port')
    parser.add_argument('--no-ngrok', action='store_true', help='Disable ngrok tunnel')
    args = parser.parse_args()

    print_header()

    # Check dependencies
    if not check_dependencies():
        sys.exit(1)

    # Check accounts
    if not check_accounts():
        sys.exit(1)

    # Setup ngrok (optional)
    tunnel = None
    webhook_url = None
    if not args.no_ngrok:
        print("\n[*] Setting up ngrok...")
        if setup_ngrok_token(args.ngrok_token):
            tunnel, webhook_url = start_ngrok_tunnel(args.port)
        else:
            print("[!] ngrok setup failed, continuing with local-only mode")

    # Start webhook server
    webhook_process = None
    webhook_process = start_webhook_server(args.port)
    if not webhook_process:
        print("[!] Webhook server failed to start")
        sys.exit(1)

    # Display system info
    print("\n" + "="*70)
    print(" System Ready!")
    print("="*70)

    if webhook_url:
        print(f"\n[+] IFTTT Webhook URL:")
        print(f"    {webhook_url}")
        print(f"\n[*] Configure IFTTT with:")
        print(f"    URL: {webhook_url}")
        body = '{"tweet_url": "{{LinkToTweet}}", "text": "{{Text}}"}'
        print(f"    Body: {body}")
    else:
        print(f"\n[+] Local Webhook URL:")
        print(f"    http://localhost:{args.port}/webhook/twitter")
        print(f"\n[!] For external access, configure ngrok token and run: python run.py")

    print(f"\n[*] Fallback polling: every 60-120 minutes")
    print("="*70 + "\n")

    # Start monitor (blocking)
    try:
        start_monitor()
    finally:
        # Cleanup
        print("\n[*] Cleaning up...")

        if webhook_process:
            try:
                webhook_process.terminate()
                webhook_process.wait(timeout=5)
            except:
                try:
                    webhook_process.kill()
                except:
                    pass

        if tunnel:
            try:
                from pyngrok import ngrok
                ngrok.disconnect(tunnel)
            except:
                pass

    print("[+] Shutdown complete")

if __name__ == '__main__':
    main()
