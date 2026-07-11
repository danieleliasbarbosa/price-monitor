from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Product:
    retailer: str
    name: str
    url: str
    target_price: float
    product_id: str | None = None
    asin: str | None = None
    retailer_slug: str | None = None
    min_discount_percent: float | None = None
    reference_price: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrapedProduct:
    title: str | None
    current_price: float | None
    list_price: float | None
    discount_percent: float | None


class SessionExpiredError(RuntimeError):
    """Retailer session needs re-authentication (e.g. Instacart OTP)."""


class ChallengeRequiredError(RuntimeError):
    """Interactive captcha/challenge required but headless is on."""
