"""SMS via Twilio (optional)."""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "price-monitor/1.0 (+local)"


def phone_to_e164(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if phone.strip().startswith("+") and 10 <= len(digits) <= 15:
        return f"+{digits}"
    return None


def sms_configured() -> bool:
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        and os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        and os.getenv("TWILIO_FROM_NUMBER", "").strip()
    )


def send_sms(message: str, *, to: str) -> bool:
    """Send SMS with Twilio. Returns False if not configured or send failed."""
    if not sms_configured():
        print("[sms] Twilio is not configured (TWILIO_ACCOUNT_SID / AUTH_TOKEN / FROM_NUMBER).")
        return False

    e164 = phone_to_e164(to)
    if not e164:
        print(f"[sms] Invalid phone number: {to!r}")
        return False

    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_num = os.getenv("TWILIO_FROM_NUMBER", "").strip()

    data = urllib.parse.urlencode(
        {"To": e164, "From": from_num, "Body": message}
    ).encode("utf-8")
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok = 200 <= resp.status < 300
            if ok:
                print(f"[sms] Twilio OK -> {e164}")
            else:
                print(f"[sms] Twilio HTTP {resp.status}: {body[:240]}")
            return ok
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"[sms] Twilio failed ({exc.code}): {body[:240]}")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[sms] Twilio failed: {exc}")
        return False
