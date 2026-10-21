"""Password accounts and revocable server-side sessions (MongoDB or local JSON)."""
import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import OrderedDict
from threading import RLock
from uuid import uuid4

from fastapi import Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .ui import UI_ROOT
from .i18n import get_language, install_i18n, language_context, translate

COOKIE = "mas_session"
ITERATIONS = 600_000


def normalize_email(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9]+(?:\.?[a-z0-9]+)*(?:\+[a-z0-9._-]+)?@gmail\.com", value):
        raise ValueError("Gmail 주소(@gmail.com)를 입력하세요.")
    # Gmail delivers dotted addresses and +tags to the same mailbox.
    local = value.split("@", 1)[0].split("+", 1)[0].replace(".", "")
    if len(local) > 64:
        raise ValueError("Gmail 주소를 확인하세요.")
    return local + "@gmail.com"


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        rounds = int(iterations)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= rounds <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), rounds)
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.cookie = "__Host-mas_session" if settings.session_https_only else COOKIE
        self.max_age = max(1, settings.session_max_hours) * 3600
        self.attempts = OrderedDict()
        self.attempt_lock = RLock()
        self.dummy_hash = hash_password(secrets.token_urlsafe(32))

    def initialize(self):
        s = self.settings
        if bool(s.admin_email) != bool(s.admin_password):
            raise RuntimeError("MAS_ADMIN_EMAIL과 MAS_ADMIN_PASSWORD를 함께 설정하세요.")
        admin = None
        if s.admin_email:
            email = normalize_email(s.admin_email)
            if not 12 <= len(s.admin_password) <= 256:
                raise RuntimeError("MAS_ADMIN_PASSWORD는 12~256자여야 합니다.")
            with self.store.lock:
                admin = self.store.identity_get("users", digest(email))
                if admin is None:
                    admin = self.new_user(email, s.admin_password, "admin")
                elif not verify_password(s.admin_password, admin["password_hash"]):
                    admin["password_hash"] = hash_password(s.admin_password)
                admin.update(role="admin", env_managed=True, disabled=False)
                self.store.identity_save("users", admin)
        # Removing/changing env credentials revokes the former administrator's access.
        for user in self.store.identity_records("users"):
            if user.get("env_managed") and (admin is None or user["id"] != admin["id"]):
                user.update(disabled=True, role="user")
                self.store.identity_save("users", user)
        if admin:
            for project in self.store.list():
                if not project.get("owner_id"):
                    project["owner_id"] = admin["user_id"]
                    self.store.save(project)
        self.cleanup_sessions()

    def new_user(self, email, password, role="user"):
        return {"id": digest(email), "user_id": uuid4().hex, "email": email,
                "password_hash": hash_password(password), "role": role,
                "created_at": time.time()}

    @staticmethod
    def public_user(user):
        return {k: user[k] for k in ("user_id", "email", "role")}

    def current_user(self, request):
        token = request.cookies.get(self.cookie, "")
        if not token or len(token) > 128:
            return None
        session = self.store.identity_get("auth_sessions", digest(token))
        if not session or session["expires_at"] <= time.time():
            return None
        user = self.store.identity_get("users", session["user_key"])
        if not user or user.get("disabled") or session["credential"] != digest(user["password_hash"]):
            return None
        return self.public_user(user)

    def cleanup_sessions(self):
        for session in self.store.identity_records("auth_sessions"):
            if session["expires_at"] <= time.time():
                self.store.identity_delete("auth_sessions", session["id"])

    def start_session(self, request, user):
        token = secrets.token_urlsafe(32)
        with self.store.lock:
            old = request.cookies.get(self.cookie, "")
            if old:
                self.store.identity_delete("auth_sessions", digest(old))
            self.cleanup_sessions()
            self.store.identity_save("auth_sessions", {
                "id": digest(token), "user_key": user["id"],
                "credential": digest(user["password_hash"]), "expires_at": time.time() + self.max_age,
            })
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(self.cookie, token, max_age=self.max_age, httponly=True,
                            secure=self.settings.session_https_only, samesite="lax", path="/")
        return response

    def retry_after(self, request):
        # Trust the socket peer, never an untrusted X-Forwarded-For header.
        key = request.client.host if request.client else "unknown"
        stamp = time.monotonic()
        with self.attempt_lock:
            attempts = [t for t in self.attempts.pop(key, []) if stamp - t < 900]
            self.attempts[key] = attempts
            while len(self.attempts) > 10_000:
                self.attempts.popitem(last=False)
            if len(attempts) >= 10:
                return max(1, int(900 - (stamp - attempts[0])) + 1)
            attempts.append(stamp)
        return 0

    def visible_projects(self, user):
        return [p for p in self.store.list() if p.get("owner_id") == user["user_id"]]

    def guard(self, request):
        path = request.url.path
        if path in {"/login", "/register", "/language"} or path.startswith("/static/"):
            return None
        user = self.current_user(request)
        request.state.user = user
        if user is None:
            if path.startswith(("/api/", "/ui/")):
                return JSONResponse({"detail": translate("로그인이 필요합니다.", get_language(request))}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        # All project routes, including audio, downloads and mutations, share this gate.
        parts = path.rstrip("/").split("/")
        if len(parts) >= 4 and parts[1:3] in (["api", "projects"], ["ui", "tracks"]):
            project = self.store.get(parts[3])
            if not project or project.get("owner_id") != user["user_id"]:
                return JSONResponse({"detail": translate("프로젝트가 없습니다.", get_language(request))}, status_code=404)
        return None


def register_auth(app, auth):
    templates = Jinja2Templates(directory=UI_ROOT / "templates")
    install_i18n(templates)

    def page(request, mode, error=None, status=200, email="", headers=None):
        return templates.TemplateResponse(request=request, name="auth.html", status_code=status,
                                          headers=headers, context={**language_context(request), "mode": mode, "error": error, "email": email})

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        return RedirectResponse("/", 303) if auth.current_user(request) else page(request, "login")

    @app.get("/register", include_in_schema=False)
    def register_page(request: Request):
        return RedirectResponse("/", 303) if auth.current_user(request) else page(request, "register")

    def submit(request, mode, email, password, confirmation=""):
        retry = auth.retry_after(request)
        if retry:
            return page(request, mode, f"시도가 너무 많습니다. {retry}초 후 다시 시도하세요.", 429,
                        email, {"Retry-After": str(retry)})
        try:
            normalized = normalize_email(email)
        except ValueError as exc:
            return page(request, mode, str(exc), 422, email)
        key = digest(normalized)
        if mode == "register":
            if not 12 <= len(password) <= 256:
                return page(request, mode, "비밀번호는 12~256자로 입력하세요.", 422, email)
            if password != confirmation:
                return page(request, mode, "비밀번호 확인이 일치하지 않습니다.", 422, email)
            with auth.store.lock:
                if auth.store.identity_get("users", key):
                    return page(request, mode, "가입할 수 없는 주소입니다. 기존 계정은 로그인하세요.", 409, email)
                user = auth.new_user(normalized, password)
                auth.store.identity_save("users", user)
        else:
            user = auth.store.identity_get("users", key)
            valid = verify_password(password, user["password_hash"] if user else auth.dummy_hash)
            if not valid or not user or user.get("disabled"):
                return page(request, mode, "이메일 또는 비밀번호가 올바르지 않습니다.", 401, email)
        return auth.start_session(request, user)

    @app.post("/login", include_in_schema=False)
    def login(request: Request, email: str = Form(..., max_length=254),
              password: str = Form(..., max_length=256)):
        return submit(request, "login", email, password)

    @app.post("/register", include_in_schema=False)
    def register(request: Request, email: str = Form(..., max_length=254),
                 password: str = Form(..., max_length=256),
                 password_confirm: str = Form(..., max_length=256)):
        return submit(request, "register", email, password, password_confirm)

    @app.post("/logout", include_in_schema=False)
    def logout(request: Request):
        token = request.cookies.get(auth.cookie, "")
        if token:
            auth.store.identity_delete("auth_sessions", digest(token))
        response = RedirectResponse("/login", 303)
        response.delete_cookie(auth.cookie, path="/", secure=auth.settings.session_https_only,
                               httponly=True, samesite="lax")
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        return request.state.user
