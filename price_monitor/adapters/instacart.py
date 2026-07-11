from __future__ import annotations

import json
import re
import time
from typing import Any

from playwright.sync_api import Page

from price_monitor.adapters.base import NAV_TIMEOUT_MS
from price_monitor.alerts import notify_message
from price_monitor.config import base_product_fields
from price_monitor.models import Product, ScrapedProduct, SessionExpiredError
from price_monitor.prices import parse_price
from price_monitor.urls import (
    instacart_canonical_url,
    instacart_name_from_url,
    instacart_product_id_from_url,
    instacart_retailer_slug_from_url,
)

PAGE_SETTLE_MS = 3_500
AUTH_REQUIRED_MSG = (
    "Sessão Instacart inválida ou expirada (login/OTP necessário).\n"
    "Rode:\n"
    "  python -m price_monitor auth --retailer instacart"
)


class InstacartAdapter:
    name = "instacart"
    brand = "Instacart"
    default_timezone = "America/Los_Angeles"
    default_headless = True
    supports_auth = True

    def product_key(self, product: Product) -> str:
        if product.product_id:
            slug = product.retailer_slug or "default"
            return f"id:{product.product_id}:retailer:{slug}"
        return f"url:{product.url}"

    def normalize_product(
        self, data: dict[str, Any], settings: dict[str, Any]
    ) -> Product:
        fields = base_product_fields(data, self.name)
        raw_url = fields["url"]

        product_id = data.get("product_id") or data.get("id")
        if product_id is not None:
            product_id = str(product_id).strip() or None
        if product_id is None:
            product_id = instacart_product_id_from_url(raw_url)
        if not product_id:
            raise ValueError(
                f"Não foi possível extrair o product_id da URL Instacart: {raw_url}"
            )

        retailer_slug = (
            data.get("retailer_slug")
            or data.get("retailerSlug")
            or instacart_retailer_slug_from_url(raw_url)
            or settings.get("retailer_slug")
            or settings.get("retailerSlug")
        )
        if retailer_slug is not None:
            retailer_slug = str(retailer_slug).strip() or None

        fields["url"] = instacart_canonical_url(
            product_id, original=raw_url, retailer_slug=retailer_slug
        )
        if not fields["name"]:
            fields["name"] = (
                instacart_name_from_url(raw_url) or f"Instacart {product_id}"
            )

        fields["min_discount_percent"] = None
        fields["reference_price"] = None

        return Product(**fields, product_id=product_id, retailer_slug=retailer_slug)

    def prepare_session(
        self, page: Page, settings: dict[str, Any], *, headless: bool
    ) -> None:
        page.goto(
            "https://www.instacart.com/",
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS,
        )
        page.wait_for_timeout(PAGE_SETTLE_MS)
        if self._looks_like_challenge(page) or self._looks_like_login_or_otp(page):
            notify_message(AUTH_REQUIRED_MSG, subject="Instacart: autenticação necessária")
            raise SessionExpiredError(AUTH_REQUIRED_MSG)

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

        if self._looks_like_challenge(page) or self._looks_like_login_or_otp(page):
            raise SessionExpiredError(AUTH_REQUIRED_MSG)

        zip_code = settings.get("zip") or settings.get("zip_code")
        if set_location and zip_code:
            self._maybe_set_zip(page, str(zip_code))
            page.goto(product.url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(PAGE_SETTLE_MS)
            if self._looks_like_challenge(page) or self._looks_like_login_or_otp(page):
                raise SessionExpiredError(AUTH_REQUIRED_MSG)

        title_ld, price_ld, list_ld = self._from_json_ld(page)
        title = self._extract_title(page) or title_ld
        # Ordem importa: DOM/“Current price” primeiro; JSON embutido por último (muitos preços na página)
        current = (
            self._extract_current_price(page)
            or price_ld
            or self._price_near_product_id(page, product.product_id)
        )
        list_price = self._extract_list_price(page) or list_ld
        if list_price is not None and current is not None and abs(list_price - current) < 0.001:
            list_price = None
        return ScrapedProduct(title, current, list_price, None)

    def run_auth(self, page: Page) -> int:
        print("Abrindo Instacart para autenticação OTP (SMS)...")
        print("1. Faça login e digite o código no navegador.")
        print("2. Quando estiver logado, volte aqui e pressione Enter.")
        print()
        page.goto(
            "https://www.instacart.com/",
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS,
        )
        page.wait_for_timeout(PAGE_SETTLE_MS)
        try:
            input("Pressione Enter quando estiver logado no Instacart... ")
        except EOFError:
            time.sleep(120)
        page.wait_for_timeout(1500)
        if self._looks_like_challenge(page):
            print("Ainda há challenge/captcha. Resolva e rode auth de novo.")
            return 1
        if self._looks_like_login_or_otp(page):
            print("Ainda parece tela de login/OTP. Complete e rode auth de novo.")
            return 1
        print("Perfil salvo. Pode rodar: python -m price_monitor check --retailer instacart")
        return 0

    def _page_text_lower(self, page: Page) -> str:
        try:
            return (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            return ""

    def _looks_like_challenge(self, page: Page) -> bool:
        url = (page.url or "").lower()
        if any(h in url for h in ("captcha", "challenge", "cdn-cgi", "blocked")):
            return True
        body = self._page_text_lower(page)
        if any(
            p in body
            for p in (
                "just a moment",
                "verify you are human",
                "checking your browser",
                "attention required",
            )
        ):
            return True
        for sel in ["#challenge-form", "text=Just a moment"]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _looks_like_login_or_otp(self, page: Page) -> bool:
        url = (page.url or "").lower()
        if any(h in url for h in ("/login", "/signin", "/auth", "otp", "verify-code")):
            if "/products/" not in url:
                return True
        body = self._page_text_lower(page)
        if any(
            p in body
            for p in (
                "enter the code",
                "verification code",
                "we sent a code",
                "log in or sign up",
                "continue with phone",
            )
        ):
            return True
        for sel in [
            "input[autocomplete='one-time-code']",
            "text=Enter the code",
            "text=Verification code",
        ]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _maybe_set_zip(self, page: Page, zip_code: str) -> None:
        zip_code = zip_code.strip()
        if not re.fullmatch(r"\d{5}(-\d{4})?", zip_code):
            print(f"  Aviso: CEP inválido ignorado: {zip_code}")
            return
        for sel in [
            "button:has-text('Address')",
            "button:has-text('Deliver')",
            "[data-testid*='address' i]",
            "button:has-text('Enter address')",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=3000)
                    page.wait_for_timeout(800)
                    break
            except Exception:
                continue
        filled = False
        for sel in [
            "input[placeholder*='address' i]",
            "input[placeholder*='ZIP' i]",
            "input[name*='zip' i]",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.fill(zip_code, timeout=3000)
                filled = True
                page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
                break
            except Exception:
                continue
        if filled:
            print(f"  Tentativa de definir CEP/endereço: {zip_code}")
        else:
            print(f"  Não foi possível definir CEP automaticamente ({zip_code}).")

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

    def _from_next_data(self, page: Page):
        # Mantido só para debug/fallback pontual; scrape principal não usa mais
        # (o blob tem dezenas de preços de produtos relacionados).
        return None, None, None

    def _from_embedded(self, page: Page):
        return None, None, None

    def _price_near_product_id(self, page: Page, product_id: str | None) -> float | None:
        """Procura preço no HTML perto do product_id (evita cards relacionados)."""
        if not product_id:
            return None
        try:
            html = page.content()
        except Exception:
            return None

        # Janela de texto ao redor do id do produto
        for m in re.finditer(re.escape(str(product_id)), html):
            start = max(0, m.start() - 800)
            end = min(len(html), m.end() + 1200)
            window = html[start:end]
            for pat in [
                r'"formattedPrice(?:String)?"\s*:\s*"\$?(\d+(?:\.\d{1,2})?)"',
                r'"price(?:String|Amount)?"\s*:\s*"?\$?(\d+(?:\.\d{1,2})?)"?',
                r'"amount"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
                r"Current price:\s*\$(\d+\.\d{2})",
                r"\$(\d+\.\d{2})",
            ]:
                pm = re.search(pat, window, re.I)
                if pm:
                    price = parse_price(pm.group(1), max_value=10_000)
                    if price is not None:
                        return price
        return None

    def _extract_title(self, page: Page) -> str | None:
        for sel in [
            'h1[data-testid*="product" i]',
            'h1[data-testid*="ItemDetails" i]',
            "h1",
            '[data-testid="product-name"]',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                text = (loc.inner_text(timeout=2000) or "").strip()
                if text and "just a moment" not in text.lower():
                    return re.sub(r"\s+", " ", text)
            except Exception:
                continue
        return None

    def _extract_current_price(self, page: Page) -> float | None:
        # 1) Texto explícito "Current price: $X.XX"
        try:
            body = page.locator("body").inner_text(timeout=4000)
        except Exception:
            body = ""
        m = re.search(r"Current price:\s*\$?\s*(\d+\.\d{2})", body, re.I)
        if m:
            price = parse_price(m.group(1), max_value=10_000)
            if price is not None:
                return price

        # 2) Preço logo após o h1 do produto (buy box)
        try:
            h1 = page.locator("h1").first
            if h1.count() > 0:
                # irmãos / container próximo
                for sel in [
                    "xpath=ancestor::*[self::section or self::div][1]//*[contains(text(),'$')]",
                    "xpath=following::*[contains(text(),'$')][1]",
                ]:
                    try:
                        loc = h1.locator(sel).first
                        if loc.count() == 0:
                            continue
                        text = loc.inner_text(timeout=1500)
                        # Evita preços de tamanho/unidade soltos sem contexto
                        pm = re.search(r"\$\s*(\d+\.\d{2})", text)
                        if pm:
                            price = parse_price(pm.group(1), max_value=10_000)
                            if price is not None:
                                return price
                    except Exception:
                        continue
        except Exception:
            pass

        # 3) Seletores específicos do item principal
        for sel in [
            '[data-testid*="ItemDetails" i] [data-testid*="price" i]',
            '[data-testid*="product-price" i]',
            '[data-testid="item_details_price"]',
            '[aria-label*="current price" i]',
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

        # 4) Fallback: primeira linha do body que parece preço principal após o título
        #    (não varrer a página inteira pelo primeiro $)
        if body:
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            title_idx = -1
            for i, ln in enumerate(lines):
                if "arrowhead" in ln.lower() and len(ln) > 10:
                    title_idx = i
                    break
            search_lines = lines[title_idx : title_idx + 15] if title_idx >= 0 else lines[:40]
            for ln in search_lines:
                if re.search(r"current price", ln, re.I):
                    pm = re.search(r"\$?\s*(\d+\.\d{2})", ln)
                    if pm:
                        return parse_price(pm.group(1), max_value=10_000)
                # Linha que é só/quase só o preço, ex. "$8.07"
                if re.fullmatch(r"\$?\s*\d+\.\d{2}", ln):
                    return parse_price(ln, max_value=10_000)
                # "$8.07" no início da linha (ex. "$8.070.5 L" no markdown às vezes)
                pm = re.match(r"\$\s*(\d+\.\d{2})", ln)
                if pm:
                    return parse_price(pm.group(1), max_value=10_000)
        return None

    def _extract_list_price(self, page: Page) -> float | None:
        for sel in [
            "del",
            "s",
            '[class*="was" i]',
            '[class*="regular" i]',
            '[data-testid*="full" i]',
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
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            return None
        m = re.search(
            r"(?:regular|was|orig(?:inal)?)\s*(?:price)?\s*\$?\s*(\d+\.\d{2})",
            body,
            re.I,
        )
        return parse_price(m.group(1), max_value=10_000) if m else None
