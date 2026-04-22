from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


UNIVERSE = ["SPY", "QQQ", "GLD", "USO", "EWJ"]
ACTIVE_UNIVERSE = ["SPY", "QQQ", "GLD", "USO", "EWJ"]
INITIAL_CAPITAL = 10_000.0
LOOKBACK_BY_PERIOD = {"1mo": "6mo", "1y": "2y"}
MAX_SINGLE_WEIGHT = 0.55
MAX_EQUITY_PAIR = 0.90


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    daily_portfolio: pd.Series
    benchmark_daily: pd.Series
    summary: dict[str, float | str]


def period_to_offset(period: str) -> pd.DateOffset:
    if period.endswith("mo"):
        return pd.DateOffset(months=int(period[:-2]))
    if period.endswith("y"):
        return pd.DateOffset(years=int(period[:-1]))
    raise ValueError(f"Unsupported period: {period}")


def zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def max_drawdown(cumulative: pd.Series) -> float:
    running_max = cumulative.cummax()
    drawdown = cumulative / running_max - 1.0
    return float(drawdown.min())


def annualized_sharpe(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    if returns.empty:
        return 0.0
    daily_rf = risk_free_rate / 252.0
    excess = returns - daily_rf
    vol = excess.std(ddof=0)
    if vol == 0 or np.isnan(vol):
        return 0.0
    return float(np.sqrt(252.0) * excess.mean() / vol)


def portfolio_beta(returns: pd.Series, benchmark: pd.Series) -> float:
    aligned = pd.concat([returns, benchmark], axis=1).dropna()
    aligned.columns = ["portfolio", "benchmark"]
    if aligned.empty:
        return 0.0
    bench_var = aligned["benchmark"].var(ddof=0)
    if bench_var == 0 or np.isnan(bench_var):
        return 0.0
    cov = aligned["portfolio"].cov(aligned["benchmark"])
    return float(cov / bench_var)


def classify_regime(features: pd.DataFrame) -> str:
    spy_mom = float(features.loc["SPY", "mom_20"])
    qqq_mom = float(features.loc["QQQ", "mom_20"])
    spy_vol = float(features.loc["SPY", "vol_20"])
    median_vol = float(features["vol_20"].median())
    gold_mom = float(features.loc["GLD", "mom_20"])

    if spy_mom > 0 and qqq_mom > 0 and spy_vol <= median_vol * 1.2:
        return "risk_on"
    if spy_mom < 0 and qqq_mom < 0 and gold_mom > 0:
        return "defensive"
    return "mixed"


def apply_regime_tilt(weights: pd.Series, regime: str) -> pd.Series:
    tilted = weights.copy()
    if regime == "risk_on":
        multipliers = {"SPY": 1.20, "QQQ": 1.25, "GLD": 0.70, "USO": 1.15, "EWJ": 1.05}
    elif regime == "defensive":
        multipliers = {"SPY": 0.95, "QQQ": 0.80, "GLD": 1.10, "USO": 0.70, "EWJ": 0.90}
    else:
        multipliers = {"SPY": 1.05, "QQQ": 1.00, "GLD": 0.95, "USO": 1.00, "EWJ": 1.00}

    for ticker, multiplier in multipliers.items():
        if ticker in tilted.index:
            tilted.loc[ticker] *= multiplier
    return tilted


def redistribute_capacity(weights: pd.Series, residual: float, priorities: list[str]) -> tuple[pd.Series, float]:
    for ticker in priorities:
        if residual <= 1e-12:
            break
        room = max(0.0, MAX_SINGLE_WEIGHT - weights.get(ticker, 0.0))
        if room <= 0:
            continue
        add = min(room, residual)
        weights.loc[ticker] += add
        residual -= add
    return weights, residual


def apply_constraints(weights: pd.Series) -> pd.Series:
    constrained = weights.copy().reindex(UNIVERSE, fill_value=0.0)

    if constrained.sum() <= 0:
        constrained.loc["SPY"] = 0.60
        constrained.loc["QQQ"] = 0.30
        constrained.loc["GLD"] = 0.10

    total = constrained.sum()
    if total > 0:
        constrained = constrained / total

    for ticker in UNIVERSE:
        constrained.loc[ticker] = min(constrained.loc[ticker], MAX_SINGLE_WEIGHT)

    equity_sum = constrained.loc["SPY"] + constrained.loc["QQQ"]
    if equity_sum > MAX_EQUITY_PAIR:
        scale = MAX_EQUITY_PAIR / equity_sum
        constrained.loc["SPY"] *= scale
        constrained.loc["QQQ"] *= scale

    residual = max(0.0, 1.0 - constrained.sum())
    constrained, residual = redistribute_capacity(constrained, residual, ["QQQ", "SPY", "EWJ", "USO", "GLD"])

    total_after = constrained.sum()
    if total_after > 1.0:
        constrained = constrained / total_after

    return constrained


def compute_target_weights(prices: pd.DataFrame, as_of: pd.Timestamp) -> tuple[pd.Series, str, pd.DataFrame]:
    window = prices.loc[:as_of, ACTIVE_UNIVERSE]
    returns = window.pct_change()

    features = pd.DataFrame(index=ACTIVE_UNIVERSE)
    features["mom_5"] = window.iloc[-1] / window.iloc[-6] - 1.0
    features["mom_20"] = window.iloc[-1] / window.iloc[-21] - 1.0
    features["vol_20"] = returns.iloc[-20:].std(ddof=0) * np.sqrt(252.0)
    rolling_max = window.iloc[-20:].max()
    features["drawdown_20"] = window.iloc[-1] / rolling_max - 1.0

    spy_returns = returns["SPY"]
    corr_20 = {}
    for ticker in ACTIVE_UNIVERSE:
        corr_20[ticker] = returns[ticker].iloc[-20:].corr(spy_returns.iloc[-20:])
    features["corr_spy_20"] = pd.Series(corr_20)

    trend = 0.75 * zscore(features["mom_5"]) + 0.25 * zscore(features["mom_20"])
    drawdown_penalty = zscore(features["drawdown_20"].abs())
    risk_penalty = 0.5 * zscore(features["vol_20"]) + 0.2 * drawdown_penalty
    total_score = trend - 0.65 * risk_penalty
    features["total_score"] = total_score

    selected = features[features["total_score"] > 0].index.tolist()
    if len(selected) < 1:
        selected = [features["total_score"].idxmax()]

    selected_scores = features.loc[selected, "total_score"]
    shifted = selected_scores - selected_scores.min() + 1e-6
    weights = shifted / shifted.sum()

    full_weights = pd.Series(0.0, index=UNIVERSE)
    for ticker in selected:
        full_weights.loc[ticker] = weights.loc[ticker]

    regime = classify_regime(features)
    full_weights = apply_regime_tilt(full_weights, regime)
    full_weights = apply_constraints(full_weights)
    return full_weights, regime, features


def download_prices(period: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "yfinance is not installed. Install it with `python -m pip install yfinance` "
            "or provide a local CSV with daily adjusted close prices."
        ) from exc

    raw = yf.download(
        tickers=UNIVERSE,
        period=LOOKBACK_BY_PERIOD.get(period, period),
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=False,
    )
    if raw.empty:
        raise SystemExit("No price data was downloaded.")

    if isinstance(raw.columns, pd.MultiIndex):
        prices = pd.DataFrame({ticker: raw[ticker]["Close"] for ticker in UNIVERSE})
    else:
        prices = raw[["Close"]].rename(columns={"Close": UNIVERSE[0]})
    prices = prices.dropna(how="any")
    return prices


def run_backtest(prices: pd.DataFrame, evaluation_period: str) -> BacktestResult:
    prices = prices.sort_index()
    daily_returns = prices.pct_change().dropna()
    evaluation_start = prices.index.max() - period_to_offset(evaluation_period)

    rebalance_dates = []
    for idx in daily_returns.index:
        if idx.weekday() == 4 and idx >= evaluation_start:
            rebalance_dates.append(idx)

    trades = []
    portfolio_returns = pd.Series(0.0, index=daily_returns.index)
    benchmark_returns = daily_returns["SPY"].copy()

    for rebalance_date in rebalance_dates:
        loc = daily_returns.index.get_loc(rebalance_date)
        if isinstance(loc, slice) or loc < 20:
            continue
        if loc + 5 >= len(daily_returns.index):
            break

        weights, regime, features = compute_target_weights(prices, rebalance_date)
        hold_dates = daily_returns.index[loc + 1 : loc + 6]
        period_returns = daily_returns.loc[hold_dates, UNIVERSE].mul(weights, axis=1).sum(axis=1)
        portfolio_returns.loc[hold_dates] = period_returns

        period_port_return = float((1.0 + period_returns).prod() - 1.0)
        period_bench_return = float((1.0 + daily_returns.loc[hold_dates, "SPY"]).prod() - 1.0)
        trades.append(
            {
                "rebalance_date": rebalance_date.date().isoformat(),
                "regime": regime,
                "weights": ", ".join(f"{k}:{v:.1%}" for k, v in weights[weights > 0].items()),
                "portfolio_return_1w": period_port_return,
                "spy_return_1w": period_bench_return,
                "top_scores": ", ".join(
                    f"{idx}:{val:.2f}"
                    for idx, val in features["total_score"].sort_values(ascending=False).head(3).items()
                ),
            }
        )

    active_portfolio_returns = portfolio_returns.loc[portfolio_returns.index >= evaluation_start]
    benchmark_returns = benchmark_returns.loc[benchmark_returns.index >= evaluation_start]
    cumulative = (1.0 + active_portfolio_returns).cumprod()
    benchmark_cumulative = (1.0 + benchmark_returns).cumprod()

    summary = {
        "start_date": active_portfolio_returns.index[0].date().isoformat(),
        "end_date": active_portfolio_returns.index[-1].date().isoformat(),
        "strategy_total_return": float(cumulative.iloc[-1] - 1.0),
        "spy_total_return": float(benchmark_cumulative.iloc[-1] - 1.0),
        "annualized_volatility": float(active_portfolio_returns.std(ddof=0) * np.sqrt(252.0)),
        "spy_annualized_volatility": float(benchmark_returns.std(ddof=0) * np.sqrt(252.0)),
        "max_drawdown": max_drawdown(cumulative),
        "spy_max_drawdown": max_drawdown(benchmark_cumulative),
        "sharpe_ratio": annualized_sharpe(active_portfolio_returns),
        "spy_sharpe_ratio": annualized_sharpe(benchmark_returns),
        "beta_vs_spy": portfolio_beta(active_portfolio_returns, benchmark_returns),
        "num_rebalances": int(len(trades)),
    }

    return BacktestResult(
        trades=pd.DataFrame(trades),
        daily_portfolio=active_portfolio_returns,
        benchmark_daily=benchmark_returns,
        summary=summary,
    )


def print_result(label: str, result: BacktestResult) -> None:
    print(f"\n=== {label} ===")
    for key, value in result.summary.items():
        if isinstance(value, float):
            if "date" in key:
                print(f"{key}: {value}")
            else:
                print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")

    if not result.trades.empty:
        print("\nRecent rebalance log:")
        print(result.trades.tail(5).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the strategy described in REPORT.md.")
    parser.add_argument(
        "--periods",
        nargs="+",
        default=["1mo", "1y"],
        help="Yahoo Finance periods to backtest, e.g. 1mo 1y",
    )
    args = parser.parse_args()

    for period in args.periods:
        prices = download_prices(period)
        result = run_backtest(prices, period)
        print_result(period, result)


if __name__ == "__main__":
    main()
