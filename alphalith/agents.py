"""
7-layer 13-node analyst committee — Alphalith v0.4.1.
七层流水线、13 个运行时节点：

  Layer I    (4 节点)  技术 / 基本面 / 新闻 / 情绪分析师
  Layer 1.5  (1 节点)  形势摘要 → 蒸馏 4 分析师报告为结构化快照
  Layer II   (2 节点)  多头研究员 + 空头研究员（多轮辩论）
  Layer III  (1 节点)  研究经理 → 汇总辩论、输出平衡报告
  Layer IV   (1 节点)  交易员 → 独立决策（买卖/仓位/时机）
  Layer V    (3 节点)  激进风控 + 保守风控 + 中立风控 → 三视角审议
  Layer VI   (1 节点)  基金经理 → 最终审批/否决
"""
from __future__ import annotations

import json as _json
import re
from typing import Optional

from .data import MarketData
from .llm import LLM
from .schema import (
    AgentReport, DebateRound, SituationSummary,
    ManagerReport, TraderReport, RiskReview,
)

def _parse_json(text: str):
    """健壮解析 LLM 返回中的 JSON。

    依次尝试：
    1. 整段直接解析
    2. 去掉 markdown 代码块后解析
    3. 栈匹配找完整 {} 子串后解析
    返回 None 表示全部失败。
    """
    if not text:
        return None
    s = text.strip()

    # 1. 直接解析
    try:
        return _json.loads(s)
    except _json.JSONDecodeError:
        pass

    # 2. 去掉 markdown 代码块
    cleaned = re.sub(r"```(?:json)?\s*([\s\S]*?)\s*```", r"\1", s, flags=re.DOTALL).strip()
    try:
        return _json.loads(cleaned)
    except _json.JSONDecodeError:
        pass

    # 3. 栈匹配找第一个完整 {} 对象
    start = s.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(s[start:], start):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"' and not esc:
                in_str = not in_str
            if not in_str:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return _json.loads(s[start:i + 1])
                        except _json.JSONDecodeError:
                            # 回退到简单 l/r 查找
                            end = s.rfind("}", start)
                            if end > start:
                                try:
                                    return _json.loads(s[start:end + 1])
                                except _json.JSONDecodeError:
                                    pass
                        break
    return None


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

_ANALYST_PROMPT = """你是一名{role}，正在为投研委员会撰写 {symbol} 的深度分析报告。

【市场快照（全员共享，禁止虚构任何快照中没有的数据）】
{snapshot}

【你的职责与报告要求】
{focus}

【报告格式要求】请写出完整报告，用 Markdown 格式，至少 500 字，按以下结构：
- 报告标题
- 一、选用的指标/方法论与选择理由（如适用，用表格列出）
- 二、核心发现与分析（用表格呈现关键数据，给出深度解读）
- 三、综合判断与交易建议（列出看多/看空因素、关键水平位、策略建议）
- 风险提示（标注数据缺失或不确定的部分）
- 附录：关键指标数据表（如适用）

【⚠️ 最后一行必须单独一行，仅输出 JSON 元数据（仅用于解析 stance 和 confidence）】：
{{"stance": "bullish|bearish|neutral", "confidence": 0.0-1.0}}
"""

