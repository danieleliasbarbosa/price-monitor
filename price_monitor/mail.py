"""Envio de e-mail via Resend SDK (com fallback HTTP/SMTP)."""

from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from email.mime.text import MIMEText

USER_AGENT = "price-monitor/1.0 (+local)"


def _email_from() -> str:
    return (
        os.getenv("EMAIL_FROM", "").strip()
        or "beth.t@example.com"
    )


def send_email(
    subject: str,
    message: str,
    *,
    to: str | None = None,
    html: str | None = None,
) -> bool:
    """
    Envia e-mail. Preferência: Resend SDK.
    Fallback: HTTP Resend, depois SMTP_* se configurado.
    """
    email_to = (to or os.getenv("EMAIL_TO", "")).strip()
    if not email_to:
        print("[email] Destinatário ausente (to / EMAIL_TO).")
        return False

    if send_resend(subject, message, to=email_to, html=html):
        return True
    return send_smtp(subject, message, to=email_to)


def send_resend(
    subject: str,
    message: str,
    *,
    to: str,
    html: str | None = None,
) -> bool:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        return False

    payload: dict[str, object] = {
        "from": _email_from(),
        "to": [to],
        "subject": subject,
        "text": message,
    }
    if html:
        payload["html"] = html

    if _send_resend_sdk(api_key, payload, to=to):
        return True
    return _send_resend_http(api_key, payload, to=to)


def _send_resend_sdk(api_key: str, payload: dict[str, object], *, to: str) -> bool:
    try:
        import resend
    except ImportError:
        return False

    try:
        resend.api_key = api_key
        result = resend.Emails.send(payload)
        print(f"[email] Resend OK -> {to}: {result}")
        return True
    except Exception as exc:
        print(f"[email] Resend SDK falhou: {exc}")
        return False


def _send_resend_http(api_key: str, payload: dict[str, object], *, to: str) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok = 200 <= resp.status < 300
            if ok:
                print(f"[email] Resend HTTP OK -> {to}: {body[:200]}")
            else:
                print(f"[email] Resend HTTP {resp.status}: {body}")
            return ok
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"[email] Resend HTTP falhou ({exc.code}): {body}")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[email] Resend HTTP falhou: {exc}")
        return False


def send_smtp(subject: str, message: str, *, to: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    port_raw = os.getenv("SMTP_PORT", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    email_from = os.getenv("EMAIL_FROM", "").strip() or user

    if not host or not port_raw:
        return False

    try:
        port = int(port_raw)
    except ValueError:
        print(f"[email] SMTP_PORT inválida: {port_raw}")
        return False

    msg = MIMEText(message, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = to

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.sendmail(email_from, [to], msg.as_string())
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
                smtp.sendmail(email_from, [to], msg.as_string())
        return True
    except Exception as exc:
        print(f"[email] SMTP falhou: {exc}")
        return False
