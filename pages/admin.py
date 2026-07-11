"""Administrator page boundary."""


def login_html(core) -> str:
    return core.ADMIN_LOGIN_HTML



def admin_html(core) -> str:
    return core.ADMIN_HTML


def forbidden_html(core) -> str:
    return core.FORBIDDEN_HTML
