"""Simulation service facade."""


def history(core, email: str | None) -> dict:
    return {
        "ok": True,
        "history": core.aggregate_sim_history(email),
        "runs": core.recent_sim_history(12, email),
        "latest": core.latest_sim_result(email),
        "smartTReview": core.smart_t_review_payload(email),
    }
