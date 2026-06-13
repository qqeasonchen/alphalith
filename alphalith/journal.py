"""
SQLite-backed decision journal — 每次 analyze() 自动落库，便于回溯与复盘。

零依赖：仅用标准库 sqlite3 + json。
默认库位置：~/.alphalith/journal.db，可通过 ALPHALITH_DB_PATH 覆盖。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .schema import Decision


def _db_path() -> Path:
    p = os.getenv("ALPHALITH_DB_PATH")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".alphalith" / "journal.db"


def _connect() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            action TEXT NOT NULL,
            confidence REAL NOT NULL,
            entry_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            llm TEXT,
            data_source TEXT,
            llm_total_tokens INTEGER,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_symbol_ts ON decisions(symbol, ts DESC)"
    )
    return conn


def save(decision: Decision) -> None:
    """把 Decision 落库；失败不抛，仅静默（journal 不应阻塞主流程）。"""
    try:
        with _connect() as conn:
            payload = json.dumps(decision.to_adp_json(), ensure_ascii=False)
            conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                (id, ts, symbol, market, action, confidence, entry_price, shares,
                 llm, data_source, llm_total_tokens, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.id,
                    decision.timestamp.isoformat(),
                    decision.symbol,
                    decision.market.value,
                    decision.action,
                    decision.confidence,
                    decision.entry_price,
                    decision.suggested_shares,
                    decision.extra.get("llm"),
                    decision.extra.get("data_source"),
                    int(decision.extra.get("llm_total_tokens") or 0),
                    payload,
                ),
            )
    except Exception:
        pass


def history(symbol: Optional[str] = None, limit: int = 20) -> list[dict]:
    """读取最近 N 条决策摘要。"""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            if symbol:
                cur.execute(
                    "SELECT id, ts, symbol, market, action, confidence, entry_price, "
                    "shares, llm, data_source, llm_total_tokens "
                    "FROM decisions WHERE symbol=? ORDER BY ts DESC LIMIT ?",
                    (symbol, limit),
                )
            else:
                cur.execute(
                    "SELECT id, ts, symbol, market, action, confidence, entry_price, "
                    "shares, llm, data_source, llm_total_tokens "
                    "FROM decisions ORDER BY ts DESC LIMIT ?",
                    (limit,),
                )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def db_path() -> str:
    return str(_db_path())


def review(symbol: Optional[str] = None) -> dict:
    """对 journal 做聚合统计——决策分布、平均置信度、token 消耗、数据源占比。

    返回字典含：
      - total: 总决策数
      - by_action: {buy/hold/sell: count}
      - avg_confidence: 平均置信度
      - by_llm: {llm_name: count}
      - by_source: {sina/akshare/fallback: count}
      - tokens_total: token 总消耗
      - latest: 最近 5 条简表
    """
    try:
        with _connect() as conn:
            cur = conn.cursor()
            where = "WHERE symbol=?" if symbol else ""
            args = (symbol,) if symbol else ()

            cur.execute(f"SELECT COUNT(*) FROM decisions {where}", args)
            total = cur.fetchone()[0]

            cur.execute(
                f"SELECT action, COUNT(*) FROM decisions {where} GROUP BY action", args
            )
            by_action = dict(cur.fetchall())

            cur.execute(
                f"SELECT AVG(confidence), COALESCE(SUM(llm_total_tokens),0) "
                f"FROM decisions {where}",
                args,
            )
            avg_conf, tokens_total = cur.fetchone()

            cur.execute(
                f"SELECT llm, COUNT(*) FROM decisions {where} GROUP BY llm", args
            )
            by_llm = dict(cur.fetchall())

            cur.execute(
                f"SELECT data_source, COUNT(*) FROM decisions {where} GROUP BY data_source",
                args,
            )
            by_source = dict(cur.fetchall())

            cur.execute(
                f"SELECT ts, symbol, action, confidence, entry_price, shares "
                f"FROM decisions {where} ORDER BY ts DESC LIMIT 5",
                args,
            )
            latest = [
                {
                    "ts": r[0],
                    "symbol": r[1],
                    "action": r[2],
                    "confidence": r[3],
                    "entry_price": r[4],
                    "shares": r[5],
                }
                for r in cur.fetchall()
            ]

            return {
                "total": total,
                "by_action": by_action,
                "avg_confidence": float(avg_conf) if avg_conf is not None else 0.0,
                "by_llm": by_llm,
                "by_source": by_source,
                "tokens_total": int(tokens_total or 0),
                "latest": latest,
            }
    except Exception as e:
        return {"error": str(e)}
