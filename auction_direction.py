"""Auction-to-Smart-T direction gate shared by live and simulated flows.

The module deliberately uses only information available at ``time_text``.  It
never looks at the rest of the day, so the same decision can be replayed in a
backtest without future-data leakage.
"""

from __future__ import annotations

from typing import Iterable, Mapping


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clock_minutes(value: object) -> int:
    text = str(value or "").strip().replace(":", "")
    if len(text) < 4 or not text[:4].isdigit():
        return -1
    return int(text[:2]) * 60 + int(text[2:4])


def evaluate_auction_gate(
    *,
    pre_close: object,
    open_price: object,
    current_price: object,
    average: object,
    points: Iterable[Mapping[str, object]],
    time_text: object,
    auction_price: object = 0,
) -> dict:
    """Return an auditable positive/reverse-T opening direction gate.

    ``auction_price`` is the stored 09:25 virtual match price when available.
    If it is absent, the first continuous-auction price is used as a clearly
    labelled degraded proxy.  Confirmation always uses bars seen by now only.
    """

    previous = _number(pre_close)
    opened = _number(open_price)
    current = _number(current_price)
    avg = _number(average)
    auction = _number(auction_price)
    minute = _clock_minutes(time_text)

    seen: list[tuple[int, float]] = []
    for point in points or []:
        point_minute = _clock_minutes(point.get("time"))
        price = _number(point.get("price"))
        if price <= 0 or point_minute < 9 * 60 + 30:
            continue
        if minute >= 0 and point_minute > minute:
            continue
        seen.append((point_minute, price))
    seen.sort(key=lambda item: item[0])
    prices = [item[1] for item in seen]
    if not opened and prices:
        opened = prices[0]
    basis = auction if auction > 0 else opened
    source = "09:25集合竞价快照" if auction > 0 else "开盘价代理（缺少竞价快照）"
    gap_pct = (basis - previous) / previous * 100.0 if previous > 0 and basis > 0 else 0.0

    preferred = ""
    plan_label = "平开观察"
    if gap_pct >= 0.30:
        preferred, plan_label = "SELL_FIRST", "高开转弱候选"
    # Cross-sectional replay showed that a shallow low gap can still be useful
    # once the five-minute reclaim and right-side execution checks confirm it.
    # The same relaxation on high-gap reverse-T created sell-fly-away losses,
    # so keep the directions asymmetric and let 09:35 structure decide.
    elif gap_pct <= -0.05:
        preferred, plan_label = "BUY_FIRST", "低开转强候选"

    payload = {
        "available": bool(previous > 0 and basis > 0),
        "source": source,
        "gapPct": round(gap_pct, 3),
        "preferredDirection": preferred,
        "state": "NEUTRAL" if not preferred else "PENDING_CONFIRMATION",
        "label": plan_label,
        "confirmed": False,
        "invalidated": False,
        "confirmationCount": 0,
        "conditions": [],
        "reason": "平开或缺少明确缺口，交给盘中趋势判断。" if not preferred else "仅制定预案，09:35后至少两项条件成立才执行。",
    }
    if not payload["available"]:
        payload.update(state="WAIT_DATA", reason="昨收或开盘/竞价价格缺失，禁止用猜测方向开仓。")
        return payload
    if not preferred:
        return payload
    if minute < 9 * 60 + 35:
        return payload
    if not prices or current <= 0:
        payload.update(state="WAIT_DATA", reason="09:35确认数据不足，继续等待。")
        return payload

    first_window = prices[: min(5, len(prices))]
    recent = prices[-3:]
    early_high = max(first_window)
    early_low = min(first_window)
    if preferred == "SELL_FIRST":
        checks = [
            (current < opened * 0.999, "跌破开盘价"),
            (avg > 0 and current < avg * 0.999, "跌破VWAP"),
            (current < early_high * 0.998, "反弹未过开盘第一波高点"),
            (len(recent) >= 3 and recent[-1] < recent[-2] < recent[-3], "连续高点/价格降低"),
        ]
        invalid = current > early_high * 1.002 and (avg <= 0 or current > avg * 1.001)
        confirmed_label = "高开转弱·反T优先"
        invalid_reason = "重新突破开盘第一波高点并站上VWAP，反T预案失效，避免卖飞。"
    else:
        checks = [
            (current > opened * 1.001, "重新站上开盘价"),
            (avg > 0 and current > avg * 1.001, "重新站上VWAP"),
            (current > early_high * 1.001, "突破第一波高点"),
            (len(recent) >= 3 and recent[-1] > recent[-2] > recent[-3], "连续低点/价格抬高"),
        ]
        invalid = current < early_low * 0.998 and (avg <= 0 or current < avg * 0.999)
        confirmed_label = "低开转强·正T优先"
        invalid_reason = "继续跌破开盘第一波低点并位于VWAP下方，正T预案失效，禁止越跌越买。"

    matched = [label for ok, label in checks if ok]
    payload["conditions"] = matched
    payload["confirmationCount"] = len(matched)
    # The execution layer independently requires a failed VWAP/open retest,
    # a renewed downturn and the observed high to hold.  Requiring three
    # auction checks here counted the same reversal evidence twice and made
    # otherwise valid high-gap reverse-T setups much rarer than low-gap buys.
    required_confirmations = 2
    if invalid:
        payload.update(state="INVALIDATED", invalidated=True, label="竞价方向已失效", reason=invalid_reason)
    elif len(matched) >= required_confirmations:
        payload.update(
            state="CONFIRMED",
            confirmed=True,
            label=confirmed_label,
            reason=f"已确认{len(matched)}项：{'、'.join(matched)}。",
        )
    else:
        payload["reason"] = f"09:35后仅确认{len(matched)}项，未达到{required_confirmations}项门槛。"
    return payload
