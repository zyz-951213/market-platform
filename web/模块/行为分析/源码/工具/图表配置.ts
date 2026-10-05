import type { EChartsOption } from "echarts";
import type { AgentsChartData, BatchRow, EquityChartData, IndustryRow, KlineSymbolData, PaperNavCompareData, ProbsChartData, StatesChartData } from "@/类型/接口";
import {
  agentColors,
  barZoomX,
  barZoomY,
  baseGrid,
  chartTheme,
  chartTimeDataZoom,
  chartTimeGrid,
  formatChartDate,
  formatSymbolChartDate,
  fmtStockName,
  marketStateColors,
  marketStateSolidColors,
  truncateChartLabel,
} from "./格式化";

export function batchBarOption(rows: BatchRow[]): EChartsOption {
  const labels = rows.map((r) => fmtStockName(r));
  const count = labels.length;
  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter(params) {
        const list = Array.isArray(params) ? params : [params];
        const idx = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
        const row = rows[idx] || {};
        const title = fmtStockName(row);
        const lines = list.map((p) => {
          const item = p as { marker?: string; seriesName?: string; data?: number };
          return `${item.marker}${item.seriesName}: ${((item.data || 0) * 100).toFixed(2)}%`;
        });
        return `${title}<br/>${lines.join("<br/>")}<br/><span style="opacity:.7">点击查看个股详情</span>`;
      },
    },
    legend: { data: ["策略", "基准"], textStyle: { color: chartTheme.axis.color } },
    grid: { left: 8, right: count > 10 ? 28 : 16, top: 40, bottom: 12, containLabel: true },
    dataZoom: barZoomY(count),
    xAxis: {
      type: "value",
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 11,
        fontStyle: "normal",
        formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      triggerEvent: true,
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 11,
        fontStyle: "normal",
        width: 108,
        overflow: "truncate",
        interval: 0,
        margin: 8,
      },
      axisTick: { alignWithLabel: true },
    },
    series: [
      {
        name: "策略",
        type: "bar",
        barGap: "20%",
        data: rows.map((r) => r.strategy_return),
        itemStyle: { color: "#3ecf8e" },
        cursor: "pointer",
      },
      {
        name: "基准",
        type: "bar",
        data: rows.map((r) => r.benchmark_return),
        itemStyle: { color: "#4c8bf5" },
        cursor: "pointer",
      },
    ],
  };
}

export function industryExcessOption(
  rows: IndustryRow[],
  options?: {
    metric?: keyof IndustryRow | string;
    seriesName?: string;
  },
): EChartsOption {
  const metric = (options?.metric || "median_excess_return") as keyof IndustryRow;
  const seriesName = options?.seriesName || "中位数超额";
  const sectors = rows.map((r) => r.sector);
  const values = rows.map((r) => {
    const v = r[metric];
    return v == null || Number.isNaN(Number(v)) ? null : Number(v);
  });
  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter(params) {
        const list = Array.isArray(params) ? params : [params];
        const p = list[0] as { name?: string; value?: number | null; marker?: string };
        const val = p?.value;
        const text = val == null || Number.isNaN(Number(val)) ? "--" : `${(Number(val) * 100).toFixed(2)}%`;
        return `${p?.name || ""}<br/>${p?.marker || ""}${seriesName}: ${text}`;
      },
    },
    grid: baseGrid({ bottom: sectors.length > 10 ? 96 : 72 }),
    dataZoom: barZoomX(sectors.length),
    xAxis: {
      type: "category",
      data: sectors,
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 10,
        fontStyle: "normal",
        rotate: sectors.length > 12 ? 50 : 35,
        margin: 14,
        interval: 0,
        hideOverlap: false,
        formatter: (v: string) => truncateChartLabel(v, 6),
      },
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: chartTheme.axis.color, fontStyle: "normal", formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [{
      name: seriesName,
      type: "bar",
      data: values,
      itemStyle: {
        color: (p: { value?: number | null }) => {
          const v = p.value;
          if (v == null || Number.isNaN(Number(v))) return "#6b7280";
          return Number(v) >= 0 ? "#3ecf8e" : "#f07178";
        },
      },
    }],
  };
}

