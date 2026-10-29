# 🔔 Webhook Setup - الدليل الكامل

## الخطوة 1️⃣: تثبيت المتطلبات

```bash
cd ~/twitterx

# تثبيت Flask (للـ webhook server)
pip install flask

# تثبيت requests (للـ testing)
pip install requests

# (اختياري) تثبيت ngrok (للـ external access)
pip install pyngrok
```

---

## الخطوة 2️⃣: تشغيل الـ Webhook Server

### **في Terminal #1:**

```bash
cd ~/twitterx
python webhook_server.py
```

**يجب أن ترى:**
```
============================================================
🚀 Webhook Server
============================================================
✅ Server شغّال على: http://0.0.0.0:8000
✅ Accounts: 3
✅ Config: ✓

📍 Endpoints:
  POST   http://localhost:8000/webhook/twitter  ← IFTTT يرسل هنا
  GET    http://localhost:8000/health            ← فحص الحالة
  GET    http://localhost:8000/status            ← عرض الوضع
  POST   http://localhost:8000/test              ← تجربة محلية

⏳ استنى على IFTTT notifications...
============================================================
```

**إذا فشل؟ شوف "Troubleshooting" بالأسفل**

---

## الخطوة 3️⃣: اختبار الـ Webhook محلياً

### **في Terminal #2:**

```bash
cd ~/twitterx
python test_webhook.py
```

**يجب أن ترى:**
```
🏥 فحص صحة الـ Server
✅ Server شغّال!
   Accounts: 3
   Config: True

📊 حالة الـ Server
✅ Accounts: 3
✅ Webhooks: 0

🧪 اختبار الـ Webhook
...
✅ اختبار الـ webhook نجح!
   يمكنك الآن إعداد IFTTT
```

**إذا فشل؟ Server مش شغّال (راجع Terminal #1)**

---

## الخطوة 4️⃣: إعداد IFTTT

### **A. إذا كنت في الجهاز الخاص بك (Local)**

1. **اذهب لـ https://ifttt.com**
2. **سجّل دخول أو اعمل account جديد**
3. **Click "Create" → "New Applet"**

4. **البداية بـ IF:**
   - اضغط على "**IF**" (الأزرق)
   - اختار "**Twitter**"
   - اختار "**New tweet by a specific user**"
   - اكتب اسم المدير: `@manager_handle`
   - اضغط "Create trigger"

5. **بعدين THEN:**
   - اضغط على "**THEN**" (الأحمر)
   - اختار "**Webhooks**"
   - اختار "**Make a web request**"

6. **في Webhooks Form:**
   ```
   URL: http://localhost:8000/webhook/twitter
   Method: POST
   Content Type: application/json
   
   Body:
   {
     "tweet_id": "{{TweetId}}",
     "text": "{{Text}}"
   }
   ```

7. **اضغط "Create action"**
8. **اضغط "Finish"**

✅ **تمام! الآن IFTTT متصل**

---

### **B. إذا كان Server على الإنترنت (VPS/Cloud)**

#### **Option 1: استخدام Static IP**

إذا عندك static IP:

```
URL: http://your-ip:8000/webhook/twitter
```

**مثال:**
```
URL: http://192.168.1.100:8000/webhook/twitter
```

---

#### **Option 2: استخدام Ngrok (أسهل)**

1. **قم بـ signup لـ ngrok:**
   ```
   https://ngrok.com/signup
   ```

2. **حمّل و ثبّت ngrok:**
   ```bash
   pip install pyngrok
   ```

3. **في Terminal جديد (#3):**
   ```bash
   python -c "from pyngrok import ngrok; 
   url = ngrok.connect(8000); 
   print(f'Webhook URL: {url}/webhook/twitter')"
   ```

4. **ستطبع شيء مثل:**
   ```
   Webhook URL: http://abc123def456.ngrok.io/webhook/twitter
   ```

5. **استخدم هاي الـ URL في IFTTT:**
   ```
   URL: http://abc123def456.ngrok.io/webhook/twitter
   ```

---

## الخطوة 5️⃣: الاختبار النهائي

### **انزل تغريدة جديدة من اسم المدير**

في Terminal #1 يجب أن ترى:
```
⚡ WEBHOOK تويتة جديدة: 1234567890
   Text: مثال على التغريدة...
✅ تفاعل مكتمل: 5 حساب
```

✅ **تمام! كل شي شغّال!**

---

## 🚨 Troubleshooting

### **مشكلة #1: "Address already in use"**

```
❌ Port 8000 مستخدم بالفعل!
```

**الحل:**

```bash
# استخدم port مختلف
python webhook_server.py --port 8001

# أو اغلق البرنامج الآخر
lsof -i :8000
kill -9 <PID>
```

---

### **مشكلة #2: "Connection refused"**

```
❌ لا يمكن الاتصال بـ http://localhost:8000
```

**الحل:**

1. تأكد أن الـ server شغّال (Terminal #1)
2. تأكد من الـ URL
3. جرّب: `curl http://localhost:8000/health`

---

### **مشكلة #3: IFTTT ما ترسل webhook**

**الحل:**

1. تحقق من Twitter connection في IFTTT
   - Settings → Connected services
   - Twitter يجب يكون connected

2. تحقق من الـ JSON format في IFTTT
   - يجب يكون exactly:
   ```json
   {
     "tweet_id": "{{TweetId}}",
     "text": "{{Text}}"
   }
   ```

3. عدّل Applet و جرّب مرة تانية

---

### **مشكلة #4: "missing tweet_id"**

```
❌ Missing tweet_id في الـ webhook payload
```

**الحل:**

تأكد من الـ IFTTT body:
```json
{
  "tweet_id": "{{TweetId}}",
  "text": "{{Text}}"
}
```

**NOT:**
```json
{
  "id": "{{TweetId}}",  ← خطأ!
  "message": "{{Text}}"  ← خطأ!
}
```

---

### **مشكلة #5: "لا حسابات موجودة"**

```
❌ لا حسابات موجودة! استخدم: python main.py add --handle ...
```

**الحل:**

أولاً أضيف حسابات:
```bash
python main.py add --handle @account1 --username "Name"
python main.py add --handle @account2 --username "Name"
```

ثم سجّل دخول:
```bash
python main.py login acc_001
python main.py login acc_002
```

ثم شغّل الـ webhook:
```bash
python webhook_server.py
```

---

## 📋 قائمة التحقق

قبل الـ go live:

- [ ] Flask مثبتة: `pip list | grep Flask`
- [ ] Webhook server شغّال
- [ ] اختبار محلي نجح: `python test_webhook.py`
- [ ] IFTTT Applet created و activated
- [ ] اختبار مع تغريدة حقيقية
- [ ] Logs تظهر في Terminal #1

---

## 🎯 الخطوات النهائية

### **للـ 24/7 Operation:**

شغّل الـ server في background:

```bash
# في Linux/Mac
nohup python webhook_server.py > webhook.log 2>&1 &

# أو استخدم screen
screen -S webhook
python webhook_server.py
# اضغط Ctrl+A ثم D للـ detach
```

### **مراقبة الـ Logs:**

```bash
tail -f webhook.log
```

---

## ✅ النتيجة النهائية

```
Manager tweets on Twitter
        ↓ (instant)
IFTTT detects
        ↓ (< 1 sec)
POST to webhook
        ↓
Server processes
        ↓
All accounts react immediately ⚡
```

**تم! الآن البوت يتفاعل بـ seconds بدل دقايق!** 🚀
