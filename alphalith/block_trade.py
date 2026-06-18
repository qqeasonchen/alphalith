"""大宗交易模块（A 股）。

数据源：东方财富数据中心 RPT_DATA_BLOCKTRADE
- 成交价 vs 收盘价的折溢价
- 买卖营业部（含机构专用）

价值：
- 大宗交易折价 → 大股东减持/产业资本撤离
- 大宗交易溢价 → 接盘方看好
- 与龙虎榜交叉验证机构动向
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass
from typing import Optional

from .em import em_table


@dataclass
class BlockTrade:
    code: str
    name: str
    trade_date: str
    price: float  # 成交价
    close: float  # 当日收盘
    premium: float  # 折溢价 %  (price/close-1)*100
    volume: float  # 成交量（股）
    amount: float  # 成交额（元）
    buy_branch: str  # 买方营业部
    sell_branch: str  # 卖方营业部

    def to_dict(self) -> dict:
        return asdict(self)


def _today_str() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def fetch_block_trades(
    *,
    code: Optional[str] = None,
    trade_date: Optional[str] = None,
    days: int = 30,
    page_size: int = 50,
) -> list[BlockTrade]:
    """拉取大宗交易记录。

    - 不传 code/trade_date → 最近 days 天全市场
    - 传 code → 该股最近 days 天
    - 传 trade_date → 当日全市场
    """
    if trade_date:
        filters = f"(TRADE_DATE='{trade_date}')"
    else:
        end = _dt.date.today()
        start = end - _dt.timedelta(days=days)
        filters = f"(TRADE_DATE>='{start.strftime('%Y-%m-%d')}')(TRADE_DATE<='{end.strftime('%Y-%m-%d')}')"
    if code:
        filters += f"(SECURITY_CODE=\"{code}\")"

    rows = em_table(
        "RPT_DATA_BLOCKTRADE",
        sort_col="TRADE_DATE",
        sort_order=-1,
        filters=filters,
        page_size=page_size,
    )

    out: list[BlockTrade] = []
    for r in rows:
        price = float(r.get("DEAL_PRICE") or 0)
        close = float(r.get("CLOSE_PRICE") or 0)
        premium = (price / close - 1) * 100 if close else 0.0
        out.append(
            BlockTrade(
                code=r.get("SECURITY_CODE", ""),
                name=r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME", ""),
                trade_date=(r.get("TRADE_DATE") or "")[:10],
                price=price,
                close=close,
                premium=premium,
                volume=float(r.get("DEAL_VOLUME") or 0),
                amount=float(r.get("DEAL_AMT") or 0),
                buy_branch=r.get("BUYER_NAME") or r.get("BUY_DEPT_NAME", ""),
                sell_branch=r.get("SELLER_NAME") or r.get("SELL_DEPT_NAME", ""),
            )
        )
    return out


def summarize_for_agent(trades: list[BlockTrade], code: Optional[str] = None) -> str:
    """让分析师能直接消费的中文摘要。"""
    if not trades:
        return f"大宗交易: 近期无记录（{code or '全市场'}）"

    total_amt = sum(t.amount for t in trades)
    avg_premium = sum(t.premium for t in trades) / len(trades)
    inst_buy = sum(1 for t in trades if "机构专用" in t.buy_branch)
    inst_sell = sum(1 for t in trades if "机构专用" in t.sell_branch)

    direction = (
        "整体溢价（接盘意愿强）" if avg_premium > 1
        else "整体折价（出货压力）" if avg_premium < -1
        else "平价"
    )
    head = (
        f"大宗交易: {len(trades)}笔合计{total_amt/1e8:.2f}亿元 "
        f"均{direction} 折溢价 {avg_premium:+.2f}%"
    )
    if inst_buy or inst_sell:
        head += f" | 机构席位: 买{inst_buy}/卖{inst_sell}"

    # 列出最大一笔
    top = max(trades, key=lambda t: t.amount)
    head += (
        f"\n最大单: {top.name}({top.code}) {top.trade_date} "
        f"{top.amount/1e8:.2f}亿 {top.premium:+.2f}% "
        f"{top.buy_branch} ← {top.sell_branch}"
    )
    return head
