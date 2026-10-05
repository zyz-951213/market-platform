<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import type { DataTableColumns } from "naive-ui";
import {
  getAgentLabels,
  getBatchSummary,
  getKline,
  getSymbolAgents,
  getSymbolEquity,
  getSymbolProbs,
  getSymbolStates,
  getSymbolSummary,
} from "@/接口/客户端";
import ChartBox from "@/组件/图表容器.vue";
import { useAppStore } from "@/状态/应用";
import type { AgentsChartData, BatchRow, EquityChartData, KlineSymbolData, ProbsChartData, StatesChartData, SymbolSummary } from "@/类型/接口";
import { agentsOption, batchBarOption, equityOption, symbolKlineOption, probsOption, statesOption } from "@/工具/图表配置";
import { firstOptionValue, sectorOptions, symbolOptions as buildSymbolOptions } from "@/工具/行业级联";
import { fmtPct, fmtPctSigned, horizontalBarHeight, marketStateOrder, marketStateSolidColors, signedClass } from "@/工具/格式化";

const store = useAppStore();
/** 统一级联：全市场回测与个股选择共用，默认落到三级行业 */
const l1 = ref("");
const l2 = ref("");
const l3 = ref("");
const symbol = ref("");
const filterQ = ref("");
const summary = ref<SymbolSummary | null>(null);
const equity = ref<EquityChartData | null>(null);
const states = ref<StatesChartData | null>(null);
const agents = ref<AgentsChartData | null>(null);
const probs = ref<ProbsChartData | null>(null);
const kline = ref<KlineSymbolData | null>(null);
const agentLabels = ref<{ agent: string; top_features: string }[]>([]);
const loading = ref(false);
const detailRef = ref<HTMLElement | null>(null);

const batchRows = ref<BatchRow[]>([]);
const batchTotal = ref(0);
const batchLoading = ref(false);
const batchQ = ref("");
let batchFilterTimer: ReturnType<typeof setTimeout> | null = null;
let filterTimer: ReturnType<typeof setTimeout> | null = null;
let pickerReady = false;
let loadSeq = 0;
let syncingCascade = false;

const l1Options = computed(() => sectorOptions(store.sectorTree, []));
const l2Options = computed(() => sectorOptions(store.sectorTree, [l1.value]));
const l3Options = computed(() => sectorOptions(store.sectorTree, [l1.value, l2.value]));
const symbolOptions = computed(() => buildSymbolOptions(store.sectorTree, l1.value, l2.value, l3.value));
const batchChartOption = computed(() => batchBarOption(batchRows.value));
const batchChartHeight = computed(() => horizontalBarHeight(batchRows.value.length));
const metricsHint = computed(() => {
  const ratio = batchRows.value.find((r) => r.walk_forward_ratio != null)?.walk_forward_ratio ?? 0.7;
  const trainPct = Math.round(ratio * 100);
  return `收益指标：全样本回测；样本外为后 ${100 - trainPct}% 验证窗 · T-1 信号 · 含摩擦成本 · 仓位经风控闸门`;
});

const klineChartOption = computed(() => {
  if (!kline.value?.bars?.length) return null;
  return symbolKlineOption(kline.value, states.value);
});

const klineRemountKey = computed(() => {
  if (!kline.value?.bars?.length) return "empty";
  const last = kline.value.dates?.[kline.value.dates.length - 1] || "";
  const n = kline.value.bars.length;
  const hasStates = states.value?.dates?.length ? 1 : 0;
  return `${symbol.value}|${last}|${n}|${hasStates}`;
});

const sectorPathText = computed(() => [l1.value, l2.value, l3.value].filter(Boolean).join(" / "));

const hintText = computed(() => {
  if (symbol.value) {
    const meta = store.symbolIndex.get(symbol.value);
    const parts = [meta?.sector_l1 || l1.value, meta?.sector_l2 || l2.value, meta?.sector_l3 || l3.value].filter(Boolean);
    return `${meta?.name || symbol.value} · ${parts.join(" / ")}`;
  }
  if (l3.value) return `${sectorPathText.value} · 共 ${symbolOptions.value.length} 只，请选择或点击上方回测条`;
  if (l2.value) return `已选 ${l1.value} / ${l2.value}，请选择三级行业`;
  if (l1.value) return `已选 ${l1.value}，请选择二级行业`;
  return "中信一级 → 二级 → 三级 → 个股";
});

