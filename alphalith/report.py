"""
Pretty printer for human-readable decision report.
"""
from __future__ import annotations

from .schema import Decision

CURRENCY_SYM = {"CNY": "¥", "HKD": "HK$", "USD": "$"}
MARKET_FLAG = {"a_stock": "🇨🇳", "hk_stock": "🇭🇰", "us_stock": "🇺🇸"}
ACTION_LABEL = {"buy": "买入", "sell": "卖出", "hold": "持有"}


def render(d: Decision) -> str:
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
            lines.append(f"    🐂 看多：{dbt.bull}")
            lines.append(f"    🐻 看空：{dbt.bear}")
    lines.append("")
    lines.append("【费用明细】")
    lines.append(f"  佣金：{cur}{d.fees.commission:.2f}    印花税：{cur}{d.fees.stamp_tax:.2f}")
    lines.append(
        f"  过户费：{cur}{d.fees.transfer_fee:.4f}    SEC 费：{cur}{d.fees.sec_fee:.4f}"
    )
    if d.fees.other:
        for k, v in d.fees.other.items():
            lines.append(f"  {k}：{cur}{v:.4f}")
    lines.append(f"  合计：{cur}{d.fees.total:.2f}    盈亏平衡：+{d.fees.breakeven_pct:.3f}%")
    lines.append("")
    lines.append("【市场规则提示】")
    for w in d.market_warnings:
        lines.append(f"  • {w}")
    lines.append("")
    lines.append(f"🛡 风控审议：{d.risk_review}")
    lines.append(
        f"🔧 LLM：{d.extra.get('llm', '-')}    "
        f"数据源：{d.extra.get('data_source', '-')}    "
        f"深度：{d.extra.get('depth', '-')}"
    )
    calls = d.extra.get("llm_calls")
    if calls is not None:
        in_t = d.extra.get("llm_prompt_tokens", 0)
        out_t = d.extra.get("llm_completion_tokens", 0)
        total = d.extra.get("llm_total_tokens", 0)
        est = " (估算)" if d.extra.get("llm_tokens_estimated") else ""
        lines.append(
            f"💬 调用：{calls} 次    in：{in_t} tok    out：{out_t} tok    "
            f"total：{total} tok{est}"
        )
    lines.append("━" * 56)
    lines.append("📜 决策已封存于立石（Sealed in the Bedrock）")
    return "\n".join(lines)
