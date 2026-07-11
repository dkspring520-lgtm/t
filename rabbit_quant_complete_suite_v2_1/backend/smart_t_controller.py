from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import time
from typing import Any, Dict, Literal, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from .rabbit_intraday_signal_engine import (
        SignalConfig, calculate_intraday_signals, latest_ui_payload,
    )
except ImportError:
    from rabbit_intraday_signal_engine import (
        SignalConfig, calculate_intraday_signals, latest_ui_payload,
    )

Sensitivity = Literal["steady", "balanced", "sensitive", "custom"]
Regime = Literal["OBSERVE", "UPTREND", "RANGE", "DOWNTREND", "NO_TRADE"]
Direction = Literal["SELL_FIRST", "BUY_FIRST"]


@dataclass(frozen=True)
class SmartTProfile:
    """前台只展示三档；复杂阈值统一放在后台管理。"""

    name: str
    label: str
    confirmed_score: float
    watch_score: float
    candidate_score: float
    cooldown_bars: int
    zone_memory_bars: int
    min_expected_net_rate: float
    strong_trend_extra_score: float
    max_daily_cycles: int
    opening_observe_bars: int = 8
    min_volume_ratio: float = 0.18


PROFILES: Dict[str, SmartTProfile] = {
    "steady": SmartTProfile(
        name="steady",
        label="稳健",
        confirmed_score=88.0,
        watch_score=58.0,
        candidate_score=74.0,
        cooldown_bars=8,
        zone_memory_bars=6,
        min_expected_net_rate=0.0050,
        strong_trend_extra_score=10.0,
        max_daily_cycles=2,
    ),
    "balanced": SmartTProfile(
        name="balanced",
        label="平衡",
        confirmed_score=82.0,
        watch_score=52.0,
        candidate_score=68.0,
        cooldown_bars=5,
        zone_memory_bars=5,
        min_expected_net_rate=0.0035,
        strong_trend_extra_score=8.0,
        max_daily_cycles=3,
    ),
    "sensitive": SmartTProfile(
        name="sensitive",
        label="灵敏",
        confirmed_score=76.0,
        watch_score=48.0,
        candidate_score=62.0,
        cooldown_bars=3,
        zone_memory_bars=4,
        min_expected_net_rate=0.0025,
        strong_trend_extra_score=6.0,
        max_daily_cycles=5,
    ),
}


@dataclass(frozen=True)
class SmartTOptions:
    sensitivity: Sensitivity = "balanced"

    # 费用与成交模型。费率为一个完整T循环的总交易费用，不含滑点。
    estimated_cycle_cost_rate: float = 0.0010
    slippage_rate_per_side: float = 0.0002

    # 简洁前台开关。
    market_filter_enabled: bool = True
    important_alerts_enabled: bool = True

    # 09:25 只传入优先方向；执行器会自行用“截至当前”的开盘K线验证，
    # 不接受调用方提前传入 09:35 的结论，避免回测前视。
    auction_bias: str = "WAIT"
    auction_confirmation_time: str = "09:35"

    # A股做T默认要求已有可卖底仓。正T买入后，退出时卖的是原有可卖底仓，
    # 不是当天新买入的股票。
    allow_sell_first: bool = True
    allow_buy_first: bool = True
    base_holding_available: bool = True
    cash_available: bool = True

    # 隐藏风控层。
    force_close_enabled: bool = True
    force_close_time: str = "14:50"
    new_cycle_cutoff_time: str = "14:30"
    max_holding_bars: int = 45
    max_adverse_rate: float = 0.0060
    max_adverse_atr_multiple: float = 1.00
    daily_loss_limit_rate: float = 0.0150
    allowed_regimes: Tuple[str, ...] = ("UPTREND", "RANGE", "DOWNTREND")


@dataclass
class SmartTradeRecord:
    date: str
    direction: str
    signal_time: str
    entry_time: str
    exit_signal_time: Optional[str]
    exit_time: str
    entry_price: float
    exit_price: float
    gross_return: float
    estimated_cost_rate: float
    total_slippage_rate: float
    net_return: float
    result: str
    exit_reason: str
    holding_bars: int


