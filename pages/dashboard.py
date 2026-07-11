"""Dashboard and market-radar page boundary."""


def dashboard_html(core) -> str:
    """Return the monitoring cockpit document from the compatibility store."""
    return core.HTML


def market_radar_html(core) -> str:
    return core.MARKET_RADAR_HTML


def landing_html(core) -> str:
    return core.LANDING_HTML
