from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse
import stripe
import os

router = APIRouter()
stripe.api_key = ""
WEBHOOK_SECRET = ""

@router.post("/payment")
async def stripe_webhook(
        request: Request,
        stripe_signature: str = Header(None, alias="Stripe-Signature"),
):
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=stripe_signature, secret=WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        session = stripe.checkout.Session.retrieve(
            data["id"],
            expand=["payment_intent", "customer", "invoice.customer"],
        )

        email = (
                        session.get("customer_details", {}) or {}
                ).get("email") or (
                        (session.get("customer") or {}) and session["customer"].get("email")
                ) or (
                        (session.get("invoice") or {}) and session["invoice"].get("customer_email")
                )

        payment_intent_id = (
            session["payment_intent"]["id"]
            if isinstance(session["payment_intent"], dict)
            else session["payment_intent"]
        )
        amount = session["amount_total"]
        currency = session["currency"]
        customer_id = (
            session["customer"]["id"]
            if isinstance(session["customer"], dict)
            else session.get("customer")
        )

        # TODO: nutze metadata für stabilen Bezug (order_id/user_id)
        metadata = (session.get("metadata") or {}) | (
                (session.get("payment_intent") or {}).get("metadata") or {}
        )

        # Persistieren: mappe per email und/oder metadata
        # upsert_order_payment(email=email, pi=payment_intent_id, amount=amount, currency=currency, customer_id=customer_id, metadata=metadata)
        print(email, payment_intent_id, amount, currency, customer_id, metadata)

        return JSONResponse({"ok": True})

    if etype == "payment_intent.succeeded":
        # Optional: Falls nur PI kommt, E-Mail nachladen über PI->Customer
        pi_id = data["id"]
        pi = stripe.PaymentIntent.retrieve(pi_id, expand=["customer", "latest_charge"])
        email = (pi.get("customer") or {}).get("email")
        # Wenn email None ist: zugehörige Session(s) auflösen
        if not email:
            # Suche die Checkout Session, die dieses PI verwendet hat
            sessions = stripe.checkout.Session.list(payment_intent=pi_id, limit=1)
            if sessions.data:
                sess = stripe.checkout.Session.retrieve(
                    sessions.data[0].id, expand=["customer"]
                )
                email = (
                        (sess.get("customer_details") or {}).get("email")
                        or (sess.get("customer") or {}).get("email")
                )
        # upsert_payment_with_email(pi_id, email)
        print(email, pi_id)
        return JSONResponse({"ok": True})

    return JSONResponse({"ok": True})