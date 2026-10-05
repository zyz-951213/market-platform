import * as echarts from "echarts";
import { api } from "@/接口/客户端.js";
import {
  $, fmtPct, fmtPctSigned, fmtNum, fmtMoney, fmtChartDate, directionClass, escapeHtml, signedClass,
} from "@/公共/工具.js";
import { baseGrid, layerColor } from "@/公共/主题.js";
import { overviewCache, channelLabels } from "@/公共/状态.js";

const CASH_ASSET_LABELS = {
  csi300: "沪深300", gold: "黄金", bond: "国债", nasdaq: "纳指", repo: "逆回购", mm: "货币",
};

let paperEquityChart;
let paperDrawdownChart;
let paperPositionChart;
let paperCashChart;

export function resizePaperCharts() {
  [paperEquityChart, paperDrawdownChart, paperPositionChart, paperCashChart]
    .forEach((c) => c?.resize());
}

export function currentPaperProvider() {
  return $("paper-provider")?.value || overviewCache?.active_api || "";
}

function renderPaperStats(data) {
  const st = data?.status;
  if (!st) return;
  const ret = st.total_return;
  if ($("paper-equity")) $("paper-equity").textContent = fmtMoney(st.equity);
  if ($("paper-return")) {
    $("paper-return").textContent = fmtPct(ret);
    $("paper-return").className = `stat-value ${signedClass(ret)}`;
  }
  if ($("paper-position")) {
    const posShow = st.allocation_mode === "unified_sharpe"
      ? (st.offense_weight ?? st.position)
      : st.position;
    $("paper-position").textContent = fmtPct(posShow);
  }
  // 顶部卡片：统一账本展示进攻组权重；沪深300仍在 metrics 芯片里
  const posCard = document.querySelector("#page-paper .stat-card:nth-child(3) .stat-label");
  if (posCard) posCard.textContent = st.allocation_mode === "unified_sharpe" ? "进攻组权重" : "当前仓位";

  const sig = st.latest_signal;
  if ($("paper-signal")) {
    $("paper-signal").textContent = sig
      ? `${sig.direction} · ${(sig.confidence * 100).toFixed(0)}%`
      : "暂无";
    $("paper-signal").className = `stat-value small ${directionClass(sig?.direction)}`;
  }
  if ($("paper-initial")) $("paper-initial").textContent = fmtMoney(st.initial_capital);
  if ($("paper-strategy-value")) {
    const stratVal = st.allocation_mode === "unified_sharpe"
      ? st.equity
      : (st.strategy_value != null ? st.strategy_value : (st.equity || 0) * (st.position || 0));
    $("paper-strategy-value").textContent = fmtMoney(stratVal);
  }
  if ($("paper-idle-total")) {
    if (st.allocation_mode === "unified_sharpe") {
      $("paper-idle-total").textContent = fmtPct(st.position);
    } else {
      const idleTot = st.idle_total != null ? st.idle_total : st.cash;
      $("paper-idle-total").textContent = fmtMoney(idleTot);
    }
  }
  renderPaperIdleAlloc(st);
  if ($("paper-trades")) $("paper-trades").textContent = String(st.total_trades ?? 0);
  if ($("paper-last-date")) $("paper-last-date").textContent = st.last_processed_date || "--";
  if ($("paper-hint") && st.last_processed_date) {
    $("paper-hint").textContent =
      `最近处理 ${st.last_processed_date} · 数据至 ${st.last_bar_date || "--"} · 仅供参考`;
  }
}

