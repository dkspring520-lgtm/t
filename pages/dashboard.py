"""Dashboard and market-radar page boundary."""

from .layout import inject_app_layout


def dashboard_html(core) -> str:
    """Return the monitoring cockpit document from the compatibility store."""
    return inject_app_layout(core.HTML, "dashboard")


def market_radar_html(core) -> str:
    """Return the radar with its page-local presentation resources.

    The market-scoring API remains in ``app_core``; these assets only turn its
    existing snapshot into a clearer market-to-Smart-T explanation.
    """
    html = inject_app_layout(core.MARKET_RADAR_HTML, "market-radar")
    if "/assets/radar-page.js" not in html:
        html = html.replace(
            "</body>",
            '<script defer src="/assets/radar-page.js?v=20260711c"></script></body>',
            1,
        )
    return html


def landing_html(core) -> str:
    return core.LANDING_HTML
