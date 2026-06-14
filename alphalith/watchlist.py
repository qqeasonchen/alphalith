"""
自选股 / 观察列表 — SQLite 持久化，支持多列表管理。

表结构：
  lists:    id, name, created_at
  items:    id, list_id, symbol, name, market, added_at, notes

API:
  create_list(name) → list_id
  delete_list(list_id)
  list_lists() → [dict]
  add_item(list_id, symbol, name?, market?, notes?) → item_id
  remove_item(item_id)
  list_items(list_id) → [dict]
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
        CREATE TABLE IF NOT EXISTS watchlist_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            market TEXT DEFAULT '',
            added_at REAL NOT NULL,
            notes TEXT DEFAULT '',
            FOREIGN KEY (list_id) REFERENCES watchlist_lists(id) ON DELETE CASCADE
        )
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    # Ensure UNIQUE constraint per list
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_wl_items_list_sym "
            "ON watchlist_items(list_id, symbol)"
        )
    except sqlite3.OperationalError:
        pass
    return conn


# ── Lists ──

def create_list(name: str) -> dict:
    """创建新观察列表。同名则返回已有列表。返回 {"id": int, "name": str}。"""
    name = name.strip()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM watchlist_lists WHERE name=?", (name,)
        ).fetchone()
        if existing:
            return {"id": existing[0], "name": name}
        cur = conn.execute(
            "INSERT INTO watchlist_lists (name, created_at) VALUES (?, ?)",
            (name, time.time()),
        )
        return {"id": cur.lastrowid, "name": name}


def delete_list(list_id: int) -> bool:
    with _connect() as conn:
        conn.execute("DELETE FROM watchlist_items WHERE list_id=?", (list_id,))
        cur = conn.execute("DELETE FROM watchlist_lists WHERE id=?", (list_id,))
        return cur.rowcount > 0


def rename_list(list_id: int, new_name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE watchlist_lists SET name=? WHERE id=?", (new_name.strip(), list_id)
        )
        return cur.rowcount > 0


def list_lists() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM watchlist_lists ORDER BY created_at"
        ).fetchall()
    return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]


# ── Items ──

def add_item(list_id: int, symbol: str, name: str = "",
             market: str = "", notes: str = "") -> dict:
    """添加标的到列表。返回 {"id": int, "symbol": str}。"""
    with _connect() as conn:
        # Upsert semantics
        cur = conn.execute(
            "INSERT OR REPLACE INTO watchlist_items "
            "(list_id, symbol, name, market, added_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (list_id, symbol.upper(), name, market, time.time(), notes),
        )
        return {"id": cur.lastrowid, "symbol": symbol.upper()}


def remove_item(item_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM watchlist_items WHERE id=?", (item_id,))
        return cur.rowcount > 0


def remove_item_by_symbol(list_id: int, symbol: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist_items WHERE list_id=? AND symbol=?",
            (list_id, symbol.upper()),
        )
        return cur.rowcount > 0


def list_items(list_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, symbol, name, market, added_at, notes "
            "FROM watchlist_items WHERE list_id=? ORDER BY added_at",
            (list_id,),
        ).fetchall()
    return [
        {"id": r[0], "symbol": r[1], "name": r[2], "market": r[3],
         "added_at": r[4], "notes": r[5]}
        for r in rows
    ]


def get_items_with_quotes(list_id: int) -> list[dict]:
    """获取列表项并附带实时行情（如有数据源可用）。"""
    items = list_items(list_id)
    try:
        from .data import load_market_data
    except Exception:
        return items

    result = []
    for it in items:
        try:
            md = load_market_data(it["symbol"])
            it["price"] = md.quote.price
            it["change_pct"] = md.quote.change_pct
            it["prev_close"] = md.quote.prev_close
            it["name"] = md.quote.name or it["name"]
            it["source"] = md.quote.source
        except Exception:
            it["price"] = 0
            it["change_pct"] = 0
            it["prev_close"] = 0
            it["source"] = "n/a"
        result.append(it)
    return result
