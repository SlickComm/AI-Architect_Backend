import os
from dotenv import load_dotenv
from google.cloud import firestore
from typing import Optional

load_dotenv()

def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val

class FirestoreManager:
    def __init__(self) -> None:
        project_id = require_env("GCP_PROJECT_ID")
        self.db = firestore.Client(project=project_id)

    def create_successfull_payment(self, data: dict) -> None:
        self.db.collection("successfull_payments").document(data["payment_intent_id"]).set(data)
        self.add_user_tokens(data["email"], data["amount"])

    def add_user_tokens(self, user_id: str, tokens: int) -> None:
        doc = self.db.collection("users").document(user_id).get()
        if not doc.exists:
            self.db.collection("users").document(user_id).set({"tokens": tokens})
            return


        self.db.collection("users").document(user_id).update({"tokens": firestore.Increment(tokens)})

    def get_user_tokens(self, email: str) -> Optional[int]:
        snap = self.db.collection("users").document(email).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        return int(data.get("tokens", 0))

    def reduce_user_tokens(self, email: str, amount: int) -> int:
        doc_ref = self.db.collection("users").document(email)

        @firestore.transactional
        def run(txn: firestore.Transaction) -> int:
            snap_or_iter = txn.get(doc_ref)
            if not hasattr(snap_or_iter, "exists"):
                snaps = list(snap_or_iter)
                if not snaps:
                    raise ValueError("user_not_found")
                snap = snaps[0]
            else:
                snap = snap_or_iter

            if not snap.exists:
                raise ValueError("user_not_found")

            data = snap.to_dict() or {}
            current = int(data.get("tokens", 0))
            if current < amount:
                raise ValueError("insufficient_tokens")

            new_balance = current - amount
            txn.update(doc_ref, {"tokens": new_balance})

            self.add_buy_history(email, amount, balanceBefore=current)

            return new_balance

        return run(self.db.transaction())

    def add_buy_history(self, email: str, amount: int, balanceBefore: int = None):
        currentTime = firestore.SERVER_TIMESTAMP
        doc_ref = self.db.collection("buy_history").document()
        doc_ref.set({
            "email": email,
            "amount": amount,
            "time": currentTime,
            "balanceBefore": balanceBefore,
            "balanceAfter": balanceBefore - amount if balanceBefore else "unknown"
        })


