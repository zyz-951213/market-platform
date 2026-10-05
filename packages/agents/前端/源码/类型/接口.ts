export interface SectorTreeNode {
  name: string;
  count?: number;
  children?: SectorTreeNode[];
  symbol?: string;
}

export interface BatchRow {
  symbol: string;
  name?: string;
  sector?: string;
  sector_l1?: string;
  sector_l2?: string;
  sector_l3?: string;
  latest_state?: string;
  strategy_return?: number | null;
  benchmark_return?: number | null;
  excess_return?: number | null;
  oos_strategy_return?: number | null;
  oos_benchmark_return?: number | null;
  oos_excess_return?: number | null;
  oos_strategy_sharpe?: number | null;
  walk_forward_ratio?: number | null;
  metrics_scope?: string;
  raw_suggested_position?: number | null;
  risk_note?: string;
  risk_adjusted?: boolean;
  suggested_position?: number | null;
  trading_days?: number;
}

export interface OverviewData {
  analyzed_count?: number;
  data_symbol_count?: number;
  classification?: {
    standard?: { name?: string };
    l1_count?: number;
    l2_count?: number;
    l3_count?: number;
  };
  update?: UpdateStatus;
}

export interface UpdateStatus {
  latest_bar_date?: string;
  expected_trading_day?: string;
  stale?: number | null;
  suspended?: number | null;
  analyze_pending?: number;
  is_running?: boolean;
  scheduler_enabled?: boolean;
  last_run_at?: string;
  /** 模拟盘状态文件更新时间，后台换仓后变化，用于前端刷新 */
  paper_updated_at?: string | null;
  paper_last_trade_date?: string | null;
}

export interface DailySignalRow {
  symbol: string;
  name?: string;
  sector?: string;
  sector_l1?: string;
  sector_l2?: string;
  sector_l3?: string;
  signal_date?: string | null;
  market_state?: string;
  prev_state?: string;
  suggested_position?: number | null;
  prev_position?: number | null;
  position_delta?: number | null;
  action: string;
  oos_excess_return?: number | null;
  weight?: number;
  rebalance_action?: string;
}

export interface DailySignalsData {
  signal_date?: string | null;
  execution_note: string;
  summary: {
    total_analyzed: number;
    changed_count: number;
    add_count: number;
    reduce_count: number;
    state_change_count: number;
    hold_count: number;
  };
  changes: DailySignalRow[];
  high_conviction: DailySignalRow[];
  defensive: DailySignalRow[];
  state_changes?: DailySignalRow[];
  portfolio?: {
    portfolio_id: string;
    name: string;
    portfolio_action?: string;
    portfolio_position?: number;
    portfolio_heat?: number;
    dominant_tomorrow_state?: string;
    summary?: string;
    rebalance: DailySignalRow[];
    holdings: DailySignalRow[];
  } | null;
}

export interface IndustryRow {
  sector: string;
  n_symbols?: number;
  mean_excess_return?: number | null;
  median_excess_return?: number | null;
  median_oos_excess_return?: number | null;
  median_index_excess_return?: number | null;
  hs300_industry_return?: number | null;
  hs300_industry_n?: number | null;
  hs300_index_excess_return?: number | null;
  hs300_n_win?: number | null;
  hs300_n_lose?: number | null;
  hs300_n_flat?: number | null;
  hs300_win_rate?: number | null;
  csi1000_industry_return?: number | null;
  csi1000_industry_n?: number | null;
  csi1000_index_excess_return?: number | null;
  csi1000_n_win?: number | null;
  csi1000_n_lose?: number | null;
  csi1000_n_flat?: number | null;
  csi1000_win_rate?: number | null;
  csi2000_industry_return?: number | null;
  csi2000_industry_n?: number | null;
  csi2000_index_excess_return?: number | null;
  csi2000_n_win?: number | null;
  csi2000_n_lose?: number | null;
  csi2000_n_flat?: number | null;
  csi2000_win_rate?: number | null;
  n_win?: number | null;
  n_lose?: number | null;
  n_flat?: number | null;
  count_win_rate?: number | null;
  oos_n_win?: number | null;
  oos_n_lose?: number | null;
  oos_n_flat?: number | null;
  oos_win_rate?: number | null;
  win_rate?: number | null;
  mean_strategy_return?: number | null;
  mean_benchmark_return?: number | null;
}

export interface IndexIndustryBenchmark {
  id: string;
  name: string;
  code?: string;
  prefix?: string;
  label?: string;
  return_key?: string;
  n_key?: string;
  excess_key?: string;
  n_win_key?: string;
  n_lose_key?: string;
  n_flat_key?: string;
  win_rate_key?: string;
}

export interface IndustrySummaryData {
  rows: IndustryRow[];
  year?: number | null;
  years?: number[];
  period?: string;
  period_label?: string;
  index_name?: string;
  index_code?: string;
  weight_mode?: string;
  weight_label?: string;
  index_benchmark?: string;
  index_benchmark_label?: string;
  index_benchmarks?: IndexIndustryBenchmark[];
  sector_level?: string;
  sector_level_label?: string;
}

export interface SymbolSummary extends BatchRow {
  symbol: string;
  latest_bar_date?: string | null;
  is_suspended?: boolean;
}

