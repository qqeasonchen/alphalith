"""东方财富数据中心通用客户端（限流 + 防封）。

实战教训（来自 a-stock-data 4.8k stars 项目，2026 实测）：
- 单 IP 每分钟 < 60 次，间隔 ≥ 1.5s
- UA / Referer 必填，且不并发
- 触发风控会返回 412/429/空数据，需要退避

所有 datacenter-web.eastmoney.com 接口必须经过 em_get()。
"""
from __future__ import annotations

import json as _json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_MIN_INTERVAL = 1.5  # 秒；社区实测下界
_lock = threading.Lock()
_last_call_ts = 0.0


def _throttle() -> None:
    """串行化 + 固定休眠，避免触发东财风控。"""
    global _last_call_ts
    with _lock:
        elapsed = time.time() - _last_call_ts
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        _last_call_ts = time.time()


def em_get(
    url: str,
    params: Optional[dict] = None,
    *,
    headers: Optional[dict] = None,
    timeout: float = 12.0,
    retries: int = 2,
) -> Optional[dict]:
    """限流封装。返回 dict 或 None（失败/空）。

    自动处理：
    - URL 拼参（GET）
    - JSON 解析
    - 限流间隔
    - 重试退避
    """
    if params:
        qs = urllib.parse.urlencode(params, doseq=True, safe=",()/")
        full_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"
    else:
        full_url = url

    h = dict(_DEFAULT_HEADERS)
    if headers:
        h.update(headers)

    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            req = urllib.request.Request(full_url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            # 东财接口偶尔返回 jsonp(...)
            if raw.startswith(("jQuery", "jsonpgz", "callback")) and "(" in raw:
                raw = raw[raw.index("(") + 1: raw.rindex(")")]
            return _json.loads(raw)
        except Exception as e:  # noqa: BLE001
            last_err = e
            # 触发风控时退避更长
            time.sleep(2.0 * (attempt + 1))
    return None


def em_table(
    report_name: str,
    *,
    columns: str = "ALL",
    page: int = 1,
    page_size: int = 50,
    sort_col: Optional[str] = None,
    sort_order: int = -1,  # -1 desc, 1 asc
    filters: Optional[str] = None,
    extra: Optional[dict] = None,
) -> list[dict[str, Any]]:
    """datacenter-web 通用查询，返回 list[dict]。

    示例：龙虎榜 report_name="RPT_DAILYBILLBOARD_DETAILSNEW"。
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "pageNumber": page,
        "pageSize": page_size,
        "source": "WEB",
        "client": "WEB",
    }
    if sort_col:
        params["sortColumns"] = sort_col
        params["sortTypes"] = sort_order
    if filters:
        params["filter"] = filters
    if extra:
        params.update(extra)

    res = em_get("https://datacenter-web.eastmoney.com/api/data/v1/get", params)
    if not res or not res.get("success"):
        return []
    data = (res.get("result") or {}).get("data") or []
    return list(data)
