import math
from typing import Dict, List

MAX_AMOUNT = 1_000_000


def calculate_shares(amount: float, members: List[str], mode: str, values: List[float]) -> Dict[str, float]:
    """Work out what each member owes for one expense.
    Raises ValueError with a plain message when the numbers do not work."""
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Enter an amount to see the split.")
    if amount > MAX_AMOUNT:
        raise ValueError(f"Amount is too large, the limit is KES {MAX_AMOUNT:,}.")
    if not members:
        raise ValueError("Pick at least one person to split with.")
    if len(set(members)) != len(members):
        raise ValueError("The same person was added twice.")
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Numbers cannot be negative.")

    if mode == "equal":
        each = round(amount / len(members), 2)
        shares = {member: each for member in members}

    elif mode == "percentage":
        if len(values) != len(members):
            raise ValueError("Enter a percentage for every person.")
        total = sum(values)
        if abs(total - 100) > 0.01:
            raise ValueError(f"Percentages add up to {total:g}, they must add up to 100.")
        shares = {m: round(amount * p / 100, 2) for m, p in zip(members, values)}

    else:
        if len(values) != len(members):
            raise ValueError("Enter an amount for every person.")
        difference = round(amount - sum(values), 2)
        if difference > 0:
            raise ValueError(f"KES {difference:,.2f} is still left to assign.")
        if difference < 0:
            raise ValueError(f"Amounts are over by KES {abs(difference):,.2f}.")
        shares = dict(zip(members, values))

    # Rounding can leave a cent or two over, so the last person absorbs it.
    last = members[-1]
    drift = round(amount - sum(shares.values()), 2)
    shares[last] = round(shares[last] + drift, 2)
    return shares