_FOCUS = {
    "技术分析师": (
        "撰写深度技术分析报告。聚焦：价格走势、均线系统(MA/EMA)、MACD、RSI、"
        "布林带、成交量与VWMA、ATR波动率。用表格呈现各指标近期变化趋势。"
        "必须引用快照中的具体数字。给出关键支撑/阻力位（至少各3个）。"
        "如果快照里数据不足，在报告中明确标注数据缺失处。"
    ),
    "基本面分析师": (
        "撰写基本面与估值分析报告。聚焦：PE/PB/ROE/市值等估值指标，"
        "对比历史区间或同行业基准，判断当前估值水位。"
        "分析公司基本面强弱，用表格列出关键财务指标（如有）。"
        "如果快照里没有基本面数字，必须在报告中明确说'⚠️ 基本面数据不可用'，"
        "并分析可能原因，但不得编造任何数字。"
    ),
    "新闻分析师": (
        "撰写新闻与事件驱动分析报告。必须逐一引用快照中的新闻头条原文，"
        "每一条新闻标注利好/利空/中性，并说明理由。"
        "分析宏观环境、行业趋势、地缘政治等会影响该标的的外部因素。"
        "用表格汇总关键新闻与影响评估。"
        "如果新闻全部是'暂无新闻流'，必须明确说明当前处于新闻真空期，不得编造。"
    ),
    "情绪分析师": (
        "撰写市场情绪与资金流分析报告。逐项分析各数据源（新闻/社交/搜索等）的"
        "情绪信号。检查跨源差异与一致性，判断是否存在市场共识或分化。"
        "识别主导叙事主题、潜在催化剂与风险。"
        "用表格汇总情绪信号方向、数据源、支持证据和权重。"
        "结合涨跌幅和成交量判断市场情绪热度，如有背离要明确指出。"
    ),
}

_FOCUS_KEYS = list(_FOCUS.keys())


def _parse_analyst(reply: str, name: str) -> AgentReport:
    """解析分析师报告：分离 Markdown 全文 + JSON 元数据。

    优先从文本最后一行提取 JSON 元数据（stance/confidence），
    其余内容作为 full_text 保存。失败时正则降级。
    """
    stance = "neutral"
    conf = 0.6
    full_text = reply.strip()
    summary = ""

    # ── 尝试从尾部提取 JSON 元数据 ──
    lines = full_text.rsplit("\n", 5)
    for i in range(len(lines) - 1, -1, -1):
        obj = _parse_json(lines[i].strip())
        if obj is not None and "stance" in obj:
            st = str(obj.get("stance", "")).lower()
            if st in ("bullish", "bearish", "neutral"):
                stance = st
            try:
                conf = max(0.0, min(1.0, float(obj.get("confidence", 0.6))))
            except (ValueError, TypeError):
                pass
            # 去掉 JSON 行，剩余为全文
            full_text = "\n".join(lines[:i]).strip()
            break

    # ── 如果尾部没有 JSON，尝试全局 JSON 解析 ──
    if full_text == reply.strip():
        obj = _parse_json(reply)
        if obj is not None and "stance" in obj:
            st = str(obj.get("stance", "")).lower()
            if st in ("bullish", "bearish", "neutral"):
                stance = st
            try:
                conf = max(0.0, min(1.0, float(obj.get("confidence", 0.6))))
            except (ValueError, TypeError):
                pass
            # JSON 回复，没有长文本
            summary = str(obj.get("summary", reply[:200]))[:200]
        else:
            # 正则降级
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

    # ── 生成摘要 ──
    if not summary:
        # 从 full_text 中取前两行作为摘要
        f_lines = full_text.split("\n")
        summary = " ".join(l.strip("# ").strip() for l in f_lines[:3] if l.strip())[:200]

    return AgentReport(
        name=name, stance=stance, confidence=conf,
        summary=summary[:200], full_text=full_text[:8000],
    )  # type: ignore[arg-type]


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
# Layer 1.5 — 形势摘要（Situation Summariser）
# ═══════════════════════════════════════════════════════════════

_SUMMARISER_SYS = (
    "你是形势摘要专家。你的任务是将 4 位分析师的独立报告蒸馏为一份 ≤400 token 的"
    "结构化情势快照，只保留最关键的信息和分歧点。不要添加分析师未提及的任何新信息。"
)

_SUMMARISER_PROMPT = """【你的任务】将以下 4 位分析师报告蒸馏为一份简洁的结构化情势快照。

【市场快照】
{snapshot}

【4 位分析师独立报告】
{analyst_summary}

请输出 JSON（不超过 400 tokens 总输出）：
{{
  "snapshot_text": "≤200字，将四位分析师的核心判断浓缩为一句话形势评估。必须明确标注多头/空头/中性立场分布",
  "key_drivers": ["最关键的 2-3 个驱动因素，必须引用具体数字或新闻原文"],
  "uncertainties": ["1-2 个分析师之间有分歧或不确定的领域"]
}}"""


