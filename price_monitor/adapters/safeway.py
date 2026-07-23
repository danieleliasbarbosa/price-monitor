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
from price_monitor.urls import safeway_canonical_url, safeway_product_id_from_url

PAGE_SETTLE_MS = 3_000
INCAPSULA_WAIT_MS = 90_000
PRODUCT_READY_POLL_MS = 2_000
# Espera extra só com janela visível (Incapsula costuma auto-liberar).
HEADED_CHALLENGE_WAIT_MS = 180_000
WARM_URL = "https://www.safeway.com/"

_MODAL_TITLE_MARKERS = (
    "shopped with us before",
    "welcome back",
    "verify your email",
    "let’s add your email",
    "let's add your email",
    "more than one account",
    "pardon our interruption",
)


class SafewayAdapter:
    name = "safeway"
    brand = "Safeway"
    default_timezone = "America/Los_Angeles"
    default_headless = True
    supports_auth = False

    def product_key(self, product: Product) -> str:
        if product.product_id:
            return f"id:{product.product_id}"
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
            product_id = safeway_product_id_from_url(raw_url)
        if not product_id:
            raise ValueError(
                f"Could not extract product_id from Safeway URL: {raw_url}"
            )

        fields["url"] = safeway_canonical_url(product_id, raw_url)
        if not fields["name"]:
            fields["name"] = f"Safeway {product_id}"

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
        self._open_product(page, product.url, headless=headless)

        zip_code = settings.get("zip") or settings.get("zip_code")
        if set_location and zip_code:
            self._maybe_set_zip(page, str(zip_code))
            self._open_product(page, product.url, headless=headless)

        self._dismiss_modals(page)

        # JSON-LD é a fonte mais estável (DOM costuma estar atrás de modais/SPA)
        title_ld, price_ld, list_ld = self._from_json_ld(page)
        title_emb, price_emb, list_emb = self._from_embedded(page)
        title = title_ld or self._extract_title(page) or title_emb
        current = price_ld or self._extract_current_price(page) or price_emb
        list_price = list_ld or self._extract_list_price(page) or list_emb
        if list_price is not None and current is not None and abs(list_price - current) < 0.001:
            list_price = None
        return ScrapedProduct(title, current, list_price, None)

    def _open_product(self, page: Page, url: str, *, headless: bool) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_SETTLE_MS)

        # Incapsula costuma liberar sozinho em ~20–40s; espera antes de falhar.
        if self._wait_for_product_ready(page):
            return

        if self._looks_like_challenge(page):
            self._wait_challenge(page, headless=headless)
            if self._wait_for_product_ready(page):
                return

        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        if self._wait_for_product_ready(page):
            return

        if self._looks_like_challenge(page):
            self._wait_challenge(page, headless=headless)
            self._wait_for_product_ready(page)

    def _wait_for_product_ready(self, page: Page) -> bool:
        """Espera Incapsula liberar e o JSON-LD/DOM do produto aparecer."""
        deadline = time.monotonic() + (INCAPSULA_WAIT_MS / 1000)
        while time.monotonic() < deadline:
            if self._has_product_payload(page):
                return True
            page.wait_for_timeout(PRODUCT_READY_POLL_MS)
        return self._has_product_payload(page)

    def _has_product_payload(self, page: Page) -> bool:
        if self._looks_like_challenge(page):
            return False
        try:
            html = page.content()
        except Exception:
            return False
        if len(html) < 5_000:
            return False
        if '"@type":"Product"' in html or '"@type": "Product"' in html:
            return True
        if 'data-bpn="' in html and "product-details" in html:
            return True
        title_ld, price_ld, _ = self._from_json_ld(page)
        return bool(title_ld or price_ld is not None)

    def _dismiss_modals(self, page: Page) -> None:
        for sel in [
            'button[aria-label="Close"]',
            'button[aria-label="close"]',
            'button:has-text("No thanks")',
            'button:has-text("Not now")',
            'button:has-text("Maybe later")',
            '[data-testid="modal-close"]',
            ".modal button.close",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=1500)
                    page.wait_for_timeout(400)
            except Exception:
                continue

    def run_auth(self, page: Page) -> int:
        print(
            "Safeway does not use OTP. To refresh the session without pressing Enter:\n"
            "  python -m price_monitor warm --retailer safeway"
        )
        return 1

    def warm_session(
        self,
        page: Page,
        settings: dict[str, Any],
        *,
        product_url: str | None = None,
    ) -> bool:
        """
        Abre a Safeway e espera o Incapsula liberar sozinho (sem Enter).
        Grava cookies no perfil persistente. Retorna True se a página liberou.
        """
        url = (product_url or "").strip() or WARM_URL
        print(f"  Warm Safeway: {url}")
        print("  Waiting for Incapsula to clear automatically (no interaction)...")
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_SETTLE_MS)

        # Home page: sucesso = sumiu Incapsula e há conteúdo.
        deadline = time.monotonic() + (HEADED_CHALLENGE_WAIT_MS / 1000)
        while time.monotonic() < deadline:
            if product_url:
                if self._has_product_payload(page):
                    print("  Warm OK — product page cleared.")
                    return True
            else:
                if self._home_ready(page):
                    print("  Warm OK — home page cleared.")
                    return True
            page.wait_for_timeout(PRODUCT_READY_POLL_MS)

        print("  Warm failed — Incapsula did not clear in time.")
        return False

    def _home_ready(self, page: Page) -> bool:
        if self._looks_like_challenge(page):
            return False
        try:
            html = page.content()
        except Exception:
            return False
        if len(html) < 20_000:
            return False
        title = (page.title() or "").lower()
        return "safeway" in title or "aisles" in html.lower() or "shop" in html.lower()

    def _wait_challenge(self, page: Page, *, headless: bool) -> None:
        if headless:
            raise ChallengeRequiredError(
                "Safeway asked for verification (Incapsula) in headless mode.\n"
                "Without human interaction, use one of these options:\n"
                "  1) python -m price_monitor warm --retailer safeway\n"
                "     (visible window; waits to auto-clear and saves cookies)\n"
                "  2) Disable headed_fallback and run warm periodically:\n"
                "       python -m price_monitor warm --retailer safeway\n"
                "  3) Or force a window: check --retailer safeway --no-headless"
            )
        print("  Incapsula detected — waiting for automatic clear (no Enter)...")
        deadline = time.monotonic() + (HEADED_CHALLENGE_WAIT_MS / 1000)
        while time.monotonic() < deadline:
            if self._has_product_payload(page) or (
                not self._looks_like_challenge(page) and len(page.content()) > 20_000
            ):
                print("  Incapsula cleared automatically.")
                return
            page.wait_for_timeout(PRODUCT_READY_POLL_MS)
        raise ChallengeRequiredError(
            "Safeway did not clear Incapsula in time (no interaction).\n"
            "Try again later or run:\n"
            "  python -m price_monitor warm --retailer safeway"
        )

    def _looks_like_challenge(self, page: Page) -> bool:
        url = (page.url or "").lower()
        if any(h in url for h in ("captcha", "challenge", "blocked", "interstitial")):
            return True
        try:
            html = page.content()
        except Exception:
            html = ""
        html_l = html.lower()
        if any(
            p in html_l
            for p in (
                "_incapsula_resource",
                "incapsula incident",
                "pardon our interruption",
                "px-captcha",
                "made us think you were a bot",
            )
        ):
            # Incapsula às vezes auto-resolve; só trata como challenge se ainda
            # não houver payload de produto.
            if "_incapsula_resource" in html_l or "incapsula incident" in html_l:
                if len(html) < 20_000:
                    return True
            else:
                return True
        try:
            body = (page.locator("body").inner_text(timeout=2000) or "").lower()
        except Exception:
            body = ""
        if any(
            p in body
            for p in (
                "pardon our interruption",
                "please stand by",
                "made us think you were a bot",
                "verify you are a human",
                "press & hold",
                "request unsuccessful",
            )
        ):
            return True
        for sel in ["#px-captcha", "text=Pardon Our Interruption", "#main-iframe"]:
            try:
                loc = page.locator(sel)
                if loc.count() == 0:
                    continue
                if sel == "#main-iframe":
                    # iframe Incapsula na página quase vazia
                    if len(html) < 5_000 and loc.first.is_visible():
                        return True
                    continue
                if loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _maybe_set_zip(self, page: Page, zip_code: str) -> None:
        zip_code = zip_code.strip()
        if not re.fullmatch(r"\d{5}(-\d{4})?", zip_code):
            print(f"  Warning: invalid ZIP ignored: {zip_code}")
            return
        for sel in [
            "button:has-text('Select Store')",
            "button:has-text('Change Store')",
            "[data-qa='header-store-button']",
            "button:has-text('Enter ZIP')",
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
            "input[placeholder*='ZIP' i]",
            "input[name*='zip' i]",
            "input[id*='zip' i]",
            "input[aria-label*='ZIP' i]",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.fill(zip_code, timeout=3000)
                filled = True
                page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
                for confirm in [
                    "button:has-text('Make This My Store')",
                    "button:has-text('Shop This Store')",
                    "[data-qa='make-my-store']",
                ]:
                    try:
                        btn = page.locator(confirm).first
                        if btn.count() > 0 and btn.is_visible():
                            btn.click(timeout=3000)
                            page.wait_for_timeout(1500)
                            break
                    except Exception:
                        continue
                break
            except Exception:
                continue
        if filled:
            print(f"  Tried to set ZIP/store: {zip_code}")
        else:
            print(f"  Could not set ZIP automatically ({zip_code}).")

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

    def _from_embedded(self, page: Page):
        try:
            html = page.content()
        except Exception:
            return None, None, None
        title = None
        m = re.search(r'"(?:name|productName)"\s*:\s*"([^"\\]{3,200})"', html)
        if m:
            title = m.group(1)
        current = list_price = None
        for pat in [
            r'"(?:price|salePrice|currentPrice)"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
        ]:
            m = re.search(pat, html)
            if m:
                current = parse_price(m.group(1), max_value=10_000)
                if current is not None:
                    break
        for pat in [
            r'"(?:regularPrice|wasPrice|listPrice)"\s*:\s*"?(\d+(?:\.\d{1,2})?)"?',
        ]:
            m = re.search(pat, html)
            if m:
                list_price = parse_price(m.group(1), max_value=10_000)
                if list_price is not None:
                    break
        return title, current, list_price

    def _is_junk_title(self, text: str) -> bool:
        low = text.lower().strip()
        if not low or len(low) < 3:
            return True
        return any(m in low for m in _MODAL_TITLE_MARKERS)

    def _extract_title(self, page: Page) -> str | None:
        for sel in [
            'h1[data-qa="prd-nam"]',
            'h1[data-qa="prd-name"]',
            "[data-qa='prd-nam']",
            ".product-details-wrapper h1",
            '[data-bpn] h1',
            "main h1",
            "h1",
        ]:
            try:
                locs = page.locator(sel)
                count = min(locs.count(), 8)
                for i in range(count):
                    text = (locs.nth(i).inner_text(timeout=1500) or "").strip()
                    text = re.sub(r"\s+", " ", text)
                    if text and not self._is_junk_title(text):
                        return text
            except Exception:
                continue
        try:
            tab = (page.title() or "").strip()
            # "Arrowhead ... - safeway"
            if tab:
                tab = re.sub(r"\s*[-|]\s*safeway\s*$", "", tab, flags=re.I).strip()
                if tab and not self._is_junk_title(tab):
                    return tab
        except Exception:
            pass
        return None

    def _extract_current_price(self, page: Page) -> float | None:
        for sel in [
            '[data-qa="prd-spc"]',
            '[data-qa="prd-price"]',
            '[data-testid="product-price"]',
            ".product-price",
            ".product-details-wrapper [class*='price' i]",
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
        # Evita varrer a página inteira pelo primeiro $ (carrosséis / deals)
        try:
            root = page.locator(".product-details-wrapper, [data-bpn], main").first
            body = root.inner_text(timeout=3000) if root.count() else ""
        except Exception:
            body = ""
        if not body:
            return None
        m = re.search(r"\$\s*(\d+\.\d{2})", body)
        return parse_price(m.group(1), max_value=10_000) if m else None

    def _extract_list_price(self, page: Page) -> float | None:
        for sel in ["del", "s", '[class*="was-price"]', '[class*="regular-price"]']:
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
