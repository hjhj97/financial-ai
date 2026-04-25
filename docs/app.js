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
  SPY: document.getElementById("wSPY"),
  QQQ: document.getElementById("wQQQ"),
  GLD: document.getElementById("wGLD"),
  USO: document.getElementById("wUSO"),
  EWJ: document.getElementById("wEWJ"),
};

const presetButtons = {
  conservative: document.getElementById("presetConservative"),
  aggressive: document.getElementById("presetAggressive"),
};

const presetMeta = document.getElementById("presetMeta");

const controlValues = {
  SPY: document.getElementById("wSPYValue"),
  QQQ: document.getElementById("wQQQValue"),
  GLD: document.getElementById("wGLDValue"),
  USO: document.getElementById("wUSOValue"),
  EWJ: document.getElementById("wEWJValue"),
};

const charts = {
  strategy: null,
  etf: null,
  allocation: null,
  contribution: null,
};

const appState = {
  data: null,
  activePreset: null,
};

function pct(v) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
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

function getRawWeightsFromControls() {
  return Object.fromEntries(UNIVERSE.map((t) => [t, Number(controls[t].value)]));
}

function normalizeWeights(rawWeights) {
  const total = UNIVERSE.reduce((acc, t) => acc + (rawWeights[t] || 0), 0);
  if (total <= 0) {
    const equal = 1 / UNIVERSE.length;
    return Object.fromEntries(UNIVERSE.map((t) => [t, equal]));
  }
  return Object.fromEntries(UNIVERSE.map((t) => [t, (rawWeights[t] || 0) / total]));
}

function weightsToIntegerPercents(weights) {
  const normalized = normalizeWeights(weights);
  const rows = UNIVERSE.map((ticker) => {
    const scaled = normalized[ticker] * 100;
    const floorValue = Math.floor(scaled);
    return {
      ticker,
      floorValue,
      frac: scaled - floorValue,
    };
  });

  let remain = 100 - rows.reduce((acc, row) => acc + row.floorValue, 0);
  rows.sort((a, b) => b.frac - a.frac);

  for (let i = 0; i < rows.length && remain > 0; i += 1) {
    rows[i].floorValue += 1;
    remain -= 1;
  }

  return Object.fromEntries(rows.map((row) => [row.ticker, row.floorValue]));
}

function setControlWeights(weights) {
  const integerWeights = weightsToIntegerPercents(weights);
  for (const ticker of UNIVERSE) {
    controls[ticker].value = String(integerWeights[ticker]);
  }
}

function updateControlLabels(rawWeights) {
  for (const t of UNIVERSE) {
    controlValues[t].textContent = `${rawWeights[t]}%`;
  }
  const total = UNIVERSE.reduce((acc, t) => acc + rawWeights[t], 0);
  const totalEl = document.getElementById("weightTotalText");
  totalEl.textContent = `입력 비중 합계: ${total}% (차트 계산 시 자동으로 100% 정규화)`;
}

function formatCurrentContext() {
  const context = appState.data?.current_context;
  if (!context) {
    return "버튼을 누르면 계산된 최적 ETF 비율이 슬라이더에 반영됩니다.";
  }
  const endDate = context.window_end ? ` ~ ${context.window_end}` : "";
  return `${context.as_of}${endDate} 단기 목표: ${context.summary}`;
}

function formatProfileMeta(profile) {
  if (!profile) {
    return formatCurrentContext();
  }
  const m = profile.metrics || {};
  const r = Number(m.total_return_pct || 0).toFixed(2);
  const v = Number(m.annualized_volatility_pct || 0).toFixed(2);
  const d = Number(m.max_drawdown_pct || 0).toFixed(2);
  return `${profile.name} | 목표: ${profile.objective} | 누적수익률 ${r}%, 연변동성 ${v}%, MDD ${d}%`;
}

function updatePresetUI(activeKey) {
  for (const [key, btn] of Object.entries(presetButtons)) {
    if (!btn) continue;
    btn.classList.toggle("active", key === activeKey);
  }
  const profiles = appState.data?.risk_profiles || {};
  const activeProfile = activeKey ? profiles[activeKey] : null;
  if (presetMeta) presetMeta.textContent = formatProfileMeta(activeProfile);
}

function applyPreset(key) {
  const profile = appState.data?.risk_profiles?.[key];
  if (!profile || !profile.weights) return;
  setControlWeights(profile.weights);
  appState.activePreset = key;
  updatePresetUI(key);
  rerenderAll();
}

function buildDailyReturns(etfIndex100) {
  const n = etfIndex100.SPY.length;
  const dailyRet = {};
  for (const ticker of UNIVERSE) {
    dailyRet[ticker] = [];
    for (let i = 0; i < n; i += 1) {
      if (i === 0) dailyRet[ticker].push(0);
      else dailyRet[ticker].push(etfIndex100[ticker][i] / etfIndex100[ticker][i - 1] - 1);
    }
  }
  return dailyRet;
}