export function industryWinOption(
  rows: IndustryRow[],
  options?: {
    winKey?: keyof IndustryRow | string;
    loseKey?: keyof IndustryRow | string;
    flatKey?: keyof IndustryRow | string;
    rateKey?: keyof IndustryRow | string;
    title?: string;
  },
): EChartsOption {
  const winKey = (options?.winKey || "n_win") as keyof IndustryRow;
  const loseKey = (options?.loseKey || "n_lose") as keyof IndustryRow;
  const flatKey = (options?.flatKey || "n_flat") as keyof IndustryRow;
  const rateKey = (options?.rateKey || "win_rate") as keyof IndustryRow;
  const title = options?.title || "胜负只数";
  const sectors = rows.map((r) => r.sector);
  const wins = rows.map((r) => {
    const v = r[winKey];
    return v == null || Number.isNaN(Number(v)) ? 0 : Number(v);
  });
  const loses = rows.map((r) => {
    const v = r[loseKey];
    return v == null || Number.isNaN(Number(v)) ? 0 : Number(v);
  });
  const flats = rows.map((r) => {
    const v = r[flatKey];
    return v == null || Number.isNaN(Number(v)) ? 0 : Number(v);
  });
  const rates = rows.map((r) => {
    const v = r[rateKey];
    return v == null || Number.isNaN(Number(v)) ? null : Number(v);
  });
  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter(params) {
        const list = Array.isArray(params) ? params : [params];
        if (!list.length) return "";
        const name = String((list[0] as { name?: string }).name || "");
        const lines = [name];
        for (const raw of list) {
          const p = raw as { seriesName?: string; value?: number | null; marker?: string; seriesType?: string };
          if (p.seriesType === "line") {
            const val = p.value;
            const text = val == null || Number.isNaN(Number(val)) ? "—" : `${(Number(val) * 100).toFixed(1)}%`;
            lines.push(`${p.marker || ""}${p.seriesName || "胜率"}: ${text}`);
          } else {
            lines.push(`${p.marker || ""}${p.seriesName || ""}: ${p.value ?? 0} 只`);
          }
        }
        const idx = sectors.indexOf(name);
        if (idx >= 0) {
          const total = wins[idx] + loses[idx] + flats[idx];
          lines.push(`合计: ${total} 只`);
        }
        return lines.join("<br/>");
      },
    },
    legend: {
      data: ["跑赢", "跑输", "持平", "胜率"],
      textStyle: { color: chartTheme.axis.color, fontSize: 11 },
      top: 0,
    },
    grid: { ...baseGrid({ bottom: sectors.length > 10 ? 96 : 72 }), top: 40, right: 48 },
    dataZoom: barZoomX(sectors.length),
    xAxis: {
      type: "category",
      data: sectors,
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 10,
        fontStyle: "normal",
        rotate: sectors.length > 12 ? 50 : 35,
        margin: 14,
        interval: 0,
        hideOverlap: false,
        formatter: (v: string) => truncateChartLabel(v, 6),
      },
      axisTick: { alignWithLabel: true },
    },
    yAxis: [
      {
        type: "value",
        name: "只数",
        nameTextStyle: { color: chartTheme.axis.color, fontSize: 11 },
        axisLabel: { color: chartTheme.axis.color, fontStyle: "normal" },
        splitLine: { lineStyle: { color: chartTheme.split } },
      },
      {
        type: "value",
        name: "胜率",
        min: 0,
        max: 1,
        nameTextStyle: { color: chartTheme.axis.color, fontSize: 11 },
        axisLabel: {
          color: chartTheme.axis.color,
          fontStyle: "normal",
          formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
        },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "跑赢",
        type: "bar",
        stack: "outcome",
        barMaxWidth: 28,
        data: wins,
        itemStyle: { color: "#3ecf8e" },
      },
      {
        name: "跑输",
        type: "bar",
        stack: "outcome",
        barMaxWidth: 28,
        data: loses,
        itemStyle: { color: "#f07178" },
      },
      {
        name: "持平",
        type: "bar",
        stack: "outcome",
        barMaxWidth: 28,
        data: flats,
        itemStyle: { color: "#9aa4b2" },
      },
      {
        name: "胜率",
        type: "line",
        yAxisIndex: 1,
        data: rates,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 2, color: "#4c8bf5" },
        itemStyle: { color: "#4c8bf5" },
        z: 5,
      },
    ],
  };
}

