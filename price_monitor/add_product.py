"""Adiciona produtos ao produtos.json a partir de URLs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from price_monitor.adapters import get_adapter
from price_monitor.config import retailer_settings
from price_monitor.urls import detect_retailer_from_url


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
        raise ValueError("O JSON de configuração deve ser uma lista ou um objeto.")
    raw.setdefault("products", raw.get("produtos") or [])
    if "produtos" in raw and "products" not in raw:
        raw["products"] = raw.pop("produtos")
    if not isinstance(raw["products"], list):
        raise ValueError("'products' deve ser uma lista.")
    return raw


def _save_raw(config_path: Path, raw: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(raw, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _parse_target_price(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("target_price é obrigatório.")
    try:
        price = float(str(value).replace(",", ".").replace("$", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"target_price inválido: {value}") from exc
    if price <= 0:
        raise ValueError("target_price deve ser > 0.")
    return price


def _entry_from_product(
    *,
    retailer: str,
    url: str,
    target_price: float,
    min_discount_percent: float | None = None,
    reference_price: float | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "retailer": retailer,
        "url": url,
        "target_price": target_price,
    }
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
    min_discount_percent: float | None = None,
    reference_price: float | None = None,
    retailer: str | None = None,
) -> tuple[str, dict[str, Any], bool]:
    """
    Adiciona ou atualiza um produto no JSON.

    Retorna (ação, entry, created) onde ação é 'added' ou 'updated'.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("URL vazia.")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    detected = detect_retailer_from_url(url)
    retailer = (retailer or detected or "").strip().lower()
    if not retailer:
        raise ValueError(
            f"Não foi possível detectar o varejista da URL:\n  {url}\n"
            "Use --retailer amazon|safeway|instacart|target"
        )
    if detected and retailer != detected:
        raise ValueError(
            f"URL parece ser de '{detected}', mas --retailer={retailer} foi informado."
        )

    if target_price is None:
        raise ValueError(
            "target_price é obrigatório. Use --target-price ou informe no prompt."
        )
    target_price = _parse_target_price(target_price)

    adapter = get_adapter(retailer)
    raw = _load_raw(config_path)
    settings = retailer_settings(raw, retailer)

    draft = _entry_from_product(
        retailer=retailer,
        url=url,
        target_price=target_price,
        min_discount_percent=min_discount_percent,
        reference_price=reference_price,
    )
    product = adapter.normalize_product(draft, settings)
    entry = _entry_from_product(
        retailer=retailer,
        url=product.url,
        target_price=product.target_price,
        min_discount_percent=product.min_discount_percent,
        reference_price=product.reference_price,
    )
    new_key = adapter.product_key(product)

    products: list[Any] = raw["products"]
    for i, existing in enumerate(products):
        if not isinstance(existing, dict):
            continue
        existing_retailer = (existing.get("retailer") or "").strip().lower()
        if existing_retailer != retailer:
            continue
        try:
            existing_product = adapter.normalize_product(
                {**existing, "target_price": existing.get("target_price", target_price)},
                settings,
            )
        except Exception:
            continue
        if adapter.product_key(existing_product) != new_key:
            continue
        updated = dict(existing)
        updated["url"] = entry["url"]
        updated["retailer"] = retailer
        updated["target_price"] = target_price
        if min_discount_percent is not None:
            updated["min_discount_percent"] = min_discount_percent
        if reference_price is not None:
            updated["reference_price"] = reference_price
        products[i] = updated
        _save_raw(config_path, raw)
        return "updated", updated, False

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
    print("Cole a URL do produto (Enter vazio para sair).")
    print("Varejistas: amazon, safeway, instacart, target")
    print("target_price é obrigatório.\n")
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
                price_raw = input(f"Preço alvo{hint}: ").strip()
            except EOFError:
                print()
                return 0 if added else 1

            if not price_raw and default_target_price is not None:
                target_price = default_target_price
                break
            if not price_raw:
                print("  target_price é obrigatório.")
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
            print(f"  Erro: {exc}")
            continue

        label = "Adicionado" if action == "added" else "Atualizado"
        print(
            f"  {label}: {entry['retailer']} | {entry['url']} | "
            f"${entry['target_price']:.2f}"
        )
        added += 1

    print(f"\nPronto. {added} produto(s) processado(s).")
    return 0
