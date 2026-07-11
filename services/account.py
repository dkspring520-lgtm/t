"""Account service facade."""


def account(core, handler) -> dict:
    return core.account_payload(handler)


def watchlist(core, email: str | None) -> dict:
    return core.watchlist_payload(email)


def save_watchlist(core, data: dict, email: str | None) -> dict:
    return core.save_watchlist_payload(data, email)
