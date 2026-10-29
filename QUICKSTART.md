# TwitterX Bot - Quick Start Guide

## ⚡ One-Command Startup

Everything is ready to go! Just run:

```bash
python run.py
```

That's it! No environment variables, no setup needed. The ngrok token is embedded.

---

## 📋 Complete Setup (First Time Only)

### Step 1: Install Dependencies
```bash
python3 setup.py
```

### Step 2: Add Accounts
```bash
python main.py add --handle @account1 --username "Name"
python main.py add --handle @account2 --username "Name"
```

### Step 3: Login to Accounts
```bash
python main.py login acc_001
python main.py login acc_002
```

### Step 4: Run Bot
```bash
python run.py
```

---

## 🪟 Windows Quick Start

### Option 1: Batch Script (Easiest)
```cmd
start-bot.bat
```

### Option 2: PowerShell
```powershell
.\start-bot.ps1
```

### Option 3: Direct Command
```cmd
python run.py
```

---

## 🚀 What Happens on Startup

```
[+] Dependencies OK
[+] 1 accounts loaded

[*] Setting up ngrok...
[+] ngrok authenticated successfully
[*] Starting ngrok tunnel on port 8000...
[+] ngrok tunnel: https://abc123.ngrok.io
[+] Webhook URL: https://abc123.ngrok.io/webhook/twitter

[*] Starting Webhook Server (port 8000)...
[+] Webhook Server running (PID: 1234)

[+] IFTTT Webhook URL:
    https://abc123.ngrok.io/webhook/twitter

[*] Configure IFTTT with:
    URL: https://abc123.ngrok.io/webhook/twitter
    Body: {"tweet_url": "{{LinkToTweet}}", "text": "{{Text}}"}

[*] Starting Monitor...
[*] Press Ctrl+C to stop
```

---

## 🔧 Configure IFTTT (One-Time)

1. Go to https://ifttt.com/applets
2. Create/Edit applet
3. **IF:** Twitter → New tweet by @Engcryptoo
4. **THEN:** Webhooks → Make a web request

**Settings:**
```
URL: https://abc123.ngrok.io/webhook/twitter
Method: POST
Content Type: application/json

Body:
{
  "tweet_url": "{{LinkToTweet}}",
  "text": "{{Text}}"
}
```

---

## 📊 System Architecture

```
Manager tweets on Twitter
    ↓ (instant)
IFTTT detects
    ↓ (< 1 sec)
ngrok tunnel (https://abc123.ngrok.io)
    ↓
Webhook Server (http://0.0.0.0:8000)
    ↓
Monitor processes
    ↓
All accounts engage ⚡
    ↓
If no webhook: Polling every 60-120 min (fallback)
```

---

## ⚙️ Advanced Options

### Run Without ngrok (Local Only)
```bash
python run.py --no-ngrok
```

### Custom Port
```bash
python run.py --port 8001
```

### Custom ngrok Token
```bash
python run.py --ngrok-token YOUR_TOKEN_HERE
```

---

## 🐛 Troubleshooting

### Port 8000 already in use
```bash
python run.py --port 8001
```

### No accounts found
```bash
python main.py add --handle @account1 --username "Name"
python main.py login acc_001
```

### Dependencies missing
```bash
pip install -r requirements.txt
python -m playwright install chromium
```

---

## 🛑 Stop the Bot

Press **Ctrl+C** to stop gracefully. All processes will clean up automatically.

---

## 📝 What Gets Created

When you run the bot, it creates:

- `data/accounts.json` - Your account configurations
- `data/seen.json` - Tracked tweets and engagement
- `data/config.json` - Bot settings
- `logs/` - Activity logs
- `sessions/` - Browser sessions (auto-cleaned)

---

## ✨ That's It!

**One command:**
```bash
python run.py
```

Your bot is now:
- ✅ Running webhook server on port 8000
- ✅ Connected to ngrok tunnel
- ✅ Ready for IFTTT webhooks
- ✅ Monitoring for manager tweets
- ✅ Auto-engaging all accounts

**Happy botting!** 🚀
