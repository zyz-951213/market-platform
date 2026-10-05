<script setup lang="ts">
import type { EChartsOption } from "echarts";
import { computed, h, onMounted, onUnmounted, ref, watch } from "vue";
import type { DataTableColumns } from "naive-ui";
import { useMessage } from "naive-ui";
import { getPaperNavCompare, getPaperStatus, runPaperCycle } from "@/接口/客户端";
import ChartBox from "@/组件/图表容器.vue";
import { useAppStore } from "@/状态/应用";
import type {
  PaperNavCompareData,
  PaperPick,
  PaperPosition,
  PaperState,
  PaperStatusData,
  PaperTrade,
} from "@/类型/接口";
import { fmtPct, fmtPctSigned, fmtStockName, signedClass } from "@/工具/格式化";
import { paperNavCompareOption } from "@/工具/图表配置";

const store = useAppStore();
const message = useMessage();
const loading = ref(false);
const running = ref(false);
const paperState = ref<PaperState | null>(null);
const navCompare = ref<PaperNavCompareData | null>(null);
const recentTrades = ref<PaperTrade[]>([]);
const summary = ref("");
const config = ref({ enabled: true, top_n: 20, notify_after_cycle: true });
const knownUpdatedAt = ref("");

const metricChips = computed(() => {
  const s = paperState.value;
  if (!s) return [];
  const initial = s.initial_capital || 1;
  const ret = s.metrics?.total_return ?? s.nav / initial - 1;
  return [
    { label: "模拟净值", value: s.nav.toLocaleString("zh-CN", { maximumFractionDigits: 0 }), accent: true },
    { label: "总收益", value: fmtPctSigned(ret), accent: ret > 0 },
    { label: "最大回撤", value: fmtPct(s.metrics?.max_drawdown) },
    { label: "现金", value: s.cash.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) },
    { label: "持仓数", value: String(s.positions?.length || 0) },
    { label: "成交笔数", value: String(s.metrics?.trade_count || s.trades?.length || 0) },
  ];
});

const pendingInfo = computed(() => {
  const p = paperState.value?.pending;
  if (!p?.holdings?.length) return "暂无待执行清单";
  const status = p.executed ? "已执行" : "待执行";
  return `信号日 ${p.signal_date || "--"} → 执行日 ${p.execute_date || "--"} · ${status} · ${p.holdings.length} 只`;
});

const navChartOption = computed((): EChartsOption | null => {
  const data = navCompare.value;
  if (!data?.series?.length) return null;
  return paperNavCompareOption(data);
});

const benchmarkChips = computed(() => {
  const data = navCompare.value;
  if (!data?.series?.length) return [];
  return data.series.map((s) => ({
    label: s.name,
    value: s.return == null ? "--" : fmtPctSigned(s.return / 100),
    accent: s.id === "paper",
    signed: s.return,
  }));
});

const tableEpoch = computed(() => knownUpdatedAt.value || paperState.value?.last_trade_date || "0");

const pickColumns: DataTableColumns<PaperPick> = [
  { title: "#", key: "rank", width: 44, render: (_r, i) => i + 1 },
  { title: "代码", key: "symbol", width: 88 },
  { title: "名称", key: "name", ellipsis: { tooltip: true }, render: (r) => fmtStockName(r, store.symbolIndex) },
  { title: "评分", key: "recommend_score", width: 72, render: (r) => (r.recommend_score ?? 0).toFixed(3) },
  { title: "状态", key: "latest_state", width: 88 },
  { title: "建议仓位", key: "suggested_position", width: 88, render: (r) => fmtPct(r.suggested_position) },
  { title: "目标权重", key: "weight", width: 88, render: (r) => fmtPct(r.weight) },
  {
    title: "样本外超额",
    key: "oos_excess_return",
    width: 96,
    render: (r) =>
      h(
        "span",
        { class: signedClass(r.oos_excess_return ?? r.excess_return) },
        fmtPctSigned(r.oos_excess_return ?? r.excess_return),
      ),
  },
];

