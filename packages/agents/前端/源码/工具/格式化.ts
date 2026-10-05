export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return `${(v * 100).toFixed(2)}%`;
}

export function fmtPctSigned(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  const pct = (v * 100).toFixed(2);
  return v > 0 ? `+${pct}%` : `${pct}%`;
}

/** 优先显示名称，空名时回退代码；可选再查本地名称映射 */
export function fmtStockName(
  row: { symbol?: string | null; name?: string | null } | string | null | undefined,
  nameBySymbol?: Map<string, { name?: string } | string> | Record<string, string>,
): string {
  if (row == null) return "";
  if (typeof row === "string") {
    const sym = row.padStart(6, "0");
    const mapped = lookupName(sym, nameBySymbol);
    return mapped || sym;
  }
  const sym = String(row.symbol ?? "").padStart(6, "0");
  const name = String(row.name ?? "").trim();
  if (name) return name;
  const mapped = lookupName(sym, nameBySymbol);
  return mapped || sym || "";
}

function lookupName(
  sym: string,
  nameBySymbol?: Map<string, { name?: string } | string> | Record<string, string>,
): string {
  if (!nameBySymbol || !sym) return "";
  if (nameBySymbol instanceof Map) {
    const hit = nameBySymbol.get(sym) ?? nameBySymbol.get(sym.replace(/^0+/, ""));
    if (hit == null) return "";
    if (typeof hit === "string") return hit.trim();
    return String(hit.name ?? "").trim();
  }
  const hit = nameBySymbol[sym] ?? nameBySymbol[sym.replace(/^0+/, "")];
  return String(hit ?? "").trim();
}

export function signedClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "";
  if (v > 0) return "positive";
  if (v < 0) return "negative";
  return "";
}

export const chartTheme = {
  axis: { color: "#9aa0a6" },
  split: "#2a2f3a",
};

export const marketStateOrder = ["恐慌", "震荡偏弱", "横盘", "震荡偏强", "狂热"] as const;

export const marketStateColors: Record<string, string> = {
  恐慌: "rgba(240, 113, 120, 0.22)",
  震荡偏弱: "rgba(245, 166, 35, 0.18)",
  横盘: "rgba(154, 160, 166, 0.14)",
  震荡偏强: "rgba(76, 139, 245, 0.18)",
  狂热: "rgba(62, 207, 142, 0.22)",
};

export const marketStateSolidColors: Record<string, string> = {
  恐慌: "#f07178",
  震荡偏弱: "#f5a623",
  横盘: "#9aa0a6",
  震荡偏强: "#4c8bf5",
  狂热: "#3ecf8e",
};

export const agentColors = [
  "#4c8bf5",
  "#f5a623",
  "#3ecf8e",
  "#e06c9f",
  "#56d4c8",
  "#c9a227",
  "#9b8afb",
];

export function baseGrid(extra: { bottom?: number; left?: number } = {}) {
  return { left: extra.left ?? 52, right: 20, top: 36, bottom: extra.bottom ?? 48, containLabel: true };
}

export function horizontalBarHeight(count: number, min = 280, perRow = 28, padding = 72, max = 720): number {
  if (count <= 0) return min;
  return Math.min(Math.max(min, count * perRow + padding), max);
}

export function truncateChartLabel(text: string, maxLen = 8): string {
  const value = String(text ?? "").trim();
  if (value.length <= maxLen) return value;
  return `${value.slice(0, maxLen)}…`;
}

export function barZoomX(count: number, axisIndex = 0) {
  const zooms: Record<string, unknown>[] = [{ type: "inside", xAxisIndex: axisIndex }];
  if (count > 8) {
    zooms.push({
      type: "slider",
      xAxisIndex: axisIndex,
      height: 20,
      bottom: 6,
      borderColor: chartTheme.split,
      fillerColor: "rgba(76, 139, 245, 0.12)",
      handleStyle: { color: "#4c8bf5" },
      textStyle: { color: chartTheme.axis.color, fontSize: 10 },
      brushSelect: false,
    });
  }
  return zooms;
}

export function barZoomY(count: number, axisIndex = 0) {
  const zooms: Record<string, unknown>[] = [{ type: "inside", yAxisIndex: axisIndex }];
  if (count > 10) {
    zooms.push({
      type: "slider",
      yAxisIndex: axisIndex,
      width: 14,
      right: 6,
      top: 36,
      bottom: 12,
      borderColor: chartTheme.split,
      fillerColor: "rgba(76, 139, 245, 0.12)",
      handleStyle: { color: "#4c8bf5" },
      textStyle: { color: chartTheme.axis.color, fontSize: 10 },
      brushSelect: false,
    });
  }
  return zooms;
}

export function chartTimeGrid(extra: Record<string, number> = {}) {
  return { left: 52, right: 48, top: 40, bottom: 88, containLabel: true, ...extra };
}

export function chartTimeDataZoom() {
  return [
    { type: "inside", xAxisIndex: 0 },
    {
      type: "slider",
      xAxisIndex: 0,
      height: 22,
      bottom: 10,
      borderColor: chartTheme.split,
      fillerColor: "rgba(76, 139, 245, 0.12)",
      handleStyle: { color: "#4c8bf5" },
      textStyle: { color: chartTheme.axis.color, fontSize: 11 },
    },
  ];
}

export function formatChartDate(value: string) {
  const text = String(value || "");
  if (text.length >= 10) return text.slice(5);
  return text;
}

export function formatSymbolChartDate(value: string) {
  const text = String(value || "");
  if (text.length >= 10) return text.slice(0, 10);
  return text;
}
