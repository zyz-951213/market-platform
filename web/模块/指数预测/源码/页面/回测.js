import * as echarts from "echarts";
import { api } from "@/接口/客户端.js";
import {
  $, fmtPct, fmtNum, fmtPctSigned, escapeHtml, signedClass, freqLabel,
} from "@/公共/工具.js";
import {
  baseGrid, chartTheme, cashAssetColors, hexToRgba, recentZoomConfig, dateAxisLabel, layerColor,
} from "@/公共/主题.js";
import { overviewCache, channelLabels } from "@/公共/状态.js";
let equityChart;
let drawdownChart;
let positionChart;
let predDistChart;
let confusionChart;
let compareChart;
let cashAssetsChart;
let allocationChart;
let allocationMainChart;
let allocationIdleChart;
let allocationCache = null;
let allocationViewMode = "stack";
let accuracyPartitionCache = null;

export function resizeBacktestCharts() {
  [
    equityChart, drawdownChart, positionChart, predDistChart, confusionChart,
    compareChart, cashAssetsChart, allocationChart, allocationMainChart,
    allocationIdleChart,
  ].forEach((c) => c?.resize());
}

export function renderStrategySummary(summary) {
  const box = $("strategy-summary");
  if (!box || !summary) return;
  box.classList.remove("hidden");

  const pct = (v) => `${(Number(v) * 100).toFixed(0)}%`;
  const cash = summary.cash_management || {};
  const cls = summary.classification || {};
  let cashBrief = "闲置关闭";
  if (cash.enabled && cash.sleeves?.length) {
    cashBrief = cash.sleeves.map((s) => `${s.label}${pct(s.weight)}`).join(" · ");
  } else if (cash.enabled && cash.allocations?.length) {
    cashBrief = cash.allocations.map((a) => `${a.label}${pct(a.weight)}`).join("/");
  }

  const parts = [
    summary.philosophy || summary.headline || "当前策略",
    `底仓${pct(summary.base_position)}（牛/熊${pct(summary.bull_regime_base)}/${pct(summary.bear_regime_base)}）`,
    summary.position_cycle ? "阶梯建仓" : null,
    `信号 ±${cls.daily_threshold_pct ?? "--"}%`,
    summary.prompt_label || summary.prompt_version || null,
    `校准${summary.return_calibration ? "开" : "关"}`,
    cashBrief,
  ].filter(Boolean);

  const notes = (summary.notes || []).slice(0, 3).join(" · ");
  const title = [...parts, ...(summary.notes || [])].join(" · ");
  const tags = (summary.tags || []).slice(0, 8)
    .map((t) => `<span class="strategy-tag">${escapeHtml(t)}</span>`)
    .join("");
  box.innerHTML = `
    <div class="strategy-summary-line" title="${escapeHtml(title)}">${escapeHtml(parts.join(" · "))}</div>
    ${tags ? `<div class="strategy-summary-tags">${tags}</div>` : ""}
    ${notes ? `<div class="strategy-summary-notes">${escapeHtml(notes)}</div>` : ""}
  `;
}

function renderBacktestMetrics(data) {
  const m = data.metrics || {};
  $("bt-total").textContent = fmtPct(m.total_return);
  $("bt-bench").textContent = fmtPct(m.benchmark_total_return);
  $("bt-annual").textContent = fmtPct(m.annualized_return);
  $("bt-dd").textContent = fmtPct(m.max_drawdown);
  $("bt-sharpe").textContent = fmtNum(m.sharpe_ratio, 2);
  $("bt-cash-ret").textContent = m.cash_mgmt_enabled && m.cash_return != null ? fmtPctSigned(m.cash_return) : "--";
  $("bt-cash-ret").className = signedClass(m.cash_return);
  $("bt-strat-ret").textContent = m.cash_mgmt_enabled && m.strategy_only_return != null
    ? fmtPct(m.strategy_only_return)
    : (m.total_return != null ? fmtPct(m.total_return) : "--");
  $("bt-avg-pos").textContent = m.avg_position != null ? fmtPct(m.avg_position) : "--";
  if ($("bt-excess")) {
    $("bt-excess").textContent = m.excess_return != null ? fmtPctSigned(m.excess_return) : "--";
    $("bt-excess").className = signedClass(m.excess_return);
  }
  $("bt-acc").textContent = fmtPct(m.accuracy);
  $("bt-dir-acc").textContent = fmtPct(m.directional_accuracy);
  $("bt-trades").textContent = m.total_trades ?? "--";
  $("bt-pos").textContent = fmtPct(m.periods_in_market);
  const btFreq = data.backtest_bar_freq || data.bar_freq;
  const savedHint = data.saved_at ? ` · 更新 ${String(data.saved_at).replace("T", " ")}` : "";
  $("backtest-period").textContent = data.period?.start && data.period?.end
    ? `${data.model_name || "回测"} · ${freqLabel(btFreq, overviewCache?.bar_meta || data)} · ${data.period.start} → ${data.period.end}${savedHint}`
    : "暂无回测数据";
}

function renderEquityChart(data) {
  if (!equityChart) equityChart = echarts.init($("equity-chart"));
  const unified = (data.metrics?.allocation_mode || "") === "unified_sharpe";
  const hasCashSplit = !unified && Boolean(data.metrics?.cash_mgmt_enabled && data.strategy_only_equity?.length);
  const legend = hasCashSplit
    ? ["综合净值", "策略净收益", "买入持有"]
    : ["策略净值", "买入持有"];
  if ($("equity-chart-title")) {
    $("equity-chart-title").textContent = hasCashSplit
      ? "净值曲线（综合策略 + 策略净收益 vs 买入持有）"
      : "净值曲线（策略 vs 买入持有）";
  }

  const series = [
    {
      name: hasCashSplit ? "综合净值" : "策略净值",
      type: "line",
      smooth: true,
      showSymbol: false,
      data: data.strategy_equity,
      lineStyle: { color: "#3ecf8e", width: 2 },
      areaStyle: { color: "rgba(62,207,142,0.1)" },
    },
    {
      name: "买入持有",
      type: "line",
      smooth: true,
      showSymbol: false,
      data: data.benchmark_equity,
      lineStyle: { color: "#9aa0a6", width: 2, type: "dashed" },
    },
  ];
  if (hasCashSplit) {
    series.splice(1, 0, {
      name: "策略净收益",
      type: "line",
      smooth: true,
      showSymbol: false,
      data: data.strategy_only_equity,
      lineStyle: { color: "#4c8bf5", width: 1.5, type: "dotted" },
    });
  }

  equityChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (items) => {
        const rows = items.map((it) => `${it.marker}${it.seriesName}: ${((it.data - 1) * 100).toFixed(2)}%`);
        return `${items[0].axisValue}<br/>${rows.join("<br/>")}`;
      },
    },
    legend: { data: legend, textStyle: { color: "#9aa0a6" } },
    grid: { ...baseGrid(), bottom: 56 },
    dataZoom: recentZoomConfig(data.dates),
    xAxis: { type: "category", data: data.dates, axisLabel: dateAxisLabel() },
    yAxis: {
      scale: true,
      axisLabel: {
        color: "#9aa0a6",
        formatter: (v) => `${((v - 1) * 100).toFixed(0)}%`,
      },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series,
  }, { notMerge: true });
}