def run_situation_summariser(
    llm: LLM, md: MarketData, reports: list[AgentReport]
) -> SituationSummary:
    """Layer 1.5: 形势摘要 — 蒸馏 4 分析师报告为结构化快照。"""
    snapshot = make_snapshot(md)
    analyst_summary = "\n".join(
        f"- {r.name} [{r.stance}, conf={r.confidence:.0%}]: {r.summary}" for r in reports
    )
    prompt = _SUMMARISER_PROMPT.format(snapshot=snapshot, analyst_summary=analyst_summary)
    try:
        reply = llm.chat(prompt, system=_SUMMARISER_SYS)
        obj = _parse_json(reply)
        if obj is not None:
            return SituationSummary(
                snapshot_text=str(obj.get("snapshot_text", reply[:200])),
                key_drivers=[str(k) for k in obj.get("key_drivers", [])[:3]],
                uncertainties=[str(u) for u in obj.get("uncertainties", [])[:2]],
            )
    except Exception:
        pass
    return SituationSummary(
        snapshot_text=analyst_summary[:300],
        key_drivers=["分析师数据不足，无法提炼关键驱动"],
        uncertainties=["摘要降级：LLM 调用失败或 JSON 解析异常"],
    )


# ═══════════════════════════════════════════════════════════════
# Layer II — 多头/空头研究员（逐点辩论）
# ═══════════════════════════════════════════════════════════════

_BULL_SYS = (
    "你是专业的「多头研究员」。\n"
    "辩论风格：理性、证据驱动、逐点回应。\n"
    "输出格式要求：\n"
    "1. 开头一句话总结核心看多逻辑\n"
    "2. 用「📌 论点 N：」标记 2-3 个要点，每个引用具体数字/新闻\n"
    "3. 如非首轮，必须先回应对手上一轮的质疑，格式：「⚡ 回击：空头认为 X，但数据/事实表明 Y」\n"
    "4. 总字数 150-300 字"
)

_BEAR_SYS = (
    "你是专业的「空头研究员」。\n"
    "辩论风格：质疑但不情绪化、逻辑拆解、寻找盲点。\n"
    "输出格式要求：\n"
    "1. 开头一句话指出多头最薄弱的假设或论据\n"
    "2. 用「📌 质疑 N：」标记 2-3 个要点，每个引用具体数据\n"
    "3. 如非首轮，必须先回应对手上一轮的反驳，格式：「⚡ 回应：多头称 X，但数据/事实表明 Y」\n"
    "4. 总字数 150-300 字"
)


def run_debate(
    llm: LLM, md: MarketData, reports: list[AgentReport], rounds: int = 1
) -> list[DebateRound]:
    """Layer II: 多头 vs 空头，逐点辩论，模拟 TradingAgents 风格。

    每一轮：
    - 多头先发言，引用分析师结论 + 市场数据，给出具体看多论点
    - 空头逐点回击，指出多头逻辑漏洞或数据盲点
    - 下一轮多头基于空头质疑调整/强化论点
    """
    if rounds <= 0:
        return []

    snapshot = make_snapshot(md)
    analyst_summary = "\n".join(
        f"【{r.name}】{r.stance} (信心 {r.confidence:.0%})：{r.summary}"
        for r in reports
    )

    debates: list[DebateRound] = []

    for rnd in range(rounds):
        # ── 构建本轮的对抗上下文 ──
        prev_collapse = ""
        if rnd > 0:
            prev_collapse = (
                "\n\n【前一轮辩论全文】⚠️ 你必须逐点回应对手观点：\n"
                f"🐂 多头 R{rnd}：{debates[-1].bull}\n"
                f"🐻 空头 R{rnd}：{debates[-1].bear}\n"
            )

        # ── 多头发言 ──
        bull_rebuttal = ""
        if rnd > 0:
            bull_rebuttal = (
                f"【上一轮对方的质疑】你上一轮论点被对方这样攻击，本轮必须回应：\n"
                f"空头: {debates[-1].bear}\n"
            )

        bull_prompt = (
            f"你是「多头研究员」。第 {rnd + 1} 轮辩论。\n\n"
            f"【市场快照】\n{snapshot}\n\n"
            f"【4 位分析师结论】\n{analyst_summary}\n"
            f"{prev_collapse}"
            f"{bull_rebuttal}"
        )
        bull = _safe_chat(llm, bull_prompt, _BULL_SYS)

        # ── 空头发言 ──
        bear_prompt = (
            f"你是「空头研究员」。第 {rnd + 1} 轮辩论。\n\n"
            f"【市场快照】\n{snapshot}\n\n"
            f"【4 位分析师结论】\n{analyst_summary}\n"
            f"{prev_collapse}"
            f"【本轮对手刚发表的论点】⚠️ 你必须逐条拆解对方逻辑漏洞：\n"
            f"多头: {bull}\n"
        )
        bear = _safe_chat(llm, bear_prompt, _BEAR_SYS)

        debates.append(DebateRound(bull=bull[:600], bear=bear[:600]))

    return debates