function simulateFixedAllocation(data, weights) {
  const n = data.dates.length;
  const dailyRet = buildDailyReturns(data.etf_index_100);
  const portfolioRet = Array(n).fill(0);
  const contributionCurves = Object.fromEntries(UNIVERSE.map((t) => [t, Array(n).fill(0)]));

  for (let i = 1; i < n; i += 1) {
    let r = 0;
    for (const t of UNIVERSE) {
      const contrib = weights[t] * dailyRet[t][i];
      contributionCurves[t][i] = contributionCurves[t][i - 1] + contrib * 100;
      r += contrib;
    }
    portfolioRet[i] = r;
  }

  const strategyIndex = [];
  const spyIndex = [];
  let s = 100;
  let b = 100;
  for (let i = 0; i < n; i += 1) {
    s *= 1 + portfolioRet[i];
    b *= 1 + dailyRet.SPY[i];
    strategyIndex.push(s);
    spyIndex.push(b);
  }

  const stratTotal = strategyIndex.at(-1) / strategyIndex[0] - 1;
  const spyTotal = spyIndex.at(-1) / spyIndex[0] - 1;
  const stratVol = std(portfolioRet.slice(1)) * Math.sqrt(252);
  const spyVol = std(dailyRet.SPY.slice(1)) * Math.sqrt(252);
  const spyDailyVar = (spyVol / Math.sqrt(252)) ** 2;
  const betaRaw = spyDailyVar ? covariance(portfolioRet.slice(1), dailyRet.SPY.slice(1)) / spyDailyVar : 0;

  return {
    strategyIndex,
    spyIndex,
    contributionCurves,
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
  cards.appendChild(makeCard("포트폴리오 누적수익률", pct(summary.strategy_total_return_pct)));
  cards.appendChild(makeCard("SPY 누적수익률", pct(summary.spy_total_return_pct)));
  cards.appendChild(makeCard("포트폴리오-SPY", pct(summary.alpha_vs_spy_pct)));
  cards.appendChild(makeCard("포트폴리오 MDD", pct(summary.max_drawdown_pct)));
  cards.appendChild(makeCard("포트폴리오 변동성", `${summary.annualized_volatility_pct.toFixed(2)}%`));
  cards.appendChild(makeCard("Beta (vs SPY)", summary.beta_vs_spy.toFixed(2)));
}

function renderStrategyChart(labels, strategySeries, spySeries) {
  if (charts.strategy) charts.strategy.destroy();
  charts.strategy = new Chart(document.getElementById("strategyChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Portfolio", data: strategySeries, borderColor: COLORS.strategy, borderWidth: 2.3, pointRadius: 0 },
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

function renderAllocationChart(weights) {
  if (charts.allocation) charts.allocation.destroy();
  charts.allocation = new Chart(document.getElementById("allocationChart"), {
    type: "doughnut",
    data: {
      labels: UNIVERSE,
      datasets: [
        {
          data: UNIVERSE.map((t) => weights[t] * 100),
          backgroundColor: UNIVERSE.map((t) => COLORS[t]),
          borderColor: "#ffffff",
          borderWidth: 2,
        },
      ],
    },
    options: { plugins: { legend: { position: "right" } } },
  });
}

function renderContributionChart(labels, contributionCurves) {
  if (charts.contribution) charts.contribution.destroy();
  charts.contribution = new Chart(document.getElementById("contributionChart"), {
    type: "line",
    data: {
      labels,
      datasets: UNIVERSE.map((ticker) => ({
        label: ticker,
        data: contributionCurves[ticker],
        borderColor: COLORS[ticker],
        borderWidth: ticker === "SPY" ? 2.2 : 1.6,
        pointRadius: 0,
      })),
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top" } },
      scales: { y: { title: { display: true, text: "Cumulative Contribution (%p)" } } },
    },
  });
}

function rerenderAll() {
  const rawWeights = getRawWeightsFromControls();
  updateControlLabels(rawWeights);
  const effectiveWeights = normalizeWeights(rawWeights);
  const result = simulateFixedAllocation(appState.data, effectiveWeights);
  renderCards(result.summary);
  renderStrategyChart(appState.data.dates, result.strategyIndex, result.spyIndex);
  renderEtfChart(appState.data.dates, appState.data.etf_index_100);
  renderAllocationChart(effectiveWeights);
  renderContributionChart(appState.data.dates, result.contributionCurves);
}

function attachEvents() {
  Object.values(controls).forEach((el) =>
    el.addEventListener("input", () => {
      appState.activePreset = null;
      updatePresetUI(null);
      rerenderAll();
    }),
  );

  presetButtons.conservative?.addEventListener("click", () => applyPreset("conservative"));
  presetButtons.aggressive?.addEventListener("click", () => applyPreset("aggressive"));
}

function cloneData(raw) {
  return {
    ...raw,
    dates: [...raw.dates],
    etf_index_100: Object.fromEntries(UNIVERSE.map((t) => [t, [...raw.etf_index_100[t]]])),
    risk_profiles: raw.risk_profiles || null,
    current_context: raw.current_context || null,
  };
}

async function main() {
  const res = await fetch("./backtest_data.json");
  if (!res.ok) throw new Error("Failed to load backtest_data.json");
  appState.data = cloneData(await res.json());
  attachEvents();
  if (!appState.data.risk_profiles) {
    for (const btn of Object.values(presetButtons)) {
      if (btn) btn.disabled = true;
    }
  }
  applyPreset("aggressive");
}

main().catch((err) => {
  const cards = document.getElementById("summary-cards");
  cards.innerHTML = `<article class="card"><div class="label">Error</div><div class="value">${err.message}</div></article>`;
});
