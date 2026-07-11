#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Random A-share intraday T simulation."""

from __future__ import annotations

import concurrent.futures
import contextlib
import io
import json
import os
import random
import re
import sys
import time as time_module
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Iterable, List, Optional

from auction_direction import evaluate_auction_gate
from services.trade_engine import PositionState, TradeCostModel

BASE_DIR = Path(__file__).resolve().parent
STOCK_POOL_CACHE = BASE_DIR / "a_share_pool_cache.json"
MINUTE_CACHE_DIR = BASE_DIR / "minute_cache"
SIM_HISTORY_PATH = BASE_DIR / "simulation_history.jsonl"
ADAPTIVE_STRATEGY_PATH = Path(os.environ.get("ADAPTIVE_STRATEGY_PATH") or BASE_DIR / "adaptive_strategy.json")
DEFAULT_STRATEGY = {
    "buy_min_dev": -1.15,
    "buy_max_dev": -2.8,
    "buy_rebound": 0.45,
    "buy_confirm": 5,
    "sell_min_dev": 1.35,
    "sell_max_dev": 2.8,
    "sell_fade": 1.2,
    "sell_confirm": 6,
    "hold_exit_minutes": 25,
    "min_take_profit_minutes": 25,
    "min_stop_minutes": 15,
    "fast_take_profit_pct": 1.8,
    "emergency_stop_pct": -2.2,
    "vwap_take_profit_pct": 0.25,
    "normal_take_profit_pct": 0.6,
    "late_take_profit_pct": 0.45,
    "vwap_reclaim_pct": 0.35,
    "vwap_exit_buffer_pct": 0.20,
    "opening_enabled": 1,
    "opening_drop_pct": -1.8,
    "opening_reclaim_pct": 0.45,
    "opening_spike_pct": 1.6,
    "opening_fade_pct": 0.38,
    "reverse_t_enabled": 1,
    "trade_end_hm": "14:00",
    "opening_reverse_strict": 0,
    "min_trade_quality": 9,
    "second_confirm_enabled": 1,
    "version": 3,
}
ACTIVE_STRATEGY: dict[str, float] = {}
SIM_RELAX_CONFIRM = 0
SIM_DAYS = 1
SMART_T_PROFILE = "balanced"
SIM_MODE = "strict"
SIM_BASE_SHARES = 6000
SIM_COST_MODEL = TradeCostModel()
ACTIVE_POSITION: PositionState | None = None
SMART_T_PROFILE_LABELS = {"steady": "稳健", "balanced": "平衡", "sensitive": "灵敏", "quantbrain": "量化学习"}
PREV_CLOSE_BY_SYMBOL: dict[str, float] = {}
LOCAL_STOCK_NAME_MAP = {
    "000630": "铜陵有色",
    "601899": "紫金矿业",
    "601012": "隆基绿能",
    "600580": "卧龙电驱",
}


@dataclass(frozen=True)
class Stock:
    name: str
    code: str
    symbol: str


@dataclass(frozen=True)
class Bar:
    hm: str
    price: float
    volume_lot: float
    amount_yuan: float
    date: str = ""


@dataclass(frozen=True)
class Result:
    stock: Stock
    action: str
    buy_time: str
    buy_price: float
    sell_time: str
    sell_price: float
    pnl_pct: float
    pnl_yuan: float
    trade_amount: float
    shares: int
    reason: str
    cycles: tuple[dict, ...] = ()
    gross_pnl_yuan: float = 0.0
    fees_yuan: float = 0.0
    position: dict | None = None
    daily_results: tuple[dict, ...] = ()

    @property
    def entry_time(self) -> str:
        if self.action == "未触发":
            return "99:99"
        if self.cycles:
            entries = [str(item.get("sellTime") if str(item.get("action") or "").startswith("反T") else item.get("buyTime") or "99:99") for item in self.cycles]
            return min(entries, default="99:99")
        if self.action.startswith("反T"):
            return self.sell_time
        return self.buy_time

    @property
    def exit_time(self) -> str:
        if self.action == "未触发":
            return "99:99"
        if self.cycles:
            exits = [str(item.get("buyTime") if str(item.get("action") or "").startswith("反T") else item.get("sellTime") or "99:99") for item in self.cycles]
            return max(exits, default="99:99")
        return self.buy_time if self.action.startswith("反T") else self.sell_time


