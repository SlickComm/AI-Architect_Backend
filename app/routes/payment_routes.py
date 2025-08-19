import os
import stripe

from typing import Dict, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# --- Stripe Setup ---
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
PRICE_PER_TOKEN_CENTS = 300  # 3,00 €
CURRENCY = "eur"

# --- Session / Balance Helpers ---
# Versuche vorhandenen session_manager zu nutzen (falls dein Projekt ihn hat)
try:
    from app.session_manager import session_manager  # <== Pfad ggf. anpassen
except Exception:
    session_manager = None

# Fallback: In-Memory Guthaben (nur für Entwicklung)
_BALANCES: Dict[str, int] = {}

def _get_balance(session_id: str) -> int:
    if session_manager:
        sess = session_manager.get_session(session_id) or {}
        return int(sess.get("balance_tokens", 0))
    return int(_BALANCES.get(session_id, 0))

def _set_balance(session_id: str, tokens: int) -> None:
    tokens = max(0, int(tokens))
    if session_manager:
        sess = session_manager.get_session(session_id) or {}
        sess["balance_tokens"] = tokens
        session_manager.update_session(session_id, sess)
    else:
        _BALANCES[session_id] = tokens

# --- Schemas ---
class CheckoutReq(BaseModel):
    session_id: str
    tokens: int = 1
    email: Optional[str] = None

class PurchaseReq(BaseModel):
    session_id: str

# --- Endpoints ---
@router.get("/billing/balance")
def billing_balance(session_id: str):
    return {"tokens": _get_balance(session_id)}

@router.post("/billing/create-checkout-session")
def create_checkout_session(req: CheckoutReq):
    if not stripe.api_key:
        raise HTTPException(500, "Stripe nicht konfiguriert (STRIPE_SECRET_KEY fehlt)")

    quantity = max(1, int(req.tokens))
    amount_cents = PRICE_PER_TOKEN_CENTS * quantity

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    success_url = f"{frontend_url}/billing/success?session_id={req.session_id}"
    cancel_url  = f"{frontend_url}/billing/cancel?session_id={req.session_id}"

    sess = session_manager.get_session(req.session_id) if session_manager else {}
    existing_customer = (sess or {}).get("stripe_customer_id")

    params = dict(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": CURRENCY,
                "unit_amount": amount_cents,
                "product_data": {"name": f"ChatCAD Token x{quantity} (1 Token = 3€)"},
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"session_id": req.session_id, "tokens": str(quantity)},
        payment_intent_data={
            "metadata": {"session_id": req.session_id, "tokens": str(quantity)},
            "setup_future_usage": "off_session", 
        },
    )

    if existing_customer:
        params["customer"] = existing_customer        
    else:
        params["customer_creation"] = "always"        
        if req.email:
            params["customer_email"] = req.email

    cs = stripe.checkout.Session.create(**params)
    return {"id": cs.id, "url": cs.url}

@router.post("/billing/purchase-download")
def purchase_download(req: PurchaseReq):
    bal = _get_balance(req.session_id)
    if bal >= 1:
        _set_balance(req.session_id, bal - 1)
        return {"status": "ok", "tokens_left": bal - 1}
    # kein Guthaben -> Checkout für 1 Token anbieten
    cs = create_checkout_session(CheckoutReq(session_id=req.session_id, tokens=1))
    return {
        "status": "insufficient_tokens",
        "need_checkout": True,
        "checkout_session_id": cs["id"],
        "checkout_url": cs["url"],
    }

@router.post("/stripe/webhook")
async def stripe_webhook(req: Request):
    payload = await req.body()
    sig = req.headers.get("stripe-signature")
    wh_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not wh_secret:
        raise HTTPException(500, "Webhook-Secret fehlt (STRIPE_WEBHOOK_SECRET)")

    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except Exception as e:
        raise HTTPException(400, f"Webhook Error: {e}")

    if event["type"] == "checkout.session.completed":
        cs = event["data"]["object"]
        sess_id = (cs.get("metadata") or {}).get("session_id")
        customer_id = cs.get("customer")  # <- WICHTIG
        tokens = int((cs.get("metadata") or {}).get("tokens") or 0)

        if sess_id:
            # Tokens gutschreiben:
            if tokens > 0:
                _set_balance(sess_id, _get_balance(sess_id) + tokens)
            # Customer merken (für 1-Klick bei Wiederkauf)
            if customer_id and session_manager:
                sess = session_manager.get_session(sess_id) or {}
                sess["stripe_customer_id"] = customer_id
                session_manager.update_session(sess_id, sess)

    elif event["type"] == "payment_intent.succeeded":
        pi = event["data"]["object"]
        meta = pi.get("metadata", {}) or {}
        sess_id = meta.get("session_id")
        tokens = int(meta.get("tokens") or 0)
        if sess_id and tokens > 0:
            _set_balance(sess_id, _get_balance(sess_id) + tokens)

    return {"received": True}

@router.post("/billing/auto-topup-one")
def auto_topup_one(req: PurchaseReq):
    if not session_manager:
        raise HTTPException(400, "Auto-Topup benötigt persistente Session/DB")

    sess = session_manager.get_session(req.session_id) or {}
    customer = sess.get("stripe_customer_id")
    if not customer:
        return {"need_checkout": True, "reason": "no_customer"}

    try:
        intent = stripe.PaymentIntent.create(
            amount=PRICE_PER_TOKEN_CENTS,
            currency=CURRENCY,
            customer=customer,
            off_session=True,
            confirm=True,
            metadata={"session_id": req.session_id, "tokens": "1"},
        )
        _set_balance(req.session_id, _get_balance(req.session_id) + 1)
        return {"status": "ok", "tokens_left": _get_balance(req.session_id)}
    except stripe.error.CardError as e:
        # Bank verlangt Authentifizierung → als Fallback Checkout nutzen
        return {"need_checkout": True, "reason": "authentication_required"}