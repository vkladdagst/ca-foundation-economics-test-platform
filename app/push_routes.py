"""Endpoints for a browser to register / drop its Web Push subscription.

Works for whoever is logged in (teacher or student). CSRF is enforced via
the X-CSRFToken header, same as the student answer-autosave endpoint.
"""
from flask import Blueprint, jsonify, request
from flask_login import current_user

from app.extensions import db
from app.models import PushSubscription, Teacher

push_bp = Blueprint("push", __name__, url_prefix="/push")


def _owner():
    if not current_user.is_authenticated:
        return None, None
    return ("teacher" if isinstance(current_user, Teacher) else "student"), current_user.id


@push_bp.route("/subscribe", methods=["POST"])
def subscribe():
    owner_type, owner_id = _owner()
    if not owner_type:
        return jsonify({"ok": False, "error": "Not logged in."}), 401

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    keys = data.get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not endpoint or not p256dh or not auth:
        return jsonify({"ok": False, "error": "Invalid subscription."}), 400

    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub:
        sub.owner_type, sub.owner_id = owner_type, owner_id
        sub.p256dh, sub.auth = p256dh, auth
    else:
        db.session.add(PushSubscription(
            owner_type=owner_type, owner_id=owner_id,
            endpoint=endpoint, p256dh=p256dh, auth=auth,
        ))
    db.session.commit()
    return jsonify({"ok": True})


@push_bp.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if endpoint:
        PushSubscription.query.filter_by(endpoint=endpoint).delete()
        db.session.commit()
    return jsonify({"ok": True})