function renderCashAssetsConfig(cash) {
  const box = $("cash-assets-config");
  if (!box) return;
  const strat = cash.strategy || {};
  const stats = cash.backtest_stats || {};
  const basePct = (v) => (v != null ? `${(v * 100).toFixed(0)}%` : "--");
  const sleeves = strat.sleeves || [];
  const allocations = strat.allocations || [];

  const strategyItems = [
    strat.philosophy || strat.headline || "趋势定底仓 · 研判调叠加",
    strat.allocation_mode === "unified_sharpe"
      ? "配置：进攻(沪深300+纳指) / 防守(金+债) / 现金流(货币+逆回购)"
      : `常态底仓 ${basePct(strat.base_position)} · 牛/熊 ${basePct(strat.bull_regime_base)}/${basePct(strat.bear_regime_base)}（相对 SMA60）`,
    strat.allocation_mode === "unified_sharpe"
      ? "配权：组间滚动夏普 + 组内逆波动；LLM tilt / risk-off"
      : (strat.position_cycle
        ? "仓位：底仓 + 研判叠加 · 阶梯建仓；减仓需结构性确认"
        : "仓位：底仓 + 研判叠加"),
    strat.classification
      ? `信号：日频涨/跌/平 ±${strat.classification.daily_threshold_pct ?? "--"}%（下一交易日）`
      : "信号：日频涨/跌/平",
    `${strat.active_model || "DeepSeek"} · ${strat.prompt_label || strat.prompt_version || "--"}`,
    `收益校准：${strat.return_calibration ? "开启" : "关闭"}`,
  ];

  let allocHtml;
  if (sleeves.length) {
    const byKey = Object.fromEntries(allocations.map((a) => [a.key, a]));
    allocHtml = `<ul class="cash-sleeve-list">${sleeves.map((sleeve) => {
      const kids = (sleeve.assets || [])
        .map((k) => byKey[k])
        .filter(Boolean)
        .map((a) => `${escapeHtml(a.label)} ${escapeHtml(String(a.symbol))} ${(a.weight * 100).toFixed(0)}%`)
        .join(" · ");
      return `<li><strong>${escapeHtml(sleeve.label)} ${(sleeve.weight * 100).toFixed(0)}%</strong>
        <span class="chip-sub">${escapeHtml(sleeve.desc || "")}</span>
        <div class="cash-sleeve-assets">${kids}</div></li>`;
    }).join("")}</ul>`;
  } else if (allocations.length) {
    allocHtml = `<ul>${allocations.map((a) => `
        <li>${escapeHtml(a.label)} ${escapeHtml(a.symbol)} · ${(a.weight * 100).toFixed(0)}%</li>
      `).join("")}</ul>`;
  } else {
    allocHtml = `<p class="empty-hint">暂无配置</p>`;
  }

  const avgIdle = stats.avg_idle;
  const unified = strat.allocation_mode === "unified_sharpe";
  const formula = unified
    ? "组合权重 Σ = 1；进攻/防守/现金流由滚动夏普重标定"
    : (avgIdle != null
      ? `闲置比例 ≈ 1 − 仓位 · 回测平均仓位 ${basePct(stats.avg_position)} → 平均闲置 ${basePct(avgIdle)}`
      : "闲置比例 = 1 − 当日策略仓位");
  const dynNote = unified
    ? ((strat.notes || [])[0] || "组间夏普 + 组内逆波动 + LLM tilt/risk-off")
    : ((strat.notes || []).find((n) => String(n).includes("闲置") || String(n).includes("逆波动"))
      || "闲置：逆波动 + 状态偏移 + 高置信判跌调配");

  box.innerHTML = `
    <div class="cash-config-card">
      <h4>${unified ? "统一夏普多资产" : "价投策略（沪深300 仓位）"}</h4>
      <ul>${strategyItems.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>
      ${strat.headline ? `<div class="cash-config-formula">${escapeHtml(strat.headline)}</div>` : ""}
    </div>
    <div class="cash-config-card">
      <h4>${unified ? "三组六袖" : "闲置资金三袖"}</h4>
      ${allocHtml}
      <div class="cash-config-formula">${escapeHtml(formula)}</div>
      <div class="cash-config-formula">${escapeHtml(dynNote)}</div>
    </div>
    <div class="cash-config-card">
      <h4>回测实测</h4>
      <div class="cash-stat-row">
        <div class="cash-stat-pill">${unified ? "沪深300均权" : "平均仓位"}<strong>${basePct(stats.avg_position)}</strong></div>
        <div class="cash-stat-pill">${unified ? "非沪深300均权" : "平均闲置"}<strong>${basePct(stats.avg_idle)}</strong></div>
        <div class="cash-stat-pill">${unified ? "进攻组贡献" : "策略净收益"}<strong>${stats.strategy_only_return != null ? fmtPct(stats.strategy_only_return) : "--"}</strong></div>
        <div class="cash-stat-pill">${unified ? "防守+现金流" : "闲置贡献"}<strong class="${signedClass(stats.cash_return)}">${stats.cash_return != null ? fmtPctSigned(stats.cash_return) : "--"}</strong></div>
      </div>
      <ul style="margin-top:8px">
        <li>模型 ${escapeHtml(stats.model_name || "--")}</li>
        <li>数据区间 ${escapeHtml(cash.start_date || "--")} → ${escapeHtml(cash.end_date || "--")}</li>
      </ul>
    </div>
  `;
}