def _safe_chat(llm: LLM, prompt: str, system: str) -> str:
    """调用 LLM.chat，异常时返回降级信息。"""
    try:
        return llm.chat(prompt, system=system).strip()
    except Exception as e:
        return f"调用失败({e.__class__.__name__})"


# ═══════════════════════════════════════════════════════════════
# Layer III — 研究经理
# ═══════════════════════════════════════════════════════════════

_RESEARCH_MANAGER_SYS = (
    "你是资深研究经理（Research Manager），职责是汇总多方观点、识别逻辑矛盾、"
    "输出平衡的综合报告。你会指出多头论点的薄弱环节，也会质疑空头是否有遗漏的利好。"
    "最终给出对交易员有价值的指导性结论。"
    "输出完整的 Markdown 格式报告，800 字以上，包含数据表格。"
)

_RESEARCH_MANAGER_PROMPT = """【你的任务】作为研究经理，汇总以下所有输入，撰写一份结构化的投研综合报告。

【市场快照】
{snapshot}

【4 位分析师独立报告】
{analyst_summary}

【多空研究员辩论记录】
{debate_log}

【报告格式】
## 一、辩论核心矛盾总结
（用表格列出多头 vs 空头在 3-5 个关键论点上的对立立场）

## 二、关键风险/机会评估
（逐一分析最重要的风险和机会，每个 50-100 字）

## 三、投资计划与评级
（综合评级 buy/sell/hold，含置信度、仓位建议、目标价范围、止损位）

## 四、最终结论
（150 字以内的决策性总结）

【⚠️ 最后一行单独输出 JSON 元数据】：
{{"stance": "bullish|bearish|neutral", "confidence": 0.0-1.0, "key_points": ["要点1","要点2","要点3"]}}
"""


def run_research_manager(
    llm: LLM, md: MarketData, reports: list[AgentReport], debates: list[DebateRound]
) -> ManagerReport:
    """Layer III: 研究经理——汇总辩论，产出完整 Markdown 报告。"""
    snapshot = make_snapshot(md)
    analyst_summary = "\n".join(
        f"- {r.name} [{r.stance}, conf={r.confidence:.0%}]: {r.summary}" for r in reports
    )
    debate_log = "\n".join(
        f"轮次 {i+1}:\n  多头: {d.bull[:500]}\n  空头: {d.bear[:500]}\n"
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

    # ── 从尾部提取 JSON 元数据 ──
    full_text = reply.strip()
    stance = "neutral"
    confidence = 0.5
    key_points: list[str] = []
    summary = ""

    lines = full_text.rsplit("\n", 5)
    for i in range(len(lines) - 1, -1, -1):
        obj = _parse_json(lines[i].strip())
        if obj is not None and "stance" in obj:
            st = str(obj.get("stance", "neutral")).lower()
            if st in ("bullish", "bearish", "neutral"):
                stance = st
            confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.5))))
            key_points = [str(k) for k in obj.get("key_points", [])[:5]]
            full_text = "\n".join(lines[:i]).strip()
            break

    if not summary:
        f_lines = full_text.split("\n")
        summary = " ".join(l.strip("# ").strip() for l in f_lines[:3] if l.strip())[:300]

    return ManagerReport(
        summary=summary, stance=stance,  # type: ignore[arg-type]
        confidence=confidence, key_points=key_points,
        full_text=full_text[:8000],
    )


