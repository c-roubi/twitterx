# TwitterX Bot - Start Guide

## ⚡ One Command - Everything Works!

### Windows
```cmd
monitor.bat
```

### Or Direct Command
```cmd
python main.py monitor
```

### Linux/Mac
```bash
python main.py monitor
```

---

## ✅ What Happens

```
[+] All dependencies OK
[+] Accounts loaded
[+] Monitor started

[*] Checking manager (@Engcryptoo) tweets every 5 minutes...

2026-10-29 04:20:00 | Check #1 - No new tweets
2026-10-29 04:25:00 | Check #2 - FOUND NEW TWEET!
2026-10-29 04:25:05 | [+] Like: 5 accounts ✓
2026-10-29 04:25:10 | [+] Retweet: 4 accounts ✓
2026-10-29 04:25:15 | [+] Comment: 3 accounts ✓
2026-10-29 04:30:00 | Check #3 - No new tweets
```

---

## 📋 First Time Setup

```cmd
python3 setup.py
python main.py add --handle @account1 --username "Name"
python main.py login acc_001
python main.py monitor
```

---

## 🔄 How It Works

✅ **Automatic Every 5 Minutes:**
- Check manager's Twitter account
- Find new tweets
- Engage all accounts (Like + Retweet + Comment)
- Wait 5 minutes
- Repeat

✅ **Features:**
- Parallel engagement (multiple accounts at once)
- Smart timing (spread across 15-42 minutes)
- AI-powered comments (contextual)
- Language detection (Arabic/English)
- Error recovery (auto-retry)

✅ **No External Services Needed:**
- No IFTTT setup
- No ngrok tunnels
- No environment variables
- Just pure polling

---

## 🛑 Stop

Press **Ctrl+C** to stop gracefully.

---

## That's It!

**One command to rule them all:**

```bash
python main.py monitor
```

🚀
