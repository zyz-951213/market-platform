import { fmtChartDate } from "@/公共/工具.js";

export const chartTheme = {
  axis: { color: "#9aa0a6" },
  split: "#2a2f3a",
};

export const cashAssetColors = {
  strategy: "#3ecf8e",
  csi300: "#3ecf8e",
  gold: "#c9a227",
  bond: "#7cb87c",
  nasdaq: "#4c8bf5",
  repo: "#9aa0a6",
  mm: "#56d4c8",
  idle: "#6b7280",
  idle_uninvested: "#6b7280",
  // 与黄金 #c9a227 拉开；虚线参考用高对比白橙，避免图例/折线被默认色盘盖掉
  portfolio: "#ff8a3d",
  actual: "#e06c9f",
};

export function baseGrid() {
  return { left: 52, right: 20, top: 36, bottom: 32 };
}

export function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export function layerColor(key) {
  return cashAssetColors[key] || "#9aa0a6";
}

/** 图表 dataZoom：默认展示全区间，仍可用滑块/滚轮缩放。 */
export function recentZoomConfig(dates, _recentBars = 120) {
  return [
    {
      type: "inside",
      start: 0,
      end: 100,
      minValueSpan: 20,
      zoomOnMouseWheel: true,
      moveOnMouseMove: true,
    },
    {
      type: "slider",
      height: 16,
      bottom: 2,
      start: 0,
      end: 100,
      minValueSpan: 20,
      borderColor: "#2a2f3a",
      fillerColor: "rgba(76,139,245,0.15)",
    },
  ];
}

export function dateAxisLabel() {
  return {
    color: chartTheme.axis.color || "#9aa0a6",
    hideOverlap: true,
    showMaxLabel: true,
    formatter: fmtChartDate,
  };
}
