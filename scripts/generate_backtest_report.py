# -*- coding: utf-8 -*-
"""
Generate a standalone HTML backtest report from dashboard JSON files.
"""
import argparse
import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}"


def _fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}%"


def _fmt_ratio(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def _render_metric_cards(summary: Dict[str, Any], stats: Dict[str, Any]) -> str:
    performance = stats.get("performance", {})
    agent = performance.get("agent", {})
    period = stats.get("period", {})
    cards = [
        ("组合净值", _fmt_number(summary.get("totalAssetValue"))),
        ("累计收益", _fmt_pct(summary.get("totalReturn"))),
        ("年化收益", _fmt_pct(agent.get("annualizedReturnPct"))),
        ("Sharpe", _fmt_ratio(agent.get("sharpe"))),
        ("最大回撤", _fmt_pct(agent.get("maxDrawdownPct"))),
        ("Calmar", _fmt_ratio(agent.get("calmar"))),
        ("交易日数", str(period.get("tradingDays") or 0)),
        ("总交易数", str(stats.get("totalTrades") or 0)),
    ]
    return "".join(
        (
            '<div class="metric-card">'
            f"<div class=\"metric-label\">{label}</div>"
            f"<div class=\"metric-value\">{value}</div>"
            "</div>"
        )
        for label, value in cards
    )


def _render_weights(ticker_weights: Dict[str, float]) -> str:
    if not ticker_weights:
        return "<p class=\"muted\">当前无持仓。</p>"
    rows = "".join(
        f"<tr><td>{escape(ticker)}</td><td>{_fmt_pct(weight * 100 if abs(weight) <= 1 else weight)}</td></tr>"
        for ticker, weight in sorted(
            ticker_weights.items(),
            key=lambda item: abs(item[1]),
            reverse=True,
        )
    )
    return (
        "<table><thead><tr><th>Ticker</th><th>Weight</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _render_benchmark_table(stats: Dict[str, Any]) -> str:
    performance = stats.get("performance", {})
    agent = performance.get("agent", {})
    benchmarks = performance.get("benchmarks", {})
    comparison = performance.get("comparison", {})
    rows = [
        (
            "Agent",
            agent,
            None,
        ),
        (
            "Equal Weight",
            benchmarks.get("equalWeight", {}),
            comparison.get("equalWeight", {}),
        ),
        (
            "Market Cap Weighted",
            benchmarks.get("marketCapWeighted", {}),
            comparison.get("marketCapWeighted", {}),
        ),
        (
            "Momentum",
            benchmarks.get("momentum", {}),
            comparison.get("momentum", {}),
        ),
    ]

    rendered_rows = []
    for name, metrics, diff in rows:
        rendered_rows.append(
            "<tr>"
            f"<td>{escape(name)}</td>"
            f"<td>{_fmt_pct(metrics.get('totalReturnPct'))}</td>"
            f"<td>{_fmt_pct(metrics.get('annualizedReturnPct'))}</td>"
            f"<td>{_fmt_pct(metrics.get('volatilityPct'))}</td>"
            f"<td>{_fmt_ratio(metrics.get('sharpe'))}</td>"
            f"<td>{_fmt_pct(metrics.get('maxDrawdownPct'))}</td>"
            f"<td>{_fmt_ratio(metrics.get('calmar'))}</td>"
            f"<td>{_fmt_pct(diff.get('excessReturnPct')) if diff else '—'}</td>"
            f"<td>{_fmt_ratio(diff.get('sharpeSpread')) if diff else '—'}</td>"
            "</tr>"
        )

    return (
        "<table><thead><tr>"
        "<th>Portfolio</th><th>Total Return</th><th>Annualized</th>"
        "<th>Volatility</th><th>Sharpe</th><th>Max Drawdown</th>"
        "<th>Calmar</th><th>Excess Return</th><th>Sharpe Spread</th>"
        f"</tr></thead><tbody>{''.join(rendered_rows)}</tbody></table>"
    )


def _render_leaderboard(leaderboard: List[Dict[str, Any]]) -> str:
    if not leaderboard:
        return "<p class=\"muted\">暂无分析师评测数据。</p>"

    ranking_rows = []
    for entry in leaderboard:
        if entry.get("rank") is None:
            continue
        breakdown = entry.get("scoreBreakdown", {})
        ranking_rows.append(
            "<tr>"
            f"<td>{entry.get('rank')}</td>"
            f"<td>{escape(entry.get('name', entry.get('agentId', 'Unknown')))}</td>"
            f"<td>{_fmt_ratio(entry.get('weightedScore'))}</td>"
            f"<td>{_fmt_ratio(breakdown.get('winRate'))}</td>"
            f"<td>{_fmt_ratio(breakdown.get('rm'))}</td>"
            f"<td>{_fmt_ratio(breakdown.get('grounding'))}</td>"
            f"<td>{_fmt_ratio(breakdown.get('audit'))}</td>"
            f"<td>{_fmt_ratio(breakdown.get('presentation'))}</td>"
            "</tr>"
        )

    if not ranking_rows:
        return "<p class=\"muted\">暂无可排名分析师数据。</p>"

    return (
        "<table><thead><tr>"
        "<th>Rank</th><th>Agent</th><th>Weighted</th><th>WinRate</th>"
        "<th>RM</th><th>Grounding</th><th>Audit</th><th>Presentation</th>"
        f"</tr></thead><tbody>{''.join(ranking_rows)}</tbody></table>"
    )


def _render_trades(trades: List[Dict[str, Any]]) -> str:
    if not trades:
        return "<p class=\"muted\">暂无交易记录。</p>"

    rows = []
    for trade in trades[:20]:
        rows.append(
            "<tr>"
            f"<td>{escape(str(trade.get('trading_date') or ''))}</td>"
            f"<td>{escape(str(trade.get('ticker') or ''))}</td>"
            f"<td>{escape(str(trade.get('side') or ''))}</td>"
            f"<td>{_fmt_number(trade.get('qty'), 0)}</td>"
            f"<td>{_fmt_number(trade.get('price'))}</td>"
            "</tr>"
        )

    return (
        "<table><thead><tr><th>Date</th><th>Ticker</th><th>Side</th>"
        f"<th>Qty</th><th>Price</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _build_polyline(points: Iterable[Dict[str, Any]], width: int, height: int) -> str:
    values = [float(point.get("v", 0.0)) for point in points if point.get("v") is not None]
    if len(values) < 2:
        return ""
    min_v = min(values)
    max_v = max(values)
    spread = max(max_v - min_v, 1e-9)
    coords = []
    for idx, value in enumerate(values):
        x = (idx / (len(values) - 1)) * width
        normalized = (value - min_v) / spread
        y = height - (normalized * height)
        coords.append(f"{x:.1f},{y:.1f}")
    return " ".join(coords)


def _render_equity_chart(summary: Dict[str, Any]) -> str:
    series = {
        "Agent": summary.get("equity", []),
        "Equal Weight": summary.get("baseline", []),
        "Market Cap": summary.get("baseline_vw", []),
        "Momentum": summary.get("momentum", []),
    }
    colors = {
        "Agent": "#2563eb",
        "Equal Weight": "#16a34a",
        "Market Cap": "#ea580c",
        "Momentum": "#9333ea",
    }
    width, height = 760, 220
    polylines = []
    legends = []
    for name, points in series.items():
        polyline = _build_polyline(points, width, height)
        if not polyline:
            continue
        color = colors[name]
        polylines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{polyline}" />'
        )
        legends.append(
            f'<span class="legend-item"><span class="legend-dot" style="background:{color}"></span>{name}</span>'
        )

    if not polylines:
        return "<p class=\"muted\">暂无净值曲线数据。</p>"

    return (
        f'<div class="legend">{"".join(legends)}</div>'
        f'<svg viewBox="0 0 {width} {height}" class="equity-chart">'
        f'{"".join(polylines)}</svg>'
    )


def build_report_html(
    dashboard_dir: Path,
    summary: Dict[str, Any],
    stats: Dict[str, Any],
    trades: List[Dict[str, Any]],
    leaderboard: List[Dict[str, Any]],
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    period = stats.get("period", {})
    title = dashboard_dir.parent.name
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)} Backtest Report</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --card: #ffffff;
      --ink: #132238;
      --muted: #5e6b7a;
      --line: #d7dfeb;
      --accent: #2563eb;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .page {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 24px 48px;
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      margin-bottom: 24px;
    }}
    h1, h2 {{
      margin: 0;
    }}
    h1 {{
      font-size: 34px;
      letter-spacing: -0.03em;
    }}
    h2 {{
      font-size: 20px;
      margin-bottom: 14px;
    }}
    .meta, .muted {{
      color: var(--muted);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 24px;
    }}
    .metric-card, .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 10px 30px rgba(19, 34, 56, 0.05);
    }}
    .metric-card {{
      padding: 18px 18px 16px;
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.03em;
    }}
    .two-col {{
      display: grid;
      grid-template-columns: 1.45fr 1fr;
      gap: 18px;
      margin-bottom: 18px;
    }}
    .panel {{
      padding: 18px 20px;
      overflow: hidden;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 14px;
      font-size: 13px;
      color: var(--muted);
    }}
    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .legend-dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
    }}
    .equity-chart {{
      width: 100%;
      background: linear-gradient(180deg, rgba(37,99,235,0.04), rgba(37,99,235,0));
      border-radius: 12px;
    }}
    @media (max-width: 900px) {{
      .grid, .two-col {{
        grid-template-columns: 1fr;
      }}
      .hero {{
        flex-direction: column;
        align-items: flex-start;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <div class="hero">
      <div>
        <div class="meta">Day 19-20 · Backtest Report</div>
        <h1>{escape(title)}</h1>
        <div class="meta">区间 {escape(str(period.get("startDate") or "N/A"))} - {escape(str(period.get("endDate") or "N/A"))}</div>
      </div>
      <div class="meta">Generated at {generated_at}</div>
    </div>

    <div class="grid">{_render_metric_cards(summary, stats)}</div>

    <div class="panel" style="margin-bottom:18px;">
      <h2>净值与基准曲线</h2>
      {_render_equity_chart(summary)}
    </div>

    <div class="two-col">
      <div class="panel">
        <h2>量化指标与基准对比</h2>
        {_render_benchmark_table(stats)}
      </div>
      <div class="panel">
        <h2>当前持仓权重</h2>
        {_render_weights(stats.get("tickerWeights", {}))}
      </div>
    </div>

    <div class="two-col">
      <div class="panel">
        <h2>分析师排行榜</h2>
        {_render_leaderboard(leaderboard)}
      </div>
      <div class="panel">
        <h2>最近交易</h2>
        {_render_trades(trades)}
      </div>
    </div>
  </div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML backtest report from dashboard JSON files.",
    )
    parser.add_argument(
        "--dashboard-dir",
        required=True,
        help="Path to the team_dashboard directory.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output HTML file path. Defaults to <dashboard-dir>/backtest_report.html",
    )
    args = parser.parse_args()

    dashboard_dir = Path(args.dashboard_dir)
    summary = _load_json(dashboard_dir / "summary.json", {})
    stats = _load_json(dashboard_dir / "stats.json", {})
    trades = _load_json(dashboard_dir / "trades.json", [])
    leaderboard = _load_json(dashboard_dir / "leaderboard.json", [])

    html = build_report_html(dashboard_dir, summary, stats, trades, leaderboard)
    output = Path(args.output) if args.output else dashboard_dir / "backtest_report.html"
    output.write_text(html, encoding="utf-8")
    print(f"Backtest report written to {output}")


if __name__ == "__main__":
    main()
