"""
Yahoo Finance 期权链 — 美股期权数据（仅美股）

数据源: query2.finance.yahoo.com（公开免鉴权，需 cookie + crumb）
覆盖: 全到期日列表 / calls + puts 完整字段（含 IV、OI、Volume）

设计原则: 与 Alphalith 一致 —— 纯 urllib，无第三方依赖。
"""
from __future__ import annotations

import http.cookiejar
import json as _json
import os
import time
import tempfile
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field
from typing import Optional


# 用极简 UA 避免 Yahoo 针对完整 Safari UA 的反爬
YAHOO_UA = "Mozilla/5.0"
TIMEOUT = 15
CRUMB_CACHE_TTL = 6 * 3600  # 6 小时
CRUMB_CACHE_FILE = os.path.join(tempfile.gettempdir(), "alphalith_yahoo_crumb.json")

# 模块级 cookie + crumb 缓存
_cookiejar: Optional[http.cookiejar.CookieJar] = None
_crumb: Optional[str] = None
_opener: Optional[urllib.request.OpenerDirector] = None


def _load_crumb_cache() -> Optional[tuple]:
    try:
        if not os.path.exists(CRUMB_CACHE_FILE):
            return None
        with open(CRUMB_CACHE_FILE, "r", encoding="utf-8") as f:
            d = _json.load(f)
        if time.time() - d.get("ts", 0) > CRUMB_CACHE_TTL:
            return None
        return d.get("crumb"), d.get("cookie", [])
    except Exception:
        return None


def _save_crumb_cache(crumb: str, cj: http.cookiejar.CookieJar) -> None:
    try:
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain,
                    "path": c.path} for c in cj]
        with open(CRUMB_CACHE_FILE, "w", encoding="utf-8") as f:
            _json.dump({"ts": time.time(), "crumb": crumb, "cookie": cookies}, f)
    except Exception:
        pass


def _ensure_session() -> tuple:
    """
    建立带 cookie+crumb 的 opener。
    Yahoo v7/v10 接口必须先访问 finance.yahoo.com 拿 cookie，
    再通过 query1/v1/test/getcrumb 拿 crumb。
    crumb 缓存 6 小时，命中直接复用。
    """
    global _cookiejar, _crumb, _opener
    if _opener is not None and _crumb:
        return _opener, _crumb

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ("User-Agent", YAHOO_UA),
        ("Accept", "*/*"),
    ]

    # 优先走磁盘缓存
    cached = _load_crumb_cache()
    if cached and cached[0]:
        crumb, cookies = cached
        from http.cookiejar import Cookie
        for ck in cookies:
            cj.set_cookie(Cookie(
                version=0, name=ck["name"], value=ck["value"],
                port=None, port_specified=False, domain=ck["domain"],
                domain_specified=True, domain_initial_dot=ck["domain"].startswith("."),
                path=ck["path"], path_specified=True, secure=False,
                expires=None, discard=False, comment=None, comment_url=None,
                rest={}, rfc2109=False,
            ))
        _cookiejar, _crumb, _opener = cj, crumb, opener
        return opener, crumb

    # Step 1: 主站拿 cookie
    for warm_url in ("https://finance.yahoo.com", "https://fc.yahoo.com"):
        try:
            opener.open(warm_url, timeout=TIMEOUT).read(2048)
            break
        except urllib.error.HTTPError:
            continue
        except (urllib.error.URLError, TimeoutError):
            continue

    # Step 2: 拿 crumb（query1 / query2 都试一遍）
    crumb = None
    for crumb_url in (
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
    ):
        try:
            with opener.open(crumb_url, timeout=TIMEOUT) as resp:
                c = resp.read().decode("utf-8").strip()
            if c and len(c) <= 64:
                crumb = c
                break
        except Exception:
            continue

    if not crumb:
        return None, None

    _save_crumb_cache(crumb, cj)
    _cookiejar = cj
    _crumb = crumb
    _opener = opener
    return opener, crumb


def _val(o, key):
    """Yahoo 字段可能是 dict({raw, fmt}) 或裸值。"""
    v = o.get(key)
    if isinstance(v, dict):
        return v.get("raw")
    return v


# ────────────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────────────
@dataclass
class OptionContract:
    strike: float = 0.0
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open_interest: int = 0
    implied_volatility: float = 0.0
    in_the_money: bool = False
    expiration: str = ""
    contract_symbol: str = ""

    @classmethod
    def from_yahoo(cls, o: dict) -> "OptionContract":
        exp = o.get("expiration")
        exp_str = exp.get("fmt") if isinstance(exp, dict) else (exp or "")
        return cls(
            strike=_val(o, "strike") or 0.0,
            last_price=_val(o, "lastPrice") or 0.0,
            bid=_val(o, "bid") or 0.0,
            ask=_val(o, "ask") or 0.0,
            volume=int(_val(o, "volume") or 0),
            open_interest=int(_val(o, "openInterest") or 0),
            implied_volatility=_val(o, "impliedVolatility") or 0.0,
            in_the_money=bool(o.get("inTheMoney", False)),
            expiration=str(exp_str),
            contract_symbol=o.get("contractSymbol", ""),
        )


