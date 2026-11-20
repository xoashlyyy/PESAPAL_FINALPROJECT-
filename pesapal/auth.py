import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

_secret = b""
TOKEN_LIFETIME = 60 * 60 * 12  # 12 hours


def init(secret: str):
    global _secret
    _secret = secret.encode()


def _sign(payload: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_secret, payload.encode(), hashlib.sha256).digest()).decode()


def make_token(user_id: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": user_id, "e": int(time.time()) + TOKEN_LIFETIME}).encode()).decode()
    return f"{body}.{_sign(body)}"


def verify_token(token: str) -> Optional[str]:
    """Returns the user id for a genuine, unexpired token, otherwise None."""
    try:
        body, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(body)):
            return None
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
        return data["u"] if data["e"] > time.time() else None
    except Exception:
        return None


# PINs are stored as salted PBKDF2 hashes, never as the PIN itself.
def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()


def check_pin(pin: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        return hmac.compare_digest(digest, hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex())
    except Exception:
        return False


# One-time email codes: 6 digits, valid for 10 minutes, 5 tries.
_codes: dict = {}


def new_code(email: str, purpose: str) -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    _codes[(email, purpose)] = {"code": code, "exp": time.time() + 600, "tries": 0}
    return code


def check_code(email: str, purpose: str, code: str) -> bool:
    entry = _codes.get((email, purpose))
    if not entry or entry["exp"] < time.time() or entry["tries"] >= 5:
        return False
    entry["tries"] += 1
    if hmac.compare_digest(entry["code"], code):
        del _codes[(email, purpose)]
        return True
    return False


# Five wrong PINs lock the account for five minutes.
_fails: dict = {}


def locked_for(email: str) -> int:
    count, until = _fails.get(email, (0, 0))
    return max(int(until - time.time()), 0) if count >= 5 else 0


def record_fail(email: str):
    count, until = _fails.get(email, (0, 0))
    _fails[email] = (count + 1, time.time() + 300 if count + 1 >= 5 else until)


def clear_fails(email: str):
    _fails.pop(email, None)