def main(argv: list[str]) -> int:
    global ACTIVE_STRATEGY, SIM_DAYS, SMART_T_PROFILE, SIM_MODE, SIM_BASE_SHARES, SIM_COST_MODEL
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    SMART_T_PROFILE = _arg_value(argv, "--smart-profile", "balanced")
    if SMART_T_PROFILE not in SMART_T_PROFILE_LABELS:
        SMART_T_PROFILE = "balanced"
    ACTIVE_STRATEGY = apply_smart_t_profile(load_adaptive_strategy(), SMART_T_PROFILE)
    sample_size = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 10
    total_cash = _arg_float(argv, "--cash", 100000.0)
    per_trade = _arg_float(argv, "--per-trade", 20000.0)
    days = max(1, min(10, int(_arg_float(argv, "--days", 1.0))))
    SIM_DAYS = days
    SIM_MODE = _arg_value(argv, "--mode", "strict")
    if SIM_MODE not in {"strict", "scan"}:
        SIM_MODE = "strict"
    SIM_BASE_SHARES = max(0, int(_arg_float(argv, "--base-shares", 6000.0)) // 100 * 100)
    SIM_COST_MODEL = TradeCostModel(
        commission_rate=max(0.0, _arg_float(argv, "--commission-rate", 0.0003)),
        min_commission=max(0.0, _arg_float(argv, "--min-commission", 5.0)),
        stamp_duty_rate=max(0.0, _arg_float(argv, "--stamp-duty-rate", 0.0005)),
        transfer_fee_rate=max(0.0, _arg_float(argv, "--transfer-fee-rate", 0.00001)),
        slippage_bps=max(0.0, _arg_float(argv, "--slippage-bps", 2.0)),
    )
    max_trades = int(_arg_float(argv, "--max-trades", 0.0))
    json_file = _arg_value(argv, "--json-file", "")
    if per_trade <= 0:
        per_trade = max(total_cash / max(sample_size, 1), 1000.0)
    custom_stocks = _arg_value(argv, "--stocks", "")
    pool = build_random_pool()
    custom_pool = parse_custom_stock_pool(custom_stocks, pool)
    custom_mode = bool(custom_pool)
    if custom_pool:
        pool = custom_pool
        sample_size = min(max(sample_size, len(custom_pool)), len(custom_pool))

    scan_size = max(sample_size * 4, sample_size + 24)
    results: List[Result] = []
    minute_map: dict[str, List[Bar]] = {}
    random.shuffle(pool)
    selected = fetch_simulation_candidates(pool, scan_size, days)
    if days <= 1 and not custom_mode and len(selected) < scan_size:
        selected.extend(history_simulation_candidates(scan_size - len(selected), {stock.code for stock, _bars in selected}))
    skipped = max(0, min(len(pool), max(scan_size * 4, scan_size + 18)) - len(selected))
    for stock, bars in selected:
        minute_map[stock.code] = bars
        results.append(simulate_across_days(stock, bars, per_trade, days))

    if not results:
        print("\u4eca\u65e5\u5206\u65f6\u6570\u636e\u4e0d\u8db3\uff0c\u6682\u65f6\u65e0\u6cd5\u6a21\u62df\u3002")
        return 0

    results = apply_cash_constraints(results, total_cash)
    results = apply_daily_trade_limit(results, max_trades)
    results = select_display_results(results, sample_size, SIM_MODE)
    traded = [r for r in results if r.action != "\u672a\u89e6\u53d1"]
    completed_cycles = sum(len(r.cycles) if r.cycles else 1 for r in traded)
    wins = [r for r in traded if r.pnl_pct > 0]
    avg = sum(r.pnl_pct for r in traded) / len(traded) if traded else 0.0
    total_pnl = sum(r.pnl_yuan for r in traded)
    total_gross = sum(r.gross_pnl_yuan for r in traded)
    total_fees = sum(r.fees_yuan for r in traded)
    ending_cash = total_cash + total_pnl
    cash_return = total_pnl / total_cash * 100 if total_cash > 0 else 0.0
    win_rate = len(wins) / len(traded) * 100 if traded else 0.0

    today = datetime.now().strftime("%Y-%m-%d")
    if days > 1:
        print(f"\u3010\u968f\u673a{len(results)}\u80a1\u8fd1{days}\u5929\u505aT\u6a21\u62df\u3011{today}")
        print(f"\u8bf4\u660e\uff1a\u6309\u80a1\u7968\u9010\u65e5\u8ba1\u7b97VWAP\u548c\u4e70\u5356\u70b9\uff0c\u6c47\u603b\u771f\u5b9e\u65e5\u5ea6\u51c0\u6536\u76ca\uff0c\u4e0d\u518d\u6311\u9009\u4ee3\u8868\u65e5\u3002")
    else:
        print(f"\u3010\u968f\u673a{len(results)}\u80a1\u5f53\u65e5\u505aT\u6a21\u62df\u3011{today}")
    print(f"\u8d44\u91d1 {total_cash:,.0f}\u5143  \u5355\u7b14 {per_trade:,.0f}\u5143")
    print(f"\u56de\u6d4b\u6a21\u5f0f {'\u4e25\u683c\u968f\u673a' if SIM_MODE == 'strict' else '\u673a\u4f1a\u626b\u63cf'}  \u5e95\u4ed3 {SIM_BASE_SHARES}\u80a1  \u53ef\u5356\u4ec5\u9650\u6628\u4ed3")
    print(f"智能做T档位 {SMART_T_PROFILE_LABELS[SMART_T_PROFILE]}（正T/反T双向）")
    print(f"\u89e6\u53d1 {len(traded)}/{len(results)}  \u5b8c\u6210T\u5faa\u73af {completed_cycles}\u8f6e  \u80dc\u7387 {win_rate:.1f}%  \u5e73\u5747 {avg:+.2f}%")
    print(f"\u4ea4\u6613\u8d39\u7528 {total_fees:,.2f}\u5143  \u6bdb\u6536\u76ca {total_gross:+,.2f}\u5143")
    print(f"\u6a21\u62df\u76c8\u4e8f {total_pnl:+,.2f}\u5143  \u8d44\u91d1\u6536\u76ca {cash_return:+.2f}%")
    print(f"\u6eda\u52a8\u8d44\u91d1 {ending_cash:,.2f}\u5143")
    print(
        "\u81ea\u9002\u5e94\u7b56\u7565 "
        f"\u6b63T\u504f\u79bb{ACTIVE_STRATEGY['buy_min_dev']:.2f}%/{ACTIVE_STRATEGY['buy_max_dev']:.2f}% "
        f"\u786e\u8ba4{int(ACTIVE_STRATEGY['buy_confirm'])} "
        f"\u53cdT\u504f\u79bb{ACTIVE_STRATEGY['sell_min_dev']:.2f}%/{ACTIVE_STRATEGY['sell_max_dev']:.2f}% "
        f"\u786e\u8ba4{int(ACTIVE_STRATEGY['sell_confirm'])}"
    )
    if skipped:
        print(f"\u8df3\u8fc7 {skipped} \u53ea\uff1a\u5206\u65f6\u6570\u636e\u4e0d\u8db3")
    print("")
    for r in results:
        if r.action == "\u672a\u89e6\u53d1":
            print(f"{r.stock.name}({r.stock.code}) \u672a\u89e6\u53d1\uff1a{r.reason}")
        else:
            if r.action.startswith("反T"):
                route = f"{r.sell_time} {r.sell_price:.2f} -> {r.buy_time} {r.buy_price:.2f}"
            else:
                route = f"{r.buy_time} {r.buy_price:.2f} -> {r.sell_time} {r.sell_price:.2f}"
            print(f"{r.stock.name}({r.stock.code}) {r.action} {route} {r.pnl_pct:+.2f}%  {r.pnl_yuan:+.2f}\u5143  {r.shares}\u80a1  {r.reason}")
    if json_file:
        write_json_result(json_file, results, minute_map)
    return 0


def fetch_simulation_candidates(pool: List[Stock], sample_size: int, days: int = 1) -> list[tuple[Stock, List[Bar]]]:
    """Fetch minute bars in parallel so random simulations do not crawl stock by stock."""
    # Keep random tests responsive: scan enough symbols for variety, but do not
    # let slow quote providers hold the UI hostage.
    scan_factor = 2 if days > 1 else 3
    max_attempts = min(len(pool), max(sample_size * scan_factor, sample_size + 18), 90 if days > 1 else 120)
    candidates = pool[:max_attempts]
    if not candidates:
        return []
    out: list[tuple[Stock, List[Bar]]] = []
    workers = min(12, max(4, min(sample_size, 30)))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(fetch_minutes, stock.symbol, days): stock for stock in candidates}
    try:
        deadline = time_module.time() + (14 if days > 1 else 10)
        pending = set(futures)
        while pending and len(out) < sample_size and time_module.time() < deadline:
            done, pending = concurrent.futures.wait(pending, timeout=0.6, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                stock = futures[fut]
                try:
                    bars = fut.result()
                except Exception:
                    continue
                if len(bars) < 30:
                    continue
                out.append((stock, bars))
                if len(out) >= sample_size:
                    break
        for fut in pending:
            fut.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return out


def simulate_across_days(stock: Stock, bars: List[Bar], trade_amount: float, days: int) -> Result:
    groups = split_bars_by_date(bars)
    if days <= 1 or len(groups) <= 1:
        return simulate_one(stock, bars, trade_amount, PREV_CLOSE_BY_SYMBOL.get(stock.symbol))

    daily_results: list[tuple[str, Result]] = []
    selected_groups = groups[-days:]
    for group_index, (date, day_bars) in enumerate(selected_groups):
        if len(day_bars) < 30:
            continue
        previous_close = None
        absolute_index = groups.index((date, day_bars))
        if absolute_index > 0 and groups[absolute_index - 1][1]:
            previous_close = groups[absolute_index - 1][1][-1].price
        elif group_index == 0:
            previous_close = PREV_CLOSE_BY_SYMBOL.get(stock.symbol)
        daily_results.append((date, simulate_one(stock, day_bars, trade_amount, previous_close)))
    if not daily_results:
        return simulate_one(stock, bars, trade_amount, PREV_CLOSE_BY_SYMBOL.get(stock.symbol))

    latest_date, latest = daily_results[-1]
    traded = [(date, result) for date, result in daily_results if result.action != "\u672a\u89e6\u53d1"]
    net_pnl = sum(result.pnl_yuan for _date, result in daily_results)
    gross_pnl = sum(result.gross_pnl_yuan for _date, result in daily_results)
    fees = sum(result.fees_yuan for _date, result in daily_results)
    active_days = len(traded)
    winning_days = sum(1 for _date, result in traded if result.pnl_yuan > 0)
    daily_payload = tuple({
        "date": date, "action": result.action, "pnl": result.pnl_pct,
        "money": result.pnl_yuan, "fees": result.fees_yuan,
    } for date, result in daily_results)
    base_amount = max(trade_amount * max(active_days, 1), 1.0)
    return Result(
        stock, f"\u8fd1{len(daily_results)}\u65e5\u6c47\u603b", latest.buy_time, latest.buy_price,
        latest.sell_time, latest.sell_price, net_pnl / base_amount * 100.0, net_pnl,
        trade_amount, latest.shares,
        f"\u8fd1{len(daily_results)}\u65e5\u771f\u5b9e\u805a\u5408\uff1a\u89e6\u53d1{active_days}\u65e5\uff0c\u76c8\u5229{winning_days}\u65e5\uff0c\u6bcf\u65e5\u72ec\u7acb\u6062\u590d\u5e95\u4ed3\u3002\u6700\u8fd1\u65e5{latest_date}\uff1a{latest.reason}",
        latest.cycles, gross_pnl, fees, latest.position, daily_payload,
    )


def split_bars_by_date(bars: List[Bar]) -> list[tuple[str, List[Bar]]]:
    if not bars:
        return []
    groups: dict[str, list[Bar]] = {}
    order: list[str] = []
    for bar in bars:
        date = bar.date or datetime.now().strftime("%Y-%m-%d")
        if date not in groups:
            groups[date] = []
            order.append(date)
        groups[date].append(bar)
    return [(date, groups[date]) for date in order if len(groups[date]) >= 1]


def clone_result_with_reason(result: Result, stock: Stock, reason: str) -> Result:
    return Result(
        stock,
        result.action,
        result.buy_time,
        result.buy_price,
        result.sell_time,
        result.sell_price,
        result.pnl_pct,
        result.pnl_yuan,
        result.trade_amount,
        result.shares,
        reason,
        result.cycles,
        result.gross_pnl_yuan,
        result.fees_yuan,
        result.position,
        result.daily_results,
    )


def history_simulation_candidates(sample_size: int, used_codes: set[str]) -> list[tuple[Stock, List[Bar]]]:
    if sample_size <= 0:
        return []
    try:
        lines = SIM_HISTORY_PATH.read_text(encoding="utf-8").splitlines()[-700:]
    except Exception:
        return []
    rows: list[tuple[Stock, List[Bar]]] = []
    seen = set(used_codes)
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        for row in item.get("stocks") or []:
            code = str(row.get("code") or "")
            name = str(row.get("name") or code)
            if len(code) != 6 or code in seen:
                continue
            symbol = ("sh" if code.startswith(("6", "9")) else "sz") + code
            prices = row.get("prices") or []
            bars: list[Bar] = []
            for p in prices:
                try:
                    hm = _hm(str(p.get("time") or ""))
                    price = float(p.get("price") or 0.0)
                    if price <= 0:
                        continue
                    volume = 100.0
                    bars.append(Bar(hm, price, volume, price * volume * 100.0))
                except Exception:
                    continue
            if len(bars) < 30:
                continue
            rows.append((Stock(name, code, symbol), bars))
            seen.add(code)
            if len(rows) >= sample_size:
                return rows
    return rows


def apply_cash_constraints(results: List[Result], total_cash: float) -> List[Result]:
    out: list[Result] = []
    active: list[tuple[int, float]] = []
    for result in sorted(results, key=lambda r: r.entry_time):
        if result.action == "未触发":
            out.append(result)
            continue
        start_min = _hm_to_minutes(result.entry_time)
        end_min = max(_hm_to_minutes(result.exit_time), start_min + 1)
        active = [(end, amount) for end, amount in active if end > start_min]
        used = sum(amount for _end, amount in active)
        available = total_cash - used
        if result.trade_amount > available + 1e-6:
            out.append(
                Result(
                    result.stock,
                    "未触发",
                    "--:--",
                    0.0,
                    "--:--",
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0,
                    f"资金不足未执行：需要{result.trade_amount:,.0f}元，可用{available:,.0f}元",
                )
            )
            continue
        active.append((end_min, result.trade_amount))
        out.append(result)
    return sorted(out, key=lambda r: r.stock.code)


def apply_daily_trade_limit(results: List[Result], max_trades: int) -> List[Result]:
    min_quality = int((ACTIVE_STRATEGY or DEFAULT_STRATEGY).get("min_trade_quality", 0))
    traded = [r for r in results if r.action != "未触发"]
    allowed_traded = [r for r in traded if _trade_quality_score(r) >= min_quality]
    if max_trades > 0 and len(allowed_traded) > max_trades:
        allowed_traded = sorted(allowed_traded, key=_trade_quality_key, reverse=True)[:max_trades]
    allowed = {id(r) for r in allowed_traded}
    out: List[Result] = []
    for result in results:
        if result.action != "未触发" and id(result) not in allowed:
            quality = _trade_quality_score(result)
            out.append(
                Result(
                    result.stock,
                    "未触发",
                    "--:--",
                    0.0,
                    "--:--",
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0,
                    f"信号质量不足：质量{quality}/{min_quality}，不为了凑交易次数强行做T",
                )
            )
        else:
            out.append(result)
    return out


def select_display_results(results: List[Result], sample_size: int, mode: str = "strict") -> List[Result]:
    if sample_size <= 0 or len(results) <= sample_size:
        return results
    if mode == "strict":
        # Preserve the random sample order: this mode must not cherry-pick wins.
        return results[:sample_size]
    ranked = sorted(results, key=_display_quality_key, reverse=True)
    return sorted(ranked[:sample_size], key=lambda r: (r.action == "未触发", r.stock.code))


def _display_quality_key(result: Result) -> tuple[float, float]:
    score = 0.0
    if result.action != "未触发":
        score += 100.0
    if result.action.startswith("\u6b63T"):
        score += 20.0
    reason = result.reason or ""
    if "低吸未确认" in reason:
        score += 8.0
    if "反T未确认" in reason:
        score += 3.0
    if "VWAP偏离不足" in reason or "空间不足" in reason:
        score -= 4.0
    return (score, abs(result.pnl_pct))


def _trade_quality_key(result: Result) -> tuple[float, float, int]:
    action = result.action
    score = 0.0
    if action.startswith("正T"):
        score += 10.0
    if result.entry_time < "10:05":
        score -= 3.0
    if result.entry_time > "14:00":
        score -= 1.5
    return (_trade_quality_score(result), result.trade_amount, -_hm_to_minutes(result.entry_time))


def _trade_quality_score(result: Result) -> int:
    return _trade_quality_score_clean(result)


def _trade_quality_score_clean(result: Result) -> int:
    if result.cycles:
        scores = []
        for cycle in result.cycles:
            scores.append(_trade_quality_score_clean(Result(
                result.stock,
                str(cycle.get("action") or "未触发"),
                str(cycle.get("buyTime") or "--:--"),
                float(cycle.get("buyPrice") or 0),
                str(cycle.get("sellTime") or "--:--"),
                float(cycle.get("sellPrice") or 0),
                float(cycle.get("pnl") or 0),
                float(cycle.get("money") or 0),
                result.trade_amount,
                int(cycle.get("shares") or 0),
                str(cycle.get("reason") or ""),
            )))
        return round(sum(scores) / len(scores)) if scores else 0
    action = result.action
    is_positive_t = action.startswith("\u6b63T")
    is_reverse_t = action.startswith("\u53cdT")
    if action == "\u672a\u89e6\u53d1":
        return 0
    score = 0
    entry = _hm_to_minutes(result.entry_time)
    opening_reverse = is_reverse_t and (9 * 60 + 35 <= entry <= 10 * 60 + 5)
    opening_positive = is_positive_t and (9 * 60 + 35 <= entry <= 10 * 60 + 5)
    exit_text = result.sell_time if is_positive_t else result.buy_time
    hold = max(_hm_to_minutes(exit_text) - entry, 0)
    if is_positive_t:
        score += 5
    elif is_reverse_t:
        score += 2
    if opening_reverse:
        score += 5
    if opening_positive:
        score += 4
    if 9 * 60 + 45 <= entry <= 10 * 60 + 20:
        score += 4
    elif 10 * 60 + 20 < entry <= 11 * 60:
        score += 2
    elif 13 * 60 <= entry <= 13 * 60 + 35:
        score += 2
    if entry < 9 * 60 + 45 and not (opening_reverse or opening_positive):
        score -= 3
    if entry >= 13 * 60 + 50:
        score -= 4
    if hold < (5 if opening_reverse else 8):
        score -= 2
    elif 10 <= hold <= 28:
        score += 2
    if "\u5c3e\u76d8" in action:
        score -= 5
    if "\u6b62\u635f" in action:
        score -= 1
    return max(0, min(20, score))


def _trade_quality_score_legacy(result: Result) -> int:
    if result.action == "未触发":
        return 0
    score = 0
    entry = _hm_to_minutes(result.entry_time)
    exit_text = result.sell_time if result.action.startswith("正T") else result.buy_time
    hold = max(_hm_to_minutes(exit_text) - entry, 0)
    if result.action.startswith("正T"):
        score += 5
    elif result.action.startswith("反T"):
        score += 2
    if 9 * 60 + 45 <= entry <= 10 * 60 + 20:
        score += 4
    elif 10 * 60 + 20 < entry <= 11 * 60:
        score += 2
    elif 13 * 60 <= entry <= 13 * 60 + 35:
        score += 2
    if entry < 9 * 60 + 45:
        score -= 3
    if entry >= 13 * 60 + 50:
        score -= 4
    if hold < 8:
        score -= 2
    elif 10 <= hold <= 28:
        score += 2
    if "尾盘" in result.action:
        score -= 5
    if "止损" in result.action:
        score -= 1
    return max(0, min(20, score))


def _hm_to_minutes(hm: str) -> int:
    try:
        h, m = [int(x) for x in hm.split(":", 1)]
        return h * 60 + m
    except Exception:
        return 24 * 60


def load_adaptive_strategy() -> dict[str, float]:
    strategy = dict(DEFAULT_STRATEGY)
    try:
        data = json.loads(ADAPTIVE_STRATEGY_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    for key, default in DEFAULT_STRATEGY.items():
        try:
            strategy[key] = type(default)(data.get(key, default))
        except Exception:
            strategy[key] = default
    strategy["buy_min_dev"] = max(-2.2, min(-0.7, float(strategy["buy_min_dev"])))
    strategy["buy_max_dev"] = max(-3.0, min(-1.5, float(strategy["buy_max_dev"])))
    strategy["buy_rebound"] = max(0.15, min(1.2, float(strategy["buy_rebound"])))
    strategy["buy_confirm"] = max(3, min(6, int(strategy["buy_confirm"])))
    strategy["sell_min_dev"] = max(0.7, min(1.8, float(strategy["sell_min_dev"])))
    strategy["sell_max_dev"] = max(1.8, min(3.2, float(strategy["sell_max_dev"])))
    strategy["sell_fade"] = max(0.10, min(1.3, float(strategy["sell_fade"])))
    strategy["sell_confirm"] = max(3, min(6, int(strategy["sell_confirm"])))
    strategy["hold_exit_minutes"] = max(15, min(35, int(strategy["hold_exit_minutes"])))
    strategy["min_take_profit_minutes"] = max(15, min(35, int(strategy.get("min_take_profit_minutes", 25))))
    strategy["min_stop_minutes"] = max(10, min(25, int(strategy.get("min_stop_minutes", 15))))
    strategy["fast_take_profit_pct"] = max(1.2, min(3.0, float(strategy.get("fast_take_profit_pct", 1.8))))
    strategy["emergency_stop_pct"] = max(-3.5, min(-1.6, float(strategy.get("emergency_stop_pct", -2.2))))
    strategy["vwap_take_profit_pct"] = max(0.10, min(1.0, float(strategy.get("vwap_take_profit_pct", 0.25))))
    strategy["normal_take_profit_pct"] = max(0.20, min(1.5, float(strategy.get("normal_take_profit_pct", 0.6))))
    strategy["late_take_profit_pct"] = max(0.15, min(1.2, float(strategy.get("late_take_profit_pct", 0.45))))
    strategy["vwap_reclaim_pct"] = max(0.20, min(0.90, float(strategy.get("vwap_reclaim_pct", 0.35))))
    strategy["vwap_exit_buffer_pct"] = max(0.05, min(0.60, float(strategy.get("vwap_exit_buffer_pct", 0.20))))
    strategy["opening_enabled"] = max(0, min(1, int(strategy.get("opening_enabled", 1))))
    strategy["opening_drop_pct"] = max(-4.5, min(-1.0, float(strategy.get("opening_drop_pct", -1.8))))
    strategy["opening_reclaim_pct"] = max(0.25, min(1.2, float(strategy.get("opening_reclaim_pct", 0.45))))
    strategy["opening_spike_pct"] = max(1.4, min(4.5, float(strategy.get("opening_spike_pct", 2.4))))
    strategy["opening_fade_pct"] = max(0.25, min(1.5, float(strategy.get("opening_fade_pct", 0.65))))
    strategy["trade_end_hm"] = str(strategy.get("trade_end_hm") or "14:00")
    strategy["opening_reverse_strict"] = max(0, min(1, int(strategy.get("opening_reverse_strict", 1))))
    strategy["min_trade_quality"] = max(0, min(20, int(strategy.get("min_trade_quality", 9))))
    strategy["second_confirm_enabled"] = max(0, min(1, int(strategy.get("second_confirm_enabled", 1))))
    return strategy


def apply_smart_t_profile(strategy: dict[str, float], profile: str) -> dict[str, float]:
    """给模拟测试应用三档有界参数；不修改硬风控与费用。"""
    out = dict(strategy)
    if profile == "steady":
        out["min_trade_quality"] = max(12, int(out.get("min_trade_quality", 9)))
        out["buy_confirm"] = min(6, max(5, int(out.get("buy_confirm", 5))))
        out["sell_confirm"] = min(6, max(6, int(out.get("sell_confirm", 6))))
        out["opening_reverse_strict"] = 1
        out["second_confirm_enabled"] = 1
    elif profile == "sensitive":
        out["min_trade_quality"] = min(7, int(out.get("min_trade_quality", 9)))
        out["buy_confirm"] = max(3, int(out.get("buy_confirm", 5)) - 1)
        out["sell_confirm"] = max(3, int(out.get("sell_confirm", 6)) - 1)
        out["opening_reverse_strict"] = 0
        out["second_confirm_enabled"] = 1
        out["max_daily_cycles"] = 5
        out["cycle_cooldown_minutes"] = 5
    elif profile == "quantbrain":
        out["min_trade_quality"] = max(8, int(out.get("min_trade_quality", 9)))
        out["buy_confirm"] = max(4, min(6, int(out.get("buy_confirm", 5))))
        out["sell_confirm"] = max(4, min(6, int(out.get("sell_confirm", 6))))
        out["opening_reverse_strict"] = 1
        out["second_confirm_enabled"] = 1
        out["quantbrain_enabled"] = 1
        out["max_daily_cycles"] = 4
        out["cycle_cooldown_minutes"] = 8
    else:
        out["min_trade_quality"] = max(9, int(out.get("min_trade_quality", 9)))
        out["second_confirm_enabled"] = 1
        out["max_daily_cycles"] = 3
        out["cycle_cooldown_minutes"] = 10
    if profile == "steady":
        out["max_daily_cycles"] = 2
        out["cycle_cooldown_minutes"] = 15
    return out


def write_json_result(path: str, results: List[Result], minute_map: dict[str, List[Bar]]) -> None:
    payload = []
    for result in results:
        bars = minute_map.get(result.stock.code, [])
        payload.append(
            {
                "name": result.stock.name,
                "code": result.stock.code,
                "action": result.action,
                "buyTime": result.buy_time,
                "sellTime": result.sell_time,
                "pnl": result.pnl_pct,
                "money": result.pnl_yuan,
                "grossPnl": result.gross_pnl_yuan,
                "fees": result.fees_yuan,
                "tradeAmount": result.trade_amount,
                "shares": result.shares,
                "reason": result.reason,
                "cycles": list(result.cycles),
                "position": result.position or {},
                "dailyResults": list(result.daily_results),
                "prices": [{"time": b.hm, "price": b.price} for b in bars],
            }
        )
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return


def fetch_stock_pool(max_pages: int = 80) -> List[Stock]:
    ak_pool = fetch_stock_pool_akshare()
    if ak_pool:
        return ak_pool
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)
    futures = [executor.submit(fetch_stock_pool_page, page) for page in range(1, max_pages + 1)]
    done, _pending = concurrent.futures.wait(futures, timeout=30)
    executor.shutdown(wait=False, cancel_futures=True)
    stocks: list[Stock] = []
    seen: set[str] = set()
    for fut in done:
        try:
            page_stocks = fut.result()
        except Exception:
            continue
        for stock in page_stocks:
            if stock.code not in seen:
                seen.add(stock.code)
                stocks.append(stock)
    return stocks


def fetch_stock_pool_akshare() -> List[Stock]:
    try:
        import akshare as ak

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_info_a_code_name()
    except Exception:
        return []
    stocks: list[Stock] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        code = str(row.get("code") or row.get("代码") or "").strip()
        name = str(row.get("name") or row.get("名称") or "").strip()
        if len(code) != 6 or code in seen or not _is_common_a_share(code, name):
            continue
        prefix = "sh" if code.startswith("6") else "sz"
        seen.add(code)
        stocks.append(Stock(name, code, prefix + code))
    return stocks


def fetch_stock_pool_page(page: int) -> List[Stock]:
    params = {
        "pn": page,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f6",
        "fs": "m:1+t:2,m:0+t:6",
        "fields": "f12,f14,f2,f3,f5,f6",
    }
    url = "http://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    data = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "replace"))
    rows = data.get("data", {}).get("diff", []) if isinstance(data, dict) else []
    stocks: list[Stock] = []
    for item in rows:
        code = str(item.get("f12") or "")
        name = str(item.get("f14") or "")
        price = _to_float(item.get("f2"))
        if not _is_common_a_share(code, name):
            continue
        if price <= 1:
            continue
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        stocks.append(Stock(name, code, prefix + code))
    return stocks


def fallback_stock_pool() -> List[Stock]:
    items = [
        ("铜陵有色", "000630", "sz000630"),
        ("\u7d2b\u91d1\u77ff\u4e1a", "601899", "sh601899"),
        ("\u9686\u57fa\u7eff\u80fd", "601012", "sh601012"),
        ("\u4e2d\u4fe1\u8bc1\u5238", "600030", "sh600030"),
        ("\u4e2d\u91d1\u516c\u53f8", "601995", "sh601995"),
        ("\u4e2d\u56fd\u5e73\u5b89", "601318", "sh601318"),
        ("\u62db\u5546\u94f6\u884c", "600036", "sh600036"),
        ("\u8d35\u5dde\u8305\u53f0", "600519", "sh600519"),
        ("\u4e94\u7cae\u6db2", "000858", "sz000858"),
        ("\u7f8e\u7684\u96c6\u56e2", "000333", "sz000333"),
        ("\u6bd4\u4e9a\u8fea", "002594", "sz002594"),
        ("\u5b81\u5fb7\u65f6\u4ee3", "300750", "sz300750"),
        ("\u4e1c\u65b9\u8d22\u5bcc", "300059", "sz300059"),
        ("\u4e2d\u9645\u65ed\u521b", "300308", "sz300308"),
        ("\u65b0\u6613\u76db", "300502", "sz300502"),
        ("\u5de5\u4e1a\u5bcc\u8054", "601138", "sh601138"),
        ("\u4e2d\u56fd\u795e\u534e", "601088", "sh601088"),
        ("\u4e2d\u56fd\u8239\u8236", "600150", "sh600150"),
        ("\u957f\u5b89\u6c7d\u8f66", "000625", "sz000625"),
        ("\u4e2d\u5174\u901a\u8baf", "000063", "sz000063"),
        ("\u4e2d\u79d1\u66d9\u5149", "603019", "sh603019"),
        ("\u4e09\u82b1\u667a\u63a7", "002050", "sz002050"),
        ("\u8d5b\u529b\u65af", "601127", "sh601127"),
        ("\u5929\u98ce\u8bc1\u5238", "601162", "sh601162"),
        ("\u795e\u706b\u80a1\u4efd", "000933", "sz000933"),
        ("\u536b\u661f\u5316\u5b66", "002648", "sz002648"),
        ("\u6850\u6606\u80a1\u4efd", "601233", "sh601233"),
        ("\u607a\u82f1\u7f51\u7edc", "002517", "sz002517"),
        ("\u4eac\u4e1c\u65b9A", "000725", "sz000725"),
        ("\u6c5f\u6dee\u6c7d\u8f66", "600418", "sh600418"),
        ("\u957f\u6c5f\u7535\u529b", "600900", "sh600900"),
        ("平安银行", "000001", "sz000001"),
        ("万科A", "000002", "sz000002"),
        ("TCL科技", "000100", "sz000100"),
        ("潍柴动力", "000338", "sz000338"),
        ("格力电器", "000651", "sz000651"),
        ("中信海直", "000099", "sz000099"),
        ("中国稀土", "000831", "sz000831"),
        ("浪潮信息", "000977", "sz000977"),
        ("分众传媒", "002027", "sz002027"),
        ("苏宁环球", "000718", "sz000718"),
        ("云南白药", "000538", "sz000538"),
        ("泸州老窖", "000568", "sz000568"),
        ("盐湖股份", "000792", "sz000792"),
        ("东方盛虹", "000301", "sz000301"),
        ("山西汾酒", "600809", "sh600809"),
        ("伊利股份", "600887", "sh600887"),
        ("恒瑞医药", "600276", "sh600276"),
        ("药明康德", "603259", "sh603259"),
        ("海天味业", "603288", "sh603288"),
        ("中国中免", "601888", "sh601888"),
        ("中国建筑", "601668", "sh601668"),
        ("中国中铁", "601390", "sh601390"),
        ("中国铁建", "601186", "sh601186"),
        ("中国交建", "601800", "sh601800"),
        ("中国石油", "601857", "sh601857"),
        ("中国石化", "600028", "sh600028"),
        ("中国海油", "600938", "sh600938"),
        ("陕西煤业", "601225", "sh601225"),
        ("兖矿能源", "600188", "sh600188"),
        ("中国铝业", "601600", "sh601600"),
        ("江西铜业", "600362", "sh600362"),
        ("洛阳钼业", "603993", "sh603993"),
        ("山东黄金", "600547", "sh600547"),
        ("中金黄金", "600489", "sh600489"),
        ("北方稀土", "600111", "sh600111"),
        ("包钢股份", "600010", "sh600010"),
        ("宝钢股份", "600019", "sh600019"),
        ("华友钴业", "603799", "sh603799"),
        ("天齐锂业", "002466", "sz002466"),
        ("赣锋锂业", "002460", "sz002460"),
        ("阳光电源", "300274", "sz300274"),
        ("通威股份", "600438", "sh600438"),
        ("晶澳科技", "002459", "sz002459"),
        ("TCL中环", "002129", "sz002129"),
        ("天合光能", "688599", "sh688599"),
        ("亿纬锂能", "300014", "sz300014"),
        ("欣旺达", "300207", "sz300207"),
        ("恩捷股份", "002812", "sz002812"),
        ("科大讯飞", "002230", "sz002230"),
        ("海康威视", "002415", "sz002415"),
        ("大华股份", "002236", "sz002236"),
        ("立讯精密", "002475", "sz002475"),
        ("歌尔股份", "002241", "sz002241"),
        ("蓝思科技", "300433", "sz300433"),
        ("领益智造", "002600", "sz002600"),
        ("北方华创", "002371", "sz002371"),
        ("韦尔股份", "603501", "sh603501"),
        ("兆易创新", "603986", "sh603986"),
        ("中芯国际", "688981", "sh688981"),
        ("澜起科技", "688008", "sh688008"),
        ("寒武纪", "688256", "sh688256"),
        ("三六零", "601360", "sh601360"),
        ("昆仑万维", "300418", "sz300418"),
        ("中文在线", "300364", "sz300364"),
        ("同花顺", "300033", "sz300033"),
        ("指南针", "300803", "sz300803"),
        ("东方证券", "600958", "sh600958"),
        ("华泰证券", "601688", "sh601688"),
        ("国泰君安", "601211", "sh601211"),
        ("海通证券", "600837", "sh600837"),
        ("兴业银行", "601166", "sh601166"),
        ("工商银行", "601398", "sh601398"),
        ("建设银行", "601939", "sh601939"),
        ("农业银行", "601288", "sh601288"),
        ("邮储银行", "601658", "sh601658"),
        ("宁波银行", "002142", "sz002142"),
        ("江苏银行", "600919", "sh600919"),
        ("上海机场", "600009", "sh600009"),
        ("中国国航", "601111", "sh601111"),
        ("南方航空", "600029", "sh600029"),
        ("春秋航空", "601021", "sh601021"),
        ("顺丰控股", "002352", "sz002352"),
        ("韵达股份", "002120", "sz002120"),
        ("牧原股份", "002714", "sz002714"),
        ("温氏股份", "300498", "sz300498"),
        ("新希望", "000876", "sz000876"),
        ("万华化学", "600309", "sh600309"),
        ("荣盛石化", "002493", "sz002493"),
        ("恒力石化", "600346", "sh600346"),
        ("迈瑞医疗", "300760", "sz300760"),
        ("爱尔眼科", "300015", "sz300015"),
        ("智飞生物", "300122", "sz300122"),
        ("泰格医药", "300347", "sz300347"),
        ("片仔癀", "600436", "sh600436"),
        ("上海电气", "601727", "sh601727"),
        ("三一重工", "600031", "sh600031"),
        ("徐工机械", "000425", "sz000425"),
        ("中联重科", "000157", "sz000157"),
    ]
    return [Stock(name, code, symbol) for name, code, symbol in items]


def build_random_pool() -> List[Stock]:
    """Use a larger live pool first so repeated simulations do not circle the same 30 stocks."""
    pool = load_cached_stock_pool()
    if not pool:
        pool = fetch_stock_pool()
        save_cached_stock_pool(pool)
    by_code = {stock.code: stock for stock in pool}
    for stock in fallback_stock_pool():
        by_code.setdefault(stock.code, stock)
    rows = list(by_code.values())
    return rows if rows else fallback_stock_pool()


def parse_custom_stock_pool(text: str, pool: List[Stock]) -> List[Stock]:
    tokens = re.findall(r"(?:sh|sz)?\d{6}", str(text or ""), re.I)
    if not tokens:
        return []
    by_code = {stock.code: stock for stock in pool}
    for stock in fallback_stock_pool():
        by_code.setdefault(stock.code, stock)
    out: list[Stock] = []
    seen: set[str] = set()
    for token in tokens:
        code_match = re.search(r"(\d{6})", token)
        if not code_match:
            continue
        code = code_match.group(1)
        if code in seen:
            continue
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
        stock = by_code.get(code) or Stock(LOCAL_STOCK_NAME_MAP.get(code, code), code, prefix + code)
        out.append(stock)
        seen.add(code)
        if len(out) >= 30:
            break
    return out


def load_cached_stock_pool() -> List[Stock]:
    try:
        data = json.loads(STOCK_POOL_CACHE.read_text(encoding="utf-8"))
        if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return []
        rows = data.get("stocks") or []
        return [Stock(str(x["name"]), str(x["code"]), str(x["symbol"])) for x in rows if x.get("code")]
    except Exception:
        return []


def save_cached_stock_pool(pool: List[Stock]) -> None:
    if len(pool) < 500:
        return
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "stocks": [{"name": s.name, "code": s.code, "symbol": s.symbol} for s in pool],
    }
    try:
        STOCK_POOL_CACHE.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except Exception:
        pass


