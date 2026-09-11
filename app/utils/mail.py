"""Email notifications via Brevo's HTTP API — not raw SMTP.

Render (like most cloud hosts — AWS, Railway, Heroku included) blocks all
outbound SMTP traffic on every plan, to stop the platform being used for
spam. That's not something credentials can fix: smtplib will never connect
from here, regardless of provider. A plain HTTPS request isn't blocked
though, so this calls Brevo's transactional email API directly instead.

Configured via BREVO_API_KEY and EMAIL_FROM env vars. If either is unset,
sending is a no-op that returns False so the app keeps working without
email configured — notifications just don't go out until it's set up.

Setup (free, no card required):
1. Sign up at brevo.com.
2. Settings -> SMTP & API -> API Keys -> generate one -> that's BREVO_API_KEY.
3. Senders, Domains & Dedicated IPs -> add EMAIL_FROM as a sender and confirm
   it via the verification email Brevo sends -- sends fail until this is done.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def mail_configured():
    return bool(os.environ.get("BREVO_API_KEY") and os.environ.get("EMAIL_FROM"))


def send_email(to_addrs, subject, body_text):
    """to_addrs: a string or list of strings. Returns True if sent, False otherwise."""
    if not mail_configured():
        logger.info("Email not configured — skipping '%s'", subject)
        return False

    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]
    to_addrs = [a for a in to_addrs if a]
    if not to_addrs:
        return False

    api_key = os.environ.get("BREVO_API_KEY")
    sender = os.environ.get("EMAIL_FROM")
    sender_name = os.environ.get("EMAIL_FROM_NAME", "CA Foundation Economics Test Platform")

    payload = {
        "sender": {"email": sender, "name": sender_name},
        "to": [{"email": addr} for addr in to_addrs],
        "subject": subject,
        "textContent": body_text,
    }

    try:
        resp = requests.post(
            BREVO_API_URL,
            json=payload,
            headers={
                "accept": "application/json",
                "api-key": api_key,
                "content-type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True
        logger.error("Brevo send failed (HTTP %s): %s", resp.status_code, resp.text[:300])
        return False
    except requests.RequestException:
        logger.exception("Failed to send email '%s' to %s", subject, to_addrs)
        return False


def send_bulk(recipients, subject, body_text_fn):
    """recipients: iterable of (email, context) — body_text_fn(context) -> str.
    Sends one-by-one (fine at small-institute volume) and returns (sent, skipped)."""
    sent = skipped = 0
    for email, context in recipients:
        if not email:
            skipped += 1
            continue
        ok = send_email(email, subject, body_text_fn(context))
        if ok:
            sent += 1
        else:
            skipped += 1
    return sent, skipped
