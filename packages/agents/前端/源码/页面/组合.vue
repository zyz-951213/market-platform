<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import type { DataTableColumns } from "naive-ui";
import { NCheckbox } from "naive-ui";
import {
  buildPortfolio,
  getBatchSummary,
  getKline,
  getPortfolio,
  getPortfolioDailyAnalysis,
  getPortfolioEquity,
  getRecommend,
  getSectors,
  listPortfolios,
} from "@/接口/客户端";
import ChartBox from "@/组件/图表容器.vue";
import { useAppStore } from "@/状态/应用";
import type {
  KlineSymbolData,
  PortfolioBuildResult,
  PortfolioHistoryItem,
  PortfolioPick,
  SectorItem,
} from "@/类型/接口";
import {
  heatOption,
  klineOption,
  portfolioEquityOption,
  probsFromMap,
  sectorPieOption,
} from "@/工具/图表配置";
import { fmtPct, fmtPctSigned, fmtStockName, horizontalBarHeight } from "@/工具/格式化";
import { sectorOptions, singleOptionValue } from "@/工具/行业级联";

const props = defineProps<{ history: PortfolioHistoryItem[] }>();
const emit = defineEmits<{ historyChange: [PortfolioHistoryItem[]] }>();

const store = useAppStore();
const mode = ref<"sector" | "custom">("sector");
const portfolioL1 = ref("");
const portfolioL2 = ref("");
const sectorsCache = ref<SectorItem[]>([]);
const selectedL3 = ref<SectorItem[]>([]);
const customSymbols = ref<PortfolioPick[]>([]);
const picks = ref<PortfolioPick[]>([]);
const searchQ = ref("");
const searchRows = ref<PortfolioPick[]>([]);
const showSuggest = ref(false);
const picksPerSector = ref(3);
const sortBy = ref("recommend_score");
const weightMode = ref("equal");
const result = ref<PortfolioBuildResult | null>(null);
const equityData = ref<import("@/类型/接口").EquityChartData | null>(null);
const dailySummary = ref("");
const dailyProbs = ref<Record<string, number>>({});
const dailyHoldings = ref<PortfolioPick[]>([]);
const heatChartHeight = computed(() => horizontalBarHeight(dailyHoldings.value.length, 280, 26, 64, 480));
const klineMap = ref<Record<string, KlineSymbolData>>({});
const building = ref(false);
const checkedSectors = ref<string[]>([]);
let searchTimer: ReturnType<typeof setTimeout> | null = null;

const l1Options = computed(() => sectorOptions(store.sectorTree, []));
const l2Options = computed(() => sectorOptions(store.sectorTree, [portfolioL1.value]));

const resultTitle = computed(() => result.value?.name || "策略分析");
const metrics = computed(() => result.value?.metrics || {});

const metricChips = computed(() => {
  const m = metrics.value;
  return [
    { label: "组合收益", value: fmtPctSigned(m.portfolio_total_return), accent: true },
    { label: "基准收益", value: fmtPctSigned(m.benchmark_total_return) },
    { label: "超额收益", value: fmtPctSigned(m.excess_return), accent: true },
    { label: "组合夏普", value: m.portfolio_sharpe?.toFixed(2) ?? "--" },
    { label: "最大回撤", value: fmtPct(m.portfolio_max_drawdown) },
    { label: "加权仓位", value: fmtPct(m.avg_position) },
  ];
});

const pickColumns: DataTableColumns<PortfolioPick> = [
  {
    title: "选",
    key: "selected",
    width: 50,
    render: (row, index) =>
      h(NCheckbox, {
        checked: row.selected !== false,
        onUpdateChecked: (v: boolean) => { picks.value[index].selected = v; },
      }),
  },
  { title: "代码", key: "symbol" },
  { title: "名称", key: "name", render: (r) => r.name || "--" },
  { title: "三级行业", key: "sector", render: (r) => r.sector || "--" },
  { title: "状态", key: "latest_state", render: (r) => r.latest_state || "--" },
  { title: "仓位", key: "suggested_position", render: (r) => fmtPct(r.suggested_position) },
  { title: "超额", key: "excess_return", render: (r) => fmtPctSigned(r.excess_return) },
  { title: "评分", key: "recommend_score", render: (r) => (r.recommend_score || 0).toFixed(3) },
];

