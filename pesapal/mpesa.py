import base64
import os
import random
import secrets
import string
from datetime import datetime

import httpx

CONSUMER_KEY = os.getenv("DARAJA_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("DARAJA_CONSUMER_SECRET", "")
SHORTCODE = os.getenv("DARAJA_SHORTCODE", "174379")
PASSKEY = os.getenv("DARAJA_PASSKEY", "")
CALLBACK_URL = os.getenv("DARAJA_CALLBACK_URL", "https://example.com/mpesa/callback")
CALLBACK_SECRET = os.getenv("DARAJA_CALLBACK_SECRET") or secrets.token_urlsafe(16)
BASE_URL = os.getenv("DARAJA_BASE_URL", "https://sandbox.safaricom.co.ke")

# With no Daraja keys set, the backend pretends the payment succeeded.
SIMULATE = not CONSUMER_KEY


def normalise_phone(text: str):
    """Turn a Kenyan number into the 2547XXXXXXXX style Daraja expects.
    Returns None when the number does not look valid."""
    digits = text.replace(" ", "").replace("+", "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    if len(digits) == 12 and digits.startswith("254") and digits.isdigit() and digits[3] in "71":
        return digits
    return None


def make_receipt() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


async def _access_token() -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{BASE_URL}/oauth/v1/generate?grant_type=client_credentials",
            auth=(CONSUMER_KEY, CONSUMER_SECRET),
        )
    response.raise_for_status()
    return response.json()["access_token"]


async def send_stk_push(phone: str, amount: float, reference: str) -> str:
    """Ask Safaricom to show the PIN prompt on the customer's phone.
    Returns the CheckoutRequestID used to match the callback later."""
    if SIMULATE:
        return "ws_CO_" + "".join(random.choices(string.digits, k=12))

    token = await _access_token()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{SHORTCODE}{PASSKEY}{timestamp}".encode()).decode()

    payload = {
        "BusinessShortCode": SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(round(amount)),
        "PartyA": phone,
        "PartyB": SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": f"{CALLBACK_URL}?key={CALLBACK_SECRET}",
        "AccountReference": reference[:12],
        "TransactionDesc": "PesaPal settlement",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{BASE_URL}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    response.raise_for_status()
    return response.json()["CheckoutRequestID"]