function renderCashAssetsChart(data) {
  const block = $("cash-assets-block");
  const metricsBox = $("cash-assets-metrics");
  const configBox = $("cash-assets-config");
  const cash = data.cash_assets;
  if (!block) return;

  if (!cash?.enabled || !cash.series?.length || !cash.dates?.length) {
    block.classList.add("hidden");
    if (cashAssetsChart) cashAssetsChart.clear();
    if (metricsBox) metricsBox.innerHTML = "";
    if (configBox) configBox.innerHTML = "";
    return;
  }

  block.classList.remove("hidden");
  renderCashAssetsConfig(cash);
  if (!cashAssetsChart) cashAssetsChart = echarts.init($("cash-assets-chart"));

  if ($("cash-assets-hint") && cash.start_date) {
    const unified = cash.unified || cash.strategy?.allocation_mode === "unified_sharpe";
    const sleeveHint = unified
      ? "六袖满仓净值参考 · 虚线=等权六袖"
      : "三袖锚定：现金≈34% · 纳指≈33% · 防御(金+债)≈33%，再经逆波动/状态动态调";
    if ($("cash-assets-title")) {
      $("cash-assets-title").textContent = unified ? "多资产净值参考" : "闲置资金与策略配置";
    }
    $("cash-assets-hint").textContent =
      `数据自 ${cash.start_date}${cash.end_date ? ` → ${cash.end_date}` : ""} · ${sleeveHint}`;
  }

  if (metricsBox) {
    metricsBox.innerHTML = cash.series.map((s) => {
      const weightText = s.weight != null ? ` · 权重 ${(s.weight * 100).toFixed(0)}%` : "";
      const startText = s.start_date && s.start_date !== cash.start_date ? ` · 自 ${s.start_date}` : "";
      const retCls = signedClass(s.total_return);
      return `
        <div class="cash-asset-chip">
          ${escapeHtml(s.label)}${escapeHtml(weightText)}${escapeHtml(startText)}
          <strong class="${retCls}">${fmtPctSigned(s.total_return)}</strong>
        </div>
      `;
    }).join("");
  }

  const chartDates = cash.dates;
  const legend = cash.series.map((s) => {
    if (s.key === "portfolio" || s.key === "actual") return s.label;
    const w = s.weight != null ? ` ${(s.weight * 100).toFixed(0)}%` : "";
    return `${s.label}${w}`;
  });

  const series = cash.series.map((s) => {
    const isAccent = s.key === "portfolio" || s.key === "actual";
    const color = cashAssetColors[s.key] || "#9aa0a6";
    return {
      name: s.key === "portfolio" || s.key === "actual"
        ? s.label
        : `${s.label}${s.weight != null ? ` ${(s.weight * 100).toFixed(0)}%` : ""}`,
      type: "line",
      smooth: true,
      showSymbol: false,
      connectNulls: false,
      data: s.equity,
      // series.color 同时驱动折线、图例、tooltip 标记；仅设 lineStyle 时图例会走默认色盘
      color,
      itemStyle: { color },
      lineStyle: {
        color,
        width: isAccent ? 2.5 : 1.8,
        type: isAccent ? "dashed" : "solid",
      },
    };
  });

  cashAssetsChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (items) => {
        const rows = items
          .filter((it) => it.data != null && !Number.isNaN(it.data) && it.seriesName !== "零轴")
          .map((it) => `${it.marker}${it.seriesName}: ${((it.data - 1) * 100).toFixed(2)}%`);
        if (!rows.length) return items[0]?.axisValue || "";
        return `${items[0].axisValue}<br/>${rows.join("<br/>")}`;
      },
    },
    legend: { data: legend, textStyle: { color: "#9aa0a6" }, top: 0 },
    grid: { ...baseGrid(), top: 48, bottom: 56 },
    dataZoom: recentZoomConfig(chartDates),
    xAxis: { type: "category", data: chartDates, axisLabel: dateAxisLabel() },
    yAxis: {
      scale: true,
      axisLabel: {
        color: "#9aa0a6",
        formatter: (v) => `${((v - 1) * 100).toFixed(0)}%`,
      },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [
      ...series,
      {
        name: "零轴",
        type: "line",
        data: chartDates.map(() => 1),
        symbol: "none",
        lineStyle: { color: "rgba(154,160,166,0.35)", width: 1, type: "dashed" },
        tooltip: { show: false },
        z: 0,
      },
    ],
  }, { notMerge: true });
}

export function clearCompareChart() {
  compareChart?.clear();
}

function allocationMetricLabel(layer, activeStart) {
  if (layer.key === "strategy") return "全周期均";
  if (layer.key === "idle_uninvested") return `${activeStart || "配置"}前均`;
  if (layer.phase === "portfolio" || ["csi300", "gold", "bond", "nasdaq", "repo", "mm"].includes(layer.key)) {
    return "组合均权";
  }
  if (["gold", "bond", "nasdaq", "repo", "mm"].includes(layer.key)) {
    return `${activeStart || "配置"}后均`;
  }
  return "均占比";
}

