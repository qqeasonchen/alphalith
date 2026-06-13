"""
Market rule engines — A股 / 港股 / 美股。
市场规则引擎 — Alphalith 自研独家差异化模块。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..market import Market


@dataclass
class FeeResult:
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    sec_fee: float = 0.0
    other: dict[str, float] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.other is None:
            self.other = {}

    @property
    def total(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee + self.sec_fee + sum(self.other.values())


class MarketRules(ABC):
    market: Market
    currency: str
    settlement: str
    has_price_limit: bool

    @abstractmethod
    def round_lot(self, symbol: str, shares: int) -> int:
        ...

    @abstractmethod
    def calc_fee(self, amount: float, side: str, shares: int = 0) -> FeeResult:
        ...

    @abstractmethod
    def warnings(self, symbol: str, price: float, prev_close: float | None = None) -> list[str]:
        ...


# ─── A 股 ──────────────────────────────────────────────────────────
class AStockRules(MarketRules):
    market = Market.A_STOCK
    currency = "CNY"
    settlement = "T+1"
    has_price_limit = True

    @staticmethod
    def price_limit_pct(symbol: str) -> float:
        code = symbol.replace(".SS", "").replace(".SZ", "").replace(".BJ", "")
        if code.startswith(("688", "300")):
            return 0.20
        if "ST" in symbol.upper():
            return 0.05
        return 0.10

    def round_lot(self, symbol: str, shares: int) -> int:
        return (shares // 100) * 100

    def calc_fee(self, amount: float, side: str, shares: int = 0) -> FeeResult:
        return FeeResult(
            commission=max(amount * 0.00025, 5.0),
            transfer_fee=amount * 0.00001,
            stamp_tax=amount * 0.0005 if side == "sell" else 0.0,
        )

    def warnings(self, symbol: str, price: float, prev_close: float | None = None) -> list[str]:
        msgs = ["T+1：今日买入次日才能卖出"]
        if prev_close:
            limit = self.price_limit_pct(symbol)
            up = prev_close * (1 + limit)
            dn = prev_close * (1 - limit)
            chg = (price - prev_close) / prev_close
            msgs.append(f"距涨停 {(up - price) / price * 100:+.2f}%（涨停价 ¥{up:.2f}）")
            msgs.append(f"距跌停 {(dn - price) / price * 100:+.2f}%（跌停价 ¥{dn:.2f}）")
            if chg >= limit * 0.95:
                msgs.append("⚠️ 接近涨停，注意冲高回落风险")
        msgs.append("最小交易单位：100 股（1 手）")
        return msgs


# ─── 港股 ──────────────────────────────────────────────────────────
HK_LOT_SIZE: dict[str, int] = {
    "0700.HK": 100, "0941.HK": 500, "0388.HK": 100,
    "9988.HK": 100, "3690.HK": 100, "1810.HK": 100,
    "2318.HK": 500, "0005.HK": 400, "1299.HK": 200,
}


class HKStockRules(MarketRules):
    market = Market.HK_STOCK
    currency = "HKD"
    settlement = "T+2"
    has_price_limit = False

    def round_lot(self, symbol: str, shares: int) -> int:
        lot = HK_LOT_SIZE.get(symbol, 100)
        return (shares // lot) * lot

    def calc_fee(self, amount: float, side: str, shares: int = 0) -> FeeResult:
        return FeeResult(
            commission=max(amount * 0.0003, 50.0),
            stamp_tax=amount * 0.0010,
            other={
                "trading_fee": amount * 0.0000565,
                "ccass_fee": max(amount * 0.00002, 2.0),
                "sfc_levy": amount * 0.000027,
                "frc_levy": amount * 0.0000015,
            },
        )

    def warnings(self, symbol: str, price: float, prev_close: float | None = None) -> list[str]:
        msgs = ["T+0：当日可回转交易；资金 T+2 交收"]
        msgs.append("无涨跌停限制，注意单日波动可能较大")
        lot = HK_LOT_SIZE.get(symbol, 100)
        msgs.append(f"每手 {lot} 股")
        return msgs


# ─── 美股 ──────────────────────────────────────────────────────────
class USStockRules(MarketRules):
    market = Market.US_STOCK
    currency = "USD"
    settlement = "T+1"
    has_price_limit = False

    def round_lot(self, symbol: str, shares: int) -> int:
        return shares  # 1 股起

    def calc_fee(self, amount: float, side: str, shares: int = 0) -> FeeResult:
        sec = amount * 0.0000278 if side == "sell" else 0.0
        finra = 0.000166 * shares if side == "sell" else 0.0
        return FeeResult(
            commission=0.0,  # 默认 0 佣金券商
            sec_fee=sec,
            other={"finra_taf": finra},
        )

    def warnings(self, symbol: str, price: float, prev_close: float | None = None) -> list[str]:
        return [
            "T+0：当日可回转；T+1 资金交收（2024 起）",
            "无涨跌停限制（仅触发熔断）",
            "支持盘前 04:00–09:30、盘后 16:00–20:00（纽约时间）",
            "1 股起买，可买零股",
        ]


def get_rules(market: Market) -> MarketRules:
    return {
        Market.A_STOCK: AStockRules(),
        Market.HK_STOCK: HKStockRules(),
        Market.US_STOCK: USStockRules(),
    }[market]
