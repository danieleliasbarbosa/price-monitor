from __future__ import annotations

from typing import Any, Protocol

from playwright.sync_api import Page

from price_monitor.models import Product, ScrapedProduct

NAV_TIMEOUT_MS = 60_000


class RetailerAdapter(Protocol):
    name: str
    brand: str
    default_timezone: str
    default_headless: bool
    supports_auth: bool

    def product_key(self, product: Product) -> str: ...

    def normalize_product(
        self, data: dict[str, Any], settings: dict[str, Any]
    ) -> Product: ...

    def prepare_session(
        self, page: Page, settings: dict[str, Any], *, headless: bool
    ) -> None: ...

    def scrape(
        self,
        page: Page,
        product: Product,
        settings: dict[str, Any],
        *,
        headless: bool,
        set_location: bool = False,
    ) -> ScrapedProduct: ...

    def run_auth(self, page: Page) -> int: ...
