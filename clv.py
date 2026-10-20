"""Closing-line value math.

Line CLV is in points. A positive number means the number you saved was
better than the comparison line (the close, once the game has tipped).

Price CLV is in percentage points of implied probability. Positive means
the book's price on your side shortened after the alert, so the ticket
beat the close.
"""

from __future__ import annotations


def american_implied(price: int) -> float:
    price = int(price)
    if price < 0:
        return (-price) / ((-price) + 100.0)
    if price == 0:
        raise ValueError("american price cannot be 0")
    return 100.0 / (price + 100.0)


def line_clv(side: str, alert_line: float, other_line: float) -> float:
    if side == "Over":
        return float(other_line) - float(alert_line)
    if side == "Under":
        return float(alert_line) - float(other_line)
    raise ValueError(f"unknown side {side}")


def price_clv_points(alert_price: int, other_price: int) -> float:
    return (american_implied(other_price) - american_implied(alert_price)) * 100.0
