from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from backtest_strategy import (
    UNIVERSE,
    download_prices,
    get_current_geopolitical_context,
    optimize_current_portfolio,
    period_to_offset,
    run_backtest,
)


def parse_weights(weight_text: str) -> dict[str, float]:
    parsed = {ticker: 0.0 for ticker in UNIVERSE}
    if not isinstance(weight_text, str) or not weight_text:
        return parsed
    chunks = [chunk.strip() for chunk in weight_text.split(",")]
    for chunk in chunks:
        if ":" not in chunk:
            continue
        ticker, value = chunk.split(":", 1)
        ticker = ticker.strip()
        value = value.strip().replace("%", "")
        try:
            parsed[ticker] = float(value) / 100.0
        except ValueError:
            continue
    return parsed


def build_payload(period: str) -> dict:
    prices = download_prices(period)
    result = run_backtest(prices, period)

    evaluation_start = prices.index.max() - period_to_offset(period)
    eval_prices = prices.loc[prices.index >= evaluation_start].copy()
    eval_prices = eval_prices.dropna(how="any")
    risk_profiles = {
        "conservative": optimize_current_portfolio(prices, period, "conservative"),
        "aggressive": optimize_current_portfolio(prices, period, "aggressive"),
    }
    current_context = get_current_geopolitical_context()

    normalized = eval_prices.divide(eval_prices.iloc[0]).multiply(100.0)
    strategy_curve = (1.0 + result.daily_portfolio).cumprod().multiply(100.0)
    spy_curve = (1.0 + result.benchmark_daily).cumprod().multiply(100.0)

    common_index = strategy_curve.index.intersection(normalized.index).intersection(spy_curve.index)
    common_index = common_index.sort_values()

    weekly_weights = []
    if not result.trades.empty:
        for _, row in result.trades.iterrows():
            weekly_weights.append(
                {
                    "rebalance_date": row["rebalance_date"],
                    "regime": row["regime"],
                    "weights": parse_weights(row["weights"]),
                    "portfolio_return_1w": row["portfolio_return_1w"],
                    "spy_return_1w": row["spy_return_1w"],
                }
            )

    summary = dict(result.summary)
    summary["strategy_total_return_pct"] = summary["strategy_total_return"] * 100.0
    summary["spy_total_return_pct"] = summary["spy_total_return"] * 100.0
    summary["alpha_vs_spy_pct"] = (summary["strategy_total_return"] - summary["spy_total_return"]) * 100.0
    summary["max_drawdown_pct"] = summary["max_drawdown"] * 100.0
    summary["spy_max_drawdown_pct"] = summary["spy_max_drawdown"] * 100.0
    summary["annualized_volatility_pct"] = summary["annualized_volatility"] * 100.0
    summary["spy_annualized_volatility_pct"] = summary["spy_annualized_volatility"] * 100.0

    payload = {
        "period": period,
        "generated_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
        "summary": summary,
        "dates": [d.strftime("%Y-%m-%d") for d in common_index],
        "curves": {
            "strategy": [float(strategy_curve.loc[d]) for d in common_index],
            "spy": [float(spy_curve.loc[d]) for d in common_index],
        },
        "etf_index_100": {
            ticker: [float(normalized.loc[d, ticker]) for d in common_index] for ticker in UNIVERSE
        },
        "weekly_weights": weekly_weights,
        "risk_profiles": risk_profiles,
        "current_context": current_context,
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate web visualization data from the backtest strategy.")
    parser.add_argument("--period", default="1y", help="Evaluation period for the dashboard (default: 1y)")
    parser.add_argument(
        "--output",
        default="web/backtest_data.json",
        help="Path to output JSON file (default: web/backtest_data.json)",
    )
    args = parser.parse_args()

    payload = build_payload(args.period)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote dashboard data to {output_path}")


if __name__ == "__main__":
    main()
