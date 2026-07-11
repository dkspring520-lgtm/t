"""Authentication and account page boundary."""


def login_html(core) -> str:
    return core.AUTH_HTML.replace("__MODE__", "login")


def register_html(core) -> str:
    return core.AUTH_HTML.replace("__MODE__", "register")


def account_html(core) -> str:
    return core.ACCOUNT_HTML


def commercial_html(core) -> str:
    return core.COMMERCIAL_CLEAN_HTML


def recharge_html(core) -> str:
    return core.RECHARGE_HTML
