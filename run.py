#!/usr/bin/env python3
"""
TwitterX Bot - Unified Startup with ngrok Auto-Setup
Single command to run everything: webhook + monitor + ngrok
"""
import subprocess
import sys
import os
import time
import threading
from datetime import datetime

def print_header():
    """Print startup header."""
    print("\n" + "="*70)
    print(" TwitterX Bot - Complete Startup")
    print("="*70 + "\n")

def setup_ngrok_token():
    """Setup ngrok authentication token."""
    token = os.getenv('NGROK_AUTHTOKEN')
    if not token:
        print("[!] NGROK_AUTHTOKEN not set!")
        print("[*] Please set environment variable:")
        print("    Windows: set NGROK_AUTHTOKEN=your_token_here")
        print("    Linux/Mac: export NGROK_AUTHTOKEN=your_token_here")
        print("\n[*] Or provide token as argument:")
        print("    python run.py --ngrok-token YOUR_TOKEN")
        return False

    try:
        from pyngrok import ngrok
        ngrok.set_auth_token(token)
        print("[+] ngrok authenticated")
        return True
    except Exception as e:
        print(f"[!] ngrok setup failed: {e}")
        return False

def start_ngrok_tunnel(port=8000):
    """Start ngrok tunnel."""
    try:
        from pyngrok import ngrok
        print(f"[*] Starting ngrok tunnel on port {port}...")
        tunnel = ngrok.connect(port)
        url = str(tunnel).split(" -> ")[0].strip()
        webhook_url = f"{url}/webhook/twitter"
        print(f"[+] ngrok tunnel: {url}")
        print(f"[+] Webhook URL: {webhook_url}")
        print(f"[+] Use this URL in IFTTT!")
        return tunnel, webhook_url
    except Exception as e:
        print(f"[!] ngrok tunnel failed: {e}")
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

def start_webhook_server(port=8000):
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
            print(f"[!] Webhook server failed: {stderr}")
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
    parser.add_argument('--ngrok-token', help='ngrok authentication token')
    parser.add_argument('--port', type=int, default=8000, help='Webhook port')
    parser.add_argument('--no-ngrok', action='store_true', help='Disable ngrok tunnel')
    args = parser.parse_args()

    print_header()

    # Set ngrok token if provided
    if args.ngrok_token:
        os.environ['NGROK_AUTHTOKEN'] = args.ngrok_token

    # Check accounts
    if not check_accounts():
        sys.exit(1)

    # Setup ngrok (optional)
    tunnel = None
    webhook_url = None
    if not args.no_ngrok:
        if setup_ngrok_token():
            tunnel, webhook_url = start_ngrok_tunnel(args.port)
        else:
            print("[!] ngrok setup skipped")

    # Start webhook server
    webhook_process = None
    webhook_process = start_webhook_server(args.port)
    if not webhook_process:
        print("[!] Webhook server failed to start")
        sys.exit(1)

    # Display webhook info
    print("\n" + "="*70)
    print(" System Ready!")
    print("="*70)
    if webhook_url:
        print(f"\n[+] IFTTT Webhook URL:")
        print(f"    {webhook_url}")
        print(f"\n[*] Configure IFTTT with:")
        print(f"    URL: {webhook_url}")
        print(f"    Body: {\"tweet_url\": \"{{LinkToTweet}}\", \"text\": \"{{Text}}\"}")
    else:
        print(f"\n[+] Local Webhook URL:")
        print(f"    http://localhost:{args.port}/webhook/twitter")

    print(f"\n[*] Monitor will check for tweets every 60-120 minutes (fallback)")
    print("="*70 + "\n")

    # Start monitor (blocking)
    try:
        start_monitor()
    finally:
        # Cleanup
        if webhook_process:
            print("[*] Stopping webhook server...")
            webhook_process.terminate()
            try:
                webhook_process.wait(timeout=5)
            except:
                webhook_process.kill()

        if tunnel:
            from pyngrok import ngrok
            try:
                ngrok.disconnect(tunnel)
            except:
                pass

    print("[+] Shutdown complete")

if __name__ == '__main__':
    main()
