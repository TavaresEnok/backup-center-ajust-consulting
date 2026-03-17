import logging

from flask import Blueprint, jsonify, request

from app.core.config import settings
from app.web.billing.controller import BillingController

bp = Blueprint("billing_webhooks", __name__, url_prefix="/webhooks/billing")


@bp.route("/mercadopago", methods=["POST"])
def mercadopago_webhook():
    token = (settings.MERCADO_PAGO_WEBHOOK_TOKEN or "").strip()
    if token:
        request_token = (request.args.get("token") or "").strip()
        if request_token != token:
            return jsonify({"ok": False, "error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or payload.get("type") or "").strip().lower()
    data = payload.get("data") or {}
    topic = str(request.args.get("topic") or request.args.get("type") or "").strip().lower()
    payment_id = data.get("id") or request.args.get("id")

    should_process_payment = False
    if topic == "payment":
        should_process_payment = True
    if action.startswith("payment."):
        should_process_payment = True
    if action == "payment":
        should_process_payment = True

    if should_process_payment and payment_id:
        if not settings.MERCADO_PAGO_ACCESS_TOKEN:
            return jsonify({"ok": True, "processed": False, "reason": "mercado_pago_not_configured"}), 200
        try:
            BillingController.process_mercadopago_payment(payment_id=payment_id, source="webhook")
        except Exception:
            logging.getLogger(__name__).exception(
                "mercadopago webhook processing failed payment_id=%s action=%s topic=%s",
                payment_id,
                action,
                topic,
            )
            # Returning 200 avoids repeated retries storm while keeping trace in logs.
            return jsonify({"ok": True, "processed": False}), 200

    return jsonify({"ok": True, "processed": bool(should_process_payment and payment_id)}), 200
