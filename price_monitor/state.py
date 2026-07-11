from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"alerts": {}}
    try:
        with state_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"alerts": {}}
        data.setdefault("alerts", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"alerts": {}}


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
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