const sectorText = computed(() => {
  if (!summary.value) return "";
  const meta = store.symbolIndex.get(summary.value.symbol);
  const parts = [
    summary.value.sector_l1 || meta?.sector_l1 || summary.value.sector,
    summary.value.sector_l2 || meta?.sector_l2,
    summary.value.sector_l3 || meta?.sector_l3,
  ].filter(Boolean);
  return parts.length ? `行业：${parts.join(" / ")}` : "";
});

const labelColumns: DataTableColumns<{ agent: string; top_features: string }> = [
  { title: "参与者", key: "agent" },
  { title: "主导特征", key: "top_features" },
];

function normalizeSymbolKey(sym: string): string {
  return String(sym).padStart(6, "0");
}

function buildBatchParams() {
  const params = new URLSearchParams({
    limit: "50",
    offset: "0",
    sort_by: "oos_excess_return",
    sort_order: "desc",
    slim: "true",
  });
  if (l1.value) params.set("sector_l1", l1.value);
  if (l2.value) params.set("sector_l2", l2.value);
  if (l3.value) params.set("sector_l3", l3.value);
  if (batchQ.value.trim()) params.set("q", batchQ.value.trim());
  return params;
}

async function loadBatch() {
  if (!l3.value && !batchQ.value.trim()) {
    batchRows.value = [];
    batchTotal.value = 0;
    return;
  }
  batchLoading.value = true;
  try {
    const data = await getBatchSummary(buildBatchParams());
    batchRows.value = data.rows || [];
    batchTotal.value = data.total || 0;
  } finally {
    batchLoading.value = false;
  }
}

function pickKlineData(symbols: Record<string, KlineSymbolData> | undefined, sym: string): KlineSymbolData | null {
  if (!symbols) return null;
  const key = normalizeSymbolKey(sym);
  return symbols[key] || symbols[sym] || null;
}

function setCascade(l1v: string, l2v: string, l3v: string, sym: string) {
  syncingCascade = true;
  l1.value = l1v;
  l2.value = l2v;
  l3.value = l3v;
  symbol.value = sym;
  syncingCascade = false;
}

function fillDownToL3(nextL1: string, nextL2 = "", nextL3 = "") {
  const l1v = nextL1 || firstOptionValue(sectorOptions(store.sectorTree, []));
  const l2v = nextL2 || firstOptionValue(sectorOptions(store.sectorTree, [l1v]));
  const l3v = nextL3 || firstOptionValue(sectorOptions(store.sectorTree, [l1v, l2v]));
  return { l1: l1v, l2: l2v, l3: l3v };
}

