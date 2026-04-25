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
CURRENT_GEOPOLITICAL_AS_OF = "2026-04-25"
CURRENT_GEOPOLITICAL_CONTEXT = {
    "as_of": CURRENT_GEOPOLITICAL_AS_OF,
    "window_end": "2026-05-01",
    "priority": "maximize_1w_return",
    "summary": (
        "2026-05-01까지의 1주 수익률 극대화를 목표로, 유가 충격 리스크, 다음 주 대형 기술주 실적, "
        "FOMC와 BOJ 이벤트를 반영한 단기 이벤트 드리븐 오버레이"
    ),
    "drivers": [
        {
            "key": "oil_geopolitics",
            "label": "미-이란/호르무즈발 유가 이벤트",
            "portfolio_effect": "USO 비중 확대, GLD는 보조 헤지",
        },
        {
            "key": "mag7_earnings",
            "label": "다음 주 대형 기술주 실적 집중",
            "portfolio_effect": "QQQ 전술 비중 확대",
        },
        {
            "key": "central_bank_week",
            "label": "FOMC와 BOJ 이벤트 주간",
            "portfolio_effect": "SPY는 완충용, EWJ는 상한 축소",
        },
    ],
}


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


def get_current_geopolitical_context() -> dict[str, object]:
    return {
        "as_of": CURRENT_GEOPOLITICAL_CONTEXT["as_of"],
        "window_end": CURRENT_GEOPOLITICAL_CONTEXT["window_end"],
        "priority": CURRENT_GEOPOLITICAL_CONTEXT["priority"],
        "summary": CURRENT_GEOPOLITICAL_CONTEXT["summary"],
        "drivers": [dict(item) for item in CURRENT_GEOPOLITICAL_CONTEXT["drivers"]],
    }


def build_weight_candidates(seed: int = 42, num_random: int = 40000) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    candidates: list[np.ndarray] = []

    for i in range(len(UNIVERSE)):
        weights = np.zeros(len(UNIVERSE))
        weights[i] = 1.0
        candidates.append(weights)

    for i in range(len(UNIVERSE)):
        for j in range(i + 1, len(UNIVERSE)):
            for ratio in np.linspace(0.1, 0.9, 9):
                weights = np.zeros(len(UNIVERSE))
                weights[i] = ratio
                weights[j] = 1.0 - ratio
                candidates.append(weights)

    candidates.extend(rng.dirichlet(alpha=np.ones(len(UNIVERSE)), size=num_random))
    return candidates


def evaluate_static_portfolio(daily_returns: pd.DataFrame, weights: np.ndarray) -> dict[str, float]:
    portfolio_daily = daily_returns.to_numpy() @ weights
    cumulative = np.cumprod(1.0 + portfolio_daily)
    total_return = float(cumulative[-1] - 1.0)
    annualized_volatility = float(np.std(portfolio_daily, ddof=0) * np.sqrt(252.0))
    running_max = np.maximum.accumulate(cumulative)
    max_dd = float((cumulative / running_max - 1.0).min())
    return {
        "total_return": total_return,
        "total_return_pct": total_return * 100.0,
        "annualized_volatility": annualized_volatility,
        "annualized_volatility_pct": annualized_volatility * 100.0,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd * 100.0,
    }


def current_profile_spec(risk_profile: str) -> dict[str, object]:
    if risk_profile == "conservative":
        return {
            "name": "1주 밸런스형",
            "objective": "5월 1일까지의 이벤트 드리븐 수익을 노리되, GLD·SPY로 급변동을 완충",
            "premia": np.array([0.04, 0.06, 0.02, 0.09, -0.05]),
            "max_single_weight": 0.45,
            "max_equity_pair": 0.70,
            "min_weights": {"QQQ": 0.10, "USO": 0.15, "GLD": 0.05},
            "max_weights": {"EWJ": 0.08, "GLD": 0.18},
        }
    if risk_profile == "aggressive":
        return {
            "name": "1주 수익극대형",
            "objective": "5월 1일까지의 단기 알파 극대화. 유가 이벤트와 빅테크 실적을 강하게 반영",
            "premia": np.array([0.03, 0.09, 0.01, 0.13, -0.06]),
            "max_single_weight": 0.55,
            "max_equity_pair": 0.65,
            "min_weights": {"QQQ": 0.20, "USO": 0.25},
            "max_weights": {"GLD": 0.10, "EWJ": 0.05, "SPY": 0.25},
        }
    raise ValueError(f"Unsupported risk profile: {risk_profile}")


def candidate_satisfies_constraints(weights: np.ndarray, spec: dict[str, object]) -> bool:
    max_single_weight = float(spec["max_single_weight"])
    max_equity_pair = float(spec["max_equity_pair"])
    min_weights = spec.get("min_weights", {})
    max_weights = spec.get("max_weights", {})

    if float(weights.max()) > max_single_weight + 1e-12:
        return False
    if float(weights[0] + weights[1]) > max_equity_pair + 1e-12:
        return False

    for ticker, min_weight in min_weights.items():
        if float(weights[UNIVERSE.index(ticker)]) + 1e-12 < float(min_weight):
            return False

    for ticker, max_weight in max_weights.items():
        if float(weights[UNIVERSE.index(ticker)]) > float(max_weight) + 1e-12:
            return False

    return True


def optimize_current_portfolio(prices: pd.DataFrame, evaluation_period: str, risk_profile: str) -> dict[str, object]:
    spec = current_profile_spec(risk_profile)
    evaluation_start = prices.index.max() - period_to_offset(evaluation_period)
    eval_prices = prices.loc[prices.index >= evaluation_start, UNIVERSE].dropna(how="any")
    daily_returns = eval_prices.pct_change().dropna(how="any")

    equal_weights = np.repeat(1.0 / len(UNIVERSE), len(UNIVERSE))
    if daily_returns.empty:
        metrics = evaluate_static_portfolio(pd.DataFrame(np.zeros((2, len(UNIVERSE))), columns=UNIVERSE), equal_weights)
        return {
            "name": str(spec["name"]),
            "objective": str(spec["objective"]),
            "weights": {ticker: float(equal_weights[i]) for i, ticker in enumerate(UNIVERSE)},
            "metrics": metrics,
            "context_as_of": CURRENT_GEOPOLITICAL_AS_OF,
        }

    adjusted_expected_returns = daily_returns.mean().to_numpy() * 252.0 + np.asarray(spec["premia"], dtype=float)

    best_weights = None
    best_metrics = None
    best_score = None

    for weights in build_weight_candidates():
        if not candidate_satisfies_constraints(weights, spec):
            continue

        metrics = evaluate_static_portfolio(daily_returns, weights)
        scenario_return = float(weights @ adjusted_expected_returns)
        if risk_profile == "conservative":
            score = (metrics["annualized_volatility"], -scenario_return)
        else:
            score = (-scenario_return, metrics["annualized_volatility"])

        if best_score is None or score < best_score:
            best_score = score
            best_weights = weights
            best_metrics = metrics

    if best_weights is None or best_metrics is None:
        best_weights = equal_weights
        best_metrics = evaluate_static_portfolio(daily_returns, best_weights)

    return {
        "name": str(spec["name"]),
        "objective": str(spec["objective"]),
        "weights": {ticker: float(best_weights[i]) for i, ticker in enumerate(UNIVERSE)},
        "metrics": best_metrics,
        "context_as_of": CURRENT_GEOPOLITICAL_AS_OF,
    }


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
