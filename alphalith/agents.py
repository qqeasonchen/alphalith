"""
7-role 12-node analyst committee — Alphalith v0.4.0.
7种角色、12个运行时节点：

  Layer I   (4 节点)  技术 / 基本面 / 新闻 / 情绪分析师
  Layer II  (2 节点)  多头研究员 + 空头研究员（多轮辩论）
  Layer III (1 节点)  研究经理 → 汇总辩论、输出平衡报告
  Layer IV  (1 节点)  交易员 → 独立决策（买卖/仓位/时机）
  Layer V   (2 节点)  激进风控 + 保守风控 → 双视角审议
  Layer VI  (1 节点)  基金经理 → 最终审批/否决
"""
from __future__ import annotations

import json as _json
import re

from .data import MarketData
from .llm import LLM
from .schema import AgentReport, DebateRound, ManagerReport, TraderReport, RiskReview


# ═══════════════════════════════════════════════════════════════
# 快照
# ═══════════════════════════════════════════════════════════════

def make_snapshot(md: MarketData) -> str:
    """完整市场快照——全层共享。"""
    q = md.quote
    news = "\n".join(f"  - {h}" for h in md.news_headlines[:8]) or "  - （暂无）"
    return (
        f"标的：{q.name or q.symbol} ({q.symbol})  市场：{q.market.value}\n"
        f"行情：现价 {q.price:.2f} {getattr(q,'currency','')}，"
        f"昨收 {q.prev_close:.2f}，涨跌 {q.change_pct:+.2f}%，成交量 {q.volume:,.0f}\n"
        f"技术：{md.history_summary}\n"
        f"基本面：{md.fundamental_note}\n"
        f"情绪：{md.sentiment_note}\n"
        f"新闻头条（{len(md.news_headlines)} 条）：\n{news}\n"
        f"数据源：{md.sources}"
    )


# ═══════════════════════════════════════════════════════════════
# Layer I — 4 分析师
# ═══════════════════════════════════════════════════════════════

_ANALYST_PROMPT = """你是一名{role}，正在为投研委员会提供 {symbol} 的分析。

【市场快照（全员共享）】
{snapshot}

【你的职责】
{focus}

【输出格式】严格输出一个 JSON 对象，不要 markdown 代码块，不要任何前后缀文字：
{{"stance": "bullish|bearish|neutral", "confidence": 0.0-1.0, "summary": "80字内，必须引用快照中的具体数字或新闻原文"}}
"""

_FOCUS = {
    "技术分析师": (
        "聚焦价格行为：当前价相对昨收的位置、距涨跌停空间、量价配合。"
        "如果快照里有具体价位（如 1300 关口、52 周高/低），必须引用。"
    ),
    "基本面分析师": (
        "聚焦估值与质地：PE/PB/ROE/市值。"
        "对比历史区间或同行常识，给出'低估/合理/高估'判断。"
        "如果快照里没有基本面数字，必须明确说'基本面数据缺失'，不要瞎编。"
    ),
    "新闻分析师": (
        "聚焦新闻头条对短期股价的指向。"
        "必须引用至少一条头条原文（用引号），并判断利好/利空/中性。"
        "如果新闻全部是'暂无新闻流'，必须明说'当前无新闻信号'，不要假装看到了什么。"
    ),
    "情绪分析师": (
        "聚焦市场情绪与资金流。结合涨跌幅、成交量、新闻倾向综合判断热度。"
        "情绪与价格背离时要明确指出。"
    ),
}

_FOCUS_KEYS = list(_FOCUS.keys())


