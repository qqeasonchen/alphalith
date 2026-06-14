"""
CLI:
  alphalith analyze SYMBOL [--depth ...] [--json] [--no-save]
  alphalith analyze-batch SYM1 SYM2 ... [--depth ...] [--no-save]
  alphalith backtest SYMBOL [--days N] [--horizon N] [--json]
  alphalith history [--symbol X] [--limit N]
  alphalith review  [--symbol X] [--json]
  alphalith dashboard [--symbols X,Y,Z] [--output path.html]
  alphalith gui [--port PORT]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import journal
from .backtest import render_backtest, run_backtest, STRATEGIES, to_dict as backtest_to_dict
from .core import analyze
from .html_report import render_html as backtest_render_html, render_compare_html
from .market import UnknownSymbolError
from .report import render


def _generate_llm_review(r_a, r_b) -> str:
    """用 LLM 对两个策略的回测结果生成对比评语。"""
    try:
        from .llm import get_llm
        llm = get_llm()
        a_dict = {
            "strategy": r_a.strategy,
            "total_pnl": f"{r_a.total_pnl*100:+.2f}%",
            "win_rate": f"{r_a.win_rate*100:.1f}%",
            "sharpe": f"{r_a.sharpe:.2f}",
            "calmar": f"{r_a.calmar:.2f}",
            "max_drawdown": f"{r_a.max_drawdown*100:.2f}%",
            "actionable": len(r_a.actionable),
        }
        b_dict = {
            "strategy": r_b.strategy,
            "total_pnl": f"{r_b.total_pnl*100:+.2f}%",
            "win_rate": f"{r_b.win_rate*100:.1f}%",
            "sharpe": f"{r_b.sharpe:.2f}",
            "calmar": f"{r_b.calmar:.2f}",
            "max_drawdown": f"{r_b.max_drawdown*100:.2f}%",
            "actionable": len(r_b.actionable),
        }
        prompt = (
            f"你是量化策略评审专家。以下是两个策略对 {r_a.symbol} 的回测结果对比：\n\n"
            f"【策略 A】{a_dict}\n"
            f"【策略 B】{b_dict}\n\n"
            f"请用 2-4 句话给出专业评语：哪个策略整体更优？为什么？有什么风险提示？"
            f"直接输出中文，不要 markdown。"
        )
        reply = llm.chat(prompt, system="你是量化评审专家，只输出简短专业评语。").strip()
        return reply[:500]
    except Exception as e:
        return f"（LLM 评语生成失败：{e}）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alphalith",
        description="🪨 Alphalith · 慧投 — AI 投研委员会 (A股 / 港股 / 美股)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_a = sub.add_parser("analyze", help="分析单个标的")
    p_a.add_argument("symbol", help="股票代码或中文名（如 600519、茅台、0700.HK、NVDA）")
    p_a.add_argument("--depth", default="standard", choices=["quick", "standard", "deep"])
    p_a.add_argument("--json", action="store_true", help="输出 ADP v1.0 JSON")
    p_a.add_argument("--no-save", action="store_true", help="不写入决策日志库")

    p_ab = sub.add_parser("analyze-batch", help="批量分析多个标的（命令行多参数）")
    p_ab.add_argument("symbols", nargs="+", help="多个标的，空格分隔")
    p_ab.add_argument("--depth", default="standard", choices=["quick", "standard", "deep"])
    p_ab.add_argument("--no-save", action="store_true", help="不写入决策日志库")
    p_ab.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    p_ab.add_argument("--continue-on-error", action="store_true",
                      help="单个失败时继续处理后续（默认开启）", default=True)

    p_bt = sub.add_parser("backtest", help="历史 K 线滚动回测（默认均线策略 / 可选 LLM 决策）")
    p_bt.add_argument("symbol", help="股票代码或中文名")
    p_bt.add_argument("--days", type=int, default=90, help="抓多少根日 K（默认 90）")
    p_bt.add_argument("--horizon", type=int, default=5, help="持有窗口（交易日，默认 5）")
    p_bt.add_argument("--strategy", default="ma_cross",
                     choices=["ma_cross", "macd", "rsi", "bollinger", "momentum", "reversal", "llm"],
                     help="ma_cross/macd/rsi/bollinger/momentum/reversal=纯技术 / llm=每根 K 线调一次 LLM")
    p_bt.add_argument("--json", action="store_true", help="输出 JSON")
    p_bt.add_argument("--html", default=None, metavar="PATH",
                      help="输出单文件 HTML 报告到指定路径（含价格图/资金曲线/买卖点）")
    p_bt.add_argument("--compare", default=None, metavar="STRATEGY",
                      help="与另一策略对比（需同时指定 --html），如 --compare macd 或 --compare llm")

    p_h = sub.add_parser("history", help="查看历史决策")
    p_h.add_argument("--symbol", default=None, help="筛选某个标的")
    p_h.add_argument("--limit", type=int, default=20)

    p_r = sub.add_parser("review", help="对决策日志做聚合复盘")
    p_r.add_argument("--symbol", default=None, help="只看某个标的")
    p_r.add_argument("--json", action="store_true", help="输出 JSON")

    p_d = sub.add_parser("dashboard", help="生成 HTML Dashboard 面板")
    p_d.add_argument("--symbols", default="600519,0700.HK,NVDA",
                     help="监控标的列表，逗号分隔（默认 600519,0700.HK,NVDA）")
    p_d.add_argument("--output", default="alphalith_dashboard.html",
                     help="输出 HTML 路径（默认 alphalith_dashboard.html）")

    p_gui = sub.add_parser("gui", help="启动 AI 投研工作台 GUI")
    p_gui.add_argument("--port", type=int, default=8888, help="HTTP 服务端口（默认 8888）")
    p_gui.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    args = parser.parse_args(argv)

    if args.cmd == "analyze":
        try:
            d = analyze(args.symbol, depth=args.depth, persist=not args.no_save)
        except UnknownSymbolError as e:
            print(f"❌ {e}")
            return 2
        if args.json:
            print(json.dumps(d.to_adp_json(), ensure_ascii=False, indent=2, default=str))
        else:
            print(render(d))
            if not args.no_save:
                print(f"📚 已记入决策日志：{journal.db_path()}")
        return 0

    if args.cmd == "analyze-batch":
        results = []
        ok = fail = 0
        for sym in args.symbols:
            print(f"\n{'═'*72}\n▶ 分析：{sym}\n{'═'*72}")
            try:
                d = analyze(sym, depth=args.depth, persist=not args.no_save)
                ok += 1
                if args.json:
                    results.append({"input": sym, "ok": True, "decision": d.to_adp_json()})
                else:
                    print(render(d))
            except UnknownSymbolError as e:
                fail += 1
                print(f"❌ 跳过 {sym}：{e}")
                if args.json:
                    results.append({"input": sym, "ok": False, "error": str(e)})
            except Exception as e:
                fail += 1
                print(f"❌ {sym} 分析异常：{e}")
                if args.json:
                    results.append({"input": sym, "ok": False, "error": str(e)})
        if args.json:
            print(json.dumps(
                {"ok": ok, "fail": fail, "results": results},
                ensure_ascii=False, indent=2, default=str,
            ))
        else:
            print(f"\n📊 批量完成：成功 {ok} 个，失败 {fail} 个，共 {len(args.symbols)} 个")
            if not args.no_save:
                print(f"📚 决策日志：{journal.db_path()}")
        return 0 if fail == 0 else 1

    if args.cmd == "backtest":
        try:
            r = run_backtest(
                args.symbol, days=args.days, horizon=args.horizon,
                strategy=args.strategy,
            )
        except UnknownSymbolError as e:
            print(f"❌ {e}")
            return 2
        if args.json:
            print(json.dumps(backtest_to_dict(r), ensure_ascii=False, indent=2))
        else:
            print(render_backtest(r))
        if args.html and args.compare:
            from pathlib import Path as _P
            try:
                r2 = run_backtest(
                    args.symbol, days=args.days, horizon=args.horizon,
                    strategy=args.compare,
                )
            except Exception as e:
                print(f"❌ 对比策略 {args.compare} 执行失败：{e}")
                return 1

            # 若对比双方有 LLM 策略，让 LLM 生成对比评语
            llm_review = ""
            if args.strategy == "llm" or args.compare == "llm":
                llm_review = _generate_llm_review(r, r2)

            html = render_compare_html(r, r2, llm_review=llm_review)
            p = _P(args.html).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(html, encoding="utf-8")
            print(f"\n📄 策略对比 HTML 报告已生成：{p}")
            if llm_review:
                preview = llm_review[:120].replace("\n", " ")
                print(f"🤖 LLM 评语：{preview}...")
        elif args.html:
            from pathlib import Path as _P
            html = backtest_render_html(r)
            p = _P(args.html).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(html, encoding="utf-8")
            print(f"\n📄 HTML 报告已生成：{p}")
        return 0

    if args.cmd == "history":
        rows = journal.history(symbol=args.symbol, limit=args.limit)
        if not rows:
            print("（决策日志为空）")
            return 0
        print(f"决策日志（共 {len(rows)} 条，库：{journal.db_path()}）")
        print("─" * 96)
        print(f"{'时间':<20} {'标的':<14} {'市场':<8} {'决策':<5} {'置信':<5} {'手数':<6} {'入场价':<10} {'LLM':<10} {'tok':<6}")
        print("─" * 96)
        for r in rows:
            ts = r["ts"][:19].replace("T", " ")
            print(
                f"{ts:<20} {r['symbol']:<14} {r['market']:<8} {r['action']:<5} "
                f"{r['confidence']:<5.2f} {r['shares']:<6} {r['entry_price']:<10.2f} "
                f"{(r['llm'] or '-'):<10} {(r['llm_total_tokens'] or 0):<6}"
            )
        return 0

    if args.cmd == "review":
        stats = journal.review(symbol=args.symbol)
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            return 0
        if "error" in stats:
            print(f"复盘失败：{stats['error']}")
            return 1
        scope = f"标的 {args.symbol}" if args.symbol else "全部决策"
        print(f"📊 决策复盘 · {scope}")
        print("─" * 60)
        print(f"总数：{stats['total']}    平均置信度：{stats['avg_confidence']:.2f}    "
              f"累计 token：{stats['tokens_total']:,}")
        if stats["by_action"]:
            print(f"按决策：{dict(stats['by_action'])}")
        if stats["by_llm"]:
            print(f"按 LLM：{dict(stats['by_llm'])}")
        if stats["by_source"]:
            print(f"按数据源：{dict(stats['by_source'])}")
        if stats["latest"]:
            print("\n最近 5 条：")
            for r in stats["latest"]:
                ts = r["ts"][:19].replace("T", " ")
                print(f"  {ts}  {r['symbol']:<14} {r['action']:<5} "
                      f"conf={r['confidence']:.2f}  @ {r['entry_price']:.2f} × {r['shares']}")
        return 0

    if args.cmd == "dashboard":
        from pathlib import Path as _P
        from .dashboard import DashboardConfig, render_dashboard
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        cfg = DashboardConfig(symbols=syms)
        html = render_dashboard(cfg)
        p = _P(args.output).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        print(f"📊 Dashboard 已生成：{p}")
        print(f"   监控标的：{', '.join(syms)}")
        print(f"   用浏览器打开即可查看")
        return 0

    if args.cmd == "gui":
        from .gui import start_gui
        server, thread = start_gui(port=args.port, open_browser=not args.no_browser)
        try:
            while thread.is_alive():
                thread.join(1)
        except KeyboardInterrupt:
            print("\n  收到中断信号，正在停止...")
            server.shutdown()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