_CUSTOM_LIMITS = {
    "confirmed_score": (70.0, 95.0),
    "watch_score": (40.0, 70.0),
    "candidate_score": (55.0, 85.0),
    "cooldown_bars": (2, 20),
    "zone_memory_bars": (2, 12),
    "min_expected_net_rate": (0.0015, 0.015),
    "strong_trend_extra_score": (0.0, 15.0),
    "max_daily_cycles": (1, 10),
    "opening_observe_bars": (0, 30),
    "min_volume_ratio": (0.0, 1.0),
}


def _parse_clock(value: str, name: str) -> time:
    try:
        hour, minute = value.split(":", maxsplit=1)
        parsed = time(int(hour), int(minute))
    except Exception as exc:  # pragma: no cover - defensive validation
        raise ValueError(f"{name} 必须使用 HH:MM 格式") from exc
    return parsed


def _validate_options(options: SmartTOptions) -> None:
    if options.estimated_cycle_cost_rate < 0:
        raise ValueError("estimated_cycle_cost_rate 不能为负数")
    if options.slippage_rate_per_side < 0:
        raise ValueError("slippage_rate_per_side 不能为负数")
    if options.max_holding_bars < 1:
        raise ValueError("max_holding_bars 必须至少为1")
    if options.max_adverse_rate <= 0:
        raise ValueError("max_adverse_rate 必须大于0")
    if options.max_adverse_atr_multiple <= 0:
        raise ValueError("max_adverse_atr_multiple 必须大于0")
    if options.daily_loss_limit_rate <= 0:
        raise ValueError("daily_loss_limit_rate 必须大于0")
    if options.auction_bias not in {"WAIT", "SELL_FIRST", "BUY_FIRST"}:
        raise ValueError("auction_bias 只能是 WAIT / SELL_FIRST / BUY_FIRST")
    _parse_clock(options.auction_confirmation_time, "auction_confirmation_time")
    _parse_clock(options.force_close_time, "force_close_time")
    _parse_clock(options.new_cycle_cutoff_time, "new_cycle_cutoff_time")


def _bounded(name: str, value: Any) -> float:
    low, high = _CUSTOM_LIMITS[name]
    number = float(value)
    if number < low or number > high:
        raise ValueError(f"{name} 必须位于 {low}～{high} 之间")
    return number


def resolve_profile(
    sensitivity: Sensitivity = "balanced",
    custom: Optional[Mapping[str, Any]] = None,
) -> SmartTProfile:
    if sensitivity != "custom":
        if sensitivity not in PROFILES:
            raise ValueError("sensitivity 只能是 steady / balanced / sensitive / custom")
        return PROFILES[sensitivity]

    custom = dict(custom or {})
    # 兼容旧字段名 min_profit_rate。
    if "min_profit_rate" in custom and "min_expected_net_rate" not in custom:
        custom["min_expected_net_rate"] = custom.pop("min_profit_rate")

    base = PROFILES["balanced"]
    values = asdict(base)
    values["name"] = "custom"
    values["label"] = "自定义"
    for key in _CUSTOM_LIMITS:
        if key not in custom:
            continue
        value = _bounded(key, custom[key])
        if key in {
            "cooldown_bars",
            "zone_memory_bars",
            "max_daily_cycles",
            "opening_observe_bars",
        }:
            value = int(value)
        values[key] = value

    if values["watch_score"] >= values["candidate_score"]:
        raise ValueError("watch_score 必须小于 candidate_score")
    if values["candidate_score"] >= values["confirmed_score"]:
        raise ValueError("candidate_score 必须小于 confirmed_score")
    return SmartTProfile(**values)


