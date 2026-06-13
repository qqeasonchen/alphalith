"""
Four analysts + bull/bear debate.
四位分析师 + 多空辩论。

v0.1.1：分析师 prompt 升级
- 每个 agent 都能看到完整市场快照（行情/新闻/基本面/情绪）
- 各自从自己的专业视角切入，避免 4 个角色拿到割裂数据
- 辩论环节也注入完整快照，bull/bear 才能真正对抗
"""
from __future__ import annotations

import re

from .data import MarketData
from .llm import LLM
from .schema import AgentReport, DebateRound


def _make_snapshot(md: MarketData) -> str:
    """完整市场快照——所有分析师/辩论员共享。"""
    q = md.quote
    news = "\n".join(f"  - {h}" for h in md.news_headlines[:5]) or "  - （暂无）"
    return (
        f"标的：{q.name or q.symbol} ({q.symbol})  市场：{q.market.value}\n"
        f"行情：现价 {q.price:.2f} {q.currency if hasattr(q,'currency') else ''}，"
        f"昨收 {q.prev_close:.2f}，涨跌 {q.change_pct:+.2f}%，成交量 {q.volume:,.0f}\n"
        f"技术：{md.history_summary}\n"
        f"基本面：{md.fundamental_note}\n"
        f"情绪：{md.sentiment_note}\n"
        f"新闻头条（{len(md.news_headlines)} 条）：\n{news}\n"
        f"数据源：{md.sources}"
    )


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


def _parse(reply: str, name: str) -> AgentReport:
    """优先 JSON 解析，失败时降级到正则（兼容 stub 与不听话的 LLM）。"""
    import json as _json
    stance = "neutral"
    conf = 0.6
    summary = reply.strip()

    # 1) 试 JSON
    text = reply.strip()
    # 去掉可能的 markdown 围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    l, r = text.find("{"), text.rfind("}")
    if l >= 0 and r > l:
        try:
            obj = _json.loads(text[l : r + 1])
            st = str(obj.get("stance", "")).lower()
            if st in ("bullish", "bearish", "neutral"):
                stance = st
            conf = max(0.0, min(1.0, float(obj.get("confidence", 0.6))))
            summary = str(obj.get("summary", summary))[:200]
            return AgentReport(name=name, stance=stance, confidence=conf, summary=summary)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            pass

    # 2) 降级：原正则解析（兼容旧 stub）
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
    snapshot = _make_snapshot(md)
    reports: list[AgentReport] = []
    for role, focus in _FOCUS.items():
        prompt = _ANALYST_PROMPT.format(
            role=role, symbol=md.quote.symbol, snapshot=snapshot, focus=focus
        )
        reply = llm.chat(
            prompt,
            system="你是严谨、可量化的金融分析师。只引用快照中的事实，禁止虚构数字。",
        )
        reports.append(_parse(reply, role))
    return reports


def run_debate(
    llm: LLM, md: MarketData, reports: list[AgentReport], rounds: int = 1
) -> list[DebateRound]:
    if rounds <= 0:
        return []
    snapshot = _make_snapshot(md)
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
        bull = llm.chat(
            f"你是「看多研究员」。\n\n【市场快照】\n{snapshot}\n\n"
            f"【4 位分析师结论】\n{summary}\n{rebuttal_hint}\n"
            f"请给出 80 字以内的看多论点，必须引用快照中的具体数字或新闻原文。",
            system="只输出论点本身，不要前缀，不要客套。",
        )
        last_bull = bull.strip()
        bear = llm.chat(
            f"你是「看空研究员」。\n\n【市场快照】\n{snapshot}\n\n"
            f"【4 位分析师结论】\n{summary}\n"
            f"\n上一轮对手观点（请针对性反驳）：{last_bull}\n"
            f"请给出 80 字以内的看空论点，必须引用快照中的具体数字或新闻原文。",
            system="只输出论点本身，不要前缀，不要客套。",
        )
        last_bear = bear.strip()
        debates.append(
            DebateRound(bull=last_bull[:300], bear=last_bear[:300])
        )
    return debates
