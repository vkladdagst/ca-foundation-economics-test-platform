"""Web Push (VAPID) notification helper.

Configured via VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT env vars.
If those are unset, push_configured() is False and every send is a no-op —
the app runs exactly as before, the "Enable notifications" buttons just
don't appear.

Dead subscriptions (endpoint returns 404/410) are deleted automatically so
the table stays clean.
"""
import json
import logging

from flask import current_app

from app.extensions import db
from app.models import PushSubscription

logger = logging.getLogger(__name__)


def push_configured():
    return bool(
        current_app.config.get("VAPID_PUBLIC_KEY")
        and current_app.config.get("VAPID_PRIVATE_KEY")
    )


def _send_one(subscription: PushSubscription, payload: dict):
    from pywebpush import webpush, WebPushException

    try:
        webpush(
            subscription_info=subscription.as_dict(),
            data=json.dumps(payload),
            vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": current_app.config["VAPID_SUBJECT"]},
            timeout=10,
        )
        return True
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            db.session.delete(subscription)  # subscription is gone for good
        else:
            logger.warning("Push send failed (%s): %s", status, exc)
        return False
    except (ValueError, TypeError, KeyError) as exc:
        # Malformed / unusable stored subscription — it will never succeed.
        logger.warning("Dropping unusable push subscription %s: %s", subscription.id, exc)
        db.session.delete(subscription)
        return False
    except Exception:
        logger.exception("Unexpected error sending push")
        return False


def _send_to(subscriptions, title, body, url):
    if not push_configured():
        return 0
    payload = {"title": title, "body": body, "url": url}
    sent = 0
    for sub in list(subscriptions):
        if _send_one(sub, payload):
            sent += 1
    db.session.commit()
    return sent


def notify_teacher(teacher, title, body, url):
    subs = PushSubscription.query.filter_by(owner_type="teacher", owner_id=teacher.id)
    return _send_to(subs, title, body, url)


def notify_specific_students(students, title, body, url):
    ids = [s.id for s in students]
    if not ids:
        return 0
    subs = PushSubscription.query.filter(
        PushSubscription.owner_type == "student",
        PushSubscription.owner_id.in_(ids),
    )
    return _send_to(subs, title, body, url)