def fetch_minutes(symbol: str, days: int = 1) -> List[Bar]:
    if days > 1:
        bars = fetch_minutes_eastmoney(symbol, days)
        if len(bars) >= 30:
            return bars

    url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?_var=js&code={symbol}"
    try:
        text = _get(url, "utf-8", 10)
        if "=" not in text:
            text = ""
            rows = []
        else:
            data = json.loads(text.split("=", 1)[1].strip())
            rows = data.get("data", {}).get(symbol, {}).get("data", {}).get("data", []) or []
    except Exception:
        rows = []

    bars: List[Bar] = []
    last_volume = 0.0
    last_amount = 0.0
    for row in rows:
        parts = row.split()
        if len(parts) < 4:
            continue
        try:
            volume = float(parts[2])
            amount = float(parts[3])
            minute_volume = max(volume - last_volume, 0.0)
            minute_amount = max(amount - last_amount, 0.0)
            last_volume = volume
            last_amount = amount
            bars.append(Bar(_hm(parts[0]), float(parts[1]), minute_volume, minute_amount, datetime.now().strftime("%Y-%m-%d")))
        except Exception:
            continue
    if len(bars) >= 30:
        save_cached_minutes(symbol, bars)
        return bars
    bars = fetch_minutes_eastmoney(symbol, 1) or fetch_minutes_sina(symbol)
    if len(bars) >= 30:
        save_cached_minutes(symbol, bars)
        return bars
    return load_cached_minutes(symbol) or load_history_price_bars(symbol)


