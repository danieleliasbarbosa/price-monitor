from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import Page

from price_monitor.config import base_product_fields
from price_monitor.models import ChallengeRequiredError, Product, ScrapedProduct
from price_monitor.prices import parse_price
from price_monitor.adapters.base import NAV_TIMEOUT_MS
from price_monitor.urls import (
    amazon_asin_from_url,
    amazon_canonical_url,
    amazon_name_from_url,
)

PAGE_SETTLE_MS = 2_500


class AmazonAdapter:
    name = "amazon"
    brand = "Amazon"
    default_timezone = "America/New_York"
    default_headless = False
    supports_auth = False

    def product_key(self, product: Product) -> str:
        if product.asin:
            return f"asin:{product.asin}"
        return f"url:{product.url}"

    def normalize_product(
        self, data: dict[str, Any], settings: dict[str, Any]
    ) -> Product:
        fields = base_product_fields(data, self.name)
        raw_url = fields["url"]

        asin = data.get("asin")
        if asin is not None:
            asin = str(asin).strip() or None
        if asin is None:
            asin = amazon_asin_from_url(raw_url)
        if not asin:
            raise ValueError(
                f"Não foi possível extrair o ASIN da URL Amazon: {raw_url}"
            )

        fields["url"] = amazon_canonical_url(asin, raw_url)
        if not fields["name"]:
            fields["name"] = amazon_name_from_url(raw_url) or f"Amazon {asin}"

        # Amazon: só target_price (sem lista/desconto)
        fields["min_discount_percent"] = None
        fields["reference_price"] = None

        return Product(**fields, asin=asin)

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
            if self._looks_like_challenge(page) or "amazon." not in (page.url or "").lower():
                page.goto(product.url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(PAGE_SETTLE_MS)
            if self._looks_like_challenge(page):
                self._wait_challenge(page, headless=headless)

        title = self._extract_title(page)
        if self._is_unavailable(page):
            print("  Disponibilidade: indisponível / out of stock")
            return ScrapedProduct(title, None, None, None)

        current = self._extract_current_price(page)
        return ScrapedProduct(title, current, None, None)

    def _is_unavailable(self, page: Page) -> bool:
        for sel in [
            "#buybox",
            "#desktop_buybox",
            "#outOfStock",
            "#availability",
            "#availability_feature_div",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                text = (loc.inner_text(timeout=1500) or "").lower()
            except Exception:
                continue
            if any(
                p in text
                for p in (
                    "currently unavailable",
                    "out of stock",
                    "temporarily out of stock",
                    "we don't know when or if this item will be back",
                )
            ):
                return True
        try:
            body = (page.locator("#centerCol").inner_text(timeout=2000) or "").lower()
        except Exception:
            body = ""
        return "currently unavailable" in body

    def run_auth(self, page: Page) -> int:
        print("Amazon não usa subcomando auth. Rode check com --no-headless se houver captcha.")
        return 1

    def _wait_challenge(self, page: Page, *, headless: bool) -> None:
        if headless:
            raise ChallengeRequiredError(
                "Amazon pediu captcha em headless. Rode:\n"
                "  python -m price_monitor check --retailer amazon --no-headless"
            )
        print("\n" + "=" * 60)
        print("AVISO: Amazon pediu captcha / verificação.")
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
        if any(
            h in url
            for h in (
                "validatecaptcha",
                "/errors/validatecaptcha",
                "sorry/index",
                "ap/cvf",
                "robot check",
            )
        ):
            return True
        for sel in [
            "#captchacharacters",
            "form[action*='validateCaptcha']",
            "img[src*='captcha']",
            "#captcha-container",
            "text=Enter the characters you see below",
        ]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
        try:
            title = (page.title() or "").lower()
        except Exception:
            title = ""
        return "robot" in title or "captcha" in title or "validate" in title

    def _extract_title(self, page: Page) -> str | None:
        for sel in ["#productTitle", "#title span", "h1#title", "span#productTitle"]:
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

    def _price_from_whole_fraction(self, scope) -> float | None:
        try:
            whole_loc = scope.locator(".a-price-whole").first
            if whole_loc.count() == 0:
                return None
            whole_text = whole_loc.inner_text(timeout=1500)
            fraction = "00"
            frac_loc = scope.locator(".a-price-fraction").first
            if frac_loc.count() > 0:
                fraction = (frac_loc.inner_text(timeout=1000) or "00").strip()
            whole_digits = re.sub(r"[^\d]", "", whole_text or "")
            frac_digits = re.sub(r"[^\d]", "", fraction)[:2] or "00"
            if not whole_digits:
                return None
            return parse_price(f"{whole_digits}.{frac_digits}")
        except Exception:
            return None

    def _price_from_offscreen(self, scope) -> float | None:
        try:
            offs = scope.locator(".a-price .a-offscreen, span.a-offscreen")
            count = offs.count()
        except Exception:
            return None
        candidates: list[float] = []
        for i in range(min(count, 12)):
            try:
                node = offs.nth(i)
                text = node.inner_text(timeout=1000)
                price = parse_price(text)
                if price is None:
                    continue
                # Evita preço unitário ($0.01 / fl oz) e "You Save"
                try:
                    nearby = node.evaluate(
                        """(el) => {
                          const root = el.closest('#apex_desktop, #corePrice_feature_div, #centerCol, body') || el.parentElement;
                          const block = (el.closest('.a-section, .a-price, tr, span, div') || el).parentElement;
                          return ((block && block.innerText) || (root && root.innerText) || '').slice(0, 220).toLowerCase();
                        }"""
                    )
                except Exception:
                    nearby = ""
                nearby = nearby or ""
                if any(
                    u in nearby
                    for u in (
                        "/ fluid ounce",
                        "/fl oz",
                        "per fluid ounce",
                        "per ounce",
                        "/ count",
                        "unit price",
                    )
                ) and price < 1.0:
                    continue
                candidates.append(price)
            except Exception:
                continue
        if not candidates:
            return None
        # Prefere preço de produto real (ignora centavos de unit price se houver alternativa)
        plausible = [p for p in candidates if p >= 1.0]
        return plausible[0] if plausible else candidates[0]

    def _extract_current_price(self, page: Page) -> float | None:
        for sel in [
            "#corePrice_feature_div",
            "#corePriceDisplay_desktop_feature_div",
            "#apex_desktop_newAccordionRow",
            "#apex_desktop",
            "#price",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            ".a-price.priceToPay",
        ]:
            try:
                scope = page.locator(sel).first
                if scope.count() == 0:
                    continue
                price = self._price_from_offscreen(scope) or self._price_from_whole_fraction(
                    scope
                )
                if price is not None and price >= 1.0:
                    return price
            except Exception:
                continue
        # Último recurso: #centerCol, mas só preços >= $1
        try:
            scope = page.locator("#centerCol").first
            if scope.count() > 0:
                price = self._price_from_offscreen(scope)
                if price is not None and price >= 1.0:
                    return price
        except Exception:
            pass
        return None