function renderPaperPerformance(perf) {
  const panel = $("paper-performance-metrics");
  if (!panel) return;
  if (!perf?.available) {
    panel.querySelectorAll("strong").forEach((el) => { el.textContent = "--"; });
    if ($("paper-period")) $("paper-period").textContent = "暂无足够历史";
    return;
  }
  const unit = perf.bar_freq === "weekly" ? "周" : "日";
  if ($("paper-period")) {
    $("paper-period").textContent = perf.period
      ? `${perf.period.start} → ${perf.period.end} · ${perf.period.bars}${unit}`
      : "--";
  }
  if ($("paper-perf-total")) {
    $("paper-perf-total").textContent = fmtPct(perf.total_return);
    $("paper-perf-total").className = signedClass(perf.total_return);
  }
  if ($("paper-perf-strat")) {
    $("paper-perf-strat").textContent = fmtPct(perf.strategy_only_return);
    $("paper-perf-strat").className = signedClass(perf.strategy_only_return);
  }
  if ($("paper-perf-cash")) {
    const showCash = perf.cash_mgmt_enabled && perf.cash_return != null;
    $("paper-perf-cash").textContent = showCash ? fmtPctSigned(perf.cash_return) : "--";
    $("paper-perf-cash").className = signedClass(perf.cash_return);
  }
  if ($("paper-perf-bench")) $("paper-perf-bench").textContent = fmtPct(perf.benchmark_total_return);
  if ($("paper-perf-excess")) {
    $("paper-perf-excess").textContent = perf.excess_return != null ? fmtPctSigned(perf.excess_return) : "--";
    $("paper-perf-excess").className = signedClass(perf.excess_return);
  }
  if ($("paper-perf-annual")) $("paper-perf-annual").textContent = fmtPct(perf.annualized_return);
  if ($("paper-perf-dd")) $("paper-perf-dd").textContent = fmtPct(perf.max_drawdown);
  if ($("paper-perf-bench-dd")) {
    $("paper-perf-bench-dd").textContent = fmtPct(perf.benchmark_max_drawdown);
  }
  if ($("paper-perf-sharpe")) $("paper-perf-sharpe").textContent = fmtNum(perf.sharpe_ratio, 2);
  if ($("paper-perf-win")) $("paper-perf-win").textContent = fmtPct(perf.win_rate);
  if ($("paper-perf-avg-pos")) $("paper-perf-avg-pos").textContent = fmtPct(perf.avg_position);
  if ($("paper-perf-in-market")) $("paper-perf-in-market").textContent = fmtPct(perf.periods_in_market);
  if ($("paper-perf-cost")) {
    $("paper-perf-cost").textContent = perf.total_transaction_cost != null
      ? fmtPctSigned(-Math.abs(perf.total_transaction_cost))
      : "--";
  }
}

function renderPaperCharts(charts, initialCapital) {
  if (!charts?.available) {
    paperEquityChart?.clear();
    paperPositionChart?.clear();
    paperDrawdownChart?.clear();
    return;
  }
  const dates = charts.dates || [];
  const equity = charts.equity || [];
  const positions = (charts.positions || []).map((p) => (p * 100).toFixed(1));
  const benchmark = charts.benchmark_equity || null;
  const hasBench = benchmark && benchmark.some((v) => v != null);

  if ($("paper-equity-chart")) {
    if (!paperEquityChart) paperEquityChart = echarts.init($("paper-equity-chart"));
    const series = [{
      name: "组合净值",
      type: "line",
      smooth: true,
      showSymbol: false,
      data: equity,
      lineStyle: { color: "#3ecf8e", width: 2 },
      areaStyle: { color: "rgba(62,207,142,0.12)" },
      markLine: {
        silent: true,
        data: [{ yAxis: initialCapital, label: { formatter: "初始", color: "#9aa0a6" } }],
        lineStyle: { color: "#9aa0a6", type: "dashed" },
      },
    }];
    if (hasBench) {
      series.push({
        name: "买入持有",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: benchmark,
        lineStyle: { color: "#9aa0a6", width: 1.5, type: "dashed" },
      });
    }
    paperEquityChart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { data: series.map((s) => s.name), textStyle: { color: "#9aa0a6" } },
      grid: baseGrid(),
      xAxis: { type: "category", data: dates, axisLabel: { color: "#9aa0a6", formatter: fmtChartDate, hideOverlap: true } },
      yAxis: {
        scale: true,
        axisLabel: { color: "#9aa0a6", formatter: (v) => (v / 10000).toFixed(0) + "万" },
        splitLine: { lineStyle: { color: "#2a2f3a" } },
      },
      series,
    }, { notMerge: true });
  }

  if ($("paper-drawdown-chart")) {
    if (!paperDrawdownChart) paperDrawdownChart = echarts.init($("paper-drawdown-chart"));
    const ddSeries = [{
      name: "组合回撤",
      type: "line",
      smooth: true,
      showSymbol: false,
      data: charts.drawdown || [],
      lineStyle: { color: "#f07178", width: 2 },
      areaStyle: { color: "rgba(240,113,120,0.12)" },
    }];
    const benchDd = charts.benchmark_drawdown;
    if (benchDd?.some((v) => v != null)) {
      ddSeries.push({
        name: "大盘回撤",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: benchDd,
        lineStyle: { color: "#9aa0a6", width: 1.5, type: "dashed" },
      });
    }
    paperDrawdownChart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", valueFormatter: (v) => (v != null ? `${Number(v).toFixed(2)}%` : "--") },
      legend: ddSeries.length > 1 ? { data: ddSeries.map((s) => s.name), textStyle: { color: "#9aa0a6" } } : undefined,
      grid: baseGrid(),
      xAxis: { type: "category", data: dates, axisLabel: { color: "#9aa0a6", formatter: fmtChartDate, hideOverlap: true } },
      yAxis: {
        axisLabel: { color: "#9aa0a6", formatter: (v) => `${v}%` },
        splitLine: { lineStyle: { color: "#2a2f3a" } },
      },
      series: ddSeries,
    }, { notMerge: true });
  }

  if ($("paper-position-chart")) {
    if (!paperPositionChart) paperPositionChart = echarts.init($("paper-position-chart"));
    const posName = charts.position_label || "沪深300权重";
    paperPositionChart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", valueFormatter: (v) => v + "%" },
      grid: baseGrid(),
      xAxis: { type: "category", data: dates, axisLabel: { color: "#9aa0a6", formatter: fmtChartDate, hideOverlap: true } },
      yAxis: {
        max: 100,
        axisLabel: { color: "#9aa0a6", formatter: (v) => v + "%" },
        splitLine: { lineStyle: { color: "#2a2f3a" } },
      },
      series: [{
        name: posName,
        type: "line",
        step: "end",
        showSymbol: false,
        data: positions,
        lineStyle: { color: "#4c8bf5", width: 2 },
        areaStyle: { color: "rgba(76,139,245,0.12)" },
      }],
    }, { notMerge: true });
  }
}