def build_signal_config(
    profile: SmartTProfile,
    options: SmartTOptions,
) -> SignalConfig:
    # 信号引擎中的 min_profit_rate 只作为基础保护，真正的预期净价差由
    # 智能执行器再加上交易费用和滑点后计算。
    return SignalConfig(
        confirmed_score=profile.confirmed_score,
        watch_score=profile.watch_score,
        candidate_score=profile.candidate_score,
        signal_cooldown_bars=profile.cooldown_bars,
        zone_memory_bars=profile.zone_memory_bars,
        min_profit_rate=profile.min_expected_net_rate,
        strong_trend_extra_score=profile.strong_trend_extra_score,
        estimated_cycle_cost_rate=options.estimated_cycle_cost_rate,
        force_close_enabled=options.force_close_enabled,
        force_close_time=options.force_close_time,
    )


def add_market_regime(
    signals: pd.DataFrame,
    profile: SmartTProfile,
    market_filter_enabled: bool = True,
) -> pd.DataFrame:
    """把多套内部逻辑折叠成用户只看到的“智能做T”。

    UPTREND：只允许回踩正T；DOWNTREND：只允许冲高反T；
    RANGE：两边都可候选，但一次只执行一个完整循环；
    OBSERVE/NO_TRADE：不产生新循环。
    """
    out = signals.copy()
    enough = out.get("bars_from_open", pd.Series(0, index=out.index)) >= profile.opening_observe_bars
    volume_ok = (
        out.get("volume_ratio", pd.Series(1.0, index=out.index))
        .fillna(0.0)
        .ge(profile.min_volume_ratio)
    )

    optional_tradable = out.get("tradable", pd.Series(True, index=out.index)).fillna(False).astype(bool)
    optional_suspended = out.get("suspended", pd.Series(False, index=out.index)).fillna(True).astype(bool)
    data_ok = optional_tradable & ~optional_suspended

    if not market_filter_enabled:
        regime = np.where(enough & volume_ok & data_ok, "RANGE", "OBSERVE")
    else:
        trend_up = out.get("trend_up_5m", pd.Series(False, index=out.index)).fillna(False)
        trend_down = out.get("trend_down_5m", pd.Series(False, index=out.index)).fillna(False)
        strong = out.get("strong_trend_5m", pd.Series(False, index=out.index)).fillna(False)
        atr = out.get("atr14", pd.Series(np.nan, index=out.index))
        close = out["close"].replace(0.0, np.nan)
        tradable = volume_ok & data_ok & atr.notna() & close.notna()
        regime = np.select(
            [
                ~enough,
                enough & ~tradable,
                enough & trend_up & strong,
                enough & trend_down & strong,
                enough & tradable,
            ],
            ["OBSERVE", "NO_TRADE", "UPTREND", "DOWNTREND", "RANGE"],
            default="NO_TRADE",
        )
    out["smart_regime"] = regime
    return out


def _required_gross_spread(
    signal_row: pd.Series,
    profile: SmartTProfile,
    options: SmartTOptions,
    config: SignalConfig,
) -> float:
    """达到目标净价差所需的最小毛价差。"""
    close = max(float(signal_row["close"]), 1e-12)
    atr = float(signal_row["atr14"]) if pd.notna(signal_row.get("atr14")) else 0.0
    total_slippage = 2.0 * options.slippage_rate_per_side
    fees = options.estimated_cycle_cost_rate
    return max(
        profile.min_expected_net_rate + fees + total_slippage,
        fees * config.cost_buffer_multiple + total_slippage,
        config.atr_profit_multiple * atr / close,
    )


def _fill_price(raw_price: float, side: Literal["BUY", "SELL"], options: SmartTOptions) -> float:
    rate = options.slippage_rate_per_side
    return raw_price * (1.0 + rate) if side == "BUY" else raw_price * (1.0 - rate)


def _can_execute(row: pd.Series, side: Literal["BUY", "SELL"]) -> bool:
    if not bool(row.get("tradable", True)) or bool(row.get("suspended", False)):
        return False
    if side == "BUY" and bool(row.get("limit_up", False)):
        return False
    if side == "SELL" and bool(row.get("limit_down", False)):
        return False
    return True