const historyColumns: DataTableColumns<PortfolioHistoryItem> = [
  { title: "名称", key: "name" },
  { title: "ID", key: "portfolio_id" },
  { title: "持仓数", key: "n_holdings" },
  { title: "创建时间", key: "created_at" },
];

function addCheckedSectors() {
  if (!checkedSectors.value.length) {
    alert("请先勾选要添加的三级行业");
    return;
  }
  addSectors(checkedSectors.value);
  checkedSectors.value = [];
}

const addedSectorSet = computed(() => new Set(selectedL3.value.map((s) => s.sector)));

async function loadSectors() {
  if (!portfolioL1.value || !portfolioL2.value) {
    sectorsCache.value = [];
    return;
  }
  const params = new URLSearchParams({ sector_l1: portfolioL1.value, sector_l2: portfolioL2.value });
  const data = await getSectors(params);
  sectorsCache.value = data.sectors || [];
}

function addSectors(checked: string[]) {
  const added = new Set(selectedL3.value.map((s) => s.sector));
  for (const name of checked) {
    if (added.has(name)) continue;
    const meta = sectorsCache.value.find((s) => s.sector === name) || { sector: name };
    selectedL3.value.push({
      sector: meta.sector,
      sector_l1: meta.sector_l1 || portfolioL1.value,
      sector_l2: meta.sector_l2 || portfolioL2.value,
      n_symbols: meta.n_symbols,
    });
    added.add(name);
  }
}

function removeL3(sector: string) {
  selectedL3.value = selectedL3.value.filter((s) => s.sector !== sector);
}

function addCustom(row: PortfolioPick) {
  if (!row?.symbol) return;
  const sym = String(row.symbol).padStart(6, "0");
  if (customSymbols.value.some((s) => s.symbol === sym)) {
    alert("该股票已添加");
    return;
  }
  customSymbols.value.push({
    symbol: sym,
    name: row.name || "",
    sector: row.sector_l3 || row.sector || "",
    sector_l1: row.sector_l1 || "",
    sector_l2: row.sector_l2 || "",
  });
  searchQ.value = "";
  searchRows.value = [];
  showSuggest.value = false;
}

async function searchSymbols(q: string) {
  const data = await getBatchSummary(new URLSearchParams({ limit: "10", q, slim: "true" }));
  searchRows.value = data.rows || [];
  showSuggest.value = searchRows.value.length > 0;
}

async function addFromInput() {
  const q = searchQ.value.trim();
  if (!q) {
    alert("请输入股票代码或名称");
    return;
  }
  if (searchRows.value.length === 1) {
    addCustom(searchRows.value[0]);
    return;
  }
  const exact = searchRows.value.find((r) => r.symbol === q.padStart(6, "0"));
  if (exact) {
    addCustom(exact);
    return;
  }
  const data = await getBatchSummary(new URLSearchParams({ limit: "8", q, slim: "true" }));
  const rows = data.rows || [];
  if (!rows.length) {
    alert("未找到该股票或无回测数据");
    return;
  }
  if (rows.length === 1 || /^\d{6}$/.test(q)) {
    const row = rows.find((r) => r.symbol === q.padStart(6, "0")) || rows[0];
    addCustom(row);
    return;
  }
  searchRows.value = rows;
  showSuggest.value = true;
}

async function loadPickKlines() {
  if (!picks.value.length) {
    klineMap.value = {};
    return;
  }
  const symbols = picks.value.map((p) => p.symbol).join(",");
  try {
    const data = await getKline(symbols, 90);
    klineMap.value = data.symbols || {};
  } catch {
    klineMap.value = {};
  }
}

