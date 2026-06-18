"""限售解禁日历模块（A 股）。

数据源：东方财富数据中心 RPT_LIFT_STAGE
- 历史解禁记录 + 未来 90 天待解禁
- 单位：股 / 元

价值：
- 中长期持仓必查（解禁规模 > 流通市值 5% → 抛压预警）
- 与基本面分析师串联，对"限售压力"建模
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass
from typing import Optional

from .em import em_table


@dataclass
class UnlockEvent:
    code: str
    name: str
    unlock_date: str
    shares: float  # 解禁股数
    market_cap: float  # 解禁市值（元）
    ratio_of_total: float  # 占总股本比 (%)
    ratio_of_float: float  # 占流通股比 (%)
    type: str  # 类型（首发原始股/定增 …）

    def to_dict(self) -> dict:
        return asdict(self)


def _date_range(days: int = 90) -> tuple[str, str]:
    today = _dt.date.today()
    end = today + _dt.timedelta(days=days)
    return today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def fetch_upcoming_unlocks(days: int = 90, page_size: int = 100) -> list[UnlockEvent]:
    """未来 N 天的解禁日历（全市场）。"""
    start, end = _date_range(days)
    filters = f"(FREE_DATE>='{start}')(FREE_DATE<='{end}')"
    rows = em_table(
        "RPT_LIFT_STAGE",
        sort_col="FREE_DATE",
        sort_order=1,
        filters=filters,
        page_size=page_size,
    )
    return [_parse(r) for r in rows]


def fetch_stock_unlocks(code: str, *, future_days: int = 365, history_days: int = 365) -> list[UnlockEvent]:
    """单只股票最近一年 + 未来一年解禁记录。"""
    today = _dt.date.today()
    start = (today - _dt.timedelta(days=history_days)).strftime("%Y-%m-%d")
    end = (today + _dt.timedelta(days=future_days)).strftime("%Y-%m-%d")
    filters = f"(SECURITY_CODE=\"{code}\")(FREE_DATE>='{start}')(FREE_DATE<='{end}')"
    rows = em_table(
        "RPT_LIFT_STAGE",
        sort_col="FREE_DATE",
        sort_order=1,
        filters=filters,
        page_size=50,
    )
    return [_parse(r) for r in rows]


def _parse(r: dict) -> UnlockEvent:
    return UnlockEvent(
        code=r.get("SECURITY_CODE", ""),
        name=r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME", ""),
        unlock_date=(r.get("FREE_DATE") or "")[:10],
        shares=float(r.get("LIFT_NUM") or 0),
        market_cap=float(r.get("CURRENT_FREE_CAP") or r.get("LIFT_MARKET_CAP") or 0),
        ratio_of_total=float(r.get("TOTAL_RATIO") or 0),
        ratio_of_float=float(r.get("FREE_RATIO") or 0),
        type=r.get("ABLITE_NAME") or r.get("BATCH_TYPE_NAME", ""),
    )


def summarize_for_agent(events: list[UnlockEvent], code: str) -> str:
    """对基本面分析师友好的中文摘要。"""
    if not events:
        return f"解禁: 未来无解禁安排（{code}）"
    upcoming = [e for e in events if e.unlock_date >= _dt.date.today().strftime("%Y-%m-%d")]
    if not upcoming:
        return f"解禁: 未来无解禁安排（{code}）"
    nearest = upcoming[0]
    total_cap = sum(e.market_cap for e in upcoming)
    risk = ""
    if nearest.ratio_of_float > 5:
        risk = " ⚠️抛压偏大"
    elif nearest.ratio_of_float > 2:
        risk = " 注意抛压"
    return (
        f"解禁[{nearest.unlock_date}] {nearest.name}({nearest.code}) "
        f"{nearest.shares/1e8:.2f}亿股 / {nearest.market_cap/1e8:.2f}亿元 "
        f"占流通 {nearest.ratio_of_float:.2f}% [{nearest.type}]{risk} "
        f"| 未来共 {len(upcoming)} 笔合计 {total_cap/1e8:.2f}亿"
    )
