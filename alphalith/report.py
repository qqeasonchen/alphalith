"""
TradingAgents-style comprehensive report renderer — Alphalith v0.4.1.

Produces a full multi-section Markdown report matching the TradingAgents format:
  I.   Analyst Team Reports (4 analysts with full Markdown)
  II.  Research Team Decision (Bull/Bear debate + Research Manager)
  III. Trading Team Plan (Trader)
  IV.  Risk Management Team Decision (Aggressive/Conservative/Neutral)
  V.   Portfolio Manager Decision (Final ruling)
"""
from __future__ import annotations

from datetime import datetime

from .schema import Decision

CURRENCY_SYM = {"CNY": "¥", "HKD": "HK$", "USD": "$"}
MARKET_FLAG = {"a_stock": "🇨🇳", "hk_stock": "🇭🇰", "us_stock": "🇺🇸"}
ACTION_LABEL = {"buy": "买入", "sell": "卖出", "hold": "持有"}


def render(d: Decision) -> str:
    """Generate a complete TradingAgents-style comprehensive report."""
    cur = CURRENCY_SYM.get(d.currency, "")
    flag = MARKET_FLAG.get(d.market.value, "")
    symbol_info = f"{flag} {d.symbol}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    parts: list[str] = []

    # ── Header ──
    parts.append(f"# Trading Analysis Report: {d.symbol}")
    parts.append(f"")
    parts.append(f"Generated: {now}  |  Decision ID: {d.id}")
    parts.append(f"Data Source: {d.extra.get('data_source', '-')}  |  Depth: {d.extra.get('depth', '-')}")
    parts.append(f"")

    # ── I. Analyst Team Reports ──
    parts.append("## I. Analyst Team Reports")
    parts.append("")
    for r in d.agent_reports:
        parts.append(f"### {r.name}")
        parts.append("")
        if r.full_text:
            # Use the full markdown report directly
            parts.append(r.full_text)
        else:
            stance_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(r.stance, "⚪")
            parts.append(f"{stance_emoji} **{r.stance.upper()}** (Confidence: {r.confidence:.0%})")
            parts.append(f"")
            parts.append(r.summary)
        parts.append("")
        parts.append("---")
        parts.append("")

    # ── II. Research Team Decision ──
    parts.append("## II. Research Team Decision")
    parts.append("")

    # Bull Researcher
    parts.append("### Bull Researcher")
    parts.append("")
    if d.debate:
        for i, dbt in enumerate(d.debate, 1):
            parts.append(f"**Round {i}**")
            parts.append("")
            parts.append(dbt.bull)
            parts.append("")
    else:
        parts.append("*(No debate rounds)*")
    parts.append("")

    # Bear Researcher
    parts.append("### Bear Researcher")
    parts.append("")
    if d.debate:
        for i, dbt in enumerate(d.debate, 1):
            parts.append(f"**Round {i}**")
            parts.append("")
            parts.append(dbt.bear)
            parts.append("")
    else:
        parts.append("*(No debate rounds)*")
    parts.append("")

    # Research Manager
    parts.append("### Research Manager")
    parts.append("")
    if d.manager_report.full_text:
        parts.append(d.manager_report.full_text)
    else:
        stance_label = {"bullish": "🟢 BULLISH", "bearish": "🔴 BEARISH", "neutral": "⚪ NEUTRAL"}.get(d.manager_report.stance, "NEUTRAL")
        parts.append(f"**{stance_label}** (Confidence: {d.manager_report.confidence:.0%})")
        parts.append("")
        parts.append(d.manager_report.summary)
    parts.append("")
    parts.append("---")
    parts.append("")

    # ── III. Trading Team Plan ──
    parts.append("## III. Trading Team Plan")
    parts.append("")
    parts.append("### Trader")
    parts.append("")
    if d.trader_report.full_text:
        parts.append(d.trader_report.full_text)
    else:
        act_label = ACTION_LABEL.get(d.trader_report.action, d.trader_report.action).upper()
        parts.append(f"FINAL TRANSACTION PROPOSAL: **{act_label}**")
        parts.append("")
        parts.append(d.trader_report.reasoning)
    parts.append("")
    parts.append("---")
    parts.append("")

    # ── IV. Risk Management Team Decision ──
    parts.append("## IV. Risk Management Team Decision")
    parts.append("")

    if d.risk_reviews:
        rr = d.risk_reviews[0]

        parts.append("### Aggressive Analyst")
        parts.append("")
        parts.append(rr.aggressive_full or rr.aggressive or "")
        parts.append("")
        parts.append("---")
        parts.append("")

        parts.append("### Conservative Analyst")
        parts.append("")
        parts.append(rr.conservative_full or rr.conservative or "")
        parts.append("")
        parts.append("---")
        parts.append("")

        parts.append("### Neutral Analyst")
        parts.append("")
        parts.append(rr.neutral_full or rr.neutral or "")
        parts.append("")
        parts.append("---")
        parts.append("")
    else:
        parts.append("*(No risk reviews)*")
        parts.append("")
        parts.append("---")
        parts.append("")

    # ── V. Portfolio Manager Decision ──
    parts.append("## V. Portfolio Manager Decision")
    parts.append("")
    parts.append("### Portfolio Manager")
    parts.append("")

    if d.risk_reviews and d.risk_reviews[0].fund_manager_full:
        parts.append(d.risk_reviews[0].fund_manager_full)
    else:
        action_label = ACTION_LABEL.get(d.action, d.action)
        parts.append(f"## 最终交易决策：{symbol_info}")
        parts.append("")
        parts.append(f"**决策：{action_label}** | 置信度：{d.confidence:.0%} | 仓位：{d.suggested_shares} 股")
        parts.append(f"入场：{cur}{d.entry_price:.2f} | 止损：{cur}{d.stop_loss:.2f} | 止盈：{cur}{d.take_profit:.2f}")
        parts.append("")
        parts.append(d.reasoning)
    parts.append("")

    # ── Fees & Market Warnings ──
    parts.append("---")
    parts.append("")
    parts.append("### 费用明细")
    f = d.fees
    parts.append(f"| 项目 | 金额 |")
    parts.append(f"|------|------|")
    parts.append(f"| 佣金 | {cur}{f.commission:.2f} |")
    parts.append(f"| 印花税 | {cur}{f.stamp_tax:.2f} |")
    parts.append(f"| 过户费 | {cur}{f.transfer_fee:.4f} |")
    parts.append(f"| SEC 费 | {cur}{f.sec_fee:.4f} |")
    for k, v in (f.other or {}).items():
        parts.append(f"| {k} | {cur}{v:.4f} |")
    parts.append(f"| **合计** | **{cur}{f.total:.2f}** (盈亏平衡 +{f.breakeven_pct:.3f}%) |")
    parts.append("")

    if d.market_warnings:
        parts.append("### 市场规则提示")
        for w in d.market_warnings:
            parts.append(f"- {w}")
        parts.append("")

    # ── Meta ──
    parts.append("---")
    parts.append("")
    calls = d.extra.get("llm_calls")
    in_t = d.extra.get("llm_prompt_tokens", 0)
    out_t = d.extra.get("llm_completion_tokens", 0)
    total_t = d.extra.get("llm_total_tokens", 0)
    parts.append(f"*Report generated by Alphalith v0.4.1 | LLM: {d.extra.get('llm', '-')} | "
                f"Calls: {calls} | Tokens: in={in_t} out={out_t} total={total_t}*")
    parts.append("")
    parts.append("📜 决策已封存于立石（Sealed in the Bedrock）")

    return "\n".join(parts)


