"""Shared UI translations. Stored names, evidence and historical reports stay intact."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Form, Request
from fastapi.responses import RedirectResponse
from jinja2 import pass_context

LANGUAGE_COOKIE = "mas-language"
MESSAGES = json.loads((Path(__file__).parent / "static" / "locales" / "en.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def get_language(request):
    return "en" if request and request.cookies.get(LANGUAGE_COOKIE) == "en" else "ko"


def interpolate(text, values):
    return PLACEHOLDER.sub(lambda m: str(values.get(m[1], m[0])), text)


PATTERNS = []
for source, target in MESSAGES.items():
    if PLACEHOLDER.search(source):
        parts, position = [], 0
        for match in PLACEHOLDER.finditer(source):
            parts.extend([re.escape(source[position:match.start()]), f"(?P<{match[1]}>.+?)"])
            position = match.end()
        parts.append(re.escape(source[position:]))
        PATTERNS.append((re.compile("".join(parts), re.DOTALL), target))


def translate(message, language="ko", **values):
    if message is None:
        return ""
    text = str(message)
    if language != "en":
        return interpolate(text, values)
    target = MESSAGES.get(text)
    if target is None:
        for pattern, replacement in PATTERNS:
            match = pattern.fullmatch(text)
            if match:
                target = replacement
                values = {**{k: MESSAGES.get(v, v) for k, v in match.groupdict().items()}, **values}
                break
    return interpolate(target if target is not None else text, values)


def install_i18n(templates):
    @pass_context
    def t(context, message, **values):
        return translate(message, get_language(context.get("request")), **values)
    templates.env.globals["t"] = t


def language_context(request):
    language = get_language(request)
    return {"lang": language, "i18n_messages": MESSAGES if language == "en" else {},
            "language_return": request.url.path + ("?" + request.url.query if request.url.query else "")}


def register_language(app, settings):
    @app.post("/language", include_in_schema=False)
    def set_language(request: Request, language: str = Form(..., pattern="^(ko|en)$"),
                     return_to: str = Form("/", max_length=2048)):
        # Redirect only to a local path, never a scheme, network path or backslash.
        try:
            target = urlsplit(return_to)
            safe = not target.scheme and not target.netloc
        except ValueError:
            safe = False
        if not safe or not return_to.startswith("/") or return_to.startswith("//") or "\\" in return_to or any(ord(c) < 32 for c in return_to):
            return_to = "/"
        response = RedirectResponse(return_to, status_code=303)
        response.set_cookie(LANGUAGE_COOKIE, language, max_age=365 * 86400, path="/",
                            samesite="lax", secure=settings.session_https_only)
        return response
