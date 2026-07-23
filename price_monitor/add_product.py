"""Adiciona produtos ao produtos.json a partir de URLs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from price_monitor.adapters import get_adapter
from price_monitor.config import KNOWN_RETAILERS, retailer_settings
from price_monitor.urls import detect_retailer_from_url, retailer_slug_from_url


def _load_raw(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {
            "cooldown_hours": 24,
            "headless": True,
            "retailers": {},
            "products": [],
        }
    with config_path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, list):
        return {"products": raw}
    if not isinstance(raw, dict):
        raise ValueError("Config JSON must be a list or an object.")
    raw.setdefault("products", raw.get("produtos") or [])
    if "produtos" in raw and "products" not in raw:
        raw["products"] = raw.pop("produtos")
    if not isinstance(raw["products"], list):
        raise ValueError("'products' must be a list.")
    return raw


def _save_raw(config_path: Path, raw: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(raw, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _parse_target_price(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("target_price is required.")
    try:
        price = float(str(value).replace(",", ".").replace("$", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid target_price: {value}") from exc
    if price <= 0:
        raise ValueError("target_price must be > 0.")
    return price


def _normalize_url_key(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    return value.rstrip("/").lower()


def _entry_from_product(
    *,
    retailer: str,
    url: str,
    target_price: float,
    name: str | None = None,
    min_discount_percent: float | None = None,
    reference_price: float | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "retailer": retailer,
        "url": url,
        "target_price": target_price,
    }
    if name:
        entry["name"] = name
    if min_discount_percent is not None:
        entry["min_discount_percent"] = min_discount_percent
    if reference_price is not None:
        entry["reference_price"] = reference_price
    return entry


def add_url_to_config(
    config_path: Path,
    url: str,
    *,
    target_price: float | None = None,
    name: str | None = None,
    min_discount_percent: float | None = None,
    reference_price: float | None = None,
    retailer: str | None = None,
) -> tuple[str, dict[str, Any], bool]:
    """
    Adiciona um produto no JSON.

    Retorna (ação, entry, created) onde ação é 'added'.
    Levanta ValueError se a URL/produto já existir na lista.
    Lojas ainda não suportadas são salvas como pending (disponível em 24h).
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Empty URL.")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    name_value = re.sub(r"\s+", " ", (name or "").strip())
    if len(name_value) > 200:
        raise ValueError("Product name is too long (max. 200 characters).")

    if target_price is None:
        raise ValueError(
            "target_price is required. Use --target-price or enter it at the prompt."
        )
    target_price = _parse_target_price(target_price)

    detected = detect_retailer_from_url(url)
    retailer = (retailer or detected or "").strip().lower()
    if detected and retailer and retailer != detected:
        raise ValueError(
            f"URL looks like '{detected}', but --retailer={retailer} was provided."
        )

    raw = _load_raw(config_path)
    products: list[Any] = raw["products"]
    incoming_urls = {_normalize_url_key(url)}

    # Loja ainda não integrada: salva como pendente.
    if not retailer or retailer not in KNOWN_RETAILERS:
        slug = retailer or retailer_slug_from_url(url)
        if not slug:
            raise ValueError(
                "Could not identify the store from the URL. "
                "Use a valid URL (e.g. amazon.com, walmart.com)."
            )
        for existing in products:
            if not isinstance(existing, dict):
                continue
            existing_url = _normalize_url_key(str(existing.get("url") or ""))
            if existing_url and existing_url in incoming_urls:
                raise ValueError("That URL is already in your product list.")
        available_after = (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat()
        entry: dict[str, Any] = {
            "retailer": slug,
            "url": url,
            "target_price": target_price,
            "pending": True,
            "available_after": available_after,
        }
        if name_value:
            entry["name"] = name_value
        else:
            entry["name"] = slug
        products.append(entry)
        _save_raw(config_path, raw)
        return "added", entry, True

    adapter = get_adapter(retailer)
    settings = retailer_settings(raw, retailer)

    draft = _entry_from_product(
        retailer=retailer,
        url=url,
        target_price=target_price,
        name=name_value or None,
        min_discount_percent=min_discount_percent,
        reference_price=reference_price,
    )
    product = adapter.normalize_product(draft, settings)
    entry = _entry_from_product(
        retailer=retailer,
        url=product.url,
        target_price=product.target_price,
        name=name_value or None,
        min_discount_percent=product.min_discount_percent,
        reference_price=product.reference_price,
    )
    incoming_urls.add(_normalize_url_key(product.url))

    for existing in products:
        if not isinstance(existing, dict):
            continue
        existing_url = _normalize_url_key(str(existing.get("url") or ""))
        if existing_url and existing_url in incoming_urls:
            raise ValueError("That URL is already in your product list.")

    products.append(entry)
    _save_raw(config_path, raw)
    return "added", entry, True


def prompt_add_interactive(
    config_path: Path,
    *,
    default_target_price: float | None = None,
) -> int:
    """Loop interativo: cola URL + preço alvo até linha vazia."""
    print(f"Config: {config_path}")
    print("Paste the product URL (empty Enter to exit).")
    print("Retailers: amazon, safeway, instacart, target")
    print("target_price is required.\n")
    added = 0
    while True:
        try:
            url = input("URL: ").strip()
        except EOFError:
            print()
            break
        if not url:
            break

        target_price: float | None = None
        while target_price is None:
            try:
                hint = (
                    f" [{default_target_price}]"
                    if default_target_price is not None
                    else ""
                )
                price_raw = input(f"Target price{hint}: ").strip()
            except EOFError:
                print()
                return 0 if added else 1

            if not price_raw and default_target_price is not None:
                target_price = default_target_price
                break
            if not price_raw:
                print("  target_price is required.")
                continue
            try:
                target_price = _parse_target_price(price_raw)
            except ValueError as exc:
                print(f"  {exc}")

        try:
            action, entry, _ = add_url_to_config(
                config_path,
                url,
                target_price=target_price,
            )
        except ValueError as exc:
            print(f"  Error: {exc}")
            continue

        print(
            f"  Added: {entry['retailer']} | {entry['url']} | "
            f"${entry['target_price']:.2f}"
        )
        added += 1

    print(f"\nDone. {added} product(s) processed.")
    return 0
