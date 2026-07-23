from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"alerts": {}, "last_checks": {}}
    try:
        with state_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"alerts": {}, "last_checks": {}}
        data.setdefault("alerts", {})
        data.setdefault("last_checks", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"alerts": {}, "last_checks": {}}


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state.setdefault("alerts", {})
    state.setdefault("last_checks", {})
    with state_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)


def cooldown_active(state: dict[str, Any], key: str, cooldown_hours: float) -> bool:
    alerts = state.get("alerts") or {}
    entry = alerts.get(key)
    if not entry:
        return False
    last = entry.get("last_alert_at")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_dt < timedelta(hours=cooldown_hours)


def mark_alerted(
    state: dict[str, Any], key: str, reason: str, price: float | None
) -> None:
    state.setdefault("alerts", {})
    state["alerts"][key] = {
        "last_alert_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "price": price,
    }


def mark_checked(
    state: dict[str, Any],
    key: str,
    *,
    title: str | None,
    current_price: float | None,
    list_price: float | None,
    status: str,
    reason: str | None = None,
    error: str | None = None,
) -> None:
    state.setdefault("last_checks", {})
    state["last_checks"][key] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "current_price": current_price,
        "list_price": list_price,
        "status": status,
        "reason": reason,
        "error": error,
    }


def clear_product_state(state: dict[str, Any], key: str) -> bool:
    """Remove last_checks/alerts de um produto. Retorna True se algo foi removido."""
    removed = False
    alerts = state.get("alerts")
    if isinstance(alerts, dict) and key in alerts:
        del alerts[key]
        removed = True
    checks = state.get("last_checks")
    if isinstance(checks, dict) and key in checks:
        del checks[key]
        removed = True
    return removed