function renderAllocationSplit(alloc) {
  const unified = alloc.allocation_mode === "unified_sharpe" || alloc.absolute_cash_weights;
  const strategy = alloc.layers.find((l) => l.key === "strategy" || l.key === "csi300");
  const uninvested = alloc.layers.find((l) => l.key === "idle_uninvested");
  const breakdown = unified
    ? { dates: alloc.dates, layers: alloc.layers.filter((l) => l.key !== "strategy") }
    : (alloc.idle_breakdown || {});
  const dates = alloc.dates;
  const activeStart = alloc.active_start;

  if (!allocationMainChart) allocationMainChart = echarts.init($("allocation-main-chart"));
  if (!allocationIdleChart) allocationIdleChart = echarts.init($("allocation-idle-chart"));

  if ($("allocation-main-title")) {
    $("allocation-main-title").textContent = unified ? "沪深300权重" : "策略 vs 未投资 / 已配置闲置";
  }
  if ($("allocation-idle-title")) {
    $("allocation-idle-title").textContent = unified ? "六袖绝对权重" : "闲置品种拆分（配置生效后，占总资产比例）";
  }

  const investedIdleValues = dates.map((_, i) => {
    if (unified) return null;
    if (!activeStart || dates[i] < activeStart) return null;
    return alloc.layers
      .filter((l) => ["gold", "bond", "nasdaq", "repo", "mm"].includes(l.key))
      .reduce((s, l) => s + (l.values[i] || 0), 0);
  });

  const stratColor = layerColor(unified ? "csi300" : "strategy");
  const uninvestedColor = layerColor("idle_uninvested");
  const investedColor = "#56d4c8";
  const mainLegend = [unified ? "沪深300" : "策略仓位"];
  if (!unified && uninvested) mainLegend.push("未投资闲置");
  if (!unified && activeStart) mainLegend.push("已配置闲置");

  const mainSeries = [
    {
      name: unified ? "沪深300" : "策略仓位",
      type: "line",
      step: "end",
      showSymbol: false,
      sampling: "lttb",
      data: strategy?.values || [],
      lineStyle: { width: 2.5, color: stratColor },
      areaStyle: { color: hexToRgba(stratColor, 0.22) },
      itemStyle: { color: stratColor },
      z: 3,
    },
  ];
  if (!unified && uninvested) {
    mainSeries.push({
      name: "未投资闲置",
      type: "line",
      step: "end",
      showSymbol: false,
      sampling: "lttb",
      data: uninvested.values.map((v, i) => (activeStart && dates[i] >= activeStart ? null : v)),
      lineStyle: { width: 2, color: uninvestedColor },
      areaStyle: { color: hexToRgba(uninvestedColor, 0.2) },
      itemStyle: { color: uninvestedColor },
      z: 2,
    });
  }
  if (!unified && activeStart) {
    mainSeries.push({
      name: "已配置闲置",
      type: "line",
      step: "end",
      showSymbol: false,
      sampling: "lttb",
      connectNulls: false,
      data: investedIdleValues,
      lineStyle: { width: 2, color: investedColor },
      areaStyle: { color: hexToRgba(investedColor, 0.15) },
      itemStyle: { color: investedColor },
      z: 2,
    });
  }

  allocationMainChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (items) => {
        const rows = items
          .filter((it) => it.data != null)
          .map((it) => `${it.marker}${it.seriesName}: ${(it.data * 100).toFixed(1)}%`);
        const note = !unified && activeStart && items[0]?.axisValue < activeStart
          ? "<br/><span style='color:#9aa0a6'>尚未按配置投资</span>"
          : "";
        return `${items[0]?.axisValue || ""}<br/>${rows.join("<br/>")}${note}`;
      },
    },
    legend: { data: mainLegend, textStyle: { color: "#9aa0a6" }, top: 0 },
    grid: { left: 48, right: 16, top: 36, bottom: 52 },
    dataZoom: recentZoomConfig(dates),
    xAxis: { type: "category", data: dates, axisLabel: dateAxisLabel() },
    yAxis: {
      max: 1,
      axisLabel: { color: "#9aa0a6", formatter: (v) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [
      ...mainSeries,
      ...(!unified && activeStart ? [{
        name: "配置起点",
        type: "line",
        data: [],
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "#f5a623", type: "dashed", width: 1 },
          label: { formatter: activeStart, color: "#f5a623", fontSize: 10 },
          data: [{ xAxis: activeStart }],
        },
      }] : []),
    ],
  }, { notMerge: true });

  const bdDates = breakdown.dates || [];
  const bdLayers = breakdown.layers || [];
  const idleSeries = bdLayers.map((layer) => {
    const c = layerColor(layer.key);
    const avgVal = layer.avg_active != null ? layer.avg_active : layer.avg;
    const seriesName = layer.weight_label ? `${layer.label} (${layer.weight_label})` : layer.label;
    return {
      name: seriesName,
      type: "line",
      step: "end",
      showSymbol: false,
      sampling: "lttb",
      stack: unified ? "fund" : undefined,
      data: layer.values,
      lineStyle: { width: unified ? 0 : 2, color: c },
      areaStyle: { color: hexToRgba(c, unified ? 0.55 : 0.2) },
      itemStyle: { color: c },
      markLine: (!unified && avgVal != null) ? {
        silent: true,
        symbol: "none",
        lineStyle: { type: "dotted", color: c, opacity: 0.5 },
        label: { formatter: `均 ${(avgVal * 100).toFixed(1)}%`, color: "#9aa0a6", fontSize: 10 },
        data: [{ yAxis: avgVal }],
      } : undefined,
    };
  });

  allocationIdleChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (items) => {
        const rows = items.map((it) => `${it.marker}${it.seriesName}: ${(it.data * 100).toFixed(1)}%`);
        const total = items.reduce((s, it) => s + Number(it.data || 0), 0);
        return `${items[0]?.axisValue || ""}<br/>${rows.join("<br/>")}<br/>合计: ${(total * 100).toFixed(1)}%`;
      },
    },
    legend: { data: idleSeries.map((s) => s.name), textStyle: { color: "#9aa0a6" }, top: 0, type: "scroll" },
    grid: { left: 48, right: 16, top: 40, bottom: 52 },
    dataZoom: recentZoomConfig(bdDates, 120),
    xAxis: { type: "category", data: bdDates, axisLabel: dateAxisLabel() },
    yAxis: {
      max: unified ? 1 : undefined,
      scale: !unified,
      axisLabel: { color: "#9aa0a6", formatter: (v) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: idleSeries,
  }, { notMerge: true });
}