# ═══════════════════════════════════════════════════════════════
# Layer IV — 交易员
# ═══════════════════════════════════════════════════════════════

_TRADER_SYS = (
    "你是独立交易员（Trader），不盲从研究报告。你会综合考虑：技术面时机、"
    "仓位管理、风险收益比、流动性。你的决策可以有别于研究结论——"
    "如果研究报告极度看多但估值过高，你可以选择观望或轻仓。"
    "输出完整的 Markdown 交易计划，至少 400 字。"
)

_TRADER_PROMPT = """【你的任务】阅读研究经理报告，独立制定交易计划。

【研究经理综合报告】
{manager_summary}

【原始快照（不要忽略市场实际情况）】
{snapshot}

【交易计划格式】
## 一、对研究结论的评估
（你同意/不同意研究经理的哪些判断，为什么）

## 二、交易策略
（列出操作 action:buy/sell/hold，仓位百分比，入场方式/价位，止损/止盈）

## 三、风险收益比分析
（列出可能的下行风险 vs 上行空间，量化估计）

## 四、独立判断理由
（如果与研究经理意见不同，详细说明原因）

【⚠️ 最后一行单独输出 JSON 元数据】：
{{"action": "buy|sell|hold", "confidence": 0.0-1.0, "position_pct": 0.0-1.0, "entry_strategy": "入场策略描述"}}
"""


def run_trader(
    llm: LLM, md: MarketData, manager: ManagerReport
) -> TraderReport:
    """Layer IV: 交易员——独立决策（完整 Markdown 交易计划）。"""
    snapshot = make_snapshot(md)
    prompt = _TRADER_PROMPT.format(manager_summary=manager.summary, snapshot=snapshot)
    try:
        reply = llm.chat(prompt, system=_TRADER_SYS)
    except Exception as e:
        return TraderReport(
            action="hold", confidence=0.0,
            reasoning=f"交易员调用失败({e.__class__.__name__})",
        )

    full_text = reply.strip()
    action = "hold"
    confidence = 0.3
    position_pct = 0.0
    entry_strategy = ""
    reasoning = ""

    # ── 从尾部提取 JSON ──
    lines = full_text.rsplit("\n", 5)
    for i in range(len(lines) - 1, -1, -1):
        obj = _parse_json(lines[i].strip())
        if obj is not None and "action" in obj:
            act = str(obj.get("action", "hold")).lower()
            if act in ("buy", "sell", "hold"):
                action = act
            confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.3))))
            position_pct = max(0.0, min(1.0, float(obj.get("position_pct", 0.0))))
            entry_strategy = str(obj.get("entry_strategy", ""))
            reasoning = str(obj.get("reasoning", ""))
            full_text = "\n".join(lines[:i]).strip()
            break

    if not reasoning:
        f_lines = full_text.split("\n")
        reasoning = " ".join(l.strip("# ").strip() for l in f_lines[:2] if l.strip())[:200]

    return TraderReport(
        action=action,  # type: ignore[arg-type]
        confidence=confidence, position_pct=position_pct,
        entry_strategy=entry_strategy, reasoning=reasoning,
        full_text=full_text[:6000],
    )


# ═══════════════════════════════════════════════════════════════
# Layer V — 三视角风控（激进 + 保守 + 中立）
# ═══════════════════════════════════════════════════════════════

_AGGRESSIVE_RISK_SYS = (
    "你是激进型风控官（Aggressive Risk Analyst）。你偏好承担可控风险以追求更高收益，"
    "在仓位、止损、杠杆方面比较宽容。但你不赌——你必须基于数据。"
    "输出完整的 Markdown 风险分析报告，300 字以上，含表格。"
)

