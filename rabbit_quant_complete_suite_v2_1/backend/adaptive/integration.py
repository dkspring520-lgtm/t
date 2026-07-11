from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

try:
    from ..smart_t_controller import SmartTOptions, run_smart_t
except ImportError:  # 兼容直接把backend加入sys.path的旧项目
    from smart_t_controller import SmartTOptions, run_smart_t

from .models import AdaptiveParams
from .service import AdaptiveLearningService


def params_to_custom(params: AdaptiveParams) -> Dict[str, Any]:
    return params.to_dict()


def run_adaptive_smart_t(
    symbol: str,
    minute_df: pd.DataFrame,
    learning: AdaptiveLearningService,
    options: Optional[SmartTOptions] = None,
    record: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """一行接入：正式版参数驱动原智能做T，并将结果写入学习库。"""
    opts = options or SmartTOptions(sensitivity="custom")
    if opts.sensitivity != "custom":
        opts = replace(opts, sensitivity="custom")
    params = learning.current_params()
    signals, trades, payload = run_smart_t(
        minute_df,
        options=opts,
        custom=params_to_custom(params),
    )
    payload["learning"] = learning.growth_payload()
    if record:
        learning.record_intraday(symbol, signals, trades)
    return signals, trades, payload
