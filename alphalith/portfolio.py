"""
持仓追踪 — SQLite 持久化，自动计算实时盈亏。

表结构：
  positions: id, symbol, name, market, entry_price, quantity, entry_date, notes, created_at

功能：
  add/sh/remove/list 持仓
  get_summary() → 总市值、总盈亏、个股明细（含实时价格）
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
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT DEFAULT '',
            market TEXT DEFAULT '',
            entry_price REAL NOT NULL,
            quantity REAL NOT NULL,
            entry_date TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
    """)
    return conn


def add_position(symbol: str, entry_price: float, quantity: float,
                 name: str = "", market: str = "",
                 entry_date: str = "", notes: str = "") -> dict:
    """添加或更新持仓。已存在标的则更新成本和数量。返回 {"id": int, "symbol": str, "updated": bool}。"""
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, entry_price, quantity FROM positions WHERE symbol=?",
            (symbol.upper(),)
        ).fetchone()
        if existing:
            # 更新：按加权平均计算新成本
            old_cost = existing[1] * existing[2]
            new_cost = entry_price * quantity
            total_qty = existing[2] + quantity
            avg_price = (old_cost + new_cost) / total_qty if total_qty > 0 else entry_price
            conn.execute(
                "UPDATE positions SET entry_price=?, quantity=?, notes=?, created_at=? WHERE id=?",
                (avg_price, total_qty, notes, time.time(), existing[0]),
            )
            return {"id": existing[0], "symbol": symbol.upper(), "updated": True}
        else:
            cur = conn.execute(
                "INSERT INTO positions (symbol, name, market, entry_price, quantity, "
                "entry_date, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol.upper(), name, market, entry_price, quantity,
                 entry_date, notes, time.time()),
            )
            return {"id": cur.lastrowid, "symbol": symbol.upper(), "updated": False}


def update_position(pos_id: int, entry_price: float = None,
                    quantity: float = None, notes: str = None) -> bool:
    """更新持仓信息。"""
    fields = []
    values = []
    if entry_price is not None:
        fields.append("entry_price=?")
        values.append(entry_price)
    if quantity is not None:
        fields.append("quantity=?")
        values.append(quantity)
    if notes is not None:
        fields.append("notes=?")
        values.append(notes)
    if not fields:
        return False
    values.append(pos_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE positions SET {', '.join(fields)} WHERE id=?",
            values,
        )
        return cur.rowcount > 0


def remove_position(pos_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM positions WHERE id=?", (pos_id,))
        return cur.rowcount > 0


def list_positions() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, symbol, name, market, entry_price, quantity, "
            "entry_date, notes, created_at FROM positions ORDER BY created_at"
        ).fetchall()
    return [
        {"id": r[0], "symbol": r[1], "name": r[2], "market": r[3],
         "entry_price": r[4], "quantity": r[5], "entry_date": r[6],
         "notes": r[7], "created_at": r[8]}
        for r in rows
    ]


def get_summary() -> dict:
    """获取持仓汇总，含实时价格和盈亏。"""
    positions = list_positions()
    if not positions:
        return {"positions": [], "total_cost": 0, "total_value": 0,
                "total_pnl": 0, "total_pnl_pct": 0, "count": 0}

    # 尝试获取实时行情
    enriched = []
    for pos in positions:
        pos["current_price"] = 0
        pos["change_pct"] = 0
        pos["pnl"] = 0
        pos["pnl_pct"] = 0
        pos["market_value"] = 0
        pos["source"] = "n/a"
        try:
            from .data import load_market_data
            md = load_market_data(pos["symbol"])
            pos["current_price"] = md.quote.price
            pos["change_pct"] = md.quote.change_pct
            pos["name"] = md.quote.name or pos["name"]
            pos["source"] = md.quote.source
            pos["market_value"] = pos["current_price"] * pos["quantity"]
            pos["pnl"] = pos["market_value"] - pos["entry_price"] * pos["quantity"]
            if pos["entry_price"] > 0:
                pos["pnl_pct"] = (pos["current_price"] / pos["entry_price"] - 1) * 100
        except Exception:
            pos["current_price"] = pos["entry_price"]
            pos["market_value"] = pos["entry_price"] * pos["quantity"]

        # Round for display
        for k in ("current_price", "pnl", "pnl_pct", "market_value"):
            if isinstance(pos.get(k), float):
                pos[k] = round(pos[k], 2)
        enriched.append(pos)

    total_cost = sum(p["entry_price"] * p["quantity"] for p in enriched)
    total_value = sum(p["market_value"] for p in enriched)
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    return {
        "positions": enriched,
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "count": len(enriched),
    }