export function equityOption(data: EquityChartData): EChartsOption {
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: ["策略", "基准", "超额比", "仓位"], textStyle: { color: chartTheme.axis.color } },
    grid: chartTimeGrid(),
    dataZoom: chartTimeDataZoom(),
    xAxis: {
      type: "category",
      data: data.dates,
      axisLabel: { color: chartTheme.axis.color, fontSize: 11, margin: 14, hideOverlap: true, rotate: 30, formatter: formatSymbolChartDate },
    },
    yAxis: [
      { name: "净值(归一化)", scale: true, axisLabel: { color: chartTheme.axis.color }, splitLine: { lineStyle: { color: chartTheme.split } } },
      { name: "仓位", min: 0, max: 1, axisLabel: { color: chartTheme.axis.color, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }, splitLine: { show: false } },
    ],
    series: [
      { name: "策略", type: "line", yAxisIndex: 0, smooth: false, showSymbol: false, data: data.strategy, lineStyle: { color: "#3ecf8e", width: 2 } },
      { name: "基准", type: "line", yAxisIndex: 0, smooth: false, showSymbol: false, data: data.benchmark, lineStyle: { color: "#4c8bf5", width: 2 } },
      { name: "超额比", type: "line", yAxisIndex: 0, smooth: false, showSymbol: false, data: data.excess, lineStyle: { color: "#f5a623", width: 1, type: "dashed" } },
      { name: "仓位", type: "line", yAxisIndex: 1, step: "end", showSymbol: false, data: data.position, lineStyle: { color: "rgba(155,138,251,0.9)", width: 1 }, areaStyle: { color: "rgba(155,138,251,0.15)" } },
    ],
  };
}

export function statesOption(data: StatesChartData): EChartsOption {
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: ["仓位", "状态"], textStyle: { color: chartTheme.axis.color } },
    grid: chartTimeGrid(),
    dataZoom: chartTimeDataZoom(),
    xAxis: {
      type: "category",
      data: data.dates,
      axisLabel: { color: chartTheme.axis.color, fontSize: 11, margin: 14, hideOverlap: true, rotate: 30, formatter: formatSymbolChartDate },
    },
    yAxis: [
      { type: "value", min: 0, max: 1, axisLabel: { color: chartTheme.axis.color, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }, splitLine: { lineStyle: { color: chartTheme.split } } },
      { type: "value", min: 0, max: 4, axisLabel: { color: chartTheme.axis.color, formatter: (v: number) => data.state_order[v] || "" }, splitLine: { show: false } },
    ],
    series: [
      { name: "仓位", type: "line", yAxisIndex: 0, smooth: true, showSymbol: false, data: data.positions, lineStyle: { color: "#f5a623", width: 2 }, areaStyle: { color: "rgba(245,166,35,0.12)" } },
      { name: "状态", type: "line", yAxisIndex: 1, step: "end", showSymbol: false, data: data.state_values, lineStyle: { color: "#9b8afb", width: 1.5 } },
    ],
  };
}

export function agentsOption(data: AgentsChartData): EChartsOption {
  const names = Object.keys(data.series || {});
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: names, textStyle: { color: chartTheme.axis.color }, type: "scroll" },
    grid: chartTimeGrid({ right: 20 }),
    dataZoom: chartTimeDataZoom(),
    xAxis: {
      type: "category",
      data: data.dates,
      axisLabel: { color: chartTheme.axis.color, fontSize: 11, margin: 14, hideOverlap: true, rotate: 30, formatter: formatSymbolChartDate },
    },
    yAxis: { axisLabel: { color: chartTheme.axis.color }, splitLine: { lineStyle: { color: chartTheme.split } } },
    series: names.map((name, i) => ({
      name,
      type: "line",
      smooth: true,
      showSymbol: false,
      data: data.series[name],
      lineStyle: { color: agentColors[i % agentColors.length], width: 1.5 },
    })),
  };
}