def _parse_analyst(reply: str, name: str) -> AgentReport:
    """优先 JSON 解析，失败时降级到正则。"""
    stance = "neutral"
    conf = 0.6
    summary = reply.strip()
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    l, r = text.find("{"), text.rfind("}")
    if l >= 0 and r > l:
        try:
            obj = _json.loads(text[l: r + 1])
            st = str(obj.get("stance", "")).lower()
            if st in ("bullish", "bearish", "neutral"):
                stance = st
            conf = max(0.0, min(1.0, float(obj.get("confidence", 0.6))))
            summary = str(obj.get("summary", summary))[:200]
            return AgentReport(name=name, stance=stance, confidence=conf, summary=summary)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            pass
    stance_map = {"看多": "bullish", "看空": "bearish", "中性": "neutral"}
    for k, v in stance_map.items():
        if k in reply:
            stance = v
            break
    m = re.search(r"置信度[：: ]*([0-9.]+)", reply)
    if m:
        try:
            conf = max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            pass
    m2 = re.search(r"摘要[：:]\s*(.+)", reply, flags=re.S)
    if m2:
        summary = m2.group(1).strip().split("\n")[0][:200]
    return AgentReport(name=name, stance=stance, confidence=conf, summary=summary)  # type: ignore[arg-type]


def run_analysts(llm: LLM, md: MarketData) -> list[AgentReport]:
    """Layer I: 4 分析师并行（串行调用，独立降级）。"""
    snapshot = make_snapshot(md)
    reports: list[AgentReport] = []
    for role, focus in _FOCUS.items():
        prompt = _ANALYST_PROMPT.format(
            role=role, symbol=md.quote.symbol, snapshot=snapshot, focus=focus
        )
        try:
            reply = llm.chat(
                prompt,
                system="你是严谨、可量化的金融分析师。只引用快照中的事实，禁止虚构数字。",
            )
            reports.append(_parse_analyst(reply, role))
        except Exception as e:
            reports.append(AgentReport(
                name=role,
                stance="neutral",
                confidence=0.5,
                summary=f"调用失败({e.__class__.__name__})，已降级为中性。",
            ))
    return reports


# ═══════════════════════════════════════════════════════════════
# Layer II — 多头/空头研究员（辩论）
# ═══════════════════════════════════════════════════════════════

def run_debate(
    llm: LLM, md: MarketData, reports: list[AgentReport], rounds: int = 1
) -> list[DebateRound]:
    """Layer II: 多头研究员 vs 空头研究员，N 轮辩论。"""
    if rounds <= 0:
        return []
    snapshot = make_snapshot(md)
    summary = "\n".join(
        f"- {r.name}：{r.stance} ({r.confidence:.0%}) {r.summary}" for r in reports
    )
    debates: list[DebateRound] = []
    last_bull = ""
    last_bear = ""
    for i in range(rounds):
        rebuttal_hint = ""
        if i > 0:
            rebuttal_hint = f"\n上一轮对手观点（请针对性反驳）：{last_bear or last_bull}\n"
        try:
            bull = llm.chat(
                f"你是「多头研究员」。\n\n【市场快照】\n{snapshot}\n\n"
                f"【4 位分析师结论】\n{summary}\n{rebuttal_hint}\n"
                f"请给出 80 字以内的看多论点，必须引用快照中的具体数字或新闻原文。",
                system="只输出论点本身，不要前缀，不要客套。",
            )
            last_bull = bull.strip()
        except Exception as e:
            last_bull = f"看多方调用失败({e.__class__.__name__})"
        try:
            bear = llm.chat(
                f"你是「空头研究员」。\n\n【市场快照】\n{snapshot}\n\n"
                f"【4 位分析师结论】\n{summary}\n"
                f"\n上一轮对手观点（请针对性反驳）：{last_bull}\n"
                f"请给出 80 字以内的看空论点，必须引用快照中的具体数字或新闻原文。",
                system="只输出论点本身，不要前缀，不要客套。",
            )
            last_bear = bear.strip()
        except Exception as e:
            last_bear = f"看空方调用失败({e.__class__.__name__})"
        debates.append(
            DebateRound(bull=last_bull[:300], bear=last_bear[:300])
        )
    return debates


# ═══════════════════════════════════════════════════════════════
# Layer III — 研究经理
# ═══════════════════════════════════════════════════════════════

