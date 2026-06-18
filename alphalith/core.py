"""
Core council v0.4.1 — 7 层 13 节点投研流水线 并产出 ADP Decision。

  Layer I    (4 节点) 技术/基本面/新闻/情绪分析师
  Layer 1.5  (1 节点) 形势摘要 → 蒸馏分析师报告
  Layer II   (2 节点) 多头+空头研究员（辩论）
  Layer III  (1 节点) 研究经理 → 汇总平衡报告
  Layer IV   (1 节点) 交易员 → 独立决策
  Layer V    (3 节点) 激进+保守+中立风控 → 三视角审议
  Layer VI   (1 节点) 基金经理 → 最终审批

总计 13 次 LLM 调用（standard depth），deep 模式 15 次。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from . import journal
from .agents import (
    run_analysts,
    run_situation_summariser,
    run_debate,
    run_research_manager,
    run_trader,
    run_risk_reviews,
    run_fund_manager,
    make_snapshot,
    _FOCUS_KEYS,
)
from .data import load_market_data
from .llm import get_llm, LLM
from .rules import get_rules
from .schema import (
    Decision, FeeBreakdown, AgentReport, DebateRound,
    SituationSummary, ManagerReport, TraderReport, RiskReview,
)


def analyze(
    symbol: str,
    depth: str = "standard",
    persist: bool = True,
    progress_cb: callable = None,
) -> Decision:
    """主入口：7 层 13 节点全流程分析。

    Args:
        symbol: 标的代码
        depth: quick | standard | deep
        persist: 是否落库
        progress_cb: 进度回调 (step_name, step_index, total_steps) → None
    """
    md = load_market_data(symbol)
    rules = get_rules(md.quote.market)
    llm = get_llm()

    total_steps = 7
    step_idx = [0]  # mutable counter for closure

    def _step(name: str):
        step_idx[0] += 1
        if progress_cb:
            progress_cb(name, step_idx[0], total_steps)

    # ── Layer I: 4 分析师 ──
    _step("分析师团队分析")
    try:
        reports = run_analysts(llm, md)
    except Exception:
        reports = [
            AgentReport(name=role, stance="neutral", confidence=0.5,
                        summary="管道中断，分析师降级。")
            for role in _FOCUS_KEYS
        ]

    # ── Layer 1.5: 形势摘要 ──
    _step("形势摘要总结")
    try:
        situation = run_situation_summariser(llm, md, reports)
    except Exception:
        situation = SituationSummary(
            snapshot_text="形势摘要调用失败，降级使用原始分析师报告。",
            key_drivers=["摘要层异常"],
        )
    
    # 追加信号评分到快照（如果 A 股且有评分）
    if md.market == Market.A_STOCK and md.signal_score:
        situation.snapshot_text += "\n\n" + md.signal_score.to_markdown()

    # ── Layer II: 多空辩论 ──
    _step("多头空头研究员辩论")
    debate_rounds = {"quick": 0, "standard": 1, "deep": 3}.get(depth, 1)
    try:
        debates = run_debate(llm, md, reports, debate_rounds)
    except Exception:
        debates = []

    # ── Layer III: 研究经理 ──
    _step("研究经理汇总")
    try:
        manager = run_research_manager(llm, md, reports, debates)
    except Exception:
        manager = ManagerReport(
            summary="研究经理调用失败，降级为中性。",
            stance="neutral", confidence=0.5,
        )

    # ── Layer IV: 交易员 ──
    _step("交易员独立决策")
    try:
        trader = run_trader(llm, md, manager)
    except Exception:
        trader = TraderReport(
            action="hold", confidence=0.0,
            reasoning="交易员调用失败，降级 hold。",
        )

    # ── Layer V: 双视角风控 ──
    _step("双视角风控审议")
    try:
        risk = run_risk_reviews(llm, md, manager, trader)
    except Exception:
        risk = RiskReview(
            aggressive="激进风控失败", aggressive_stance="approve",
            conservative="保守风控失败", conservative_stance="approve",
        )

    # ── Layer VI: 基金经理 ──
    _step("基金经理最终审批")
    try:
        fm = run_fund_manager(llm, md, manager, trader, risk)
    except Exception:
        fm = {"action": "hold", "confidence": 0.3, "position_pct": 0.0,
              "reasoning": "基金经理调用失败，降级 hold。"}

    action = fm["action"]
    confidence = fm["confidence"]
    position_pct = fm["position_pct"]
    reasoning = fm["reasoning"]

    # ── 将经理理由写入 RiskReview ──
    risk.final_verdict = reasoning
    risk.fund_manager_full = fm.get("full_text", "")

    # ── 仓位与价格 ──
    entry = md.quote.price
    stop_loss_pct = 0.97 if md.quote.market.value == "a_stock" else 0.95
    stop = entry * stop_loss_pct
    target = entry * 1.06

    if action == "buy":
        raw_shares = max(int(10000 * position_pct / entry), 1)
    else:
        raw_shares = 0
    shares = rules.round_lot(md.quote.symbol, raw_shares)

    # ── 费用 ──
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

    # ── 警告 ──
    warns = rules.warnings(md.quote.symbol, md.quote.price, md.quote.prev_close)

    # ── 风控否决强制降级 ──
    if action == "buy" and shares == 0:
        action = "hold"
        reasoning += " | 风控否决：建议手数 < 最小交易单位。"
        risk.final_verdict = reasoning

    # ── 构建 Decision ──
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
        situation_summary=situation,
        debate=debates,
        manager_report=manager,
        trader_report=trader,
        risk_reviews=[risk],
        risk_review="",
        reasoning=reasoning,
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


def analyze_with_sse(
    symbol: str, depth: str = "standard", persist: bool = True
):
    """SSE generator: yield 每步进度 + 最终 Decision。
    供 GUI / 外部调用，以 SSE 流式方式输出分析进度。

    用法:
        for event in analyze_with_sse("AAPL"):
            if event["type"] == "progress":
                print(f"[{event['step']}/{event['total']}] {event['message']}")
            elif event["type"] == "result":
                decision = event["decision"]
    """
    md = load_market_data(symbol)
    rules = get_rules(md.quote.market)
    llm = get_llm()

    # 进度发射器
    def _yield_progress(total):
        step = [0]
        def cb(name, _, _2):
            step[0] += 1
        return cb

    total_steps = 7
    step_i = [0]

    def progress_emitter(name: str, idx: int, total: int):
        step_i[0] = idx

    # ── 依次执行并 yield 进度 ──

    # Layer I
    yield {"type": "progress", "step": 0, "total": total_steps,
           "message": "正在启动 4 位分析师..."}
    try:
        reports = run_analysts(llm, md)
    except Exception:
        reports = [
            AgentReport(name=role, stance="neutral", confidence=0.5,
                        summary="管道中断，降级。")
            for role in _FOCUS_KEYS
        ]
    yield {"type": "progress", "step": 1, "total": total_steps,
           "message": "✓ 4 位分析师完成",
           "analysts": [{"name": r.name, "stance": r.stance, "confidence": r.confidence,
                         "summary": r.summary} for r in reports]}

    # Layer 1.5
    yield {"type": "progress", "step": 1, "total": total_steps,
           "message": "蒸馏形势快照中..."}
    try:
        situation = run_situation_summariser(llm, md, reports)
    except Exception:
        situation = SituationSummary(
            snapshot_text="形势摘要降级",
            key_drivers=["管道异常"],
        )
    yield {"type": "progress", "step": 2, "total": total_steps,
           "message": "✓ 形势快照完成"}

    # Layer II
    yield {"type": "progress", "step": 2, "total": total_steps,
           "message": "多头 vs 空头研究员辩论中..."}
    debate_rounds = {"quick": 0, "standard": 1, "deep": 3}.get(depth, 1)
    try:
        debates = run_debate(llm, md, reports, debate_rounds)
    except Exception:
        debates = []
    yield {"type": "progress", "step": 3, "total": total_steps,
           "message": "✓ 多空辩论完成"}

    # Layer III
    yield {"type": "progress", "step": 3, "total": total_steps,
           "message": "研究经理汇总中..."}
    try:
        manager = run_research_manager(llm, md, reports, debates)
    except Exception:
        manager = ManagerReport(summary="研究经理降级", stance="neutral", confidence=0.5)
    yield {"type": "progress", "step": 4, "total": total_steps,
           "message": "✓ 研究经理报告完成"}

    # Layer IV
    yield {"type": "progress", "step": 4, "total": total_steps,
           "message": "交易员独立决策中..."}
    try:
        trader = run_trader(llm, md, manager)
    except Exception:
        trader = TraderReport(action="hold", confidence=0.0, reasoning="降级 hold")
    yield {"type": "progress", "step": 5, "total": total_steps,
           "message": f"✓ 交易员决策: {trader.action}"}

    # Layer V
    yield {"type": "progress", "step": 5, "total": total_steps,
           "message": "三视角风控审议中..."}
    try:
        risk = run_risk_reviews(llm, md, manager, trader)
    except Exception:
        risk = RiskReview(aggressive="降级", aggressive_stance="approve",
                        conservative="降级", conservative_stance="approve",
                        neutral="降级", neutral_stance="approve")
    yield {"type": "progress", "step": 6, "total": total_steps,
           "message": f"✓ 风控完成 (激进:{risk.aggressive_stance} / 保守:{risk.conservative_stance} / 中立:{risk.neutral_stance})"}

    # Layer VI
    yield {"type": "progress", "step": 6, "total": total_steps,
           "message": "基金经理最终审批..."}
    try:
        fm = run_fund_manager(llm, md, manager, trader, risk)
    except Exception:
        fm = {"action": "hold", "confidence": 0.3, "position_pct": 0.0,
              "reasoning": "降级 hold"}
    yield {"type": "progress", "step": 7, "total": total_steps,
           "message": f"✓ 基金经理裁定: {fm['action']}"}

    risk.final_verdict = fm["reasoning"]
    risk.fund_manager_full = fm.get("full_text", "")

    # Build final decision
    action = fm["action"]
    confidence = fm["confidence"]
    position_pct = fm["position_pct"]

    entry = md.quote.price
    stop = entry * (0.97 if md.quote.market.value == "a_stock" else 0.95)
    target = entry * 1.06
    if action == "buy":
        raw_shares = max(int(10000 * position_pct / entry), 1)
    else:
        raw_shares = 0
    shares = rules.round_lot(md.quote.symbol, raw_shares)
    amount = entry * shares
    fee = rules.calc_fee(amount, "buy" if action == "buy" else "sell", shares)
    fb = FeeBreakdown(
        commission=fee.commission, stamp_tax=fee.stamp_tax,
        transfer_fee=fee.transfer_fee, sec_fee=fee.sec_fee,
        other=fee.other, total=fee.total,
        breakeven_pct=(fee.total / amount * 100) if amount else 0.0,
    )
    warns = rules.warnings(md.quote.symbol, md.quote.price, md.quote.prev_close)

    if action == "buy" and shares == 0:
        action = "hold"
        risk.final_verdict += " | 风控否决：最小交易单位不满足"

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
        situation_summary=situation,
        debate=debates,
        manager_report=manager,
        trader_report=trader,
        risk_reviews=[risk],
        reasoning=fm["reasoning"],
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

    yield {"type": "result", "decision": decision}


def _make_id(symbol: str) -> str:
    raw = f"{symbol}-{datetime.utcnow().isoformat()}"
    return "ALH-" + hashlib.sha1(raw.encode()).hexdigest()[:10].upper()
