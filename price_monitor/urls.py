"""Extrai IDs e normaliza URLs de Amazon, Safeway, Instacart, Target e Walmart."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse


def detect_retailer_from_url(url: str) -> str | None:
    """Infere o varejista pelo host da URL."""
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("amazon.com") or ".amazon." in host:
        return "amazon"
    if host.endswith("safeway.com"):
        return "safeway"
    if host.endswith("instacart.com"):
        return "instacart"
    if host.endswith("target.com"):
        return "target"
    if host.endswith("walmart.com"):
        return "walmart"
    return None


def amazon_asin_from_url(url: str) -> str | None:
    patterns = [
        r"/(?:dp|gp/product|product)/([A-Z0-9]{10})(?:[/?]|$)",
        r"[?&]asin=([A-Z0-9]{10})(?:&|$)",
    ]
    for pat in patterns:
        m = re.search(pat, url, re.I)
        if m:
            return m.group(1).upper()
    return None


def amazon_name_from_url(url: str) -> str | None:
    """Usa o slug amigável antes de /dp/ quando existir."""
    m = re.search(
        r"amazon\.[^/]+/([^/]+)/dp/[A-Z0-9]{10}",
        url,
        re.I,
    )
    if not m:
        return None
    slug = m.group(1).strip()
    if not slug or slug.lower() in {"dp", "gp", "product"}:
        return None
    name = slug.replace("-", " ").replace("_", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name or None


def amazon_canonical_url(asin: str, original: str | None = None) -> str:
    host = "www.amazon.com"
    if original:
        parsed = urlparse(original)
        if parsed.netloc:
            host = parsed.netloc
    return f"https://{host}/dp/{asin}"


def safeway_product_id_from_url(url: str) -> str | None:
    m = re.search(r"product-details\.(\d+)", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"/product/[^/]+/(\d+)", url, re.I)
    if m:
        return m.group(1)
    return None


def safeway_canonical_url(product_id: str, original: str | None = None) -> str:
    host = "www.safeway.com"
    if original:
        parsed = urlparse(original)
        if parsed.netloc:
            host = parsed.netloc
    return f"https://{host}/shop/product-details.{product_id}.html"


def instacart_product_id_from_url(url: str) -> str | None:
    m = re.search(r"/products/(\d+)", url, re.I)
    return m.group(1) if m else None


def instacart_name_from_url(url: str) -> str | None:
    m = re.search(r"/products/\d+-([^/?#]+)", url, re.I)
    if not m:
        return None
    slug = m.group(1).strip()
    name = slug.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", name).strip() or None


def instacart_retailer_slug_from_url(url: str) -> str | None:
    qs = parse_qs(urlparse(url).query)
    values = qs.get("retailerSlug") or qs.get("retailerslug")
    if values and values[0].strip():
        return values[0].strip()
    return None


def instacart_canonical_url(
    product_id: str,
    *,
    original: str | None = None,
    retailer_slug: str | None = None,
) -> str:
    host = "www.instacart.com"
    slug_tail = ""
    if original:
        parsed = urlparse(original)
        if parsed.netloc:
            host = parsed.netloc
        m = re.search(rf"/products/{re.escape(product_id)}-([^/?#]+)", original, re.I)
        if m:
            slug_tail = f"-{m.group(1)}"
    path = f"/products/{product_id}{slug_tail}"
    query = f"retailerSlug={retailer_slug}" if retailer_slug else ""
    return urlunparse(("https", host, path, "", query, ""))


def target_tcin_from_url(url: str) -> str | None:
    """Extrai TCIN de /-/A-12345678 ou A-12345678."""
    m = re.search(r"/A-(\d{6,})", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"[?&]tcin=(\d{6,})", url, re.I)
    if m:
        return m.group(1)
    return None


def target_preselect_from_url(url: str) -> str | None:
    qs = parse_qs(urlparse(url).query)
    values = qs.get("preselect")
    if values and values[0].strip():
        return values[0].strip()
    return None


def target_name_from_url(url: str) -> str | None:
    m = re.search(r"target\.[^/]+/p/([^/]+)/-/A-\d+", url, re.I)
    if not m:
        return None
    slug = m.group(1).strip()
    if not slug or slug == "-":
        return None
    name = slug.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", name).strip() or None


def target_canonical_url(
    tcin: str,
    *,
    original: str | None = None,
    preselect: str | None = None,
) -> str:
    host = "www.target.com"
    slug = "-"
    if original:
        parsed = urlparse(original)
        if parsed.netloc:
            host = parsed.netloc
        m = re.search(r"/p/([^/]+)/-/A-\d+", original, re.I)
        if m and m.group(1).strip():
            slug = m.group(1).strip()
    path = f"/p/{slug}/-/A-{tcin}"
    query = f"preselect={preselect}" if preselect else ""
    return urlunparse(("https", host, path, "", query, ""))


def walmart_product_id_from_url(url: str) -> str | None:
    """Extrai item ID de /ip/.../12345678 ou /ip/12345678."""
    m = re.search(r"/ip/(?:[^/]+/)?(\d{6,})(?:[/?#]|$)", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"[?&](?:itemId|item_id)=(\d{6,})", url, re.I)
    return m.group(1) if m else None


def walmart_name_from_url(url: str) -> str | None:
    m = re.search(r"/ip/([^/]+)/\d{6,}", url, re.I)
    if not m:
        return None
    slug = m.group(1).strip()
    if not slug or slug.isdigit():
        return None
    name = slug.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", name).strip() or None


def walmart_canonical_url(product_id: str, original: str | None = None) -> str:
    host = "www.walmart.com"
    slug = None
    if original:
        parsed = urlparse(original)
        if parsed.netloc:
            host = parsed.netloc
        m = re.search(rf"/ip/([^/]+)/{re.escape(product_id)}", original, re.I)
        if m and m.group(1).strip() and not m.group(1).isdigit():
            slug = m.group(1).strip()
    path = f"/ip/{slug}/{product_id}" if slug else f"/ip/{product_id}"
    return urlunparse(("https", host, path, "", "", ""))
