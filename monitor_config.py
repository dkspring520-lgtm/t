#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared watchlist settings for realtime and seconds-level monitors."""

from __future__ import annotations

import json
import re
from pathlib import Path

from stock_t_signal import StockConfig

BASE_DIR = Path(__file__).resolve().parent
WATCHLIST_PATH = BASE_DIR / "monitor_watchlist.json"
A_SHARE_POOL_PATH = BASE_DIR / "a_share_pool_cache.json"

COMMON_NAMES = {
    "601899": "紫金矿业",
    "601012": "隆基绿能",
    "603993": "洛阳钼业",
    "600519": "贵州茅台",
    "000858": "五粮液",
    "000063": "中兴通讯",
    "601318": "中国平安",
    "600036": "招商银行",
    "601088": "中国神华",
    "600030": "中信证券",
    "002050": "三花智控",
    "002648": "卫星化学",
    "600580": "卧龙电驱",
    "688356": "键凯科技",
}

DEFAULT_WATCHLIST = [
    StockConfig("紫金矿业", "601899", "sh601899"),
    StockConfig("隆基绿能", "601012", "sh601012"),
]


def load_watchlist() -> list[StockConfig]:
    try:
        data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return list(DEFAULT_WATCHLIST)
    items = data.get("stocks") if isinstance(data, dict) else data
    stocks = _items_to_stocks(items)
    return stocks or list(DEFAULT_WATCHLIST)


def save_watchlist_text(text: str) -> dict:
    stocks = parse_watchlist_text(text)
    if not stocks:
        stocks = list(DEFAULT_WATCHLIST)
    payload = {"stocks": [stock_to_dict(s) for s in stocks]}
    WATCHLIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "stocks": payload["stocks"], "text": watchlist_text(stocks)}


def parse_watchlist_text(text: str) -> list[StockConfig]:
    tokens = re.split(r"[\s,，、;；]+", str(text or "").strip())
    out: list[StockConfig] = []
    seen: set[str] = set()
    for token in tokens:
        stock = parse_stock_token(token)
        if stock and stock.code not in seen:
            out.append(stock)
            seen.add(stock.code)
        if len(out) >= 12:
            break
    return out


def parse_stock_token(token: str) -> StockConfig | None:
    raw = str(token or "").strip()
    if not raw:
        return None
    m = re.search(r"(sh|sz)?(\d{6})", raw, re.I)
    if not m:
        return None
    code = m.group(2)
    prefix = (m.group(1) or infer_prefix(code)).lower()
    symbol = f"{prefix}{code}"
    name = stock_name_by_code(code) or raw.replace(m.group(0), "").strip(" -_()（）") or code
    return StockConfig(name, code, symbol)


def stock_to_dict(stock: StockConfig) -> dict:
    return {"name": stock.name, "code": stock.code, "symbol": stock.symbol}


def watchlist_text(stocks: list[StockConfig] | None = None) -> str:
    return ",".join(s.symbol for s in (stocks or load_watchlist()))


def infer_prefix(code: str) -> str:
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def stock_name_by_code(code: str) -> str:
    if code in COMMON_NAMES:
        return COMMON_NAMES[code]
    try:
        data = json.loads(A_SHARE_POOL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for item in data.get("stocks", []):
        if str(item.get("code") or "") == code:
            return str(item.get("name") or "").strip()
    return ""


def _items_to_stocks(items: object) -> list[StockConfig]:
    stocks: list[StockConfig] = []
    seen: set[str] = set()
    if isinstance(items, str):
        return parse_watchlist_text(items)
    if not isinstance(items, list):
        return []
    for item in items:
        stock = None
        if isinstance(item, str):
            stock = parse_stock_token(item)
        elif isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            symbol = str(item.get("symbol") or "").strip()
            stock = parse_stock_token(symbol or code)
            if stock:
                name = str(item.get("name") or stock.name).strip() or stock.name
                if not name or name == stock.code:
                    name = stock_name_by_code(stock.code) or stock.name
                stock = StockConfig(name, stock.code, stock.symbol)
        if stock and stock.code not in seen:
            stocks.append(stock)
            seen.add(stock.code)
        if len(stocks) >= 12:
            break
    return stocks
