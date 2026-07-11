from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

from .auction_radar import AuctionRadarConfig, calculate_auction_radar
from .market_intelligence import IntelligenceConfig, generate_market_intelligence
from .smart_t_controller import SmartTOptions, run_smart_t


def build_complete_payload(
    *,
    symbol: str,
    daily_df: pd.DataFrame,
    minute_df: pd.DataFrame,
    previous_close: float,
    auction_df: pd.DataFrame,
    benchmark_daily: Optional[pd.DataFrame] = None,
    sector_daily: Optional[pd.DataFrame] = None,
    benchmark_intraday: Optional[pd.DataFrame] = None,
    sector_intraday: Optional[pd.DataFrame] = None,
    benchmark_auction: Optional[Mapping[str, Any]] = None,
    sector_auction: Optional[Mapping[str, Any]] = None,
    avg_auction_volume_20d: Optional[float] = None,
    smart_t_options: Optional[SmartTOptions] = None,
    intelligence_config: Optional[IntelligenceConfig] = None,
    auction_config: Optional[AuctionRadarConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """一次生成网页需要的集合竞价、走势研判与智能做T数据。"""
    auction_payload = calculate_auction_radar(
        previous_close=previous_close,
        auction_df=auction_df,
        intraday_df=minute_df,
        daily_df=daily_df,
        benchmark_auction=benchmark_auction,
        sector_auction=sector_auction,
        avg_auction_volume_20d=avg_auction_volume_20d,
        config=auction_config,
    )
    # 使用雷达已截断至 09:25 的价格，避免调用方误传盘中数据造成未来泄漏。
    final_price = float(auction_payload.get("gap", {}).get("auction_price") or previous_close)
    auction_summary = {
        "change_pct": final_price / previous_close - 1.0,
        "volume_ratio": auction_payload.get("features", {}).get("volume_strength"),
    }
    intelligence = generate_market_intelligence(
        daily_df=daily_df,
        intraday_df=minute_df,
        benchmark_daily=benchmark_daily,
        sector_daily=sector_daily,
        benchmark_intraday=benchmark_intraday,
        sector_intraday=sector_intraday,
        auction=auction_summary,
        benchmark_auction=benchmark_auction,
        sector_auction=sector_auction,
        config=intelligence_config,
    )
    # 集合竞价作为上层环境过滤，不直接制造交易信号。
    code = auction_payload.get("prediction", {}).get("code")
    validation = auction_payload.get("validation", {}).get("status")
    if code in {"HIGH_OPEN_FADE", "LOW_OPEN_CONTINUE"}:
        auction_bias = "SELL_FIRST"
    elif code in {"LOW_OPEN_RECOVERY", "HIGH_OPEN_CONTINUE"}:
        auction_bias = "BUY_FIRST"
    else:
        auction_bias = "WAIT"
    options = replace(smart_t_options or SmartTOptions(), auction_bias=auction_bias)
    signals, trades, smart_payload = run_smart_t(minute_df, options=options)
    smart_payload["auctionBias"] = auction_bias
    smart_payload["auctionConfirmed"] = validation == "CONFIRMED"

    payload = {
        "version": "2.1.0",
        "symbol": symbol,
        "auction_radar": auction_payload,
        **intelligence,
        "smart_t": smart_payload,
        "summary": f"{auction_payload.get('prediction', {}).get('label', '竞价待判断')}；{intelligence.get('summary', '')}",
        "disclaimer": "仅用于行情研判、提醒与模拟，不构成投资建议或收益保证。",
    }
    return signals, trades, payload
