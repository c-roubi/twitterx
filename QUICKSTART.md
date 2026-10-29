# TwitterX Bot - Quick Start

## Setup (One-Time)

```bash
python3 setup.py
```

## Configure ngrok Token

### Windows (Batch)
```cmd
set NGROK_AUTHTOKEN=3LMjg3cmh8s21sXWwfBpd0lAzLE_5MXqg1tnaX32waZktDJK8
```

### Windows (PowerShell)
```powershell
$env:NGROK_AUTHTOKEN = "3LMjg3cmh8s21sXWwfBpd0lAzLE_5MXqg1tnaX32waZktDJK8"
```

### Linux/Mac
```bash
export NGROK_AUTHTOKEN=3LMjg3cmh8s21sXWwfBpd0lAzLE_5MXqg1tnaX32waZktDJK8
```

## Add Accounts

```bash
python main.py add --handle @account1 --username "Name"
python main.py login acc_001
```

## Run Bot (Single Command!)

### Option 1: With ngrok Tunnel
```bash
python run.py
```

**Output:**
```
[+] ngrok tunnel: https://undocked-nutty-prone.ngrok-free.dev
[+] Webhook URL: https://undocked-nutty-prone.ngrok-free.dev/webhook/twitter
[+] Use this URL in IFTTT!

[*] Configure IFTTT with:
    URL: https://undocked-nutty-prone.ngrok-free.dev/webhook/twitter
    Body: {"tweet_url": "{{LinkToTweet}}", "text": "{{Text}}"}
```

### Option 2: Local Only (No External Access)
```bash
python run.py --no-ngrok
```

---

## Configure IFTTT (One-Time)

1. Go to https://ifttt.com/applets
2. Create/Edit applet for @Engcryptoo
3. Set Webhook URL: `https://your-ngrok-url/webhook/twitter`
4. Set Body:
```json
{
  "tweet_url": "{{LinkToTweet}}",
  "text": "{{Text}}"
}
```

---

## System Architecture

```
Manager tweets on Twitter
    ↓ (instant)
IFTTT detects
    ↓ (< 1 sec)
ngrok tunnel
    ↓
Webhook Server (http://0.0.0.0:8000)
    ↓
All accounts engage ⚡
    ↓
If no webhook: Polling every 60-120 min
```

---

## What Happens When You Run?

✅ ngrok tunnel auto-starts
✅ Webhook server starts (background)
✅ Monitor loop starts (foreground)
✅ Displays webhook URL automatically
✅ Ready for IFTTT

---

## Stop Bot

Press `Ctrl+C` to stop everything gracefully.

---

## Troubleshooting

### ngrok error: authentication failed
```bash
set NGROK_AUTHTOKEN=your_token_here
python run.py
```

### Port 8000 already in use
```bash
python run.py --port 8001
```

### No accounts found
```bash
python main.py add --handle @account1 --username "Name"
python main.py login acc_001
```

---

## That's it! 🚀

**One command to rule them all:**
```bash
python run.py
```