function renderAllocationStack(alloc) {
  if (!allocationChart) allocationChart = echarts.init($("allocation-chart"));
  const legend = alloc.layers.map((l) => l.label);
  const series = alloc.layers.map((layer, idx) => {
    const c = layerColor(layer.key);
    return {
      name: layer.label,
      type: "line",
      stack: "fund",
      smooth: 0.2,
      showSymbol: false,
      sampling: "lttb",
      areaStyle: { color: hexToRgba(c, 0.55 - idx * 0.06) },
      lineStyle: { width: 0 },
      itemStyle: { color: c },
      emphasis: { focus: "series" },
      data: layer.values,
    };
  });

  allocationChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (items) => {
        const rows = items
          .filter((it) => it.data != null)
          .map((it) => `${it.marker}${it.seriesName}: ${(it.data * 100).toFixed(1)}%`);
        const total = items.filter((it) => it.data != null).reduce((s, it) => s + Number(it.data), 0);
        return `${items[0]?.axisValue || ""}<br/>${rows.join("<br/>")}<br/>合计: ${(total * 100).toFixed(1)}%`;
      },
    },
    legend: { data: legend, textStyle: { color: "#9aa0a6" }, top: 0, type: "scroll" },
    grid: { ...baseGrid(), top: 44, bottom: 48 },
    dataZoom: recentZoomConfig(alloc.dates),
    xAxis: { type: "category", data: alloc.dates, axisLabel: dateAxisLabel() },
    yAxis: {
      max: 1,
      axisLabel: { color: "#9aa0a6", formatter: (v) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series,
  }, { notMerge: true });
}

export function setAllocationViewMode(mode) {
  allocationViewMode = mode;
  document.querySelectorAll(".alloc-mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
  const splitView = $("allocation-split-view");
  const stackView = $("allocation-stack-view");
  if (splitView) splitView.classList.toggle("hidden", mode !== "split");
  if (stackView) stackView.classList.toggle("hidden", mode !== "stack");
  if (mode === "stack" && allocationCache) {
    renderAllocationStack(allocationCache);
    setTimeout(() => allocationChart?.resize(), 50);
  } else {
    setTimeout(() => {
      allocationMainChart?.resize();
      allocationIdleChart?.resize();
    }, 50);
  }
}

function renderAllocationChart(data) {
  const block = $("allocation-block");
  const metricsBox = $("allocation-metrics");
  const alloc = data.allocation_history;
  if (!alloc?.enabled || !alloc.layers?.length || !alloc.dates?.length) {
    if (block) block.classList.add("hidden");
    allocationCache = null;
    allocationChart?.clear();
    allocationMainChart?.clear();
    allocationIdleChart?.clear();
    if (metricsBox) metricsBox.innerHTML = "";
    return;
  }
  if (!block) {
    allocationCache = alloc;
    renderAllocationSplit(alloc);
    if (allocationViewMode === "stack") renderAllocationStack(alloc);
    return;
  }

  block.classList.remove("hidden");
  allocationCache = alloc;

  if ($("allocation-hint") || $("allocation-desc")) {
    const hintEl = $("allocation-hint") || $("allocation-desc");
    if (alloc.allocation_mode === "unified_sharpe" || alloc.absolute_cash_weights) {
      hintEl.textContent = "多资产配置：六袖绝对权重堆叠（沪深300 / 纳指 / 金 / 债 / 货币 / 逆回购）";
      allocationViewMode = "stack";
    } else {
      const startNote = alloc.active_start ? ` · ${alloc.active_start} 起闲置才按配置投资` : "";
      hintEl.textContent = alloc.cash_mgmt_enabled
        ? `三袖（现金/纳指/防御）动态拆分；${alloc.active_start || "数据就绪"} 前闲置未投资${startNote}`
        : "左图策略仓位与闲置现金，右图为闲置部分";
    }
  }

  const idleWrap = $("allocation-idle-wrap");
  const unified = alloc.allocation_mode === "unified_sharpe" || alloc.absolute_cash_weights;
  const hasIdleBreakdown = unified
    || alloc.layers.some((l) => !["strategy", "idle", "idle_uninvested"].includes(l.key));
  if (idleWrap) idleWrap.classList.toggle("hidden", !hasIdleBreakdown);

  if (metricsBox) {
    const activeStart = alloc.active_start;
    metricsBox.innerHTML = alloc.layers.map((layer) => {
      const metricLabel = allocationMetricLabel(layer, activeStart);
      const displayAvg = layer.avg_active != null && layer.key !== "strategy"
        ? layer.avg_active
        : layer.avg;
      const weightNote = layer.weight_label
        ? `<span class="chip-sub">${escapeHtml(layer.weight_label)}</span>`
        : "";
      const fullCycleNote = layer.avg_active != null && layer.key !== "strategy"
        && Math.abs(layer.avg_active - layer.avg) > 0.001
        ? `<span class="chip-sub">全周期 ${(layer.avg * 100).toFixed(1)}%</span>`
        : "";
      return `
        <div class="cash-asset-chip" style="border-left: 3px solid ${layerColor(layer.key)}">
          ${escapeHtml(layer.label)}
          <span class="chip-meta">${metricLabel}</span>
          <strong>${(displayAvg * 100).toFixed(1)}%</strong>
          ${weightNote}${fullCycleNote}
        </div>
      `;
    }).join("");
  }

  renderAllocationSplit(alloc);
  if (allocationViewMode === "stack") {
    renderAllocationStack(alloc);
  }
  setAllocationViewMode(allocationViewMode);
}

function renderDrawdownChart(data) {
  if (!drawdownChart) drawdownChart = echarts.init($("drawdown-chart"));
  drawdownChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (items) => {
        const rows = items.map((it) => `${it.marker}${it.seriesName}: ${(it.data * 100).toFixed(2)}%`);
        return `${items[0].axisValue}<br/>${rows.join("<br/>")}`;
      },
    },
    legend: { data: ["策略回撤", "大盘回撤"], textStyle: { color: "#9aa0a6" } },
    grid: { ...baseGrid(), bottom: 56 },
    dataZoom: recentZoomConfig(data.dates),
    xAxis: { type: "category", data: data.dates, axisLabel: dateAxisLabel() },
    yAxis: {
      axisLabel: { color: "#9aa0a6", formatter: (v) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [
      {
        name: "策略回撤",
        type: "line",
        showSymbol: false,
        data: data.strategy_drawdown,
        lineStyle: { color: "#f07178", width: 2 },
        areaStyle: { color: "rgba(240,113,120,0.15)" },
      },
      {
        name: "大盘回撤",
        type: "line",
        showSymbol: false,
        data: data.benchmark_drawdown,
        lineStyle: { color: "#c9a227", width: 1.5, type: "dashed" },
      },
    ],
  }, { notMerge: true });
}