_RESEARCH_MANAGER_SYS = (
    "你是资深研究经理，职责是汇总多方观点、识别逻辑矛盾、输出平衡的综合报告。"
    "你会指出多头论点的薄弱环节，也会质疑空头是否有遗漏的利好。"
    "最终给出对交易员有价值的指导性结论。"
)

_RESEARCH_MANAGER_PROMPT = """【你的任务】作为研究经理，汇总以下所有输入，输出一份结构化的投研综合报告。

【市场快照】
{snapshot}

【4 位分析师独立报告】
{analyst_summary}

【多空研究员辩论记录】
{debate_log}

请输出 JSON：
{{
  "summary": "200字内综合判断，指出多空双方最有力的论据，并给出倾向性",
  "stance": "bullish|bearish|neutral",
  "confidence": 0.0-1.0,
  "key_points": ["要点1", "要点2", "要点3"]
}}"""


def run_research_manager(
    llm: LLM, md: MarketData, reports: list[AgentReport], debates: list[DebateRound]
) -> ManagerReport:
    """Layer III: 研究经理——汇总辩论，产出平衡分析。"""
    snapshot = make_snapshot(md)
    analyst_summary = "\n".join(
        f"- {r.name} [{r.stance}, conf={r.confidence:.0%}]: {r.summary}" for r in reports
    )
    debate_log = "\n".join(
        f"轮次 {i+1}:\n  多头: {d.bull[:200]}\n  空头: {d.bear[:200]}\n"
        for i, d in enumerate(debates)
    ) or "（无辩论）"

    prompt = _RESEARCH_MANAGER_PROMPT.format(
        snapshot=snapshot, analyst_summary=analyst_summary, debate_log=debate_log
    )
    try:
        reply = llm.chat(prompt, system=_RESEARCH_MANAGER_SYS)
    except Exception as e:
        return ManagerReport(
            summary=f"研究经理调用失败({e.__class__.__name__})",
            stance="neutral", confidence=0.5,
        )

    try:
        text = reply.strip()
        l, r = text.find("{"), text.rfind("}")
        if l >= 0 and r > l:
            obj = _json.loads(text[l: r + 1])
            st = str(obj.get("stance", "neutral")).lower()
            if st not in ("bullish", "bearish", "neutral"):
                st = "neutral"
            return ManagerReport(
                summary=str(obj.get("summary", reply[:300])),
                stance=st,  # type: ignore[arg-type]
                confidence=max(0.0, min(1.0, float(obj.get("confidence", 0.5)))),
                key_points=[str(k) for k in obj.get("key_points", [])[:5]],
            )
    except Exception:
        pass
    return ManagerReport(summary=reply[:300], stance="neutral", confidence=0.5)


# ═══════════════════════════════════════════════════════════════
# Layer IV — 交易员
# ═══════════════════════════════════════════════════════════════

_TRADER_SYS = (
    "你是独立交易员，不盲从研究报告。你会综合考虑：技术面时机、仓位管理、"
    "风险收益比、流动性。你的决策可以有别于研究结论——"
    "如果研究报告极度看多但估值过高，你可以选择观望或轻仓。"
)

_TRADER_PROMPT = """【你的任务】阅读研究经理报告，独立决定操作。

【研究经理综合报告】
{manager_summary}

【原始快照（不要忽略市场实际情况）】
{snapshot}

请输出 JSON：
{{
  "action": "buy|sell|hold",
  "confidence": 0.0-1.0,
  "position_pct": 0.0-1.0,
  "entry_strategy": "入场策略（限价/市价/分批/观望等）",
  "reasoning": "150字内，说明为什么采用此操作，是否与研究结论有分歧"
}}"""


