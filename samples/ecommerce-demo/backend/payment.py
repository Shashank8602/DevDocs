def charge(user: str, amount_cents: int) -> dict:
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    return {"user": user, "amount": amount_cents, "status": "paid"}


def refund(charge: dict) -> dict:
    return {**charge, "status": "refunded"}
