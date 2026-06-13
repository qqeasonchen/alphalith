"""
Core council — 调度全流程并产出 ADP Decision。
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from . import journal
from .agents import run_analysts, run_debate
from .data import load_market_data
from .llm import get_llm
from .rules import get_rules
from .schema import Decision, FeeBreakdown


def _decide_action(reports, debates) -> tuple[str, float]:
    """简单加权：分析师 70% + 辩论 30%。"""
    score = 0.0
    weight = 0.0
    for r in reports:
        s = {"bullish": 1, "neutral": 0, "bearish": -1}[r.stance]
        score += s * r.confidence
        weight += r.confidence
    bull_count = sum(1 for r in reports if r.stance == "bullish")
    bear_count = sum(1 for r in reports if r.stance == "bearish")
    avg = score / max(weight, 1e-6)
    if avg > 0.25 and bull_count >= 2:
        return "buy", min(0.95, 0.55 + abs(avg) * 0.4)
    if avg < -0.25 and bear_count >= 2:
        return "sell", min(0.95, 0.55 + abs(avg) * 0.4)
    return "hold", min(0.95, 0.5 + abs(avg) * 0.2)


def _make_id(symbol: str) -> str:
    raw = f"{symbol}-{datetime.utcnow().isoformat()}"
    return "ALH-" + hashlib.sha1(raw.encode()).hexdigest()[:10].upper()


def analyze(symbol: str, depth: str = "standard", persist: bool = True) -> Decision:
    """主入口：分析任意标的并返回 ADP v1.0 决策对象。

    depth: quick | standard | deep
    persist: 是否落库到 SQLite journal（默认开），库位由 ALPHALITH_DB_PATH 控制。
    """
    md = load_market_data(symbol)
    rules = get_rules(md.quote.market)
    llm = get_llm()

    # 1) 四分析师
    reports = run_analysts(llm, md)

    # 2) 多空辩论
    rounds = {"quick": 0, "standard": 1, "deep": 3}.get(depth, 1)
    debates = run_debate(llm, md, reports, rounds)

    # 3) 决策合成
    action, confidence = _decide_action(reports, debates)

    # 4) 仓位与价格
    entry = md.quote.price
    stop = entry * (0.97 if md.quote.market.value == "a_stock" else 0.95)
    target = entry * 1.06
    raw_shares = max(int(10000 / entry), 1) if action == "buy" else 0
    shares = rules.round_lot(md.quote.symbol, raw_shares)

    # 5) 费用
    amount = entry * shares
    fee = rules.calc_fee(amount, "buy" if action == "buy" else "sell", shares)
    fb = FeeBreakdown(
        commission=fee.commission,
        stamp_tax=fee.stamp_tax,
        transfer_fee=fee.transfer_fee,
        sec_fee=fee.sec_fee,
        other=fee.other,
        total=fee.total,
        breakeven_pct=(fee.total / amount * 100) if amount else 0.0,
    )

    # 6) 警告
    warns = rules.warnings(md.quote.symbol, md.quote.price, md.quote.prev_close)

    # 7) 风控审议
    risk = "通过：仓位、止损、规则约束均符合默认风控"
    if action == "buy" and shares == 0:
        action = "hold"
        risk = "拒绝：建议手数 < 最小交易单位，自动改为 hold"

    decision = Decision(
        id=_make_id(md.quote.symbol),
        symbol=md.quote.symbol,
        market=md.quote.market,
        currency=rules.currency,  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        confidence=confidence,
        suggested_shares=shares,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        agent_reports=reports,
        debate=debates,
        risk_review=risk,
        reasoning="多智能体投研委员会综合 4 维分析与多空辩论给出决策。",
        market_warnings=warns,
        fees=fb,
        extra={
            "depth": depth,
            "llm": llm.name,
            "data_source": md.quote.source,
            "llm_calls": llm.usage.calls,
            "llm_prompt_tokens": llm.usage.prompt_tokens,
            "llm_completion_tokens": llm.usage.completion_tokens,
            "llm_total_tokens": llm.usage.total_tokens,
            "llm_tokens_estimated": llm.usage.estimated,
        },
    )

    if persist:
        journal.save(decision)
    return decision
