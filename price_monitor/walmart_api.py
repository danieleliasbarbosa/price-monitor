"""Cliente Walmart via SerpApi (engine=walmart_product)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"

# ZIP → store_id conhecidos (Bay Area / usados no projeto).
# Sem store_id a SerpApi cai numa loja default distante e pode
# devolver só marketplace out-of-stock (preço errado).
ZIP_STORE_IDS: dict[str, str] = {
    "94080": "2280",  # South SF → Mountain View 94040 (mais próximo conhecido)
    "94040": "2280",  # Mountain View
    "94086": "4175",  # Sunnyvale
    "95035": "2119",  # Milpitas
    "95129": "2486",  # San Jose
    "94579": "5434",  # San Leandro
}


class WalmartApiError(RuntimeError):
    pass


def _settings_get(settings: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        val = settings.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def resolve_store_id(settings: dict[str, Any]) -> str | None:
    api_cfg = settings.get("api") if isinstance(settings.get("api"), dict) else {}
    merged = {**api_cfg, **settings}

    store_id = (
        os.getenv("SERPAPI_WALMART_STORE_ID", "").strip()
        or _settings_get(merged, "store_id", "storeId")
    )
    if store_id:
        return store_id

    zip_code = (
        os.getenv("SERPAPI_WALMART_ZIP", "").strip()
        or _settings_get(merged, "zip", "postal_code", "postalCode")
    )
    if zip_code:
        mapped = ZIP_STORE_IDS.get(zip_code.strip())
        if mapped:
            return mapped
    return None


def load_api_credentials(settings: dict[str, Any]) -> dict[str, str] | None:
    """
    Credenciais via env (preferido) ou retailers.walmart no JSON.

    Env:
      SERPAPI_API_KEY
      SERPAPI_WALMART_STORE_ID (opcional)
      SERPAPI_WALMART_ZIP (opcional; mapeia para store_id conhecido)
    JSON (retailers.walmart):
      serpapi_api_key / api_key
      store_id (recomendado — preço local)
      zip (fallback se store_id omitido e ZIP conhecido)
    """
    api_cfg = settings.get("api") if isinstance(settings.get("api"), dict) else {}
    merged = {**api_cfg, **settings}

    api_key = (
        os.getenv("SERPAPI_API_KEY", "").strip()
        or _settings_get(
            merged,
            "serpapi_api_key",
            "serp_api_key",
            "api_key",
            "apiKey",
        )
    )
    if not api_key:
        return None

    creds: dict[str, str] = {"api_key": api_key}
    store_id = resolve_store_id(settings)
    if store_id:
        creds["store_id"] = store_id
    return creds


def api_configured(settings: dict[str, Any]) -> bool:
    return load_api_credentials(settings) is not None


def fetch_item(item_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    creds = load_api_credentials(settings)
    if creds is None:
        raise WalmartApiError(
            "SerpApi is not configured for Walmart.\n"
            "Set SERPAPI_API_KEY (or retailers.walmart.serpapi_api_key)."
        )

    item_id = str(item_id).strip()
    if not item_id:
        raise WalmartApiError("Empty Walmart product_id.")

    params: dict[str, str] = {
        "engine": "walmart_product",
        "product_id": item_id,
        "api_key": creds["api_key"],
        "no_cache": "true",
    }
    if creds.get("store_id"):
        params["store_id"] = creds["store_id"]

    url = f"{SERPAPI_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "price-monitor/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            body = str(exc.reason)
        raise WalmartApiError(
            f"SerpApi HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise WalmartApiError(f"SerpApi network error: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WalmartApiError(f"Invalid SerpApi JSON: {raw[:200]}") from exc

    if not isinstance(data, dict):
        raise WalmartApiError(f"SerpApi: unexpected format: {type(data)}")

    if data.get("error"):
        raise WalmartApiError(f"SerpApi: {data['error']}")

    status = (data.get("search_metadata") or {}).get("status")
    if status and str(status).lower() not in {"success", "cached"}:
        raise WalmartApiError(f"SerpApi status={status}")

    product = data.get("product_result")
    if not isinstance(product, dict):
        raise WalmartApiError(
            "SerpApi: product_result missing "
            f"(product_id={item_id})."
        )

    # Anexa metadados úteis para log (não vêm só do product_result).
    location = (data.get("search_information") or {}).get("location")
    if isinstance(location, dict):
        product = {**product, "_serpapi_location": location}
    if creds.get("store_id"):
        product = {**product, "_serpapi_store_id": creds["store_id"]}
    return product


def parse_item_price(
    item: dict[str, Any],
) -> tuple[str | None, float | None, float | None]:
    """Retorna (title, current_price, list_price) a partir de product_result."""
    title = item.get("title") or item.get("name")
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = None

    current = None
    list_price = None

    # Preferir oferta Walmart.com (INTERNAL) quando existir na lista.
    walmart_offer = _preferred_offer_price(item)
    if walmart_offer is not None:
        current = walmart_offer

    price_map = item.get("price_map")
    if isinstance(price_map, dict):
        if current is None:
            current = _as_float(price_map.get("price"))
        was = price_map.get("was_price")
        if isinstance(was, dict):
            list_price = _as_float(was.get("price"))
        elif was is not None:
            list_price = _as_float(was)

    if current is None:
        offers = item.get("offers")
        if isinstance(offers, list):
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                current = _as_float(offer.get("price"))
                if current is not None:
                    break

    if current is not None and current <= 0:
        current = None
    if list_price is not None and list_price <= 0:
        list_price = None
    if (
        list_price is not None
        and current is not None
        and abs(list_price - current) < 0.001
    ):
        list_price = None

    return title, current, list_price


def describe_offer_context(item: dict[str, Any]) -> str:
    """Linha curta para log: loja / seller / estoque."""
    parts: list[str] = []
    store_id = item.get("_serpapi_store_id")
    loc = item.get("_serpapi_location")
    if isinstance(loc, dict):
        city = loc.get("city") or ""
        zip_code = loc.get("postal_code") or ""
        sid = loc.get("store_id") or store_id or ""
        label = ", ".join(p for p in (str(city), str(zip_code)) if p)
        if sid:
            label = f"{label} (store {sid})" if label else f"store {sid}"
        if label:
            parts.append(label)
    elif store_id:
        parts.append(f"store {store_id}")

    seller = item.get("seller_name") or item.get("seller_display_name")
    if seller:
        parts.append(str(seller))

    stock = item.get("in_stock")
    if stock is False:
        parts.append("OUT OF STOCK")
    elif stock is True:
        parts.append("in stock")

    loc_ship = (item.get("shipping_option") or {}).get("location")
    loc_del = (item.get("delivery_option") or {}).get("location")
    loc_hint = loc_del or loc_ship
    if loc_hint and not any(str(loc_hint) in p for p in parts):
        parts.append(str(loc_hint))

    return " | ".join(parts)


def _preferred_offer_price(item: dict[str, Any]) -> float | None:
    offers = item.get("offers")
    if not isinstance(offers, list):
        return None

    def is_walmart(offer: dict[str, Any]) -> bool:
        names = " ".join(
            str(offer.get(k) or "")
            for k in ("seller_name", "seller_display_name", "seller_type")
        ).lower()
        return "walmart" in names or offer.get("seller_type") == "INTERNAL"

    preferred = [o for o in offers if isinstance(o, dict) and is_walmart(o)]
    pool = preferred or [o for o in offers if isinstance(o, dict)]
    for offer in pool:
        price = _as_float(offer.get("price"))
        if price is not None and price > 0:
            return price
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