function renderPositionChart(data) {
  if (!positionChart) positionChart = echarts.init($("position-chart"));
  positionChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => `${(v * 100).toFixed(1)}%`,
    },
    grid: { ...baseGrid(), bottom: 56 },
    dataZoom: recentZoomConfig(data.dates),
    xAxis: { type: "category", data: data.dates, axisLabel: dateAxisLabel() },
    yAxis: {
      max: 1,
      axisLabel: { color: "#9aa0a6", formatter: (v) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [{
      name: "沪深300权重",
      type: "line",
      step: "end",
      showSymbol: false,
      data: data.position,
      lineStyle: { color: "#4c8bf5", width: 2 },
      areaStyle: { color: "rgba(76,139,245,0.15)" },
    }],
  }, { notMerge: true });
}

function renderPredDistChart(data) {
  if (!predDistChart) predDistChart = echarts.init($("pred-dist-chart"));
  const colors = { 涨: "#3ecf8e", 跌: "#f07178", 平: "#c9a227" };
  const predData = Object.entries(data.prediction_dist || {}).map(([name, value]) => ({
    name, value, itemStyle: { color: colors[name] || "#4c8bf5" },
  }));
  const actualData = Object.entries(data.actual_dist || {}).map(([name, value]) => ({
    name, value, itemStyle: { color: colors[name] || "#9aa0a6" },
  }));

  predDistChart.setOption({
    backgroundColor: "transparent",
    title: [
      { text: "模型预测", left: "24%", top: 0, textStyle: { color: "#9aa0a6", fontSize: 12 } },
      { text: "实际标签", left: "68%", top: 0, textStyle: { color: "#9aa0a6", fontSize: 12 } },
    ],
    tooltip: { trigger: "item" },
    series: [
      {
        type: "pie",
        radius: ["38%", "58%"],
        center: ["30%", "55%"],
        data: predData,
        label: { color: "#d5d9e0", fontSize: 11 },
      },
      {
        type: "pie",
        radius: ["38%", "58%"],
        center: ["74%", "55%"],
        data: actualData,
        label: { color: "#d5d9e0", fontSize: 11 },
      },
    ],
  }, { notMerge: true });
}

function renderConfusionChart(data) {
  if (!confusionChart) confusionChart = echarts.init($("confusion-chart"));
  const labels = ["跌", "平", "涨"];
  const matrix = labels.map((actual) =>
    labels.map((pred) => (data.confusion?.[actual]?.[pred] || 0))
  );
  const flat = matrix.flat();
  const maxVal = Math.max(...flat, 1);

  confusionChart.setOption({
    backgroundColor: "transparent",
    tooltip: { position: "top" },
    grid: { left: 56, right: 16, top: 16, bottom: 40 },
    xAxis: {
      type: "category",
      data: labels.map((x) => `预测${x}`),
      axisLabel: { color: "#9aa0a6" },
      splitArea: { show: true },
    },
    yAxis: {
      type: "category",
      data: labels.map((x) => `实际${x}`),
      axisLabel: { color: "#9aa0a6" },
      splitArea: { show: true },
    },
    visualMap: {
      min: 0,
      max: maxVal,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: ["#1b2230", "#4c8bf5", "#3ecf8e"] },
      textStyle: { color: "#9aa0a6" },
      show: false,
    },
    series: [{
      type: "heatmap",
      data: matrix.flatMap((row, i) => row.map((val, j) => [j, i, val])),
      label: { show: true, color: "#fff", fontSize: 12 },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,0.4)" } },
    }],
  }, { notMerge: true });
}
function formatRunLabel(run) {
  if (!run) return "";
  const saved = run.saved_at ? String(run.saved_at).replace("T", " ").slice(0, 16) : "";
  const annual = run.annualized_return != null ? `${(run.annualized_return * 100).toFixed(2)}%` : "--";
  const cashTag = run.cash_mgmt_enabled ? " · 含现金" : "";
  return `${saved} · 年化 ${annual}${cashTag} · ${run.run_id?.slice(0, 8) || ""}`;
}

function formatCompareMetricsLine(prefix, m) {
  const cash = m.cash_mgmt_enabled && m.cash_return != null
    ? ` · 现金 ${fmtPctSigned(m.cash_return)}`
    : "";
  const strat = m.cash_mgmt_enabled && m.strategy_only_return != null
    ? ` · 策略净 ${fmtPct(m.strategy_only_return)}`
    : "";
  const excess = m.excess_return != null ? ` · 超额 ${fmtPctSigned(m.excess_return)}` : "";
  return `${prefix}: 年化 ${fmtPct(m.annualized_return)} · 回撤 ${fmtPct(m.max_drawdown)} · 夏普 ${fmtNum(m.sharpe_ratio, 2)}${strat}${cash}${excess}`;
}

const COMPARE_COLORS = ["#4c8bf5", "#f5a623", "#3ecf8e", "#c084fc", "#f07178", "#22d3ee"];

/** 用渠道汇总填充「模型对比」多选（不再用历史 run 档案） */
export function fillCompareProviders(ranked = []) {
  const box = $("compare-provider-picks");
  if (!box) return;
  const list = (ranked || []).filter((r) => r.provider && !String(r.provider).startsWith("tuzi_"));
  if (!list.length) {
    box.innerHTML = `<span class="hint">暂无已入库渠道</span>`;
    return;
  }
  const prev = new Set(
    [...box.querySelectorAll('input[type="checkbox"]:checked')].map((el) => el.value),
  );
  // 默认勾选前两名（若无历史勾选）
  const defaultIds = list.slice(0, Math.min(2, list.length)).map((r) => r.provider);
  box.innerHTML = list.map((r) => {
    const id = r.provider;
    const label = channelLabels[id] || r.model || id;
    const checked = prev.size ? prev.has(id) : defaultIds.includes(id);
    return `<label class="compare-chip">
      <input type="checkbox" value="${escapeHtml(id)}" ${checked ? "checked" : ""} />
      <span>${escapeHtml(label)}</span>
    </label>`;
  }).join("");
}

export function selectedCompareProviders() {
  const box = $("compare-provider-picks");
  if (!box) return [];
  return [...box.querySelectorAll('input[type="checkbox"]:checked')].map((el) => el.value);
}

/** @deprecated 保留空实现，兼容旧 boot 调用 */
export async function loadBacktestRuns() {
  return [];
}

