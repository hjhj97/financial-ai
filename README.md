# Assignment for AI Project (Financial AI), Ajou Graduate School in 2026 Spring

This repository contains course work for the Financial AI project at Ajou Graduate School in Spring 2026. The project focuses on designing and explaining an ETF allocation strategy under a constrained portfolio challenge using five ETFs: `SPY`, `QQQ`, `GLD`, `USO`, and `EWJ`.

The repository includes strategy reports, assignment notes, and a simple backtesting script used to evaluate how the proposed portfolio rules would have performed on recent market data. The main goal is not only to seek returns, but also to demonstrate a clear, reproducible investment logic with risk-aware portfolio construction.

## Interactive Dashboard

You can generate a small web dashboard to visualize how the strategy performance changed over time versus ETF movements.

1. Generate dashboard data:
```bash
python generate_web_data.py --period 1y --output web/backtest_data.json
```
2. Run a local web server:
```bash
python -m http.server 8000 --directory web
```
3. Open:
```text
http://localhost:8000
```

The dashboard includes:
- Strategy vs SPY cumulative performance (index base 100)
- ETF price index comparison (SPY, QQQ, GLD, USO, EWJ)
- Weekly rebalancing weights over time
- Interactive strategy controls (momentum/risk/weight caps/rebalance period/USO toggle)