def render_text(d: Decision) -> str:
    """Legacy compact text renderer for CLI output."""
    cur = CURRENCY_SYM.get(d.currency, "")
    flag = MARKET_FLAG.get(d.market.value, "")
    lines = []
    lines.append("━" * 56)
    lines.append("🪨 Alphalith · 慧投 — AI 投研委员会决策报告")
    lines.append("━" * 56)
    lines.append(f"标的：{flag} {d.symbol}    决策ID：{d.id}")
    lines.append(f"决策：{ACTION_LABEL[d.action]}    置信度：{d.confidence:.0%}")
    lines.append(
        f"建议：{d.suggested_shares} 股 × {cur}{d.entry_price:.2f}"
        f" = {cur}{d.suggested_shares * d.entry_price:,.2f}"
    )
    lines.append(f"止损：{cur}{d.stop_loss:.2f}    止盈：{cur}{d.take_profit:.2f}")
    lines.append("")
    lines.append("【分析师团队】")
    for r in d.agent_reports:
        stance_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}[r.stance]
        lines.append(f"  {stance_emoji} {r.name:<10} ({r.confidence:.0%}) {r.summary}")
    if d.debate:
        lines.append("")
        lines.append("【多空辩论】")
        for i, dbt in enumerate(d.debate, 1):
            lines.append(f"  Round {i}")
            lines.append(f"    🐂 看多：{dbt.bull[:200]}")
            lines.append(f"    🐻 看空：{dbt.bear[:200]}")
    lines.append("")
    lines.append(f"🛡 风控审议：{d.risk_review}")
    lines.append("━" * 56)
    lines.append("📜 决策已封存于立石（Sealed in the Bedrock）")
    return "\n".join(lines)
