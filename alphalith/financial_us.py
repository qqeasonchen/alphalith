"""
SEC EDGAR XBRL companyfacts — 美股深度财务数据（仅美股）

数据源: data.sec.gov（公开免鉴权，需 User-Agent）
覆盖: 503 GAAP 指标 / 历史财报 / 10-K & 10-Q 列表 / Ticker→CIK 映射

设计原则: 与 Alphalith 一致 —— 纯 urllib，无第三方依赖。
"""
from __future__ import annotations

import json as _json
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional


SEC_UA = "Alphalith Research alphalith@example.com"
SEC_TIMEOUT = 15

# 模块级缓存
_cik_cache: Optional[dict] = None


def _http_get_json(url: str) -> dict:
    """SEC 接口要求 User-Agent，否则 403。"""
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=SEC_TIMEOUT) as resp:
        return _json.loads(resp.read().decode("utf-8"))


# ────────────────────────────────────────────────────────────
# Ticker → CIK
# ────────────────────────────────────────────────────────────
def ticker_to_cik(ticker: str) -> Optional[dict]:
    """
    将美股 ticker 转换为 SEC CIK。
    返回: {"ticker": "AAPL", "cik": "0000320193", "company": "Apple Inc."}
    失败返回 None。
    """
    global _cik_cache
    if _cik_cache is None:
        try:
            _cik_cache = _http_get_json("https://www.sec.gov/files/company_tickers.json")
        except (urllib.error.URLError, _json.JSONDecodeError, TimeoutError):
            return None

    t = ticker.upper().strip()
    for _, v in _cik_cache.items():
        if v.get("ticker") == t:
            return {
                "ticker": t,
                "cik": str(v["cik_str"]).zfill(10),
                "company": v.get("title", ""),
            }
    return None