_CONSERVATIVE_RISK_SYS = (
    "你是保守型风控官（Conservative Risk Analyst）。你的首要目标是资产保全，"
    "宁可错过不可做错。你会严格审查仓位合理性、止损距离、流动性风险和黑天鹅可能。"
    "输出完整的 Markdown 风险分析报告，300 字以上，含表格。"
)

_NEUTRAL_RISK_SYS = (
    "你是中立型风控官（Neutral Risk Analyst）。你的任务是平衡激进与保守两个极端，"
    "找出最合理的中间路径。你既不偏袒冒进也不过分保守，基于事实和数据给出公允评估。"
    "输出完整的 Markdown 风险分析报告，300 字以上，含表格。"
)

_RISK_PROMPT = """【你的任务】作为{style}风控官，审议交易员的决策。

【市场快照】
{snapshot}

【研究经理报告】
{manager_summary}

【交易员决策】
操作: {action}  置信度: {confidence:.0%}  仓位: {position_pct:.0%}
入场策略: {entry_strategy}
交易员理由: {trader_reasoning}

【报告格式】
## 一、交易决策风险评估
（从{style}风控角度分析该决策的风险敞口）

## 二、关键风险点（表格）
（列出 3-5 个具体风险，标注严重程度和概率）

## 三、修改建议
（如果 approve 写无修改，否则给出具体调整）

## 四、最终裁决
（approve/reject/modify 及理由）

【⚠️ 最后一行单独输出 JSON 元数据】：
{{"verdict": "approve|reject|modify", "analysis": "核心风险分析总结", "modifications": "修改建议描述"}}
"""


def _parse_risk_reply(reply: str) -> tuple[str, str, str, str]:
    """解析风控回复：返回 (verdict, short_analysis, full_text, modifications)。"""
    full_text = reply.strip()
    verdict = "approve"
    analysis = ""
    modifications = ""

    lines = full_text.rsplit("\n", 5)
    for i in range(len(lines) - 1, -1, -1):
        obj = _parse_json(lines[i].strip())
        if obj is not None and "verdict" in obj:
            v = str(obj.get("verdict", "approve")).lower()
            if v in ("approve", "reject", "modify"):
                verdict = v
            analysis = str(obj.get("analysis", ""))
            modifications = str(obj.get("modifications", ""))
            full_text = "\n".join(lines[:i]).strip()
            break

    if not analysis:
        f_lines = full_text.split("\n")
        analysis = " ".join(l.strip("# ").strip() for l in f_lines[:2] if l.strip())[:200]

    return verdict, analysis[:300], full_text[:5000], modifications[:300]


def run_risk_reviews(
    llm: LLM, md: MarketData, manager: ManagerReport, trader: TraderReport
) -> RiskReview:
    """Layer V: 三视角风控审议（完整 Markdown 报告）。"""
    snapshot = make_snapshot(md)
    risk_prompt_tpl = _RISK_PROMPT

    def _review(system: str, style: str) -> tuple:
        try:
            prompt = risk_prompt_tpl.format(
                snapshot=snapshot, manager_summary=manager.summary,
                action=trader.action, confidence=trader.confidence,
                position_pct=trader.position_pct, entry_strategy=trader.entry_strategy,
                trader_reasoning=trader.reasoning, style=style,
            )
            reply = llm.chat(prompt, system=system)
            return _parse_risk_reply(reply)
        except Exception as e:
            return ("approve", f"调用失败({e.__class__.__name__})", "", "")

    agg_v, agg_a, agg_full, _ = _review(_AGGRESSIVE_RISK_SYS, "激进型")
    con_v, con_a, con_full, _ = _review(_CONSERVATIVE_RISK_SYS, "保守型")
    neu_v, neu_a, neu_full, _ = _review(_NEUTRAL_RISK_SYS, "中立型")

    return RiskReview(
        aggressive=agg_a, aggressive_stance=agg_v,  # type: ignore[arg-type]
        conservative=con_a, conservative_stance=con_v,  # type: ignore[arg-type]
        neutral=neu_a, neutral_stance=neu_v,  # type: ignore[arg-type]
        aggressive_full=agg_full, conservative_full=con_full, neutral_full=neu_full,
    )