export function renderCompareMetrics(channels) {
  const box = $("compare-metrics");
  if (!box) return;
  if (!channels?.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = channels.map((ch) => {
    const label = channelLabels[ch.provider] || ch.model_name || ch.provider;
    return `<div class="compare-metric-row"><span>${formatCompareMetricsLine(label, ch.metrics || {})}</span></div>`;
  }).join("");
}

function renderCompareChart(channels) {
  if (!compareChart) compareChart = echarts.init($("compare-chart"));
  if (!channels?.length) {
    compareChart.clear();
    return;
  }

  const allDates = [...new Set(channels.flatMap((ch) => ch.dates || []))].sort();
  const legend = channels.map((ch) => channelLabels[ch.provider] || ch.provider);
  const series = channels.map((ch, i) => {
    const map = Object.fromEntries((ch.dates || []).map((d, j) => [d, ch.equity[j]]));
    return {
      name: legend[i],
      type: "line",
      smooth: true,
      showSymbol: false,
      connectNulls: true,
      data: allDates.map((d) => map[d] ?? null),
      lineStyle: { color: COMPARE_COLORS[i % COMPARE_COLORS.length], width: 2 },
    };
  });

  compareChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (items) => {
        const rows = items
          .filter((it) => it.data != null)
          .map((it) => `${it.marker}${it.seriesName}: ${((it.data - 1) * 100).toFixed(2)}%`);
        return `${items[0]?.axisValue || ""}<br/>${rows.join("<br/>")}`;
      },
    },
    legend: { data: legend, textStyle: { color: "#9aa0a6" } },
    grid: { ...baseGrid(), bottom: 56 },
    dataZoom: recentZoomConfig(allDates),
    xAxis: { type: "category", data: allDates, axisLabel: dateAxisLabel() },
    yAxis: {
      scale: true,
      axisLabel: {
        color: "#9aa0a6",
        formatter: (v) => `${((v - 1) * 100).toFixed(0)}%`,
      },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series,
  }, { notMerge: true });
}

export async function runBacktestCompare() {
  const providers = selectedCompareProviders();
  if (providers.length < 2) throw new Error("请至少勾选两个模型渠道");
  const results = await Promise.all(
    providers.map(async (p) => {
      const data = await api(`/api/backtest-charts?provider=${encodeURIComponent(p)}`);
      if (!data?.available) throw new Error(`${channelLabels[p] || p} 暂无回测`);
      return {
        provider: data.llm_provider || p,
        model_name: data.model_name,
        metrics: data.metrics || {},
        dates: data.dates || [],
        equity: data.strategy_equity || [],
      };
    }),
  );
  renderCompareMetrics(results);
  renderCompareChart(results);
  return results;
}

export function renderBacktestDashboard(data) {
  const warn = $("backtest-freq-warning");
  if (data?.strategy_summary) {
    renderStrategySummary(data.strategy_summary);
  }
  if (warn) {
    if (data?.freq_warning) {
      warn.textContent = data.freq_warning;
      warn.classList.remove("hidden");
    } else {
      warn.textContent = "";
      warn.classList.add("hidden");
    }
  }
  if (!data?.available) {
    $("backtest-period").textContent = "暂无回测，请运行: market-predictor pipeline -- --step backtest --model llm";
    return;
  }
  const grid = document.querySelector(".backtest-charts-grid");
  // stale 覆盖层会遮住图表，实际数据可用时去掉覆盖
  if (grid) grid.classList.remove("stale-overlay");
  renderBacktestMetrics(data);
  renderEquityChart(data);
  renderDrawdownChart(data);
  renderCashAssetsChart(data);
  renderAllocationChart(data);
  renderPositionChart(data);
  renderPredDistChart(data);
  renderConfusionChart(data);
}
export async function loadBacktestCharts(provider) {
  const sel = $("backtest-provider");
  const channel = provider ?? sel?.value ?? "";
  const q = channel ? `?provider=${encodeURIComponent(channel)}` : "";
  const data = await api(`/api/backtest-charts${q}`);
  renderBacktestDashboard(data);
  if ($("backtest-period") && data?.model_name) {
    const prov = data.llm_provider || channel || "";
    const label = channelLabels[prov] || prov;
    $("backtest-period").textContent = [
      data.period?.start && data.period?.end ? `${data.period.start} → ${data.period.end}` : null,
      label ? `渠道 ${label}` : null,
      data.model_name,
    ].filter(Boolean).join(" · ");
  }
  highlightChannelSummaryRow(channel || data?.llm_provider || "");
  return data;
}

function highlightChannelSummaryRow(provider) {
  const body = $("channel-summary-body");
  if (!body) return;
  const rows = body.querySelectorAll("tr[data-provider]");
  rows.forEach((tr) => {
    tr.classList.toggle("active", Boolean(provider) && tr.dataset.provider === provider);
  });
}

function renderChannelSummaries(data) {
  const body = $("channel-summary-body");
  const hint = $("channel-summary-hint");
  if (!body) return;
  // 国内渠道优先；排除兔子海外
  const allRanked = data?.ranked || [];
  const ranked = allRanked.filter((r) => r.provider && !String(r.provider).startsWith("tuzi_"));
  const n = data?.n_available ?? ranked.length;
  if (hint) {
    hint.textContent = ranked.length
      ? `${ranked.length} 路已入库 · 点击行切换下方曲线`
      : "暂无渠道回测";
  }
  if (!ranked.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty-hint">暂无多渠道回测数据</td></tr>`;
    return;
  }
  const fmtNum = (v, digits = 2) => (v == null || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(digits));
  const fmtWhen = (s) => {
    if (!s) return "--";
    const t = String(s).replace("T", " ");
    return t.length >= 16 ? t.slice(0, 16) : t;
  };
  body.innerHTML = ranked.map((r) => {
    const ret = r.total_return;
    const retCls = ret == null ? "" : (ret >= 0 ? "positive" : "negative");
    const dd = r.max_drawdown;
    const label = channelLabels[r.provider] || r.model || r.provider;
    return `<tr data-provider="${escapeHtml(r.provider)}">
      <td class="num">${r.rank ?? ""}</td>
      <td>${escapeHtml(label)}</td>
      <td class="num ${retCls}">${fmtPct(ret)}</td>
      <td class="num">${fmtPct(r.annualized_return)}</td>
      <td class="num">${fmtNum(r.sharpe_ratio, 3)}</td>
      <td class="num">${fmtPct(dd)}</td>
      <td class="num">${r.win_rate != null ? fmtPct(r.win_rate) : "--"}</td>
      <td class="num">${r.total_trades ?? "--"}</td>
      <td class="num">${fmtWhen(r.saved_at)}</td>
    </tr>`;
  }).join("");
}