export function probsOption(data: ProbsChartData): EChartsOption {
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { ...baseGrid(), bottom: 40 },
    xAxis: {
      type: "category",
      data: data.states,
      axisLabel: { color: chartTheme.axis.color, fontSize: 11, fontStyle: "normal", interval: 0 },
    },
    yAxis: {
      max: 1,
      axisLabel: { color: chartTheme.axis.color, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [{ type: "bar", data: data.probabilities, itemStyle: { color: "#4c8bf5" } }],
  };
}

export function portfolioEquityOption(data: EquityChartData): EChartsOption {
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: ["组合", "基准", "超额比", "仓位"], textStyle: { color: chartTheme.axis.color } },
    grid: chartTimeGrid(),
    dataZoom: chartTimeDataZoom(),
    xAxis: {
      type: "category",
      data: data.dates,
      axisLabel: { color: chartTheme.axis.color, fontSize: 11, margin: 14, hideOverlap: true, formatter: formatChartDate },
    },
    yAxis: [
      { name: "净值", scale: true, axisLabel: { color: chartTheme.axis.color }, splitLine: { lineStyle: { color: chartTheme.split } } },
      { name: "仓位", min: 0, max: 1, axisLabel: { color: chartTheme.axis.color, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }, splitLine: { show: false } },
    ],
    series: [
      { name: "组合", type: "line", yAxisIndex: 0, showSymbol: false, data: data.strategy, lineStyle: { color: "#3ecf8e", width: 2 } },
      { name: "基准", type: "line", yAxisIndex: 0, showSymbol: false, data: data.benchmark, lineStyle: { color: "#4c8bf5", width: 2 } },
      { name: "超额比", type: "line", yAxisIndex: 0, showSymbol: false, data: data.excess, lineStyle: { color: "#f5a623", width: 1, type: "dashed" } },
      { name: "仓位", type: "line", yAxisIndex: 1, step: "end", showSymbol: false, data: data.position, lineStyle: { color: "rgba(155,138,251,0.9)" }, areaStyle: { color: "rgba(155,138,251,0.12)" } },
    ],
  };
}

export function sectorPieOption(weights: Record<string, number>): EChartsOption {
  const data = Object.entries(weights || {}).map(([name, value]) => ({ name, value }));
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "item", formatter: "{b}: {d}%" },
    series: [{ type: "pie", radius: ["40%", "68%"], data, label: { color: chartTheme.axis.color } }],
  };
}

export function heatOption(holdings: { name?: string; symbol: string; suggested_position?: number | null; weight?: number | null }[]): EChartsOption {
  const names = holdings.map((h) => fmtStockName(h));
  const count = names.length;
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { data: ["仓位", "权重"], textStyle: { color: chartTheme.axis.color } },
    grid: { left: 8, right: count > 8 ? 28 : 16, top: 36, bottom: 12, containLabel: true },
    dataZoom: barZoomY(count),
    xAxis: {
      type: "value",
      max: 1,
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 11,
        fontStyle: "normal",
        formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    yAxis: {
      type: "category",
      data: names,
      inverse: true,
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 10,
        fontStyle: "normal",
        width: 96,
        overflow: "truncate",
        interval: 0,
      },
      axisTick: { alignWithLabel: true },
    },
    series: [
      { name: "仓位", type: "bar", barGap: "20%", data: holdings.map((h) => h.suggested_position), itemStyle: { color: "#9b8afb" } },
      { name: "权重", type: "bar", data: holdings.map((h) => h.weight), itemStyle: { color: "#3ecf8e" } },
    ],
  };
}

export function normalizeChartDate(value: string): string {
  const text = String(value || "");
  if (text.length >= 10) return text.slice(0, 10);
  return text;
}

