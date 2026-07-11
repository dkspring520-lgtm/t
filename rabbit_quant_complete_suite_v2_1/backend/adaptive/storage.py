from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from .models import AdaptiveParams, SignalObservation, TradeObservation


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LearningStore:
    def __init__(self, database_path: str):
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS parameter_versions (
                    version_id TEXT PRIMARY KEY,
                    parent_version TEXT,
                    status TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    promoted_at TEXT,
                    shadow_started_at TEXT,
                    note TEXT DEFAULT '',
                    FOREIGN KEY(parent_version) REFERENCES parameter_versions(version_id)
                );

                CREATE TABLE IF NOT EXISTS signals (
                    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    signal_time TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    score REAL NOT NULL,
                    price REAL NOT NULL,
                    volume_ratio REAL NOT NULL,
                    top_score REAL NOT NULL,
                    bottom_score REAL NOT NULL,
                    executed INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    parameter_version TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(symbol, signal_time, direction, parameter_version)
                );

                CREATE TABLE IF NOT EXISTS signal_outcomes (
                    signal_id INTEGER PRIMARY KEY,
                    outcome_json TEXT NOT NULL,
                    labeled_at TEXT NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES signals(signal_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    net_return REAL NOT NULL,
                    result TEXT NOT NULL,
                    exit_reason TEXT NOT NULL,
                    parameter_version TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    holding_bars INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(symbol, entry_time, exit_time, direction)
                );

                CREATE TABLE IF NOT EXISTS learning_runs (
                    run_id TEXT PRIMARY KEY,
                    run_type TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    champion_version TEXT NOT NULL,
                    challenger_version TEXT,
                    result_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(trading_date);
                CREATE INDEX IF NOT EXISTS idx_signals_version ON signals(parameter_version);
                CREATE INDEX IF NOT EXISTS idx_trades_version ON trades(parameter_version);
                CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trading_date);
                """
            )

    def ensure_initial_champion(self, params: AdaptiveParams) -> str:
        current = self.get_champion()
        if current:
            return current["version_id"]
        return self.create_version(params, status="champion", note="初始稳定参数")

    def create_version(
        self,
        params: AdaptiveParams,
        status: str,
        parent_version: Optional[str] = None,
        note: str = "",
    ) -> str:
        version_id = f"v{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO parameter_versions(
                    version_id,parent_version,status,params_json,created_at,
                    promoted_at,shadow_started_at,note
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    version_id,
                    parent_version,
                    status,
                    json.dumps(params.to_dict(), ensure_ascii=False, sort_keys=True),
                    now,
                    now if status == "champion" else None,
                    now if status == "challenger" else None,
                    note,
                ),
            )
        return version_id

    def get_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM parameter_versions WHERE version_id=?", (version_id,)
            ).fetchone()
        return None if row is None else self._version_row(row)

    def get_champion(self) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM parameter_versions
                WHERE status='champion'
                ORDER BY COALESCE(promoted_at, created_at) DESC LIMIT 1
                """
            ).fetchone()
        return None if row is None else self._version_row(row)

    def get_challenger(self) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM parameter_versions
                WHERE status='challenger'
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
        return None if row is None else self._version_row(row)

    def list_versions(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM parameter_versions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._version_row(row) for row in rows]

    @staticmethod
    def _version_row(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["params"] = AdaptiveParams.from_mapping(json.loads(data.pop("params_json")))
        return data

    def promote(self, challenger_version: str) -> None:
        now = _utc_now()
        with self.connect() as conn:
            challenger = conn.execute(
                "SELECT version_id FROM parameter_versions WHERE version_id=? AND status='challenger'",
                (challenger_version,),
            ).fetchone()
            if challenger is None:
                raise ValueError("挑战版不存在或状态不正确")
            conn.execute(
                "UPDATE parameter_versions SET status='archived' WHERE status='champion'"
            )
            cursor = conn.execute(
                """
                UPDATE parameter_versions
                SET status='champion', promoted_at=?
                WHERE version_id=? AND status='challenger'
                """,
                (now, challenger_version),
            )
            if cursor.rowcount != 1:
                raise ValueError("挑战版不存在或状态不正确")

    def rollback_to(self, version_id: str, reason: str) -> None:
        now = _utc_now()
        with self.connect() as conn:
            target = conn.execute(
                """
                SELECT version_id FROM parameter_versions
                WHERE version_id=? AND status IN ('archived','rolled_back')
                """,
                (version_id,),
            ).fetchone()
            if target is None:
                raise ValueError("回滚版本不存在或状态不允许")
            current = conn.execute(
                "SELECT version_id FROM parameter_versions WHERE status='champion'"
            ).fetchone()
            if current:
                conn.execute(
                    "UPDATE parameter_versions SET status='rolled_back', note=? WHERE version_id=?",
                    (reason, current["version_id"]),
                )
            cursor = conn.execute(
                "UPDATE parameter_versions SET status='champion', promoted_at=?, note=? WHERE version_id=?",
                (now, f"回滚启用：{reason}", version_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("回滚版本不存在或状态不允许")

    def insert_signal(self, observation: SignalObservation) -> Optional[int]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO signals(
                    symbol,signal_time,trading_date,direction,regime,score,price,
                    volume_ratio,top_score,bottom_score,executed,decision,
                    parameter_version,features_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    observation.symbol,
                    observation.signal_time,
                    observation.trading_date,
                    observation.direction,
                    observation.regime,
                    observation.score,
                    observation.price,
                    observation.volume_ratio,
                    observation.top_score,
                    observation.bottom_score,
                    1 if observation.executed else 0,
                    observation.decision,
                    observation.parameter_version,
                    json.dumps(observation.features, ensure_ascii=False, default=str),
                    _utc_now(),
                ),
            )
            if cursor.rowcount == 0:
                return None
            return int(cursor.lastrowid)

    def insert_trade(self, observation: TradeObservation) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO trades(
                    symbol,trading_date,direction,entry_time,exit_time,entry_price,
                    exit_price,net_return,result,exit_reason,parameter_version,
                    regime,holding_bars,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    observation.symbol,
                    observation.trading_date,
                    observation.direction,
                    observation.entry_time,
                    observation.exit_time,
                    observation.entry_price,
                    observation.exit_price,
                    observation.net_return,
                    observation.result,
                    observation.exit_reason,
                    observation.parameter_version,
                    observation.regime,
                    observation.holding_bars,
                    _utc_now(),
                ),
            )

    def pending_signals(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        query = """
            SELECT s.* FROM signals s
            LEFT JOIN signal_outcomes o ON o.signal_id=s.signal_id
            WHERE o.signal_id IS NULL
        """
        params: tuple[Any, ...] = ()
        if symbol:
            query += " AND s.symbol=?"
            params = (symbol,)
        query += " ORDER BY s.signal_time"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def save_outcome(self, signal_id: int, outcome: Mapping[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signal_outcomes(signal_id,outcome_json,labeled_at)
                VALUES(?,?,?)
                """,
                (signal_id, json.dumps(dict(outcome), ensure_ascii=False), _utc_now()),
            )

    def labeled_signals(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT s.*, o.outcome_json
            FROM signals s JOIN signal_outcomes o ON o.signal_id=s.signal_id
            WHERE 1=1
        """
        params: list[Any] = []
        if start_date:
            query += " AND s.trading_date>=?"
            params.append(start_date)
        if end_date:
            query += " AND s.trading_date<=?"
            params.append(end_date)
        query += " ORDER BY s.signal_time"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["outcome"] = json.loads(item.pop("outcome_json"))
            item["features"] = json.loads(item.pop("features_json"))
            result.append(item)
        return result

    def recent_trades(self, version_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trades WHERE parameter_version=?
                ORDER BY exit_time DESC LIMIT ?
                """,
                (version_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_learning_run(
        self,
        run_type: str,
        champion_version: str,
        challenger_version: Optional[str],
        result: Mapping[str, Any],
    ) -> str:
        run_id = uuid.uuid4().hex
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO learning_runs(
                    run_id,run_type,started_at,completed_at,champion_version,
                    challenger_version,result_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    run_type,
                    now,
                    now,
                    champion_version,
                    challenger_version,
                    json.dumps(dict(result), ensure_ascii=False, default=str),
                ),
            )
        return run_id

    def counts(self) -> Dict[str, int]:
        with self.connect() as conn:
            signals = conn.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]
            labeled = conn.execute("SELECT COUNT(*) n FROM signal_outcomes").fetchone()["n"]
            trades = conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"]
        return {"signals": int(signals), "labeledSignals": int(labeled), "trades": int(trades)}