# ═══════════════════════════════════════════════════════════════
# Layer VI — 基金经理（最终审批）
# ═══════════════════════════════════════════════════════════════

_FUND_MANAGER_SYS = (
    "你是基金经理（Portfolio Manager），负责最终审批交易决策。"
    "你综合考虑研究、交易、风控三方意见，做出最终裁定。"
    "你可以批准(approve)、否决(reject)、或调整(modify)。"
    "你的决策是不可上诉的。输出完整的 Markdown 最终裁定书，400 字以上。"
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
⚪ 中立风控 [{neu_stance}]: {neu_text}

【裁定书格式】
## 最终交易决策：{symbol}
（一句话概述最终裁定）

## 一、核心矛盾的最终裁定
（150字，对多空核心矛盾做出最终裁决）

## 二、操作选择理由
（为什么选择 buy/sell/hold，引用具体风控意见）

## 三、具体交易指令
（操作/仓位/入场价/止损/止盈，用表格呈现）

## 四、核心观察节点与触发条件
（列出后续需关注的关键事件和调整触发条件）

【⚠️ 最后一行单独输出 JSON 元数据】：
{{"final_action": "buy|sell|hold", "final_confidence": 0.0-1.0, "final_position_pct": 0.0-1.0}}
"""


def run_fund_manager(
    llm: LLM, md: MarketData,
    manager: ManagerReport, trader: TraderReport,
    risk: RiskReview,
) -> dict:
    """Layer VI: 基金经理——最终裁定。返回 {action, confidence, position_pct, reasoning, full_text}。"""
    snapshot = make_snapshot(md)
    prompt = _FUND_MANAGER_PROMPT.format(
        snapshot=snapshot,
        manager_summary=manager.full_text or manager.summary,
        action=trader.action, confidence=trader.confidence,
        position_pct=trader.position_pct, entry_strategy=trader.entry_strategy,
        agg_stance=risk.aggressive_stance, agg_text=risk.aggressive_full or risk.aggressive[:300],
        con_stance=risk.conservative_stance, con_text=risk.conservative_full or risk.conservative[:300],
        neu_stance=risk.neutral_stance, neu_text=risk.neutral_full or risk.neutral[:300],
        symbol=md.quote.symbol,
    )
    try:
        reply = llm.chat(prompt, system=_FUND_MANAGER_SYS)
    except Exception as e:
        act = trader.action if trader.action != "buy" else "hold"
        return {
            "action": act, "confidence": 0.3, "position_pct": 0.0,
            "reasoning": f"基金经理调用失败({e.__class__.__name__})，降级保守处理。",
            "full_text": "",
        }

    full_text = reply.strip()
    action = trader.action if trader.action != "buy" else "hold"
    confidence = 0.3
    position_pct = 0.0
    reasoning = ""

    # ── 从尾部提取 JSON ──
    lines = full_text.rsplit("\n", 5)
    for i in range(len(lines) - 1, -1, -1):
        obj = _parse_json(lines[i].strip())
        if obj is not None and "final_action" in obj:
            act = str(obj.get("final_action", trader.action)).lower()
            if act in ("buy", "sell", "hold"):
                action = act
            confidence = max(0.0, min(1.0, float(obj.get("final_confidence", 0.3))))
            position_pct = max(0.0, min(1.0, float(obj.get("final_position_pct", 0.0))))
            full_text = "\n".join(lines[:i]).strip()
            break

    if not reasoning:
        f_lines = full_text.split("\n")
        reasoning = " ".join(l.strip("# ").strip() for l in f_lines[:3] if l.strip())[:200]

    return {
        "action": action, "confidence": confidence, "position_pct": position_pct,
        "reasoning": reasoning, "full_text": full_text[:8000],
    }