def _choose_direction(
    signal_row: pd.Series,
    options: SmartTOptions,
) -> Optional[Direction]:
    regime = str(signal_row["smart_regime"])
    if regime not in options.allowed_regimes or regime in {"OBSERVE", "NO_TRADE"}:
        return None

    top_ok = bool(signal_row["top_trigger"])
    bottom_ok = bool(signal_row["bottom_trigger"])

    # A股正T退出时也必须依靠原有可卖底仓，因此两种方向都要求底仓可用。
    sell_allowed = options.allow_sell_first and options.base_holding_available
    buy_allowed = (
        options.allow_buy_first
        and options.cash_available
        and options.base_holding_available
    )

    if regime == "UPTREND":
        return "BUY_FIRST" if buy_allowed and bottom_ok else None
    if regime == "DOWNTREND":
        return "SELL_FIRST" if sell_allowed and top_ok else None

    choose_sell = sell_allowed and top_ok
    choose_buy = buy_allowed and bottom_ok
    if choose_sell and choose_buy:
        return (
            "SELL_FIRST"
            if float(signal_row["top_score"]) >= float(signal_row["bottom_score"])
            else "BUY_FIRST"
        )
    if choose_sell:
        return "SELL_FIRST"
    if choose_buy:
        return "BUY_FIRST"
    return None


def apply_auction_direction_gate(
    chosen: Optional[Direction],
    signal_row: pd.Series,
    bars_so_far: pd.DataFrame,
    options: SmartTOptions,
) -> Tuple[Optional[Direction], str]:
    """用截至信号时点的开盘数据确认/撤销竞价方向，不读取未来K线。"""
    bias = options.auction_bias
    if bias == "WAIT":
        return chosen, "NO_BIAS"
    confirm_time = _parse_clock(options.auction_confirmation_time, "auction_confirmation_time")
    ts = pd.Timestamp(signal_row.name)
    if ts.time() < confirm_time:
        return None, "WAIT_0935"

    bars = bars_so_far.between_time("09:30", ts.strftime("%H:%M"))
    if len(bars) < 3:
        return None, "OPENING_BARS_INSUFFICIENT"
    first = bars.iloc[0]
    last = bars.iloc[-1]
    open_price = float(first["open"])
    first_high = float(first["high"])
    first_low = float(first["low"])
    last_price = float(last["close"])
    if "vwap" in bars.columns and pd.notna(last.get("vwap")):
        vwap = float(last["vwap"])
    else:
        typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
        volume = pd.to_numeric(bars.get("volume", 0), errors="coerce").fillna(0.0)
        vwap = float((typical * volume).sum() / volume.sum()) if volume.sum() > 0 else open_price
    lowering_highs = bool(len(bars) >= 3 and bars["high"].iloc[-1] < bars["high"].iloc[-2] < bars["high"].iloc[-3])
    rising_lows = bool(len(bars) >= 3 and bars["low"].iloc[-1] > bars["low"].iloc[-2] > bars["low"].iloc[-3])

    down_checks = (last_price < open_price, last_price < vwap, last_price < first_low, lowering_highs)
    up_checks = (last_price > open_price, last_price > vwap, last_price > first_high, rising_lows)
    confirmation = sum(down_checks if bias == "SELL_FIRST" else up_checks)
    invalidation = sum(up_checks if bias == "SELL_FIRST" else down_checks)
    if invalidation >= 2:
        return chosen, "INVALIDATED"
    if confirmation < 2:
        return None, "PENDING"
    if chosen != bias:
        return None, "DIRECTION_BLOCKED"
    return chosen, "CONFIRMED"