function buildStateMarkAreas(dates: string[], stateNames: string[]) {
  if (!dates.length) return [];
  const areas: [{ xAxis: string; itemStyle?: { color: string } }, { xAxis: string }][] = [];
  let start = 0;
  for (let i = 1; i <= dates.length; i += 1) {
    if (i === dates.length || stateNames[i] !== stateNames[start]) {
      const state = stateNames[start] || "横盘";
      areas.push([
        { xAxis: dates[start], itemStyle: { color: marketStateColors[state] || marketStateColors["横盘"] } },
        { xAxis: dates[i - 1] },
      ]);
      start = i;
    }
  }
  return areas;
}

function alignOverlayToKline(kdata: KlineSymbolData, overlay?: StatesChartData | null) {
  const dates = kdata.dates.map(normalizeChartDate);
  const lookup = new Map<string, { state: string; position: number | null }>();
  if (overlay?.dates?.length) {
    overlay.dates.forEach((d, i) => {
      lookup.set(normalizeChartDate(d), {
        state: overlay.states[i] || "横盘",
        position: overlay.positions[i] ?? null,
      });
    });
  }

  const states: string[] = [];
  const positions: (number | null)[] = [];
  let lastState = "横盘";
  let lastPos: number | null = null;
  for (const d of dates) {
    const hit = lookup.get(d);
    if (hit) {
      lastState = hit.state;
      lastPos = hit.position;
    }
    states.push(lastState);
    positions.push(lastPos);
  }
  return { dates, states, positions };
}

/** ECharts candlestick 的 data 可能是 [o,c,l,h] 或 [idx,o,c,l,h] */
function unpackCandle(data: unknown): { open?: number; close?: number; low?: number; high?: number } {
  if (!Array.isArray(data) || data.length < 4) return {};
  const nums = data.map((v) => Number(v));
  if (nums.length === 4) {
    const [open, close, low, high] = nums;
    return { open, close, low, high };
  }
  const open = nums[1];
  const close = nums[2];
  const low = nums[3];
  const high = nums[4];
  return { open, close, low, high };
}

