import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["PESAPAL_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

import pytest
from fastapi.testclient import TestClient
from splitting import calculate_shares
from ledger import Ledger
from store import Store
import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def no_auto_callback(monkeypatch):
    async def idle(checkout_id): pass          # keep prompts pending, like a real unanswered phone
    monkeypatch.setattr(main, "simulate_callback", idle)


EMAILS = {"0712345678": "amina", "0722000111": "brian", "0733000222": "wanjiku", "0700000333": "kevin"}


def login(phone):
    r = client.post("/auth/login", json={"email": EMAILS[phone] + "@pesapal.demo", "pin": "1234"}).json()
    return r, {"Authorization": "Bearer " + r["token"]}


# splitting
def test_equal_split_totals_exactly():
    s = calculate_shares(100, ["a", "b", "c"], "equal", [])
    assert round(sum(s.values()), 2) == 100

def test_percentages_must_total_100():
    with pytest.raises(ValueError): calculate_shares(100, ["a", "b"], "percentage", [60, 30])

def test_custom_over_and_under_refused():
    with pytest.raises(ValueError): calculate_shares(100, ["a", "b"], "custom", [60, 60])
    with pytest.raises(ValueError): calculate_shares(100, ["a", "b"], "custom", [10, 10])

def test_negative_and_duplicates_refused():
    with pytest.raises(ValueError): calculate_shares(100, ["a", "b"], "custom", [150, -50])
    with pytest.raises(ValueError): calculate_shares(100, ["a", "a"], "equal", [])


# ledger
def test_hash_chain_detects_tampering_and_survives_restart():
    path = os.path.join(tempfile.mkdtemp(), "l.db")
    ledger = Ledger(Store(path))
    assert ledger.verify() is None
    reloaded = Ledger(Store(path))
    assert len(reloaded.entries) == len(ledger.entries)       # persisted
    assert reloaded.verify() is None
    reloaded.demo_tamper()
    assert Ledger(Store(path)).verify() == 1                  # change caught after reload
    reloaded.demo_repair()
    assert Ledger(Store(path)).verify() is None


# API security and rules
def test_requests_without_token_are_rejected():
    assert client.get("/balances").status_code == 401
    assert client.get("/balances", headers={"Authorization": "Bearer fake.token"}).status_code == 401
    assert client.get("/balances", headers={"X-User-Id": "anything"}).status_code == 401
    assert "pin_hash" not in login("0712345678")[0]

def test_cannot_pay_more_than_owed_or_double_pay():
    me, h = login("0722000111")                               # Brian owes Amina 1500
    amina = next(p for p in client.get("/balances", headers=h).json()["people"] if p["name"] == "Amina")
    assert client.post("/settlements", headers=h, json={"payee_id": amina["id"], "amount": 999999, "phone": "0722000111"}).status_code == 400
    ok = client.post("/settlements", headers=h, json={"payee_id": amina["id"], "amount": amina["net"] * -1, "phone": "0722000111"})
    assert ok.status_code == 200
    again = client.post("/settlements", headers=h, json={"payee_id": amina["id"], "amount": 10, "phone": "0722000111"})
    assert again.status_code == 400                           # prompt already in progress

def test_others_cannot_cancel_my_payment():
    _, h1 = login("0733000222")
    _, h2 = login("0700000333")
    people = client.get("/balances", headers=h1).json()["people"]
    target = next(p for p in people if p["net"] < 0)
    cid = client.post("/settlements", headers=h1, json={"payee_id": target["id"], "amount": 10, "phone": "0733000222"}).json()["checkout_id"]
    assert client.post(f"/settlements/{cid}/cancel", headers=h2).status_code == 404
    assert client.post(f"/settlements/{cid}/cancel", headers=h1).status_code == 200


# money features
def test_fund_cannot_overdraw_and_needs_reason():
    _, h = login("0700000333")
    saved = client.get("/fund", headers=h).json()["saved"]
    assert client.post("/fund/withdraw", headers=h, json={"amount": saved + 1, "note": "rent"}).status_code == 400
    assert client.post("/fund/withdraw", headers=h, json={"amount": 10, "note": ""}).status_code == 400
    assert client.post("/fund/stash", headers=h, json={"amount": 100}).json()["saved"] == saved + 100

def test_paying_a_bill_splits_it_once():
    _, h = login("0712345678")
    bill = next(b for b in client.get("/bills", headers=h).json() if b["title"] == "Wifi")
    assert client.post(f"/bills/{bill['id']}/paid", headers=h).status_code == 200
    assert client.post(f"/bills/{bill['id']}/paid", headers=h).status_code == 400
    assert ledger_has("Wifi")

def ledger_has(title):
    return any(e["title"] == title for e in main.ledger.entries)


# email + PIN sign in
def test_register_verify_login_and_reset():
    body = {"name": "Zawadi", "email": "Zawadi@Example.com", "phone": "0711111111", "gender": "female", "pin": "4321"}
    code = client.post("/auth/register", json=body).json()["dev_code"]
    assert client.post("/auth/login", json={"email": "zawadi@example.com", "pin": "4321"}).status_code == 403   # not verified yet
    assert client.post("/auth/verify", json={"email": "zawadi@example.com", "code": "000000"}).status_code == 400
    assert "token" in client.post("/auth/verify", json={"email": "zawadi@example.com", "code": code}).json()
    assert client.post("/auth/login", json={"email": "zawadi@example.com", "pin": "4321"}).status_code == 200
    reset = client.post("/auth/forgot", json={"email": "zawadi@example.com"}).json()["dev_code"]
    assert client.post("/auth/reset", json={"email": "zawadi@example.com", "code": reset, "new_pin": "9999"}).status_code == 200
    assert client.post("/auth/login", json={"email": "zawadi@example.com", "pin": "9999"}).status_code == 200

def test_wrong_pin_locks_the_account():
    for _ in range(5):
        assert client.post("/auth/login", json={"email": "ghost@example.com", "pin": "0000"}).status_code == 401
    assert client.post("/auth/login", json={"email": "ghost@example.com", "pin": "0000"}).status_code == 429

def test_bad_pins_and_emails_refused():
    base = {"name": "X", "email": "x@example.com", "phone": "0722222222", "gender": "male", "pin": "12"}
    assert client.post("/auth/register", json=base).status_code == 422
    assert client.post("/auth/register", json={**base, "pin": "1234", "email": "nope"}).status_code == 400
