"""
Alphalith GUI — 零外部依赖的投研工作台。

Architecture:
  Python http.server (stdlib) + 单文件 HTML (inline CSS/JS, Canvas charts)

Layout:
  ┌──────────────────────────────────────────────┐
  │  Top Nav: Research | Signals | Backtest | Pf │
  ├──────────────┬───────────────────────────────┤
  │  Chat Panel  │  Results Area                 │
  │  (AI 对话)    │  - Chart / Signal / Status    │
  │              │                               │
  │  [Settings]  │                               │
  │  [Input...]  │                               │
  └──────────────┴───────────────────────────────┘

Usage:
  alphalith gui [--port PORT]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

CONFIG_PATH = Path.home() / ".alphalith" / "config.json"
USERS_PATH = Path.home() / ".alphalith" / "users.json"
GUI_DIR = Path(__file__).parent

# In-memory session store: token -> {"username": str, "created": ts, "expires": ts}
_SESSIONS: dict = {}
_SESSIONS_LOCK = threading.Lock()
SESSION_TTL = 7 * 24 * 3600  # 7 天

# ====================== 用户认证工具 ======================

def _load_users() -> dict:
    if USERS_PATH.exists():
        try:
            with open(USERS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_users(users: dict) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    tmp.replace(USERS_PATH)
    try:
        os.chmod(USERS_PATH, 0o600)
    except OSError:
        pass


# 默认管理员账号
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "alphalith"


def _ensure_default_admin() -> dict:
    """如果用户库为空，创建默认管理员账号。返回最新 users 字典。"""
    users = _load_users()
    if users:
        return users
    pwd_hash, salt = _hash_password(DEFAULT_ADMIN_PASSWORD)
    users = {
        DEFAULT_ADMIN_USERNAME: {
            "username": DEFAULT_ADMIN_USERNAME,
            "display_name": "管理员",
            "password_hash": pwd_hash,
            "salt": salt,
            "role": "admin",
            "created_at": time.time(),
            "is_default": True,
        }
    }
    _save_users(users)
    return users


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """PBKDF2-SHA256 加盐哈希。返回 (hash_hex, salt_hex)。"""
    if salt is None:
        salt = secrets.token_hex(16)
    salt_bytes = bytes.fromhex(salt)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 200_000)
    return dk.hex(), salt


def _verify_password(password: str, hash_hex: str, salt: str) -> bool:
    calc_hex, _ = _hash_password(password, salt)
    return secrets.compare_digest(calc_hex, hash_hex)


def _create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {
            "username": username,
            "created": now,
            "expires": now + SESSION_TTL,
        }
    return token


def _get_session(token: str) -> dict | None:
    if not token:
        return None
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(token)
        if not sess:
            return None
        if sess["expires"] < time.time():
            _SESSIONS.pop(token, None)
            return None
        return sess


def _delete_session(token: str) -> None:
    if not token:
        return
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def _validate_username(name: str) -> str | None:
    if not name or not isinstance(name, str):
        return "用户名不能为空"
    name = name.strip()
    if len(name) < 2 or len(name) > 32:
        return "用户名长度需 2-32 个字符"
    if not re.match(r"^[A-Za-z0-9_\u4e00-\u9fa5][A-Za-z0-9_\-\.\u4e00-\u9fa5]*$", name):
        return "用户名只能包含字母、数字、下划线、连字符、点和中文"
    return None


def _validate_password(pwd: str) -> str | None:
    if not pwd or not isinstance(pwd, str):
        return "密码不能为空"
    if len(pwd) < 6 or len(pwd) > 128:
        return "密码长度需 6-128 个字符"
    return None

DEFAULT_CONFIG = {
    "provider": "deepseek",
    "model_name": "deepseek-v4-pro",
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.7,
    "data_source": "auto",
    "data_source_order": ["sina", "eastmoney", "tencent", "akshare", "yfinance"],
    "custom_sources": {},
    "providers": {
        "deepseek": {
            "name": "DeepSeek 官方",
            "base_url": "https://api.deepseek.com/v1",
            "key_env": "DEEPSEEK_API_KEY",
            "models": [
                {"id": "deepseek-v4-pro",      "name": "V4 Pro · 旗舰",      "desc": "万亿MoE 100万ctx · 编程推理SOTA"},
                {"id": "deepseek-v4-flash",    "name": "V4 Flash · 高速",    "desc": "极致性价比 轻量高速"},
            ],
        },
        "bailian": {
            "name": "阿里云百炼 · Qwen3",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "key_env": "DASHSCOPE_API_KEY",
            "models": [
                {"id": "qwen3.6-max-preview",  "name": "Qwen3.6 Max · 旗舰",  "desc": "最新旗舰 · 需 Coding Plan"},
                {"id": "qwen3-coder-plus",     "name": "Qwen3 Coder Plus · 编程", "desc": "编程专精 · 需 Coding Plan"},
                {"id": "qwen3.5-omni",         "name": "Qwen3.5 Omni · 多模态", "desc": "图文理解 128K ctx"},
            ],
        },
        "openai": {
            "name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "key_env": "OPENAI_API_KEY",
            "models": [
                {"id": "gpt-5.5",              "name": "GPT-5.5 · 旗舰",       "desc": "100万ctx 最强通用"},
                {"id": "gpt-5.6",              "name": "GPT-5.6 · 最新预览",   "desc": "150万ctx 即将发布"},
                {"id": "o4-mini",              "name": "o4-mini · 推理",       "desc": "推理专精 高性价比"},
            ],
        },
        "claude": {
            "name": "Anthropic Claude",
            "base_url": "https://api.anthropic.com/v1",
            "key_env": "ANTHROPIC_API_KEY",
            "models": [
                {"id": "claude-opus-4-7-20250530",  "name": "Opus 4.7 · 旗舰",  "desc": "最强编码 SWE-bench 82% · 需代理"},
                {"id": "claude-sonnet-4-6-20250217","name": "Sonnet 4.6 · 主力", "desc": "编程高效 性价比 · 需代理"},
            ],
        },
        "gemini": {
            "name": "Google Gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "key_env": "GEMINI_API_KEY",
            "models": [
                {"id": "gemini-3.1-pro",        "name": "3.1 Pro · 推理",      "desc": "复杂推理 Agent · 需代理"},
                {"id": "gemini-3.5-flash",      "name": "3.5 Flash · 最新",    "desc": "Agent & Coding SOTA · 需代理"},
            ],
        },
        "zhipu": {
            "name": "智谱 GLM",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "key_env": "ZHIPU_API_KEY",
            "models": [
                {"id": "glm-5.2",              "name": "GLM-5.2 · 最新",       "desc": "6月13日发布 1M ctx 最强国产Coding · Coding Plan"},
                {"id": "glm-5.2-flash",        "name": "GLM-5.2 Flash · 高速", "desc": "轻量高速 即将发布"},
            ],
        },
        "kimi": {
            "name": "Kimi 月之暗面",
            "base_url": "https://api.moonshot.cn/v1",
            "key_env": "MOONSHOT_API_KEY",
            "models": [
                {"id": "kimi-k2.7-code",       "name": "K2.7 Code · 最新编程", "desc": "6月12日发布 1T参数 编程专精 · Coding Plan"},
                {"id": "kimi-k2.6",            "name": "K2.6 · 旗舰",          "desc": "256K ctx 通用旗舰"},
            ],
        },
        "volcengine": {
            "name": "火山方舟 · 豆包",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "key_env": "ARK_API_KEY",
            "models": [
                {"id": "doubao-pro-256k",      "name": "豆包 Pro 256K · 旗舰", "desc": "字节生态 · Coding Plan"},
                {"id": "doubao-lite-128k",     "name": "豆包 Lite 128K · 轻量","desc": "高速低价 · Coding Plan"},
            ],
        },
        "qianfan": {
            "name": "百度千帆",
            "base_url": "https://qianfan.baidubce.com/v2",
            "key_env": "QIANFAN_API_KEY",
            "models": [
                {"id": "ernie-4.5-8k",         "name": "ERNIE 4.5 · 旗舰",     "desc": "百度旗舰"},
                {"id": "ernie-speed-128k",     "name": "ERNIE Speed · 高速",   "desc": "高并发低延迟"},
            ],
        },
        "tencent-coding": {
            "name": "腾讯 Coding Plan",
            "base_url": "https://api.lkeap.cloud.tencent.com/coding/v3",
            "key_env": "TENCENT_CODING_API_KEY",
            "models": [
                {"id": "tc-code-latest",         "name": "Auto · 智能路由",      "desc": "自动匹配最优模型 · Coding Plan"},
                {"id": "glm-5",                  "name": "GLM-5 · 智谱",         "desc": "Coding Plan 代理 · 智谱旗舰"},
                {"id": "kimi-k2.5",              "name": "Kimi K2.5 · 月暗",     "desc": "Coding Plan 代理 · 月暗旗舰"},
                {"id": "minimax-m2.5",           "name": "MiniMax M2.5 · 海螺",  "desc": "Coding Plan 代理 · MiniMax旗舰"},
            ],
        },
        "tencent-token": {
            "name": "腾讯 Token Plan · 通用",
            "base_url": "https://api.lkeap.cloud.tencent.com/plan/v3",
            "key_env": "TENCENT_TOKEN_API_KEY",
            "models": [
                {"id": "tc-code-latest",         "name": "Auto · 智能路由",      "desc": "自动匹配最优模型 · Token Plan"},
                {"id": "glm-5.1",                "name": "GLM-5.1 · 智谱最新",   "desc": "Token Plan · 智谱最新旗舰"},
                {"id": "glm-5",                  "name": "GLM-5 · 智谱",         "desc": "Token Plan · 智谱旗舰"},
                {"id": "kimi-k2.5",              "name": "Kimi K2.5 · 月暗",     "desc": "Token Plan · 月暗旗舰"},
                {"id": "minimax-m2.7",           "name": "MiniMax M2.7 · 海螺最新","desc": "Token Plan · MiniMax最新"},
                {"id": "minimax-m2.5",           "name": "MiniMax M2.5 · 海螺",  "desc": "Token Plan · MiniMax旗舰"},
            ],
        },
        "tencent-hy": {
            "name": "腾讯 Hy Token Plan",
            "base_url": "https://api.lkeap.cloud.tencent.com/plan/v3",
            "key_env": "TENCENT_HY_API_KEY",
            "models": [
                {"id": "hy3-preview",            "name": "Hy3 Preview · 旗舰",   "desc": "295B/21B MoE · 256K ctx · Agent 工作负载"},
            ],
        },
        "minimax": {
            "name": "MiniMax",
            "base_url": "https://api.minimax.chat/v1",
            "key_env": "MINIMAX_API_KEY",
            "models": [
                {"id": "minimax-m1",            "name": "MiniMax-M1 · 旗舰",    "desc": "长上下文 推理"},
            ],
        },
        "stepfun": {
            "name": "阶跃星辰",
            "base_url": "https://api.stepfun.com/v1",
            "key_env": "STEPFUN_API_KEY",
            "models": [
                {"id": "step-3.5-flash",         "name": "Step 3.5 Flash · 高速", "desc": "轻量高效"},
            ],
        },
        "siliconflow": {
            "name": "硅基流动 (国内直连)",
            "base_url": "https://api.siliconflow.cn/v1",
            "key_env": "SILICONFLOW_API_KEY",
            "models": [
                {"id": "deepseek-ai/DeepSeek-V4-Pro",   "name": "DeepSeek V4 Pro (代理)",  "desc": "国内直连 免翻墙"},
                {"id": "Qwen/Qwen3.6-Max",              "name": "Qwen3.6 Max (代理)",      "desc": "国内直连 免翻墙"},
                {"id": "Pro/deepseek-ai/DeepSeek-V4-Pro","name": "DS V4 Pro (付费增强)",    "desc": "硅基增强版 效果更优"},
            ],
        },
    },
    "data": {
        "a_share": "akshare",
        "hk_stock": "yfinance",
        "us_stock": "yfinance",
    },
    "agents": {
        "market_scanner": True,
        "sentiment_analyzer": True,
        "technical_trader": True,
    },
    "advanced": {
        "max_agents": 5,
        "timeout": 120,
        "retry": 3,
        "cache_ttl_h": 24,
    },
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            _deep_merge(merged, saved)
            return merged
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


class GuiHandler(BaseHTTPRequestHandler):
    """Serves app.html and API endpoints."""

    server_config: dict = {}

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def _send_json(self, data, status=200, set_cookie: str | None = None, clear_cookie: bool = False):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Credentials", "true")
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"alphalith_session={set_cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}",
            )
        if clear_cookie:
            self.send_header(
                "Set-Cookie",
                "alphalith_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
            )
        self.end_headers()
        self.wfile.write(body)

    def _get_cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                if k == name:
                    return v
        return ""

    def _current_user(self) -> dict | None:
        token = self._get_cookie("alphalith_session")
        return _get_session(token)

    def _send_html(self, html: str, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, msg, status=500):
        self._send_json({"error": msg}, status)

    def _read_body(self) -> bytes:
        cl = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(cl)

    def _send_sse_headers(self):
        """Server-Sent Events 流式响应头。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse_send(self, event: str, data: dict):
        """发送一个 SSE 事件帧。"""
        try:
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            html_path = GUI_DIR / "app.html"
            if html_path.exists():
                self._send_html(html_path.read_text(encoding="utf-8"))
            else:
                self._send_error_json("app.html not found", 404)
            return

        if path == "/api/config":
            self._send_json(load_config())
            return

        if path == "/api/auth/me":
            sess = self._current_user()
            if sess:
                users = _load_users()
                u = users.get(sess["username"], {})
                self._send_json({
                    "logged_in": True,
                    "username": sess["username"],
                    "display_name": u.get("display_name") or sess["username"],
                    "role": u.get("role", "user"),
                    "is_default": bool(u.get("is_default")),
                    "created_at": u.get("created_at"),
                    "user_count": len(users),
                })
            else:
                users = _load_users()
                self._send_json({
                    "logged_in": False,
                    "user_count": len(users),
                    "has_default_admin": any(u.get("is_default") for u in users.values()),
                })
            return

        if path == "/api/auth/logout":
            token = self._get_cookie("alphalith_session")
            _delete_session(token)
            self._send_json({"ok": True}, clear_cookie=True)
            return

        if path == "/api/agents/status":
            try:
                from ..agents import AgentPool
                pool = AgentPool()
                statuses = [
                    {"name": a.name, "status": a.status, "last_run": str(a.last_run) if a.last_run else None}
                    for a in pool.agents
                ]
                self._send_json({"agents": statuses})
            except Exception as e:
                self._send_json({"agents": [], "error": str(e)})
            return

        if path == "/api/history":
            try:
                qs = parse_qs(parsed.query)
                sym = qs.get("symbol", [None])[0]
                limit = int(qs.get("limit", ["50"])[0])
                from ..journal import history as jnl_history
                rows = jnl_history(symbol=sym, limit=limit)
                self._send_json({"history": rows, "total": len(rows)})
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/review":
            try:
                qs = parse_qs(parsed.query)
                sym = qs.get("symbol", [None])[0]
                from ..journal import review as jnl_review
                result = jnl_review(symbol=sym)
                self._send_json(result)
            except Exception as e:
                self._send_error_json(str(e))
            return

        self._send_error_json("Not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                save_config(body)
                self._send_json({"ok": True, "path": str(CONFIG_PATH)})
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
            return

        if path == "/api/auth/register":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""
                display_name = (body.get("display_name") or username).strip()

                err = _validate_username(username) or _validate_password(password)
                if err:
                    self._send_error_json(err, 400)
                    return

                users = _load_users()
                if username in users:
                    self._send_error_json("用户名已存在", 409)
                    return

                hash_hex, salt = _hash_password(password)
                users[username] = {
                    "password_hash": hash_hex,
                    "salt": salt,
                    "display_name": display_name,
                    "created_at": int(time.time()),
                }
                _save_users(users)

                token = _create_session(username)
                self._send_json({
                    "ok": True,
                    "username": username,
                    "display_name": display_name,
                }, set_cookie=token)
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/auth/login":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""

                if not username or not password:
                    self._send_error_json("用户名和密码不能为空", 400)
                    return

                users = _load_users()
                u = users.get(username)
                if not u:
                    self._send_error_json("用户名或密码错误", 401)
                    return

                if not _verify_password(password, u["password_hash"], u["salt"]):
                    self._send_error_json("用户名或密码错误", 401)
                    return

                token = _create_session(username)
                self._send_json({
                    "ok": True,
                    "username": username,
                    "display_name": u.get("display_name") or username,
                }, set_cookie=token)
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/resolve":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                # 兼容前端发送的 query 字段，以及直接使用的 text 字段
                text = body.get("query", body.get("text", ""))
                # Try to extract a symbol from natural language
                from ..market import detect_market, CN_NAME_MAP
                
                # 1) Try exact CN name match first
                for cn_name, code in CN_NAME_MAP.items():
                    if cn_name in text:
                        market, sym = detect_market(code)
                        self._send_json({"symbol": code, "market": market, "display": cn_name})
                        return
                
                # 2) Try regex extraction
                import re
                # A-share 6-digit code
                m = re.search(r"\b(\d{6})\b", text)
                if m:
                    market, sym = detect_market(m.group(1))
                    self._send_json({"symbol": sym, "market": market, "display": m.group(1)})
                    return
                # HK stock: 4-5 digits .HK
                m = re.search(r"\b(\d{4,5})\.HK\b", text, re.IGNORECASE)
                if m:
                    market, sym = detect_market(m.group(0))
                    self._send_json({"symbol": sym, "market": market, "display": m.group(0)})
                    return
                # US stock: 2-5 uppercase letters
                m = re.search(r"\b([A-Z]{2,5})\b(?!\.)", text)
                if m:
                    market, sym = detect_market(m.group(1))
                    self._send_json({"symbol": sym, "market": market, "display": m.group(1)})
                    return
                
                self._send_json({"error": "Could not resolve symbol from text", "symbol": "", "market": ""})
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/chat":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                # 兼容前端发送的 message 字段，以及直接使用的 prompt 字段
                prompt = body.get("message", body.get("prompt", ""))
                system = body.get("system", "你是专业的量化投资研究助手，提供数据驱动的分析与建议。")
                if not prompt:
                    self._send_error_json("prompt is required", 400)
                    return

                from ..llm import get_llm
                llm = get_llm()
                reply = llm.chat(prompt, system=system)
                self._send_json({
                    "reply": reply,
                    "usage": {
                        "calls": llm.usage.calls,
                        "total_tokens": llm.usage.total_tokens,
                    },
                })
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/analyze":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbol = body.get("symbol", "")
                depth = body.get("depth", "standard")
                if not symbol:
                    self._send_error_json("symbol is required", 400)
                    return

                from ..core import analyze as run_analyze
                decision = run_analyze(symbol, depth=depth, persist=True)
                self._send_json({
                    "id": decision.id,
                    "symbol": decision.symbol,
                    "market": decision.market.value,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "entry_price": decision.entry_price,
                    "stop_loss": decision.stop_loss,
                    "take_profit": decision.take_profit,
                    "suggested_shares": decision.suggested_shares,
                    "agent_reports": [
                        {"name": r.name, "stance": r.stance, "confidence": r.confidence, "summary": r.summary}
                        for r in decision.agent_reports
                    ] if decision.agent_reports else [],
                    "debate": [
                        {"bull": d.bull, "bear": d.bear}
                        for d in decision.debate
                    ] if decision.debate else [],
                    "risk_review": decision.risk_review,
                    "reasoning": decision.reasoning,
                    "market_warnings": list(decision.market_warnings or []),
                    "fees": {
                        "commission": decision.fees.commission,
                        "stamp_tax": decision.fees.stamp_tax,
                        "transfer_fee": decision.fees.transfer_fee,
                        "sec_fee": decision.fees.sec_fee,
                        "other": decision.fees.other,
                        "total": decision.fees.total,
                        "breakeven_pct": decision.fees.breakeven_pct,
                    } if decision.fees else {},
                    "currency": decision.currency.value if hasattr(decision.currency, "value") else str(decision.currency),
                    "adp": decision.to_adp_json(),
                    "extra": decision.extra,
                })
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/analyze/stream":
            # SSE 流式分析：拆解 analyze() 各阶段并实时回传
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbol = body.get("symbol", "")
                depth = body.get("depth", "standard")
                persist = bool(body.get("persist", True))
                if not symbol:
                    self._send_error_json("symbol is required", 400)
                    return
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
                return

            self._send_sse_headers()
            try:
                self._sse_send("progress", {"stage": "init", "pct": 5, "msg": f"准备分析 {symbol}..."})

                from ..data import load_market_data
                from ..rules import get_rules
                from ..llm import get_llm
                from ..agents import run_analysts, run_debate
                from ..core import _decide_action, _make_id
                from ..schema import Decision, FeeBreakdown, AgentReport
                from .. import journal

                self._sse_send("progress", {"stage": "fetch", "pct": 15, "msg": "抓取行情/新闻/基本面..."})
                md = load_market_data(symbol)
                rules = get_rules(md.quote.market)
                llm = get_llm()
                self._sse_send("quote", {
                    "symbol": md.quote.symbol,
                    "name": md.quote.name,
                    "market": md.quote.market.value,
                    "price": md.quote.price,
                    "prev_close": md.quote.prev_close,
                    "change_pct": md.quote.change_pct,
                    "volume": md.quote.volume,
                    "source": md.quote.source,
                    "history_summary": md.history_summary,
                    "fundamental_note": md.fundamental_note,
                    "sentiment_note": md.sentiment_note,
                    "news_headlines": list(md.news_headlines[:5]),
                })

                # 1) 4 分析师 — 逐个流式
                from ..agents import _make_snapshot, _ANALYST_PROMPT, _FOCUS, _parse
                snapshot = _make_snapshot(md)
                reports = []
                roles = list(_FOCUS.keys())
                for idx, (role, focus) in enumerate(_FOCUS.items()):
                    self._sse_send("progress", {
                        "stage": "analyst",
                        "pct": 20 + int(idx * 12),
                        "msg": f"调用 {role} ({idx+1}/{len(roles)})...",
                        "agent": role,
                    })
                    prompt = _ANALYST_PROMPT.format(
                        role=role, symbol=md.quote.symbol, snapshot=snapshot, focus=focus
                    )
                    try:
                        reply = llm.chat(
                            prompt,
                            system="你是严谨、可量化的金融分析师。只引用快照中的事实，禁止虚构数字。",
                        )
                        rep = _parse(reply, role)
                    except Exception as e:
                        rep = AgentReport(name=role, stance="neutral", confidence=0.5,
                                          summary=f"调用失败({e.__class__.__name__})，已降级。")
                    reports.append(rep)
                    self._sse_send("analyst", {
                        "name": rep.name,
                        "stance": rep.stance,
                        "confidence": rep.confidence,
                        "summary": rep.summary,
                    })

                # 2) 多空辩论 — 逐轮流式
                rounds = {"quick": 0, "standard": 1, "deep": 3}.get(depth, 1)
                debates = []
                if rounds > 0:
                    summary = "\n".join(
                        f"- {r.name}：{r.stance} ({r.confidence:.0%}) {r.summary}" for r in reports
                    )
                    last_bull = ""
                    last_bear = ""
                    for i in range(rounds):
                        self._sse_send("progress", {
                            "stage": "debate",
                            "pct": 70 + int(i * 8),
                            "msg": f"多空辩论 第 {i+1} 轮 (共 {rounds} 轮)...",
                            "round": i + 1,
                            "total_rounds": rounds,
                        })
                        rebuttal = f"\n上一轮对手观点（请反驳）：{last_bear}\n" if i > 0 else ""
                        try:
                            bull = llm.chat(
                                f"你是「看多研究员」。\n\n【市场快照】\n{snapshot}\n\n"
                                f"【4 位分析师结论】\n{summary}\n{rebuttal}\n"
                                f"请给出 80 字以内的看多论点，必须引用快照中的具体数字。",
                                system="只输出论点本身，不要前缀，不要客套。",
                            )
                            last_bull = bull.strip()[:300]
                        except Exception as e:
                            last_bull = f"看多调用失败({e.__class__.__name__})"
                        self._sse_send("debate_bull", {"round": i + 1, "bull": last_bull})

                        try:
                            bear = llm.chat(
                                f"你是「看空研究员」。\n\n【市场快照】\n{snapshot}\n\n"
                                f"【4 位分析师结论】\n{summary}\n"
                                f"\n上一轮对手观点（请反驳）：{last_bull}\n"
                                f"请给出 80 字以内的看空论点，必须引用快照中的具体数字。",
                                system="只输出论点本身，不要前缀，不要客套。",
                            )
                            last_bear = bear.strip()[:300]
                        except Exception as e:
                            last_bear = f"看空调用失败({e.__class__.__name__})"
                        self._sse_send("debate_bear", {"round": i + 1, "bear": last_bear})

                        from ..schema import DebateRound
                        debates.append(DebateRound(bull=last_bull, bear=last_bear))

                # 3) 决策合成
                self._sse_send("progress", {"stage": "synth", "pct": 92, "msg": "综合决策与风控复核..."})
                action, confidence = _decide_action(reports, debates)
                entry = md.quote.price
                stop = entry * (0.97 if md.quote.market.value == "a_stock" else 0.95)
                target = entry * 1.06
                raw_shares = max(int(10000 / entry), 1) if action == "buy" else 0
                shares = rules.round_lot(md.quote.symbol, raw_shares)
                amount = entry * shares
                fee = rules.calc_fee(amount, "buy" if action == "buy" else "sell", shares)
                fb = FeeBreakdown(
                    commission=fee.commission, stamp_tax=fee.stamp_tax,
                    transfer_fee=fee.transfer_fee, sec_fee=fee.sec_fee,
                    other=fee.other, total=fee.total,
                    breakeven_pct=(fee.total / amount * 100) if amount else 0.0,
                )
                warns = rules.warnings(md.quote.symbol, md.quote.price, md.quote.prev_close)
                risk = "通过：仓位、止损、规则约束均符合默认风控"
                if action == "buy" and shares == 0:
                    action = "hold"
                    risk = "拒绝：建议手数 < 最小交易单位，自动改为 hold"

                decision = Decision(
                    id=_make_id(md.quote.symbol),
                    symbol=md.quote.symbol,
                    market=md.quote.market,
                    currency=rules.currency,  # type: ignore[arg-type]
                    action=action,  # type: ignore[arg-type]
                    confidence=confidence,
                    suggested_shares=shares,
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=target,
                    agent_reports=reports,
                    debate=debates,
                    risk_review=risk,
                    reasoning="多智能体投研委员会综合 4 维分析与多空辩论给出决策。",
                    market_warnings=warns,
                    fees=fb,
                    extra={
                        "depth": depth,
                        "llm": llm.name,
                        "data_source": md.quote.source,
                        "llm_calls": llm.usage.calls,
                        "llm_prompt_tokens": llm.usage.prompt_tokens,
                        "llm_completion_tokens": llm.usage.completion_tokens,
                        "llm_total_tokens": llm.usage.total_tokens,
                        "llm_tokens_estimated": llm.usage.estimated,
                    },
                )
                if persist:
                    journal.save(decision)

                self._sse_send("progress", {"stage": "done", "pct": 100, "msg": "✅ 分析完成"})
                self._sse_send("done", {
                    "id": decision.id,
                    "symbol": decision.symbol,
                    "market": decision.market.value,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "entry_price": decision.entry_price,
                    "stop_loss": decision.stop_loss,
                    "take_profit": decision.take_profit,
                    "suggested_shares": decision.suggested_shares,
                    "risk_review": decision.risk_review,
                    "reasoning": decision.reasoning,
                    "market_warnings": list(decision.market_warnings or []),
                    "fees": {
                        "commission": fb.commission, "stamp_tax": fb.stamp_tax,
                        "transfer_fee": fb.transfer_fee, "sec_fee": fb.sec_fee,
                        "other": fb.other, "total": fb.total,
                        "breakeven_pct": fb.breakeven_pct,
                    },
                    "currency": decision.currency.value if hasattr(decision.currency, "value") else str(decision.currency),
                    "adp": decision.to_adp_json(),
                    "extra": decision.extra,
                })
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                try:
                    self._sse_send("error", {"message": str(e)})
                except Exception:
                    pass
            return

        if path == "/api/backtest":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbol = body.get("symbol", "600519")
                # 兼容前端发送的 period_days 和 days 两种字段名
                days = int(body.get("period_days", body.get("days", 90)))
                horizon = int(body.get("horizon", body.get("horizon", 5)))
                strategy = body.get("strategy", "ma_cross")

                from ..backtest import run_backtest, to_dict
                r = run_backtest(symbol, days=days, horizon=horizon, strategy=strategy)
                self._send_json(to_dict(r))
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/compare":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbols = body.get("symbols", [])
                symbol = body.get("symbol", "")
                days = int(body.get("days", body.get("period_days", 90)))
                horizon = int(body.get("horizon", body.get("horizon", 5)))
                strategy = body.get("strategy", "ma_cross")

                from ..backtest import run_backtest, to_dict

                results = []
                # 多标的对比（前端用法：/compare 标的A 标的B）
                if isinstance(symbols, list) and len(symbols) >= 2:
                    for sym in symbols[:5]:
                        try:
                            r = run_backtest(sym, days=days, horizon=horizon, strategy=strategy)
                            results.append(to_dict(r))
                        except Exception as e:
                            results.append({"symbol": sym, "error": str(e)})
                else:
                    # 单标的多策略对比（原始用法，保持兼容）
                    strategy_a = body.get("strategy_a", "ma_cross")
                    strategy_b = body.get("strategy_b", "momentum")
                    sym = symbol or (symbols[0] if symbols else "600519")
                    r_a = run_backtest(sym, days=days, horizon=horizon, strategy=strategy_a)
                    r_b = run_backtest(sym, days=days, horizon=horizon, strategy=strategy_b)
                    results = [to_dict(r_a), to_dict(r_b)]

                # 生成 LLM 对比评语
                llm_review = ""
                valid = [r for r in results if "error" not in r]
                if len(valid) >= 2:
                    try:
                        from ..llm import get_llm
                        llm = get_llm()
                        summary = "\n".join([
                            f"【{r.get('symbol', '?')}】总收益:{r.get('total_return_pct',0):+.2f}%, "
                            f"Sharpe:{r.get('sharpe_ratio',0):.2f}, 最大回撤:{r.get('max_drawdown_pct',0):.2f}%"
                            for r in valid
                        ])
                        prompt = (
                            f"你是量化策略评审专家。以下是回测结果对比：\n{summary}\n\n"
                            f"请用 2-4 句话给出专业评语：哪个表现更优？有什么风险提示？"
                            f"直接输出中文，不要 markdown。"
                        )
                        llm_review = llm.chat(prompt, system="只输出中文评语，不要客套。")
                    except Exception:
                        pass

                self._send_json({
                    "results": results,
                    "llm_review": llm_review,
                })
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/signals":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbols = body.get("symbols", [])

                from ..journal import history as jnl_history

                signals = []
                for sym in (symbols or [])[:10]:
                    rows = jnl_history(symbol=sym, limit=1)
                    if rows:
                        r = rows[0]
                        signals.append({
                            "symbol": r.get("symbol", sym),
                            "signal": r.get("action", "hold"),
                            "summary": r.get("reasoning", r.get("notes", "")),
                            "confidence": r.get("confidence", 0.5),
                            "timestamp": r.get("date", r.get("ts", "")),
                        })
                    else:
                        signals.append({
                            "symbol": sym,
                            "signal": "unknown",
                            "summary": "暂无信号数据，请先运行分析。",
                            "confidence": 0.0,
                            "timestamp": "",
                        })

                self._send_json({"signals": signals})
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/dashboard":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbols = body.get("symbols", [])
                if isinstance(symbols, str):
                    symbols = [s.strip() for s in symbols.split(",") if s.strip()]

                from ..dashboard import DashboardConfig, render_dashboard
                cfg = DashboardConfig(symbols=symbols)
                html = render_dashboard(cfg)
                self._send_json({"html": html})
            except Exception as e:
                self._send_error_json(str(e))
            return

        self._send_error_json("Not found", 404)


def start_gui(port: int = 8888, open_browser: bool = True):
    """Start the Alphalith GUI server."""
    config = load_config()

    # 首次启动自动创建默认管理员
    users = _ensure_default_admin()
    has_default_admin = any(u.get("is_default") for u in users.values())

    server = HTTPServer(("127.0.0.1", port), GuiHandler)
    server.server_config = config

    url = f"http://127.0.0.1:{port}"

    def _serve():
        print(f"\n  Alphalith GUI · AI 投研工作台")
        print(f"  ─────────────────────────────")
        print(f"  地址：{url}")
        print(f"  配置：{CONFIG_PATH}")
        if has_default_admin:
            print(f"  默认账号：{DEFAULT_ADMIN_USERNAME}  密码：{DEFAULT_ADMIN_PASSWORD}")
            print(f"  ⚠️  首次登录后请尽快修改密码")
        print(f"  按 Ctrl+C 停止服务\n")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            print("\n  GUI 服务已停止")

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    if open_browser:
        webbrowser.open(url)

    return server, t
