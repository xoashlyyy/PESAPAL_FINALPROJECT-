"""Money management features: emergency fund, budget, shared bills and house kitties."""
import calendar
import math
from datetime import date, datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from splitting import calculate_shares

LIMITS = {"Rent": 6000, "Groceries": 4000, "Food": 5000, "Transport": 3000,
          "Data & Airtime": 1500, "Utilities": 2500, "Fun": 2000}
TIPS = ["Buy KPLC tokens as a house and split the cost, one person buying small units pays more per unit.",
        "Bundle data: one shared Safaricom home fibre plan is cheaper than four separate bundles.",
        "Cook in batches with your flatmates. A shared sufuria beats four lunches from the kibanda.",
        "Pay rent on the 1st through the app so nobody has to chase anybody on the 5th.",
        "Keep 3 months of basic costs in your Dharura fund before spending on wants."]


class GoalReq(BaseModel):
    goal: float = Field(gt=0, le=10_000_000)
    monthly_need: float = Field(gt=0, le=1_000_000)

class AmountReq(BaseModel):
    amount: float = Field(gt=0, le=1_000_000)
    note: str = Field("", max_length=60)

class RoundupReq(BaseModel):
    on: bool

class BudgetReq(BaseModel):
    income: float = Field(ge=0, le=10_000_000)
    income_label: str = Field("Allowance", max_length=30)
    payday: int = Field(ge=1, le=31)
    limits: Dict[str, float] = {}

class BillReq(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    amount: float = Field(gt=0, le=1_000_000)
    due_day: int = Field(ge=1, le=31)
    category: str = "Utilities"
    member_ids: List[str] = Field(min_length=1)

class KittyReq(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    target: float = Field(gt=0, le=10_000_000)


def due_date(day: int, y: int, m: int) -> date:
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))

def next_month(y: int, m: int):
    return (y + 1, 1) if m == 12 else (y, m + 1)

def next_date(day: int, today: date) -> date:
    d = due_date(day, today.year, today.month)
    return d if d >= today else due_date(day, *next_month(today.year, today.month))


