# -*- coding: utf-8 -*-
"""خادم تيليجرام وهمي لاختبار حلقة البوت الحقيقية."""
import json, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeTelegram:
    def __init__(self):
        self.updates, self.calls, self.fail = [], [], {}
        self.webhook_active = False
        self.lock = threading.Lock()
        self.next_id = 100
        self.confirmed = 0

        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *a): pass
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                params = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
                method = self.path.rsplit("/", 1)[-1]
                code, body = outer.handle(method, params)
                data = json.dumps(body).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/bot{token}/{method}" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def push(self, **update):
        with self.lock:
            self.next_id += 1
            update["update_id"] = self.next_id
            self.updates.append(update)

    def handle(self, method, params):
        with self.lock:
            self.calls.append((method, params))
        if method in self.fail and self.fail[method]:
            fn = self.fail[method]
            r = fn(params) if callable(fn) else fn
            if r:
                return r
        if method == "getMe":
            return 200, {"ok": True, "result": {"id": 1, "username": "FakeBot", "first_name": "Fake"}}
        if method == "deleteWebhook":
            self.webhook_active = False
            return 200, {"ok": True, "result": True}
        if method == "getUpdates":
            if self.webhook_active:
                return 409, {"ok": False, "error_code": 409, "description": "Conflict: can't use getUpdates method while webhook is active"}
            off = int(params.get("offset") or 0)
            deadline = time.time() + min(int(params.get("timeout") or 0), 1)   # long-poll قصير للاختبار
            while time.time() < deadline:
                with self.lock:
                    if off:
                        self.confirmed = max(self.confirmed, off - 1)
                        self.updates = [u for u in self.updates if u["update_id"] >= off]
                    out = [u for u in self.updates if u["update_id"] >= off]
                if out:
                    return 200, {"ok": True, "result": out}
                time.sleep(0.05)
            return 200, {"ok": True, "result": []}
        if method == "sendMessage":
            return 200, {"ok": True, "result": {"message_id": 500 + len(self.calls)}}
        if method in ("editMessageText", "answerCallbackQuery", "setMyCommands"):
            return 200, {"ok": True, "result": True}
        if method == "getChat":
            return 200, {"ok": True, "result": {"id": -1001, "type": "channel", "title": "Fake"}}
        return 404, {"ok": False, "description": "unknown method"}

    def sent(self, method="sendMessage"):
        with self.lock:
            return [p for m, p in self.calls if m == method]

    def wait_for(self, pred, timeout=8):
        t = time.time()
        while time.time() - t < timeout:
            if pred():
                return True
            time.sleep(0.05)
        return False
