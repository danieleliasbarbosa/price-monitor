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
    walmart_canonical_url,
    walmart_name_from_url,
    walmart_product_id_from_url,
)
from price_monitor.walmart_api import (
    WalmartApiError,
    api_configured,
    describe_offer_context,
    fetch_item,
    parse_item_price,
)

PAGE_SETTLE_MS = 3_500
CHALLENGE_WAIT_MS = 90_000
HEADED_CHALLENGE_WAIT_MS = 300_000
CHALLENGE_POLL_MS = 2_000
WARM_URL = "https://www.walmart.com/"


class WalmartAdapter:
    name = "walmart"
    brand = "Walmart"
    default_timezone = "America/Chicago"
    default_headless = True
    supports_auth = False

    def product_key(self, product: Product) -> str:
        if product.product_id:
            return f"id:{product.product_id}"
        return f"url:{product.url}"

    def can_use_api(self, settings: dict[str, Any]) -> bool:
        return api_configured(settings)

    def normalize_product(
        self, data: dict[str, Any], settings: dict[str, Any]
    ) -> Product:
        fields = base_product_fields(data, self.name)
        raw_url = fields["url"]

        product_id = data.get("product_id") or data.get("item_id") or data.get("id")
        if product_id is not None:
            product_id = str(product_id).strip() or None
        if product_id is None:
            product_id = walmart_product_id_from_url(raw_url)
        if not product_id:
            raise ValueError(
                f"Não foi possível extrair o product_id da URL Walmart: {raw_url}"
            )

        fields["url"] = walmart_canonical_url(product_id, raw_url)
        if not fields["name"]:
            fields["name"] = walmart_name_from_url(raw_url) or f"Walmart {product_id}"

        fields["min_discount_percent"] = None
        fields["reference_price"] = None

        return Product(**fields, product_id=product_id)

    def prepare_session(
        self, page: Page, settings: dict[str, Any], *, headless: bool
    ) -> None:
        if self.can_use_api(settings):
            return
        try:
            page.goto(WARM_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(PAGE_SETTLE_MS)
            if self._looks_like_challenge(page):
                print("  Challenge na home Walmart — aguardando...")
                if not self._wait_challenge_auto(page, timeout_ms=CHALLENGE_WAIT_MS):
                    if not headless:
                        self._wait_challenge(page, headless=False)
        except Exception:
            pass

    def scrape_via_api(
        self, product: Product, settings: dict[str, Any]
    ) -> ScrapedProduct:
        if not product.product_id:
            raise WalmartApiError("Produto Walmart sem product_id.")
        print("  Fonte: SerpApi (walmart_product)")
        item = fetch_item(product.product_id, settings)
        ctx = describe_offer_context(item)
        if ctx:
            print(f"  Contexto: {ctx}")
        title, current, list_price = parse_item_price(item)
        if not title:
            title = product.name
        if item.get("in_stock") is False:
            print(
                "  Aviso: SerpApi marca sem estoque nesta loja — "
                "preço pode ser de marketplace. Ajuste store_id/zip."
            )
        return ScrapedProduct(title, current, list_price, None)

    def scrape(
        self,
        page: Page,
        product: Product,
        settings: dict[str, Any],
        *,
        headless: bool,
        set_location: bool = False,
    ) -> ScrapedProduct:
        # Preferência: API (sem PerimeterX)
        if self.can_use_api(settings):
            try:
                return self.scrape_via_api(product, settings)
            except WalmartApiError as exc:
                allow_browser = bool(settings.get("browser_fallback", False))
                if not allow_browser:
                    raise ChallengeRequiredError(
                        f"{exc}\n"
                        "Configure SERPAPI_API_KEY ou ative "
                        "retailers.walmart.browser_fallback=true "
                        "(PerimeterX costuma bloquear)."
                    ) from exc
                print(f"  SerpApi falhou ({exc}); tentando browser...")

        self._open_product(page, product.url, headless=headless)
        title_ld, price_ld, list_ld = self._from_json_ld(page)
        title_nx, price_nx, list_nx = self._from_next_data(page, product.product_id)
        title = self._extract_title(page) or title_ld or title_nx
        current = self._extract_current_price(page) or price_ld or price_nx
        list_price = self._extract_list_price(page) or list_ld or list_nx
        if list_price is not None and current is not None and abs(list_price - current) < 0.001:
            list_price = None
        return ScrapedProduct(title, current, list_price, None)

    def _open_product(self, page: Page, url: str, *, headless: bool) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_SETTLE_MS)

        if self._looks_like_challenge(page):
            print("  Verificação Walmart (PerimeterX) detectada...")
            if self._wait_challenge_auto(page, timeout_ms=CHALLENGE_WAIT_MS):
                print("  Verificação liberou automaticamente.")
            else:
                self._wait_challenge(page, headless=headless)
                if self._looks_like_challenge(page):
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    page.wait_for_timeout(PAGE_SETTLE_MS)
                    if self._looks_like_challenge(page):
                        if not self._wait_challenge_auto(page, timeout_ms=45_000):
                            self._wait_challenge(page, headless=headless)

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._looks_like_challenge(page):
                break
            if self._has_product_payload(page):
                return
            page.wait_for_timeout(1500)

    def _has_product_payload(self, page: Page) -> bool:
        if self._looks_like_challenge(page):
            return False
        if self._extract_current_price(page) is not None:
            return True
        if self._from_json_ld(page)[1] is not None:
            return True
        if self._from_next_data(page, None)[1] is not None:
            return True
        title = self._extract_title(page)
        return bool(title and "robot" not in title.lower())

    def _wait_challenge_auto(self, page: Page, *, timeout_ms: int) -> bool:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            if self._has_product_payload(page):
                return True
            if not self._looks_like_challenge(page):
                try:
                    if len(page.content()) > 30_000:
                        return True
                except Exception:
                    pass
            page.wait_for_timeout(CHALLENGE_POLL_MS)
        return self._has_product_payload(page)

    def warm_session(
        self,
        page: Page,
        settings: dict[str, Any],
        *,
        product_url: str | None = None,
    ) -> bool:
        if self.can_use_api(settings):
            print("  Walmart está em modo SerpApi — warm de browser não é necessário.")
            try:
                # Smoke test da API com o product_id da URL se possível
                from price_monitor.urls import walmart_product_id_from_url

                pid = walmart_product_id_from_url(product_url or "") if product_url else None
                if pid:
                    item = fetch_item(pid, settings)
                    title, price, _ = parse_item_price(item)
                    print(f"  SerpApi OK: {title or pid} | ${price}")
                else:
                    print("  SERPAPI_API_KEY presente (sem product_id para testar).")
                return True
            except WalmartApiError as exc:
                print(f"  SerpApi falhou: {exc}")
                return False

        url = (product_url or "").strip() or WARM_URL
        print(f"  Warm Walmart (browser): {url}")
        print("=" * 60)
        print("  Preferível: configure SERPAPI_API_KEY (sem Press & Hold).")
        print("  Enquanto isso: se aparecer Press & Hold, SEGURE na janela.")
        print("=" * 60)
        page.goto(WARM_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_SETTLE_MS)
        if self._looks_like_challenge(page):
            print("  Challenge na home — faça Press & Hold agora...")
            if not self._wait_until_clear(page, timeout_ms=HEADED_CHALLENGE_WAIT_MS):
                print("  Warm falhou na home.")
                return False
            print("  Home liberada.")

        if url.rstrip("/") != WARM_URL.rstrip("/"):
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(PAGE_SETTLE_MS)
            if self._looks_like_challenge(page) or not self._has_product_payload(page):
                print("  Challenge/produto — faça Press & Hold se aparecer...")
                if not self._wait_until_clear(page, timeout_ms=HEADED_CHALLENGE_WAIT_MS):
                    print("  Warm falhou no produto.")
                    return False
            if not self._has_product_payload(page):
                print("  Warm parcial — home OK.")
                return True

        print("  Warm OK — sessão Walmart gravada.")
        return True

    def _wait_until_clear(self, page: Page, *, timeout_ms: int) -> bool:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            if self._has_product_payload(page):
                return True
            if not self._looks_like_challenge(page):
                try:
                    if len(page.content()) > 30_000:
                        return True
                except Exception:
                    pass
            page.wait_for_timeout(CHALLENGE_POLL_MS)
        return self._has_product_payload(page) or not self._looks_like_challenge(page)

    def run_auth(self, page: Page) -> int:
        print(
            "Walmart via SerpApi — configure:\n"
            "  SERPAPI_API_KEY\n"
            "Docs: https://serpapi.com/walmart-product-api"
        )
        return 1

    def _wait_challenge(self, page: Page, *, headless: bool) -> None:
        if headless:
            raise ChallengeRequiredError(
                "Walmart bloqueou no browser.\n"
                "Use SerpApi (recomendado):\n"
                "  SERPAPI_API_KEY\n"
                "Ou: python -m price_monitor warm --retailer walmart --reset-profile"
            )
        print("  Press & Hold na janela — aguardando (sem Enter)...")
        if self._wait_until_clear(page, timeout_ms=HEADED_CHALLENGE_WAIT_MS):
            print("  Página liberada.")
            return
        raise ChallengeRequiredError("Walmart não liberou a tempo.")

    def _looks_like_challenge(self, page: Page) -> bool:
        url = (page.url or "").lower()
        if any(h in url for h in ("blocked", "challenge", "captcha", "px-captcha")):
            return True
        try:
            body = (page.locator("body").inner_text(timeout=2500) or "").lower()
        except Exception:
            body = ""
        if any(
            p in body
            for p in (
                "robot or human",
                "press & hold",
                "verify you are",
                "access denied",
                "are you a human",
            )
        ):
            return True
        for sel in ["#px-captcha", "text=Press & Hold"]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
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

    def _from_next_data(self, page: Page, product_id: str | None):
        try:
            raw = page.locator("script#__NEXT_DATA__").first.inner_text(timeout=2000)
            blob = raw
        except Exception:
            try:
                blob = page.content()
            except Exception:
                return None, None, None

        title = None
        if product_id:
            for m in re.finditer(re.escape(str(product_id)), blob):
                window = blob[max(0, m.start() - 400) : m.end() + 1200]
                tm = re.search(r'"name"\s*:\s*"([^"\\]{5,200})"', window)
                if tm:
                    title = tm.group(1)
                for pat in [
                    r'"currentPrice"\s*:\s*\{[^}]*?"price"\s*:\s*(\d+(?:\.\d+)?)',
                    r'"price"\s*:\s*(\d+\.\d{2})',
                ]:
                    pm = re.search(pat, window)
                    if pm:
                        price = parse_price(pm.group(1), max_value=10_000)
                        if price is not None:
                            return title, price, None
        return title, None, None

    def _extract_title(self, page: Page) -> str | None:
        for sel in [
            'h1[itemprop="name"]',
            'h1[data-automation-id="product-title"]',
            "#main-title",
            "h1",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                text = (loc.inner_text(timeout=2000) or "").strip()
                if text and "robot" not in text.lower():
                    return re.sub(r"\s+", " ", text)
            except Exception:
                continue
        return None

    def _extract_current_price(self, page: Page) -> float | None:
        for sel in [
            '[itemprop="price"]',
            '[data-automation-id="product-price"]',
            'span[data-seo-id="hero-price"]',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                try:
                    content = loc.get_attribute("content", timeout=800)
                    price = parse_price(content, max_value=10_000)
                    if price is not None:
                        return price
                except Exception:
                    pass
                price = parse_price(loc.inner_text(timeout=1500), max_value=10_000)
                if price is not None:
                    return price
            except Exception:
                continue
        return None

    def _extract_list_price(self, page: Page) -> float | None:
        for sel in ['[data-automation-id="comparison-price"]', "del", "s"]:
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