export function symbolKlineOption(
  kdata: KlineSymbolData,
  overlay?: StatesChartData | null,
): EChartsOption {
  const aligned = alignOverlayToKline(kdata, overlay);
  const hasOverlay = aligned.positions.some((p) => p != null);
  const markAreas = buildStateMarkAreas(aligned.dates, aligned.states);
  const positionData = aligned.positions.map((p) => (p == null ? 0 : p));

  const nDates = aligned.dates.length;
  // 全历史可缩放；默认先看最近约 500 根，滑块可拉到上市日
  const startPct = nDates > 500 ? Math.max(0, (1 - 500 / nDates) * 100) : 0;
  const dataZoom = [
    { type: "inside", xAxisIndex: hasOverlay ? [0, 1] : [0], start: startPct, end: 100 },
    {
      type: "slider",
      xAxisIndex: hasOverlay ? [0, 1] : [0],
      start: startPct,
      end: 100,
      height: 22,
      bottom: 10,
      borderColor: chartTheme.split,
      fillerColor: "rgba(76, 139, 245, 0.12)",
      handleStyle: { color: "#4c8bf5" },
      textStyle: { color: chartTheme.axis.color, fontSize: 11 },
    },
  ];

  if (!hasOverlay) {
    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        formatter(params) {
          const list = Array.isArray(params) ? params : [params];
          const candle = list.find((p) => (p as { seriesType?: string }).seriesType === "candlestick") || list[0];
          const item = candle as { axisValue?: string; data?: unknown };
          const { open, close, low, high } = unpackCandle(item.data);
          const date = item.axisValue || "";
          if (open == null && close == null) return date;
          return [
            date,
            `开 ${open?.toFixed(2) ?? "--"}`,
            `收 ${close?.toFixed(2) ?? "--"}`,
            `低 ${low?.toFixed(2) ?? "--"}`,
            `高 ${high?.toFixed(2) ?? "--"}`,
          ].join("<br/>");
        },
      },
      grid: chartTimeGrid({ top: 20, bottom: 72, left: 48 }),
      dataZoom,
      xAxis: {
        type: "category",
        data: aligned.dates,
        axisLabel: {
          color: chartTheme.axis.color,
          fontSize: 10,
          hideOverlap: true,
          formatter: formatSymbolChartDate,
        },
        axisLine: { lineStyle: { color: chartTheme.split } },
      },
      yAxis: {
        scale: true,
        axisLabel: { color: chartTheme.axis.color, fontSize: 10 },
        splitLine: { lineStyle: { color: chartTheme.split } },
      },
      series: [{
        type: "candlestick",
        data: kdata.bars,
        itemStyle: { color: "#f07178", color0: "#3ecf8e", borderColor: "#f07178", borderColor0: "#3ecf8e" },
      }],
    };
  }

  return {
    backgroundColor: "transparent",
    animation: false,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      formatter(params) {
        const list = Array.isArray(params) ? params : [params];
        const idx = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
        const candle = list.find((p) => (p as { seriesType?: string }).seriesType === "candlestick");
        const item = (candle || list[0]) as { axisValue?: string; data?: unknown };
        const { open, close, low, high } = unpackCandle(item.data);
        const date = aligned.dates[idx] || item.axisValue || "";
        const state = aligned.states[idx] || "--";
        const pos = aligned.positions[idx];
        const lines = [
          date,
          `状态 ${state}`,
          pos != null ? `仓位 ${(pos * 100).toFixed(0)}%` : null,
          `开 ${open?.toFixed(2) ?? "--"} · 收 ${close?.toFixed(2) ?? "--"}`,
          `低 ${low?.toFixed(2) ?? "--"} · 高 ${high?.toFixed(2) ?? "--"}`,
        ].filter(Boolean);
        return lines.join("<br/>");
      },
    },
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    grid: [
      { left: 48, right: 16, top: 28, height: "58%" },
      { left: 48, right: 16, top: "72%", height: "14%" },
    ],
    dataZoom,
    xAxis: [
      {
        type: "category",
        data: aligned.dates,
        gridIndex: 0,
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: chartTheme.split } },
      },
      {
        type: "category",
        data: aligned.dates,
        gridIndex: 1,
        axisLabel: {
          color: chartTheme.axis.color,
          fontSize: 10,
          hideOverlap: true,
          formatter: formatSymbolChartDate,
        },
        axisLine: { lineStyle: { color: chartTheme.split } },
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        axisLabel: { color: chartTheme.axis.color, fontSize: 10 },
        splitLine: { lineStyle: { color: chartTheme.split } },
      },
      {
        gridIndex: 1,
        min: 0,
        max: 1,
        splitNumber: 2,
        axisLabel: {
          color: chartTheme.axis.color,
          fontSize: 9,
          formatter: (v: number) => `${(v * 100).toFixed(0)}%`,
        },
        splitLine: { lineStyle: { color: chartTheme.split } },
      },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: kdata.bars,
        itemStyle: { color: "#f07178", color0: "#3ecf8e", borderColor: "#f07178", borderColor0: "#3ecf8e" },
        markArea: {
          silent: true,
          itemStyle: { opacity: 1 },
          data: markAreas,
        },
      },
      {
        name: "仓位",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: positionData,
        barMaxWidth: 8,
        itemStyle: {
          color: (p) => {
            const state = aligned.states[p.dataIndex] || "横盘";
            return marketStateSolidColors[state] || "#9aa0a6";
          },
          opacity: 0.85,
        },
      },
    ],
  };
}

