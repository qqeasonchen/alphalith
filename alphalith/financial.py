"""
Financial statements & fundamentals — 三市场零依赖数据。
数据源:
  美股: yfinance (PE/PB/ROE/EPS/股息/负债), Yahoo HTML fallback
  A股: 东财 push2 基本面
  港股: 腾讯 qt.gtimg.cn
"""
from __future__ import annotations

import json as _json
import re
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

from .market import Market, detect_market


@dataclass
class Financials:
    symbol: str
    name: str = ""
    pe: float = 0.0
    pb: float = 0.0
    roe: float = 0.0
    market_cap: float = 0.0          # 亿 (本币)
    revenue_growth: float = 0.0       # YoY %
    profit_margin: float = 0.0        # 净利润率 %
    dividend_yield: float = 0.0
    debt_ratio: float = 0.0
    eps: float = 0.0
    bvps: float = 0.0                # 每股净资产
    source: str = ""
    raw_metrics: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        parts = [f"{self.name}({self.symbol})"]
        if self.pe: parts.append(f"PE {self.pe:.1f}")
        if self.pb: parts.append(f"PB {self.pb:.2f}")
        if self.roe: parts.append(f"ROE {self.roe:.1f}%")
        if self.market_cap: parts.append(f"市值 {self.market_cap:.0f}亿")
        if self.eps: parts.append(f"EPS {self.eps:.2f}")
        if self.revenue_growth: parts.append(f"营收增长 {self.revenue_growth:+.1f}%")
        if self.dividend_yield: parts.append(f"股息率 {self.dividend_yield:.2f}%")
        if not parts[1:]:
            return parts[0] + " (数据缺失)"
        return " | ".join(parts)

    @property
    def note(self) -> str:
        """可注入快照的基本面摘要。"""
        if not self.name:
            return "基本面数据缺失"
        items = []
        if self.name: items.append(self.name)
        items.append(f"PE:{self.pe:.1f}" if self.pe else "PE:N/A")
        items.append(f"PB:{self.pb:.2f}" if self.pb else "PB:N/A")
        if self.roe: items.append(f"ROE:{self.roe:.1f}%")
        if self.market_cap: items.append(f"市值:{self.market_cap:.0f}亿")
        if self.eps: items.append(f"EPS:{self.eps:.2f}")
        items.append(f"源:{self.source}")
        return " ".join(items)


def _fetch_yfinance_info(ticker: str) -> Optional[dict]:
    """yfinance .info dict（完整基本面）。"""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("regularMarketPrice") is None:
            return None
        return info
    except Exception:
        return None


def _fetch_eastmoney_a(code: str) -> Optional[dict]:
    """东财 A 股基本面（使用已知字段编号）。
    f9=PE(TTM动态), f23=PB, f37=ROE%, f20=总市值, f21=流通市值,
    f43=每股收益, f57=代码, f58=名称, f45=52周高, f46=52周低
    """
    suffix = "1" if code.startswith(("6", "9")) else "0"
    secid = f"{suffix}.{code}"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f9,f20,f21,f23,f37,f43,f45,f46,f57,f58&invt=2&fltt=1"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        return data.get("data")
    except Exception:
        return None


def _fetch_tencent_fundamental(code: str, market: Market) -> Optional[dict]:
    """腾讯 qt.gtimg.cn 基本面（已工作，扩展字段）。"""
    from .data import _qt_parts
    if market == Market.A_STOCK:
        qcode = ("sh" if code.startswith(("6", "9")) else "sz") + code
    elif market == Market.HK_STOCK:
        qcode = "r_hk" + code.zfill(5)
    else:
        return None
    parts = _qt_parts(f"https://qt.gtimg.cn/q={qcode}", marker=f"v_{qcode}")
    if len(parts) < 10:
        return None
    return {"name": parts[1], "parts": parts}


def load_financials(symbol_input: str) -> Financials:
    """加载完整的财务数据。"""
    market, normalized = detect_market(symbol_input)
    code = normalized.split(".")[0].split(":")[-1]

    # ── 美股: yfinance ──
    if market == Market.US_STOCK:
        info = _fetch_yfinance_info(code)
        if info:
            return Financials(
                symbol=normalized,
                name=info.get("shortName") or info.get("longName") or code,
                pe=float(info.get("trailingPE") or info.get("forwardPE") or 0),
                pb=float(info.get("priceToBook") or 0),
                roe=float((info.get("returnOnEquity") or 0)) * 100,
                market_cap=float(info.get("marketCap") or 0) / 1e8,
                revenue_growth=float((info.get("revenueGrowth") or 0)) * 100,
                profit_margin=float((info.get("profitMargins") or 0)) * 100,
                dividend_yield=float((info.get("dividendYield") or 0)) * 100,
                debt_ratio=float(info.get("debtToEquity") or 0),
                eps=float(info.get("trailingEps") or 0),
                bvps=float(info.get("bookValue") or 0),
                source="yfinance",
                raw_metrics={"sector": info.get("sector", ""), "industry": info.get("industry", "")},
            )
        return Financials(symbol=normalized, name=code, source="none")

    # ── A 股: 东财优先 ──
    if market == Market.A_STOCK:
        em = _fetch_eastmoney_a(code)
        if em and em.get("f58"):
            try:
                pe = float(em.get("f9", 0) or 0)
            except Exception:
                pe = 0.0
            try:
                pb = float(em.get("f23", 0) or 0)
            except Exception:
                pb = 0.0
            try:
                roe = float(em.get("f37", 0) or 0)
            except Exception:
                roe = 0.0
            try:
                mcap = float(em.get("f20", 0) or 0) / 1e8
            except Exception:
                mcap = 0.0
            try:
                eps = float(em.get("f43", 0) or 0)
            except Exception:
                eps = 0.0
            return Financials(
                symbol=normalized, name=str(em.get("f58", code)),
                pe=pe, pb=pb, roe=roe, market_cap=mcap, eps=eps,
                source="eastmoney",
            )
        # 腾讯 fallback
        qt = _fetch_tencent_fundamental(code, market)
        if qt:
            p = qt["parts"]
            pe = 0.0
            pb = 0.0
            roe = 0.0
            mcap = 0.0
            try:
                if len(p) > 52 and p[52] and p[52] not in ("0.00", ""):
                    pe = float(p[52])
            except Exception:
                pass
            try:
                if len(p) > 46 and p[46] and p[46] not in ("0.00", ""):
                    pb = float(p[46])
            except Exception:
                pass
            try:
                if len(p) > 39 and p[39] and p[39] not in ("0.00", ""):
                    roe = float(p[39])
            except Exception:
                pass
            try:
                if len(p) > 45 and p[45]:
                    mcap = float(p[45])
            except Exception:
                pass
            return Financials(
                symbol=normalized, name=qt.get("name", code),
                pe=pe, pb=pb, roe=roe, market_cap=mcap,
                source="tencent",
            )

    # ── 港股: 腾讯 ──
    if market == Market.HK_STOCK:
        qt = _fetch_tencent_fundamental(code, market)
        if qt:
            p = qt["parts"]
            pe = pb = roe = mcap = 0.0
            try:
                if len(p) > 39 and p[39] and p[39] not in ("0.00", ""):
                    pe = float(p[39])
            except Exception:
                pass
            try:
                if len(p) > 44 and p[44]:
                    mcap = float(p[44])
            except Exception:
                pass
            return Financials(
                symbol=normalized, name=qt.get("name", code),
                pe=pe, market_cap=mcap, source="tencent",
            )

    return Financials(symbol=normalized, name=code, source="none")
