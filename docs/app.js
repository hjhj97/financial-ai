const UNIVERSE = ["SPY", "QQQ", "GLD", "USO", "EWJ"];

const COLORS = {
  strategy: "#0f766e",
  spy: "#1d4ed8",
  SPY: "#1d4ed8",
  QQQ: "#7c3aed",
  GLD: "#b45309",
  USO: "#dc2626",
  EWJ: "#0ea5e9",
};

const controls = {
  momShortWeight: document.getElementById("momShortWeight"),
  riskPenaltyStrength: document.getElementById("riskPenaltyStrength"),
  gldTilt: document.getElementById("gldTilt"),
  equityTilt: document.getElementById("equityTilt"),
  maxSingle: document.getElementById("maxSingle"),
  maxEquityPair: document.getElementById("maxEquityPair"),
  rebalanceDays: document.getElementById("rebalanceDays"),
  allowUSO: document.getElementById("allowUSO"),
};

const controlValues = {
  momShortWeight: document.getElementById("momShortWeightValue"),
  riskPenaltyStrength: document.getElementById("riskPenaltyStrengthValue"),
  gldTilt: document.getElementById("gldTiltValue"),
  equityTilt: document.getElementById("equityTiltValue"),
  maxSingle: document.getElementById("maxSingleValue"),
  maxEquityPair: document.getElementById("maxEquityPairValue"),
  rebalanceDays: document.getElementById("rebalanceDaysValue"),
};

let charts = {
  strategy: null,
  etf: null,
  weight: null,
};

const appState = {
  data: null,
};

function pct(v) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function zscore(values) {
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length;
  const sigma = Math.sqrt(variance);
  if (!sigma) return values.map(() => 0);
  return values.map((v) => (v - mean) / sigma);
}

function std(values) {
  if (!values.length) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length;
  return Math.sqrt(variance);
}

function covariance(a, b) {
  const n = Math.min(a.length, b.length);
  if (!n) return 0;
  const ma = a.slice(0, n).reduce((x, y) => x + y, 0) / n;
  const mb = b.slice(0, n).reduce((x, y) => x + y, 0) / n;
  let sum = 0;
  for (let i = 0; i < n; i += 1) sum += (a[i] - ma) * (b[i] - mb);
  return sum / n;
}

function maxDrawdown(curve) {
  let peak = curve[0];
  let mdd = 0;
  for (const v of curve) {
    peak = Math.max(peak, v);
    mdd = Math.min(mdd, v / peak - 1);
  }
  return mdd;
}

function getParams() {
  return {
    momShortWeight: Number(controls.momShortWeight.value),
    riskPenaltyStrength: Number(controls.riskPenaltyStrength.value),
    gldTilt: Number(controls.gldTilt.value),
    equityTilt: Number(controls.equityTilt.value),
    maxSingle: Number(controls.maxSingle.value),
    maxEquityPair: Number(controls.maxEquityPair.value),
    rebalanceDays: Number(controls.rebalanceDays.value),
    allowUSO: controls.allowUSO.checked,
  };
}

function updateControlValueLabels(params) {
  controlValues.momShortWeight.textContent = params.momShortWeight.toFixed(2);
  controlValues.riskPenaltyStrength.textContent = params.riskPenaltyStrength.toFixed(2);
  controlValues.gldTilt.textContent = params.gldTilt.toFixed(2);
  controlValues.equityTilt.textContent = params.equityTilt.toFixed(2);
  controlValues.maxSingle.textContent = `${Math.round(params.maxSingle * 100)}%`;
  controlValues.maxEquityPair.textContent = `${Math.round(params.maxEquityPair * 100)}%`;
  controlValues.rebalanceDays.textContent = String(params.rebalanceDays);
}

function classifyRegime(featureMap) {
  const spyMom = featureMap.SPY.momLong;
  const qqqMom = featureMap.QQQ.momLong;
  const spyVol = featureMap.SPY.vol;
  const vols = Object.values(featureMap).map((v) => v.vol).sort((a, b) => a - b);
  const medianVol = vols[Math.floor(vols.length / 2)];
  const gldMom = featureMap.GLD.momLong;
  if (spyMom > 0 && qqqMom > 0 && spyVol <= medianVol * 1.2) return "risk_on";
  if (spyMom < 0 && qqqMom < 0 && gldMom > 0) return "defensive";
  return "mixed";
}

function applyConstraints(weights, params) {
  const adjusted = { ...weights };
  if (!params.allowUSO) adjusted.USO = 0;
  for (const ticker of UNIVERSE) {
    adjusted[ticker] = Math.min(Math.max(adjusted[ticker] || 0, 0), params.maxSingle);
  }

  const eqSum = (adjusted.SPY || 0) + (adjusted.QQQ || 0);
  if (eqSum > params.maxEquityPair) {
    const scale = params.maxEquityPair / eqSum;
    adjusted.SPY *= scale;
    adjusted.QQQ *= scale;
  }

  if (Object.values(adjusted).filter((v) => v > 0).length < 2) {
    adjusted.SPY = Math.max(adjusted.SPY || 0, 0.35);
    adjusted.GLD = Math.max(adjusted.GLD || 0, 0.20);
  }

  let sum = Object.values(adjusted).reduce((a, b) => a + b, 0);
  if (!sum) {
    adjusted.SPY = 0.6;
    adjusted.GLD = 0.4;
    sum = 1;
  }
  for (const ticker of UNIVERSE) adjusted[ticker] /= sum;
  return adjusted;
}

