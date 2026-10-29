#!/usr/bin/env python3
"""
TwitterX Bot Setup - Auto-install dependencies and initialize system
"""
import subprocess
import sys
import os

def run_command(cmd, description):
    """Execute shell command with status reporting."""
    print(f"\n[*] {description}...")
    try:
        result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        print(f"[+] {description} OK")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[-] {description} FAILED")
        print(f"    Error: {e.stderr}")
        return False

def main():
    print("\n" + "="*70)
    print(" TwitterX Bot - Setup & Configuration")
    print("="*70 + "\n")

    # Check Python version
    if sys.version_info < (3, 8):
        print("[-] Python 3.8+ required")
        sys.exit(1)

    print(f"[+] Python version: {sys.version}")

    # Install dependencies
    if not run_command(
        f"{sys.executable} -m pip install -r requirements.txt",
        "Installing dependencies"
    ):
        sys.exit(1)

    # Install Playwright browsers
    if not run_command(
        f"{sys.executable} -m playwright install chromium",
        "Installing Playwright browsers (this may take a minute)"
    ):
        sys.exit(1)

    # Check data directory
    os.makedirs("data", exist_ok=True)
    print("[+] Data directory initialized")

    # Success
    print("\n" + "="*70)
    print(" ✓ Setup Complete!")
    print("="*70 + "\n")
    print("Next steps:")
    print("  1. Set up accounts:")
    print("     python main.py add --handle @account1 --username 'Name'")
    print("  2. Login to accounts:")
    print("     python main.py login acc_001")
    print("  3. Start monitoring:")
    print("     python main.py monitor")
    print()

if __name__ == "__main__":
    main()
