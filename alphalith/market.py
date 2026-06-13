"""
Market detection & symbol normalization.
市场识别与标的代码归一化。
"""
from __future__ import annotations

import re
from enum import Enum


class Market(str, Enum):
    A_STOCK = "a_stock"
    HK_STOCK = "hk_stock"
    US_STOCK = "us_stock"


# 中文公司名 → 标准代码（最小可用词典，后续可扩为本地 JSON）
CN_NAME_MAP: dict[str, str] = {
    "茅台": "600519.SS",
    "贵州茅台": "600519.SS",
    "宁德时代": "300750.SZ",
    "比亚迪": "002594.SZ",
    "工商银行": "601398.SS",
    "腾讯": "0700.HK",
    "腾讯控股": "0700.HK",
    "阿里": "9988.HK",
    "阿里巴巴": "9988.HK",
    "美团": "3690.HK",
    "英伟达": "NASDAQ:NVDA",
    "苹果": "NASDAQ:AAPL",
    "特斯拉": "NASDAQ:TSLA",
    "微软": "NASDAQ:MSFT",
    "谷歌": "NASDAQ:GOOGL",
}


class UnknownSymbolError(ValueError):
    """无法识别的标的（典型场景：中文短语不在词典里却被误判成美股代码）。"""


def detect_market(symbol: str) -> tuple[Market, str]:
    """智能识别市场并归一化代码。

    Examples:
        >>> detect_market("600519")        # ('a_stock', '600519.SS')
        >>> detect_market("茅台")          # ('a_stock', '600519.SS')
        >>> detect_market("0700.HK")       # ('hk_stock', '0700.HK')
        >>> detect_market("NVDA")          # ('us_stock', 'NASDAQ:NVDA')

    Raises:
        UnknownSymbolError: 输入含中文但不在 CN_NAME_MAP 词典里——避免被
        默认分支误判为 NASDAQ 代码（历史 bug：拆股、陈华 → NASDAQ:拆股）。
    """
    s = symbol.strip()
    s_upper = s.upper()

    # 1) 中文名优先：要么命中字典，要么直接报错。绝不能 fall-through 到美股默认值。
    if re.search(r"[\u4e00-\u9fff]", s):
        if s in CN_NAME_MAP:
            return detect_market(CN_NAME_MAP[s])
        raise UnknownSymbolError(
            f"无法识别中文标的『{s}』。请使用代码（如 600519、0700.HK、NVDA）"
            f"或在 alphalith/market.py 的 CN_NAME_MAP 中补录。"
        )

    s = s_upper

    # 2) 显式后缀
    if s.endswith((".SS", ".SH")):
        return Market.A_STOCK, s.replace(".SH", ".SS")
    if s.endswith((".SZ", ".BJ")):
        return Market.A_STOCK, s
    if s.endswith(".HK"):
        return Market.HK_STOCK, s
    if ":" in s:
        return Market.US_STOCK, s

    # 3) 纯数字路由
    if s.isdigit():
        if len(s) == 6:
            if s.startswith(("60", "68", "90", "11", "13")):
                return Market.A_STOCK, f"{s}.SS"
            if s.startswith(("00", "30", "20")):
                return Market.A_STOCK, f"{s}.SZ"
            if s.startswith(("43", "83", "87", "88")):
                return Market.A_STOCK, f"{s}.BJ"
        if len(s) <= 5:
            return Market.HK_STOCK, f"{int(s):04d}.HK"

    # 4) 默认按美股
    return Market.US_STOCK, f"NASDAQ:{s}"
