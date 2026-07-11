"""Simulation page boundary."""

from .layout import inject_app_layout


def simulation_html(core) -> str:
    return inject_app_layout(core.SIMULATION_HTML, "simulation")


def research_html(core) -> str:
    return inject_app_layout(core.RESEARCH_HTML, "research")


def ranking_html(core) -> str:
    return inject_app_layout(core.AUTO_T_RANKING_HTML, "ranking")
