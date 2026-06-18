"""
北向资金（沪/深股通）— 日级净流入 + 个股持股变化

数据源（实测可用）:
- RPT_MUTUAL_DEAL_HISTORY: 日级净流入 / 累计净额（万元）
- RPT_MUTUAL_HOLDSTOCKNORTH_STA: 个股北向持股（季度披露 + 日变动）

注：东财 2024 年起停止盘中实时北向披露，全部走收盘后 + 季度。
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .em import em_table


CACHE_DB = os.path.join(os.path.expanduser("~"), ".alphalith_cache.db")
CACHE_TTL = 5 * 60


# ────────────────────────────────────────────────────────────
# SQLite 缓存（轻量复用）
# ────────────────────────────────────────────────────────────
def _cache_init() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
    conn.commit()
    return conn


def _cache_get(key: str, ttl: int = CACHE_TTL) -> Optional[str]:
    try:
        conn = _cache_init()
        row = conn.execute("SELECT v, ts FROM kv WHERE k=?", (key,)).fetchone()
        conn.close()
        if not row or time.time() - row[1] > ttl:
            return None
        return row[0]
    except Exception:
        return None


def _cache_set(key: str, value: str) -> None:
    try:
        conn = _cache_init()
        conn.execute("INSERT OR REPLACE INTO kv(k, v, ts) VALUES(?, ?, ?)",
                     (key, value, time.time()))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _f(x: object) -> float:
    try:
        if x in ("-", "", None):
            return 0.0
        return float(x)
    except Exception:
        return 0.0


# ────────────────────────────────────────────────────────────
# MUTUAL_TYPE 速查表
# ────────────────────────────────────────────────────────────
# 001 沪股通(A→北向)  002 深股通(A→北向)
# 003 沪市港股通(港→南向)  004 深市港股通(港→南向)
# 005 / 006 偶见复合统计
NORTHBOUND_TYPES = {"001", "002"}


# ────────────────────────────────────────────────────────────
# 全市场北向资金
# ────────────────────────────────────────────────────────────
@dataclass
class NorthboundFlow:
    """单位：亿元（已折算）"""
    trade_date: str = ""
    sh_net: float = 0.0
    sz_net: float = 0.0
    total_net: float = 0.0

    @property
    def summary(self) -> str:
        sign = "+" if self.total_net >= 0 else ""
        emo = "🔴" if self.total_net >= 0 else "🟢"  # A 股惯例：红涨绿跌
        return (
            f"{emo} 北向资金{sign}{self.total_net:.2f}亿 "
            f"(沪{sign}{self.sh_net:.2f} 深{sign}{self.sz_net:.2f}) "
            f"@ {self.trade_date}"
        )


def fetch_northbound_recent_days(days: int = 5) -> list[NorthboundFlow]:
    """
    近 N 个交易日北向资金（按 TRADE_DATE 倒序拉，再按 mutual_type 合并）。
    单条记录单位：万元 → 折成亿元。
    """
    cache_key = f"nb_recent_{days}"
    cached = _cache_get(cache_key, ttl=600)
    if cached:
        try:
            data = _json.loads(cached)
            return [NorthboundFlow(**x) for x in data]
        except Exception:
            pass

    # 拉足够多 (days * 4 类型) 防止漏行
    rows = em_table(
        "RPT_MUTUAL_DEAL_HISTORY",
        sort_col="TRADE_DATE",
        sort_order=-1,
        page_size=days * 4,
    )

    # 按 trade_date 聚合
    bucket: dict[str, dict] = {}
    for r in rows:
        td = (r.get("TRADE_DATE") or "")[:10]
        mt = r.get("MUTUAL_TYPE")
        if not td or mt not in NORTHBOUND_TYPES:
            continue
        net = _f(r.get("NET_DEAL_AMT")) / 1e4  # 万元 → 亿元
        b = bucket.setdefault(td, {"sh": 0.0, "sz": 0.0})
        if mt == "001":
            b["sh"] += net
        elif mt == "002":
            b["sz"] += net

    # 取最近 days 个交易日
    sorted_dates = sorted(bucket.keys(), reverse=True)[:days]
    out: list[NorthboundFlow] = []
    for td in sorted(sorted_dates):  # 升序输出
        b = bucket[td]
        out.append(NorthboundFlow(
            trade_date=td, sh_net=b["sh"], sz_net=b["sz"],
            total_net=b["sh"] + b["sz"],
        ))
    _cache_set(cache_key, _json.dumps([f.__dict__ for f in out]))
    return out


def fetch_northbound_today() -> Optional[NorthboundFlow]:
    """最新一个交易日（盘中无实时披露，等同最近一日）。"""
    recent = fetch_northbound_recent_days(1)
    return recent[-1] if recent else None


# ────────────────────────────────────────────────────────────
# 个股北向持股
# ────────────────────────────────────────────────────────────
@dataclass
class StockNorthboundChange:
    code: str = ""
    name: str = ""
    trade_date: str = ""
    holding_shares: float = 0.0
    holding_market_cap: float = 0.0
    holding_pct: float = 0.0
    cap_change_1d: float = 0.0    # 万元
    cap_change_5d: float = 0.0    # 万元

    @property
    def summary(self) -> str:
        sign = "+" if self.cap_change_1d >= 0 else ""
        sign5 = "+" if self.cap_change_5d >= 0 else ""
        return (
            f"北向持股 {self.name}({self.code}) "
            f"市值{self.holding_market_cap/1e8:.2f}亿 占{self.holding_pct:.2f}% | "
            f"1日{sign}{self.cap_change_1d/1e4:.2f}亿 5日{sign5}{self.cap_change_5d/1e4:.2f}亿"
            f" @ {self.trade_date}"
        )


def fetch_stock_northbound(code: str) -> Optional[StockNorthboundChange]:
    """
    个股最近一期北向持股（季度披露日 / 增持变动）。
    Report: RPT_MUTUAL_HOLDSTOCKNORTH_STA
    """
    rows = em_table(
        "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
        sort_col="TRADE_DATE",
        sort_order=-1,
        filters=f"(SECURITY_CODE=\"{code}\")",
        page_size=1,
    )
    if not rows:
        return None
    r = rows[0]
    return StockNorthboundChange(
        code=code,
        name=r.get("SECURITY_NAME", ""),
        trade_date=(r.get("TRADE_DATE") or "")[:10],
        holding_shares=_f(r.get("HOLD_SHARES")),
        holding_market_cap=_f(r.get("HOLD_MARKET_CAP")),
        holding_pct=_f(r.get("FREE_SHARES_RATIO") or r.get("HOLD_SHARES_RATIO")),
        cap_change_1d=_f(r.get("HOLD_MARKETCAP_CHG1")),
        cap_change_5d=_f(r.get("HOLD_MARKETCAP_CHG5")),
    )


# ────────────────────────────────────────────────────────────
# Agent 摘要
# ────────────────────────────────────────────────────────────
def summarize_market_for_agent(recent: Optional[list] = None) -> str:
    recent = recent if recent is not None else fetch_northbound_recent_days(5)
    if not recent:
        return ""
    last = recent[-1]
    avg = sum(r.total_net for r in recent) / len(recent)
    trend = "持续流入" if avg > 5 else ("持续流出" if avg < -5 else "震荡")
    return f"{last.summary} | 近{len(recent)}日均值 {avg:+.2f}亿/{trend}"


def summarize_stock_for_agent(code: str) -> str:
    snap = fetch_stock_northbound(code)
    return snap.summary if snap else ""
