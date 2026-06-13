"""Quick start: 三市场分别跑一次 demo。"""
from alphalith import analyze
from alphalith.report import render

for sym in ["茅台", "0700.HK", "NVDA"]:
    d = analyze(sym, depth="standard")
    print(render(d))
    print()
