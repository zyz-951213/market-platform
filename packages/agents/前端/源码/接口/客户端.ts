const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

export async function api<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const url = path.startsWith("http") ? path : `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail || res.statusText;
    throw new Error(detail);
  }
  return data as T;
}

export function getOverview() {
  return api<import("@/类型/接口").OverviewData>("/api/overview");
}

export function getIndustrySummary(year?: string | number | null) {
  const params = new URLSearchParams();
  if (year !== undefined && year !== null && String(year) !== "") {
    params.set("year", String(year));
  }
  const qs = params.toString();
  return api<import("@/类型/接口").IndustrySummaryData>(
    `/api/industry-summary${qs ? `?${qs}` : ""}`,
  );
}

export function getSectorTree() {
  return api<{ tree: import("@/类型/接口").SectorTreeNode[] }>("/api/sector-tree");
}

export function getDailySignals(params?: URLSearchParams) {
  const qs = params?.toString();
  return api<import("@/类型/接口").DailySignalsData>(`/api/daily-signals${qs ? `?${qs}` : ""}`);
}

export function getBatchSummary(params: URLSearchParams) {
  return api<{ rows: import("@/类型/接口").BatchRow[]; total: number; sort_by?: string; sort_order?: string }>(
    `/api/batch-summary?${params}`,
  );
}

export function getSymbolSummary(symbol: string) {
  return api<import("@/类型/接口").SymbolSummary>(`/api/symbol/${symbol}`);
}

export function getSymbolEquity(symbol: string) {
  return api<import("@/类型/接口").EquityChartData>(`/api/symbol/${symbol}/equity`);
}

export function getSymbolStates(symbol: string) {
  return api<import("@/类型/接口").StatesChartData>(`/api/symbol/${symbol}/states`);
}

export function getSymbolAgents(symbol: string) {
  return api<import("@/类型/接口").AgentsChartData>(`/api/symbol/${symbol}/agents`);
}

export function getSymbolProbs(symbol: string) {
  return api<import("@/类型/接口").ProbsChartData>(`/api/symbol/${symbol}/state-probs`);
}

export function getAgentLabels(symbol: string) {
  return api<{ rows: { agent: string; top_features: string }[] }>(`/api/symbol/${symbol}/agent-labels`);
}

export function getSectors(params: URLSearchParams) {
  return api<{ sectors: import("@/类型/接口").SectorItem[] }>(`/api/sectors?${params}`);
}

export function getRecommend(params: URLSearchParams) {
  return api<{ recommendations: import("@/类型/接口").PortfolioPick[] }>(`/api/recommend?${params}`);
}

export function buildPortfolio(body: Record<string, unknown>) {
  return api<import("@/类型/接口").PortfolioBuildResult>("/api/portfolio/build", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listPortfolios() {
  return api<{ portfolios: import("@/类型/接口").PortfolioHistoryItem[] }>("/api/portfolio/list");
}

export function getPortfolio(id: string) {
  return api<import("@/类型/接口").PortfolioBuildResult>(`/api/portfolio/${id}`);
}

export function getPortfolioEquity(id: string) {
  return api<import("@/类型/接口").EquityChartData>(`/api/portfolio/${id}/equity`);
}

export function getPortfolioDailyAnalysis(id: string) {
  return api<import("@/类型/接口").PortfolioBuildResult["daily_analysis"] & { summary?: string; holdings?: import("@/类型/接口").PortfolioPick[] }>(
    `/api/portfolio/${id}/daily-analysis`,
  );
}

export function getKline(symbols: string, limit = 90) {
  // limit=0 → 上市至今全历史
  return api<{ symbols: Record<string, import("@/类型/接口").KlineSymbolData> }>(
    `/api/kline?symbols=${encodeURIComponent(symbols)}&limit=${limit}`,
  );
}

export function getUpdateStatus() {
  return api<import("@/类型/接口").UpdateStatus>("/api/update/status");
}

export function getHoldings() {
  return api<import("@/类型/接口").HoldingsResponse>("/api/holdings");
}

export function saveHoldings(body: import("@/类型/接口").HoldingsDoc) {
  return api<import("@/类型/接口").HoldingsResponse>("/api/holdings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function notifyHoldings(dryRun = false) {
  return api<import("@/类型/接口").HoldingsNotifyResult>(`/api/holdings/notify?dry_run=${dryRun ? "true" : "false"}`, {
    method: "POST",
  });
}

export function getPaperStatus() {
  return api<import("@/类型/接口").PaperStatusData>("/api/paper");
}

export function getPaperTomorrowPicks() {
  return api<import("@/类型/接口").PaperTomorrowPicksData>("/api/paper/tomorrow-picks");
}

export function getPaperNavCompare() {
  return api<import("@/类型/接口").PaperNavCompareData>("/api/paper/nav-compare");
}

export function runPaperCycle(dryRun = false, notify?: boolean) {
  const params = new URLSearchParams({ dry_run: dryRun ? "true" : "false" });
  if (notify != null) params.set("notify", notify ? "true" : "false");
  return api<{
    trade_date: string;
    execution?: {
      sells?: number;
      buys?: number;
      trades?: unknown[];
      positions_count?: number;
      dry_run?: boolean;
      would_execute_date?: string;
    } | null;
    tomorrow_picks: import("@/类型/接口").PaperPick[];
    state: import("@/类型/接口").PaperState;
    recent_trades?: import("@/类型/接口").PaperTrade[];
    summary?: string;
    title?: string;
    config?: import("@/类型/接口").PaperStatusData["config"];
    notify?: Record<string, unknown> | null;
    dry_run: boolean;
    skipped?: boolean;
    reason?: string;
  }>(`/api/paper/run?${params}`, { method: "POST" });
}