def build_router(ledger, current_user) -> APIRouter:
    r, store = APIRouter(), ledger.store
    now = lambda: datetime.now().strftime("%d %b, %H:%M")

    def doc(kind, owner, make):
        rows = store.list_docs(kind, owner)
        if rows:
            return rows[0]
        data = make()
        return store.add_doc(kind, owner, data), owner, data

    new_fund = lambda: {"goal": 36000, "monthly_need": 12000, "roundup": False, "log": []}
    new_budget = lambda: {"income": 12000, "income_label": "HELB / allowance", "payday": 28, "limits": dict(LIMITS)}

    # Emergency fund (Dharura fund)
    def fund_entry(uid, amount, note, kind):
        with store.lock:
            did, _, d = doc("fund", uid, new_fund)
            d["log"].append({"type": kind, "amount": round(amount, 2), "note": note, "time": now()})
            store.save_doc(did, d)

    def fund_view(uid):
        _, _, d = doc("fund", uid, new_fund)
        saved = round(sum(x["amount"] if x["type"] == "stash" else -x["amount"] for x in d["log"]), 2)
        return {"goal": d["goal"], "monthly_need": d["monthly_need"], "roundup": d["roundup"], "saved": saved,
                "runway_months": round(saved / d["monthly_need"], 1), "log": d["log"][-10:][::-1]}

    def on_settlement(p):
        """Round-up: when a payment completes, stash the gap to the next KES 50."""
        _, _, d = doc("fund", p["payer_id"], new_fund)
        extra = math.ceil(p["amount"] / 50) * 50 - p["amount"]
        if d["roundup"] and extra > 0:
            fund_entry(p["payer_id"], extra, "Round-up on Mpesa payment", "stash")
    ledger.hooks.append(on_settlement)

    @r.get("/fund")
    def get_fund(user=Depends(current_user)):
        return fund_view(user["id"])

    @r.post("/fund/goal")
    def set_goal(body: GoalReq, user=Depends(current_user)):
        with store.lock:
            did, _, d = doc("fund", user["id"], new_fund)
            d.update(goal=body.goal, monthly_need=body.monthly_need)
            store.save_doc(did, d)
        return fund_view(user["id"])

    @r.post("/fund/stash")
    def stash(body: AmountReq, user=Depends(current_user)):
        fund_entry(user["id"], body.amount, body.note.strip() or "Stash", "stash")
        return fund_view(user["id"])

    @r.post("/fund/withdraw")
    def withdraw(body: AmountReq, user=Depends(current_user)):
        if len(body.note.strip()) < 3:
            raise HTTPException(400, "Say what the emergency is, so the fund stays for real emergencies.")
        if body.amount > fund_view(user["id"])["saved"]:
            raise HTTPException(400, "That is more than you have saved.")
        fund_entry(user["id"], body.amount, body.note.strip(), "withdraw")
        return fund_view(user["id"])

    @r.post("/fund/roundup")
    def roundup(body: RoundupReq, user=Depends(current_user)):
        with store.lock:
            did, _, d = doc("fund", user["id"], new_fund)
            d["roundup"] = body.on
            store.save_doc(did, d)
        return fund_view(user["id"])

    # Budget
    def budget_view(uid, today):
        _, _, b = doc("budget", uid, new_budget)
        key, spent = today.strftime("%b %Y"), {}
        for e in ledger.entries:
            if e["kind"] == "expense" and uid in e["shares"] and e["time"][3:11] == key:
                spent[e["category"]] = round(spent.get(e["category"], 0) + e["shares"][uid], 2)
        total = round(sum(spent.values()), 2)
        return {**b, "spent": spent, "total_spent": total, "left": round(b["income"] - total, 2),
                "days_to_payday": (next_date(b["payday"], today) - today).days, "month": today.strftime("%B")}

    @r.get("/budget")
    def get_budget(user=Depends(current_user)):
        return budget_view(user["id"], date.today())

    @r.post("/budget")
    def set_budget(body: BudgetReq, user=Depends(current_user)):
        if any(v <= 0 for v in body.limits.values()):
            raise HTTPException(400, "Limits must be more than zero.")
        with store.lock:
            did, _, _ = doc("budget", user["id"], new_budget)
            store.save_doc(did, {"income": body.income, "income_label": body.income_label.strip() or "Allowance",
                                 "payday": body.payday, "limits": body.limits})
        return budget_view(user["id"], date.today())

    # Shared bills: rent, wifi, KPLC tokens, water
    def bill_view(did, b, uid, today):
        cycle = today.strftime("%Y-%m")
        paid = b.get("last_paid") == cycle
        y, m = next_month(today.year, today.month) if paid else (today.year, today.month)
        due = due_date(b["due_day"], y, m)
        days = (due - today).days
        return {"id": did, "title": b["title"], "amount": b["amount"], "category": b["category"],
                "due_label": due.strftime("%d %b"), "days_left": days,
                "status": "paid" if paid else ("overdue" if days < 0 else "due"),
                "share": round(b["amount"] / len(b["member_ids"]), 2), "mine": uid in b["member_ids"],
                "members": [ledger.users[i]["name"] for i in b["member_ids"] if i in ledger.users],
                "paid_by": b.get("paid_by", "")}

    @r.get("/bills")
    def bills(user=Depends(current_user)):
        today = date.today()
        rows = [bill_view(i, b, user["id"], today) for i, _, b in store.list_docs("bill")]
        return sorted([x for x in rows if x["mine"]], key=lambda x: (x["status"] == "paid", x["days_left"]))

    @r.post("/bills")
    def add_bill(body: BillReq, user=Depends(current_user)):
        if any(m not in ledger.users for m in body.member_ids):
            raise HTTPException(400, "Unknown housemate.")
        members = list(dict.fromkeys(body.member_ids + [user["id"]]))
        store.add_doc("bill", user["id"], {"title": body.title.strip(), "amount": body.amount, "due_day": body.due_day,
                                            "category": body.category, "member_ids": members})
        return {"saved": True}

    @r.post("/bills/{bill_id}/paid")
    def pay_bill(bill_id: int, user=Depends(current_user)):
        """You paid the bill. It becomes a split expense so housemates owe you their share."""
        with store.lock:
            rows = [x for x in store.list_docs("bill") if x[0] == bill_id]
            if not rows or user["id"] not in rows[0][2]["member_ids"]:
                raise HTTPException(404, "Bill not found.")
            did, _, b = rows[0]
            cycle = date.today().strftime("%Y-%m")
            if b.get("last_paid") == cycle:
                raise HTTPException(400, "This bill is already marked paid for this month.")
            shares = calculate_shares(b["amount"], b["member_ids"], "equal", [])
            ledger.add_expense(b["title"], b["category"], user["id"], b["amount"], shares, "equal")
            b.update(last_paid=cycle, paid_by=user["name"])
            store.save_doc(did, b)
        return {"saved": True}

    @r.delete("/bills/{bill_id}")
    def delete_bill(bill_id: int, user=Depends(current_user)):
        rows = [x for x in store.list_docs("bill", user["id"]) if x[0] == bill_id]
        if not rows:
            raise HTTPException(404, "Bill not found.")
        store.del_doc(bill_id)
        return {"deleted": True}

    # House kitties (chama style shared pots)
    def kitty_view(did, k, uid):
        total = round(sum(c["amount"] for c in k["contributions"]), 2)
        mine = round(sum(c["amount"] for c in k["contributions"] if c["user_id"] == uid), 2)
        return {"id": did, "name": k["name"], "target": k["target"], "total": total, "mine": mine,
                "contributors": len({c["user_id"] for c in k["contributions"]})}

    @r.get("/kitties")
    def kitties(user=Depends(current_user)):
        return [kitty_view(i, k, user["id"]) for i, _, k in store.list_docs("kitty")]

    @r.post("/kitties")
    def add_kitty(body: KittyReq, user=Depends(current_user)):
        store.add_doc("kitty", user["id"], {"name": body.name.strip(), "target": body.target, "contributions": []})
        return {"saved": True}

    @r.post("/kitties/{kitty_id}/contribute")
    def contribute(kitty_id: int, body: AmountReq, user=Depends(current_user)):
        with store.lock:
            rows = [x for x in store.list_docs("kitty") if x[0] == kitty_id]
            if not rows:
                raise HTTPException(404, "Kitty not found.")
            did, _, k = rows[0]
            k["contributions"].append({"user_id": user["id"], "amount": body.amount, "time": now()})
            store.save_doc(did, k)
        return kitty_view(did, k, user["id"])

    # Plain-language tips built from the person's own numbers
    @r.get("/insights")
    def insights(user=Depends(current_user)):
        today, uid, tips = date.today(), user["id"], []
        f, b = fund_view(uid), budget_view(uid, date.today())
        if f["saved"] < f["goal"]:
            tips.append({"title": "Dharura fund", "body": f"You have {f['runway_months']} months of basic costs saved. Aim for 3 months, KES {3 * f['monthly_need']:,.0f}."})
        else:
            tips.append({"title": "Dharura fund", "body": "Goal reached. Your safety net is full, well done."})
        for cat, limit in b["limits"].items():
            spent = b["spent"].get(cat, 0)
            if spent > limit:
                tips.append({"title": f"{cat} is over budget", "body": f"You have spent KES {spent:,.0f} of KES {limit:,.0f} this month."})
            elif spent > 0.8 * limit:
                tips.append({"title": f"{cat} is nearly used up", "body": f"KES {limit - spent:,.0f} left until payday in {b['days_to_payday']} days."})
        due = [x for x in bills(user) if x["status"] != "paid" and x["days_left"] <= 3]
        for x in due[:2]:
            tips.append({"title": f"{x['title']} {'is overdue' if x['days_left'] < 0 else 'is due soon'}", "body": f"Your share is KES {x['share']:,.0f}."})
        tips.append({"title": "Student tip", "body": TIPS[today.timetuple().tm_yday % len(TIPS)]})
        return tips[:5]

    # Demo data so the app does not look empty on first run
    if not store.list_docs("bill"):
        ids = list(ledger.users)
        for title, cat, amount, day in [("House rent", "Rent", 24000, 5), ("Wifi", "Utilities", 3000, 10),
                                         ("KPLC tokens", "Utilities", 2000, 15), ("Water", "Utilities", 800, 20)]:
            store.add_doc("bill", ids[0], {"title": title, "amount": amount, "due_day": day, "category": cat, "member_ids": ids})
        store.add_doc("kitty", ids[0], {"name": "Flat emergency kitty", "target": 10000, "contributions": [
            {"user_id": ids[0], "amount": 1500, "time": now()}, {"user_id": ids[1], "amount": 1000, "time": now()}]})
        for uid, start in zip(ids, [4500, 2000, 6200, 800]):
            fund_entry(uid, start, "Demo opening balance", "stash")
    return r
