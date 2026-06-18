"""龙虎榜（Dragon-Tiger List）数据模块。

数据源：东方财富数据中心
- 列表：RPT_DAILYBILLBOARD_DETAILSNEW（每日上榜个股 + 上榜原因 + 净买入）
- 买卖席位：RPT_BILLBOARD_DAILYDETAILSBUY / RPT_BILLBOARD_DAILYDETAILSSELL（前 5 营业部）

价值：
- 短线情绪信号
- 识别游资 / 机构动向（"机构专用"席位 = 机构）
- 与龙头题材联动
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from typing import Optional

from .em import em_table


@dataclass
class DragonSeat:
    rank: int  # 1-5
    side: str  # "buy" / "sell"
    branch: str  # 营业部名
    amount: float  # 成交额（元）
    net: float  # 净额（元，正负）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DragonRecord:
    code: str
    name: str
    trade_date: str
    reason: str  # 上榜原因
    close: float
    change_pct: float
    turnover: float  # 龙虎榜成交额
    net_buy: float  # 总净买入（元）
    buy_amount: float
    sell_amount: float
    seats: list[DragonSeat] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["seats"] = [s.to_dict() for s in self.seats]
        return d

    @property
    def has_institution(self) -> bool:
        return any("机构专用" in s.branch for s in self.seats)

    @property
    def institution_net(self) -> float:
        return sum(s.net for s in self.seats if "机构专用" in s.branch)


def _today_str() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def fetch_dragon_list(
    trade_date: Optional[str] = None,
    *,
    code: Optional[str] = None,
    page_size: int = 50,
) -> list[DragonRecord]:
    """拉取某日龙虎榜列表。

    Args:
        trade_date: 'YYYY-MM-DD'，默认最近交易日
        code: 限定股票代码（如 '600519'）
        page_size: 最大返回条数
    """
    # 字段值带时间，需用范围匹配
    date_str = trade_date or _today_str()
    filters = f"(TRADE_DATE>='{date_str} 00:00:00')(TRADE_DATE<='{date_str} 23:59:59')"
    if code:
        filters += f"(SECURITY_CODE=\"{code}\")"

    rows = em_table(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        sort_col="BILLBOARD_NET_AMT",
        sort_order=-1,
        filters=filters,
        page_size=page_size,
    )

    # 当日无榜单 → 自动回溯到最近交易日
    if not rows and not trade_date:
        rows = em_table(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            sort_col="TRADE_DATE,BILLBOARD_NET_AMT",
            sort_order=-1,
            page_size=page_size,
        )

    records: list[DragonRecord] = []
    for r in rows:
        rec = DragonRecord(
            code=r.get("SECURITY_CODE", ""),
            name=r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME", ""),
            trade_date=(r.get("TRADE_DATE") or "")[:10],
            reason=r.get("EXPLANATION") or r.get("EXPLAIN") or "",
            close=float(r.get("CLOSE_PRICE") or 0),
            change_pct=float(r.get("CHANGE_RATE") or 0),
            turnover=float(r.get("BILLBOARD_DEAL_AMT") or 0),
            net_buy=float(r.get("BILLBOARD_NET_AMT") or 0),
            buy_amount=float(r.get("BILLBOARD_BUY_AMT") or 0),
            sell_amount=float(r.get("BILLBOARD_SELL_AMT") or 0),
        )
        records.append(rec)
    return records


def fetch_seats(code: str, trade_date: Optional[str] = None) -> list[DragonSeat]:
    """拉取某只个股某日的买卖前 5 营业部明细。"""
    date_str = trade_date or _today_str()
    base_filter = (
        f"(TRADE_DATE>='{date_str} 00:00:00')"
        f"(TRADE_DATE<='{date_str} 23:59:59')"
        f"(SECURITY_CODE=\"{code}\")"
    )

    seats: list[DragonSeat] = []
    for side, report in (
        ("buy", "RPT_BILLBOARD_DAILYDETAILSBUY"),
        ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL"),
    ):
        rows = em_table(
            report,
            sort_col="BUY_AMT" if side == "buy" else "SELL_AMT",
            sort_order=-1,
            filters=base_filter,
            page_size=10,
        )
        for r in rows:
            seats.append(
                DragonSeat(
                    rank=int(r.get("RANK") or 0),
                    side=side,
                    branch=r.get("OPERATEDEPT_NAME", ""),
                    amount=float(
                        r.get("BUY_AMT" if side == "buy" else "SELL_AMT") or 0
                    ),
                    net=float(r.get("NET_AMT") or 0),
                )
            )
    return seats


def fetch_dragon_with_seats(code: str, trade_date: Optional[str] = None) -> Optional[DragonRecord]:
    """便捷接口：一只股票完整龙虎榜信息（含席位）。"""
    recs = fetch_dragon_list(trade_date=trade_date, code=code, page_size=5)
    if not recs:
        return None
    rec = recs[0]
    rec.seats = fetch_seats(code, rec.trade_date)
    return rec


def summarize_for_agent(record: DragonRecord) -> str:
    """渲染成一行让基本面/情绪分析师能直接消费的中文摘要。"""
    if not record:
        return ""
    direction = "净买入" if record.net_buy >= 0 else "净卖出"
    line = (
        f"龙虎榜[{record.trade_date}] {record.name}({record.code}) "
        f"涨跌 {record.change_pct:+.2f}% | {direction} {record.net_buy/1e8:.2f}亿 "
        f"| 上榜原因: {record.reason}"
    )
    if record.has_institution:
        line += f" | 机构净 {record.institution_net/1e8:+.2f}亿"
    return line
