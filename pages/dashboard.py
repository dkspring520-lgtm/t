"""Dashboard and market-radar page boundary."""

from .layout import inject_app_layout


def dashboard_html(core) -> str:
    """Return the monitoring cockpit document from the compatibility store."""
    return inject_app_layout(core.HTML, "dashboard")


def market_radar_html(core) -> str:
    return inject_app_layout(core.MARKET_RADAR_HTML, "market-radar")


def landing_html(core) -> str:
    return core.LANDING_HTML
