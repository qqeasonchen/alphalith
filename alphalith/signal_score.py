"""
信号权重计算引擎 — 把 5 维资金流信号转成 -1.0 ~ +1.0 标准化评分。

设计原则：
1. 每个信号源独立打分（-1.0 ~ +1.0）
2. 按「决策影响力」加权求和
3. 输出：total_score + 各维度明细 + 决策建议

权重分配（可调）：
- 龙虎榜：30%（机构席位是最强短期信号）
- 大宗交易：25%（折价 = 股东出货意图）
- 北向资金：25%（外资动向，中期趋势）
- 解禁日历：15%（纯风险提示）
- 板块热点：5%（情绪加分，不主导）

评分标准：
| 信号 | +++ | ++ | + | - | -- | --- |
|---|---|---|---|---|---|---|
| 龙虎榜净买 | >5亿 | 1-5亿 | 0.5-1亿 | -0.5~0亿 | -1~0亿 | <-1亿 |
| 机构席位比 | >3:1 | 2~3:1 | 1.5~2:1 | 1:1~1.5 | 0.5~1:1 | <0.5:1 |
| 大宗折溢价 | >+5% | +2~5% | 0~+2% | -2~0% | -5~-2% | <-5% |
| 北向日级 | >+5亿 | +1~5亿 | 0~+1亿 | -1~0亿 | -5~-1亿 | <-5亿 |
| 北向持股比 | >8% | 5~8% | 3~5% | 1~3% | 0.5~1% | <0.5% |
| 解禁占流通 | <1% | 1~3% | 3~5% | 5~8% | 8~10% | >10% |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SignalScore:
    """单维度信号评分"""

    source: str  # dragon / block_trade / northbound / unlock / hotboard
    score: float  # -1.0 ~ +1.0
    detail: str  # 人类可读的评分依据
    raw_value: float = 0.0  # 原始数值（用于调试）


@dataclass
class CompositeSignal:
    """复合信号评分（5 维度 + 总分）"""

    symbol: str
    scores: list[SignalScore] = field(default_factory=list)
    total_score: float = 0.0  # -1.0 ~ +1.0
    suggestion: str = "neutral"  # strong_buy / buy / neutral / sell / strong_sell
    summary: str = ""  # 一句话总结

    def add(self, score: SignalScore):
        self.scores.append(score)

    def calculate(self):
        """按权重计算总分"""
        weights = {
            "dragon": 0.30,
            "block_trade": 0.25,
            "northbound": 0.25,
            "unlock": 0.15,
            "hotboard": 0.05,
        }
        total = 0.0
        for s in self.scores:
            w = weights.get(s.source, 0.1)
            total += s.score * w

        self.total_score = max(-1.0, min(1.0, total))

        if self.total_score > 0.5:
            self.suggestion = "strong_buy"
        elif self.total_score > 0.2:
            self.suggestion = "buy"
        elif self.total_score < -0.5:
            self.suggestion = "strong_sell"
        elif self.total_score < -0.2:
            self.suggestion = "sell"
        else:
            self.suggestion = "neutral"

        self.summary = self._make_summary()
        return self

    def _make_summary(self) -> str:
        parts = []
        for s in self.scores:
            emoji = "🟢" if s.score > 0 else "🔴" if s.score < 0 else "⚪"
            parts.append(f"{emoji} {s.source}: {s.score:+.2f}")
        return (
            f"信号总分: {self.total_score:+.2f} ({self.suggestion.upper()})\n"
            + "\n".join(parts)
        )

    @property
    def to_markdown(self) -> str:
        """生成 Markdown 格式的报告片段"""
        lines = [
            "### 📊 资金流信号评分",
            f"**总分: {self.total_score:+.2f}** ({self.suggestion.upper()})",
            "",
            "| 信号源 | 评分 | 说明 |",
            "|--------|------|------|",
        ]
        for s in self.scores:
            emoji = "🟢" if s.score > 0 else "🔴" if s.score < 0 else "⚪"
            lines.append(f"| {emoji} {s.source} | {s.score:+.2f} | {s.detail} |")

        lines += ["", f"**结论**: {self.summary.split('(')[0].strip()}", ""]
        return "\n".join(lines)


# ========== 评分函数（每个信号源一个）==========


def score_dragon(rec) -> SignalScore:
    """
    龙虎榜评分（-1.0 ~ +1.0）

    评分逻辑：
    - 净买入金额（60%权重）
    - 机构席位比（40%权重）
    """
    if not rec:
        return SignalScore(
            source="dragon", score=0.0, detail="无龙虎榜数据"
        )

    # 1. 净买入评分
    net_buy = rec.net_buy or 0.0
    if net_buy > 5e8:
        buy_score = 1.0
    elif net_buy > 1e8:
        buy_score = 0.6
    elif net_buy > 5e7:
        buy_score = 0.3
    elif net_buy > 0:
        buy_score = 0.1
    elif net_buy > -5e7:
        buy_score = -0.2
    elif net_buy > -1e8:
        buy_score = -0.5
    else:
        buy_score = -1.0

    # 2. 机构席位比评分
    seats = rec.seats or []
    buy_inst = sum(1 for s in seats if s.side == "buy" and "机构" in s.branch)
    sell_inst = sum(1 for s in seats if s.side == "sell" and "机构" in s.branch)
    total_buy = sum(1 for s in seats if s.side == "buy")
    total_sell = sum(1 for s in seats if s.side == "sell")

    inst_ratio = 0.0
    if total_buy > 0:
        inst_ratio = buy_inst / total_buy
    if total_sell > 0:
        inst_ratio -= sell_inst / total_sell

    if inst_ratio > 0.6:
        inst_score = 1.0
    elif inst_ratio > 0.3:
        inst_score = 0.5
    elif inst_ratio > 0:
        inst_score = 0.2
    elif inst_ratio > -0.3:
        inst_score = -0.3
    elif inst_ratio > -0.6:
        inst_score = -0.6
    else:
        inst_score = -1.0

    final_score = buy_score * 0.6 + inst_score * 0.4

    detail = (
        f"净买入 {net_buy/1e8:.2f}亿 "
        f"(机构买{ buy_inst}/{total_buy}, 机构卖{sell_inst}/{total_sell})"
    )

    return SignalScore(
        source="dragon",
        score=round(final_score, 2),
        detail=detail,
        raw_value=net_buy,
    )


def score_block_trade(trades: list) -> SignalScore:
    """
    大宗交易评分（-1.0 ~ +1.0）

    评分逻辑：
    - 折溢价率（70%权重）：折价 = 股东出货 → 负面
    - 交易金额（30%权重）：大额 = 信号更强
    """
    if not trades:
        return SignalScore(
            source="block_trade", score=0.0, detail="无大宗交易数据"
        )

    # 1. 平均折溢价率
    premiums = [t.premium for t in trades if t.premium is not None]
    avg_premium = sum(premiums) / len(premiums) if premiums else 0.0

    if avg_premium > 5:
        prem_score = 1.0  # 溢价 = 机构抢筹
    elif avg_premium > 2:
        prem_score = 0.6
    elif avg_premium > 0:
        prem_score = 0.3
    elif avg_premium > -2:
        prem_score = -0.2
    elif avg_premium > -5:
        prem_score = -0.5
    else:
        prem_score = -1.0  # 大幅折价 = 出货

    # 2. 交易金额（标准化到 0~1 然后映射到 -0.3~+0.3）
    total_amt = sum(t.amount for t in trades if t.amount)
    amt_score = min(total_amt / 2e9, 1.0) * 0.3  # 20亿以上 = 满分

    # 3. 机构买卖方向
    inst_buy = sum(
        1
        for t in trades
        if t.buyer and ("机构" in t.buyer or "基金" in t.buyer)
    )
    inst_sell = sum(
        1
        for t in trades
        if t.seller and ("机构" in t.seller or "基金" in t.seller)
    )

    inst_dir_score = 0.0
    if inst_buy > inst_sell:
        inst_dir_score = 0.2
    elif inst_sell > inst_buy:
        inst_dir_score = -0.2

    final_score = prem_score * 0.7 + amt_score + inst_dir_score

    detail = (
        f"平均折溢价 {avg_premium:+.2f}% "
        f"({len(trades)}笔合计{total_amt/1e8:.2f}亿)"
    )

    return SignalScore(
        source="block_trade",
        score=round(max(-1.0, min(1.0, final_score)), 2),
        detail=detail,
        raw_value=avg_premium,
    )


def score_northbound(nb_summary: str, holding_pct: float = 0.0) -> SignalScore:
    """
    北向资金评分（-1.0 ~ +1.0）

    评分逻辑：
    - 日级流向（50%权重）
    - 近 N 日趋势（30%权重）
    - 个股持股比例（20%权重）
    """
    if not nb_summary:
        return SignalScore(
            source="northbound", score=0.0, detail="无北向资金数据"
        )

    # 1. 解析日级流向
    day_score = 0.0
    if "北向资金" in nb_summary:
        # 格式：北向资金 -0.03亿  近3日 +0.12亿/震荡
        import re

        day_match = re.search(r"北向资金\s*([+-]?\d+\.?\d*)\s*亿", nb_summary)
        if day_match:
            day_amt = float(day_match.group(1)) * 1e8  # 转为元
            if day_amt > 5e8:
                day_score = 1.0
            elif day_amt > 1e8:
                day_score = 0.6
            elif day_amt > 0:
                day_score = 0.3
            elif day_amt > -1e8:
                day_score = -0.2
            elif day_amt > -5e8:
                day_score = -0.5
            else:
                day_score = -1.0

    # 2. 近 N 日趋势（从 nb_summary 解析）
    trend_score = 0.0
    if "近3日" in nb_summary:
        import re

        trend_match = re.search(r"近3日\s*([+-]?\d+\.?\d*)\s*亿", nb_summary)
        if trend_match:
            trend_amt = float(trend_match.group(1)) * 1e8
            if trend_amt > 3e8:
                trend_score = 0.5
            elif trend_amt > 0:
                trend_score = 0.3
            elif trend_amt > -3e8:
                trend_score = -0.3
            else:
                trend_score = -0.5

    # 3. 持股比例
    holding_score = 0.0
    if holding_pct > 8:
        holding_score = 1.0
    elif holding_pct > 5:
        holding_score = 0.6
    elif holding_pct > 3:
        holding_score = 0.3
    elif holding_pct > 1:
        holding_score = 0.0
    elif holding_pct > 0.5:
        holding_score = -0.2
    else:
        holding_score = -0.5

    final_score = day_score * 0.5 + trend_score * 0.3 + holding_score * 0.2

    detail = (
        f"日级流向 {day_score:+.2f} "
        f"趋势 {trend_score:+.2f} "
        f"持股{holding_pct:.2f}%"
    )

    return SignalScore(
        source="northbound",
        score=round(final_score, 2),
        detail=detail,
        raw_value=day_amt if "day_amt" in dir() else 0.0,
    )


def score_unlock(events: list) -> SignalScore:
    """
    解禁日历评分（-1.0 ~ +1.0）

    评分逻辑：
    - 未来 30 天内解禁占比（越高越负面）
    - 解禁类型（首发/定增 = 更可能减持）
    """
    if not events:
        return SignalScore(
            source="unlock", score=0.0, detail="未来无解禁"
        )

    # 计算未来 30 天总解禁占流通比
    import datetime

    today = datetime.date.today()
    future_events = [
        e for e in events if e.unlock_date and e.unlock_date >= today
    ]

    if not future_events:
        return SignalScore(
            source="unlock", score=0.0, detail="未来无解禁"
        )

    # 取最近的解禁事件
    nearest = min(future_events, key=lambda e: e.unlock_date)
    ratio = nearest.ratio_of_float or 0.0

    if ratio < 0.01:
        score = 0.1
    elif ratio < 0.03:
        score = -0.1
    elif ratio < 0.05:
        score = -0.3
    elif ratio < 0.08:
        score = -0.5
    elif ratio < 0.10:
        score = -0.7
    else:
        score = -1.0

    detail = (
        f"最近解禁 {nearest.unlock_date} "
        f"占流通{ratio:.2f}% [{nearest.type}]"
    )

    return SignalScore(
        source="unlock",
        score=round(score, 2),
        detail=detail,
        raw_value=ratio,
    )


def score_hotboard(themes: list[str]) -> SignalScore:
    """
    板块热点评分（-1.0 ~ +1.0）

    评分逻辑：
    - 个股是否属于热点板块（是 = 正面，不是 = 中性）
    - 热点强度（上榜次数越多 = 越强）
    """
    if not themes:
        return SignalScore(
            source="hotboard", score=0.0, detail="无热点归因"
        )

    # 简单逻辑：有热点归因 = +0.3，无 = 0
    # 如果有「连续上榜」= +0.5
    score = 0.0
    for theme in themes:
        if "连续" in theme or "持续" in theme:
            score = max(score, 0.5)
        else:
            score = max(score, 0.3)

    detail = f"热点归因: {', '.join(themes[:3])}"

    return SignalScore(
        source="hotboard",
        score=round(min(score, 1.0), 2),
        detail=detail,
    )


def calculate_signal_score(
    symbol: str,
    dragon_rec=None,
    block_trades: list = None,
    nb_summary: str = "",
    nb_holding_pct: float = 0.0,
    unlock_events: list = None,
    hot_themes: list[str] = None,
) -> CompositeSignal:
    """
    一站式计算复合信号评分。

    用法：
    ```python
    from alphalith.signal_score import calculate_signal_score

    sig = calculate_signal_score(
        symbol="603986",
        dragon_rec=dragon_rec,
        block_trades=trades,
        nb_summary="北向资金 -0.03亿  近3日 +0.12亿/震荡\n北向持股 6.19%",
        nb_holding_pct=6.19,
        unlock_events=events,
        hot_themes=["大涨上榜×18", "连续三日×14"],
    )
    print(sig.to_markdown)
    ```
    """
    comp = CompositeSignal(symbol=symbol)

    # 1. 龙虎榜
    if dragon_rec:
        comp.add(score_dragon(dragon_rec))

    # 2. 大宗交易
    if block_trades:
        comp.add(score_block_trade(block_trades))

    # 3. 北向资金
    if nb_summary:
        comp.add(score_northbound(nb_summary, nb_holding_pct))

    # 4. 解禁日历
    if unlock_events:
        comp.add(score_unlock(unlock_events))

    # 5. 板块热点
    if hot_themes:
        comp.add(score_hotboard(hot_themes))

    return comp.calculate()
