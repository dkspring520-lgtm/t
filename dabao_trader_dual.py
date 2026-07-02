#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Two-stock intraday T signal entrypoint for Hermes cron."""

from stock_t_signal import StockConfig, print_signals


if __name__ == "__main__":
    raise SystemExit(
        print_signals(
            [
                StockConfig("\u7d2b\u91d1\u77ff\u4e1a", "601899", "sh601899"),
                StockConfig("\u9686\u57fa\u7eff\u80fd", "601012", "sh601012"),
            ]
        )
    )
