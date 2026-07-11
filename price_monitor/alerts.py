from __future__ import annotations

import os
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from email.mime.text import MIMEText

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
        f"Alerta {brand}",
        f"Produto: {product.name}",
        f"Título: {title}",
        f"Motivo: {reason}",
    ]
    if scraped.current_price is not None:
        lines.append(f"Preço atual: ${scraped.current_price:.2f}")
    if scraped.list_price is not None:
        lines.append(f"Preço lista: ${scraped.list_price:.2f}")
    if product.reference_price is not None:
        lines.append(f"Preço referência: ${product.reference_price:.2f}")
    if scraped.discount_percent is not None:
        lines.append(f"Desconto: {scraped.discount_percent:.1f}%")
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
        print(f"[telegram] Falha ao enviar: {exc}")
        return False


def send_email(subject: str, message: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    port_raw = os.getenv("SMTP_PORT", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    email_from = os.getenv("EMAIL_FROM", "").strip() or user
    email_to = os.getenv("EMAIL_TO", "").strip()

    if not host or not port_raw or not email_to:
        return False

    try:
        port = int(port_raw)
    except ValueError:
        print(f"[email] SMTP_PORT inválida: {port_raw}")
        return False

    msg = MIMEText(message, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.sendmail(email_from, [email_to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls()
                    smtp.ehlo()
                except smtplib.SMTPException:
                    pass
                if user:
                    smtp.login(user, password)
                smtp.sendmail(email_from, [email_to], msg.as_string())
        return True
    except Exception as exc:
        print(f"[email] Falha ao enviar: {exc}")
        return False


def dispatch_alert(
    product: Product,
    scraped: ScrapedProduct,
    reason: str,
    *,
    brand: str,
) -> None:
    # Ensure discount is filled if possible
    if scraped.discount_percent is None:
        scraped.discount_percent = calc_discount(
            scraped.current_price,
            product.reference_price or scraped.list_price,
        )

    message = format_alert(product, scraped, reason, brand=brand)
    subject = f"Alerta {brand}: {product.name}"

    sent_any = False
    if send_telegram(message):
        print("[alerta] Enviado via Telegram.")
        sent_any = True
    if send_email(subject, message):
        print("[alerta] Enviado via e-mail.")
        sent_any = True

    if not sent_any:
        print("\n" + "-" * 60)
        print(message)
        print("-" * 60 + "\n")


def notify_message(message: str, *, subject: str = "Price monitor") -> None:
    print("\n" + "!" * 60)
    print(message)
    print("!" * 60 + "\n")
    send_telegram(message)
    send_email(subject, message)