export interface EquityChartData {
  dates: string[];
  strategy: number[];
  benchmark: number[];
  excess: number[];
  position: number[];
}

export interface StatesChartData {
  dates: string[];
  states: string[];
  positions: number[];
  state_values: number[];
  state_order: string[];
}

export interface AgentsChartData {
  dates: string[];
  series: Record<string, number[]>;
}

export interface ProbsChartData {
  states: string[];
  probabilities: number[];
}

export interface SectorItem {
  sector: string;
  sector_l1?: string;
  sector_l2?: string;
  n_symbols?: number;
}

export interface PortfolioPick extends BatchRow {
  selected?: boolean;
  recommend_score?: number;
  strategy_sharpe?: number;
  weight?: number;
}

export interface PortfolioBuildResult {
  portfolio_id?: string;
  name?: string;
  metrics?: Record<string, number>;
  equity_dates?: string[];
  portfolio_equity?: number[];
  benchmark_equity?: number[];
  portfolio_position?: number[];
  weights_by_sector?: Record<string, number>;
  holdings?: PortfolioPick[];
  daily_analysis?: {
    summary?: string;
    tomorrow_state_probs?: Record<string, number>;
    holdings?: PortfolioPick[];
  };
}

export interface PortfolioHistoryItem {
  portfolio_id: string;
  name?: string;
  n_holdings?: number;
  created_at?: string;
}

export interface HoldingPosition {
  symbol: string;
  name?: string;
  weight: number;
  quantity?: number | null;
  cost?: number | null;
}

export interface HoldingsDoc {
  updated_at?: string | null;
  target_source: "signals" | "portfolio";
  target_portfolio_id: string;
  cash_ratio: number;
  note: string;
  positions: HoldingPosition[];
}

export interface RebalanceRow {
  symbol: string;
  name?: string;
  sector?: string;
  current_weight: number;
  target_weight: number;
  weight_delta: number;
  weight_action: string;
  latest_state?: string;
  suggested_position?: number | null;
  signal_action?: string;
  signal_date?: string | null;
}

export interface RebalanceReport {
  updated_at?: string | null;
  target_source: string;
  target_portfolio_id: string;
  cash_ratio: number;
  execution_note: string;
  summary: string;
  stats: {
    positions_count: number;
    tracked_symbols: number;
    change_count: number;
    current_invested: number;
    target_invested: number;
  };
  rows: RebalanceRow[];
  changes: RebalanceRow[];
}

export interface HoldingsResponse {
  holdings: HoldingsDoc;
  rebalance: RebalanceReport;
}

export interface HoldingsNotifyResult {
  title: string;
  body: string;
  dry_run: boolean;
  notify_enabled: boolean;
  channels: { success: boolean; channel: string; detail: string }[];
  report: RebalanceReport;
}

export interface PaperPick {
  symbol: string;
  name?: string;
  sector?: string;
  weight?: number;
  latest_state?: string;
  suggested_position?: number | null;
  excess_return?: number | null;
  oos_excess_return?: number | null;
  strategy_sharpe?: number | null;
  recommend_score?: number | null;
}

export interface PaperPosition {
  symbol: string;
  name?: string;
  sector?: string;
  weight?: number;
  shares?: number;
  avg_cost?: number;
  last_price?: number;
  market_value?: number;
  price_date?: string;
  recommend_score?: number | null;
  latest_state?: string;
  /** target=最近换仓买入 legacy=跌停未能卖出的旧仓 */
  position_tag?: "target" | "legacy";
}

export interface PaperTrade {
  date: string;
  side: "buy" | "sell";
  symbol: string;
  name?: string;
  shares: number;
  price: number;
  amount: number;
  cost: number;
  net?: number;
}

export interface PaperState {
  version?: number;
  updated_at?: string | null;
  initial_capital: number;
  cash: number;
  nav: number;
  signal_date?: string | null;
  last_trade_date?: string | null;
  last_nav_date?: string | null;
  positions: PaperPosition[];
  pending?: {
    signal_date?: string;
    execute_date?: string;
    executed?: boolean;
    holdings?: PaperPick[];
  } | null;
  nav_curve: { date: string; nav: number; daily_return?: number }[];
  trades: PaperTrade[];
  pick_history?: { signal_date?: string; execute_date?: string; symbols?: string[]; executed?: boolean }[];
  metrics: { total_return?: number; max_drawdown?: number; trade_count?: number };
}

export interface PaperStatusData {
  config: { enabled: boolean; top_n: number; notify_after_cycle: boolean };
  state: PaperState;
  recent_trades?: PaperTrade[];
  summary: string;
  title: string;
}

export interface PaperTomorrowPicksData {
  signal_date?: string | null;
  execute_date?: string | null;
  executed?: boolean;
  picks: PaperPick[];
  execution_note: string;
}

export interface PaperNavCompareSeries {
  id: string;
  name: string;
  values: (number | null)[];
  return?: number | null;
}

export interface PaperNavCompareData {
  dates: string[];
  series: PaperNavCompareSeries[];
  returns: Record<string, number | null>;
  start_date?: string;
  end_date?: string;
}

export interface KlineSymbolData {
  dates: string[];
  bars: number[][];
}
