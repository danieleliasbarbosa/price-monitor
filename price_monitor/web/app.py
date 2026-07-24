from __future__ import annotations

import io
import json
import os
import secrets
import threading
import traceback
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from price_monitor.adapters import get_adapter, list_retailers
from price_monitor.add_product import add_url_to_config
from price_monitor.config import load_config, resolve_cooldown
from price_monitor.envfile import load_dotenv
from price_monitor.runner import run_check
from price_monitor.state import clear_product_state, load_state, save_state
from price_monitor.web.userdata import (
    CHECK_COOLDOWN_HOURS,
    ensure_user_config,
    get_check_cooldown,
    mark_check_started,
    user_config_path,
    user_state_dir,
)
from price_monitor.web.users import UserStore

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
SESSION_COOKIE = "pm_session"

_check_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


class AuthBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class RegisterBody(AuthBody):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=120)
    phone: str = Field(min_length=10, max_length=14)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=6, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)
    confirm_password: str = Field(min_length=6, max_length=128)


class ForgotPasswordBody(BaseModel):
    email: str = Field(min_length=5, max_length=120)


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=6, max_length=128)
    confirm_password: str = Field(min_length=6, max_length=128)


class ChangeContactRequestBody(BaseModel):
    field: str = Field(min_length=5, max_length=10)  # email | phone
    new_value: str = Field(min_length=5, max_length=120)
    current_password: str = Field(min_length=6, max_length=128)
    channel: str = Field(min_length=5, max_length=10)  # email | phone


class ChangeContactConfirmBody(BaseModel):
    field: str = Field(min_length=5, max_length=10)
    new_value: str = Field(min_length=5, max_length=120)
    current_password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=4, max_length=12)


class AddProductBody(BaseModel):
    url: str = Field(min_length=8)
    target_price: float = Field(gt=0)
    name: str | None = Field(default=None, max_length=200)
    retailer: str | None = None


class CheckBody(BaseModel):
    retailer: str | None = None


def _base_dir() -> Path:
    # Prefer the project root (where produtos.json / .data live), not the process cwd.
    # price_monitor/web/app.py -> parents[2] == repo root.
    return Path(__file__).resolve().parents[2]


def _data_dir() -> Path:
    path = (_base_dir() / ".data").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _user_store() -> UserStore:
    return UserStore(_data_dir())


def _session_secret() -> str:
    env = os.getenv("PRICE_MONITOR_SECRET", "").strip()
    if env:
        return env
    secret_path = _data_dir() / "session.secret"
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(32)
    secret_path.write_text(value + "\n", encoding="utf-8")
    return value


def _current_username(request: Request) -> str | None:
    user = request.session.get("user")
    if isinstance(user, str) and user.strip():
        return user.strip().lower()
    return None


def require_user(request: Request) -> str:
    username = _current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    if _user_store().get(username) is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Invalid session. Please log in again.")
    ensure_user_config(
        _data_dir(),
        username,
        project_root=_base_dir(),
        import_legacy=False,
    )
    return username


def _config_for(username: str) -> Path:
    return user_config_path(_data_dir(), username)


def _product_payload(product, adapter, state: dict[str, Any]) -> dict[str, Any]:
    key = adapter.product_key(product)
    last = (state.get("last_checks") or {}).get(key) or {}
    alert = (state.get("alerts") or {}).get(key) or {}
    current = last.get("current_price")
    target = product.target_price
    delta = None
    if current is not None and target is not None:
        delta = round(float(current) - float(target), 2)
    return {
        "retailer": product.retailer,
        "brand": adapter.brand,
        "name": product.name,
        "url": product.url,
        "target_price": target,
        "product_key": key,
        "product_id": product.product_id,
        "asin": product.asin,
        "pending": False,
        "available_after": None,
        "last_check": last or None,
        "last_alert": alert or None,
        "delta_to_target": delta,
        "below_target": (
            current is not None and target is not None and float(current) <= float(target)
        ),
    }


def _pending_product_payload(product) -> dict[str, Any]:
    raw = product.raw if isinstance(product.raw, dict) else {}
    return {
        "retailer": product.retailer,
        "brand": product.retailer,
        "name": product.name,
        "url": product.url,
        "target_price": product.target_price,
        "product_key": f"pending:{product.url}",
        "product_id": None,
        "asin": None,
        "pending": True,
        "available_after": raw.get("available_after"),
        "last_check": None,
        "last_alert": None,
        "delta_to_target": None,
        "below_target": False,
    }


