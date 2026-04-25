# Assignment for AI Project (Financial AI), Ajou Graduate School in 2026 Spring

This repository contains course work for the Financial AI project at Ajou Graduate School in Spring 2026. The project focuses on designing and explaining an ETF allocation strategy under a constrained portfolio challenge using five ETFs: `SPY`, `QQQ`, `GLD`, `USO`, and `EWJ`.

The repository includes strategy reports, assignment notes, a backtesting script, and a small interactive dashboard. The current version is tuned for a one-week holding window ending on `2026-05-01`, so the strategy intentionally puts more weight on immediate catalysts such as geopolitics, central-bank events, and next-week big-tech earnings than on medium-term macro balance.

## Current Strategy

The portfolio uses a two-layer process.

1. A quantitative base layer scores the five ETFs with recent momentum, volatility, and drawdown features.
2. A one-week event overlay adjusts the final recommendation for the actual submission window.

As of `2026-04-25`, the event overlay assumes:

- oil-sensitive trades matter because U.S.-Iran and Strait of Hormuz risk can move crude quickly
- `QQQ` deserves tactical importance because next week's large-cap tech earnings can dominate a one-week horizon
- `EWJ` should stay capped because BOJ event risk can make Japan exposure noisier than usual

The dashboard's two buttons are now interpreted as:

- `1주 밸런스형`: event-driven but still cushioned with some hedge
- `1주 수익극대형`: stronger tactical tilt toward the highest-conviction one-week catalysts

## Interactive Dashboard

You can generate a small web dashboard to visualize how the strategy behaves over time and apply the two current one-week recommendation profiles from the UI.

1. Generate dashboard data:
```bash
python generate_web_data.py --period 1y --output docs/backtest_data.json
```
2. Run a local web server:
```bash
python -m http.server 8000 --directory docs
```
3. Open:
```text
http://localhost:8000
```

The dashboard includes:
- Strategy vs SPY cumulative performance (index base 100)
- ETF price index comparison (SPY, QQQ, GLD, USO, EWJ)
- ETF contribution chart
- Interactive weight sliders
- Two one-click recommendation buttons reflecting the current one-week event overlay
