"""
Backtest — 回测：用历史 K 线滚动评估策略表现。

策略说明：
  默认策略（v1，纯技术）：
    - 5 日均线 > 20 日均线 且 当日收涨 → buy
    - 5 日均线 < 20 日均线 且 当日收跌 → sell
    - 否则                              → hold

  v3 策略模板（4 种）：
    - macd：DIF(12/26) 三阶方向转折，动量反转信号
    - rsi：RSI(14) 超买超卖 30/70，方向确认防毛刺
    - bollinger：布林带(20,2.0) %B 策略，0.15/0.85 阈值
    - momentum：20 日动量突破（涨幅>8% + 创20日新高做多，跌幅<-5% + 创20日新低做空）

  v4 因子模板（2 种）：
    - momentum：20 日动量突破（涨幅>8%+创20日新高→buy，跌幅<-5%+创20日新低→sell）
    - reversal：z-score 反转因子（偏离10日均线>2σ→均值回归），极度精选（3-4笔/90日）

  LLM 决策器（--strategy llm）：
    - 把当时刻的 OHLCV 摘要 + 最近 K 线趋势喂给 LLM
    - LLM 输出 JSON {"action":"buy/sell/hold","confidence":...,"reason":"..."}
    - 模拟"如果当时让 Alphalith 委员会决策，结果会怎样"
    - 注意：每根 K 线都跑一次 LLM，成本随 days 线性增长——默认 days=30 控量

  指标（v3 新增）：
    - 胜率 / 平均单笔 / 累计收益（沿用）
    - 最大回撤 / 夏普比率 / Calmar / Sortino
    - 信息比率（超额收益 vs B&H 跟踪误差）
    - 最长连胜 / 最长连败 / 盈亏比

零依赖：仅用标准库。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from .data import Bar, load_history
from .market import detect_market


@dataclass
class Trade:
    date: str
    action: str            # buy | sell | hold
    price: float
    horizon_days: int
    exit_date: str
    exit_price: float
    pnl_pct: float         # buy: exit/price-1，sell: 1-exit/price
    confidence: float = 0.6
    reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    market: str
    bars: int
    horizon: int
    strategy: str = "ma_cross"
    trades: list[Trade] = field(default_factory=list)
    # 用于基准对比 / 可视化的原始 K 线（轻量保留 date+close）
    bar_dates: list[str] = field(default_factory=list)
    bar_closes: list[float] = field(default_factory=list)

    @property
    def n_buy(self) -> int:  return sum(1 for t in self.trades if t.action == "buy")
    @property
    def n_sell(self) -> int: return sum(1 for t in self.trades if t.action == "sell")
    @property
    def n_hold(self) -> int: return sum(1 for t in self.trades if t.action == "hold")
    @property
    def actionable(self) -> list[Trade]:
        return [t for t in self.trades if t.action in ("buy", "sell")]

    @property
    def win_rate(self) -> float:
        a = self.actionable
        if not a:
            return 0.0
        return sum(1 for t in a if t.pnl_pct > 0) / len(a)

    @property
    def avg_pnl(self) -> float:
        a = self.actionable
        return (sum(t.pnl_pct for t in a) / len(a)) if a else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_pct for t in self.actionable)

    @property
    def best(self) -> Optional[Trade]:
        a = self.actionable
        return max(a, key=lambda t: t.pnl_pct) if a else None

    @property
    def worst(self) -> Optional[Trade]:
        a = self.actionable
        return min(a, key=lambda t: t.pnl_pct) if a else None

    @property
    def max_drawdown(self) -> float:
        """基于 actionable trade 的累计 PnL 曲线计算最大回撤。
        返回负值（如 -0.087 表示 -8.7%）；无交易返回 0。

        注意：这是"按时间顺序累加每笔 PnL"的回撤，不是真实仓位等权回测——
        当 actionable trade 高度重叠（短窗口策略）时数值会偏大，仅作相对比较参考。
        """
        a = self.actionable
        if not a:
            return 0.0
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in a:
            equity += t.pnl_pct
            peak = max(peak, equity)
            dd = equity - peak  # ≤ 0
            max_dd = min(max_dd, dd)
        return max_dd

    @property
    def sharpe(self) -> float:
        """简化年化夏普：假设每笔间隔 horizon 个交易日，无风险利率为 0。
        年化 = mean / std * sqrt(252 / horizon)。
        样本不足或方差为 0 时返回 0。
        """
        a = self.actionable
        if len(a) < 2:
            return 0.0
        rs = [t.pnl_pct for t in a]
        mean = sum(rs) / len(rs)
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        periods_per_year = 252 / max(self.horizon, 1)
        return mean / std * math.sqrt(periods_per_year)

    # ---------- buy & hold 基准 ----------
    @property
    def buy_hold_return(self) -> float:
        """从首根 K 线 close 一直持有到末根 close 的收益率。"""
        if len(self.bar_closes) < 2:
            return 0.0
        first, last = self.bar_closes[0], self.bar_closes[-1]
        if first == 0:
            return 0.0
        return (last - first) / first

    @property
    def alpha_vs_bh(self) -> float:
        """策略累计 - buy&hold 累计；正数表示跑赢基准。"""
        return self.total_pnl - self.buy_hold_return

    @property
    def equity_curve(self) -> list[tuple[str, float]]:
        """按交易日构造资金曲线（actionable trade 累加）。
        返回 [(date, cumulative_pnl_pct), ...]，
        无 actionable trade 时返回空列表。
        """
        a = sorted(self.actionable, key=lambda t: t.date)
        out: list[tuple[str, float]] = []
        eq = 0.0
        for t in a:
            eq += t.pnl_pct
            out.append((t.date, eq))
        return out

    @property
    def buy_hold_curve(self) -> list[tuple[str, float]]:
        """按 K 线构造 buy & hold 曲线 [(date, ret), ...]。"""
        if len(self.bar_closes) < 2:
            return []
        first = self.bar_closes[0]
        if first == 0:
            return []
        return [
            (d, (c - first) / first)
            for d, c in zip(self.bar_dates, self.bar_closes)
        ]

    # ---------- 连胜/连败 ----------
    @property
    def max_win_streak(self) -> int:
        """最长连续盈利笔数。"""
        a = self.actionable
        cur = best = 0
        for t in a:
            if t.pnl_pct > 0:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    @property
    def max_loss_streak(self) -> int:
        """最长连续亏损笔数。"""
        a = self.actionable
        cur = best = 0
        for t in a:
            if t.pnl_pct < 0:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best

    @property
    def win_loss_ratio(self) -> float:
        """盈亏比：盈利笔平均收益 / |亏损笔平均损失|。"""
        a = self.actionable
        wins = [t.pnl_pct for t in a if t.pnl_pct > 0]
        losses = [t.pnl_pct for t in a if t.pnl_pct < 0]
        if not wins or not losses:
            return 0.0
        avg_w = sum(wins) / len(wins)
        avg_l = abs(sum(losses) / len(losses))
        return avg_w / avg_l if avg_l > 0 else 0.0

    # ---------- 信息比率 ----------
    @property
    def info_ratio(self) -> float:
        """信息比率：超额收益 / 跟踪误差（年化）。

        对每笔 actionable trade 计算超额收益 = 策略收益 - 同期 B&H 收益，
        再年化。仅 >0 时策略才真正跑赢被动持有。
        """
        a = self.actionable
        if len(a) < 2:
            return 0.0
        excess = []
        for t in a:
            # buy: 策略做多 = B&H 同期做多 → 超额 = 0
            # sell: 策略做空获利 = B&H 同期亏损 → 超额 = 2 * pnl
            if t.action == "buy":
                excess.append(0.0)
            else:  # sell
                excess.append(2.0 * t.pnl_pct)
        mean_ex = sum(excess) / len(excess)
        var_ex = sum((e - mean_ex) ** 2 for e in excess) / (len(excess) - 1)
        std_ex = math.sqrt(var_ex) if var_ex > 0 else 0.0
        if std_ex == 0:
            return 0.0
        return mean_ex / std_ex * math.sqrt(self._periods_per_year)

    # ---------- 风险指标：Calmar / Sortino ----------
    @property
    def _periods_per_year(self) -> float:
        return 252.0 / max(self.horizon, 1)

    @property
    def calmar(self) -> float:
        """Calmar 比率 = 年化收益率 / |最大回撤|。
        衡量每单位最大回撤能产生多少收益，越大越好。
        回撤为 0 或无交易时返回 0。
        """
        a = self.actionable
        if len(a) < 2:
            return 0.0
        md = abs(self.max_drawdown)
        if md == 0:
            return 0.0
        # 年化收益 = (1 + 总收益)^(年周期数) - 1
        n_periods = len(a)
        years = n_periods / self._periods_per_year if self._periods_per_year > 0 else 1.0
        total = self.total_pnl
        if total <= -1.0:
            ann_ret = -1.0
        elif years > 0:
            ann_ret = (1.0 + total) ** (1.0 / years) - 1.0
        else:
            ann_ret = total
        return ann_ret / md

    @property
    def sortino(self) -> float:
        """Sortino 比率：只惩罚下行波动。
        = (mean_return - risk_free) / downside_deviation * sqrt(periods_per_year)
        下行偏差仅计算 r < 0 的部分。
        """
        a = self.actionable
        if len(a) < 2:
            return 0.0
        rs = [t.pnl_pct for t in a]
        mean = sum(rs) / len(rs)
        # 下行偏差：只取负收益
        downside = [min(r, 0.0) for r in rs]
        # 分母用 len(downside) - 1（样本标准差）
        ss = sum(d ** 2 for d in downside)
        d_std = math.sqrt(ss / (len(downside) - 1)) if len(downside) > 1 else 0.0
        if d_std == 0:
            return 0.0
        return mean / d_std * math.sqrt(self._periods_per_year)


# ---------- 决策器：返回 (action, confidence, reason) ----------
def _sma(values: list[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


# 决策器签名：(bars_so_far) -> (action, confidence, reason)
DecideResult = tuple[str, float, str]


def default_decide(bars_so_far: list[Bar]) -> DecideResult:
    """v1 默认决策器：5/20 均线 + 当日涨跌。"""
    if len(bars_so_far) < 21:
        return ("hold", 0.5, "数据不足 21 根 K 线，等待")
    closes = [b.close for b in bars_so_far]
    sma5 = _sma(closes, 5)
    sma20 = _sma(closes, 20)
    today = bars_so_far[-1]
    yday = bars_so_far[-2]
    rose = today.close > yday.close
    fell = today.close < yday.close
    if sma5 and sma20:
        gap = (sma5 - sma20) / sma20
        if sma5 > sma20 and rose:
            return ("buy", min(0.9, 0.55 + abs(gap) * 5),
                    f"SMA5({sma5:.2f}) > SMA20({sma20:.2f}) 且当日收涨")
        if sma5 < sma20 and fell:
            return ("sell", min(0.9, 0.55 + abs(gap) * 5),
                    f"SMA5({sma5:.2f}) < SMA20({sma20:.2f}) 且当日收跌")
    return ("hold", 0.5, "均线与价格信号不一致")


# ---------- v3 策略模板：MACD / RSI / 布林 ----------
def _ema(values: list[float], n: int) -> Optional[float]:
    """指数移动平均。"""
    if len(values) < n:
        return None
    k = 2.0 / (n + 1)
    ema = sum(values[:n]) / n
    for v in values[n:]:
        ema = v * k + ema * (1 - k)
    return ema


def _std(values: list[float]) -> float:
    """总体标准差（零依赖）。"""
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def macd_decide(bars_so_far: list[Bar]) -> DecideResult:
    """MACD(12/26/9) DIF 方向转折策略。"""
    if len(bars_so_far) < 27:
        return ("hold", 0.5, "数据不足 27 根 K 线")
    closes = [b.close for b in bars_so_far]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    if ema12 is None or ema26 is None:
        return ("hold", 0.5, "EMA 计算失败")
    dif = ema12 - ema26

    # 前一日和前两日 DIF
    prev = closes[:-1]
    p_ema12 = _ema(prev, 12)
    p_ema26 = _ema(prev, 26)
    p_dif = (p_ema12 - p_ema26) if (p_ema12 and p_ema26) else dif

    prev2 = closes[:-2]
    p2_ema12 = _ema(prev2, 12)
    p2_ema26 = _ema(prev2, 26)
    p2_dif = (p2_ema12 - p2_ema26) if (p2_ema12 and p2_ema26) else p_dif

    today = bars_so_far[-1]
    yday = bars_so_far[-2]
    rising = today.close > yday.close

    # DIF 由跌转升 → buy；由升转跌 → sell
    if p2_dif > p_dif and p_dif < dif and rising:
        strength = min(0.9, 0.55 + abs(dif - p_dif) / closes[-1] * 50)
        return ("buy", strength, f"MACD DIF 转升({dif:+.2f})，动量反转做多")
    if p2_dif < p_dif and p_dif > dif and not rising:
        strength = min(0.9, 0.55 + abs(p_dif - dif) / closes[-1] * 50)
        return ("sell", strength, f"MACD DIF 转跌({dif:+.2f})，动量衰竭做空")
    return ("hold", 0.5, f"MACD DIF({dif:+.2f}) 方向延续")


def rsi_decide(bars_so_far: list[Bar]) -> DecideResult:
    """RSI(14) 超买超卖策略。"""
    if len(bars_so_far) < 16:
        return ("hold", 0.5, "数据不足 16 根 K 线")
    closes = [b.close for b in bars_so_far]
    n = 14
    gains = []
    losses = []
    for i in range(len(closes) - n, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    if avg_loss == 0:
        rsi_val = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_val = 100.0 - 100.0 / (1.0 + rs)

    # 前一日的 RSI 用于判断方向
    prev_closes = closes[:-1]
    prev_gains = []
    prev_losses = []
    for i in range(len(prev_closes) - n, len(prev_closes)):
        diff = prev_closes[i] - prev_closes[i - 1]
        if diff > 0:
            prev_gains.append(diff)
            prev_losses.append(0.0)
        else:
            prev_gains.append(0.0)
            prev_losses.append(-diff)
    prev_avg_g = sum(prev_gains) / n
    prev_avg_l = sum(prev_losses) / n
    prev_rsi = 100.0 - 100.0 / (1.0 + prev_avg_g / prev_avg_l) if prev_avg_l > 0 else 100.0

    if rsi_val < 30 and prev_rsi < rsi_val:
        return ("buy", min(0.9, 0.55 + (30 - rsi_val) / 30),
                f"RSI({rsi_val:.1f}) 超卖区反弹，抄底信号")
    if rsi_val > 70 and prev_rsi > rsi_val:
        return ("sell", min(0.9, 0.55 + (rsi_val - 70) / 30),
                f"RSI({rsi_val:.1f}) 超买区回落，止盈信号")
    return ("hold", 0.5, f"RSI({rsi_val:.1f}) 中性区间")


def bollinger_decide(bars_so_far: list[Bar]) -> DecideResult:
    """布林带(20,2.0) %B 策略：%B < 0.05 超卖买入，%B > 0.95 超买卖出。"""
    if len(bars_so_far) < 21:
        return ("hold", 0.5, "数据不足 21 根 K 线")
    closes = [b.close for b in bars_so_far]
    n = 20
    recent = closes[-n:]
    mid = sum(recent) / n
    std = _std(recent)
    upper = mid + 2.0 * std
    lower = mid - 2.0 * std
    price = closes[-1]
    b_pct = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    if b_pct < 0.15:
        dist = 0.15 - b_pct
        return ("buy", min(0.9, 0.55 + dist * 3),
                f"%B={b_pct*100:.1f}% 接近下轨({lower:.2f})，超跌买入")
    if b_pct > 0.85:
        dist = b_pct - 0.85
        return ("sell", min(0.9, 0.55 + dist * 3),
                f"%B={b_pct*100:.1f}% 接近上轨({upper:.2f})，超涨卖出")
    return ("hold", 0.5,
            f"%B={b_pct*100:.1f}% 中轨({mid:.2f}) 带宽({(upper-lower)/mid*100:.1f}%)")


# ---------- v4 策略模板：动量 / 反转因子 ----------
def momentum_decide(bars_so_far: list[Bar]) -> DecideResult:
    """20 日动量突破策略。

    逻辑：
    - buy：20 日涨幅 > 8% 且价格创 20 日新高 → 趋势确立，追涨做多
    - sell：20 日涨幅 < -5% 且价格创 20 日新低 → 趋势破位，止损做空
    - hold：其他情况
    """
    if len(bars_so_far) < 22:
        return ("hold", 0.5, "数据不足 22 根 K 线")
    closes = [b.close for b in bars_so_far]
    lookback = 20
    window = closes[-lookback:]
    today = closes[-1]
    high20 = max(window)
    low20 = min(window)
    ret20 = (today - window[0]) / window[0]

    # 涨幅阈值和创近期新高
    if ret20 > 0.08 and today >= high20:
        strength = min(0.9, 0.55 + ret20 * 2)
        return ("buy", strength,
                f"动量突破：20日涨幅{ret20*100:.1f}% + 创20日新高({high20:.2f})")
    # 跌幅阈值和创近期新低
    if ret20 < -0.05 and today <= low20:
        strength = min(0.9, 0.55 + abs(ret20) * 2)
        return ("sell", strength,
                f"动量破位：20日跌幅{ret20*100:.1f}% + 创20日新低({low20:.2f})")
    return ("hold", 0.5,
            f"20日动量{ret20*100:+.1f}%，不触发（阈值+8%/-5%+突破确认）")


def reversal_decide(bars_so_far: list[Bar]) -> DecideResult:
    """短线反转因子：均值回归，恐慌买入，狂热卖出。

    逻辑：
    - buy：偏离 10 日均线 > 2.0σ 下方 → 超跌反弹
    - sell：偏离 10 日均线 > 2.0σ 上方 → 超涨回落
    - hold：中性区间
    """
    if len(bars_so_far) < 12:
        return ("hold", 0.5, "数据不足 12 根 K 线")
    closes = [b.close for b in bars_so_far]
    n = 10
    window = closes[-n:]
    mean10 = sum(window) / n
    std10 = _std(window)
    today = closes[-1]
    if std10 == 0:
        return ("hold", 0.5, "波动为零")
    z = (today - mean10) / std10  # z-score

    if z < -2.0:
        strength = min(0.9, 0.55 + abs(z) * 0.15)
        return ("buy", strength,
                f"z={z:.1f} 超跌（{today:.2f} vs 均{mean10:.2f} σ{std10:.2f}），均值回归做多")
    if z > 2.0:
        strength = min(0.9, 0.55 + z * 0.15)
        return ("sell", strength,
                f"z={z:.1f} 超涨（{today:.2f} vs 均{mean10:.2f} σ{std10:.2f}），均值回归做空")
    return ("hold", 0.5,
            f"z={z:+.1f} 中性（{today:.2f} vs 均{mean10:.2f}），无极端偏离")


# ---------- 策略注册表 ----------
STRATEGIES: dict[str, Callable[[list[Bar]], DecideResult]] = {
    "ma_cross": default_decide,
    "macd": macd_decide,
    "rsi": rsi_decide,
    "bollinger": bollinger_decide,
    "momentum": momentum_decide,
    "reversal": reversal_decide,
}


def llm_decide_factory(llm=None) -> Callable[[list[Bar]], DecideResult]:
    """构造一个 LLM 决策器。每根 K 线点都喂一份摘要给 LLM。
    使用全局 get_llm()（DeepSeek/Stub 自动降级），
    传入 llm=可注入测试桩。
    """
    import json as _json
    from .llm import get_llm
    _llm = llm or get_llm()

    def _decide(bars: list[Bar]) -> DecideResult:
        if len(bars) < 5:
            return ("hold", 0.5, "K 线不足")
        recent = bars[-10:]
        closes = [b.close for b in bars]
        sma5 = _sma(closes, 5)
        sma20 = _sma(closes, 20)
        today = bars[-1]
        recent_str = "\n".join(
            f"  {b.date}  O{b.open:.2f} H{b.high:.2f} L{b.low:.2f} C{b.close:.2f} V{b.volume:.0f}"
            for b in recent
        )
        prompt = (
            f"你是一名量化交易员，基于历史 K 线给出当日决策。\n\n"
            f"【最近 10 根日 K 线】\n{recent_str}\n\n"
            f"【技术指标】SMA5={sma5}, SMA20={sma20}, 当日收盘={today.close:.2f}\n\n"
            f"【输出】严格 JSON：{{\"action\":\"buy|sell|hold\",\"confidence\":0.0-1.0,\"reason\":\"30字内\"}}\n"
            f"不要 markdown 代码块。"
        )
        try:
            reply = _llm.chat(prompt, system="只返回 JSON，不要客套。").strip()
            if reply.startswith("```"):
                reply = reply.strip("`")
                if reply.lower().startswith("json"):
                    reply = reply[4:]
            l, r = reply.find("{"), reply.rfind("}")
            obj = _json.loads(reply[l : r + 1]) if l >= 0 else {}
            act = str(obj.get("action", "hold")).lower()
            if act not in ("buy", "sell", "hold"):
                act = "hold"
            conf = max(0.0, min(1.0, float(obj.get("confidence", 0.5))))
            reason = str(obj.get("reason", ""))[:80]
            return (act, conf, reason)
        except Exception as e:
            return ("hold", 0.3, f"LLM 解析失败: {type(e).__name__}")

    return _decide


# ---------- 主回测循环 ----------
def run_backtest(
    symbol: str,
    days: int = 90,
    horizon: int = 5,
    decide_fn: Callable[[list[Bar]], DecideResult] | None = None,
    strategy: str = "ma_cross",
) -> BacktestResult:
    """对 symbol 跑一次历史回测。

    Args:
        symbol: 600519 / 茅台 / 0700.HK / NVDA 都行
        days: 抓多少根日 K 线
        horizon: 决策后第几根 K 线收盘价作为出场点
        decide_fn: 决策函数，签名 (bars_so_far) -> (action, confidence, reason)
                   若为 None，按 strategy 选默认实现
        strategy: ma_cross | llm（仅当 decide_fn 为 None 时生效）
    """
    if decide_fn is None:
        if strategy == "llm":
            decide_fn = llm_decide_factory()
        elif strategy in STRATEGIES:
            decide_fn = STRATEGIES[strategy]
        else:
            decide_fn = default_decide

    market, normalized = detect_market(symbol)
    bars = load_history(symbol, days=days)
    if not bars:
        return BacktestResult(
            symbol=normalized, market=market.value,
            bars=0, horizon=horizon, strategy=strategy, trades=[],
        )

    trades: list[Trade] = []
    last_decision_idx = len(bars) - 1 - horizon
    for i in range(len(bars)):
        if i > last_decision_idx:
            break
        sub = bars[: i + 1]
        action, conf, reason = decide_fn(sub)
        entry = bars[i]
        exit_bar = bars[i + horizon]
        if action == "buy":
            pnl = (exit_bar.close - entry.close) / entry.close
        elif action == "sell":
            pnl = (entry.close - exit_bar.close) / entry.close
        else:
            pnl = 0.0
        trades.append(Trade(
            date=entry.date, action=action, price=entry.close,
            horizon_days=horizon,
            exit_date=exit_bar.date, exit_price=exit_bar.close,
            pnl_pct=pnl, confidence=conf, reason=reason,
        ))

    return BacktestResult(
        symbol=normalized, market=market.value,
        bars=len(bars), horizon=horizon, strategy=strategy, trades=trades,
        bar_dates=[b.date for b in bars],
        bar_closes=[b.close for b in bars],
    )


def render_backtest(r: BacktestResult) -> str:
    """格式化输出。"""
    if r.bars == 0:
        return f"❌ 未拉到 {r.symbol} 的历史 K 线（数据源可能限速或代码无效）"
    lines = []
    lines.append(f"📈 回测 · {r.symbol} ({r.market})  策略：{r.strategy}")
    lines.append("─" * 64)
    lines.append(f"K 线天数：{r.bars}    持有窗口：{r.horizon} 个交易日")
    lines.append(
        f"信号分布：buy {r.n_buy}  sell {r.n_sell}  hold {r.n_hold}    "
        f"实际入场 {len(r.actionable)} 笔"
    )
    if r.actionable:
        lines.append(
            f"胜率：{r.win_rate*100:.1f}%    "
            f"平均单笔：{r.avg_pnl*100:+.2f}%    "
            f"累计：{r.total_pnl*100:+.2f}%"
        )
        lines.append(
            f"最大回撤：{r.max_drawdown*100:.2f}%    "
            f"年化夏普(粗算)：{r.sharpe:.2f}"
        )
        lines.append(
            f"Calmar：{r.calmar:.2f}    "
            f"Sortino：{r.sortino:.2f}    "
            f"信息比：{r.info_ratio:.2f}"
        )
        lines.append(
            f"最长连胜：{r.max_win_streak} 笔    "
            f"最长连败：{r.max_loss_streak} 笔    "
            f"盈亏比：{r.win_loss_ratio:.2f}"
        )
        # 基准对比
        bh = r.buy_hold_return
        alpha = r.alpha_vs_bh
        win = "✅ 跑赢" if alpha > 0 else "❌ 跑输"
        lines.append(
            f"基准 buy & hold：{bh*100:+.2f}%    "
            f"策略 alpha：{alpha*100:+.2f}%   {win}"
        )
        if r.best:
            lines.append(
                f"最好一笔：{r.best.date} {r.best.action} "
                f"@ {r.best.price:.2f} → {r.best.exit_date} {r.best.exit_price:.2f}  "
                f"({r.best.pnl_pct*100:+.2f}%)"
            )
        if r.worst:
            lines.append(
                f"最差一笔：{r.worst.date} {r.worst.action} "
                f"@ {r.worst.price:.2f} → {r.worst.exit_date} {r.worst.exit_price:.2f}  "
                f"({r.worst.pnl_pct*100:+.2f}%)"
            )
        lines.append("─" * 64)
        lines.append("最近 5 笔交易：")
        for t in [x for x in r.trades if x.action in ("buy", "sell")][-5:]:
            tail = f"  ({t.reason})" if t.reason else ""
            lines.append(
                f"  {t.date}  {t.action:<4} @ {t.price:>8.2f}  →  "
                f"{t.exit_date}  {t.exit_price:>8.2f}   "
                f"{t.pnl_pct*100:+.2f}%{tail}"
            )
    else:
        lines.append("（窗口内未触发任何 buy/sell 信号——可考虑放宽阈值或拉长 days）")
    return "\n".join(lines)


def to_dict(r: BacktestResult) -> dict:
    return {
        "symbol": r.symbol,
        "market": r.market,
        "strategy": r.strategy,
        "bars": r.bars,
        "horizon": r.horizon,
        "n_buy": r.n_buy,
        "n_sell": r.n_sell,
        "n_hold": r.n_hold,
        "actionable": len(r.actionable),
        "win_rate": r.win_rate,
        "avg_pnl": r.avg_pnl,
        "total_pnl": r.total_pnl,
        "buy_hold_return": r.buy_hold_return,
        "alpha_vs_bh": r.alpha_vs_bh,
        "max_drawdown": r.max_drawdown,
        "sharpe": r.sharpe,
        "calmar": r.calmar,
        "sortino": r.sortino,
        "info_ratio": r.info_ratio,
        "max_win_streak": r.max_win_streak,
        "max_loss_streak": r.max_loss_streak,
        "win_loss_ratio": r.win_loss_ratio,
        "trades": [t.__dict__ for t in r.trades],
    }
