import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

PAYMONGO_SECRET_KEY = os.getenv("PAYMONGO_SECRET_KEY")
PAYMONGO_PUBLIC_KEY = os.getenv("PAYMONGO_PUBLIC_KEY")
BASE_URL = "https://api.paymongo.com/v1"

SUCCESS_URL = os.getenv("SUCCESS_URL", "http://127.0.0.1:8000/payment/success")
FAILED_URL = os.getenv("FAILED_URL", "http://127.0.0.1:8000/payment/failed")

def _headers():
    if not PAYMONGO_SECRET_KEY:
        raise ValueError("PAYMONGO_SECRET_KEY not set in.env")
    auth = base64.b64encode(f"{PAYMONGO_SECRET_KEY}:".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json"
    }

def create_gcash_checkout(amount_php: float, description: str, reference_id: str = None, metadata: dict = None):
    amount_centavos = int(float(amount_php) * 100)

    if amount_centavos < 10000:
        raise Exception(f"GCash minimum is ₱100. You tried ₱{amount_php}. Add more.")

    merged_metadata = {
        "reference_id": reference_id or "gastokita-sale",
        "description": description[:100],
    }
    if metadata:
        merged_metadata.update(metadata)

    payload = {
        "data": {
            "attributes": {
                "amount": amount_centavos,
                "currency": "PHP",
                "type": "gcash",
                "redirect": {
                    "success": SUCCESS_URL,
                    "failed": FAILED_URL
                },
                "metadata": merged_metadata
            }
        }
    }

    res = requests.post(f"{BASE_URL}/sources", headers=_headers(), json=payload)
    if res.status_code not in (200, 201):
        raise Exception(f"PayMongo GCash source failed {res.status_code}: {res.text}")

    data = res.json()["data"]
    attr = data.get("attributes", {})
    redirect = attr.get("redirect", {})

    checkout_url = (
        redirect.get("checkout") or 
        redirect.get("checkout_url") or 
        redirect.get("url") or
        attr.get("checkout_url")
    )

    if not checkout_url:
        raise Exception(f"No checkout URL in PayMongo response: {res.json()}")

    return {
        "checkout_id": data["id"],
        "checkout_url": checkout_url,
        "source_id": data["id"],
        "amount": amount_php,
        "status": attr.get("status"),
        "raw": data
    }

def retrieve_checkout(checkout_id: str):
    try:
        res = requests.get(f"{BASE_URL}/sources/{checkout_id}", headers=_headers())
        if res.status_code == 200:
            src = res.json()["data"]
            attr = src["attributes"]
            status = attr["status"]

            if status == "chargeable":
                try:
                    payment = create_payment_from_source(attr["amount"] / 100, checkout_id)
                    is_paid = payment["attributes"]["status"] == "paid"
                    return {
                        "id": checkout_id,
                        "is_paid": is_paid,
                        "status": status,
                        "payments": [payment],
                        "attributes": attr
                    }
                except Exception as e:
                    return {
                        "id": checkout_id,
                        "is_paid": True,
                        "status": status,
                        "payments": [],
                        "attributes": attr,
                        "capture_error": str(e)
                    }

            return {
                "id": checkout_id,
                "is_paid": False,
                "status": status,
                "payments": [],
                "attributes": attr
            }
    except:
        pass
    res = requests.get(f"{BASE_URL}/checkout_sessions/{checkout_id}", headers=_headers())
    if res.status_code!= 200:
        raise Exception(f"Retrieve failed: {res.text}")
    attr = res.json()["data"]["attributes"]
    payments = attr.get("payments", [])
    is_paid = len(payments) > 0 and payments[0]["attributes"]["status"] == "paid"
    return {
        "id": checkout_id,
        "is_paid": is_paid,
        "payments": payments,
        "attributes": attr
    }

def create_gcash_source(amount_php: float, reference_id: str = None):
    return create_gcash_checkout(amount_php, "GCash Source", reference_id)

def create_payment_from_source(amount_php: float, source_id: str):
    amount_centavos = int(float(amount_php) * 100)
    payload = {
        "data": {
            "attributes": {
                "amount": amount_centavos,
                "currency": "PHP",
                "source": {"id": source_id, "type": "source"}
            }
        }
    }
    res = requests.post(f"{BASE_URL}/payments", headers=_headers(), json=payload)
    if res.status_code not in (200, 201):
        if "already" in res.text.lower():
            return {"attributes": {"status": "paid"}, "id": "already_paid"}
        raise Exception(f"Create payment failed: {res.text}")
    return res.json()["data"]