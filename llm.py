"""مزودات الذكاء الاصطناعي (Gemini / OpenAI) عبر HTTP بدون مكتبات خارجية."""
import base64
import json
import re
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def _post_json(url, payload, headers, timeout=120):
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(6):
        req = urllib.request.Request(url, data, {"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()[:300]
            last = LLMError(f"HTTP {e.code}: {body!r}")
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < 5:
                time.sleep(min(2 ** attempt, 30))
                continue
    raise LLMError(f"فشل الاتصال بعد عدة محاولات: {last}")


def parse_json(text):
    """يستخرج JSON حتى لو النموذج لفّه بـ ```."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise LLMError("رد النموذج مو JSON صالح")


class Gemini:
    def __init__(self, api_key, model):
        self.key, self.model = api_key, model

    def complete(self, system, prompt, images):
        parts = [{"text": prompt}] + [
            {"inline_data": {"mime_type": m, "data": base64.b64encode(b).decode()}}
            for b, m in images]
        payload = {"system_instruction": {"parts": [{"text": system}]},
                   "contents": [{"role": "user", "parts": parts}],
                   "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        d = _post_json(url, payload, {"x-goog-api-key": self.key})
        try:
            return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise LLMError(f"رد غير متوقع من Gemini: {str(d)[:200]}")


class OpenAI:
    def __init__(self, api_key, model, base_url="https://api.openai.com/v1"):
        self.key, self.model, self.base = api_key, model, base_url.rstrip("/")

    def complete(self, system, prompt, images):
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {
                "url": f"data:{m};base64,{base64.b64encode(b).decode()}", "detail": "high"}}
            for b, m in images]
        payload = {"model": self.model, "temperature": 0,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": content}]}
        d = _post_json(f"{self.base}/chat/completions", payload,
                       {"Authorization": f"Bearer {self.key}"})
        try:
            return d["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LLMError(f"رد غير متوقع من OpenAI: {str(d)[:200]}")


def make_llm(env):
    p = env.get("LLM_PROVIDER", "gemini").lower()
    if p == "gemini":
        if not env.get("GEMINI_API_KEY"):
            raise SystemExit("ناقص GEMINI_API_KEY")
        return Gemini(env["GEMINI_API_KEY"], env.get("GEMINI_MODEL", "gemini-3.5-flash"))
    if p == "openai":
        if not env.get("OPENAI_API_KEY"):
            raise SystemExit("ناقص OPENAI_API_KEY")
        return OpenAI(env["OPENAI_API_KEY"], env.get("OPENAI_MODEL", "gpt-4o"),
                      env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    raise SystemExit("LLM_PROVIDER لازم يكون gemini أو openai")
