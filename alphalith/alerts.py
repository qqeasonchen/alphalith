"""
预警通知 — 价格/涨跌幅条件预警，SQLite 持久化 + 浏览器通知。

条件类型：
  price_above:    实时价 > 阈值 → 触发
  price_below:    实时价 < 阈值 → 触发
  change_above:   涨跌幅 > 阈值 → 触发
  change_below:   涨跌幅 < 阈值 → 触发

表结构：
  alerts: id, symbol, name, condition_type, threshold, enabled, created_at, last_triggered
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional


def _db_path() -> Path:
    p = os.getenv("ALPHALITH_DB_PATH")
    if p:
        return Path(p).expanduser().parent / "store.db"
    return Path.home() / ".alphalith" / "store.db"


def _connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            condition_type TEXT NOT NULL,
            threshold REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            last_triggered REAL DEFAULT 0
        )
    """)
    return conn


CONDITION_TYPES = {
    "price_above": "价格突破上限",
    "price_below": "价格跌破下限",
    "change_above": "涨跌幅超过",
    "change_below": "涨跌幅低于",
}


def create_alert(symbol: str, condition_type: str, threshold: float,
                 name: str = "") -> dict:
    """创建新预警。返回 {"id": int, ...}。"""
    if condition_type not in CONDITION_TYPES:
        raise ValueError(f"无效条件类型: {condition_type}，可选: {list(CONDITION_TYPES.keys())}")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO alerts (symbol, name, condition_type, threshold, enabled, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (symbol.upper(), name, condition_type, threshold, time.time()),
        )
        return {
            "id": cur.lastrowid,
            "symbol": symbol.upper(),
            "condition_type": condition_type,
            "threshold": threshold,
        }


def delete_alert(alert_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
        return cur.rowcount > 0


def toggle_alert(alert_id: int, enabled: bool = None) -> bool:
    """切换预警开关。enabled=None 时翻转。"""
    with _connect() as conn:
        if enabled is None:
            cur = conn.execute(
                "UPDATE alerts SET enabled = 1 - enabled WHERE id=?", (alert_id,)
            )
        else:
            cur = conn.execute(
                "UPDATE alerts SET enabled=? WHERE id=?", (1 if enabled else 0, alert_id)
            )
        return cur.rowcount > 0


def list_alerts(symbol: str = None) -> list[dict]:
    """列出所有预警。"""
    with _connect() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT id, symbol, name, condition_type, threshold, enabled, "
                "created_at, last_triggered FROM alerts WHERE symbol=? ORDER BY created_at",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, symbol, name, condition_type, threshold, enabled, "
                "created_at, last_triggered FROM alerts ORDER BY created_at"
            ).fetchall()
    return [
        {
            "id": r[0], "symbol": r[1], "name": r[2],
            "condition_type": r[3], "threshold": r[4],
            "enabled": bool(r[5]), "created_at": r[6],
            "last_triggered": r[7],
        }
        for r in rows
    ]


def check_alerts() -> list[dict]:
    """检查所有启用的预警，返回触发的列表。"""
    alerts = list_alerts()
    if not alerts:
        return []

    triggered = []
    # 收集所有标的
    symbols = list(set(a["symbol"] for a in alerts if a["enabled"]))
    quotes = {}
    for sym in symbols:
        try:
            from .data import load_market_data
            md = load_market_data(sym)
            quotes[sym] = {
                "price": md.quote.price,
                "change_pct": md.quote.change_pct,
                "name": md.quote.name,
            }
        except Exception:
            pass

    now = time.time()
    for alert in alerts:
        if not alert["enabled"]:
            continue
        sym = alert["symbol"]
        if sym not in quotes:
            continue
        q = quotes[sym]
        triggered_now = False

        ct = alert["condition_type"]
        if ct == "price_above" and q["price"] > alert["threshold"]:
            triggered_now = True
        elif ct == "price_below" and q["price"] < alert["threshold"]:
            triggered_now = True
        elif ct == "change_above" and q["change_pct"] > alert["threshold"]:
            triggered_now = True
        elif ct == "change_below" and q["change_pct"] < alert["threshold"]:
            triggered_now = True

        if triggered_now:
            alert["current_price"] = q["price"]
            alert["current_change_pct"] = q["change_pct"]
            alert["name"] = q.get("name", alert["name"])
            triggered.append(alert)
            # Update last_triggered
            with _connect() as conn:
                conn.execute(
                    "UPDATE alerts SET last_triggered=? WHERE id=?",
                    (now, alert["id"]),
                )

    return triggered
