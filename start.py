#!/usr/bin/env python3
"""
TwitterX Bot - Unified Startup Script
Combines webhook server and monitor in single command
"""
import subprocess
import sys
import os
import time
import argparse
from datetime import datetime

def print_banner():
    """Print application banner."""
    print("\n" + "="*70)
    print(" TwitterX Automation Bot - Unified Startup")
    print("="*70 + "\n")

def check_dependencies():
    """Verify all dependencies are installed."""
    print("[*] Checking dependencies...")

    required = ['flask', 'requests', 'playwright', 'google.generativeai', 'pyngrok']
    missing = []

    for module in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)

    if missing:
        print(f"[!] Missing dependencies: {', '.join(missing)}")
        print(f"[*] Run: pip install -r requirements.txt")
        return False

    print("[+] All dependencies OK")
    return True

def check_config():
    """Verify configuration files exist."""
    print("[*] Checking configuration...")

    required = [
        'data/config.json',
        'data/accounts.json'
    ]

    missing = []
    for path in required:
        if not os.path.exists(path):
            missing.append(path)

    if missing:
        print(f"[!] Missing files: {', '.join(missing)}")
        print(f"[*] Create accounts with: python main.py add --handle @account1 --username 'Name'")
        return False

    print("[+] Configuration OK")
    return True

def start_webhook_server(port=8000, use_ngrok=False):
    """Start webhook server in background."""
    print(f"\n[*] Starting Webhook Server (port {port})...")

    cmd = [sys.executable, 'webhook_server.py', '--port', str(port)]
    if use_ngrok:
        cmd.append('--ngrok')

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2)  # Give server time to start

        # Check if process is still running
        if process.poll() is not None:
            stderr = process.stderr.read().decode()
            print(f"[!] Webhook server failed to start: {stderr}")
            return None

        print(f"[+] Webhook Server running (PID: {process.pid})")
        return process
    except Exception as e:
        print(f"[!] Failed to start webhook: {e}")
        return None

def start_monitor():
    """Start monitor in foreground."""
    print(f"\n[*] Starting Monitor...")

    try:
        subprocess.run(
            [sys.executable, 'main.py', 'monitor'],
            check=True
        )
    except KeyboardInterrupt:
        print("\n[*] Monitor stopped")
    except subprocess.CalledProcessError as e:
        print(f"[!] Monitor error: {e}")
        sys.exit(1)

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='TwitterX Bot Startup')
    parser.add_argument('--webhook-only', action='store_true', help='Start only webhook')
    parser.add_argument('--monitor-only', action='store_true', help='Start only monitor')
    parser.add_argument('--port', type=int, default=8000, help='Webhook port')
    parser.add_argument('--ngrok', action='store_true', help='Enable ngrok tunnel')
    args = parser.parse_args()

    print_banner()

    # Check dependencies
    if not check_dependencies():
        sys.exit(1)

    # Check configuration
    if not check_config():
        sys.exit(1)

    webhook_process = None

    try:
        if not args.monitor_only:
            webhook_process = start_webhook_server(args.port, args.ngrok)
            if not webhook_process and not args.webhook_only:
                print("[!] Webhook server failed, continuing with monitor only...")

        if not args.webhook_only:
            start_monitor()

    except KeyboardInterrupt:
        print("\n[*] Shutting down...")
    finally:
        if webhook_process:
            print("[*] Stopping webhook server...")
            webhook_process.terminate()
            webhook_process.wait(timeout=5)

    print("[+] Shutdown complete")

if __name__ == '__main__':
    main()
