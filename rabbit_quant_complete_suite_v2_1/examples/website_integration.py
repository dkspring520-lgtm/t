"""放进你现有Flask/FastAPI/Django接口时可参考的框架无关示例。"""
from backend.adaptive import AdaptiveLearningService, LearningConfig, run_adaptive_smart_t

learning = AdaptiveLearningService(
    LearningConfig(
        database_path="data/rabbit_learning.sqlite3",
        mode="manual",  # manual最安全；验证通过后由你点确认
    )
)


def calculate_for_symbol(symbol, minute_df):
    signals, trades, payload = run_adaptive_smart_t(symbol, minute_df, learning)
    return payload


def after_market_close(symbol, full_day_minute_df):
    return learning.end_of_day(symbol, full_day_minute_df)


def weekly_learning_job():
    return learning.create_weekly_challenger().to_dict()


def review_shadow_version(apply=False):
    return learning.review_challenger(apply=apply).to_dict()


def strategy_growth_api():
    return learning.growth_payload()