def create_app() -> FastAPI:
    load_dotenv(_base_dir() / ".env", override=True)
    app = FastAPI(title="price-monitor", docs_url="/api/docs")
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(),
        session_cookie=SESSION_COOKIE,
        same_site="lax",
        https_only=False,
        max_age=60 * 60 * 24 * 14,
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/auth/me")
    def auth_me(request: Request) -> dict[str, Any]:
        username = _current_username(request)
        store = _user_store()
        entry = store.get(username) if username else None
        if not username or entry is None:
            return {"authenticated": False, "user": None}
        return {"authenticated": True, "user": store.public_profile(entry)}

    @app.get("/api/auth/profile")
    def auth_profile(username: str = Depends(require_user)) -> dict[str, Any]:
        store = _user_store()
        entry = store.get(username)
        if entry is None:
            raise HTTPException(status_code=404, detail="User not found.")
        return {"user": store.public_profile(entry)}

    @app.post("/api/auth/change-password")
    def auth_change_password(
        body: ChangePasswordBody, username: str = Depends(require_user)
    ) -> dict[str, Any]:
        if body.new_password != body.confirm_password:
            raise HTTPException(status_code=400, detail="New passwords do not match.")
        try:
            _user_store().change_password(
                username,
                current_password=body.current_password,
                new_password=body.new_password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "message": "Password changed successfully."}

    @app.post("/api/auth/change-contact/request")
    def auth_change_contact_request(
        body: ChangeContactRequestBody, username: str = Depends(require_user)
    ) -> dict[str, Any]:
        from price_monitor.mail import send_email
        from price_monitor.sms import send_sms, sms_configured

        field = body.field.strip().lower()
        channel = body.channel.strip().lower()
        try:
            code, profile = _user_store().start_contact_change(
                username,
                field=field,
                new_value=body.new_value,
                current_password=body.current_password,
                channel=channel,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        label = "email" if field == "email" else "phone number"
        msg = (
            f"Your Price Monitor verification code is {code}. "
            f"Use it to confirm your {label} change. It expires in 10 minutes."
        )
        sent = False
        if channel == "email":
            to = str(profile.get("email") or "").strip()
            sent = send_email(
                "Price Monitor verification code",
                msg,
                to=to,
                html=(
                    f"<p>Your Price Monitor verification code is "
                    f"<strong>{code}</strong>.</p>"
                    f"<p>Use it to confirm your {label} change. "
                    "It expires in 10 minutes.</p>"
                ),
            )
        else:
            if not sms_configured():
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "SMS is not configured on this server. "
                        "Choose email to receive the code, or set Twilio env vars."
                    ),
                )
            to = str(profile.get("phone") or "").strip()
            sent = send_sms(msg, to=to)

        if os.getenv("DEV_PRINT_VERIFY_CODES", "").strip() in {"1", "true", "yes"}:
            print(f"[verify] code for {username}/{field} via {channel}: {code}")

        if not sent:
            raise HTTPException(
                status_code=502,
                detail="Could not send the verification code. Try again shortly.",
            )
        return {
            "ok": True,
            "message": f"Verification code sent via {channel}.",
            "channel": channel,
            "field": field,
        }

    @app.post("/api/auth/change-contact/confirm")
    def auth_change_contact_confirm(
        body: ChangeContactConfirmBody, username: str = Depends(require_user)
    ) -> dict[str, Any]:
        try:
            user = _user_store().confirm_contact_change(
                username,
                field=body.field,
                new_value=body.new_value,
                current_password=body.current_password,
                code=body.code,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "message": "Contact updated successfully.",
            "user": user,
        }

    @app.get("/api/auth/check-username")
    def auth_check_username(username: str = "") -> dict[str, Any]:
        from price_monitor.web.users import USERNAME_RE, normalize_username

        name = normalize_username(username)
        if len(name) < 3:
            return {
                "username": name,
                "available": None,
                "message": "Enter at least 3 characters.",
            }
        if "@" in name:
            return {
                "username": name,
                "available": False,
                "message": "Username cannot contain @.",
            }
        if not USERNAME_RE.match(name):
            return {
                "username": name,
                "available": False,
                "message": "Invalid characters. Use the same ones allowed in email (no @/space).",
            }
        if _user_store().exists(name):
            return {
                "username": name,
                "available": False,
                "message": "A user with that username already exists.",
            }
        return {
            "username": name,
            "available": True,
            "message": "Username available.",
        }

    @app.post("/api/auth/register")
    def auth_register(body: RegisterBody, request: Request) -> dict[str, Any]:
        store = _user_store()
        first_user = store.count() == 0
        try:
            user = store.create(
                body.username,
                body.password,
                name=body.name,
                email=body.email,
                phone=body.phone,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ensure_user_config(
            _data_dir(),
            user["username"],
            project_root=_base_dir(),
            # Primeiro usuário herda o produtos.json legado da raiz, se existir.
            import_legacy=first_user,
        )
        request.session["user"] = user["username"]
        return {
            "ok": True,
            "user": user,
            "imported_legacy": first_user and (_base_dir() / "produtos.json").exists(),
        }

    @app.post("/api/auth/login")
    def auth_login(body: AuthBody, request: Request) -> dict[str, Any]:
        user = _user_store().authenticate(body.username, body.password)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        ensure_user_config(
            _data_dir(),
            user["username"],
            project_root=_base_dir(),
            import_legacy=False,
        )
        request.session["user"] = user["username"]
        return {"ok": True, "user": user}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"ok": True}

    @app.post("/api/auth/forgot-password")
    def auth_forgot_password(body: ForgotPasswordBody) -> dict[str, Any]:
        """
        Sempre responde sucesso genérico (não revela se o e-mail existe).
        """
        from price_monitor.mail import send_email

        generic = {
            "ok": True,
            "message": (
                "If that email is registered, you will receive a link "
                "to reset your password."
            ),
        }
        result = _user_store().create_password_reset_token(body.email)
        if not result:
            return generic
        token, profile = result
        base = os.getenv("APP_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
        reset_url = f"{base}/?reset={token}"
        name = str(profile.get("name") or profile.get("username") or "user")
        subject = "Reset password — price-monitor"
        text = (
            f"Hi, {name}.\n\n"
            "We received a request to reset the password for your price-monitor account.\n"
            f"Open this link (valid for 1 hour):\n{reset_url}\n\n"
            "If you did not request this, ignore this email.\n"
        )
        html = (
            f"<p>Hi, <strong>{name}</strong>.</p>"
            "<p>We received a request to reset the password for your "
            "<strong>price-monitor</strong> account.</p>"
            f'<p><a href="{reset_url}">Click here to create a new password</a> '
            "(valid for 1 hour).</p>"
            f"<p style=\"word-break:break-all;color:#666;font-size:12px\">{reset_url}</p>"
            "<p>If you did not request this, ignore this email.</p>"
        )
        email_to = str(profile.get("email") or "").strip()
        sent = False
        if email_to:
            sent = send_email(subject, text, to=email_to, html=html)
        if not sent:
            return {
                "ok": False,
                "message": (
                    "Could not send the email right now. "
                    "Try again shortly or check your spam folder."
                ),
            }
        return generic

    @app.post("/api/auth/reset-password")
    def auth_reset_password(body: ResetPasswordBody) -> dict[str, Any]:
        if body.new_password != body.confirm_password:
            raise HTTPException(status_code=400, detail="Passwords do not match.")
        try:
            user = _user_store().reset_password_with_token(
                body.token, body.new_password
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "message": "Password reset. Sign in with your new password.",
            "user": user,
        }

    @app.get("/api/meta")
    def meta(username: str = Depends(require_user)) -> dict[str, Any]:
        path = _config_for(username)
        cooldown = 24.0
        if path.exists():
            try:
                _products, settings = load_config(path, allow_empty=True)
                cooldown = resolve_cooldown(settings, None)
            except Exception:
                pass
        check_cd = get_check_cooldown(
            _data_dir(), username, hours=CHECK_COOLDOWN_HOURS
        )
        return {
            "user": username,
            "config": str(path),
            "config_exists": path.exists(),
            "retailers": list_retailers(),
            "cooldown_hours": cooldown,
            "check_cooldown": check_cd,
        }

    @app.get("/api/products")
    def products(username: str = Depends(require_user)) -> dict[str, Any]:
        path = _config_for(username)
        if not path.exists():
            return {"products": [], "count": 0}

        products, _settings = load_config(path, allow_empty=True)
        state_root = user_state_dir(_data_dir(), username)
        known = set(list_retailers())
        items: list[dict[str, Any]] = []
        for product in products:
            if product.retailer not in known or (product.raw or {}).get("pending"):
                items.append(_pending_product_payload(product))
                continue
            adapter = get_adapter(product.retailer)
            state = load_state((state_root / f"{product.retailer}.json").resolve())
            items.append(_product_payload(product, adapter, state))
        return {"products": items, "count": len(items)}

    @app.post("/api/products")
    def add_product(
        body: AddProductBody, username: str = Depends(require_user)
    ) -> dict[str, Any]:
        config_path = _config_for(username)
        try:
            action, entry, _ = add_url_to_config(
                config_path,
                body.url.strip(),
                target_price=body.target_price,
                name=(body.name.strip() if body.name else None),
                retailer=(body.retailer.strip().lower() if body.retailer else None),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        added_retailer = str(entry.get("retailer") or "").strip().lower()
        pending_store = bool(entry.get("pending"))
        # "Loja nova" só para lojas ainda não integradas (pending).
        new_retailer = pending_store
        response: dict[str, Any] = {
            "action": action,
            "product": entry,
            "new_retailer": new_retailer,
            "pending_store": pending_store,
            "job_id": None,
            "check_started": False,
        }
        # Lojas suportadas: verifica o preço só deste produto (sem cooldown de 24h).
        if not pending_store and added_retailer in set(list_retailers()):
            # Garante que preço antigo em .state não reapareça ao readicionar.
            try:
                from price_monitor.config import retailer_settings as merge_settings

                adapter = get_adapter(added_retailer)
                raw_cfg = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
                settings = (
                    {k: v for k, v in raw_cfg.items() if k not in {"products", "produtos"}}
                    if isinstance(raw_cfg, dict)
                    else {}
                )
                product = adapter.normalize_product(
                    {**entry, "target_price": entry.get("target_price") or body.target_price},
                    merge_settings(settings, added_retailer),
                )
                key = adapter.product_key(product)
                state_path = (
                    user_state_dir(_data_dir(), username) / f"{added_retailer}.json"
                ).resolve()
                state = load_state(state_path)
                if clear_product_state(state, key):
                    save_state(state_path, state)
            except Exception:
                pass

            started = _start_check_job(
                username,
                retailer=added_retailer,
                url_filter=str(entry.get("url") or body.url),
                apply_cooldown=False,
            )
            if started:
                response["job_id"] = started["job_id"]
                response["check_started"] = True
            else:
                response["check_message"] = (
                    "Product added, but another check is already in progress."
                )
        return response

    @app.delete("/api/products/{index}")
    def delete_product(
        index: int, username: str = Depends(require_user)
    ) -> dict[str, Any]:
        path = _config_for(username)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Configuration not found")

        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            products = raw
            wrapper = None
            settings: dict[str, Any] = {}
        else:
            products = raw.get("products") or []
            wrapper = raw
            settings = {
                k: v for k, v in raw.items() if k not in {"products", "produtos"}
            }
        if index < 0 or index >= len(products):
            raise HTTPException(status_code=404, detail="Invalid index")
        removed = products.pop(index)

        # Limpa preço/alerta salvos desse produto para não reaparecer ao readicionar.
        if isinstance(removed, dict):
            retailer = str(removed.get("retailer") or "").strip().lower()
            if retailer and retailer in set(list_retailers()):
                try:
                    from price_monitor.config import retailer_settings as merge_settings

                    adapter = get_adapter(retailer)
                    rsettings = merge_settings(settings, retailer)
                    product = adapter.normalize_product(
                        {**removed, "target_price": removed.get("target_price") or 0.01},
                        rsettings,
                    )
                    key = adapter.product_key(product)
                    state_path = (
                        user_state_dir(_data_dir(), username) / f"{retailer}.json"
                    ).resolve()
                    state = load_state(state_path)
                    if clear_product_state(state, key):
                        save_state(state_path, state)
                except Exception:
                    pass

        if wrapper is None:
            path.write_text(
                json.dumps(products, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            wrapper["products"] = products
            path.write_text(
                json.dumps(wrapper, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return {"removed": removed}

    def _start_check_job(
        username: str,
        *,
        retailer: str | None = None,
        url_filter: str | None = None,
        apply_cooldown: bool = True,
    ) -> dict[str, Any] | None:
        if not _check_lock.acquire(blocking=False):
            return None
        if apply_cooldown:
            mark_check_started(_data_dir(), username)

        job_id = uuid.uuid4().hex[:12]
        config_path = _config_for(username)
        state_dir = user_state_dir(_data_dir(), username)
        project_root = _base_dir()
        job: dict[str, Any] = {
            "id": job_id,
            "user": username,
            "status": "running",
            "retailer": retailer,
            "url_filter": url_filter,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "exit_code": None,
            "log": "",
            "error": None,
        }
        _jobs[job_id] = job

        def worker() -> None:
            buf = io.StringIO()
            notify_email = None
            try:
                entry = _user_store().get(username)
                if entry:
                    notify_email = str(entry.get("email") or "").strip() or None
            except Exception:
                notify_email = None
            try:
                with redirect_stdout(buf):
                    code = run_check(
                        config_path=config_path,
                        base_dir=project_root,
                        retailer_filter=retailer,
                        headless=True,
                        cooldown_hours=None,
                        state_dir=state_dir,
                        profile_base=project_root,
                        url_filter=url_filter,
                        notify_email=notify_email,
                        show_summary=False,
                    )
                # Log must be set before status leaves "running" (poll race).
                job["log"] = buf.getvalue()
                job["exit_code"] = code
                job["status"] = "ok" if code == 0 else "finished_with_errors"
            except Exception as exc:
                traceback.print_exc(file=buf)
                job["log"] = buf.getvalue()
                job["status"] = "error"
                job["error"] = str(exc)
                job["exit_code"] = 1
            finally:
                if not job.get("log"):
                    job["log"] = buf.getvalue()
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
                _check_lock.release()

        threading.Thread(target=worker, daemon=True).start()
        return {
            "job_id": job_id,
            "status": "running",
            "check_cooldown": get_check_cooldown(
                _data_dir(), username, hours=CHECK_COOLDOWN_HOURS
            ),
        }

    @app.post("/api/check")
    def start_check(
        request: Request,
        body: CheckBody | None = None,
        username: str = Depends(require_user),
    ) -> dict[str, Any]:
        body = body or CheckBody()
        retailer = (body.retailer or "").strip().lower() or None
        if retailer and retailer not in list_retailers():
            raise HTTPException(status_code=400, detail=f"Invalid retailer: {retailer}")

        check_cd = get_check_cooldown(
            _data_dir(), username, hours=CHECK_COOLDOWN_HOURS
        )
        if not check_cd["allowed"]:
            hours = int(check_cd["remaining_seconds"] // 3600)
            minutes = int((check_cd["remaining_seconds"] % 3600) // 60)
            raise HTTPException(
                status_code=429,
                detail=(
                    "You already checked prices. "
                    f"Next check in {hours}h {minutes:02d}min."
                ),
            )

        started = _start_check_job(
            username, retailer=retailer, apply_cooldown=True
        )
        if started is None:
            raise HTTPException(
                status_code=409, detail="A check is already in progress."
            )
        return started

    @app.get("/api/check/{job_id}")
    def check_status(
        job_id: str, username: str = Depends(require_user)
    ) -> dict[str, Any]:
        job = _jobs.get(job_id)
        if not job or job.get("user") != username:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/api/check-latest")
    def check_latest(username: str = Depends(require_user)) -> dict[str, Any]:
        mine = [j for j in _jobs.values() if j.get("user") == username]
        if not mine:
            return {"job": None}
        latest = max(mine, key=lambda j: j.get("started_at") or "")
        return {"job": latest}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def run_server(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    load_dotenv(_base_dir() / ".env", override=True)
    print(f"price-monitor UI -> http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, reload=False)
