import hashlib
import json
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import auth
from splitting import calculate_shares
from store import Store


class Ledger:
    """Users plus an append-only log of expenses and settlements, saved in SQLite.
    Records are never edited or deleted. Each stores the hash of the record
    before it, so any change to old data is detected by verify()."""

    def __init__(self, store: Optional[Store] = None):
        self.store = store or Store(":memory:")
        self.lock = threading.RLock()
        self.users: Dict[str, dict] = {u["id"]: u for u in self.store.load_users()}
        self.entries: List[dict] = self.store.load_entries()
        self.pending: Dict[str, dict] = self.store.load_json("pending")
        self.outcomes: Dict[str, dict] = self.store.load_json("outcomes")
        self.reminders: List[dict] = self.store.load_reminders()
        self.feedback: List[dict] = self.store.load_feedback()
        self._original: Optional[tuple] = None
        self.hooks = []   # called with the payment when an Mpesa settlement completes
        if not self.users:
            self._seed()
        for user in self.users.values():      # upgrade accounts made before email sign-in existed
            if not user["email"]:
                user.update(email=f"{user['name'].lower().replace(' ', '')}@pesapal.demo", pin_hash=auth.hash_pin("1234"))
                self.store.update_user(user)

    # Users

    def add_user(self, name: str, phone: str, gender: str = "female", email: str = "",
                 pin_hash: str = "", verified: bool = True) -> dict:
        with self.lock:
            user = {"id": uuid.uuid4().hex[:8], "name": name, "phone": phone, "gender": gender,
                    "email": email, "pin_hash": pin_hash, "verified": verified}
            self.store.save_user(user)
            self.users[user["id"]] = user
            return user

    def find_by_email(self, email: str) -> Optional[dict]:
        return next((u for u in self.users.values() if u["email"] == email), None)

    def save_user(self, user: dict):
        self.store.update_user(user)

    def find_by_phone(self, phone: str) -> Optional[dict]:
        for user in self.users.values():
            if user["phone"] == phone:
                return user
        return None

    def _seed(self):
        amina = self.add_user("Amina", "254712345678", "female", "amina@pesapal.demo", auth.hash_pin("1234"))
        brian = self.add_user("Brian", "254722000111", "male", "brian@pesapal.demo", auth.hash_pin("1234"))
        wanjiku = self.add_user("Wanjiku", "254733000222", "female", "wanjiku@pesapal.demo", auth.hash_pin("1234"))
        kevin = self.add_user("Kevin", "254700000333", "male", "kevin@pesapal.demo", auth.hash_pin("1234"))
        everyone = [amina["id"], brian["id"], wanjiku["id"], kevin["id"]]
        for title, category, payer, amount in [
            ("Electricity", "Utilities", kevin, 2400),
            ("Water bill", "Utilities", wanjiku, 1200),
            ("Groceries", "Groceries", amina, 3000),
        ]:
            shares = calculate_shares(amount, everyone, "equal", [])
            self.add_expense(title, category, payer["id"], amount, shares, "equal")

    # Records

    @staticmethod
    def _fingerprint(record: dict) -> str:
        core = {key: value for key, value in record.items() if key != "hash"}
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()

    def _append(self, record: dict):
        with self.lock:
            previous = self.entries[-1]["hash"] if self.entries else "START"
            record["id"] = len(self.entries) + 1
            record["time"] = datetime.now().strftime("%d %b %Y, %H:%M")
            record["previous_hash"] = previous
            record["hash"] = self._fingerprint(record)
            self.store.save_entry(record)
            self.entries.append(record)

    def add_expense(self, title, category, payer_id, amount, shares, mode):
        self._append({
            "kind": "expense", "title": title, "category": category,
            "payer_id": payer_id, "payee_id": "", "amount": amount,
            "shares": shares, "mode": mode, "status": "Recorded", "reference": "",
        })

    def add_settlement(self, payer_id, payee_id, amount, status, reference):
        self._append({
            "kind": "settlement", "title": "Mpesa payment", "category": "Mpesa",
            "payer_id": payer_id, "payee_id": payee_id, "amount": amount,
            "shares": {}, "mode": "", "status": status, "reference": reference,
        })

    def verify(self) -> Optional[int]:
        """None when the log is intact, otherwise the id of the first bad record."""
        previous = "START"
        for entry in self.entries:
            if entry["previous_hash"] != previous or self._fingerprint(entry) != entry["hash"]:
                return entry["id"]
            previous = entry["hash"]
        return None

    # Demo helpers: let you show the examiners that tampering is caught.

    def demo_tamper(self) -> bool:
        with self.lock:
            if not self.entries or self._original:
                return False
            entry = self.entries[0]
            self._original = (entry["id"], entry["amount"])
            entry["amount"] = entry["amount"] + 1000  # hash deliberately not updated
            self.store.raw_update_entry(entry["id"], entry)
            return True

    def demo_repair(self):
        with self.lock:
            if self._original:
                entry = self.entries[self._original[0] - 1]
                entry["amount"] = self._original[1]
                self.store.raw_update_entry(entry["id"], entry)
                self._original = None

    # Balances

    def net_with(self, me: str, other: str) -> float:
        """Positive means the other person owes me, negative means I owe them."""
        total = 0.0
        for entry in self.entries:
            if entry["kind"] == "expense":
                if entry["payer_id"] == me:
                    total += entry["shares"].get(other, 0)
                elif entry["payer_id"] == other:
                    total -= entry["shares"].get(me, 0)
            elif entry["status"] == "Completed":
                if entry["payer_id"] == me and entry["payee_id"] == other:
                    total += entry["amount"]
                elif entry["payer_id"] == other and entry["payee_id"] == me:
                    total -= entry["amount"]
        return round(total, 2)

    def pending_total(self, payer_id: str, payee_id: str) -> float:
        """Money already being collected through unanswered Mpesa prompts."""
        return round(sum(p["amount"] for p in self.pending.values()
                         if p["payer_id"] == payer_id and p["payee_id"] == payee_id), 2)

    def summary_for(self, user_id: str) -> dict:
        owe = owed = 0.0
        people = []
        for other in self.users.values():
            if other["id"] == user_id:
                continue
            net = self.net_with(user_id, other["id"])
            people.append({"id": other["id"], "name": other["name"], "net": net})
            if net < 0:
                owe += abs(net)
            else:
                owed += net
        return {"owe": round(owe, 2), "owed": round(owed, 2), "people": people}

    def describe(self, entry: dict, viewer_id: str) -> dict:
        payee = self.users.get(entry["payee_id"])
        return {
            "id": entry["id"], "kind": entry["kind"], "title": entry["title"],
            "category": entry["category"], "amount": entry["amount"], "mode": entry["mode"],
            "status": entry["status"], "reference": entry["reference"], "time": entry["time"],
            "hash": entry["hash"],
            "payer_name": self.users[entry["payer_id"]]["name"],
            "payee_name": payee["name"] if payee else "",
            "shares": [{"name": self.users[uid]["name"], "amount": value}
                       for uid, value in entry["shares"].items()],
            "my_share": entry["shares"].get(viewer_id),
        }

    # Reminders

    def add_reminder(self, to_id: str, text: str):
        with self.lock:
            reminder = {"to_id": to_id, "text": text}
            self.store.save_reminder(reminder)
            self.reminders.append(reminder)

    def auto_reminders(self) -> int:
        """Queue a reminder for everyone who owes money, once for each amount."""
        added = 0
        with self.lock:
            for creditor in self.users.values():
                for debtor in self.users.values():
                    if creditor["id"] == debtor["id"]:
                        continue
                    owed = self.net_with(creditor["id"], debtor["id"])
                    if owed <= 0:
                        continue
                    text = f"Automatic reminder: you owe {creditor['name']} KES {owed:,.2f}."
                    if any(r["to_id"] == debtor["id"] and r["text"] == text for r in self.reminders):
                        continue
                    self.add_reminder(debtor["id"], text)
                    added += 1
        return added

    # Feedback

    def add_feedback(self, user_id: str, clarity: int, trust: int, ease: int) -> dict:
        with self.lock:
            record = {"user_id": user_id, "clarity": clarity, "trust": trust, "ease": ease}
            self.store.save_feedback(record)
            self.feedback.append(record)
        return self.feedback_summary()

    def feedback_summary(self) -> dict:
        count = len(self.feedback)
        def average(key):
            return round(sum(f[key] for f in self.feedback) / count, 1) if count else 0
        return {"count": count, "clarity": average("clarity"),
                "trust": average("trust"), "ease": average("ease")}

    # Settlements

    def add_pending(self, checkout_id: str, data: dict):
        with self.lock:
            self.pending[checkout_id] = data
            self.store.put_json("pending", checkout_id, data)

    def swap_pending(self, old_id: str, new_id: str):
        with self.lock:
            data = self.pending.pop(old_id)
            self.store.del_json("pending", old_id)
            self.add_pending(new_id, data)

    def finish_settlement(self, checkout_id: str, success: bool, receipt: str, reason: str):
        """Called by the Daraja callback. Safe to call twice for the same payment."""
        with self.lock:
            pending = self.pending.pop(checkout_id, None)
            if pending is None:
                return
            self.store.del_json("pending", checkout_id)
            status = "Completed" if success else "Failed"
            self.add_settlement(pending["payer_id"], pending["payee_id"],
                                pending["amount"], status, receipt)
            if success:
                for hook in self.hooks:
                    hook(pending)
            outcome = {"status": status, "reference": receipt, "reason": reason}
            self.outcomes[checkout_id] = outcome
            self.store.put_json("outcomes", checkout_id, outcome)