async function previewPicks() {
  if (mode.value === "custom") {
    const symbols = customSymbols.value.map((s) => s.symbol);
    if (!symbols.length) {
      alert("请至少添加一只股票");
      return;
    }
    const data = await getRecommend(new URLSearchParams({
      symbols: symbols.join(","),
      sort_by: sortBy.value,
    }));
    picks.value = (data.recommendations || []).map((p) => ({ ...p, selected: true }));
  } else {
    const sectors = selectedL3.value.map((s) => s.sector);
    if (!sectors.length) {
      alert("请至少添加一个三级行业");
      return;
    }
    const data = await getRecommend(new URLSearchParams({
      sectors: sectors.join(","),
      picks_per_sector: String(picksPerSector.value),
      sort_by: sortBy.value,
    }));
    picks.value = (data.recommendations || []).map((p) => ({ ...p, selected: true }));
  }
  await loadPickKlines();
}

function normalizeEquity(res: PortfolioBuildResult): import("@/类型/接口").EquityChartData | null {
  const dates = res.equity_dates;
  const pe = res.portfolio_equity;
  const be = res.benchmark_equity;
  if (!dates?.length || !pe?.length || !be?.length) return null;
  const p0 = pe[0];
  const b0 = be[0];
  return {
    dates,
    strategy: pe.map((v) => v / p0),
    benchmark: be.map((v) => v / b0),
    excess: pe.map((v, i) => (v / p0) / (be[i] / b0)),
    position: res.portfolio_position || [],
  };
}

function showResult(res: PortfolioBuildResult, equity?: import("@/类型/接口").EquityChartData, daily?: { summary?: string; tomorrow_state_probs?: Record<string, number>; holdings?: PortfolioPick[] }) {
  result.value = res;
  equityData.value = equity || normalizeEquity(res);
  dailySummary.value = daily?.summary || res.daily_analysis?.summary || "";
  dailyProbs.value = daily?.tomorrow_state_probs || res.daily_analysis?.tomorrow_state_probs || {};
  dailyHoldings.value = daily?.holdings || res.daily_analysis?.holdings || res.holdings || [];
}

async function onBuild() {
  building.value = true;
  try {
    const selectedSymbols = picks.value.filter((p) => p.selected).map((p) => p.symbol);
    const custom = customSymbols.value.map((s) => s.symbol);
    let body: Record<string, unknown>;
    if (selectedSymbols.length) {
      body = { sectors: [], symbols: selectedSymbols, picks_per_sector: picksPerSector.value, weight_mode: weightMode.value, sort_by: sortBy.value };
    } else if (mode.value === "custom") {
      if (!custom.length) {
        alert("请至少添加一只股票");
        return;
      }
      body = { sectors: [], symbols: custom, picks_per_sector: picksPerSector.value, weight_mode: weightMode.value, sort_by: sortBy.value };
    } else {
      const sectors = selectedL3.value.map((s) => s.sector);
      if (!sectors.length) {
        alert("请至少添加一个三级行业");
        return;
      }
      body = { sectors, symbols: [], picks_per_sector: picksPerSector.value, weight_mode: weightMode.value, sort_by: sortBy.value };
    }
    const res = await buildPortfolio(body);
    showResult(res);
    const history = await listPortfolios();
    emit("historyChange", history.portfolios || []);
  } catch (e) {
    alert((e as Error).message);
  } finally {
    building.value = false;
  }
}

async function loadHistory(id: string) {
  const [detail, equity, daily] = await Promise.all([
    getPortfolio(id),
    getPortfolioEquity(id),
    getPortfolioDailyAnalysis(id),
  ]);
  picks.value = (detail.holdings || []).map((h) => ({ ...h, selected: true }));
  await loadPickKlines();
  showResult(detail, equity, daily);
}

