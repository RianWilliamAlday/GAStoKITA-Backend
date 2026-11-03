"""
services/discounts.py

Order-level discount helpers shared by routers/sales.py and routers/gcash.py.

A discount is sent by the app as:
    discount_type  = "percent" | "fixed"
    discount_value = 20 (percent) or 50.00 (pesos)

The server is the one that works out the peso amount, so the app can't send a made-up total.
"""
from typing import List, Optional

from fastapi import HTTPException

PERCENT = "percent"
FIXED = "fixed"


def compute_discount(subtotal: float, discount_type: Optional[str], discount_value: Optional[float]) -> float:
    """Peso amount to take off `subtotal` (rounded to centavos).

    Returns 0.0 when no discount was asked for. Raises HTTP 400 for a discount that makes no sense
    (unknown type, 100% or more, or not smaller than the order total).
    """
    value = float(discount_value or 0)
    kind = (discount_type or "").strip().lower()
    if not kind or value <= 0:
        return 0.0

    if kind == PERCENT:
        if value >= 100:
            raise HTTPException(400, "A percent discount must be less than 100%")
        amount = round(subtotal * value / 100.0, 2)
    elif kind in (FIXED, "amount", "peso"):
        amount = round(value, 2)
    else:
        raise HTTPException(400, f"Unknown discount type '{discount_type}' (use 'percent' or 'fixed')")

    if amount <= 0:
        return 0.0
    if amount >= subtotal - 0.009:
        raise HTTPException(400, f"Discount P{amount:.2f} must be less than the order total P{subtotal:.2f}")
    return amount


def allocate(total: float, weights: List[float]) -> List[float]:
    """Split `total` over rows in proportion to `weights`.

    The pieces are rounded to centavos and always add back up to exactly `total`
    (the last row takes the rounding difference), so reports that sum the rows stay correct.
    """
    n = len(weights)
    out = [0.0] * n
    if not n or not total:
        return out
    wsum = float(sum(weights))
    if wsum <= 0:
        out[-1] = round(total, 2)
        return out
    assigned = 0.0
    for i, w in enumerate(weights):
        if i == n - 1:
            out[i] = round(total - assigned, 2)
        else:
            out[i] = round(total * w / wsum, 2)
            assigned += out[i]
    return out
