"""Dependency-free validation for incremental A-share minute bars.

The simulation and the adaptive learner must consume the same truthful input:
one-minute incremental volume (lots) and amount (yuan).  This module keeps the
validation independent from either engine so dirty cache rows cannot silently
enter replay or training.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
import math
from typing import TypeVar


T = TypeVar("T")


def minute_of_day(value: object) -> int:
    text = str(value or "").strip()[:5]
    try:
        hour, minute = (int(part) for part in text.split(":", 1))
    except (TypeError, ValueError):
        return -1
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return -1
    return hour * 60 + minute


def is_a_share_session_minute(value: object) -> bool:
    minute = minute_of_day(value)
    return (9 * 60 + 30 <= minute <= 11 * 60 + 30) or (13 * 60 <= minute <= 15 * 60)


def normalise_volume_lots(price: object, volume: object, amount: object) -> float:
    """Normalise a provider's incremental volume to A-share lots.

    Tencent/Eastmoney responses are not consistent across boards: some rows
    expose shares while others expose lots.  The amount-to-volume ratio makes
    the unit observable without using a board-code guess.  Invalid or
    ambiguous rows return zero and are isolated by the caller.
    """

    try:
        price_value = float(price)
        volume_value = float(volume)
        amount_value = float(amount)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not all(math.isfinite(value) and value > 0 for value in (price_value, volume_value, amount_value)):
        return 0.0
    ratio = amount_value / (volume_value * price_value)
    if 0.25 <= ratio <= 4.0:  # provider volume is shares
        return volume_value / 100.0
    if 25.0 <= ratio <= 400.0:  # provider volume is already lots
        return volume_value
    return 0.0


def normalise_trade_date(value: object, *, fallback: date | None = None) -> str:
    """Return an ISO weekday date; weekend-labelled cached bars move to Friday."""

    text = str(value or "").strip()[:10]
    parsed: date
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        parsed = fallback or datetime.now().date()
    while parsed.weekday() >= 5:
        parsed -= timedelta(days=1)
    return parsed.isoformat()


def sanitize_incremental_records(
    records: Iterable[T],
    *,
    time_getter: Callable[[T], object],
    price_getter: Callable[[T], object],
    volume_getter: Callable[[T], object],
    amount_getter: Callable[[T], object],
    date_getter: Callable[[T], object] | None = None,
    lot_size: float = 100.0,
    vwap_tolerance_pct: float = 3.0,
) -> list[T]:
    """Return sorted, unique records with explainable incremental VWAP.

    Each accepted row must have positive incremental volume and amount.  Its
    implied one-minute average and the cumulative VWAP must remain compatible
    with prices observed up to that minute.  A small tolerance accounts for the
    fact that adapters expose minute close rather than minute high/low.
    """

    candidates: dict[tuple[str, int], tuple[T, float, float, float]] = {}
    for item in records:
        try:
            minute = minute_of_day(time_getter(item))
            if minute < 0 or not is_a_share_session_minute(time_getter(item)):
                continue
            price = float(price_getter(item))
            volume = float(volume_getter(item))
            amount = float(amount_getter(item))
            date = str(date_getter(item) if date_getter else "").strip()[:10]
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(value) and value > 0 for value in (price, volume, amount)):
            continue
        implied_price = amount / (volume * max(lot_size, 1e-9))
        if not (price * 0.70 <= implied_price <= price * 1.30):
            continue
        # Keep the last provider update for a duplicated date/minute, then sort
        # before applying the causal checks below.
        candidates[(date, minute)] = (item, price, volume, amount)

    accepted: list[T] = []
    active_date = None
    cumulative_volume = cumulative_amount = 0.0
    observed_low = observed_high = 0.0
    for (date, _minute), (item, price, volume, amount) in sorted(candidates.items()):
        if date != active_date:
            active_date = date
            cumulative_volume = cumulative_amount = 0.0
            observed_low = observed_high = price
        else:
            observed_low = min(observed_low, price)
            observed_high = max(observed_high, price)

        next_volume = cumulative_volume + volume
        next_amount = cumulative_amount + amount
        vwap = next_amount / (next_volume * max(lot_size, 1e-9))
        tolerance = max(0.0, float(vwap_tolerance_pct)) / 100.0
        if not (observed_low * (1.0 - tolerance) <= vwap <= observed_high * (1.0 + tolerance)):
            continue
        cumulative_volume = next_volume
        cumulative_amount = next_amount
        accepted.append(item)
    return accepted