function renderPaperHistory(records, initialCapital = 1000000) {
  const body = $("paper-history-body");
  if (!body) return;
  if (!records?.length) {
    body.innerHTML = '<tr><td colspan="16" class="empty-hint">暂无模拟交易记录</td></tr>';
    return;
  }
  const fmtContrib = (v) => (v != null && Math.abs(v) > 1e-9 ? fmtPctSigned(v) : "--");
  body.innerHTML = records.map((r) => {
    const posChange = `${fmtPct(r.old_position)} → ${fmtPct(r.new_position)}`;
    const capital = r.equity_ratio != null
      ? fmtMoney(r.equity_ratio * initialCapital)
      : "--";
    const retCls = signedClass(r.period_return);
    const fmtSmall = (v) => (v != null && !Number.isNaN(v) ? fmtPctSigned(v) : "--");
    return `<tr>
      <td>${escapeHtml(r.date)}</td>
      <td class="${directionClass(r.direction)}">${escapeHtml(r.direction)}</td>
      <td>${((r.confidence || 0) * 100).toFixed(0)}%</td>
      <td>${posChange}${r.trade_occurred ? " ✓" : ""}</td>
      <td class="${retCls}">${fmtPctSigned(r.period_return)}</td>
      <td class="${signedClass(r.strategy_return)}">${fmtSmall(r.strategy_return)}</td>
      <td class="${signedClass(r.cash_return)}">${fmtSmall(r.cash_return)}</td>
      <td>${fmtContrib(r.cash_contrib_csi300)}</td>
      <td>${fmtContrib(r.cash_contrib_nasdaq)}</td>
      <td>${fmtContrib(r.cash_contrib_gold)}</td>
      <td>${fmtContrib(r.cash_contrib_bond)}</td>
      <td>${fmtContrib(r.cash_contrib_mm)}</td>
      <td>${fmtContrib(r.cash_contrib_repo)}</td>
      <td>${r.cost_ratio ? fmtPctSigned(-Math.abs(r.cost_ratio)) : "--"}</td>
      <td class="${signedClass(r.benchmark_return)}">${fmtSmall(r.benchmark_return)}</td>
      <td>${capital}</td>
    </tr>`;
  }).join("");
}