def save_cached_minutes(symbol: str, bars: List[Bar]) -> None:
    if len(bars) < 30:
        return
    try:
        MINUTE_CACHE_DIR.mkdir(exist_ok=True)
        payload = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "symbol": symbol,
            "bars": [{"hm": b.hm, "price": b.price, "volume": b.volume_lot, "amount": b.amount_yuan, "date": b.date} for b in bars],
        }
        (MINUTE_CACHE_DIR / f"{symbol}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except Exception:
        pass


def load_cached_minutes(symbol: str) -> List[Bar]:
    try:
        data = json.loads((MINUTE_CACHE_DIR / f"{symbol}.json").read_text(encoding="utf-8"))
        rows = data.get("bars") or []
        bars = [Bar(str(x["hm"]), float(x["price"]), float(x.get("volume") or 0.0), float(x.get("amount") or 0.0), str(x.get("date") or data.get("date") or "")) for x in rows if x.get("price")]
        return bars if len(bars) >= 30 else []
    except Exception:
        return []


def load_history_price_bars(symbol: str) -> List[Bar]:
    code = symbol[2:]
    try:
        lines = SIM_HISTORY_PATH.read_text(encoding="utf-8").splitlines()[-500:]
    except Exception:
        return []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        for stock in item.get("stocks") or []:
            if str(stock.get("code")) != code:
                continue
            prices = stock.get("prices") or []
            bars: list[Bar] = []
            for row in prices:
                try:
                    hm = _hm(str(row.get("time") or ""))
                    price = float(row.get("price") or 0.0)
                    if price <= 0:
                        continue
                    volume = 100.0
                    bars.append(Bar(hm, price, volume, price * volume * 100.0))
                except Exception:
                    continue
            return bars if len(bars) >= 30 else []
    return []


def fetch_minutes_eastmoney(symbol: str, days: int = 1) -> List[Bar]:
    code = symbol[2:]
    market = "1" if symbol.startswith("sh") else "0"
    params = {
        "secid": f"{market}.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "iscca": "0",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "ndays": str(max(1, min(10, int(days or 1)))),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get?" + urllib.parse.urlencode(params)
    try:
        data = json.loads(_get(url, "utf-8", 10))
    except Exception:
        return []
    result_data = data.get("data", {}) if isinstance(data, dict) else {}
    rows = result_data.get("trends", []) if isinstance(result_data, dict) else []
    try:
        previous_close = float(result_data.get("preClose") or 0)
        if previous_close > 0:
            PREV_CLOSE_BY_SYMBOL[symbol] = previous_close
    except (TypeError, ValueError):
        pass
    bars: List[Bar] = []
    for row in rows:
        parts = str(row).split(",")
        if len(parts) < 7:
            continue
        try:
            full_time = parts[0]
            date = full_time[:10] if len(full_time) >= 10 and full_time[4:5] == "-" else datetime.now().strftime("%Y-%m-%d")
            hm = full_time[-5:]
            close = float(parts[2])
            volume = float(parts[5])
            amount = float(parts[6])
            bars.append(Bar(hm, close, volume, amount, date))
        except Exception:
            continue
    return bars


def fetch_minutes_sina(symbol: str) -> List[Bar]:
    url = f"https://quotes.sina.cn/cn/api/openapi.php/CN_MinlineService.getMinlineData?symbol={symbol}"
    try:
        data = json.loads(_get(url, "utf-8", 10))
    except Exception:
        return []
    rows = data.get("result", {}).get("data", []) if isinstance(data, dict) else []
    bars: List[Bar] = []
    last_total_volume = 0.0
    last_total_amount = 0.0
    running_volume = 0.0
    for row in rows:
        try:
            hm = _hm(str(row.get("m", ""))[:5])
            price = float(row.get("p") or 0)
            avg_price = float(row.get("avg_p") or price)
            volume = float(row.get("v") or 0)
            total_volume = float(row.get("tot_v") or 0)
            if total_volume <= 0:
                running_volume += volume
                total_volume = running_volume
            total_amount = total_volume * avg_price
            minute_volume = max(total_volume - last_total_volume, volume, 0.0)
            minute_amount = max(total_amount - last_total_amount, minute_volume * price, 0.0)
            last_total_volume = total_volume
            last_total_amount = total_amount
            if price > 0:
                bars.append(Bar(hm, price, minute_volume / 100.0, minute_amount, datetime.now().strftime("%Y-%m-%d")))
        except Exception:
            continue
    return bars


def _cycle_dict(result: Result) -> dict:
    return {
        "action": result.action,
        "buyTime": result.buy_time,
        "buyPrice": result.buy_price,
        "sellTime": result.sell_time,
        "sellPrice": result.sell_price,
        "pnl": result.pnl_pct,
        "money": result.pnl_yuan,
        "grossPnl": result.gross_pnl_yuan,
        "fees": result.fees_yuan,
        "shares": result.shares,
        "reason": result.reason,
        "position": result.position or {},
    }


def _minute_text(total: int) -> str:
    total = max(0, min(23 * 60 + 59, int(total)))
    return f"{total // 60:02d}:{total % 60:02d}"


def simulate_one(stock: Stock, bars: List[Bar], trade_amount: float, previous_close: float | None = None) -> Result:
    """Run several independent closed T cycles while keeping full-day VWAP."""
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    limit = max(1, min(5, int(strategy.get("max_daily_cycles", 3))))
    cooldown = max(3, min(30, int(strategy.get("cycle_cooldown_minutes", 10))))
    cycles: list[Result] = []
    global ACTIVE_POSITION
    position = PositionState(SIM_BASE_SHARES)
    ACTIVE_POSITION = position
    entry_after = ""
    last_result: Result | None = None
    for _ in range(limit):
        result = _simulate_one_cycle(stock, bars, trade_amount, previous_close, entry_after, position)
        last_result = result
        if result.action == "未触发":
            break
        cycles.append(result)
        end_time = result.buy_time if result.action.startswith("反T") else result.sell_time
        end_minute = _hm_to_minutes(end_time)
        if end_minute >= _hm_to_minutes(str(strategy.get("trade_end_hm") or "14:00")):
            break
        entry_after = _minute_text(end_minute + cooldown)
    if not cycles:
        return last_result or Result(stock, "未触发", "--:--", 0.0, "--:--", 0.0, 0.0, 0.0, 0.0, 0, "无有效循环")
    payload = tuple(_cycle_dict(item) for item in cycles)
    if len(cycles) == 1:
        item = cycles[0]
        return Result(stock, item.action, item.buy_time, item.buy_price, item.sell_time, item.sell_price, item.pnl_pct, item.pnl_yuan, item.trade_amount, item.shares, item.reason, payload, item.gross_pnl_yuan, item.fees_yuan, item.position)
    total_money = sum(item.pnl_yuan for item in cycles)
    total_gross = sum(item.gross_pnl_yuan for item in cycles)
    total_fees = sum(item.fees_yuan for item in cycles)
    base_amount = max((item.trade_amount for item in cycles), default=trade_amount)
    total_pct = total_money / base_amount * 100.0 if base_amount > 0 else 0.0
    first, last = cycles[0], cycles[-1]
    directions = " / ".join(item.action for item in cycles)
    return Result(stock, f"智能做T{len(cycles)}轮", first.buy_time, first.buy_price, last.sell_time, last.sell_price, total_pct, total_money, base_amount, min(item.shares for item in cycles), f"完成{len(cycles)}轮闭环：{directions}；底仓已恢复", payload, total_gross, total_fees, position.snapshot())


def _simulate_one_cycle(stock: Stock, bars: List[Bar], trade_amount: float, previous_close: float | None = None, entry_after: str = "", position: PositionState | None = None) -> Result:
    # Do not inspect end-of-day extrema here: all entry decisions are causal.
    day_amp = 0.0
    # Full-day range is a reporting value only; never gate an intraday decision with future bars.
    if False and day_amp < 2.0:
        return Result(stock, "未触发", "--:--", 0.0, "--:--", 0.0, 0.0, 0.0, 0.0, 0, f"日内振幅{day_amp:.1f}%，空间不足2%")

    position = position or ACTIVE_POSITION or PositionState(SIM_BASE_SHARES)
    total_vol = 0.0
    total_amt = 0.0
    buy: Optional[Bar] = None
    sell_first: Optional[Bar] = None
    mode = ""
    lows: List[float] = []
    highs: List[float] = []
    avg_prices: List[float] = []
    volumes: List[float] = []

    for idx, bar in enumerate(bars):
        total_vol += bar.volume_lot
        total_amt += bar.amount_yuan
        lows.append(bar.price)
        highs.append(bar.price)
        volumes.append(bar.volume_lot)
        if total_vol <= 0:
            continue
        avg = total_amt / (total_vol * 100.0)
        avg_prices.append(avg)
        if avg <= 0 or not _sane_vwap(bar.price, avg) or not _in_trade_window(bar.hm):
            continue
        if entry_after and bar.hm <= entry_after:
            continue
        observed_low, observed_high = min(lows), max(highs)
        observed_range = (observed_high - observed_low) / observed_low * 100.0 if observed_low > 0 else 0.0
        if observed_range < float((ACTIVE_STRATEGY or DEFAULT_STRATEGY).get("min_observed_range_pct", 0.65)):
            continue
        dev = (bar.price - avg) / avg * 100.0

        if buy is None and sell_first is None:
            gate = evaluate_auction_gate(
                pre_close=previous_close or 0,
                open_price=bars[0].price,
                current_price=bar.price,
                average=avg,
                points=[{"time": item.hm, "price": item.price} for item in bars[: idx + 1]],
                time_text=bar.hm,
            )
            gate_state = str(gate.get("state") or "NEUTRAL")
            preference = str(gate.get("preferredDirection") or "")
            opening_wait = gate_state in {"PENDING_CONFIRMATION", "WAIT_DATA"} and bar.hm < "09:45"
            allow_buy = not opening_wait and not (gate_state == "CONFIRMED" and preference != "BUY_FIRST")
            allow_sell = not opening_wait and not (gate_state == "CONFIRMED" and preference != "SELL_FIRST")
            buy_setup = _is_opening_buy_setup(bars, idx, bar, avg, lows, volumes) or _is_better_buy_setup(bars, idx, bar, dev, lows, avg_prices, volumes)
            sell_setup = _is_opening_reverse_setup(bars, idx, bar, avg, highs, volumes) or _is_better_reverse_t_setup(bars, idx, bar, dev, highs, avg_prices, volumes)
            if int(ACTIVE_STRATEGY.get("quantbrain_enabled", 0)):
                factor_rsi = _causal_rsi(bars, idx)
                buy_setup = buy_setup and factor_rsi <= 62.0
                sell_setup = sell_setup and factor_rsi >= 38.0
            if allow_buy and buy_setup:
                buy = bar
                mode = "正T"
            elif allow_sell and sell_setup:
                sell_first = bar
                mode = "反T"
            continue

        if mode == "正T" and buy is not None:
            hold_minutes = _minutes_between(buy.hm, bar.hm)
            pnl = (bar.price - buy.price) / buy.price * 100.0
            sell_dev = (bar.price - avg) / avg * 100.0
            hold_limit = int(ACTIVE_STRATEGY.get("hold_exit_minutes", 5))
            min_stop = int(ACTIVE_STRATEGY.get("min_stop_minutes", 12))
            emergency_stop = float(ACTIVE_STRATEGY.get("emergency_stop_pct", -1.6))
            min_take_profit = int(ACTIVE_STRATEGY.get("min_take_profit_minutes", 8))
            fast_take_profit = float(ACTIVE_STRATEGY.get("fast_take_profit_pct", 1.35))
            vwap_take_profit = float(ACTIVE_STRATEGY.get("vwap_take_profit_pct", 0.25))
            normal_take_profit = float(ACTIVE_STRATEGY.get("normal_take_profit_pct", 0.6))
            late_take_profit = float(ACTIVE_STRATEGY.get("late_take_profit_pct", 0.45))
            exit_buffer = float(ACTIVE_STRATEGY.get("vwap_exit_buffer_pct", 0.20))
            near_or_above_vwap = bar.price >= avg * (1.0 - exit_buffer / 100.0)
            if (
                (hold_minutes >= 12 and near_or_above_vwap and pnl >= vwap_take_profit)
                or (hold_minutes >= min_take_profit and pnl >= normal_take_profit)
                or (hold_minutes >= 12 and pnl >= fast_take_profit)
                or (hold_minutes >= hold_limit and pnl >= late_take_profit)
            ):
                return _trade_result(stock, "正T止盈", buy, bar, trade_amount, "右侧低吸确认后达到目标，已拉开时间间隔")
            if pnl <= emergency_stop or (
                hold_minutes >= min_stop
                and pnl <= -0.55
                and idx >= 2
                and bar.price < min(bars[idx - 1].price, bars[idx - 2].price)
            ):
                return _trade_result(stock, "正T止损", buy, bar, trade_amount, "确认破位后止损")
            if hold_minutes >= hold_limit and sell_dev >= 0.7 and pnl > 0:
                return _trade_result(stock, "正T高抛", buy, bar, trade_amount, "回到均价上方")

        if mode == "反T" and sell_first is not None:
            hold_minutes = _minutes_between(sell_first.hm, bar.hm)
            pnl = (sell_first.price - bar.price) / sell_first.price * 100.0
            buy_dev = (bar.price - avg) / avg * 100.0
            hold_limit = int(ACTIVE_STRATEGY.get("hold_exit_minutes", 5))
            min_stop = int(ACTIVE_STRATEGY.get("min_stop_minutes", 12))
            emergency_stop = float(ACTIVE_STRATEGY.get("emergency_stop_pct", -1.6))
            min_take_profit = int(ACTIVE_STRATEGY.get("min_take_profit_minutes", 8))
            fast_take_profit = float(ACTIVE_STRATEGY.get("fast_take_profit_pct", 1.35))
            vwap_take_profit = float(ACTIVE_STRATEGY.get("vwap_take_profit_pct", 0.25))
            normal_take_profit = float(ACTIVE_STRATEGY.get("normal_take_profit_pct", 0.6))
            late_take_profit = float(ACTIVE_STRATEGY.get("late_take_profit_pct", 0.45))
            exit_buffer = float(ACTIVE_STRATEGY.get("vwap_exit_buffer_pct", 0.20))
            near_or_below_vwap = bar.price <= avg * (1.0 + exit_buffer / 100.0)
            if (
                (hold_minutes >= 12 and near_or_below_vwap and pnl >= vwap_take_profit)
                or (hold_minutes >= min_take_profit and pnl >= normal_take_profit)
                or (hold_minutes >= 12 and pnl >= fast_take_profit)
                or (hold_minutes >= hold_limit and pnl >= late_take_profit)
            ):
                return _reverse_t_result(stock, "反T买回", sell_first, bar, trade_amount, "高抛确认后回落买回，已拉开时间间隔")
            if pnl <= emergency_stop or (
                hold_minutes >= min_stop
                and pnl <= -0.55
                and idx >= 2
                and bar.price > max(bars[idx - 1].price, bars[idx - 2].price)
            ):
                return _reverse_t_result(stock, "反T止损", sell_first, bar, trade_amount, "确认继续走强后止损")
            if hold_minutes >= hold_limit and buy_dev <= -0.3 and pnl > 0:
                return _reverse_t_result(stock, "反T买回", sell_first, bar, trade_amount, "回落到均价下方")

    if buy is not None:
        end = _last_before(bars, "14:30") or bars[-1]
        return _trade_result(stock, "正T尾盘处理", buy, end, trade_amount, "未到止盈止损")
    if sell_first is not None:
        end = _last_before(bars, "14:30") or bars[-1]
        return _reverse_t_result(stock, "反T尾盘买回", sell_first, end, trade_amount, "未到买回目标")

    day_low = min(b.price for b in bars)
    day_high = max(b.price for b in bars)
    return Result(stock, "未触发", "--:--", 0.0, "--:--", 0.0, 0.0, 0.0, 0.0, 0, _no_trigger_reason(bars, day_low, day_high))


def _no_trigger_reason(bars: List[Bar], day_low: float, day_high: float) -> str:
    amp = (day_high - day_low) / day_low * 100.0 if day_low > 0 else 0.0
    if len(bars) < 12:
        return f"日内振幅{amp:.1f}%，分时样本不足"
    total_vol = 0.0
    total_amt = 0.0
    best_buy = {"dev": 0.0, "rebound": 0.0, "score": 0, "hm": "--:--"}
    best_sell = {"dev": 0.0, "fade": 0.0, "score": 0, "hm": "--:--"}
    lows: List[float] = []
    highs: List[float] = []
    avg_prices: List[float] = []
    volumes: List[float] = []
    prices = [b.price for b in bars]
    for idx, bar in enumerate(bars):
        total_vol += bar.volume_lot
        total_amt += bar.amount_yuan
        lows.append(bar.price)
        highs.append(bar.price)
        volumes.append(bar.volume_lot)
        if total_vol <= 0 or not _in_trade_window(bar.hm):
            continue
        avg = total_amt / (total_vol * 100.0)
        if avg <= 0 or not _sane_vwap(bar.price, avg):
            continue
        avg_prices.append(avg)
        dev = (bar.price - avg) / avg * 100.0
        buy_score = _buy_confirmation_score(bars, idx, lows, volumes) if idx >= 6 else 0
        sell_score = _sell_confirmation_score(bars, idx, highs, volumes) if idx >= 6 else 0
        day_low_sofar = min(lows) if lows else bar.price
        day_high_sofar = max(highs) if highs else bar.price
        rebound = (bar.price - day_low_sofar) / day_low_sofar * 100.0 if day_low_sofar > 0 else 0.0
        fade = (day_high_sofar - bar.price) / day_high_sofar * 100.0 if day_high_sofar > 0 else 0.0
        if dev < best_buy["dev"]:
            best_buy = {"dev": dev, "rebound": rebound, "score": buy_score, "hm": bar.hm}
        if dev > best_sell["dev"]:
            best_sell = {"dev": dev, "fade": fade, "score": sell_score, "hm": bar.hm}

    sm = _smart_money_reason(bars)
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    buy_ok = best_buy["dev"] <= float(strategy["buy_min_dev"])
    sell_ok = best_sell["dev"] >= float(strategy["sell_min_dev"])
    notes = [f"日内振幅{amp:.1f}%"]
    if not buy_ok and not sell_ok:
        notes.append(f"VWAP偏离不足：低位{best_buy['dev']:.2f}%，高位{best_sell['dev']:.2f}%")
    elif buy_ok and best_buy["score"] < _sim_required_confirm("buy"):
        notes.append(f"低吸未确认：{best_buy['hm']} 偏离{best_buy['dev']:.2f}%，拐头确认{best_buy['score']}/{_sim_required_confirm('buy')}")
    elif sell_ok and best_sell["score"] < _sim_required_confirm("sell"):
        notes.append(f"反T未确认：{best_sell['hm']} 偏离{best_sell['dev']:.2f}%，回落确认{best_sell['score']}/{_sim_required_confirm('sell')}")
    else:
        notes.append("有波动但买卖闭合空间或风控条件不足")
    notes.append(sm)
    return "；".join(notes)


def _sane_vwap(price: float, avg: float) -> bool:
    if price <= 0 or avg <= 0:
        return False
    ratio = price / avg
    return 0.5 <= ratio <= 1.5


def _causal_rsi(bars: List[Bar], idx: int, period: int = 14) -> float:
    prices = [item.price for item in bars[max(0, idx - period): idx + 1] if item.price > 0]
    if len(prices) < 2:
        return 50.0
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gain = sum(max(value, 0.0) for value in changes) / len(changes)
    loss = sum(max(-value, 0.0) for value in changes) / len(changes)
    if loss <= 1e-12:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def _smart_money_reason(bars: List[Bar]) -> str:
    if len(bars) < 15:
        return "主力行为：样本不足"
    recent = bars[-20:] if len(bars) >= 20 else bars
    prices = [b.price for b in recent]
    volumes = [max(b.volume_lot, 0.0) for b in recent]
    price_change = (prices[-1] - prices[0]) / prices[0] * 100.0 if prices[0] > 0 else 0.0
    vol_avg = sum(volumes) / len(volumes) if volumes else 0.0
    last_vol = volumes[-1] if volumes else 0.0
    high = max(prices)
    low = min(prices)
    close_pos = (prices[-1] - low) / (high - low) if high > low else 0.5
    if price_change > 0.8 and last_vol >= vol_avg * 0.8:
        return "主力行为：疑似程序化推升，等待回踩承接"
    if price_change < -0.6 and close_pos > 0.45:
        return "主力行为：疑似吸筹承接，但右侧信号不足"
    if close_pos < 0.35 and last_vol >= vol_avg:
        return "主力行为：疑似高位派发或回落，未满足反T确认"
    return "主力行为：中性震荡，暂不强行交易"


def _trade_result(stock: Stock, action: str, buy: Bar, sell: Bar, trade_amount: float, reason: str, position: PositionState | None = None) -> Result:
    position = position or ACTIVE_POSITION or PositionState(SIM_BASE_SHARES)
    shares = int(trade_amount / buy.price / 100) * 100
    if shares <= 0:
        shares = 100
    shares = position.executable_shares(shares)
    if shares < 100:
        return Result(stock, "未触发", "--:--", 0.0, "--:--", 0.0, 0.0, 0.0, 0.0, 0, "可卖底仓不足，禁止虚拟卖出", position=position.snapshot())
    buy_price = SIM_COST_MODEL.execution_price(buy.price, "buy")
    sell_price = SIM_COST_MODEL.execution_price(sell.price, "sell")
    actual_amount = shares * buy_price
    gross_pnl = (sell_price - buy_price) * shares
    fees = SIM_COST_MODEL.fee("buy", actual_amount, stock.code) + SIM_COST_MODEL.fee("sell", shares * sell_price, stock.code)
    pnl_yuan = gross_pnl - fees
    pnl_pct = pnl_yuan / actual_amount * 100.0 if actual_amount > 0 else 0.0
    position.settle_closed_t(shares)
    return Result(stock, action, buy.hm, buy_price, sell.hm, sell_price, pnl_pct, pnl_yuan, actual_amount, shares, reason + "（净收益已扣费用）", (), gross_pnl, fees, position.snapshot())


def _reverse_t_result(stock: Stock, action: str, sell: Bar, buyback: Bar, trade_amount: float, reason: str, position: PositionState | None = None) -> Result:
    position = position or ACTIVE_POSITION or PositionState(SIM_BASE_SHARES)
    shares = int(trade_amount / sell.price / 100) * 100
    if shares <= 0:
        shares = 100
    shares = position.executable_shares(shares)
    if shares < 100:
        return Result(stock, "未触发", "--:--", 0.0, "--:--", 0.0, 0.0, 0.0, 0.0, 0, "可卖底仓不足，禁止虚拟卖出", position=position.snapshot())
    sell_price = SIM_COST_MODEL.execution_price(sell.price, "sell")
    buy_price = SIM_COST_MODEL.execution_price(buyback.price, "buy")
    actual_amount = shares * sell_price
    gross_pnl = (sell_price - buy_price) * shares
    fees = SIM_COST_MODEL.fee("sell", actual_amount, stock.code) + SIM_COST_MODEL.fee("buy", shares * buy_price, stock.code)
    pnl_yuan = gross_pnl - fees
    pnl_pct = pnl_yuan / actual_amount * 100.0 if actual_amount > 0 else 0.0
    position.settle_closed_t(shares)
    return Result(stock, action, buyback.hm, buy_price, sell.hm, sell_price, pnl_pct, pnl_yuan, actual_amount, shares, reason + "（净收益已扣费用）", (), gross_pnl, fees, position.snapshot())


def _is_better_buy_setup(
    bars: List[Bar],
    idx: int,
    bar: Bar,
    dev: float,
    lows: List[float],
    avg_prices: List[float],
    volumes: List[float],
) -> bool:
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    if not _is_low_buy_window(bar.hm):
        return False
    if dev > float(strategy["buy_min_dev"]) or dev <= float(strategy["buy_max_dev"]):
        return False
    if not _vwap_reclaiming(bars, idx, avg_prices, float(strategy.get("vwap_reclaim_pct", 0.35))):
        return False
    if not _vwap_not_falling(avg_prices):
        return False
    vol_ratio = _volume_ratio(volumes)
    if vol_ratio > 4.8:
        return False
    day_low = min(lows) if lows else bar.price
    rebound = (bar.price - day_low) / day_low * 100.0 if day_low > 0 else 0.0
    if rebound < float(strategy["buy_rebound"]):
        return False
    recent = [b.price for b in bars[max(0, idx - 10) : idx + 1]]
    if len(recent) >= 2 and recent[0] > 0:
        drop10 = (recent[-1] - recent[0]) / recent[0] * 100.0
        if drop10 <= -2.2:
            return False
    prices = [b.price for b in bars]
    recent_resistance = max(prices[max(0, idx - 5) : idx])
    if bar.price < recent_resistance:
        return False
    if len(prices) >= 16:
        fast = sum(prices[idx - 4 : idx + 1]) / 5
        slow = sum(prices[idx - 14 : idx - 4]) / 10
        if fast <= slow * 1.001:
            return False
    min_vol_ratio = 1.15 if _is_opening_first_half(bar.hm) else 0.9
    if vol_ratio < min_vol_ratio:
        return False
    if bar.price <= max(bars[idx - 1].price, bars[idx - 2].price):
        return False
    if _roc(prices, idx, 3) <= 0 or _roc(prices, idx, 5) <= 0.05:
        return False
    if int(strategy.get("second_confirm_enabled", 1)) and not (
        _second_buy_confirm(bars, idx) or _sharp_reversal_buy(bars, idx)
    ):
        return False
    required = _sim_required_confirm("buy") + (1 if _is_opening_first_half(bar.hm) else 0)
    return _buy_confirmation_score(bars, idx, lows, volumes) >= max(3, required)


def _is_better_reverse_t_setup(
    bars: List[Bar],
    idx: int,
    bar: Bar,
    dev: float,
    highs: List[float],
    avg_prices: List[float],
    volumes: List[float],
) -> bool:
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    if not int(strategy.get("reverse_t_enabled", 0)):
        return False
    if dev < float(strategy["sell_min_dev"]) or dev >= float(strategy["sell_max_dev"]):
        return False
    if not _vwap_fading(bars, idx, avg_prices, float(strategy.get("vwap_reclaim_pct", 0.35))):
        return False
    if abs(dev) > 8:
        return False
    if not _recent_turns_down(bars, current_index=idx):
        return False
    if not _vwap_not_rising_too_fast(avg_prices):
        return False
    if not _vwap_flat_or_down(avg_prices):
        return False
    vol_ratio = _volume_ratio(volumes)
    min_sell_vol = 1.25 if _is_opening_first_half(bar.hm) else 1.0
    if vol_ratio < min_sell_vol or vol_ratio > 5.5:
        return False
    day_high = max(highs) if highs else bar.price
    fade = (day_high - bar.price) / day_high * 100.0 if day_high > 0 else 0.0
    if fade < float(strategy["sell_fade"]):
        return False
    recent = [b.price for b in bars[max(0, idx - 10) : idx + 1]]
    if len(recent) >= 2 and recent[0] > 0:
        rise10 = (recent[-1] - recent[0]) / recent[0] * 100.0
        if rise10 >= 2.2:
            return False
    if bar.price >= min(bars[idx - 1].price, bars[idx - 2].price):
        return False
    prices = [b.price for b in bars]
    recent_support = min(prices[max(0, idx - 5) : idx])
    if bar.price > recent_support:
        return False
    if _roc(prices, idx, 3) >= -0.08 or _roc(prices, idx, 5) >= -0.28:
        return False
    if int(strategy.get("second_confirm_enabled", 1)) and not (
        _second_sell_confirm(bars, idx) or _sharp_reversal_sell(bars, idx)
    ):
        return False
    required = _sim_required_confirm("sell") + (1 if _is_opening_first_half(bar.hm) else 0)
    return _sell_confirmation_score(bars, idx, highs, volumes) >= max(3, required)


def _is_opening_buy_setup(
    bars: List[Bar],
    idx: int,
    bar: Bar,
    avg: float,
    lows: List[float],
    volumes: List[float],
) -> bool:
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    if not int(strategy.get("opening_enabled", 1)) or not _is_opening_trade_window(bar.hm) or idx < 8 or avg <= 0:
        return False
    prices = [b.price for b in bars]
    open_price = prices[0]
    low = min(lows)
    drop_from_open = (low - open_price) / open_price * 100.0 if open_price > 0 else 0.0
    reclaim = (bar.price - low) / low * 100.0 if low > 0 else 0.0
    dev = (bar.price - avg) / avg * 100.0
    low_pos = lows.index(low) if low in lows else 0
    low_open_reclaim = (
        low_pos <= max(8, idx // 2)
        and reclaim >= 0.25
        and -1.6 <= dev <= 0.45
        and bar.price >= avg * 0.996
    )
    panic_reclaim = drop_from_open <= float(strategy.get("opening_drop_pct", -1.8))
    if not (panic_reclaim or low_open_reclaim):
        return False
    if reclaim < (0.25 if low_open_reclaim else float(strategy.get("opening_reclaim_pct", 0.45))):
        return False
    if dev < -2.7 or dev > (0.55 if low_open_reclaim else 0.35):
        return False
    if _volume_ratio(volumes) < (0.70 if low_open_reclaim else 0.85):
        return False
    breaks_recent = bar.price > max(bars[idx - 1].price, bars[idx - 2].price)
    momentum_ok = _roc(prices, idx, 3) > (-0.03 if low_open_reclaim else 0)
    return breaks_recent and momentum_ok


def _second_buy_confirm(bars: List[Bar], idx: int) -> bool:
    window = [b.price for b in bars[max(0, idx - 18) : idx + 1]]
    if len(window) < 10:
        return False
    low = min(window[:-2])
    low_pos = window.index(low)
    if low_pos >= len(window) - 4:
        return False
    high_after = max(window[low_pos + 1 :])
    rebound = (high_after - low) / low * 100.0 if low > 0 else 0.0
    pullback_low = min(window[-4:])
    current = window[-1]
    if rebound < 0.55:
        return False
    return pullback_low > low * 1.001 and current >= pullback_low * 1.001 and current > window[-2]


def _sharp_reversal_buy(bars: List[Bar], idx: int) -> bool:
    if idx < 5:
        return False
    prices = [b.price for b in bars]
    drop = (prices[idx] - prices[idx - 4]) / prices[idx - 4] * 100.0 if prices[idx - 4] > 0 else 0.0
    return drop <= -2.4 and prices[idx] > prices[idx - 1] > prices[idx - 2]


def _is_opening_reverse_setup(
    bars: List[Bar],
    idx: int,
    bar: Bar,
    avg: float,
    highs: List[float],
    volumes: List[float],
) -> bool:
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    if not int(strategy.get("opening_enabled", 1)) or not int(strategy.get("reverse_t_enabled", 1)):
        return False
    if not _is_opening_trade_window(bar.hm) or idx < 8 or avg <= 0:
        return False
    prices = [b.price for b in bars]
    open_price = prices[0]
    high = max(highs)
    spike = (high - open_price) / open_price * 100.0 if open_price > 0 else 0.0
    fade = (high - bar.price) / high * 100.0 if high > 0 else 0.0
    dev = (bar.price - avg) / avg * 100.0
    if spike < float(strategy.get("opening_spike_pct", 2.4)):
        return False
    if fade < float(strategy.get("opening_fade_pct", 0.65)):
        return False
    if int(strategy.get("opening_reverse_strict", 1)):
        if spike < 2.8 or fade < 0.90:
            return False
        if _minutes_between(bars[0].hm, bar.hm) < 12:
            return False
    if dev < 0.0:
        return False
    if _volume_ratio(volumes) < 0.75:
        return False
    momentum_turn = _roc(prices, idx, 3) < -0.05 or bar.price < min(bars[idx - 1].price, bars[idx - 2].price)
    no_new_high = bar.price < high * (1 - float(strategy.get("opening_fade_pct", 0.38)) / 100.0)
    return momentum_turn and no_new_high


def _second_sell_confirm(bars: List[Bar], idx: int) -> bool:
    window = [b.price for b in bars[max(0, idx - 18) : idx + 1]]
    if len(window) < 10:
        return False
    high = max(window[:-2])
    high_pos = window.index(high)
    if high_pos >= len(window) - 4:
        return False
    low_after = min(window[high_pos + 1 :])
    fade = (high - low_after) / high * 100.0 if high > 0 else 0.0
    rebound_high = max(window[-4:])
    current = window[-1]
    if fade < 0.55:
        return False
    return rebound_high < high * 0.999 and current <= rebound_high * 0.999 and current < window[-2]


def _sharp_reversal_sell(bars: List[Bar], idx: int) -> bool:
    if idx < 5:
        return False
    prices = [b.price for b in bars]
    rise = (prices[idx] - prices[idx - 4]) / prices[idx - 4] * 100.0 if prices[idx - 4] > 0 else 0.0
    return rise >= 2.4 and prices[idx] < prices[idx - 1] < prices[idx - 2]


def _is_low_buy_window(hm: str) -> bool:
    try:
        h, m = [int(x) for x in hm.split(":", 1)]
    except Exception:
        return False
    now = time(h, m)
    return time(9, 35) <= now <= time(11, 15) or time(13, 0) <= now <= time(13, 30)


def _is_opening_first_half(hm: str) -> bool:
    try:
        h, m = [int(x) for x in hm.split(":", 1)]
    except Exception:
        return False
    now = time(h, m)
    return time(9, 35) <= now < time(10, 0)


def _sim_required_confirm(side: str) -> int:
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    key = "buy_confirm" if side == "buy" else "sell_confirm"
    return max(3, int(strategy[key]) - SIM_RELAX_CONFIRM)


def _recent_turns_up(bars: List[Bar], current_index: int) -> bool:
    idx = current_index
    if idx < 3:
        return False
    prev = bars[idx - 3 : idx + 1]
    return prev[-1].price >= prev[-2].price and prev[-2].price >= prev[-3].price


def _recent_turns_down(bars: List[Bar], current_index: int) -> bool:
    idx = current_index
    if idx < 3:
        return False
    prev = bars[idx - 3 : idx + 1]
    return prev[-1].price <= prev[-2].price and prev[-2].price <= prev[-3].price


def _vwap_devs(bars: List[Bar], idx: int, avg_prices: List[float], lookback: int = 6) -> List[float]:
    if not avg_prices:
        return []
    start = max(0, len(avg_prices) - lookback)
    bar_start = max(0, idx - (len(avg_prices) - start) + 1)
    prices = [b.price for b in bars[bar_start : idx + 1]]
    avgs = avg_prices[start:]
    if len(prices) != len(avgs):
        n = min(len(prices), len(avgs))
        prices = prices[-n:]
        avgs = avgs[-n:]
    return [(price - avg) / avg * 100.0 for price, avg in zip(prices, avgs) if avg > 0]


def _vwap_reclaiming(bars: List[Bar], idx: int, avg_prices: List[float], min_reclaim: float) -> bool:
    devs = _vwap_devs(bars, idx, avg_prices)
    if len(devs) < 4:
        return False
    current = devs[-1]
    worst = min(devs[:-1])
    return worst <= -0.9 and current - worst >= min_reclaim and current >= devs[-2]


def _vwap_fading(bars: List[Bar], idx: int, avg_prices: List[float], min_fade: float) -> bool:
    devs = _vwap_devs(bars, idx, avg_prices)
    if len(devs) < 4:
        return False
    current = devs[-1]
    best = max(devs[:-1])
    return best >= 0.9 and best - current >= min_fade and current <= devs[-2]


def _buy_confirmation_score(bars: List[Bar], idx: int, lows: List[float], volumes: List[float]) -> int:
    """Right-side low-buy confirmation: avoid catching a falling knife."""
    if idx < 6:
        return 0
    score = 0
    prices = [b.price for b in bars]
    current = prices[idx]
    recent_low = min(prices[max(0, idx - 5) : idx + 1])
    previous_low = min(prices[max(0, idx - 6) : idx])

    if current > previous_low and prices[idx - 1] >= previous_low:
        score += 1  # no fresh low
    if current > max(prices[idx - 1], prices[idx - 2]):
        score += 1  # breaks the previous falling bars
    if recent_low > 0 and (current - recent_low) / recent_low * 100.0 >= 0.35:
        score += 1  # long-lower-shadow proxy: rebounds clearly from the intraday low
    if _roc(prices, idx, 3) > 0 and _roc(prices, idx, 3) > _roc(prices, idx - 1, 3):
        score += 1  # short ROC turns upward
    if _volume_dry_then_expand(volumes):
        score += 1  # volume dries up, then first active bid appears
    if _recent_turns_up(bars, idx):
        score += 1
    return score


def _sell_confirmation_score(bars: List[Bar], idx: int, highs: List[float], volumes: List[float]) -> int:
    """Right-side reverse-T confirmation: wait for high-level strength to fade."""
    if idx < 6:
        return 0
    score = 0
    prices = [b.price for b in bars]
    current = prices[idx]
    recent_high = max(prices[max(0, idx - 5) : idx + 1])
    previous_high = max(prices[max(0, idx - 6) : idx])

    if current < previous_high and prices[idx - 1] <= previous_high:
        score += 1  # no fresh high
    if current < min(prices[idx - 1], prices[idx - 2]):
        score += 1  # breaks the previous rising bars
    if recent_high > 0 and (recent_high - current) / recent_high * 100.0 >= 0.25:
        score += 1
    if _roc(prices, idx, 3) < 0 and _roc(prices, idx, 3) < _roc(prices, idx - 1, 3):
        score += 1
    if _volume_ratio(volumes) >= 1.0:
        score += 1
    if _recent_turns_down(bars, idx):
        score += 1
    return score


def _roc(prices: List[float], idx: int, period: int) -> float:
    if idx - period < 0 or prices[idx - period] <= 0:
        return 0.0
    return (prices[idx] - prices[idx - period]) / prices[idx - period] * 100.0


def _volume_dry_then_expand(volumes: List[float]) -> bool:
    if len(volumes) < 10:
        return False
    dry = volumes[-4:-1]
    base = volumes[-10:-4]
    dry_avg = sum(dry) / len(dry) if dry else 0.0
    base_avg = sum(base) / len(base) if base else 0.0
    return base_avg > 0 and dry_avg <= base_avg * 0.8 and volumes[-1] >= max(dry_avg * 1.25, base_avg * 0.65)


def _vwap_not_falling(avg_prices: List[float]) -> bool:
    if len(avg_prices) < 8:
        return False
    recent = avg_prices[-5:]
    earlier = avg_prices[-8:-3]
    if not recent or not earlier:
        return False
    return (sum(recent) / len(recent)) >= (sum(earlier) / len(earlier)) * 0.998


def _vwap_not_rising_too_fast(avg_prices: List[float]) -> bool:
    if len(avg_prices) < 8:
        return False
    recent = avg_prices[-5:]
    earlier = avg_prices[-8:-3]
    if not recent or not earlier:
        return False
    return (sum(recent) / len(recent)) <= (sum(earlier) / len(earlier)) * 1.004


def _vwap_flat_or_down(avg_prices: List[float]) -> bool:
    if len(avg_prices) < 10:
        return False
    recent = sum(avg_prices[-4:]) / 4
    earlier = sum(avg_prices[-10:-6]) / 4
    return recent <= earlier * 1.0008


def _volume_ratio(volumes: List[float]) -> float:
    if len(volumes) < 8:
        return 0.0
    base = volumes[-8:-1]
    avg = sum(base) / len(base) if base else 0.0
    return volumes[-1] / avg if avg > 0 else 0.0


def _last_before(bars: Iterable[Bar], hm: str) -> Optional[Bar]:
    last = None
    for bar in bars:
        if bar.hm <= hm:
            last = bar
    return last


def _minutes_between(start: str, end: str) -> int:
    try:
        sh, sm = [int(x) for x in start.split(":", 1)]
        eh, em = [int(x) for x in end.split(":", 1)]
    except Exception:
        return 0
    return max((eh * 60 + em) - (sh * 60 + sm), 0)


def _in_trade_window(hm: str) -> bool:
    try:
        h, m = [int(x) for x in hm.split(":", 1)]
    except Exception:
        return False
    strategy = ACTIVE_STRATEGY or DEFAULT_STRATEGY
    end_text = str(strategy.get("trade_end_hm") or "14:00")
    try:
        eh, em = [int(x) for x in end_text.split(":", 1)]
        end_time = time(eh, em)
    except Exception:
        end_time = time(14, 0)
    now = time(h, m)
    return time(9, 35) <= now <= end_time


def _is_opening_trade_window(hm: str) -> bool:
    try:
        h, m = [int(x) for x in hm.split(":", 1)]
    except Exception:
        return False
    now = time(h, m)
    return time(9, 35) <= now <= time(10, 5)


def _is_opening_half_hour(hm: str) -> bool:
    try:
        h, m = [int(x) for x in hm.split(":", 1)]
    except Exception:
        return False
    now = time(h, m)
    return time(9, 30) <= now <= time(10, 0)


def _is_common_a_share(code: str, name: str) -> bool:
    if not code or not name:
        return False
    if name.startswith(("\u9000", "ST", "*ST")) or "\u9000" in name:
        return False
    return code.startswith(("60", "68", "00", "30"))


def _hm(value: str) -> str:
    value = value.strip()
    if len(value) == 4 and value.isdigit():
        return f"{value[:2]}:{value[2:]}"
    if len(value) >= 5 and ":" in value:
        return value[:5]
    return value


def _to_float(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _arg_float(argv: list[str], name: str, default: float) -> float:
    if name not in argv:
        return default
    idx = argv.index(name)
    if idx + 1 >= len(argv):
        return default
    try:
        return float(argv[idx + 1])
    except Exception:
        return default


def _arg_value(argv: list[str], name: str, default: str) -> str:
    if name not in argv:
        return default
    idx = argv.index(name)
    if idx + 1 >= len(argv):
        return default
    return argv[idx + 1]


def _get(url: str, encoding: str, timeout: int) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/",
                "Accept": "application/json,text/plain,*/*",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, "replace")
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time_module.sleep(0.6 + attempt * 0.8)
    raise last_error or RuntimeError("request failed")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