@dataclass
class OptionChain:
    symbol: str
    underlying_price: float = 0.0
    expiration_dates: list = field(default_factory=list)  # Unix ts list
    calls: list = field(default_factory=list)             # OptionContract
    puts: list = field(default_factory=list)

    @property
    def call_volume(self) -> int:
        return sum(c.volume for c in self.calls)

    @property
    def put_volume(self) -> int:
        return sum(p.volume for p in self.puts)

    @property
    def put_call_volume_ratio(self) -> float:
        cv = self.call_volume
        return (self.put_volume / cv) if cv else 0.0

    @property
    def call_oi(self) -> int:
        return sum(c.open_interest for c in self.calls)

    @property
    def put_oi(self) -> int:
        return sum(p.open_interest for p in self.puts)

    @property
    def put_call_oi_ratio(self) -> float:
        co = self.call_oi
        return (self.put_oi / co) if co else 0.0

    def atm_iv(self, side: str = "call", window: int = 3) -> float:
        """取最贴近现价的 N 张合约 IV 平均，作为 ATM 隐含波动率代理。"""
        rows = self.calls if side == "call" else self.puts
        if not rows or not self.underlying_price:
            return 0.0
        sorted_by_dist = sorted(rows, key=lambda r: abs(r.strike - self.underlying_price))
        ivs = [r.implied_volatility for r in sorted_by_dist[:window] if r.implied_volatility > 0]
        return sum(ivs) / len(ivs) if ivs else 0.0

    @property
    def summary(self) -> str:
        return (
            f"{self.symbol} 现价 {self.underlying_price:.2f} | "
            f"PCR(vol) {self.put_call_volume_ratio:.2f} | "
            f"PCR(OI) {self.put_call_oi_ratio:.2f} | "
            f"ATM IV {self.atm_iv()*100:.1f}%"
        )


# ────────────────────────────────────────────────────────────
# 主 API
# ────────────────────────────────────────────────────────────
def fetch_option_chain(symbol: str, expiration: Optional[int] = None) -> Optional[OptionChain]:
    """
    Yahoo 期权链。仅美股（港股代码如 0700.HK 无期权数据）。
    symbol: 美股 ticker，如 "AAPL", "TSLA"
    expiration: Unix timestamp（不传则返回最近到期日 + 所有到期日列表）
    """
    opener, crumb = _ensure_session()
    if not opener or not crumb:
        return None

    params = {"crumb": crumb}
    if expiration:
        params["date"] = str(expiration)

    url = (f"https://query2.finance.yahoo.com/v7/finance/options/{urllib.parse.quote(symbol)}"
           f"?{urllib.parse.urlencode(params)}")

    try:
        with opener.open(url, timeout=TIMEOUT) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, _json.JSONDecodeError, TimeoutError):
        return None

    result = data.get("optionChain", {}).get("result", [])
    if not result:
        return None
    oc = result[0]

    options_list = oc.get("options") or [{}]
    options = options_list[0] if options_list else {}

    quote = oc.get("quote", {})
    chain = OptionChain(
        symbol=symbol.upper(),
        underlying_price=quote.get("regularMarketPrice") or 0.0,
        expiration_dates=oc.get("expirationDates", []),
        calls=[OptionContract.from_yahoo(o) for o in options.get("calls", [])],
        puts=[OptionContract.from_yahoo(o) for o in options.get("puts", [])],
    )
    return chain


def option_sentiment(symbol: str) -> dict:
    """
    单 API 给 Alphalith 情绪层使用：返回美股期权关键情绪指标。
    PCR > 1：看跌情绪占优；PCR < 0.7：看多情绪占优。
    """
    chain = fetch_option_chain(symbol)
    if chain is None:
        return {"available": False}
    return {
        "available": True,
        "symbol": chain.symbol,
        "underlying_price": chain.underlying_price,
        "put_call_volume_ratio": round(chain.put_call_volume_ratio, 3),
        "put_call_oi_ratio": round(chain.put_call_oi_ratio, 3),
        "atm_iv_call": round(chain.atm_iv("call"), 4),
        "atm_iv_put": round(chain.atm_iv("put"), 4),
        "call_volume": chain.call_volume,
        "put_volume": chain.put_volume,
        "expiration_count": len(chain.expiration_dates),
    }
