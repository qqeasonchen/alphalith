"""
Alphalith · 慧投
AI 投研委员会 — 多智能体 LLM 投资决策框架
The Bedrock of AI-Driven Alpha
"""

__version__ = "0.2.0"
__author__ = "Alphalith Project"

from .core import analyze
from .market import Market, detect_market
from .schema import Decision

__all__ = ["analyze", "Decision", "Market", "detect_market", "__version__"]
