"""Server-only OpenAI-compatible HTTP adapter. No silent provider fallbacks."""
from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

class AIError(ValueError):
    """An actionable, sanitized error suitable for job status and server logs."""


def redact(settings, value):
    text = str(value)
    for key in (settings.audio_ai_api_key, settings.llm_ai_api_key):
        key = key.strip()
        if key:
            text = text.replace(key, "[redacted]")
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r"[A-Za-z0-9+/=]{100,}", "[encoded data removed]", text)
    return " ".join(text.split())[:500]


def endpoint_urls(value):
    try:
        parts = urlsplit(value.strip())
        parts.port  # Validate malformed/out-of-range ports without echoing the URL.
    except ValueError:
        raise AIError("Invalid API URL. Check the scheme, host and port in .env.") from None
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise AIError("API URL must be an http(s) address without credentials, query or fragment.")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    if not path:
        path = "/v1"
    base = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return base + "/chat/completions", base + "/models"


@dataclass
class Provider:
    role: str
    url: str
    model: str
    api_key: str = field(repr=False)

    @classmethod
    def from_settings(cls, settings, role):
        prefix = "audio_ai" if role == "AUDIO" else "llm_ai"
        values = [getattr(settings, prefix + suffix).strip() for suffix in ("_url", "_model", "_api_key")]
        missing = [role + suffix for suffix, value in zip(("_AI_URL", "_AI_MODEL", "_AI_API_KEY"), values) if not value]
        if missing:
            raise AIError("Missing .env settings: " + ", ".join(missing))
        endpoint_urls(values[0])
        return cls(role, *values)

    def _request(self, settings, method, url, payload=None):
        try:
            with httpx.Client(timeout=httpx.Timeout(settings.ai_timeout_seconds, connect=10),
                              follow_redirects=False) as client:
                response = client.request(method, url, headers={"Authorization": f"Bearer {self.api_key}"},
                                          **({"json": payload} if payload is not None else {}))
            if not 200 <= response.status_code < 300:
                detail = ""
                try:
                    error = response.json().get("error", {})
                    if isinstance(error, dict):
                        detail = str(error.get("message", ""))
                except (ValueError, AttributeError):
                    pass
                hint = {400: "Check model/audio format and the serving chat template.",
                        401: "Check API key.", 403: "Check API key and server access.",
                        404: "Check API URL and served model ID.",
                        413: "Audio request is too large; reduce MAS_AUDIO_CLIP_SECONDS.",
                        422: "Server rejected the request schema/audio input.",
                        429: "Server is busy or rate limited; retry later."}.get(response.status_code, "Check server logs.")
                raise AIError(f"{self.role} HTTP {response.status_code}: {hint} {redact(settings, detail)}")
            try:
                data = response.json()
            except ValueError:
                raise AIError(f"{self.role}: Server returned invalid JSON.") from None
            if not isinstance(data, dict):
                raise AIError(f"{self.role}: Expected an OpenAI-compatible JSON object.")
            return data
        except httpx.TimeoutException:
            raise AIError(f"{self.role}: API request timed out ({settings.ai_timeout_seconds:g}s).") from None
        except httpx.RequestError:
            raise AIError(f"{self.role}: Cannot connect to API. Check .env URL, server, DNS and TLS.") from None

    def check_model(self, settings):
        _, url = endpoint_urls(self.url)
        data = self._request(settings, "GET", url)
        entries = data.get("data")
        if not isinstance(entries, list) or any(not isinstance(e, dict) or not isinstance(e.get("id"), str) for e in entries):
            raise AIError(f"{self.role}: /models response has no valid data[].id list.")
        ids = [e["id"] for e in entries]
        if self.model not in ids:
            raise AIError(f"{self.role}: Configured model ID is not served. /models IDs: {redact(settings, ', '.join(ids))}")
        return {"model": self.model, "served_models": [redact_secrets(settings, i) for i in ids], "model_verified": True}

    def complete(self, settings, messages, max_tokens):
        url, _ = endpoint_urls(self.url)
        data = self._request(settings, "POST", url, {
            "model": self.model, "messages": messages, "max_tokens": max_tokens,
        })
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, TypeError, IndexError):
            raise AIError(f"{self.role}: Invalid chat completion response (choices[0].message.content).") from None
        if choice.get("finish_reason") not in (None, "stop"):
            raise AIError(f"{self.role}: Incomplete response ({redact(settings, choice.get('finish_reason'))}); check token limit/server logs.")
        if not isinstance(content, str) or not content.strip():
            raise AIError(f"{self.role}: Empty text response. Check token budget and reasoning parser on the server.")
        if len(content) > 100000:
            raise AIError(f"{self.role}: Text response exceeds the storage limit.")
        result = {"content": content, "model": data.get("model", self.model),
                  "response_id": data.get("id"), "usage": data.get("usage"),
                  "finish_reason": choice.get("finish_reason")}
        return redact_secrets(settings, result)


def redact_secrets(settings, text):
    if isinstance(text, dict):
        return {redact_secrets(settings, k): redact_secrets(settings, v) for k, v in text.items()}
    if isinstance(text, list):
        return [redact_secrets(settings, v) for v in text]
    if not isinstance(text, str):
        return text
    for key in (settings.audio_ai_api_key, settings.llm_ai_api_key):
        key = key.strip()
        if key:
            text = text.replace(key, "[redacted]")
    return text


def configured(settings):
    return all(getattr(settings, prefix + suffix).strip()
               for prefix in ("audio_ai", "llm_ai") for suffix in ("_url", "_model", "_api_key"))
