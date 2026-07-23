from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request

from price_monitor.mail import send_email
from price_monitor.models import Product, ScrapedProduct
from price_monitor.prices import calc_discount


def format_alert(
    product: Product,
    scraped: ScrapedProduct,
    reason: str,
    *,
    brand: str,
) -> str:
    title = scraped.title or product.name
    lines = [
        f"{brand} alert",
        f"Product: {product.name}",
        f"Title: {title}",
        f"Reason: {reason}",
    ]
    if scraped.current_price is not None:
        lines.append(f"Current price: ${scraped.current_price:.2f}")
    if scraped.list_price is not None:
        lines.append(f"List price: ${scraped.list_price:.2f}")
    if product.reference_price is not None:
        lines.append(f"Reference price: ${product.reference_price:.2f}")
    if scraped.discount_percent is not None:
        lines.append(f"Discount: {scraped.discount_percent:.1f}%")
    if product.asin:
        lines.append(f"ASIN: {product.asin}")
    if product.product_id:
        lines.append(f"Product ID: {product.product_id}")
    if product.retailer_slug:
        lines.append(f"Retailer slug: {product.retailer_slug}")
    lines.append(f"URL: {product.url}")
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message}
    ).encode("utf-8")
    req = urllib.request.Request(api_url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[telegram] Failed to send: {exc}")
        return False


def dispatch_alert(
    product: Product,
    scraped: ScrapedProduct,
    reason: str,
    *,
    brand: str,
    email_to: str | None = None,
) -> None:
    if scraped.discount_percent is None:
        scraped.discount_percent = calc_discount(
            scraped.current_price,
            product.reference_price or scraped.list_price,
        )

    message = format_alert(product, scraped, reason, brand=brand)
    subject = f"{brand} alert: {product.name}"

    sent_any = False
    if send_telegram(message):
        print("[alert] Sent via Telegram.")
        sent_any = True
    if send_email(subject, message, to=email_to):
        print("[alert] Sent via email.")
        sent_any = True

    if not sent_any:
        print("\n" + "-" * 60)
        print(message)
        print("-" * 60 + "\n")


def notify_message(
    message: str,
    *,
    subject: str = "Price monitor",
    email_to: str | None = None,
) -> None:
    print("\n" + "!" * 60)
    print(message)
    print("!" * 60 + "\n")
    send_telegram(message)
    send_email(subject, message, to=email_to)
