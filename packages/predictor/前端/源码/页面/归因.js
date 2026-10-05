import * as echarts from "echarts";
import { api } from "@/接口/客户端.js";
import { $, fmtPct, fmtNum2 } from "@/公共/工具.js";
import { baseGrid } from "@/公共/主题.js";
import { channelLabels } from "@/公共/状态.js";

let rollingSharpeChart;
let yearlyChart;
let llmYearlyAccChart;
let llmRollingAccChart;

export function currentAttributionProvider() {
  return $("attribution-provider")?.value || "";
}

export function resizeAttributionCharts() {
  [rollingSharpeChart, yearlyChart, llmYearlyAccChart, llmRollingAccChart]
    .forEach((c) => c?.resize());
}

function initAttributionChart(domId, holder) {
  const el = $(domId);
  if (!el) return holder;
  const existing = echarts.getInstanceByDom(el);
  if (existing) return existing;
  // 隐藏页宽高可能为 0，仍先 init，切页后 resize 即可；否则卡片日期更新了图表却跳过重绘
  return echarts.init(el);
}

function renderAttributionCards(data) {
  const box = $("attribution-cards");
  if (!box) return;
  if (!data?.available) {
    box.innerHTML = `<p class="empty-hint">暂无归因数据，请先运行回测</p>`;
    return;
  }
  const rs = data.rolling_sharpe || {};
  const mp = data.market_participation || {};
  const llm = data.llm_accuracy || {};
  const primary = llm.primary_sample || {};
  const full = llm.full_sample || {};
  const since = data.evaluation_primary_since || llm.primary_since || "2020-01-01";
  const sinceYear = String(since).slice(0, 4);
  const unified = (data.allocation_mode || "") === "unified_sharpe";
  const cards = [
    ["渠道", channelLabels[data.llm_provider] || data.llm_provider || data.model_name || "--"],
    ["数据截止", data.data_as_of || data.period?.end || "--"],
    ["全期总收益", fmtPct(data.total_return)],
    ["超额收益", fmtPct(data.excess_return)],
    [unified ? "防守+现金流贡献" : "闲置资金贡献", fmtPct(data.cash_return)],
    [unified ? "进攻组贡献" : "纯策略净收益", fmtPct(data.strategy_only_return)],
    ["全期夏普", fmtNum2(data.sharpe, 4)],
    ["滚动夏普均值", fmtNum2(rs.mean, 4)],
    ["滚动夏普>0占比", fmtPct(rs.pct_positive)],
    ["最近1年夏普", fmtNum2(rs.latest, 4)],
    [`准确率(${sinceYear}+)`, fmtPct(primary.accuracy)],
    ["准确率(全样本)", fmtPct(full.accuracy)],
    ["方向准确率(主区间)", fmtPct(primary.directional_accuracy)],
    ["预测涨占比(主区间)", fmtPct(primary.pred_up_pct)],
    ["上涨参与率", fmtNum2(mp.up_capture, 2) + "x"],
    ["下跌保护率", fmtNum2(mp.down_capture, 2) + "x"],
    ["月度胜率", fmtPct(mp.monthly_win_rate)],
    ["最佳年度", data.best_year ?? "--"],
    ["最差年度", data.worst_year ?? "--"],
  ];
  if (unified && data.group_attribution?.avg_offense_weight != null) {
    cards.splice(6, 0, ["进攻组均权", fmtPct(data.group_attribution.avg_offense_weight)]);
  }
  box.innerHTML = cards.map(([label, value]) =>
    `<div class="metric-chip"><span>${label}</span><strong>${value}</strong></div>`
  ).join("");
}

function renderRollingSharpeChart(data) {
  if (!data?.available) return;
  const series = data.rolling_sharpe?.series || [];
  if (!series.length) return;
  rollingSharpeChart = initAttributionChart("rolling-sharpe-chart", rollingSharpeChart);
  if (!rollingSharpeChart) return;
  const dates = series.map(s => s.date);
  const values = series.map(s => s.sharpe);
  rollingSharpeChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", valueFormatter: v => v.toFixed(4) },
    grid: baseGrid(),
    xAxis: { type: "category", data: dates, axisLabel: { show: false } },
    yAxis: {
      scale: true,
      axisLabel: { color: "#9aa0a6", formatter: v => v.toFixed(1) },
      splitLine: { lineStyle: { color: "#2a2f3a" } },
    },
    series: [{
      type: "line",
      smooth: true,
      showSymbol: false,
      data: values,
      lineStyle: { color: "#4c8bf5", width: 2 },
      areaStyle: { color: "rgba(76,139,245,0.12)" },
      markLine: {
        silent: true,
        data: [{ yAxis: 0, label: { formatter: "0", color: "#f07178" } }],
        lineStyle: { color: "#f07178", type: "dashed" },
      },
    }],
  }, { notMerge: true });
}

