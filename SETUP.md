# TwitterX Bot - Complete Setup Guide

## Step 1: Install Dependencies

### Linux/Mac
```bash
cd ~/twitterx
python3 setup.py
```

### Windows
**Option A: Batch Script (recommended)**
```cmd
cd C:\Users\YourUsername\twitterx
setup.bat
```

**Option B: PowerShell**
```powershell
cd C:\Users\YourUsername\twitterx
powershell -ExecutionPolicy Bypass -File setup.ps1
```

**Option C: Manual**
```cmd
pip install -r requirements.txt
python -m playwright install chromium
```

All methods will:
- ✓ Install all Python dependencies
- ✓ Download Playwright browsers
- ✓ Initialize data directories

## Step 2: Add Accounts

```bash
python main.py add --handle @account1 --username "Name"
python main.py add --handle @account2 --username "Name"
```

## Step 3: Login to Accounts

```bash
python main.py login acc_001
python main.py login acc_002
```

## Step 4: Start Webhook Server

### Option A: Local Only
```bash
python webhook_server.py
```

### Option B: With External Access (ngrok)
```bash
python webhook_server.py --ngrok
```

This will output your IFTTT webhook URL automatically.

## Step 5: Configure IFTTT

1. Go to https://ifttt.com
2. Create new Applet:
   - **IF:** Twitter → New tweet by @Engcryptoo
   - **THEN:** Webhooks → Make a web request

3. **Webhook Settings:**
   ```
   URL: http://localhost:8000/webhook/twitter
   (or use ngrok URL from Step 4)
   
   Method: POST
   Content Type: application/json
   
   Body:
   {
     "tweet_id": "{{TweetId}}",
     "text": "{{Text}}"
   }
   ```

4. Create Applet

## Step 6: Start Monitoring

```bash
python main.py monitor
```

This will:
- ✓ Start webhook server in background
- ✓ Check for manager tweets (fallback polling)
- ✓ Auto-engage using all accounts

## Testing

```bash
# Test webhook locally
python test_webhook.py

# Or specify custom URL
python test_webhook.py http://your-ngrok-url:8000
```

## Common Issues

### Port Already in Use
```bash
python webhook_server.py --port 8001
```

### Can't Connect to Server
- Ensure `python webhook_server.py` is running
- Check firewall/antivirus settings
- For ngrok: ensure ngrok is installed (`pip install pyngrok`)

### IFTTT Not Triggering
- Verify Twitter is connected in IFTTT settings
- Test webhook manually: `python test_webhook.py`
- Check webhook URL in IFTTT applet

## Command Reference

| Command | Purpose |
|---------|---------|
| `python setup.py` | Install dependencies |
| `python main.py add ...` | Add new account |
| `python main.py login ACC_ID` | Login to account |
| `python main.py monitor` | Start monitoring |
| `python webhook_server.py` | Start webhook (local) |
| `python webhook_server.py --ngrok` | Start webhook (external) |
| `python test_webhook.py` | Test webhook endpoint |

## Architecture

```
Manager tweets on Twitter
        ↓ (instant)
IFTTT webhook
        ↓ (< 1 sec)
Webhook Server (http://0.0.0.0:8000)
        ↓
All accounts engage immediately
        ↓
If no webhook: Fallback polling (60-120 min)
```

## 24/7 Operation

Run in background on Linux/Mac:
```bash
nohup python main.py monitor > bot.log 2>&1 &
```

Monitor logs:
```bash
tail -f bot.log
```

---

**All set! Bot is now ready for production use.** 🚀
