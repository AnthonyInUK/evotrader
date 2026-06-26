# -*- coding: utf-8 -*-
"""
Generate charts and attribution for a completed backtest.

This script is intentionally offline-only. It reads the finished experiment
directory plus local price cache files, then writes charts and a markdown
review under <config>/analysis.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PRICE_CACHE = ROOT / "backend" / "data" / "akshare_cache"


@dataclass
class TradePnL:
    ticker: str
    buy_value: float = 0.0
    sell_value: float = 0.0
    final_value: float = 0.0

    @property
    def pnl(self) -> float:
        return self.sell_value + self.final_value - self.buy_value


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_symbol(ticker: str) -> str:
    return ticker.replace(".", "_")


def _load_price_cache(ticker: str) -> pd.DataFrame:
    files = sorted(PRICE_CACHE.glob(f"{_safe_symbol(ticker)}_*.parquet"))
    if not files:
        return pd.DataFrame()

    frames = []
    for path in files:
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if frame.empty:
            continue
        if not isinstance(frame.index, pd.DatetimeIndex):
            if "time" in frame.columns:
                frame.index = pd.to_datetime(frame["time"])
            else:
                frame.index = pd.to_datetime(frame.index)
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def _price_on(prices: dict[str, pd.DataFrame], ticker: str, date: pd.Timestamp) -> float | None:
    df = prices.get(ticker)
    if df is None or df.empty or "close" not in df.columns:
        return None
    key = pd.Timestamp(date).normalize()
    if key in df.index:
        return float(df.loc[key, "close"])
    before = df.loc[df.index <= key]
    if before.empty:
        return None
    return float(before.iloc[-1]["close"])


def _series_to_frame(points: list[dict[str, Any]], name: str) -> pd.DataFrame:
    rows = []
    for point in points:
        rows.append(
            {
                "date": pd.to_datetime(int(point["t"]), unit="ms"),
                name: float(point["v"]),
            }
        )
    return pd.DataFrame(rows).set_index("date")


def _max_drawdown(values: pd.Series) -> pd.Series:
    return values / values.cummax() - 1.0


def _replay_weights(
    dates: list[pd.Timestamp],
    trades: list[dict[str, Any]],
    prices: dict[str, pd.DataFrame],
    initial_cash: float,
) -> pd.DataFrame:
    trades_by_date: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        date = pd.to_datetime(trade["trading_date"]).normalize()
        trades_by_date[date].append(trade)

    cash = initial_cash
    quantities: dict[str, int] = defaultdict(int)
    rows = []

    for date in dates:
        day = pd.Timestamp(date).normalize()
        for trade in trades_by_date.get(day, []):
            ticker = trade["ticker"]
            qty = int(trade["qty"])
            value = qty * float(trade["price"])
            if str(trade["side"]).upper() == "LONG":
                quantities[ticker] += qty
                cash -= value
            else:
                quantities[ticker] -= qty
                cash += value

        values: dict[str, float] = {"CASH": cash}
        for ticker, qty in quantities.items():
            if qty <= 0:
                continue
            price = _price_on(prices, ticker, day)
            if price is None:
                continue
            values[ticker] = qty * price

        total = sum(values.values())
        row = {"date": day}
        for ticker, value in values.items():
            row[ticker] = value / total if total else 0.0
        rows.append(row)

    return pd.DataFrame(rows).set_index("date").fillna(0.0)


def _trade_pnl(
    trades: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
) -> dict[str, TradePnL]:
    pnl: dict[str, TradePnL] = defaultdict(lambda: TradePnL(ticker=""))

    for trade in trades:
        ticker = trade["ticker"]
        if not pnl[ticker].ticker:
            pnl[ticker].ticker = ticker
        value = int(trade["qty"]) * float(trade["price"])
        if str(trade["side"]).upper() == "LONG":
            pnl[ticker].buy_value += value
        else:
            pnl[ticker].sell_value += value

    for holding in holdings:
        ticker = holding.get("ticker")
        if ticker == "CASH":
            continue
        if not pnl[ticker].ticker:
            pnl[ticker].ticker = ticker
        pnl[ticker].final_value += float(holding.get("marketValue", 0.0))

    return dict(pnl)


def _plot_equity(output: Path, summary: dict[str, Any]) -> pd.DataFrame:
    frames = [
        _series_to_frame(summary.get("equity", []), "Agent"),
        _series_to_frame(summary.get("baseline", []), "EqualWeight"),
        _series_to_frame(summary.get("momentum", []), "Momentum"),
    ]
    df = pd.concat(frames, axis=1).sort_index()

    fig, ax = plt.subplots(figsize=(12, 6))
    for col in df.columns:
        ax.plot(df.index, df[col], linewidth=2.4, label=col)
    ax.set_title("Equity Curve vs Benchmarks")
    ax.set_ylabel("Portfolio Value (CNY)")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output / "equity_vs_benchmarks.png", dpi=180)
    plt.close(fig)
    return df


def _plot_drawdown(output: Path, equity: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    for col in equity.columns:
        dd = _max_drawdown(equity[col].dropna()) * 100
        ax.plot(dd.index, dd, linewidth=2.0, label=col)
    ax.set_title("Drawdown")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output / "drawdown.png", dpi=180)
    plt.close(fig)


def _plot_weights(output: Path, weights: pd.DataFrame) -> None:
    ordered = ["CASH"] + [c for c in weights.columns if c != "CASH"]
    weights = weights[[c for c in ordered if c in weights.columns]]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.stackplot(weights.index, *[weights[c] * 100 for c in weights.columns], labels=weights.columns)
    ax.set_title("End-of-Day Portfolio Weights")
    ax.set_ylabel("Weight (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper left", ncols=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output / "portfolio_weights.png", dpi=180)
    plt.close(fig)


def _plot_pnl(output: Path, pnl_rows: list[dict[str, Any]], cost_drag: float) -> None:
    labels = [row["ticker"] for row in pnl_rows] + ["cost_or_slippage"]
    values = [row["pnl"] for row in pnl_rows] + [cost_drag]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    colors = ["#0f766e" if v >= 0 else "#b91c1c" for v in values]
    ax.bar(labels, values, color=colors)
    ax.axhline(0, color="#111827", linewidth=1)
    ax.set_title("Approximate Trade PnL Attribution")
    ax.set_ylabel("CNY")
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output / "pnl_attribution.png", dpi=180)
    plt.close(fig)


def _pct(value: float) -> str:
    return f"{value:.2f}%"


def _money(value: float) -> str:
    return f"¥{value:,.2f}"


def _interpretation(metrics: dict[str, Any]) -> str:
    agent = metrics["agent_return_pct"]
    equal = metrics["equal_weight_return_pct"]
    cash_effect = metrics["cash_defense_vs_equal"]
    active = metrics["active_pnl"]

    if agent > equal and cash_effect > 0:
        return (
            "The agent's edge came mostly from risk control. The benchmark fell, "
            "while the agent kept meaningful cash and avoided weaker names. "
            "Stock selection added some absolute PnL, but cash discipline was the "
            "main source of excess return."
        )
    if agent < equal and cash_effect < 0:
        return (
            "The agent made positive absolute PnL, but high cash became a drag "
            "because the benchmark rose. This is a missed-upside result: the PM "
            "was prudent, but too conservative for this window."
        )
    if active < 0:
        return (
            "The agent underperformed because active stock decisions lost money. "
            "This points to selection or timing issues rather than only cash drag."
        )
    return (
        "The result is mixed. Active stock decisions made money, but the balance "
        "between cash, timing, and benchmark exposure determines whether that "
        "translated into excess return."
    )


def generate(config: str) -> Path:
    config_dir = ROOT / config
    output = config_dir / "analysis"
    output.mkdir(parents=True, exist_ok=True)

    summary = _load_json(config_dir / "team_dashboard" / "summary.json", {})
    stats = _load_json(config_dir / "team_dashboard" / "stats.json", {})
    trades = _load_json(config_dir / "team_dashboard" / "trades.json", [])
    holdings = _load_json(config_dir / "team_dashboard" / "holdings.json", [])

    equity = _plot_equity(output, summary)
    _plot_drawdown(output, equity)

    tickers = sorted(
        {
            trade["ticker"]
            for trade in trades
            if trade.get("ticker")
        }
        | {
            holding["ticker"]
            for holding in holdings
            if holding.get("ticker") and holding.get("ticker") != "CASH"
        }
    )
    prices = {ticker: _load_price_cache(ticker) for ticker in tickers}

    trade_dates = [
        pd.to_datetime(point["t"], unit="ms").normalize()
        for point in summary.get("equity", [])
    ]
    trade_dates = [d for d in trade_dates if d >= pd.Timestamp("2024-01-02")]
    weights = _replay_weights(trade_dates, trades, prices, initial_cash=500000.0)
    _plot_weights(output, weights)

    pnl = _trade_pnl(trades, holdings)
    pnl_rows = [
        {
            "ticker": ticker,
            "buy_value": item.buy_value,
            "sell_value": item.sell_value,
            "final_value": item.final_value,
            "pnl": item.pnl,
        }
        for ticker, item in sorted(pnl.items())
    ]
    active_pnl = float(summary.get("totalAssetValue", 0.0)) - 500000.0
    gross_trade_pnl = sum(row["pnl"] for row in pnl_rows)
    cost_drag = active_pnl - gross_trade_pnl
    _plot_pnl(output, pnl_rows, cost_drag)

    agent_end = float(summary.get("totalAssetValue", 0.0))
    equal_end = float(summary.get("baseline", [{}])[-1].get("v", 0.0))
    momentum_end = float(summary.get("momentum", [{}])[-1].get("v", 0.0))
    cash_defense = 500000.0 - equal_end
    excess_equal = agent_end - equal_end

    metrics = {
        "agent_end": agent_end,
        "agent_return_pct": (agent_end / 500000.0 - 1.0) * 100,
        "equal_weight_end": equal_end,
        "equal_weight_return_pct": (equal_end / 500000.0 - 1.0) * 100,
        "momentum_end": momentum_end,
        "momentum_return_pct": (momentum_end / 500000.0 - 1.0) * 100,
        "excess_vs_equal": excess_equal,
        "cash_defense_vs_equal": cash_defense,
        "active_pnl": active_pnl,
        "gross_trade_pnl": gross_trade_pnl,
        "cost_or_slippage_drag": cost_drag,
        "pnl_by_ticker": pnl_rows,
    }
    (output / "attribution_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = [
        f"# {config} Backtest Attribution",
        "",
        "## Key Results",
        "",
        f"- Agent final equity: {_money(agent_end)} ({_pct(metrics['agent_return_pct'])})",
        f"- Equal-weight benchmark: {_money(equal_end)} ({_pct(metrics['equal_weight_return_pct'])})",
        f"- Momentum benchmark: {_money(momentum_end)} ({_pct(metrics['momentum_return_pct'])})",
        f"- Excess vs equal weight: {_money(excess_equal)}",
        "",
        "## Attribution Read",
        "",
        (
            f"- Cash/risk-control effect vs equal-weight benchmark: "
            f"{_money(cash_defense)}. This is the loss the equal-weight benchmark took "
            "while the agent kept a large cash reserve."
        ),
        (
            f"- Active portfolio PnL over cash: {_money(active_pnl)}. "
            "This is what the actual stock decisions added after starting from cash."
        ),
        (
            f"- Gross trade PnL by ticker sums to {_money(gross_trade_pnl)}; "
            f"execution/cost/slippage drag is approximately {_money(cost_drag)}."
        ),
        "",
        "## Ticker PnL",
        "",
        "| Ticker | Buy Value | Sell Value | Final Value | Approx PnL |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in pnl_rows:
        report.append(
            "| {ticker} | {buy} | {sell} | {final} | {pnl} |".format(
                ticker=row["ticker"],
                buy=_money(row["buy_value"]),
                sell=_money(row["sell_value"]),
                final=_money(row["final_value"]),
                pnl=_money(row["pnl"]),
            )
        )

    final_weights = weights.iloc[-1].sort_values(ascending=False)
    report.extend(
        [
            "",
            "## Final Weights",
            "",
            "| Asset | Weight |",
            "|---|---:|",
        ]
    )
    for ticker, weight in final_weights.items():
        if abs(weight) < 0.0001:
            continue
        report.append(f"| {ticker} | {_pct(weight * 100)} |")

    report.extend(
        [
            "",
            "## Charts",
            "",
            "- `equity_vs_benchmarks.png`: agent vs equal-weight and momentum benchmarks.",
            "- `drawdown.png`: drawdown comparison.",
            "- `portfolio_weights.png`: daily cash and position weights.",
            "- `pnl_attribution.png`: approximate ticker-level PnL.",
            "",
            "## Interpretation",
            "",
            _interpretation(metrics),
        ]
    )
    (output / "attribution_report.md").write_text("\n".join(report), encoding="utf-8")

    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="Backtest config directory name")
    args = parser.parse_args()

    output = generate(args.config)
    print(f"Attribution report written to: {output}")


if __name__ == "__main__":
    main()
