"""
板块/概念热度 — 题材维度数据

接入策略（按可用性降级）:
1. push2 行业/概念榜（首选，盘中实时；当前网络可能被封）
2. 东财 datacenter 概念资金流（备选，日级）
3. 龙虎榜上榜原因聚合（兜底，从已有 dragon.py 数据中归纳热点）

兼容性: 只覆盖 A 股；港美股不适用。
"""
from __future__ import annotations

import json as _json
import urllib.request
import urllib.error
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from .em import em_get


PUSH_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
TIMEOUT = 10


@dataclass
class HotBoard:
    """板块热度记录。"""
    name: str = ""
    code: str = ""
    change_pct: float = 0.0           # 涨跌幅 %
    main_inflow: float = 0.0          # 主力净流入（万元）
    leading_stock: str = ""           # 领涨股
    source: str = ""                  # push2 / dragon

    @property
    def summary(self) -> str:
        sign = "+" if self.change_pct >= 0 else ""
        emo = "🔴" if self.change_pct >= 0 else "🟢"
        flow = ""
        if self.main_inflow:
            f_sign = "+" if self.main_inflow >= 0 else ""
            flow = f" 主力{f_sign}{self.main_inflow/1e4:.2f}亿"
        lead = f" [领涨: {self.leading_stock}]" if self.leading_stock else ""
        return f"{emo} {self.name} {sign}{self.change_pct:.2f}%{flow}{lead}"


# ────────────────────────────────────────────────────────────
# Source 1: push2 行业/概念榜（首选）
# ────────────────────────────────────────────────────────────
def _push2_clist(fs_value: str, sort_field: str = "f3", page_size: int = 15) -> list[dict]:
    """
    fs_value: m:90+t:3=行业, m:90+t:1=概念, m:90+t:2=地域
    sort_field: f3=涨跌幅, f62=主力净流入
    """
    url = (
        "https://push2.eastmoney.com/api/qt/clist/get?"
        f"pn=1&pz={page_size}&po=1&np=1&fltt=2&invt=2&fid={sort_field}"
        f"&fs={urllib.parse.quote(fs_value)}"
        "&fields=f2,f3,f12,f14,f62,f128,f136,f184"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": PUSH_UA,
            "Accept": "*/*",
            "Referer": "https://data.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = _json.loads(body)
        return ((data.get("data") or {}).get("diff") or [])
    except Exception:
        return []


def fetch_top_industries(page_size: int = 10) -> list[HotBoard]:
    """涨幅榜行业板块（push2）。"""
    rows = _push2_clist("m:90+t:3", "f3", page_size)
    return [
        HotBoard(
            name=r.get("f14", ""),
            code=r.get("f12", ""),
            change_pct=float(r.get("f3") or 0),
            main_inflow=float(r.get("f62") or 0),
            leading_stock=r.get("f128", ""),
            source="push2",
        )
        for r in rows
    ]


def fetch_top_concepts(page_size: int = 10) -> list[HotBoard]:
    """涨幅榜概念板块（push2）。"""
    rows = _push2_clist("m:90+t:1", "f3", page_size)
    return [
        HotBoard(
            name=r.get("f14", ""),
            code=r.get("f12", ""),
            change_pct=float(r.get("f3") or 0),
            main_inflow=float(r.get("f62") or 0),
            leading_stock=r.get("f128", ""),
            source="push2",
        )
        for r in rows
    ]


# ────────────────────────────────────────────────────────────
# Source 2: 龙虎榜上榜原因聚合（兜底）
# ────────────────────────────────────────────────────────────
def fetch_hot_themes_from_dragon(page_size: int = 50) -> list[tuple[str, int]]:
    """
    从最近龙虎榜上榜原因里归纳热点关键词。
    返回: [(关键词, 出现次数), ...] Top N。
    """
    from . import dragon as _dragon
    recs = _dragon.fetch_dragon_list(page_size=page_size)
    counter: Counter = Counter()
    for r in recs:
        reason = r.reason or ""
        # 简化关键词提取：截取常见涨幅模式
        for kw in ("连续三个交易日", "日价格涨幅偏离值", "换手率", "新股", "ST", "退市"):
            if kw in reason:
                counter[kw] += 1
        # 提取百分比标签
        if "20%" in reason:
            counter["大涨上榜(累计20%)"] += 1
        if "30%" in reason:
            counter["巨涨上榜(累计30%)"] += 1
    return counter.most_common(8)


# ────────────────────────────────────────────────────────────
# Agent 摘要（带降级）
# ────────────────────────────────────────────────────────────
def summarize_for_agent() -> str:
    """主入口：尽量返回热点板块；push2 失败则降级到龙虎榜。"""
    industries = fetch_top_industries(5)
    concepts = fetch_top_concepts(5)

    if industries or concepts:
        lines = []
        if industries:
            lines.append("热门行业 Top5: " + " | ".join(b.summary for b in industries))
        if concepts:
            lines.append("热门概念 Top5: " + " | ".join(b.summary for b in concepts))
        return "\n".join(lines)

    # 降级：龙虎榜聚合
    themes = fetch_hot_themes_from_dragon(50)
    if themes:
        return "市场热点(龙虎榜归因): " + " ".join(f"{k}×{v}" for k, v in themes)
    return ""