# ────────────────────────────────────────────────────────────
# Filings 列表
# ────────────────────────────────────────────────────────────
def sec_filings(cik: str, form_type: Optional[str] = None, limit: int = 50) -> dict:
    """
    SEC EDGAR Filing 列表。
    cik: 10 位补零 CIK，可由 ticker_to_cik() 获得
    form_type: 可选，"10-K" / "10-Q" / "8-K" 等
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        data = _http_get_json(url)
    except Exception:
        return {"company_name": "", "cik": cik, "filings": []}

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    out = []
    cik_int = int(cik)
    for i in range(len(forms)):
        if form_type and forms[i] != form_type:
            continue
        acc = accessions[i] if i < len(accessions) else ""
        doc = primary_docs[i] if i < len(primary_docs) else ""
        out.append({
            "form": forms[i],
            "date": dates[i] if i < len(dates) else "",
            "accession_number": acc,
            "primary_document": doc,
            "description": descriptions[i] if i < len(descriptions) else "",
            "url": (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                    f"{acc.replace('-', '')}/{doc}") if acc and doc else "",
        })

    return {
        "company_name": data.get("name", ""),
        "cik": cik,
        "ticker": (data.get("tickers") or [""])[0],
        "filings": out[:limit],
    }


# ────────────────────────────────────────────────────────────
# XBRL companyfacts
# ────────────────────────────────────────────────────────────

# 常用 GAAP 指标速查（按 Alphalith 命名习惯做语义映射）
GAAP_METRICS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "eps_basic": ["EarningsPerShareBasic"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByOperatingActivities"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
    "buyback": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends_paid": ["PaymentsOfDividends"],
}


def sec_xbrl_facts(cik: str, metrics: Optional[list] = None,
                   form_filter: tuple = ("10-K", "10-Q"),
                   tail: int = 20) -> dict:
    """
    SEC EDGAR XBRL companyfacts。
    cik: 10 位补零 CIK
    metrics: GAAP 指标名列表（不传返回全部可用指标元信息）
    form_filter: 仅保留 10-K / 10-Q（默认）
    tail: 每个指标返回最近 N 条记录
    """
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        facts = _http_get_json(url)
    except Exception:
        return {"company": "", "metrics": {}}

    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    if not metrics:
        available = []
        for k, v in us_gaap.items():
            available.append({
                "name": k,
                "label": v.get("label", k),
                "units": list(v.get("units", {}).keys()),
            })
        return {
            "company": facts.get("entityName", ""),
            "total_metrics": len(available),
            "available_metrics": available,
        }

    result = {}
    for m in metrics:
        node = us_gaap.get(m)
        if not node:
            result[m] = []
            continue
        units = node.get("units", {})
        unit_key = "USD" if "USD" in units else (next(iter(units), None))
        if not unit_key:
            result[m] = []
            continue
        entries = units[unit_key]
        kept = [e for e in entries if e.get("form") in form_filter]
        result[m] = [{
            "end": e.get("end"),
            "val": e.get("val"),
            "form": e.get("form"),
            "filed": e.get("filed"),
            "fy": e.get("fy"),
            "fp": e.get("fp"),
        } for e in kept[-tail:]]

    return {"company": facts.get("entityName", ""), "metrics": result}


# ────────────────────────────────────────────────────────────
# 高层封装：一行调用拿到结构化关键指标
# ────────────────────────────────────────────────────────────
@dataclass
class SecSnapshot:
    ticker: str
    cik: str
    company: str = ""
    revenue_ttm: float = 0.0
    net_income_ttm: float = 0.0
    eps_diluted_latest: float = 0.0
    assets_latest: float = 0.0
    equity_latest: float = 0.0
    rd_expense_ttm: float = 0.0
    operating_cash_flow_ttm: float = 0.0
    history: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        parts = [f"{self.company}({self.ticker})"]
        if self.revenue_ttm:
            parts.append(f"营收TTM ${self.revenue_ttm/1e9:.1f}B")
        if self.net_income_ttm:
            parts.append(f"净利TTM ${self.net_income_ttm/1e9:.1f}B")
        if self.eps_diluted_latest:
            parts.append(f"摊薄EPS ${self.eps_diluted_latest:.2f}")
        return " | ".join(parts)


def _sum_last_n_q(records: list, n: int = 4) -> float:
    """累加最近 n 季度（仅取 10-Q 增量值），用于 TTM 估算（适用于 income statement 类增量科目）。
    SEC XBRL 中 10-Q 的 income 科目分两种：
      - 当季增量（duration 约 90 天）
      - YTD 累计（duration 约 180/270 天）
    必须只取「当季增量」，否则会重复累加。
    """
    qs = []
    for r in records:
        if r.get("form") != "10-Q":
            continue
        # SEC 数据带 start/end，duration < 100 天的才是当季增量
        # 不带 start 的就用启发式：相邻条目 fy/fp 不重复
        qs.append(r)
    # 按 (fy, fp) 唯一化，保留每对最后一条
    seen = {}
    for r in qs:
        key = (r.get("fy"), r.get("fp"))
        seen[key] = r
    qs = list(seen.values())
    qs.sort(key=lambda r: (r.get("fy") or 0, {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}.get(r.get("fp", ""), 0)))
    return float(sum(r.get("val", 0) for r in qs[-n:])) if qs else 0.0


def _ttm_income(records: list) -> float:
    """
    收益类（Revenue / NetIncome / EPS）TTM —— 同 OCF 一样用「上年报 + 当期 YTD - 去年同期 YTD」。
    要正确判断 10-Q 的 fp（Q1/Q2/Q3）对应的是 YTD 还是当季增量，可通过 frame 字段或 duration 长度。
    简化版：取最近 10-K 年度值作为 TTM 近似（季度更新会有 1-2 季滞后但数量级正确）。
    """
    if not records:
        return 0.0
    annual = [r for r in records if r.get("form") == "10-K"]
    if annual:
        return float(annual[-1].get("val", 0))
    quarter = [r for r in records if r.get("form") == "10-Q"]
    return float(quarter[-1].get("val", 0)) if quarter else 0.0


def _ttm_from_filings(records: list) -> float:
    """
    现金流 / R&D 等 YTD 累计型科目的 TTM 计算。
    XBRL 中 10-Q 的 OCF 字段通常是「年初至季末累计值」，不能直接 4 季相加。
    优先策略：
      1. 找最近一份 10-K（年度全量）
      2. 加上最近一期 10-Q（YTD 当期）
      3. 减去去年同期 10-Q（YTD 去年同期）
    fallback：取最近 10-K 的值（年度数）。
    """
    if not records:
        return 0.0
    annual = [r for r in records if r.get("form") == "10-K"]
    quarter = [r for r in records if r.get("form") == "10-Q"]
    if not annual:
        return float(quarter[-1].get("val", 0)) if quarter else 0.0
    last_10k = annual[-1]
    last_10k_fy = last_10k.get("fy")
    last_10k_val = float(last_10k.get("val", 0))
    if not quarter:
        return last_10k_val
    # 找最新 10-Q
    latest_q = quarter[-1]
    latest_q_fy = latest_q.get("fy")
    latest_q_fp = latest_q.get("fp")
    # 找去年同期 10-Q（fp 相同，fy=latest_q_fy-1）
    prior_q = next(
        (r for r in reversed(quarter)
         if r.get("fy") == (latest_q_fy - 1 if latest_q_fy else None)
         and r.get("fp") == latest_q_fp),
        None,
    )
    if prior_q and latest_q_fy and latest_q_fy > last_10k_fy:
        return last_10k_val + float(latest_q.get("val", 0)) - float(prior_q.get("val", 0))
    return last_10k_val


def _latest_val(records: list) -> float:
    return float(records[-1].get("val", 0)) if records else 0.0


def fetch_us_snapshot(ticker: str) -> Optional[SecSnapshot]:
    """
    单个 API 调用拿到美股 ticker 的 SEC 结构化财务快照。
    """
    cik_info = ticker_to_cik(ticker)
    if not cik_info:
        return None
    cik = cik_info["cik"]

    targets = []
    for vlist in GAAP_METRICS.values():
        targets.extend(vlist)

    raw = sec_xbrl_facts(cik, metrics=targets)
    m = raw.get("metrics", {})

    def _pick(keys):
        for k in keys:
            if m.get(k):
                return m[k]
        return []

    rev = _pick(GAAP_METRICS["revenue"])
    ni = _pick(GAAP_METRICS["net_income"])
    eps = _pick(GAAP_METRICS["eps_diluted"])
    assets = _pick(GAAP_METRICS["assets"])
    equity = _pick(GAAP_METRICS["equity"])
    rd = _pick(GAAP_METRICS["rd_expense"])
    ocf = _pick(GAAP_METRICS["operating_cash_flow"])

    return SecSnapshot(
        ticker=ticker.upper(),
        cik=cik,
        company=cik_info.get("company", ""),
        revenue_ttm=_ttm_income(rev),
        net_income_ttm=_ttm_income(ni),
        eps_diluted_latest=_ttm_income(eps),
        assets_latest=_latest_val(assets),
        equity_latest=_latest_val(equity),
        rd_expense_ttm=_ttm_from_filings(rd),
        operating_cash_flow_ttm=_ttm_from_filings(ocf),
        history={
            "revenue": rev,
            "net_income": ni,
            "eps_diluted": eps,
            "assets": assets,
            "equity": equity,
            "rd_expense": rd,
            "operating_cash_flow": ocf,
        },
    )