function simulateStrategy(data, params) {
  const prices = data.etf_index_100;
  const n = data.dates.length;
  const lookback = 21;
  const dailyRet = {};

  for (const ticker of UNIVERSE) {
    dailyRet[ticker] = [];
    for (let i = 0; i < n; i += 1) {
      if (i === 0) dailyRet[ticker].push(0);
      else dailyRet[ticker].push(prices[ticker][i] / prices[ticker][i - 1] - 1);
    }
  }

  const strategyRet = Array(n).fill(0);
  const rebalanceLog = [];
  let currentW = { SPY: 0.35, QQQ: 0.2, GLD: 0.35, USO: 0, EWJ: 0.1 };
  let holdUntil = lookback;

  for (let i = 1; i < n; i += 1) {
    if (i >= lookback && i >= holdUntil) {
      const active = params.allowUSO ? UNIVERSE : UNIVERSE.filter((t) => t !== "USO");
      const featureMap = {};
      for (const ticker of active) {
        const p = prices[ticker];
        const r = dailyRet[ticker];
        featureMap[ticker] = {
          momShort: p[i] / p[i - 5] - 1,
          momLong: p[i] / p[i - 20] - 1,
          vol: std(r.slice(i - 19, i + 1)) * Math.sqrt(252),
          drawdown: p[i] / Math.max(...p.slice(i - 19, i + 1)) - 1,
        };
      }

      const tickers = Object.keys(featureMap);
      const momShortZ = zscore(tickers.map((t) => featureMap[t].momShort));
      const momLongZ = zscore(tickers.map((t) => featureMap[t].momLong));
      const volZ = zscore(tickers.map((t) => featureMap[t].vol));
      const ddZ = zscore(tickers.map((t) => Math.abs(featureMap[t].drawdown)));
      const score = {};

      for (let j = 0; j < tickers.length; j += 1) {
        const trend = params.momShortWeight * momShortZ[j] + (1 - params.momShortWeight) * momLongZ[j];
        const riskPenalty = params.riskPenaltyStrength * (0.6 * volZ[j] + 0.4 * ddZ[j]);
        score[tickers[j]] = trend - riskPenalty;
      }

      let selected = tickers.filter((t) => score[t] > 0);
      if (selected.length < 2) {
        selected = [...tickers].sort((a, b) => score[b] - score[a]).slice(0, 2);
      }

      let invVolSum = 0;
      const provisional = { SPY: 0, QQQ: 0, GLD: 0, USO: 0, EWJ: 0 };
      for (const t of selected) invVolSum += 1 / Math.max(featureMap[t].vol, 1e-6);
      for (const t of selected) provisional[t] = (1 / Math.max(featureMap[t].vol, 1e-6)) / invVolSum;

      const regime = classifyRegime({
        SPY: featureMap.SPY || { momLong: 0, vol: 0 },
        QQQ: featureMap.QQQ || { momLong: 0, vol: 0 },
        GLD: featureMap.GLD || { momLong: 0, vol: 0 },
      });

      if (regime === "risk_on") {
        provisional.SPY *= 1 + 0.15 + params.equityTilt;
        provisional.QQQ *= 1 + 0.1 + params.equityTilt;
        provisional.GLD *= 1 - 0.1 + params.gldTilt;
      } else if (regime === "defensive") {
        provisional.SPY *= 0.85 + params.equityTilt;
        provisional.QQQ *= 0.75 + params.equityTilt;
        provisional.GLD *= 1.35 + params.gldTilt;
      } else {
        provisional.SPY *= 1 + params.equityTilt;
        provisional.QQQ *= 0.9 + params.equityTilt;
        provisional.GLD *= 1.2 + params.gldTilt;
      }

      currentW = applyConstraints(provisional, params);
      holdUntil = i + params.rebalanceDays;
      rebalanceLog.push({ date: data.dates[i], regime, ...currentW });
    }

    let ret = 0;
    for (const ticker of UNIVERSE) ret += (currentW[ticker] || 0) * dailyRet[ticker][i];
    strategyRet[i] = ret;
  }

  const strategyIndex = [];
  const spyIndex = [];
  let s = 100;
  let b = 100;
  for (let i = 0; i < n; i += 1) {
    s *= 1 + strategyRet[i];
    b *= 1 + dailyRet.SPY[i];
    strategyIndex.push(s);
    spyIndex.push(b);
  }

  const stratTotal = strategyIndex.at(-1) / strategyIndex[0] - 1;
  const spyTotal = spyIndex.at(-1) / spyIndex[0] - 1;
  const stratVol = std(strategyRet.slice(1)) * Math.sqrt(252);
  const spyVol = std(dailyRet.SPY.slice(1)) * Math.sqrt(252);
  const spyDailyVar = (spyVol / Math.sqrt(252)) ** 2;
  const betaRaw = spyDailyVar ? covariance(strategyRet.slice(1), dailyRet.SPY.slice(1)) / spyDailyVar : 0;

  return {
    strategyIndex,
    spyIndex,
    rebalanceLog,
    summary: {
      strategy_total_return_pct: stratTotal * 100,
      spy_total_return_pct: spyTotal * 100,
      alpha_vs_spy_pct: (stratTotal - spyTotal) * 100,
      annualized_volatility_pct: stratVol * 100,
      max_drawdown_pct: maxDrawdown(strategyIndex) * 100,
      beta_vs_spy: Number.isFinite(betaRaw) ? betaRaw : 0,
    },
  };
}