watch(searchQ, () => {
  if (searchTimer) clearTimeout(searchTimer);
  const q = searchQ.value.trim();
  if (q.length < 2) {
    searchRows.value = [];
    showSuggest.value = false;
    return;
  }
  searchTimer = setTimeout(() => searchSymbols(q).catch(console.error), 300);
});

watch([portfolioL1], () => {
  const l2 = sectorOptions(store.sectorTree, [portfolioL1.value]);
  portfolioL2.value = singleOptionValue(l2);
  sectorsCache.value = [];
  loadSectors();
});

watch([portfolioL2], () => loadSectors());

onMounted(() => loadSectors());
</script>

<template>
  <div class="page active">
    <section class="panel">
      <div class="panel-head">
        <h2>策略构建</h2>
        <span class="hint">
          {{ mode === "custom" ? "搜索并添加股票，预览持仓后构建策略" : "一级 → 二级 → 勾选三级 → 添加 → 预览推荐 → 构建策略" }}
        </span>
      </div>
      <div class="mode-tabs">
        <button class="mode-tab" :class="{ active: mode === 'sector' }" @click="mode = 'sector'">按行业</button>
        <button class="mode-tab" :class="{ active: mode === 'custom' }" @click="mode = 'custom'">自定义</button>
      </div>

      <div v-show="mode === 'sector'" id="portfolio-sector-panel">
        <div class="filter-row">
          <n-select v-model:value="portfolioL1" :options="l1Options" placeholder="请选择一级行业" />
          <n-select v-model:value="portfolioL2" :options="l2Options" placeholder="请选择二级行业" :disabled="!portfolioL1" />
        </div>
        <div v-if="portfolioL1 && portfolioL2" class="sector-checkboxes">
          <p v-if="!sectorsCache.length" class="hint">该二级行业下暂无已分析标的</p>
          <template v-else>
            <n-checkbox-group v-model:value="checkedSectors">
              <label v-for="s in sectorsCache" :key="s.sector" class="sector-check" :class="{ 'is-added': addedSectorSet.has(s.sector) }">
                <n-checkbox :value="s.sector" :disabled="addedSectorSet.has(s.sector)" />
                <span>{{ s.sector }}</span>
                <span class="hint">{{ addedSectorSet.has(s.sector) ? "已添加" : `${s.n_symbols || "?"}只` }}</span>
              </label>
            </n-checkbox-group>
            <button class="btn btn-secondary" @click="addCheckedSectors">添加选中</button>
          </template>
        </div>
        <p v-else class="hint">请先选择中信一级和二级行业</p>
        <div class="selected-l3-wrap">
          <span class="hint">已选 {{ selectedL3.length }} 个</span>
          <div class="selected-l3-list">
            <span v-if="!selectedL3.length" class="hint">从上方勾选三级行业后点击「添加选中」</span>
            <span v-for="s in selectedL3" :key="s.sector" class="selected-l3-chip">
              <span class="chip-main">
                <strong>{{ s.sector }}</strong>
                <span class="chip-meta">{{ [s.sector_l1, s.sector_l2].filter(Boolean).join(" / ") }} · {{ s.n_symbols ?? "?" }}只</span>
              </span>
              <button type="button" class="chip-remove" @click="removeL3(s.sector)">×</button>
            </span>
          </div>
        </div>
      </div>

      <div v-show="mode === 'custom'" id="portfolio-custom-panel">
        <div class="symbol-search-wrap">
          <n-input v-model:value="searchQ" placeholder="搜索股票代码或名称" @keydown.enter="addFromInput" />
          <button class="btn btn-secondary" @click="addFromInput">添加</button>
          <div v-if="showSuggest" class="symbol-suggest">
            <button v-for="(r, i) in searchRows" :key="i" type="button" class="suggest-item" @click="addCustom(r)">
              <strong>{{ r.symbol }}</strong>
              <span>{{ r.name }}</span>
              <span class="hint">{{ r.sector_l3 || r.sector }}</span>
            </button>
          </div>
        </div>
        <span class="hint">{{ customSymbols.length }} 只</span>
        <div class="selected-l3-list">
          <p v-if="!customSymbols.length" class="hint">搜索股票并添加，可跨行业自由组合</p>
          <span v-for="s in customSymbols" :key="s.symbol" class="selected-l3-chip">
            <span class="chip-main">
              <strong>{{ fmtStockName(s, store.symbolIndex) }}</strong>
              <span class="chip-meta">{{ s.symbol }} · {{ s.sector || "--" }}</span>
            </span>
            <button type="button" class="chip-remove" @click="customSymbols = customSymbols.filter((x) => x.symbol !== s.symbol)">×</button>
          </span>
        </div>
      </div>

      <div class="filter-row build-controls">
        <n-input-number v-model:value="picksPerSector" :min="1" :max="20" />
        <n-select v-model:value="sortBy" :options="[
          { label: '推荐评分', value: 'recommend_score' },
          { label: '超额收益', value: 'excess_return' },
          { label: '策略夏普', value: 'strategy_sharpe' },
        ]" />
        <n-select v-model:value="weightMode" :options="[
          { label: '等权', value: 'equal' },
          { label: '按仓位', value: 'position' },
        ]" />
        <button class="btn btn-secondary" @click="previewPicks">{{ mode === "custom" ? "预览持仓" : "预览推荐" }}</button>
        <button class="btn btn-primary" :disabled="building" @click="onBuild">构建策略</button>
      </div>
    </section>

    <section v-if="picks.length" class="panel">
      <div class="panel-head">
        <h2>推荐持仓</h2>
        <span class="hint">{{ picks.length }} 只</span>
      </div>
      <n-data-table :columns="pickColumns" :data="picks" :bordered="false" />
      <div class="portfolio-picks-charts">
        <div v-for="p in picks" :key="p.symbol" class="pick-kline-card">
          <div class="pick-kline-head">
            <strong>{{ fmtStockName(p, store.symbolIndex) }}</strong>
            <span class="hint">{{ p.symbol }} · {{ p.sector || "--" }} · {{ p.latest_state || "--" }}</span>
          </div>
          <ChartBox
            v-if="klineMap[String(p.symbol).padStart(6, '0')]?.bars?.length"
            :option="klineOption(klineMap[String(p.symbol).padStart(6, '0')])"
            class="pick-kline-chart"
          />
          <p v-else class="hint">暂无 K 线数据</p>
        </div>
      </div>
    </section>

    <section v-if="result" id="portfolio-result-section" class="panel">
      <div class="panel-head">
        <h2>{{ resultTitle }}</h2>
      </div>
      <div class="metric-chips">
        <div v-for="c in metricChips" :key="c.label" class="metric-chip" :class="{ accent: c.accent }">
          <span>{{ c.label }}</span><strong>{{ c.value }}</strong>
        </div>
      </div>
      <p id="portfolio-daily-summary">{{ dailySummary }}</p>
      <div id="portfolio-charts" class="chart-grid-2">
        <ChartBox v-if="equityData" :option="portfolioEquityOption(equityData)" class="chart chart-tall" />
        <ChartBox v-if="result.weights_by_sector" :option="sectorPieOption(result.weights_by_sector)" class="chart" />
        <ChartBox v-if="Object.keys(dailyProbs).length" :option="probsFromMap(dailyProbs)" class="chart" />
        <ChartBox
          v-if="dailyHoldings.length"
          :option="heatOption(dailyHoldings)"
          class="chart"
          :style="{ height: `${heatChartHeight}px` }"
        />
      </div>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>历史策略</h2></div>
      <n-data-table
        v-if="history.length"
        :columns="historyColumns"
        :data="history"
        :row-props="(row: PortfolioHistoryItem) => ({ style: 'cursor:pointer', onClick: () => loadHistory(row.portfolio_id) })"
        :bordered="false"
      />
      <p v-else class="hint">暂无历史策略</p>
    </section>
  </div>
</template>