function renderYearlyChart(data) {
  if (!data?.available) return;
  const yearly = data.yearly || [];
  if (!yearly.length) return;
  yearlyChart = initAttributionChart("yearly-chart", yearlyChart);
  if (!yearlyChart) return;
  const years = yearly.map(y => String(y.year));
  const strat = yearly.map(y => +(y.strategy_ret * 100).toFixed(2));
  const bench = yearly.map(y => +(y.benchmark_ret * 100).toFixed(2));
  yearlyChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: items => items.map(it =>
        `${it.marker}${it.seriesName}: ${it.data.toFixed(1)}%`
      ).join("<br/>"),
    },
    legend: { data: ["策略", "大盘"], textStyle: { color: "#9aa0a6" } },
    grid: baseGrid(),
    xAxis: { type: "category", data: years, axisLabel: { color: "#9aa0a6", interval: 2 } },
    yAxis: {
      axisLabel: { color: "#9aa0a6", formatter: v => v + "%" },
      splitLine: { lineStyle: { color: "#2a2f3a" } },
    },
    series: [
      {
        name: "策略",
        type: "bar",
        data: strat,
        itemStyle: { color: "#3ecf8e" },
      },
      {
        name: "大盘",
        type: "bar",
        data: bench,
        itemStyle: { color: "#9aa0a6", opacity: 0.5 },
      },
    ],
  }, { notMerge: true });
}

function renderLlmYearlyAccChart(data) {
  const llm = data?.llm_accuracy;
  if (!llm?.available) return;
  const yearly = llm.yearly || [];
  if (!yearly.length) return;
  llmYearlyAccChart = initAttributionChart("llm-yearly-acc-chart", llmYearlyAccChart);
  if (!llmYearlyAccChart) return;
  const years = yearly.map(y => String(y.year));
  const acc = yearly.map(y => +((y.accuracy || 0) * 100).toFixed(1));
  const upPct = yearly.map(y => +((y.pred_up_pct || 0) * 100).toFixed(1));
  llmYearlyAccChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: ["准确率", "预测涨占比"], textStyle: { color: "#9aa0a6" } },
    grid: baseGrid(),
    xAxis: { type: "category", data: years, axisLabel: { color: "#9aa0a6", interval: 2 } },
    yAxis: {
      axisLabel: { color: "#9aa0a6", formatter: v => v + "%" },
      splitLine: { lineStyle: { color: "#2a2f3a" } },
      max: 100,
    },
    series: [
      { name: "准确率", type: "bar", data: acc, itemStyle: { color: "#4c8bf5" } },
      { name: "预测涨占比", type: "line", smooth: true, data: upPct, lineStyle: { color: "#f5a623" } },
    ],
  }, { notMerge: true });
}

function renderLlmRollingAccChart(data) {
  const llm = data?.llm_accuracy;
  if (!llm?.available) return;
  const series = llm.rolling_accuracy?.series || [];
  if (!series.length) return;
  llmRollingAccChart = initAttributionChart("llm-rolling-acc-chart", llmRollingAccChart);
  if (!llmRollingAccChart) return;
  const dates = series.map(s => s.date);
  const values = series.map(s => (s.accuracy || 0) * 100);
  const since = data.evaluation_primary_since || llm.primary_since;
  llmRollingAccChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", valueFormatter: v => v.toFixed(1) + "%" },
    grid: baseGrid(),
    xAxis: { type: "category", data: dates, axisLabel: { show: false } },
    yAxis: {
      scale: true,
      axisLabel: { color: "#9aa0a6", formatter: v => v.toFixed(0) + "%" },
      splitLine: { lineStyle: { color: "#2a2f3a" } },
    },
    series: [{
      type: "line",
      smooth: true,
      showSymbol: false,
      data: values,
      lineStyle: { color: "#3ecf8e", width: 2 },
      areaStyle: { color: "rgba(62,207,142,0.12)" },
      markLine: {
        silent: true,
        data: [
          { yAxis: 33.3, label: { formatter: "随机", color: "#9aa0a6" } },
        ],
        lineStyle: { color: "#9aa0a6", type: "dashed" },
      },
      markArea: since ? {
        silent: true,
        itemStyle: { color: "rgba(76,139,245,0.08)" },
        data: [[{ xAxis: dates.find(d => d >= since) || dates[0] }, { xAxis: dates[dates.length - 1] }]],
      } : undefined,
    }],
  }, { notMerge: true });
}

export async function loadAttribution() {
  try {
    const provider = currentAttributionProvider();
    const q = provider ? `?provider=${encodeURIComponent(provider)}` : "";
    const data = await api(`/api/attribution${q}`);
    renderAttributionCards(data);
    renderRollingSharpeChart(data);
    renderYearlyChart(data);
    renderLlmYearlyAccChart(data);
    renderLlmRollingAccChart(data);
    requestAnimationFrame(() => resizeAttributionCharts());
  } catch (e) {
    console.warn("归因加载失败:", e.message);
  }
}