const positionColumns: DataTableColumns<PaperPosition> = [
  { title: "代码", key: "symbol", width: 88 },
  { title: "名称", key: "name", ellipsis: { tooltip: true }, render: (r) => fmtStockName(r, store.symbolIndex) },
  {
    title: "类型",
    key: "position_tag",
    width: 64,
    render: (r) => (r.position_tag === "legacy" ? "遗留" : "目标"),
  },
  { title: "股数", key: "shares", width: 80, render: (r) => (r.shares ?? 0).toLocaleString() },
  { title: "成本", key: "avg_cost", width: 72, render: (r) => (r.avg_cost ?? 0).toFixed(2) },
  { title: "现价", key: "last_price", width: 72, render: (r) => (r.last_price ?? 0).toFixed(2) },
  {
    title: "市值",
    key: "market_value",
    width: 88,
    render: (r) => (r.market_value ?? (r.shares ?? 0) * (r.last_price ?? 0)).toLocaleString("zh-CN", { maximumFractionDigits: 0 }),
  },
  { title: "权重", key: "weight", width: 72, render: (r) => fmtPct(r.weight) },
  { title: "状态", key: "latest_state", width: 88 },
];

const tradeColumns: DataTableColumns<PaperTrade> = [
  { title: "日期", key: "date", width: 100 },
  {
    title: "方向",
    key: "side",
    width: 64,
    render: (r) =>
      h("span", { class: r.side === "buy" ? "signal-action-add" : "signal-action-reduce" }, r.side === "buy" ? "买入" : "卖出"),
  },
  { title: "代码", key: "symbol", width: 88 },
  { title: "名称", key: "name", ellipsis: { tooltip: true }, render: (r) => fmtStockName(r, store.symbolIndex) },
  { title: "股数", key: "shares", width: 80, render: (r) => r.shares.toLocaleString() },
  { title: "价格", key: "price", width: 72, render: (r) => r.price.toFixed(2) },
  { title: "金额", key: "amount", width: 96, render: (r) => r.amount.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) },
];

const tomorrowPicks = computed(() => paperState.value?.pending?.holdings || []);

const ALL_TRADE_DATES = "__all__";
const selectedTradeDate = ref<string>(ALL_TRADE_DATES);

const tradeDateOptions = computed(() => {
  const counts = new Map<string, number>();
  for (const t of recentTrades.value) {
    const d = t.date || "";
    if (!d) continue;
    counts.set(d, (counts.get(d) || 0) + 1);
  }
  const dates = [...counts.keys()].sort((a, b) => b.localeCompare(a));
  return [
    { label: "全部日期", value: ALL_TRADE_DATES },
    ...dates.map((d) => ({ label: `${d}（${counts.get(d)} 笔）`, value: d })),
  ];
});

const filteredTrades = computed(() => {
  if (selectedTradeDate.value === ALL_TRADE_DATES) return recentTrades.value;
  return recentTrades.value.filter((t) => t.date === selectedTradeDate.value);
});

const tradeColumnsDisplay = computed(() => {
  if (selectedTradeDate.value === ALL_TRADE_DATES) return tradeColumns;
  return tradeColumns.filter((c) => c.key !== "date");
});

const tradeFilterHint = computed(() => {
  const n = filteredTrades.value.length;
  if (!n) return "暂无成交";
  if (selectedTradeDate.value === ALL_TRADE_DATES) {
    return `共 ${recentTrades.value.length} 笔 · ${tradeDateOptions.value.length - 1} 个换仓日`;
  }
  const sells = filteredTrades.value.filter((t) => t.side === "sell").length;
  const buys = filteredTrades.value.filter((t) => t.side === "buy").length;
  return `${selectedTradeDate.value} · ${n} 笔（卖 ${sells} / 买 ${buys}）`;
});

function syncTradeDateDefault(forceLatest = false) {
  const dates = tradeDateOptions.value.filter((o) => o.value !== ALL_TRADE_DATES).map((o) => o.value);
  if (!dates.length) {
    selectedTradeDate.value = ALL_TRADE_DATES;
    return;
  }
  const current = selectedTradeDate.value;
  if (!forceLatest && current === ALL_TRADE_DATES) return;
  if (!forceLatest && current && dates.includes(current)) return;
  const last = paperState.value?.last_trade_date;
  selectedTradeDate.value = last && dates.includes(last) ? last : dates[0];
}

const tradeDayHint = computed(() => {
  const last = paperState.value?.last_trade_date;
  const pending = paperState.value?.pending;
  if (!last) return "暂无成交";
  if (pending && !pending.executed && pending.execute_date) {
    return `仅有换仓日产生成交 · 上次 ${last} · 待执行 ${pending.execute_date}`;
  }
  return `仅有换仓日产生成交 · 上次 ${last}`;
});

