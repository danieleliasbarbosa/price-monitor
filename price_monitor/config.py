from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from price_monitor.models import Product
from price_monitor.prices import optional_float

DEFAULT_COOLDOWN_HOURS = 24
KNOWN_RETAILERS = ("amazon", "safeway", "instacart", "target", "walmart")


def load_config(config_path: Path) -> tuple[list[Product], dict[str, Any]]:
    # Lazy import evita ciclo config <-> adapters
    from price_monitor.adapters import get_adapter

    if not config_path.exists():
        raise FileNotFoundError(
            f"Arquivo de configuração não encontrado: {config_path}\n"
            "Copie produtos.exemplo.json para produtos.json e edite."
        )

    with config_path.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    if isinstance(raw, list):
        products_data = raw
        settings: dict[str, Any] = {}
    elif isinstance(raw, dict):
        products_data = raw.get("products") or raw.get("produtos") or []
        settings = {
            k: v
            for k, v in raw.items()
            if k not in {"products", "produtos"}
        }
    else:
        raise ValueError("O JSON de configuração deve ser uma lista ou um objeto.")

    retailers_cfg = settings.get("retailers") or {}
    if not isinstance(retailers_cfg, dict):
        retailers_cfg = {}

    products: list[Product] = []
    for item in products_data:
        if not isinstance(item, dict):
            raise ValueError("Cada produto deve ser um objeto JSON.")
        retailer = (item.get("retailer") or "").strip().lower()
        if retailer not in KNOWN_RETAILERS:
            raise ValueError(
                f"Produto '{item.get('name')}' precisa de retailer "
                f"em {KNOWN_RETAILERS}."
            )
        adapter = get_adapter(retailer)
        retailer_settings = {
            **{k: v for k, v in settings.items() if k != "retailers"},
            **(retailers_cfg.get(retailer) or {}),
        }
        products.append(adapter.normalize_product(item, retailer_settings))

    if not products:
        raise ValueError("Nenhum produto configurado.")

    settings.setdefault("cooldown_hours", DEFAULT_COOLDOWN_HOURS)
    settings["retailers"] = retailers_cfg
    return products, settings


def retailer_settings(settings: dict[str, Any], retailer: str) -> dict[str, Any]:
    base = {k: v for k, v in settings.items() if k != "retailers"}
    extra = (settings.get("retailers") or {}).get(retailer) or {}
    merged = {**base, **extra}
    # Convenience: top-level zip / retailer_slug still work for grocery adapters
    return merged


def resolve_headless(
    settings: dict[str, Any],
    retailer: str,
    cli_headless: bool | None,
    default_headless: bool,
) -> bool:
    if cli_headless is not None:
        return bool(cli_headless)
    # Preferência explícita do varejista (não herdar o headless global se definido)
    retailers_cfg = settings.get("retailers") or {}
    r_only = retailers_cfg.get(retailer) or {}
    if isinstance(r_only, dict) and "headless" in r_only:
        return bool(r_only["headless"])
    if "headless" in settings:
        return bool(settings["headless"])
    return default_headless


def resolve_cooldown(settings: dict[str, Any], cli_cooldown: float | None) -> float:
    if cli_cooldown is not None:
        return float(cli_cooldown)
    return float(settings.get("cooldown_hours", DEFAULT_COOLDOWN_HOURS))


def base_product_fields(data: dict[str, Any], retailer: str) -> dict[str, Any]:
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    if not url:
        raise ValueError("Cada produto precisa de 'url'.")
    target_price = optional_float(data.get("target_price"))
    if target_price is None:
        label = name or url
        raise ValueError(f"Produto '{label}' precisa de 'target_price'.")
    if target_price <= 0:
        raise ValueError(f"Produto '{name or url}': target_price deve ser > 0.")
    return {
        "retailer": retailer,
        "name": name,  # adapters podem preencher a partir da URL se vazio
        "url": url,
        "target_price": target_price,
        "min_discount_percent": optional_float(data.get("min_discount_percent")),
        "reference_price": optional_float(data.get("reference_price")),
        "raw": data,
    }
