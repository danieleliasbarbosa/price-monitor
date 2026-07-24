"""Usuários e senhas (local) para o dashboard web."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Mesmos caracteres do local-part do e-mail (antes do @): qualquer um exceto @ e espaço.
USERNAME_RE = re.compile(r"^[^@\s]{3,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PBKDF2_ITERATIONS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters_s, salt_hex, digest_hex = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iters,
    )
    return hmac.compare_digest(digest, expected)


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_username(username: str) -> str:
    name = normalize_username(username)
    if "@" in name:
        raise ValueError("Username cannot contain @. Use the email field for that.")
    if not USERNAME_RE.match(name):
        raise ValueError(
            "Invalid username. Use 3–64 characters (same as allowed in email, without @)."
        )
    return name


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if len(password) > 128:
        raise ValueError("Password is too long.")
    return password


def validate_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not value or not EMAIL_RE.match(value) or len(value) > 120:
        raise ValueError("Invalid email.")
    return value


def validate_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", (phone or "").strip())
    if len(digits) != 10:
        raise ValueError("Invalid phone number. Use the format (xxx) xxx-xxxx.")
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def validate_name(name: str) -> str:
    value = re.sub(r"\s+", " ", (name or "").strip())
    if len(value) < 2 or len(value) > 80:
        raise ValueError("Invalid name. Use between 2 and 80 characters.")
    return value


class UserStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.users_path = data_dir / "users.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.users_path.exists():
            return {"users": {}}
        try:
            raw = json.loads(self.users_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"users": {}}
        if not isinstance(raw, dict):
            return {"users": {}}
        users = raw.get("users")
        if not isinstance(users, dict):
            users = {}
        return {"users": users}

    def _save(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.users_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.users_path)

    def get(self, username: str) -> dict[str, Any] | None:
        name = normalize_username(username)
        users = self._load()["users"]
        entry = users.get(name)
        return entry if isinstance(entry, dict) else None

    def exists(self, username: str) -> bool:
        return self.get(username) is not None

    def count(self) -> int:
        return len(self._load()["users"])

    def create(
        self,
        username: str,
        password: str,
        *,
        name: str,
        email: str,
        phone: str,
    ) -> dict[str, Any]:
        name_value = validate_name(name)
        uname = validate_username(username)
        password = validate_password(password)
        email = validate_email(email)
        phone = validate_phone(phone)
        data = self._load()
        if uname in data["users"]:
            raise ValueError("That username already exists.")
        for other in data["users"].values():
            if not isinstance(other, dict):
                continue
            if str(other.get("email") or "").lower() == email:
                raise ValueError("That email is already in use.")
        entry = {
            "username": uname,
            "name": name_value,
            "email": email,
            "phone": phone,
            "password_hash": hash_password(password),
            "created_at": _now(),
        }
        data["users"][uname] = entry
        self._save(data)
        return self.public_profile(entry)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        entry = self.get(username)
        if not entry:
            return None
        if not verify_password(password, str(entry.get("password_hash") or "")):
            return None
        return self.public_profile(entry)

    def public_profile(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": entry.get("username"),
            "name": entry.get("name") or None,
            "email": entry.get("email") or None,
            "phone": entry.get("phone") or None,
            "created_at": entry.get("created_at"),
        }

    def change_password(
        self, username: str, *, current_password: str, new_password: str
    ) -> None:
        name = normalize_username(username)
        data = self._load()
        entry = data["users"].get(name)
        if not isinstance(entry, dict):
            raise ValueError("User not found.")
        if not verify_password(current_password, str(entry.get("password_hash") or "")):
            raise ValueError("Current password is incorrect.")
        new_password = validate_password(new_password)
        if verify_password(new_password, str(entry.get("password_hash") or "")):
            raise ValueError("The new password must be different from the current one.")
        entry["password_hash"] = hash_password(new_password)
        entry["password_changed_at"] = _now()
        entry.pop("reset_token_hash", None)
        entry.pop("reset_token_expires", None)
        data["users"][name] = entry
        self._save(data)

    def start_contact_change(
        self,
        username: str,
        *,
        field: str,
        new_value: str,
        current_password: str,
        channel: str,
    ) -> tuple[str, dict[str, Any]]:
        """
        Validates password + new contact, stores a hashed 6-digit code.
        Returns (plain_code, public_profile).
        """
        name = normalize_username(username)
        field = (field or "").strip().lower()
        channel = (channel or "").strip().lower()
        if field not in {"email", "phone"}:
            raise ValueError("Invalid field. Use email or phone.")
        if channel not in {"email", "phone"}:
            raise ValueError("Invalid channel. Choose email or phone.")

        data = self._load()
        entry = data["users"].get(name)
        if not isinstance(entry, dict):
            raise ValueError("User not found.")
        if not verify_password(current_password, str(entry.get("password_hash") or "")):
            raise ValueError("Current password is incorrect.")

        if field == "email":
            new_value = validate_email(new_value)
            current = str(entry.get("email") or "").lower()
            if new_value == current:
                raise ValueError("New email must be different from the current one.")
            for other_name, other in data["users"].items():
                if other_name == name or not isinstance(other, dict):
                    continue
                if str(other.get("email") or "").lower() == new_value:
                    raise ValueError("That email is already in use.")
        else:
            new_value = validate_phone(new_value)
            current = str(entry.get("phone") or "")
            if new_value == current:
                raise ValueError("New phone must be different from the current one.")

        dest_email = str(entry.get("email") or "").strip()
        dest_phone = str(entry.get("phone") or "").strip()
        if channel == "email" and not dest_email:
            raise ValueError("No email on this account to receive the code.")
        if channel == "phone" and not dest_phone:
            raise ValueError("No phone on this account to receive the code.")

        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        entry["contact_change"] = {
            "field": field,
            "new_value": new_value,
            "channel": channel,
            "code_hash": code_hash,
            "expires": expires,
            "attempts": 0,
            "created_at": _now(),
        }
        data["users"][name] = entry
        self._save(data)
        return code, self.public_profile(entry)

    def confirm_contact_change(
        self,
        username: str,
        *,
        field: str,
        new_value: str,
        current_password: str,
        code: str,
    ) -> dict[str, Any]:
        name = normalize_username(username)
        field = (field or "").strip().lower()
        if field not in {"email", "phone"}:
            raise ValueError("Invalid field. Use email or phone.")

        data = self._load()
        entry = data["users"].get(name)
        if not isinstance(entry, dict):
            raise ValueError("User not found.")
        if not verify_password(current_password, str(entry.get("password_hash") or "")):
            raise ValueError("Current password is incorrect.")

        pending = entry.get("contact_change")
        if not isinstance(pending, dict):
            raise ValueError("No pending change. Request a new code.")
        if str(pending.get("field") or "") != field:
            raise ValueError("No pending change for that field. Request a new code.")

        if field == "email":
            expected_value = validate_email(new_value)
        else:
            expected_value = validate_phone(new_value)
        if str(pending.get("new_value") or "") != expected_value:
            raise ValueError("New value does not match the pending request. Request a new code.")

        expires_raw = str(pending.get("expires") or "").strip()
        try:
            expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Code expired. Request a new one.") from None
        if expires < datetime.now(timezone.utc):
            entry.pop("contact_change", None)
            data["users"][name] = entry
            self._save(data)
            raise ValueError("Code expired. Request a new one.")

        attempts = int(pending.get("attempts") or 0)
        if attempts >= 5:
            entry.pop("contact_change", None)
            data["users"][name] = entry
            self._save(data)
            raise ValueError("Too many attempts. Request a new code.")

        code_raw = re.sub(r"\D", "", (code or "").strip())
        if len(code_raw) != 6:
            pending["attempts"] = attempts + 1
            entry["contact_change"] = pending
            data["users"][name] = entry
            self._save(data)
            raise ValueError("Invalid verification code.")

        code_hash = hashlib.sha256(code_raw.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(code_hash, str(pending.get("code_hash") or "")):
            pending["attempts"] = attempts + 1
            entry["contact_change"] = pending
            data["users"][name] = entry
            self._save(data)
            raise ValueError("Invalid verification code.")

        if field == "email":
            # Re-check uniqueness at confirm time.
            for other_name, other in data["users"].items():
                if other_name == name or not isinstance(other, dict):
                    continue
                if str(other.get("email") or "").lower() == expected_value:
                    raise ValueError("That email is already in use.")
            entry["email"] = expected_value
        else:
            entry["phone"] = expected_value

        entry["contact_updated_at"] = _now()
        entry.pop("contact_change", None)
        data["users"][name] = entry
        self._save(data)
        return self.public_profile(entry)

        try:
            target = validate_email(email)
        except ValueError:
            return None
        data = self._load()
        for entry in data["users"].values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("email") or "").lower() == target:
                return entry
        return None

    def create_password_reset_token(self, email: str) -> tuple[str, dict[str, Any]] | None:
        """
        Cria token de reset. Retorna (token_claro, perfil) ou None se e-mail inexistente.
        """
        entry = self.find_by_email(email)
        if not entry:
            return None
        username = str(entry.get("username") or "")
        if not username:
            return None
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        data = self._load()
        current = data["users"].get(normalize_username(username))
        if not isinstance(current, dict):
            return None
        current["reset_token_hash"] = token_hash
        current["reset_token_expires"] = expires
        data["users"][normalize_username(username)] = current
        self._save(data)
        return token, self.public_profile(current)

    def reset_password_with_token(self, token: str, new_password: str) -> dict[str, Any]:
        raw = (token or "").strip()
        if len(raw) < 20:
            raise ValueError("Invalid or expired reset link.")
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        new_password = validate_password(new_password)
        data = self._load()
        matched_name = None
        matched_entry = None
        now = datetime.now(timezone.utc)
        for name, entry in data["users"].items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("reset_token_hash") or "") != token_hash:
                continue
            expires_raw = str(entry.get("reset_token_expires") or "").strip()
            try:
                expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
            except ValueError:
                raise ValueError("Invalid or expired reset link.") from None
            if expires < now:
                raise ValueError("Invalid or expired reset link.")
            matched_name = name
            matched_entry = entry
            break
        if not matched_name or not isinstance(matched_entry, dict):
            raise ValueError("Invalid or expired reset link.")
        matched_entry["password_hash"] = hash_password(new_password)
        matched_entry["password_changed_at"] = _now()
        matched_entry.pop("reset_token_hash", None)
        matched_entry.pop("reset_token_expires", None)
        data["users"][matched_name] = matched_entry
        self._save(data)
        return self.public_profile(matched_entry)