function applyPaperStatus(
  res: Partial<PaperStatusData> & { state?: PaperState; recent_trades?: PaperTrade[] },
  opts: { forceTradeDate?: boolean } = {},
) {
  const prevTradeDate = paperState.value?.last_trade_date || "";
  if (res.state) paperState.value = res.state;
  recentTrades.value = res.recent_trades || res.state?.trades || [];
  if (res.summary != null) summary.value = res.summary;
  if (res.config) config.value = res.config;
  const nextUpdated = res.state?.updated_at || "";
  if (nextUpdated) knownUpdatedAt.value = nextUpdated;
  const tradeDateChanged = !!res.state?.last_trade_date && res.state.last_trade_date !== prevTradeDate;
  syncTradeDateDefault(!!opts.forceTradeDate || tradeDateChanged);
}

async function load(opts: { forceTradeDate?: boolean } = {}) {
  loading.value = true;
  try {
    const [res, compare] = await Promise.all([getPaperStatus(), getPaperNavCompare()]);
    applyPaperStatus(res, { forceTradeDate: opts.forceTradeDate ?? !knownUpdatedAt.value });
    navCompare.value = compare;
  } catch (e) {
    message.error((e as Error).message);
  } finally {
    loading.value = false;
  }
}

async function onRun(dryRun: boolean) {
  running.value = true;
  try {
    const res = await runPaperCycle(dryRun, dryRun ? false : undefined);
    if (res.skipped) {
      message.warning(res.reason || "模拟盘已跳过");
      return;
    }
    applyPaperStatus(res, { forceTradeDate: true });
    const exec = res.execution;
    const pickN = res.tomorrow_picks?.length || paperState.value?.pending?.holdings?.length || 0;
    if (dryRun) {
      if (exec?.would_execute_date) {
        message.info(`预览：将于 ${exec.would_execute_date} 换仓（未保存）`);
      } else {
        message.info(`预览完成 · 待执行 ${pickN} 只（未保存）`);
      }
      return;
    }
    if (exec && exec.sells != null) {
      message.success(
        `换仓完成：卖 ${exec.sells} / 买 ${exec.buys} · 持仓 ${exec.positions_count ?? paperState.value?.positions?.length ?? 0}`,
      );
    } else {
      message.success(`已刷新净值与清单 · 待执行 Top${pickN}（T+1 换仓日才会新增成交）`);
    }
    navCompare.value = await getPaperNavCompare().catch(() => navCompare.value);
  } catch (e) {
    message.error((e as Error).message);
  } finally {
    running.value = false;
  }
}

function onPickSymbol(symbol: string) {
  if (symbol) store.goToSymbol(symbol);
}

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function pollPaperIfChanged() {
  if (loading.value || running.value || store.activeTab !== "paper") return;
  try {
    const res = await getPaperStatus();
    const ts = res.state?.updated_at || "";
    if (!ts || ts === knownUpdatedAt.value) return;
    applyPaperStatus(res, { forceTradeDate: true });
    navCompare.value = await getPaperNavCompare().catch(() => navCompare.value);
  } catch {
    /* ignore */
  }
}

function startPaperPoll() {
  stopPaperPoll();
  pollTimer = setInterval(pollPaperIfChanged, 10000);
}

function stopPaperPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

watch(
  () => store.activeTab,
  (tab) => {
    if (tab === "paper") {
      load({ forceTradeDate: !knownUpdatedAt.value });
      startPaperPoll();
    } else {
      stopPaperPoll();
    }
  },
);

watch(
  () => store.dataEpoch,
  (epoch) => {
    if (epoch <= 0) return;
    if (store.activeTab === "paper") load({ forceTradeDate: true });
  },
);

onMounted(() => {
  if (store.activeTab === "paper") {
    load({ forceTradeDate: true });
    startPaperPoll();
  }
});

onUnmounted(() => stopPaperPoll());
</script>

