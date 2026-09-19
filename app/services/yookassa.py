import os
import uuid

import requests

API = "https://api.yookassa.ru/v3"


def configured() -> bool:
    return bool(os.getenv("YOOKASSA_SHOP_ID") and os.getenv("YOOKASSA_SECRET_KEY") and os.getenv("PUBLIC_BASE_URL"))


def create_payment(order_id: int, amount_rub: int) -> dict:
    if not configured():
        raise RuntimeError("YooKassa is not configured")
    payload = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": f"{os.environ['PUBLIC_BASE_URL'].rstrip('/')}/?payment=return&order_id={order_id}",
        },
        "description": f"FileForge Premium, заказ №{order_id}",
        "metadata": {"order_id": str(order_id)},
    }
    r = requests.post(
        f"{API}/payments",
        auth=(os.environ["YOOKASSA_SHOP_ID"], os.environ["YOOKASSA_SECRET_KEY"]),
        headers={"Idempotence-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"YooKassa error: HTTP {r.status_code}")
    data = r.json()
    confirmation = data.get("confirmation") or {}
    return {
        "id": data.get("id"),
        "status": data.get("status"),
        "checkout_url": confirmation.get("confirmation_url"),
    }


def get_payment(payment_id: str) -> dict:
    if not configured():
        raise RuntimeError("YooKassa is not configured")
    r = requests.get(
        f"{API}/payments/{payment_id}",
        auth=(os.environ["YOOKASSA_SHOP_ID"], os.environ["YOOKASSA_SECRET_KEY"]),
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"YooKassa error: HTTP {r.status_code}")
    return r.json()
