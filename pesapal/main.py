import asyncio
import hmac
import re
import os
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import auth
import mailer
import mpesa
from ledger import Ledger
from store import Store
from schemas import (ExpenseRequest, FeedbackRequest, LoginRequest, RegisterRequest, VerifyRequest, ForgotRequest, ResetRequest, PreviewRequest,
                     ReminderRequest, SettlementRequest)
from splitting import calculate_shares

store = Store(os.getenv("PESAPAL_DB", str(Path(__file__).parent / "pesapal.db")))
auth.init(os.getenv("PESAPAL_SECRET") or store.get_meta("secret") or "")
if not auth._secret:
    generated = secrets.token_hex(32)
    store.set_meta("secret", generated)
    auth.init(generated)

ledger = Ledger(store)
DEMO_MODE = os.getenv("PESAPAL_DEMO", "1") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(reminder_loop())
    yield
    task.cancel()


app = FastAPI(title="PesaPal API", lifespan=lifespan)
# The web page is served by this same app, so cross-origin access is not needed.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
                   allow_methods=["*"], allow_headers=["*"])

RESULT_REASONS = {
    1: "Insufficient Mpesa balance.",
    1032: "The request was cancelled.",
    1037: "The prompt timed out on your phone.",
}


def current_user(authorization: str = Header("")) -> dict:
    user_id = auth.verify_token(authorization.removeprefix("Bearer ").strip())
    user = ledger.users.get(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    return user


def shares_or_400(amount, member_ids, mode, values):
    for member_id in member_ids:
        if member_id not in ledger.users:
            raise HTTPException(status_code=400, detail="Unknown group member.")
    try:
        return calculate_shares(amount, member_ids, mode, values)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


from money import build_router  # noqa: E402
app.include_router(build_router(ledger, current_user))


# Sign in and members

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def public(user: dict) -> dict:
    """What the browser may see about a person. The PIN hash never leaves the server."""
    return {k: user[k] for k in ("id", "name", "phone", "gender", "email")}


def session(user: dict) -> dict:
    return {**public(user), "token": auth.make_token(user["id"])}


def send_code(email: str, purpose: str) -> dict:
    code = auth.new_code(email, purpose)
    sent = mailer.send(email, "Your PesaPal code", f"Your PesaPal code is {code}. It expires in 10 minutes. If you did not ask for it, ignore this email.")
    if mailer.CONFIGURED and not sent:
        raise HTTPException(status_code=502, detail="We could not send the email. Please try again.")
    # With no email server set up (demo mode) the code is shown on screen so the demo still works.
    return {"sent": True, **({"dev_code": code} if DEMO_MODE and not mailer.CONFIGURED else {})}


@app.post("/auth/register")
def register(body: RegisterRequest):
    email, phone = body.email.strip().lower(), mpesa.normalise_phone(body.phone)
    gender = body.gender.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if phone is None:
        raise HTTPException(status_code=400, detail="Please enter a valid Safaricom number.")
    if gender not in ("female", "male"):
        raise HTTPException(status_code=400, detail="Please choose Female or Male.")
    existing = ledger.find_by_email(email)
    if existing and existing["verified"]:
        raise HTTPException(status_code=400, detail="An account with this email already exists. Please sign in.")
    owner = ledger.find_by_phone(phone)
    if owner and owner is not existing:
        raise HTTPException(status_code=400, detail="That phone number is already registered.")
    if existing:   # started signing up before but never verified: let them try again
        existing.update(name=body.name.strip(), phone=phone, gender=gender, pin_hash=auth.hash_pin(body.pin))
        ledger.save_user(existing)
    else:
        ledger.add_user(body.name.strip(), phone, gender, email, auth.hash_pin(body.pin), verified=False)
    return send_code(email, "verify")


@app.post("/auth/verify")
def verify_email(body: VerifyRequest):
    email = body.email.strip().lower()
    user = ledger.find_by_email(email)
    if user is None or not auth.check_code(email, "verify", body.code.strip()):
        raise HTTPException(status_code=400, detail="That code is wrong or has expired.")
    user["verified"] = True
    ledger.save_user(user)
    return session(user)


@app.post("/auth/login")
def login(body: LoginRequest):
    email = body.email.strip().lower()
    wait = auth.locked_for(email)
    if wait:
        raise HTTPException(status_code=429, detail=f"Too many wrong PINs. Try again in {wait // 60 + 1} minutes.")
    user = ledger.find_by_email(email)
    if user is None or not auth.check_pin(body.pin, user["pin_hash"]):
        auth.record_fail(email)
        raise HTTPException(status_code=401, detail="Wrong email or PIN.")
    if not user["verified"]:
        raise HTTPException(status_code=403, detail="Please verify your email first. Create the account again to get a new code.")
    auth.clear_fails(email)
    return session(user)


@app.post("/auth/forgot")
def forgot_pin(body: ForgotRequest):
    email = body.email.strip().lower()
    user = ledger.find_by_email(email)
    if user is None:   # same answer either way, so nobody can test which emails have accounts
        return {"sent": True}
    return send_code(email, "reset")


@app.post("/auth/reset")
def reset_pin(body: ResetRequest):
    email = body.email.strip().lower()
    user = ledger.find_by_email(email)
    if user is None or not auth.check_code(email, "reset", body.code.strip()):
        raise HTTPException(status_code=400, detail="That code is wrong or has expired.")
    user["pin_hash"] = auth.hash_pin(body.new_pin)
    ledger.save_user(user)
    auth.clear_fails(email)
    return {"changed": True}


@app.get("/members")
def members(user: dict = Depends(current_user)):
    return [{"id": u["id"], "name": u["name"], "gender": u["gender"]} for u in ledger.users.values()]


@app.get("/balances")
def balances(user: dict = Depends(current_user)):
    return ledger.summary_for(user["id"])


# Expenses

@app.post("/expenses/preview")
def preview(body: PreviewRequest, user: dict = Depends(current_user)):
    shares = shares_or_400(body.amount, body.member_ids, body.mode, body.values)
    return {"shares": shares}


@app.post("/expenses")
def create_expense(body: ExpenseRequest, user: dict = Depends(current_user)):
    if body.payer_id not in ledger.users:
        raise HTTPException(status_code=400, detail="Unknown payer.")
    if user["id"] != body.payer_id and user["id"] not in body.member_ids:
        raise HTTPException(status_code=403, detail="You can only record expenses you paid for or share in.")
    shares = shares_or_400(body.amount, body.member_ids, body.mode, body.values)
    ledger.add_expense(body.title.strip(), body.category, body.payer_id,
                       body.amount, shares, body.mode)
    return {"saved": True}


# Settlement through Mpesa

async def simulate_callback(checkout_id: str):
    await asyncio.sleep(5)
    ledger.finish_settlement(checkout_id, True, mpesa.make_receipt(), "")


async def launch_push(payee_name: str, amount: float, phone: str, background: BackgroundTasks) -> str:
    try:
        checkout_id = await mpesa.send_stk_push(phone, amount, f"PesaPal {payee_name}")
    except Exception:
        raise HTTPException(status_code=502, detail="Mpesa is not responding right now. Please try again.")
    if mpesa.SIMULATE:
        background.add_task(simulate_callback, checkout_id)
    return checkout_id


@app.post("/settlements")
async def start_settlement(body: SettlementRequest, background: BackgroundTasks,
                           user: dict = Depends(current_user)):
    payee = ledger.users.get(body.payee_id)
    phone = mpesa.normalise_phone(body.phone)
    if payee is None:
        raise HTTPException(status_code=400, detail="Unknown person.")
    if phone is None:
        raise HTTPException(status_code=400, detail="Please enter a valid Safaricom number.")

    if payee["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot pay yourself.")
    owed = abs(min(ledger.net_with(user["id"], payee["id"]), 0))
    owed = max(owed - ledger.pending_total(user["id"], payee["id"]), 0)
    if body.amount > owed + 0.01:
        raise HTTPException(status_code=400, detail=f"You only owe KES {owed:,.2f} (payments in progress are included).")

    checkout_id = await launch_push(payee["name"], body.amount, phone, background)
    ledger.add_pending(checkout_id, {
        "payer_id": user["id"], "payee_id": payee["id"],
        "amount": body.amount, "phone": phone,
    })
    return {"checkout_id": checkout_id}


@app.get("/settlements/{checkout_id}")
def settlement_status(checkout_id: str, user: dict = Depends(current_user)):
    pending = ledger.pending.get(checkout_id)
    if pending and pending["payer_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if checkout_id in ledger.outcomes:
        return ledger.outcomes[checkout_id]
    if pending:
        return {"status": "pending", "reference": "", "reason": ""}
    raise HTTPException(status_code=404, detail="Payment not found.")


@app.post("/settlements/{checkout_id}/resend")
async def resend_settlement(checkout_id: str, background: BackgroundTasks,
                            user: dict = Depends(current_user)):
    pending = ledger.pending.get(checkout_id)
    if pending is None or pending["payer_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Payment not found.")
    payee_name = ledger.users[pending["payee_id"]]["name"]
    new_id = await launch_push(payee_name, pending["amount"], pending["phone"], background)
    ledger.swap_pending(new_id=new_id, old_id=checkout_id)
    return {"checkout_id": new_id}


@app.post("/settlements/{checkout_id}/cancel")
def cancel_settlement(checkout_id: str, user: dict = Depends(current_user)):
    pending = ledger.pending.get(checkout_id)
    if pending is None or pending["payer_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Payment not found.")
    ledger.finish_settlement(checkout_id, False, "", "The request was cancelled.")
    return {"cancelled": True}


@app.post("/mpesa/callback")
async def mpesa_callback(payload: dict, key: str = ""):
    """Safaricom posts the payment result here after the customer answers the prompt.
    The secret in the callback URL stops anyone else from faking a payment."""
    if not mpesa.SIMULATE and not hmac.compare_digest(key, mpesa.CALLBACK_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden.")
    callback = payload.get("Body", {}).get("stkCallback", {})
    checkout_id = callback.get("CheckoutRequestID", "")
    code = callback.get("ResultCode")

    receipt = ""
    for item in callback.get("CallbackMetadata", {}).get("Item", []):
        if item.get("Name") == "MpesaReceiptNumber":
            receipt = item.get("Value", "")

    reason = RESULT_REASONS.get(code, callback.get("ResultDesc", "The payment failed."))
    ledger.finish_settlement(checkout_id, code == 0, receipt, reason)
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


# History, alerts and reminders

@app.get("/history")
def history(kind: Optional[str] = None, user: dict = Depends(current_user)):
    rows = [e for e in reversed(ledger.entries) if kind in (None, "", e["kind"])]
    return [ledger.describe(e, user["id"]) for e in rows]


@app.get("/history/verify")
def verify_history(user: dict = Depends(current_user)):
    bad_id = ledger.verify()
    return {"intact": bad_id is None, "bad_record": bad_id}


@app.get("/notifications")
def notifications(user: dict = Depends(current_user)):
    me = user["id"]
    notices = []

    for person in ledger.summary_for(me)["people"]:
        if person["net"] < 0:
            notices.append({
                "title": "Payment reminder",
                "body": f"You owe {person['name']} KES {abs(person['net']):,.2f}. Open the Home tab and tap Pay.",
            })

    for entry in reversed(ledger.entries):
        payer = ledger.users[entry["payer_id"]]["name"]
        if entry["kind"] == "expense" and entry["payer_id"] != me and me in entry["shares"]:
            notices.append({
                "title": "New expense",
                "body": f"{payer} added {entry['title']}. Your share is KES {entry['shares'][me]:,.2f}.",
            })
        elif entry["kind"] == "settlement" and entry["status"] == "Completed":
            if entry["payee_id"] == me:
                notices.append({
                    "title": "Payment received",
                    "body": f"{payer} paid you KES {entry['amount']:,.2f}, receipt {entry['reference']}.",
                })
            elif entry["payer_id"] == me:
                payee = ledger.users[entry["payee_id"]]["name"]
                notices.append({
                    "title": "Payment confirmed",
                    "body": f"You paid {payee} KES {entry['amount']:,.2f}, receipt {entry['reference']}.",
                })

    for reminder in reversed(ledger.reminders):
        if reminder["to_id"] == me:
            notices.append({"title": "Reminder", "body": reminder["text"]})

    return notices


@app.post("/reminders")
def send_reminder(body: ReminderRequest, user: dict = Depends(current_user)):
    target = ledger.users.get(body.user_id)
    if target is None:
        raise HTTPException(status_code=400, detail="Unknown person.")
    owed = ledger.net_with(user["id"], target["id"])
    if owed <= 0:
        raise HTTPException(status_code=400, detail=f"{target['name']} does not owe you anything.")
    ledger.add_reminder(target["id"], f"{user['name']} reminded you about KES {owed:,.2f}.")
    return {"message": f"{target['name']} has been reminded about KES {owed:,.2f}."}


@app.post("/reminders/auto")
def run_auto_reminders(user: dict = Depends(current_user)):
    return {"added": ledger.auto_reminders()}


async def reminder_loop():
    """Checks for unpaid balances every 30 seconds and queues reminders."""
    while True:
        ledger.auto_reminders()
        await asyncio.sleep(30)


# Feedback, used to measure how clear and trustworthy people find the system

@app.post("/feedback")
def send_feedback(body: FeedbackRequest, user: dict = Depends(current_user)):
    return ledger.add_feedback(user["id"], body.clarity, body.trust, body.ease)


@app.get("/feedback/summary")
def feedback_summary(user: dict = Depends(current_user)):
    return ledger.feedback_summary()


# Demo only: lets you show examiners that tampering with the log is detected.

@app.post("/demo/tamper")
def demo_tamper(user: dict = Depends(current_user)):
    if not DEMO_MODE:
        raise HTTPException(status_code=404, detail="Not found.")
    return {"tampered": ledger.demo_tamper()}


@app.post("/demo/repair")
def demo_repair(user: dict = Depends(current_user)):
    if not DEMO_MODE:
        raise HTTPException(status_code=404, detail="Not found.")
    ledger.demo_repair()
    return {"repaired": True}


@app.get("/health")
def health():
    return {"ok": True, "mode": "simulated" if mpesa.SIMULATE else "daraja-sandbox",
            "records": len(ledger.entries)}


# The web interface lives in the static folder. This mount must stay last,
# so that the API routes above are matched first.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