export function klineOption(kdata: KlineSymbolData, detail = false): EChartsOption {
  if (detail) return symbolKlineOption(kdata);
  return {
    backgroundColor: "transparent",
    animation: false,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      formatter(params) {
        const list = Array.isArray(params) ? params : [params];
        const item = list[0] as { axisValue?: string; data?: number[] };
        const [open, close, low, high] = item.data || [];
        const date = item.axisValue || "";
        if (!open && !close) return date;
        return [
          date,
          `开 ${open?.toFixed(2) ?? "--"}`,
          `收 ${close?.toFixed(2) ?? "--"}`,
          `低 ${low?.toFixed(2) ?? "--"}`,
          `高 ${high?.toFixed(2) ?? "--"}`,
        ].join("<br/>");
      },
    },
    grid: detail
      ? chartTimeGrid({ top: 20, bottom: 72, left: 48 })
      : { left: 42, right: 12, top: 12, bottom: 28, containLabel: true },
    dataZoom: detail ? chartTimeDataZoom() : undefined,
    xAxis: {
      type: "category",
      data: kdata.dates,
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 10,
        hideOverlap: true,
        formatter: detail ? formatSymbolChartDate : undefined,
      },
      axisLine: { lineStyle: { color: chartTheme.split } },
    },
    yAxis: {
      scale: true,
      axisLabel: { color: chartTheme.axis.color, fontSize: 10 },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [{
      type: "candlestick",
      data: kdata.bars,
      itemStyle: { color: "#f07178", color0: "#3ecf8e", borderColor: "#f07178", borderColor0: "#3ecf8e" },
    }],
  };
}

export function probsFromMap(probs: Record<string, number>): EChartsOption {
  const states = Object.keys(probs);
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { ...baseGrid(), bottom: 40 },
    xAxis: {
      type: "category",
      data: states,
      axisLabel: { color: chartTheme.axis.color, fontSize: 11, fontStyle: "normal", interval: 0 },
    },
    yAxis: { max: 1, axisLabel: { color: chartTheme.axis.color, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }, splitLine: { lineStyle: { color: chartTheme.split } } },
    series: [{ type: "bar", data: states.map((s) => probs[s]), itemStyle: { color: "#4c8bf5" } }],
  };
}

const paperBenchmarkColors: Record<string, string> = {
  paper: "#3ecf8e",
  sse: "#f07178",
  szse: "#56d4c8",
  chinext: "#e06c9f",
  hs300: "#4c8bf5",
  sz50: "#c9a227",
  csi500: "#9b8afb",
  csi1000: "#f5a623",
  csi2000: "#8fd14f",
  kc50: "#7eb6ff",
};

export function paperNavCompareOption(data: PaperNavCompareData): EChartsOption {
  const names = data.series.map((s) => s.name);
  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter(params) {
        const list = Array.isArray(params) ? params : [params];
        const idx = (list[0] as { dataIndex?: number })?.dataIndex ?? 0;
        const date = data.dates[idx] || "";
        const lines = list
          .map((p) => {
            const item = p as { marker?: string; seriesName?: string; data?: number | null };
            const val = item.data;
            const text = val == null || Number.isNaN(val) ? "--" : `${val >= 0 ? "+" : ""}${val.toFixed(2)}%`;
            return `${item.marker}${item.seriesName}: ${text}`;
          })
          .filter(Boolean);
        return `${date}<br/>${lines.join("<br/>")}`;
      },
    },
    legend: {
      data: names,
      type: "scroll",
      textStyle: { color: chartTheme.axis.color },
    },
    grid: chartTimeGrid(),
    dataZoom: chartTimeDataZoom(),
    xAxis: {
      type: "category",
      data: data.dates,
      axisLabel: {
        color: chartTheme.axis.color,
        fontSize: 11,
        margin: 14,
        hideOverlap: true,
        rotate: 30,
        formatter: formatSymbolChartDate,
      },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: {
        color: chartTheme.axis.color,
        formatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`,
      },
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: data.series.map((s) => {
      const color = paperBenchmarkColors[s.id] || "#9aa0a6";
      return {
        name: s.name,
        type: "line" as const,
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        data: s.values,
        z: s.id === "paper" ? 10 : 1,
        lineStyle: { color, width: s.id === "paper" ? 2.5 : 1.5 },
        itemStyle: { color },
        ...(s.id === "paper"
          ? { areaStyle: { color: "rgba(62, 207, 142, 0.10)" } }
          : {}),
      };
    }),
  };
}
