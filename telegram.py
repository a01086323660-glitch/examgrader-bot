"""عميل تيليغرام بسيط (stdlib فقط)."""
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


class TG:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.file_base = f"https://api.telegram.org/file/bot{token}/"

    def call(self, method, http_timeout=60, **params):
        data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                       for k, v in params.items()}).encode()
        try:
            with urllib.request.urlopen(self.base + method, data, timeout=http_timeout) as r:
                return json.loads(r.read())["result"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method}: HTTP {e.code} {e.read()[:200]!r}")

    def get_updates(self, offset, timeout=30):
        return self.call("getUpdates", http_timeout=timeout + 10, offset=offset,
                         allowed_updates=["message"], timeout=timeout)

    def send(self, chat_id, text, keyboard=None, remove_keyboard=False):
        """keyboard: قائمة صفوف من نصوص الأزرار. يُرسل مع آخر جزء فقط."""
        chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [text]
        for i, chunk in enumerate(chunks):
            params = {"chat_id": chat_id, "text": chunk}
            if i == len(chunks) - 1:
                if remove_keyboard:
                    params["reply_markup"] = {"remove_keyboard": True}
                elif keyboard is not None:
                    params["reply_markup"] = {
                        "keyboard": [[{"text": t} for t in row] for row in keyboard],
                        "resize_keyboard": True,
                        "is_persistent": True,
                    }
            self.call("sendMessage", **params)

    def download(self, file_id):
        info = self.call("getFile", file_id=file_id)
        with urllib.request.urlopen(self.file_base + info["file_path"], timeout=60) as r:
            return r.read()

    def send_document(self, chat_id, filename, content, caption="", mime="application/octet-stream"):
        b = uuid.uuid4().hex
        body = b""
        for k, v in (("chat_id", str(chat_id)), ("caption", caption)):
            body += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
        body += (f'--{b}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(self.base + "sendDocument", body,
                                     {"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["result"]