<template>
  <div class="page active">
    <section class="panel">
      <div class="panel-head">
        <h2>自动模拟</h2>
        <span class="hint">
          每日综合评分 Top {{ config.top_n }} · T 日收盘信号 → T+1 全换仓 · 自动更新后触发
          <span v-if="loading"> · 加载中…</span>
          <span v-else-if="running"> · 运行中…</span>
        </span>
      </div>

      <div v-if="metricChips.length" class="metric-chips">
        <div v-for="chip in metricChips" :key="chip.label" class="metric-chip" :class="{ accent: chip.accent }">
          <span>{{ chip.label }}</span><strong>{{ chip.value }}</strong>
        </div>
      </div>

      <p class="hint paper-pending-hint">{{ pendingInfo }}</p>
      <pre v-if="summary" class="holdings-summary">{{ summary }}</pre>

      <div class="holdings-toolbar">
        <span class="hint">数据更新完成后自动运行；也可手动触发</span>
        <div class="holdings-actions">
          <button type="button" class="btn btn-secondary" :disabled="running" @click="onRun(true)">预览周期</button>
          <button type="button" class="btn btn-primary" :disabled="running" @click="onRun(false)">立即运行</button>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>明日买入 Top {{ config.top_n }}</h2>
        <span class="hint">综合评分 = 40% 样本外超额 + 30% 绝对收益 + 20% 夏普 + 10% 状态热度 · 缓冲降换手 · 排除恐慌态</span>
      </div>
      <n-data-table
        v-if="tomorrowPicks.length"
        :key="`picks-${tableEpoch}`"
        :columns="pickColumns"
        :data="tomorrowPicks"
        :row-props="(row: PaperPick) => ({ style: 'cursor:pointer', onClick: () => onPickSymbol(row.symbol) })"
        :bordered="false"
      />
      <p v-else class="hint">运行周期后生成明日买入清单</p>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>累计收益对比</h2>
        <span v-if="navCompare?.start_date" class="hint">
          {{ navCompare.start_date }} → {{ navCompare.end_date }} · 起点归 0%，可与常用指数对照
        </span>
      </div>
      <ChartBox v-if="navChartOption" :option="navChartOption" class="chart chart-tall" />
      <p v-else class="hint">暂无净值数据，首次换仓执行后开始记录</p>
      <div v-if="benchmarkChips.length" class="metric-chips paper-benchmark-row">
        <div
          v-for="chip in benchmarkChips"
          :key="chip.label"
          class="metric-chip"
          :class="{ accent: chip.accent }"
        >
          <span>{{ chip.label }}</span>
          <strong :class="signedClass(chip.signed)">{{ chip.value }}</strong>
        </div>
      </div>
    </section>

    <div class="chart-grid-2">
      <section class="panel">
        <div class="panel-head">
          <h2>当前模拟持仓</h2>
          <span class="hint">
            上次换仓 {{ paperState?.last_trade_date || "--" }}
            · 目标 {{ paperState?.positions?.filter((p) => p.position_tag !== "legacy").length ?? 0 }} 只
            · 遗留 {{ paperState?.positions?.filter((p) => p.position_tag === "legacy").length ?? 0 }} 只
          </span>
        </div>
        <n-data-table
          v-if="paperState?.positions?.length"
          :key="`pos-${tableEpoch}-${paperState.positions.length}`"
          :columns="positionColumns"
          :data="paperState.positions"
          :bordered="false"
        />
        <p v-else class="hint">空仓，待 T+1 执行买入</p>
      </section>

      <section class="panel">
        <div class="panel-head paper-trade-head">
          <div class="paper-trade-head-text">
            <h2>最近成交</h2>
            <span class="hint">{{ tradeFilterHint }}</span>
          </div>
          <n-select
            v-model:value="selectedTradeDate"
            :options="tradeDateOptions"
            class="paper-trade-date-select"
            placeholder="选择成交日"
            :disabled="!tradeDateOptions.length"
          />
        </div>
        <n-data-table
          v-if="filteredTrades.length"
          :key="`trades-${tableEpoch}-${selectedTradeDate}-${filteredTrades.length}`"
          :columns="tradeColumnsDisplay"
          :data="filteredTrades"
          :bordered="false"
        />
        <p v-else class="hint">暂无成交记录</p>
        <p v-if="tradeDayHint" class="hint paper-trade-foot-hint">{{ tradeDayHint }}</p>
      </section>
    </div>

    <section class="panel holdings-notify-hint">
      <h3>自动推送配置</h3>
      <p class="hint">
        在 <code>默认.yaml</code> 设置 <code>notify.enabled: true</code> 与 <code>notify.webhook_url</code>，
        模拟周期结束后自动推送 Top20 清单与净值摘要。CLI：<code>market-agents paper</code>。
        此为模拟交易，不连接券商，不构成投资建议。
      </p>
    </section>
  </div>
</template>
