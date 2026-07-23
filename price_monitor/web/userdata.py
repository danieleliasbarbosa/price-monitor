"""Dados por usuário: produtos.json + estado de checks."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

LEGACY_DIR_RE = re.compile(r"^[a-z0-9_]{3,32}$")


def user_dirname(username: str) -> str:
    """
    Nome de pasta seguro para o usuário.

    - Usuários legados [a-z0-9_] continuam na pasta com o próprio nome.
    - Nomes com caracteres de e-mail (., +, etc.) usam slug + hash.
    """
    name = (username or "").strip().lower()
    if LEGACY_DIR_RE.fullmatch(name):
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:24] or "user"
    return f"{slug}-{digest}"


def user_root(data_dir: Path, username: str) -> Path:
    name = (username or "").strip().lower()
    if not name:
        raise ValueError("Empty username.")
    dirname = user_dirname(name)
    root = (data_dir / "users" / dirname).resolve()
    # Evita path traversal fora de .data/users
    users_root = (data_dir / "users").resolve()
    if users_root not in root.parents and root != users_root:
        raise ValueError(f"Invalid username for path: {username!r}")
    root.mkdir(parents=True, exist_ok=True)
    (root / ".state").mkdir(parents=True, exist_ok=True)
    return root


def user_config_path(data_dir: Path, username: str) -> Path:
    return user_root(data_dir, username) / "produtos.json"


def user_state_dir(data_dir: Path, username: str) -> Path:
    path = user_root(data_dir, username) / ".state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_check_cooldown_path(data_dir: Path, username: str) -> Path:
    return user_state_dir(data_dir, username) / "last_full_check.json"


CHECK_COOLDOWN_HOURS = 24


def get_check_cooldown(
    data_dir: Path, username: str, *, hours: float = CHECK_COOLDOWN_HOURS
) -> dict[str, Any]:
    """Retorna status do cooldown de verificação completa do usuário."""
    from datetime import datetime, timedelta, timezone

    path = user_check_cooldown_path(data_dir, username)
    last_at: datetime | None = None
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            value = str((raw or {}).get("last_check_at") or "").strip()
            if value:
                last_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if last_at.tzinfo is None:
                    last_at = last_at.replace(tzinfo=timezone.utc)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            last_at = None

    now = datetime.now(timezone.utc)
    available_at = (last_at + timedelta(hours=hours)) if last_at else None
    remaining_seconds = 0
    if available_at and available_at > now:
        remaining_seconds = int((available_at - now).total_seconds())
    allowed = remaining_seconds <= 0
    return {
        "allowed": allowed,
        "cooldown_hours": hours,
        "last_check_at": last_at.isoformat() if last_at else None,
        "available_at": available_at.isoformat() if available_at and not allowed else None,
        "remaining_seconds": remaining_seconds if not allowed else 0,
    }


def mark_check_started(data_dir: Path, username: str) -> dict[str, Any]:
    from datetime import datetime, timezone

    path = user_check_cooldown_path(data_dir, username)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {"last_check_at": now.isoformat()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def user_base_dir(data_dir: Path, username: str) -> Path:
    return user_root(data_dir, username)


def default_config_template(project_root: Path) -> dict[str, Any]:
    exemplo = project_root / "produtos.exemplo.json"
    if exemplo.exists():
        try:
            raw = json.loads(exemplo.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw = dict(raw)
                raw["products"] = []
                return raw
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "cooldown_hours": 24,
        "headless": True,
        "retailers": {},
        "products": [],
    }


def ensure_user_config(
    data_dir: Path,
    username: str,
    *,
    project_root: Path,
    import_legacy: bool = False,
) -> Path:
    """Garante produtos.json do usuário. Opcionalmente importa o JSON legado da raiz."""
    path = user_config_path(data_dir, username)
    if path.exists():
        return path

    legacy = project_root / "produtos.json"
    if import_legacy and legacy.exists():
        shutil.copy2(legacy, path)
        return path

    template = default_config_template(project_root)
    path.write_text(
        json.dumps(template, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
