from __future__ import annotations

import re
from typing import Any


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def parse_price(text: str | None, *, max_value: float = 1_000_000) -> float | None:
    """Extract a US-style monetary value from text."""
    if not text:
        return None

    cleaned = text.strip()
    if not cleaned:
        return None

    cleaned = cleaned.replace("\xa0", " ").replace("USD", "").replace("$", "")
    cleaned = cleaned.strip()
    cleaned = re.sub(r"[^\d,.\-]", "", cleaned)
    if not cleaned or cleaned in {".", ",", "-", "-.", "-,"}:
        return None

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(".") > cleaned.rfind(","):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    try:
        value = float(cleaned)
    except ValueError:
        return None

    if value <= 0 or value > max_value:
        return None
    return value


def calc_discount(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None or reference <= 0:
        return None
    if current >= reference:
        return 0.0
    return round((1.0 - (current / reference)) * 100.0, 2)


def should_alert(product, scraped) -> tuple[bool, str]:
    reasons: list[str] = []

    if (
        scraped.current_price is not None
        and scraped.current_price <= product.target_price
    ):
        reasons.append(
            f"price ${scraped.current_price:.2f} <= target ${product.target_price:.2f}"
        )

    if (
        product.min_discount_percent is not None
        and scraped.discount_percent is not None
        and scraped.discount_percent >= product.min_discount_percent
    ):
        reasons.append(
            f"discount {scraped.discount_percent:.1f}% >= minimum "
            f"{product.min_discount_percent:.1f}%"
        )

    if not reasons:
        return False, ""
    return True, "; ".join(reasons)