def simulate_smart_t_cycle(
    signals: pd.DataFrame,
    profile: SmartTProfile,
    options: SmartTOptions,
    config: SignalConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """将确认信号转换为日内做T状态机。

    关键约束：
    - 信号在当前1分钟K线收盘后才成立，模拟成交放在下一根K线开盘，
      避免使用同一根K线收盘价成交造成前视偏差；
    - 费用与双边滑点进入最低价差和净收益；
    - 包含逆向止损、最长持有、收盘前恢复、每日次数和累计亏损限制；
    - 每天独立，不把未完成循环静默跨到次日。
    """
    _validate_options(options)
    required = {
        "top_trigger",
        "bottom_trigger",
        "top_score",
        "bottom_score",
        "atr14",
        "open",
        "close",
        "smart_regime",
    }
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signals 缺少列: {sorted(missing)}")

    out = signals.copy()
    for col, default in {
        "smart_trade_state": "IDLE",
        "smart_trade_action": "",
        "smart_trade_reason": "",
        "smart_cycle_direction": "",
        "smart_signal_time": pd.NaT,
        "smart_entry_price": np.nan,
        "smart_required_spread": np.nan,
        "smart_current_spread": np.nan,
        "smart_net_return": np.nan,
        "smart_holding_bars": 0,
        "smart_auction_state": "NO_BIAS",
    }.items():
        out[col] = default

    records: list[SmartTradeRecord] = []
    force_time = _parse_clock(options.force_close_time, "force_close_time")
    cutoff_time = _parse_clock(options.new_cycle_cutoff_time, "new_cycle_cutoff_time")

    for date, day_index in out.groupby(out.index.normalize(), sort=True).groups.items():
        positions = out.index.get_indexer(day_index)
        state = "IDLE"
        direction: Optional[Direction] = None
        signal_time: Optional[pd.Timestamp] = None
        entry_time: Optional[pd.Timestamp] = None
        exit_signal_time: Optional[pd.Timestamp] = None
        entry_price: Optional[float] = None
        entry_atr: float = 0.0
        holding_bars = 0
        cooldown = 0
        cycles = 0
        daily_net = 0.0

        for local_i, pos in enumerate(positions):
            ts = out.index[pos]
            row = out.iloc[pos]
            previous_pos = positions[local_i - 1] if local_i > 0 else None
            signal_row = out.iloc[previous_pos] if previous_pos is not None else None
            raw_open = float(row["open"])

            if cooldown > 0:
                cooldown -= 1
                if cooldown == 0:
                    state = "IDLE"

            # 已开启循环先处理退出和风控；风控可在当前开盘立即执行。
            if state in {"WAIT_BUYBACK", "WAIT_SELL"} and entry_price is not None and entry_time is not None and direction is not None:
                holding_bars += 1
                side: Literal["BUY", "SELL"] = "BUY" if direction == "SELL_FIRST" else "SELL"
                executable = _can_execute(row, side)
                required_spread = (
                    _required_gross_spread(signal_row, profile, options, config)
                    if signal_row is not None
                    else profile.min_expected_net_rate
                )

                if direction == "SELL_FIRST":
                    actual_exit = _fill_price(raw_open, "BUY", options)
                    actual_spread = (entry_price - actual_exit) / entry_price
                    adverse_distance = actual_exit - entry_price
                    normal_exit = (
                        signal_row is not None
                        and bool(signal_row["bottom_trigger"])
                        and actual_spread >= required_spread
                    )
                else:
                    actual_exit = _fill_price(raw_open, "SELL", options)
                    actual_spread = (actual_exit - entry_price) / entry_price
                    adverse_distance = entry_price - actual_exit
                    normal_exit = (
                        signal_row is not None
                        and bool(signal_row["top_trigger"])
                        and actual_spread >= required_spread
                    )

                adverse_limit = max(
                    options.max_adverse_rate * entry_price,
                    options.max_adverse_atr_multiple * entry_atr,
                )
                risk_exit = adverse_distance >= adverse_limit
                time_exit = holding_bars >= options.max_holding_bars
                close_exit = options.force_close_enabled and ts.time() >= force_time

                out.iat[pos, out.columns.get_loc("smart_entry_price")] = entry_price
                out.iat[pos, out.columns.get_loc("smart_required_spread")] = required_spread
                out.iat[pos, out.columns.get_loc("smart_current_spread")] = actual_spread
                out.iat[pos, out.columns.get_loc("smart_cycle_direction")] = direction
                out.iat[pos, out.columns.get_loc("smart_signal_time")] = signal_time
                out.iat[pos, out.columns.get_loc("smart_holding_bars")] = holding_bars

                should_exit = executable and (normal_exit or risk_exit or time_exit or close_exit)
                if should_exit:
                    if normal_exit:
                        reason = "反向确认且预期净价差达标"
                        exit_signal_time = signal_row.name if signal_row is not None else None
                        action = "BUYBACK" if direction == "SELL_FIRST" else "SELL_T_EXIT"
                    elif risk_exit:
                        reason = "逆向波动超过风控阈值"
                        action = "RISK_BUYBACK" if direction == "SELL_FIRST" else "RISK_SELL"
                    elif time_exit:
                        reason = "超过最长持有时间"
                        action = "TIME_BUYBACK" if direction == "SELL_FIRST" else "TIME_SELL"
                    else:
                        reason = "临近收盘，恢复日内T仓状态"
                        action = "CLOSE_BUYBACK" if direction == "SELL_FIRST" else "CLOSE_SELL"

                    gross = actual_spread
                    net = gross - options.estimated_cycle_cost_rate
                    out.iat[pos, out.columns.get_loc("smart_trade_action")] = action
                    out.iat[pos, out.columns.get_loc("smart_trade_reason")] = reason
                    out.iat[pos, out.columns.get_loc("smart_net_return")] = net
                    records.append(
                        SmartTradeRecord(
                            date=str(pd.Timestamp(date).date()),
                            direction=direction,
                            signal_time=signal_time.isoformat() if signal_time is not None else entry_time.isoformat(),
                            entry_time=entry_time.isoformat(),
                            exit_signal_time=exit_signal_time.isoformat() if exit_signal_time is not None else None,
                            exit_time=ts.isoformat(),
                            entry_price=entry_price,
                            exit_price=actual_exit,
                            gross_return=gross,
                            estimated_cost_rate=options.estimated_cycle_cost_rate,
                            total_slippage_rate=2.0 * options.slippage_rate_per_side,
                            net_return=net,
                            result="WIN" if net > 0 else "LOSS",
                            exit_reason=reason,
                            holding_bars=holding_bars,
                        )
                    )
                    daily_net += net
                    cycles += 1
                    state = "COOLDOWN"
                    cooldown = profile.cooldown_bars
                    direction = None
                    signal_time = None
                    entry_time = None
                    exit_signal_time = None
                    entry_price = None
                    entry_atr = 0.0
                    holding_bars = 0
                    out.iat[pos, out.columns.get_loc("smart_trade_state")] = state
                    continue

            # 没有开启循环时，只在上一根K线信号已确认后，于本根开盘执行。
            if state == "IDLE":
                if cycles >= profile.max_daily_cycles:
                    state = "DAILY_LIMIT"
                elif daily_net <= -options.daily_loss_limit_rate:
                    state = "DAILY_LOSS_LIMIT"
                elif ts.time() >= cutoff_time:
                    state = "ENTRY_CUTOFF"
                elif signal_row is not None:
                    chosen = _choose_direction(signal_row, options)
                    chosen, auction_state = apply_auction_direction_gate(
                        chosen,
                        signal_row,
                        out.iloc[positions[:local_i]],
                        options,
                    )
                    out.iat[pos, out.columns.get_loc("smart_auction_state")] = auction_state
                    if chosen is not None:
                        side = "SELL" if chosen == "SELL_FIRST" else "BUY"
                        if _can_execute(row, side):
                            fill = _fill_price(raw_open, side, options)
                            state = "WAIT_BUYBACK" if chosen == "SELL_FIRST" else "WAIT_SELL"
                            direction = chosen
                            signal_time = signal_row.name
                            entry_time = ts
                            entry_price = fill
                            entry_atr = float(signal_row["atr14"]) if pd.notna(signal_row["atr14"]) else 0.0
                            holding_bars = 0
                            reason = (
                                "上一分钟冲高回落确认，卖出可T底仓"
                                if chosen == "SELL_FIRST"
                                else "上一分钟回踩止跌确认，买入T份额；后续卖出原有可卖底仓"
                            )
                            action = "SELL_T" if chosen == "SELL_FIRST" else "BUY_T"
                            out.iat[pos, out.columns.get_loc("smart_trade_action")] = action
                            out.iat[pos, out.columns.get_loc("smart_trade_reason")] = reason
                            out.iat[pos, out.columns.get_loc("smart_cycle_direction")] = chosen
                            out.iat[pos, out.columns.get_loc("smart_signal_time")] = signal_time
                            out.iat[pos, out.columns.get_loc("smart_entry_price")] = fill

            out.iat[pos, out.columns.get_loc("smart_trade_state")] = state
            if direction is not None:
                out.iat[pos, out.columns.get_loc("smart_cycle_direction")] = direction
            if signal_time is not None:
                out.iat[pos, out.columns.get_loc("smart_signal_time")] = signal_time
            if entry_price is not None:
                out.iat[pos, out.columns.get_loc("smart_entry_price")] = entry_price
            out.iat[pos, out.columns.get_loc("smart_holding_bars")] = holding_bars

        # 极端情况下没有14:50以后数据，但仍有未完成循环：若开启强制恢复，
        # 使用当天最后一根收盘价作为保守回测退出；否则明确标记为未完成风险。
        if state in {"WAIT_BUYBACK", "WAIT_SELL"} and entry_price is not None and entry_time is not None and direction is not None:
            last_pos = positions[-1]
            last_ts = out.index[last_pos]
            last_row = out.iloc[last_pos]
            if options.force_close_enabled and _can_execute(
                last_row,
                "BUY" if direction == "SELL_FIRST" else "SELL",
            ):
                raw_close = float(last_row["close"])
                exit_price = _fill_price(
                    raw_close,
                    "BUY" if direction == "SELL_FIRST" else "SELL",
                    options,
                )
                gross = (
                    (entry_price - exit_price) / entry_price
                    if direction == "SELL_FIRST"
                    else (exit_price - entry_price) / entry_price
                )
                net = gross - options.estimated_cycle_cost_rate
                reason = "当日最后数据，强制恢复日内T仓状态"
                action = "EOD_BUYBACK" if direction == "SELL_FIRST" else "EOD_SELL"
                out.iat[last_pos, out.columns.get_loc("smart_trade_action")] = action
                out.iat[last_pos, out.columns.get_loc("smart_trade_reason")] = reason
                out.iat[last_pos, out.columns.get_loc("smart_net_return")] = net
                out.iat[last_pos, out.columns.get_loc("smart_trade_state")] = "CLOSED"
                records.append(
                    SmartTradeRecord(
                        date=str(pd.Timestamp(date).date()),
                        direction=direction,
                        signal_time=signal_time.isoformat() if signal_time is not None else entry_time.isoformat(),
                        entry_time=entry_time.isoformat(),
                        exit_signal_time=None,
                        exit_time=last_ts.isoformat(),
                        entry_price=entry_price,
                        exit_price=exit_price,
                        gross_return=gross,
                        estimated_cost_rate=options.estimated_cycle_cost_rate,
                        total_slippage_rate=2.0 * options.slippage_rate_per_side,
                        net_return=net,
                        result="WIN" if net > 0 else "LOSS",
                        exit_reason=reason,
                        holding_bars=max(holding_bars, 1),
                    )
                )
            else:
                out.iat[last_pos, out.columns.get_loc("smart_trade_state")] = "OPEN_RISK"
                out.iat[last_pos, out.columns.get_loc("smart_trade_reason")] = "当日循环未完成，禁止静默跨日"

    trades = pd.DataFrame([asdict(item) for item in records])
    return out, trades


def _regime_label(regime: str) -> str:
    return {
        "OBSERVE": "开盘观察",
        "UPTREND": "上涨趋势",
        "RANGE": "震荡行情",
        "DOWNTREND": "弱势行情",
        "NO_TRADE": "暂不做T",
    }.get(regime, "未知行情")


def _state_label(state: str) -> str:
    return {
        "IDLE": "等待机会",
        "WAIT_BUYBACK": "等待回补",
        "WAIT_SELL": "等待卖出",
        "COOLDOWN": "信号冷却",
        "DAILY_LIMIT": "今日次数已满",
        "DAILY_LOSS_LIMIT": "今日亏损保护",
        "ENTRY_CUTOFF": "已停止开启新循环",
        "OPEN_RISK": "未完成循环风险",
        "CLOSED": "今日循环已恢复",
    }.get(state, state or "等待机会")


def build_frontend_payload(
    signals: pd.DataFrame,
    profile: SmartTProfile,
    options: SmartTOptions,
) -> Dict[str, Any]:
    if signals.empty:
        return {
            "mode": "智能做T",
            "sensitivity": profile.label,
            "regime": "暂无数据",
            "state": "等待行情",
            "signal": {"label": "暂无信号", "score": 0, "semantic": "neutral"},
            "settings": asdict(options),
        }

    last = signals.iloc[-1]
    base_ui = latest_ui_payload(signals)
    action = str(last.get("smart_trade_action", ""))
    reason = str(last.get("smart_trade_reason", ""))
    return {
        "mode": "智能做T" if options.sensitivity != "custom" else "自定义",
        "sensitivity": profile.label,
        "regime": _regime_label(str(last.get("smart_regime", "NO_TRADE"))),
        "regimeCode": str(last.get("smart_regime", "NO_TRADE")),
        "state": _state_label(str(last.get("smart_trade_state", "IDLE"))),
        "signal": {
            "label": base_ui.get("label", "正常"),
            "score": round(float(base_ui.get("score", 0.0)), 1),
            "semantic": base_ui.get("semantic", "neutral"),
            "referencePrice": base_ui.get("reference_price"),
            "invalidationPrice": base_ui.get("invalidation_price"),
            "reasons": base_ui.get("reasons", []),
        },
        "trade": {
            "action": action,
            "reason": reason,
            "signalTime": None if pd.isna(last.get("smart_signal_time", pd.NaT)) else pd.Timestamp(last["smart_signal_time"]).isoformat(),
            "entryPrice": None if pd.isna(last.get("smart_entry_price", np.nan)) else round(float(last["smart_entry_price"]), 4),
            "requiredGrossSpread": None if pd.isna(last.get("smart_required_spread", np.nan)) else round(float(last["smart_required_spread"]) * 100, 3),
            "currentGrossSpread": None if pd.isna(last.get("smart_current_spread", np.nan)) else round(float(last["smart_current_spread"]) * 100, 3),
            "holdingMinutes": int(last.get("smart_holding_bars", 0)),
        },
        "auctionGate": {
            "preferredDirection": options.auction_bias,
            "state": str(last.get("smart_auction_state", "NO_BIAS")),
            "confirmationTime": options.auction_confirmation_time,
        },
        "settings": {
            "marketFilter": options.market_filter_enabled,
            "importantAlerts": options.important_alerts_enabled,
            "maxDailyCycles": profile.max_daily_cycles,
            "minimumExpectedNetPercent": round(profile.min_expected_net_rate * 100, 2),
            "nextBarExecution": True,
            "newCycleCutoff": options.new_cycle_cutoff_time,
            "forceClose": options.force_close_time if options.force_close_enabled else None,
        },
        "updatedAt": signals.index[-1].isoformat(),
    }


def run_smart_t(
    minute_df: pd.DataFrame,
    options: Optional[SmartTOptions] = None,
    custom: Optional[Mapping[str, Any]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    opts = options or SmartTOptions()
    if custom and "allowed_regimes" in custom:
        allowed = tuple(str(item) for item in custom["allowed_regimes"])
        valid = {"UPTREND", "RANGE", "DOWNTREND"}
        if not allowed or not set(allowed).issubset(valid):
            raise ValueError("allowed_regimes 只能包含 UPTREND / RANGE / DOWNTREND")
        opts = replace(opts, allowed_regimes=allowed)
    _validate_options(opts)
    profile = resolve_profile(opts.sensitivity, custom)
    config = build_signal_config(profile, opts)
    signals = calculate_intraday_signals(minute_df, config)
    signals = add_market_regime(signals, profile, opts.market_filter_enabled)
    signals, trades = simulate_smart_t_cycle(signals, profile, opts, config)
    payload = build_frontend_payload(signals, profile, opts)
    return signals, trades, payload