function makeCard(label, value) {
  const el = document.createElement("article");
  el.className = "card";
  el.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
  return el;
}

function renderCards(summary) {
  const cards = document.getElementById("summary-cards");
  cards.innerHTML = "";
  cards.appendChild(makeCard("전략 누적수익률", pct(summary.strategy_total_return_pct)));
  cards.appendChild(makeCard("SPY 누적수익률", pct(summary.spy_total_return_pct)));
  cards.appendChild(makeCard("전략-SPY", pct(summary.alpha_vs_spy_pct)));
  cards.appendChild(makeCard("전략 MDD", pct(summary.max_drawdown_pct)));
  cards.appendChild(makeCard("전략 변동성", `${summary.annualized_volatility_pct.toFixed(2)}%`));
  cards.appendChild(makeCard("Beta (vs SPY)", summary.beta_vs_spy.toFixed(2)));
}

function renderStrategyChart(labels, strategySeries, spySeries) {
  if (charts.strategy) charts.strategy.destroy();
  charts.strategy = new Chart(document.getElementById("strategyChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Strategy", data: strategySeries, borderColor: COLORS.strategy, borderWidth: 2.3, pointRadius: 0 },
        { label: "SPY", data: spySeries, borderColor: COLORS.spy, borderWidth: 1.8, pointRadius: 0 },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top" } },
      scales: { y: { title: { display: true, text: "Index (Start=100)" } } },
    },
  });
}

function renderEtfChart(labels, etfIndex100) {
  if (charts.etf) charts.etf.destroy();
  charts.etf = new Chart(document.getElementById("etfChart"), {
    type: "line",
    data: {
      labels,
      datasets: UNIVERSE.map((ticker) => ({
        label: ticker,
        data: etfIndex100[ticker],
        borderColor: COLORS[ticker],
        borderWidth: ticker === "SPY" ? 2 : 1.5,
        pointRadius: 0,
      })),
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top" } },
      scales: { y: { title: { display: true, text: "Index (Start=100)" } } },
    },
  });
}

function renderWeightChart(rebalanceLog) {
  if (charts.weight) charts.weight.destroy();
  charts.weight = new Chart(document.getElementById("weightChart"), {
    type: "bar",
    data: {
      labels: rebalanceLog.map((d) => d.date),
      datasets: UNIVERSE.map((ticker) => ({
        label: ticker,
        data: rebalanceLog.map((d) => (d[ticker] || 0) * 100),
        backgroundColor: COLORS[ticker],
        stack: "weights",
      })),
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { stacked: true },
        y: { stacked: true, min: 0, max: 100, title: { display: true, text: "Weight (%)" } },
      },
    },
  });
}

function rerenderAll() {
  const params = getParams();
  updateControlValueLabels(params);
  const result = simulateStrategy(appState.data, params);
  renderCards(result.summary);
  renderStrategyChart(appState.data.dates, result.strategyIndex, result.spyIndex);
  renderEtfChart(appState.data.dates, appState.data.etf_index_100);
  renderWeightChart(result.rebalanceLog);
}

function attachEvents() {
  Object.values(controls).forEach((el) => {
    const ev = el.type === "checkbox" ? "change" : "input";
    el.addEventListener(ev, rerenderAll);
  });
}

function cloneData(raw) {
  return {
    ...raw,
    dates: [...raw.dates],
    etf_index_100: Object.fromEntries(UNIVERSE.map((t) => [t, [...raw.etf_index_100[t]]])),
  };
}

async function main() {
  const res = await fetch("./backtest_data.json");
  if (!res.ok) throw new Error("Failed to load backtest_data.json");
  appState.data = cloneData(await res.json());
  attachEvents();
  rerenderAll();
}

main().catch((err) => {
  const cards = document.getElementById("summary-cards");
  cards.innerHTML = `<article class="card"><div class="label">Error</div><div class="value">${err.message}</div></article>`;
});
