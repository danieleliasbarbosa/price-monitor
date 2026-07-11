from __future__ import annotations

import json
import re
import time
from typing import Any

from playwright.sync_api import Page

from price_monitor.adapters.base import NAV_TIMEOUT_MS
from price_monitor.config import base_product_fields
from price_monitor.models import ChallengeRequiredError, Product, ScrapedProduct
from price_monitor.prices import parse_price
from price_monitor.urls import (
    target_canonical_url,
    target_name_from_url,
    target_preselect_from_url,
    target_tcin_from_url,
)

PAGE_SETTLE_MS = 3_000


class TargetAdapter:
    name = "target"
    brand = "Target"
    default_timezone = "America/Chicago"
    default_headless = True
    supports_auth = False

    def product_key(self, product: Product) -> str:
        if product.product_id:
            preselect = (product.raw or {}).get("_preselect")
            if preselect:
                return f"tcin:{product.product_id}:preselect:{preselect}"
            return f"tcin:{product.product_id}"
        return f"url:{product.url}"

    def normalize_product(
        self, data: dict[str, Any], settings: dict[str, Any]
    ) -> Product:
        fields = base_product_fields(data, self.name)
        raw_url = fields["url"]

        product_id = data.get("product_id") or data.get("tcin") or data.get("id")
        if product_id is not None:
            product_id = str(product_id).strip().lstrip("A-a-") or None
        if product_id is None:
            product_id = target_tcin_from_url(raw_url)
        if not product_id:
            raise ValueError(
                f"Não foi possível extrair o TCIN da URL Target: {raw_url}"
            )

        preselect = data.get("preselect") or target_preselect_from_url(raw_url)
        if preselect is not None:
            preselect = str(preselect).strip() or None

        fields["url"] = target_canonical_url(
            product_id, original=raw_url, preselect=preselect
        )
        if not fields["name"]:
            fields["name"] = target_name_from_url(raw_url) or f"Target {product_id}"

        raw = dict(fields.get("raw") or data)
        if preselect:
            raw["_preselect"] = preselect
        fields["raw"] = raw

        fields["min_discount_percent"] = None
        fields["reference_price"] = None

        return Product(**fields, product_id=product_id)

    def prepare_session(
        self, page: Page, settings: dict[str, Any], *, headless: bool
    ) -> None:
        return None

    def scrape(
        self,
        page: Page,
        product: Product,
        settings: dict[str, Any],
        *,
        headless: bool,
        set_location: bool = False,
    ) -> ScrapedProduct:
        page.goto(product.url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_SETTLE_MS)

        if self._looks_like_challenge(page):
            self._wait_challenge(page, headless=headless)
            page.goto(product.url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(PAGE_SETTLE_MS)
            if self._looks_like_challenge(page):
                self._wait_challenge(page, headless=headless)

        title_ld, price_ld, list_ld = self._from_json_ld(page)
        title = self._extract_title(page) or title_ld
        current = self._extract_current_price(page) or price_ld
        list_price = self._extract_list_price(page) or list_ld
        if list_price is not None and current is not None and abs(list_price - current) < 0.001:
            list_price = None
        return ScrapedProduct(title, current, list_price, None)

    def run_auth(self, page: Page) -> int:
        print("Target não usa subcomando auth. Use check --no-headless se houver challenge.")
        return 1

    def _wait_challenge(self, page: Page, *, headless: bool) -> None:
        if headless:
            raise ChallengeRequiredError(
                "Target pediu verificação em headless. Rode:\n"
                "  python -m price_monitor check --retailer target --no-headless"
            )
        print("\n" + "=" * 60)
        print("AVISO: Target pediu captcha / verificação.")
        print("Resolva no navegador e pressione Enter.")
        print("=" * 60 + "\n")
        try:
            input("Pressione Enter após resolver... ")
        except EOFError:
            time.sleep(60)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception:
            pass
        page.wait_for_timeout(PAGE_SETTLE_MS)

    def _looks_like_challenge(self, page: Page) -> bool:
        url = (page.url or "").lower()
        if any(h in url for h in ("captcha", "challenge", "blocked", "access-denied")):
            return True
        try:
            body = (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            body = ""
        if any(
            p in body
            for p in (
                "access denied",
                "verify you are a human",
                "are you a robot",
                "press & hold",
                "checking your browser",
            )
        ):
            return True
        return False

    def _from_json_ld(self, page: Page):
        try:
            scripts = page.locator('script[type="application/ld+json"]')
            count = scripts.count()
        except Exception:
            return None, None, None
        for i in range(count):
            try:
                data = json.loads(scripts.nth(i).inner_text(timeout=1500))
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                nodes = item.get("@graph") if isinstance(item, dict) else None
                candidates = nodes if isinstance(nodes, list) else [item]
                for node in candidates:
                    if not isinstance(node, dict):
                        continue
                    t = node.get("@type")
                    types = t if isinstance(t, list) else [t]
                    if not any(str(x).lower() == "product" for x in types):
                        continue
                    title = node.get("name")
                    title = title.strip() if isinstance(title, str) else None
                    offers = node.get("offers")
                    if isinstance(offers, list) and offers:
                        offers = offers[0]
                    current = high = None
                    if isinstance(offers, dict):
                        current = parse_price(
                            str(offers.get("price") or offers.get("lowPrice") or ""),
                            max_value=10_000,
                        )
                        high = parse_price(
                            str(offers.get("highPrice") or ""), max_value=10_000
                        )
                    if title or current is not None:
                        return title, current, high
        return None, None, None

    def _extract_title(self, page: Page) -> str | None:
        for sel in [
            '[data-test="product-title"]',
            'h1[data-test="product-title"]',
            "h1",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                text = (loc.inner_text(timeout=2000) or "").strip()
                if text:
                    return re.sub(r"\s+", " ", text)
            except Exception:
                continue
        return None

    def _extract_current_price(self, page: Page) -> float | None:
        for sel in [
            '[data-test="product-price"]',
            '[data-test="current-price"]',
            'span[data-test="current-price"] span',
            '[data-test="product-price"] span',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                price = parse_price(loc.inner_text(timeout=1500), max_value=10_000)
                if price is not None:
                    return price
            except Exception:
                continue
        try:
            body = page.locator("main").inner_text(timeout=3000)
        except Exception:
            try:
                body = page.locator("body").inner_text(timeout=3000)
            except Exception:
                body = ""
        m = re.search(r"\$\s*(\d+\.\d{2})", body)
        return parse_price(m.group(1), max_value=10_000) if m else None

    def _extract_list_price(self, page: Page) -> float | None:
        for sel in [
            '[data-test="comparison-price"]',
            '[data-test="product-price-reg"]',
            "span[data-test='comparison-price'] span",
            "del",
            "s",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                price = parse_price(loc.inner_text(timeout=1500), max_value=10_000)
                if price is not None:
                    return price
            except Exception:
                continue
        return None
