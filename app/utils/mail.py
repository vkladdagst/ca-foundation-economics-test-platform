"""Minimal SMTP email helper — no third-party mail service dependency.

Configured entirely via environment variables (SMTP_HOST etc., see
.env.example). If SMTP_HOST is unset, sending is a no-op that returns False
so the app keeps working without email configured — teachers just won't
get notifications until they set it up.
"""
import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def mail_configured():
    # SMTP_HOST alone isn't enough to actually send anything — without a
    # password, Gmail (and most providers) will refuse the connection. This
    # used to check SMTP_HOST only, which showed "Configured" in Settings
    # even with no password set, silently failing every send.
    return bool(
        os.environ.get("SMTP_HOST")
        and os.environ.get("SMTP_USER")
        and os.environ.get("SMTP_PASSWORD")
    )


def send_email(to_addrs, subject, body_text):
    """to_addrs: a string or list of strings. Returns True if sent, False otherwise."""
    if not mail_configured():
        logger.info("SMTP not configured — skipping email '%s'", subject)
        return False

    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]
    to_addrs = [a for a in to_addrs if a]
    if not to_addrs:
        return False

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user)
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() != "false"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body_text)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP authentication failed for user '%s' — check SMTP_USER/SMTP_PASSWORD "
            "(Gmail requires an App Password, not your normal account password).", user,
        )
        return False
    except Exception:
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