export async function loadChannelSummaries() {
  const data = await api("/api/backtest-channel-summaries");
  renderChannelSummaries(data);
  const ranked = (data?.ranked || []).filter(
    (r) => r.provider && !String(r.provider).startsWith("tuzi_"),
  );
  fillCompareProviders(ranked);
  const sel = $("backtest-provider");
  // 默认选中已入库排名第一的渠道（不再走「主渠道 latest」空值）
  if (sel && ranked.length) {
    const ids = ranked.map((r) => r.provider);
    if (!sel.value || !ids.includes(sel.value)) {
      const existing = new Set([...sel.options].map((o) => o.value).filter(Boolean));
      for (const r of ranked) {
        if (!existing.has(r.provider)) {
          const label = channelLabels[r.provider] || r.model || r.provider;
          sel.insertAdjacentHTML("beforeend", `<option value="${r.provider}">${label}</option>`);
        }
      }
      sel.value = ranked[0].provider;
    }
  }
  highlightChannelSummaryRow(sel?.value || "");
  return data;
}


function renderAccuracyPartitions(data) {
  accuracyPartitionCache = data;
  const regimeEl = $("acc-part-regime");
  const dimSel = $("acc-part-dim");
  const routingEl = $("acc-part-routing");
  const sweetEl = $("acc-part-sweet");
  if (!data?.available) {
    if (regimeEl) regimeEl.textContent = "暂无市场状态切片数据";
    return;
  }

  const noteEl = $("acc-part-note");
  if (noteEl && data.note) noteEl.textContent = data.note;

  const regime = data.current_regime || {};
  if (regimeEl) {
    const bits = [
      regime.date ? `截至 ${regime.date}` : null,
      regime.vol_q,
      regime.trend,
      regime.above_sma60,
      regime.rsi_bucket,
    ].filter(Boolean);
    regimeEl.textContent = bits.join(" · ") || "当前状态未知";
  }

  const dims = data.dimensions || [];
  if (dimSel) {
    const prev = dimSel.value;
    dimSel.innerHTML = dims.map((d) => `<option value="${d.key}">${d.label}</option>`).join("");
    if (prev && dims.some((d) => d.key === prev)) dimSel.value = prev;
    else if (dims.some((d) => d.key === "pred")) dimSel.value = "pred";
  }

  const routing = data.routing || {};
  const weights = routing.weights || {};
  const per = routing.per_provider || {};
  const ranked = Object.entries(weights)
    .filter(([id]) => id && !String(id).startsWith("tuzi_"))
    .sort((a, b) => Number(b[1]) - Number(a[1]));
  if (routingEl) {
    routingEl.innerHTML = ranked.length
      ? ranked.map(([id, w], i) => {
          const info = per[id] || {};
          const label = channelLabels[id] || info.model || id;
          const acc = info.regime_accuracy;
          return `<div class="acc-routing-chip ${i === 0 ? "top" : ""}">
            ${escapeHtml(label)}
            <strong>${(w * 100).toFixed(0)}%</strong>
            ${acc != null ? `<span class="hint">切片acc ${(acc * 100).toFixed(1)}%</span>` : ""}
          </div>`;
        }).join("")
      : "";
  }

  const sweets = (data.sweet_spots || [])
    .filter((s) => s.provider && !String(s.provider).startsWith("tuzi_"))
    .slice(0, 12);
  if (sweetEl) {
    sweetEl.innerHTML = sweets.map((s) => {
      const label = channelLabels[s.provider] || s.model || s.provider;
      return `<span class="acc-sweet-pill"><em>${escapeHtml(label)}</em> · ${escapeHtml(s.dimension)}=${escapeHtml(s.slice)} · ${(s.accuracy * 100).toFixed(1)}% (+${s.lead_pp}pp)</span>`;
    }).join("");
  }

  renderAccuracyHeat(dimSel?.value || dims[0]?.key);
}

export function renderAccuracyHeat(dimKey) {
  const data = accuracyPartitionCache;
  const head = $("acc-heat-head");
  const body = $("acc-heat-body");
  if (!data || !head || !body) return;
  const dim = (data.dimensions || []).find((d) => d.key === dimKey) || (data.dimensions || [])[0];
  if (!dim?.heatmap) {
    body.innerHTML = `<tr><td class="empty-hint">该维度无数据</td></tr>`;
    return;
  }
  const providers = (dim.heatmap.providers || []).filter(
    (p) => p.id && !String(p.id).startsWith("tuzi_"),
  );
  const matrix = dim.heatmap.matrix || [];
  const regime = data.current_regime || {};
  const currentVal = regime[dim.key] || null;

  head.innerHTML = `<tr><th>${escapeHtml(dim.label)}</th>${
    providers.map((p) => `<th>${escapeHtml(channelLabels[p.id] || p.model || p.id)}</th>`).join("")
  }</tr>`;

  body.innerHTML = matrix.map((row) => {
    const isCurrent = currentVal && String(row.slice) === String(currentVal);
    const cells = providers.map((p) => {
      const cell = row.by_provider?.[p.id];
      if (!cell) return `<td class="heat">--</td>`;
      const best = row.best_provider === p.id ? " best" : "";
      return `<td class="heat${best}" title="n=${cell.n}">${(cell.accuracy * 100).toFixed(1)}%</td>`;
    }).join("");
    return `<tr class="${isCurrent ? "current-slice" : ""}">
      <td class="${isCurrent ? "heat current-slice" : ""}">${escapeHtml(row.slice)}${isCurrent ? " · 当前" : ""}</td>
      ${cells}
    </tr>`;
  }).join("");
}

export async function loadAccuracyPartitions() {
  const data = await api("/api/accuracy-partitions");
  renderAccuracyPartitions(data);
  return data;
}