def run_trader(
    llm: LLM, md: MarketData, manager: ManagerReport
) -> TraderReport:
    """Layer IV: 交易员——独立决策（买卖/仓位/时机）。"""
    snapshot = make_snapshot(md)
    prompt = _TRADER_PROMPT.format(manager_summary=manager.summary, snapshot=snapshot)
    try:
        reply = llm.chat(prompt, system=_TRADER_SYS)
    except Exception as e:
        return TraderReport(
            action="hold", confidence=0.0,
            reasoning=f"交易员调用失败({e.__class__.__name__})",
        )

    try:
        text = reply.strip()
        l, r = text.find("{"), text.rfind("}")
        if l >= 0 and r > l:
            obj = _json.loads(text[l: r + 1])
            act = str(obj.get("action", "hold")).lower()
            if act not in ("buy", "sell", "hold"):
                act = "hold"
            return TraderReport(
                action=act,  # type: ignore[arg-type]
                confidence=max(0.0, min(1.0, float(obj.get("confidence", 0.3)))),
                position_pct=max(0.0, min(1.0, float(obj.get("position_pct", 0.0)))),
                entry_strategy=str(obj.get("entry_strategy", "")),
                reasoning=str(obj.get("reasoning", reply[:200])),
            )
    except Exception:
        pass
    return TraderReport(action="hold", confidence=0.0, reasoning=reply[:200])


# ═══════════════════════════════════════════════════════════════
# Layer V — 双视角风控（激进 + 保守）
# ═══════════════════════════════════════════════════════════════

_AGGRESSIVE_RISK_SYS = (
    "你是激进型风控官。你偏好承担可控风险以追求更高收益，"
    "在仓位、止损、杠杆方面都比较宽容。但你不赌——你必须基于数据。"
)

_CONSERVATIVE_RISK_SYS = (
    "你是保守型风控官。你的首要目标是资产保全，宁可错过不可做错。"
    "你会严格审查仓位合理性、止损距离、流动性风险和黑天鹅可能。"
)

_RISK_PROMPT = """【你的任务】审议交易员的决策。

【市场快照】
{snapshot}

【研究经理报告】
{manager_summary}

【交易员决策】
操作: {action}  置信度: {confidence:.0%}  仓位: {position_pct:.0%}
入场策略: {entry_strategy}
交易员理由: {trader_reasoning}

请输出 JSON：
{{
  "verdict": "approve|reject|modify",
  "analysis": "200字内，从风险角度分析该决策是否合理",
  "modifications": "如果 modify，建议如何调整（仓位/止损/时机），否则留空"
}}"""


def run_risk_reviews(
    llm: LLM, md: MarketData, manager: ManagerReport, trader: TraderReport
) -> RiskReview:
    """Layer V: 激进 + 保守风控双视角审议。"""
    snapshot = make_snapshot(md)
    risk_prompt = _RISK_PROMPT.format(
        snapshot=snapshot,
        manager_summary=manager.summary,
        action=trader.action,
        confidence=trader.confidence,
        position_pct=trader.position_pct,
        entry_strategy=trader.entry_strategy,
        trader_reasoning=trader.reasoning,
    )

    # Aggressive
    agg_text = ""
    agg_stance = "approve"
    try:
        agg_reply = llm.chat(risk_prompt, system=_AGGRESSIVE_RISK_SYS)
        try:
            text = agg_reply.strip()
            l, r = text.find("{"), text.rfind("}")
            if l >= 0 and r > l:
                obj = _json.loads(text[l: r + 1])
                agg_text = str(obj.get("analysis", agg_reply[:200]))
                v = str(obj.get("verdict", "approve")).lower()
                if v in ("approve", "reject", "modify"):
                    agg_stance = v
                if v == "modify":
                    agg_text += f" | 建议: {obj.get('modifications', '')}"
        except Exception:
            agg_text = agg_reply[:200]
    except Exception as e:
        agg_text = f"激进风控调用失败({e.__class__.__name__})"

    # Conservative
    con_text = ""
    con_stance = "approve"
    try:
        con_reply = llm.chat(risk_prompt, system=_CONSERVATIVE_RISK_SYS)
        try:
            text = con_reply.strip()
            l, r = text.find("{"), text.rfind("}")
            if l >= 0 and r > l:
                obj = _json.loads(text[l: r + 1])
                con_text = str(obj.get("analysis", con_reply[:200]))
                v = str(obj.get("verdict", "approve")).lower()
                if v in ("approve", "reject", "modify"):
                    con_stance = v
                if v == "modify":
                    con_text += f" | 建议: {obj.get('modifications', '')}"
        except Exception:
            con_text = con_reply[:200]
    except Exception as e:
        con_text = f"保守风控调用失败({e.__class__.__name__})"

    return RiskReview(
        aggressive=agg_text,
        aggressive_stance=agg_stance,  # type: ignore[arg-type]
        conservative=con_text,
        conservative_stance=con_stance,  # type: ignore[arg-type]
    )