async function selectSymbol(sym: string, opts?: { scroll?: boolean }) {
  if (!sym) return;
  const path = store.findSymbolPath(sym);
  if (path) {
    setCascade(path.l1, path.l2, path.l3, path.symbol);
  } else {
    symbol.value = normalizeSymbolKey(sym);
  }
  await loadSymbol(symbol.value || sym);
  if (opts?.scroll !== false) {
    await nextTick();
    detailRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function searchAndSelect(sym: string) {
  const path = store.findSymbolPath(sym);
  if (path) {
    setCascade(path.l1, path.l2, path.l3, path.symbol);
    return;
  }
  const data = await getBatchSummary(new URLSearchParams({ limit: "5", q: sym, slim: "true" }));
  const row = data.rows?.find((r) => r.symbol === sym) || data.rows?.[0];
  if (row) {
    setCascade(row.sector_l1 || row.sector || "", row.sector_l2 || row.sector || "", row.sector_l3 || "", row.symbol);
  }
}

async function loadSymbol(sym: string) {
  if (!sym) return;
  const seq = ++loadSeq;
  loading.value = true;
  kline.value = null;
  equity.value = null;
  states.value = null;
  agents.value = null;
  probs.value = null;
  summary.value = null;
  agentLabels.value = [];
  try {
    // 各图独立拉取，避免净值等单接口失败拖垮整页
    const [s, eq, st, ag, pr, lb, kl] = await Promise.all([
      getSymbolSummary(sym).catch(() => null),
      getSymbolEquity(sym).catch(() => null),
      getSymbolStates(sym).catch(() => null),
      getSymbolAgents(sym).catch(() => null),
      getSymbolProbs(sym).catch(() => null),
      getAgentLabels(sym).catch(() => ({ rows: [] })),
      getKline(sym, 0).catch(() => ({ symbols: {} })),
    ]);
    if (seq !== loadSeq) return;
    summary.value = s;
    equity.value = eq;
    states.value = st;
    agents.value = ag;
    probs.value = pr;
    agentLabels.value = lb?.rows || [];
    kline.value = pickKlineData(kl?.symbols, sym);
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

function onL1Change() {
  const filled = fillDownToL3(l1.value);
  syncingCascade = true;
  l2.value = filled.l2;
  l3.value = filled.l3;
  symbol.value = "";
  summary.value = null;
  syncingCascade = false;
  loadBatch();
}

function onL2Change() {
  const filled = fillDownToL3(l1.value, l2.value);
  syncingCascade = true;
  l3.value = filled.l3;
  symbol.value = "";
  summary.value = null;
  syncingCascade = false;
  loadBatch();
}

function onL3Change() {
  symbol.value = "";
  summary.value = null;
  loadBatch();
}

function onBatchChartClick(params: { dataIndex?: number }) {
  const idx = params?.dataIndex;
  if (idx == null || idx < 0) return;
  const row = batchRows.value[idx];
  if (row?.symbol) selectSymbol(row.symbol);
}

async function ensurePicker() {
  if (!store.sectorTree.length) await store.loadSectorTree();
  if (pickerReady) return;
  pickerReady = true;
  if (!l3.value && store.sectorTree.length) {
    const filled = fillDownToL3("");
    setCascade(filled.l1, filled.l2, filled.l3, "");
    await loadBatch();
    const firstSym = symbolOptions.value[0]?.value;
    if (firstSym) {
      symbol.value = firstSym;
      await loadSymbol(firstSym);
    }
  }
}

watch(() => store.selectedSymbol, async (sym) => {
  if (!sym) return;
  await store.loadSectorTree();
  pickerReady = true;
  filterQ.value = "";
  await selectSymbol(sym);
  store.selectedSymbol = null;
});

watch(symbol, (sym, prev) => {
  if (syncingCascade) return;
  if (sym && sym !== prev) loadSymbol(sym);
});

watch(filterQ, () => {
  if (filterTimer) clearTimeout(filterTimer);
  const q = filterQ.value.trim();
  if (q.length < 2) return;
  filterTimer = setTimeout(async () => {
    await searchAndSelect(q);
    if (symbol.value) await loadSymbol(symbol.value);
  }, 350);
});

watch(() => store.activeTab, (tab) => {
  if (tab === "symbol") ensurePicker();
});

watch(
  () => store.dataEpoch,
  (epoch) => {
    if (epoch <= 0) return;
    if (symbol.value) loadSymbol(symbol.value);
    loadBatch();
  },
);

watch(batchQ, () => {
  if (batchFilterTimer) clearTimeout(batchFilterTimer);
  batchFilterTimer = setTimeout(() => loadBatch(), 300);
});

onMounted(() => {
  if (store.activeTab === "symbol") ensurePicker();
});
</script>

<template>
  <div class="page active">
    <section class="panel">
      <div class="panel-head">
        <h2>全市场回测</h2>
        <span class="hint">
          <template v-if="l3">
            {{ sectorPathText }} · 共 {{ batchTotal }} 只 · 图表前 {{ batchRows.length }} 只
          </template>
          <template v-else>请选择三级行业</template>
          <span v-if="batchLoading"> · 加载中…</span>
        </span>
      </div>
      <p class="hint metrics-scope-hint">{{ metricsHint }}</p>
      <div class="filter-row cascade-row">
        <n-select v-model:value="l1" :options="l1Options" placeholder="中信一级" @update:value="onL1Change" />
        <n-select v-model:value="l2" :options="l2Options" placeholder="二级行业" :disabled="!l1" @update:value="onL2Change" />
        <n-select v-model:value="l3" :options="l3Options" placeholder="三级行业" :disabled="!l2" @update:value="onL3Change" />
        <n-input v-model:value="batchQ" placeholder="在行业内搜索代码/名称" clearable />
      </div>
      <ChartBox
        v-if="batchRows.length"
        :option="batchChartOption"
        class="chart chart-bar"
        :style="{ height: `${batchChartHeight}px` }"
        @chart-click="onBatchChartClick"
      />
      <p v-else-if="batchLoading" class="hint">回测列表加载中…</p>
      <p v-else-if="!l3" class="hint">请选择到三级行业，查看该窗口内股票回测</p>
      <p v-else class="hint">该三级行业暂无回测数据</p>
    </section>

    <section class="panel">
      <div class="panel-head"><h2>个股选择</h2><span class="hint">{{ hintText }}</span></div>
      <div class="filter-row cascade-row">
        <n-select
          v-model:value="symbol"
          :options="symbolOptions"
          placeholder="请选择个股"
          :disabled="!l3"
          filterable
        />
        <n-input v-model:value="filterQ" placeholder="快速搜索代码/名称" clearable />
      </div>
    </section>

    <section ref="detailRef" v-if="summary" class="panel">
      <div class="symbol-header">
        <div>
          <h2>{{ summary.symbol }}</h2>
          <p>{{ summary.name || "未知" }}</p>
          <p class="hint">{{ sectorText }}</p>
        </div>
        <div class="metric-chips">
          <div v-if="summary.is_suspended" class="metric-chip warn">
            <span>行情</span><strong>停牌</strong>
          </div>
          <div v-else-if="summary.latest_bar_date" class="metric-chip">
            <span>行情</span><strong>{{ summary.latest_bar_date }}</strong>
          </div>
          <div class="metric-chip"><span>状态</span><strong>{{ summary.latest_state || "--" }}</strong></div>
          <div class="metric-chip"><span>风控仓位</span><strong>{{ fmtPct(summary.suggested_position) }}</strong></div>
          <div
            v-if="summary.raw_suggested_position != null && summary.risk_adjusted"
            class="metric-chip"
          >
            <span>原始仓位</span><strong>{{ fmtPct(summary.raw_suggested_position) }}</strong>
          </div>
          <div v-if="summary.risk_note" class="metric-chip">
            <span>风控</span><strong>{{ summary.risk_note }}</strong>
          </div>
          <div class="metric-chip"><span>策略</span><strong :class="signedClass(summary.strategy_return)">{{ fmtPctSigned(summary.strategy_return) }}</strong></div>
          <div class="metric-chip"><span>基准</span><strong>{{ fmtPctSigned(summary.benchmark_return) }}</strong></div>
          <div class="metric-chip accent"><span>全样本超额</span><strong :class="signedClass(summary.excess_return)">{{ fmtPctSigned(summary.excess_return) }}</strong></div>
          <div v-if="summary.oos_excess_return != null" class="metric-chip accent">
            <span>样本外超额</span><strong :class="signedClass(summary.oos_excess_return)">{{ fmtPctSigned(summary.oos_excess_return) }}</strong>
          </div>
          <div class="metric-chip"><span>交易日</span><strong>{{ summary.trading_days ?? "--" }}</strong></div>
        </div>
      </div>
      <p v-if="summary.walk_forward_ratio != null" class="hint">
        全样本回测收益；样本外为后 {{ Math.round((1 - summary.walk_forward_ratio) * 100) }}% 验证窗 · T-1 信号
      </p>
      <p v-if="loading" class="hint">图表加载中…</p>
      <div class="chart-stack">
        <div v-if="kline?.bars?.length && klineChartOption" class="chart-section">
          <div class="chart-section-head">
            <h3 class="chart-section-title">日 K 线 · 状态叠加</h3>
            <div class="state-legend">
              <span
                v-for="state in marketStateOrder"
                :key="state"
                class="state-legend-item"
              >
                <i class="state-swatch" :style="{ background: marketStateSolidColors[state] }" />
                {{ state }}
              </span>
            </div>
          </div>
          <p class="hint kline-overlay-hint">背景色块为市场状态 · 下方柱为建议仓位 · T-1 信号</p>
          <ChartBox
            :option="klineChartOption"
            :remount-key="klineRemountKey"
            class="chart chart-tall chart-kline-overlay"
          />
        </div>
        <p v-else-if="!loading && symbol" class="hint">暂无日 K 线数据</p>
        <ChartBox v-if="equity" :option="equityOption(equity)" class="chart chart-tall" />
        <ChartBox v-if="states" :option="statesOption(states)" class="chart chart-tall" />
        <ChartBox v-if="agents" :option="agentsOption(agents)" class="chart chart-tall" />
        <ChartBox v-if="probs" :option="probsOption(probs)" class="chart" />
      </div>
      <n-data-table v-if="agentLabels.length" :columns="labelColumns" :data="agentLabels" :bordered="false" />
    </section>
    <section v-else-if="loading" class="panel">
      <p class="hint">个股详情加载中…</p>
    </section>
    <section v-else-if="l3 && !symbol" class="panel">
      <p class="hint">在上方回测图点击股票，或在「个股选择」中挑选，查看 K 线与回测详情</p>
    </section>
  </div>
</template>
