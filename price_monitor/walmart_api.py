"""Cliente da Walmart Affiliate Marketing API (walmart.io)."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

AFFILIATE_ITEMS_URL = (
    "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/items"
)


class WalmartApiError(RuntimeError):
    pass


def _settings_get(settings: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        val = settings.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def load_api_credentials(settings: dict[str, Any]) -> dict[str, str] | None:
    """
    Credenciais via env (preferido) ou retailers.walmart no JSON.

    Env:
      WALMART_CONSUMER_ID
      WALMART_KEY_VERSION          (default: 1)
      WALMART_PUBLISHER_ID         (Impact Radius)
      WALMART_PRIVATE_KEY          (PEM completo) OU
      WALMART_PRIVATE_KEY_PATH     (arquivo .pem)
    """
    api_cfg = settings.get("api") if isinstance(settings.get("api"), dict) else {}
    merged = {**api_cfg, **settings}

    consumer_id = (
        os.getenv("WALMART_CONSUMER_ID", "").strip()
        or _settings_get(merged, "consumer_id", "consumerId", "WM_CONSUMER_ID")
    )
    publisher_id = (
        os.getenv("WALMART_PUBLISHER_ID", "").strip()
        or _settings_get(merged, "publisher_id", "publisherId", "impact_id", "impactId")
    )
    key_version = (
        os.getenv("WALMART_KEY_VERSION", "").strip()
        or _settings_get(merged, "key_version", "keyVersion", "WM_SEC_KEY_VERSION")
        or "1"
    )

    private_pem = os.getenv("WALMART_PRIVATE_KEY", "").strip()
    key_path = (
        os.getenv("WALMART_PRIVATE_KEY_PATH", "").strip()
        or _settings_get(merged, "private_key_path", "privateKeyPath")
    )
    if not private_pem and key_path:
        path = Path(key_path)
        if not path.is_file():
            return None
        private_pem = path.read_text(encoding="utf-8").strip()
    if not private_pem:
        private_pem = _settings_get(merged, "private_key", "privateKey") or ""

    if not consumer_id or not private_pem or not publisher_id:
        return None

    # Normaliza PEM se veio com \n literais
    if "\\n" in private_pem and "BEGIN" in private_pem:
        private_pem = private_pem.replace("\\n", "\n")

    return {
        "consumer_id": consumer_id,
        "publisher_id": publisher_id,
        "key_version": str(key_version),
        "private_key_pem": private_pem,
    }


def api_configured(settings: dict[str, Any]) -> bool:
    return load_api_credentials(settings) is not None


def _sign_headers(creds: dict[str, str]) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    payload = (
        f"{creds['consumer_id']}\n"
        f"{timestamp}\n"
        f"{creds['key_version']}\n"
    )
    private_key = serialization.load_pem_private_key(
        creds["private_key_pem"].encode("utf-8"),
        password=None,
    )
    signature = private_key.sign(
        payload.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return {
        "WM_CONSUMER.ID": creds["consumer_id"],
        "WM_CONSUMER.INTIMESTAMP": timestamp,
        "WM_SEC.KEY_VERSION": creds["key_version"],
        "WM_SEC.AUTH_SIGNATURE": base64.b64encode(signature).decode("ascii"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def fetch_item(item_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    creds = load_api_credentials(settings)
    if creds is None:
        raise WalmartApiError(
            "Walmart Affiliate API não configurada.\n"
            "Defina WALMART_CONSUMER_ID + WALMART_PRIVATE_KEY_PATH "
            "(e WALMART_PUBLISHER_ID)."
        )

    item_id = str(item_id).strip()
    query = urllib.parse.urlencode({"publisherId": creds["publisher_id"]})
    url = f"{AFFILIATE_ITEMS_URL}/{urllib.parse.quote(item_id)}?{query}"
    headers = _sign_headers(creds)
    # Evita resposta gzip binária ilegível em erros
    headers["Accept-Encoding"] = "identity"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_bytes = resp.read()
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
                import gzip

                raw_bytes = gzip.decompress(raw_bytes)
            raw = raw_bytes.decode("utf-8")
    except urllib.error.HTTPError as exc:
        body_bytes = b""
        try:
            body_bytes = exc.read()
        except Exception:
            pass
        if body_bytes[:2] == b"\x1f\x8b":
            import gzip

            try:
                body_bytes = gzip.decompress(body_bytes)
            except Exception:
                pass
        body = ""
        try:
            body = body_bytes.decode("utf-8", errors="replace")[:800]
        except Exception:
            body = repr(body_bytes[:200])
        raise WalmartApiError(
            f"Walmart API HTTP {exc.code}: {body or exc.reason}\n"
            "Confira: Consumer ID + chave privada do MESMO ambiente "
            "(Sandbox vs Production) e Key Version no dashboard."
        ) from exc
    except urllib.error.URLError as exc:
        raise WalmartApiError(f"Walmart API rede: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WalmartApiError(f"Walmart API JSON inválido: {raw[:200]}") from exc

    # Resposta pode ser objeto item, {items:[...]} ou lista
    if isinstance(data, list) and data:
        item = data[0]
    elif isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list) and items:
            item = items[0]
        else:
            item = data
    else:
        raise WalmartApiError(f"Walmart API: formato inesperado: {type(data)}")

    if not isinstance(item, dict):
        raise WalmartApiError("Walmart API: item inválido na resposta.")
    return item


def parse_item_price(item: dict[str, Any]) -> tuple[str | None, float | None, float | None]:
    """Retorna (title, current_price, list_price)."""
    title = item.get("name") or item.get("title")
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = None

    current = None
    for key in ("salePrice", "price", "currentPrice", "offerPrice"):
        if key in item and item[key] is not None:
            try:
                current = float(item[key])
                break
            except (TypeError, ValueError):
                continue

    list_price = None
    for key in ("msrp", "listPrice", "wasPrice"):
        if key in item and item[key] is not None:
            try:
                list_price = float(item[key])
                break
            except (TypeError, ValueError):
                continue

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