export async function loadPaperTrading() {
  try {
    const provider = currentPaperProvider();
    const q = provider ? `?provider=${encodeURIComponent(provider)}` : "";
    const data = await api(`/api/paper-trading/status${q}`);
    const initial = data.status?.initial_capital || 1000000;
    const label = channelLabels[data.llm_provider || provider] || data.llm_provider || provider || "--";
    if ($("paper-hint")) {
      const auto = data.status?.auto_trade_enabled ? "自动交易开" : "自动交易关";
      $("paper-hint").textContent = `只读 · 渠道 ${label} · ${auto}（前端不触发交易）`;
    }
    renderPaperStats(data);
    renderPaperPerformance(data.performance);
    renderPaperCashDetail(data.cash_detail);
    renderPaperCharts(data.charts, initial);
    const histQ = provider
      ? `/api/paper-trading/history?limit=50&provider=${encodeURIComponent(provider)}`
      : "/api/paper-trading/history?limit=50";
    const hist = await api(histQ);
    renderPaperHistory(hist.records, initial);
    requestAnimationFrame(() => {
      paperEquityChart?.resize();
      paperDrawdownChart?.resize();
      paperPositionChart?.resize();
      paperCashChart?.resize();
    });
  } catch (e) {
    console.warn("模拟实盘加载失败:", e.message);
    if ($("paper-hint")) $("paper-hint").textContent = `加载失败: ${e.message}`;
  }
}
function renderPaperIdleAlloc(st) {
  const box = $("paper-idle-alloc");
  if (!box) return;
  const items = st.sleeve_breakdown || st.idle_breakdown || [];
  const unified = st.allocation_mode === "unified_sharpe";

  if (!st.cash_mgmt_enabled || !items.length) {
    box.innerHTML = "";
    return;
  }

  const title = unified
    ? `多资产配置明细 · 总资产 ${fmtMoney(st.equity)}`
    : `闲置资金配置明细 · 合计 ${fmtMoney(st.idle_total != null ? st.idle_total : st.cash)}（占总资产 ${fmtPct(st.idle_ratio)}）`;

  box.innerHTML = `
    <div class="paper-idle-title">${title}</div>
    <div class="paper-idle-grid">
      ${items.map((item) => `
        <div class="paper-idle-card" style="border-left-color: ${layerColor(item.key)}">
          <div class="paper-idle-name">${escapeHtml(item.label)}</div>
          <div class="paper-idle-amount">${fmtMoney(item.amount)}</div>
          <div class="paper-idle-meta">
            占总资产 ${(item.weight_in_equity * 100).toFixed(1)}%
            ${unified ? "" : ` · 占闲置 ${(item.weight_in_idle * 100).toFixed(1)}%`}
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderPaperCashDetail(cash) {
  const block = $("paper-cash-block");
  if (!block) return;
  if (!cash?.enabled) {
    block.classList.add("hidden");
    paperCashChart?.clear();
    if ($("paper-cash-breakdown")) $("paper-cash-breakdown").innerHTML = "";
    if ($("paper-cash-weights")) $("paper-cash-weights").innerHTML = "";
    return;
  }

  block.classList.remove("hidden");
  const unified = cash.allocation_mode === "unified_sharpe";
  const hint = $("paper-cash-hint");
  if (hint) {
    const regime = cash.latest_regime ? ` · 当前状态 ${cash.latest_regime}` : "";
    hint.textContent = unified
      ? `组合累计收益 ${fmtPctSigned(cash.total_contribution)}${regime} · 六袖绝对权重`
      : `累计闲置贡献 ${fmtPctSigned(cash.total_contribution)}${regime} · 按闲置比例 × 配置权重 × 品种日收益`;
  }

  const breakdownBox = $("paper-cash-breakdown");
  if (breakdownBox) {
    breakdownBox.innerHTML = (cash.breakdown || []).map((item) => {
      const retCls = signedClass(item.total_contribution);
      return `
        <div class="cash-asset-chip" style="border-left: 3px solid ${layerColor(item.key)}">
          ${escapeHtml(item.label)}
          <span class="hint">权重 ${(item.avg_weight * 100).toFixed(0)}%</span>
          <strong class="${retCls}">${item.total_contribution ? fmtPctSigned(item.total_contribution) : fmtPct(item.weight)}</strong>
        </div>
      `;
    }).join("");
  }

  const weightsBox = $("paper-cash-weights");
  if (weightsBox && cash.latest_weights) {
    const latest = cash.latest_contributions || {};
    weightsBox.innerHTML = Object.entries(cash.latest_weights).map(([key, w]) => {
      if (!w) return "";
      const dayContrib = latest[key];
      const contribText = dayContrib != null && dayContrib !== 0 ? ` · 当日 ${fmtPctSigned(dayContrib)}` : "";
      return `<div class="metric-chip"><span>${CASH_ASSET_LABELS[key] || key}</span><strong>${(w * 100).toFixed(1)}%${contribText}</strong></div>`;
    }).join("");
  }

  const dates = cash.dates || [];
  const chartEl = $("paper-cash-chart");
  if (!chartEl || !dates.length) return;
  if (!paperCashChart) paperCashChart = echarts.init(chartEl);

  const series = (cash.breakdown || []).map((item) => ({
    name: item.label,
    type: "line",
    smooth: true,
    showSymbol: dates.length <= 30,
    data: item.cumulative_curve || [],
    lineStyle: { color: layerColor(item.key), width: 2 },
  }));

  if (cash.contribution_curve?.length) {
    series.push({
      name: unified ? "组合累计" : "闲置合计",
      type: "line",
      smooth: true,
      showSymbol: false,
      data: cash.contribution_curve,
      lineStyle: { color: "#9aa0a6", width: 1.5, type: "dashed" },
    });
  }

  paperCashChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => (v != null ? fmtPctSigned(v) : "--"),
    },
    legend: series.length > 1
      ? { data: series.map((s) => s.name), textStyle: { color: "#9aa0a6" }, type: "scroll" }
      : undefined,
    grid: baseGrid(),
    xAxis: { type: "category", data: dates, axisLabel: { color: "#9aa0a6", formatter: fmtChartDate, hideOverlap: true } },
    yAxis: {
      scale: true,
      axisLabel: { color: "#9aa0a6", formatter: (v) => `${(v * 100).toFixed(2)}%` },
      splitLine: { lineStyle: { color: "#2a2f3a" } },
    },
    series,
  }, { notMerge: true });
}