# ═══════════════════════════════════════════════════════════════
# Layer VI — 基金经理（最终审批）
# ═══════════════════════════════════════════════════════════════

_FUND_MANAGER_SYS = (
    "你是基金经理（Fund Manager），负责最终审批交易决策。"
    "你综合考虑研究、交易、风控三方意见，做出最终裁定。"
    "你可以批准(approve)、否决(reject)、或调整(modify)。"
    "你的决策是不可上诉的。"
)

_FUND_MANAGER_PROMPT = """【你的任务】做出最终审批/否决决定。

【市场快照】
{snapshot}

【研究经理报告】
{manager_summary}

【交易员决策】
操作: {action} | 置信度: {confidence:.0%} | 仓位: {position_pct:.0%}
策略: {entry_strategy}

【风控审议】
🟢 激进风控 [{agg_stance}]: {agg_text}
🔴 保守风控 [{con_stance}]: {con_text}

请输出 JSON：
{{
  "final_action": "buy|sell|hold",
  "final_confidence": 0.0-1.0,
  "final_position_pct": 0.0-1.0,
  "verdict": "approve|reject|modify",
  "reasoning": "200字内，说明为什么做出此决定，引用具体风控意见"
}}"""


def run_fund_manager(
    llm: LLM, md: MarketData,
    manager: ManagerReport, trader: TraderReport,
    risk: RiskReview,
) -> dict:
    """Layer VI: 基金经理——最终审批。返回 {action, confidence, position_pct, reasoning}。"""
    snapshot = make_snapshot(md)
    prompt = _FUND_MANAGER_PROMPT.format(
        snapshot=snapshot,
        manager_summary=manager.summary,
        action=trader.action,
        confidence=trader.confidence,
        position_pct=trader.position_pct,
        entry_strategy=trader.entry_strategy,
        agg_stance=risk.aggressive_stance,
        agg_text=risk.aggressive[:200],
        con_stance=risk.conservative_stance,
        con_text=risk.conservative[:200],
    )
    try:
        reply = llm.chat(prompt, system=_FUND_MANAGER_SYS)
    except Exception as e:
        # 降级：保守处理
        act = trader.action if trader.action != "buy" else "hold"
        return {
            "action": act, "confidence": 0.3,
            "position_pct": 0.0,
            "reasoning": f"基金经理调用失败({e.__class__.__name__})，降级保守处理。",
        }

    try:
        text = reply.strip()
        l, r = text.find("{"), text.rfind("}")
        if l >= 0 and r > l:
            obj = _json.loads(text[l: r + 1])
            act = str(obj.get("final_action", trader.action)).lower()
            if act not in ("buy", "sell", "hold"):
                act = "hold"
            return {
                "action": act,
                "confidence": max(0.0, min(1.0, float(obj.get("final_confidence", 0.3)))),
                "position_pct": max(0.0, min(1.0, float(obj.get("final_position_pct", 0.0)))),
                "reasoning": str(obj.get("reasoning", reply[:200])),
            }
    except Exception:
        pass

    act = trader.action if trader.action != "buy" else "hold"
    return {"action": act, "confidence": 0.3, "position_pct": 0.0, "reasoning": reply[:200]}
