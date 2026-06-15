"""
Decision schema — implements ADP v1.0 (Alphalith Decision Protocol).
决策对象 — ADP v1.0 协议实现。

零外部依赖版本：使用标准库 dataclasses，避免 pydantic 在某些受限环境下的二进制加载问题。
仍保留 to_adp_json() 接口，输出符合 ADP v1.0 的 JSON。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .market import Market


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


@dataclass
class AgentReport:
    name: str
    stance: Literal["bullish", "bearish", "neutral"]
    confidence: float
    summary: str

    def __post_init__(self) -> None:
        self.confidence = _clamp01(self.confidence)


@dataclass
class DebateRound:
    bull: str
    bear: str


@dataclass
class SituationSummary:
    """形势快照 — Layer 1.5，将 4 分析师报告蒸馏为结构化摘要（≤400 tokens）。"""
    snapshot_text: str = ""          # 蒸馏后的情势快照
    key_drivers: list[str] = field(default_factory=list)  # 最关键的 2-3 个驱动因素
    uncertainties: list[str] = field(default_factory=list)  # 分析师共识/分歧点


@dataclass
class ManagerReport:
    """研究经理 — 汇总多空辩论后产出平衡分析。"""
    summary: str = ""
    stance: Literal["bullish", "bearish", "neutral"] = "neutral"
    confidence: float = 0.5
    key_points: list[str] = field(default_factory=list)


@dataclass
class TraderReport:
    """交易员 — 独立决策（买卖/仓位/时机）。"""
    action: Literal["buy", "sell", "hold"] = "hold"
    confidence: float = 0.0
    position_pct: float = 0.0       # 仓位百分比
    entry_strategy: str = ""        # 入场策略
    reasoning: str = ""


@dataclass
class RiskReview:
    """风控审议 — 三视角 (aggressive + conservative + neutral)。"""
    aggressive: str = ""
    aggressive_stance: Literal["approve", "reject", "modify"] = "approve"
    conservative: str = ""
    conservative_stance: Literal["approve", "reject", "modify"] = "approve"
    neutral: str = ""
    neutral_stance: Literal["approve", "reject", "modify"] = "approve"
    final_verdict: str = ""         # 基金经理最终判定


@dataclass
class FeeBreakdown:
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    sec_fee: float = 0.0
    other: dict[str, float] = field(default_factory=dict)
    total: float = 0.0
    breakeven_pct: float = 0.0


@dataclass
class Decision:
    """ADP v1.0 标准决策对象。"""

    # 标的（必填）
    symbol: str
    market: Market
    currency: Literal["CNY", "HKD", "USD"]

    # 决策核心
    action: Literal["buy", "sell", "hold"] = "hold"
    confidence: float = 0.0
    suggested_shares: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0

    # 协议版本与身份
    adp_version: str = "1.0"
    id: str = ""
    timestamp: datetime = field(default_factory=_utcnow)

    # 推理链路 (7 层 13 节点)
    agent_reports: list[AgentReport] = field(default_factory=list)
    situation_summary: SituationSummary = field(default_factory=SituationSummary)
    debate: list[DebateRound] = field(default_factory=list)
    manager_report: ManagerReport = field(default_factory=ManagerReport)
    trader_report: TraderReport = field(default_factory=TraderReport)
    risk_reviews: list[RiskReview] = field(default_factory=list)
    risk_review: str = ""           # deprecated — 保留兼容
    reasoning: str = ""

    # 市场规则
    market_warnings: list[str] = field(default_factory=list)
    fees: FeeBreakdown = field(default_factory=FeeBreakdown)

    # 元信息
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confidence = _clamp01(self.confidence)

    def to_adp_json(self) -> dict[str, Any]:
        """符合 ADP v1.0 的 JSON dict（可直接 webhook 推送）。"""
        return _to_jsonable(asdict(self))


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if is_dataclass(obj):
        return _to_jsonable(asdict(obj))
    return obj
