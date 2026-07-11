"""Market-data service facade.

The compatibility call-through keeps the old API stable while new market-data
providers can be introduced here without touching request routing.
"""


def realtime(core, email: str | None) -> dict:
    return core.realtime_payload(email)


def radar(core, email: str | None) -> dict:
    return core.market_radar_payload(email)


def premarket(core, code: str, email: str | None) -> dict:
    return core.premarket_payload(code, email)
