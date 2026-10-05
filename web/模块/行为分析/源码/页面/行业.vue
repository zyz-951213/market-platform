<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import type { DataTableColumns, DataTableSortState, PaginationProps } from "naive-ui";
import { useMessage } from "naive-ui";
import ChartBox from "@/组件/图表容器.vue";
import { getIndustrySummary } from "@/接口/客户端";
import { useAppStore } from "@/状态/应用";
import type { IndexIndustryBenchmark, IndustryRow } from "@/类型/接口";
import { industryExcessOption, industryWinOption } from "@/工具/图表配置";
import { fmtPct } from "@/工具/格式化";

type ExcessMode = "median" | "oos" | "index";
type NumericKey = keyof IndustryRow;
type PeriodKey = number | "all";

const DEFAULT_BENCHES: IndexIndustryBenchmark[] = [
  {
    id: "hs300",
    name: "沪深300",
    label: "沪深300同行业（成分市值加权满仓）",
    return_key: "hs300_industry_return",
    n_key: "hs300_industry_n",
    excess_key: "hs300_index_excess_return",
    n_win_key: "hs300_n_win",
    n_lose_key: "hs300_n_lose",
    n_flat_key: "hs300_n_flat",
    win_rate_key: "hs300_win_rate",
  },
  {
    id: "csi1000",
    name: "中证1000",
    label: "中证1000同行业（成分市值加权满仓）",
    return_key: "csi1000_industry_return",
    n_key: "csi1000_industry_n",
    excess_key: "csi1000_index_excess_return",
    n_win_key: "csi1000_n_win",
    n_lose_key: "csi1000_n_lose",
    n_flat_key: "csi1000_n_flat",
    win_rate_key: "csi1000_win_rate",
  },
  {
    id: "csi2000",
    name: "中证2000",
    label: "中证2000同行业（成分市值加权满仓）",
    return_key: "csi2000_industry_return",
    n_key: "csi2000_industry_n",
    excess_key: "csi2000_index_excess_return",
    n_win_key: "csi2000_n_win",
    n_lose_key: "csi2000_n_lose",
    n_flat_key: "csi2000_n_flat",
    win_rate_key: "csi2000_win_rate",
  },
];

const store = useAppStore();
const message = useMessage();

const rows = ref<IndustryRow[]>([]);
const loading = ref(false);
const period = ref<PeriodKey>("all");
const years = ref<number[]>([]);
const periodLabel = ref("全样本");
const indexName = ref("沪深300");
const weightLabel = ref("流通市值加权");
const benchmarkLabel = ref("沪深300同行业（成分市值加权满仓）");
const sectorLevelLabel = ref("中信一级");
const indexBenchmarks = ref<IndexIndustryBenchmark[]>([]);

const excessMode = ref<ExcessMode>("median");
const indexBenchId = ref("hs300");
const sortState = ref<{ columnKey: string; order: "ascend" | "descend" }>({
  columnKey: "median_excess_return",
  order: "ascend",
});

const weightText = computed(() => weightLabel.value || "流通市值加权");
const levelText = computed(() => sectorLevelLabel.value || "中信一级");
const isAllPeriod = computed(() => period.value === "all");

const yearOptions = computed(() => {
  const opts = years.value.map((y) => ({
    label: `${y}年`,
    value: String(y),
  }));
  opts.push({ label: "全样本", value: "all" });
  return opts;
});

const periodSelect = computed({
  get: () => (period.value === "all" ? "all" : String(period.value)),
  set: (v: string) => {
    if (v === "all") selectPeriod("all");
    else selectPeriod(Number(v));
  },
});

const benches = computed(() => {
  const list = indexBenchmarks.value.length ? indexBenchmarks.value : DEFAULT_BENCHES;
  return list.map((b) => {
    const prefix = b.prefix || b.id;
    return {
      ...b,
      return_key: b.return_key || `${prefix}_industry_return`,
      n_key: b.n_key || `${prefix}_industry_n`,
      excess_key: b.excess_key || `${prefix}_index_excess_return`,
      n_win_key: b.n_win_key || `${prefix}_n_win`,
      n_lose_key: b.n_lose_key || `${prefix}_n_lose`,
      n_flat_key: b.n_flat_key || `${prefix}_n_flat`,
      win_rate_key: b.win_rate_key || `${prefix}_win_rate`,
      label: b.label || `${b.name}同行业（成分市值加权满仓）`,
    };
  });
});

const activeBench = computed(() => {
  return benches.value.find((b) => b.id === indexBenchId.value) || benches.value[0];
});

const indexLabel = computed(() => activeBench.value?.name || indexName.value || "沪深300");
const benchText = computed(
  () => activeBench.value?.label || benchmarkLabel.value || `${indexLabel.value}同行业（成分市值加权满仓）`,
);

const pagination = reactive<PaginationProps>({
  page: 1,
  pageSize: 29,
  showSizePicker: true,
  pageSizes: [10, 15, 29],
  prefix: ({ itemCount }) => `共 ${itemCount ?? 0} 个${levelText.value}行业`,
  onChange: (page) => {
    pagination.page = page;
  },
  onUpdatePageSize: (pageSize) => {
    pagination.pageSize = pageSize;
    pagination.page = 1;
  },
});

const modeMeta = computed(() => {
  const idx = indexLabel.value;
  const w = weightText.value;
  const scope = periodLabel.value;
  const excessKey = (activeBench.value?.excess_key ||
    "median_index_excess_return") as NumericKey;
  const base: Record<string, {
    key: NumericKey;
    label: string;
    compare: string;
    scope: string;
    ask: string;
    hint: string;
  }> = {
    median: {
      key: "median_excess_return",
      label: "加权超额",
      compare: "策略 vs 该股满仓",
      scope: `${scope} · ${w}`,
      ask: "相对死拿这只股，策略有没有多赚钱？",
      hint: `${scope}：策略 − 该股满仓；行业内按${w}合成。`,
    },
    index: {
      key: excessKey,
      label: `相对${idx}行业`,
      compare: `我的行业组合 vs ${idx}同行业`,
      scope: `${scope} · ${levelText.value} · 成分拆行业 · ${w}`,
      ask: `在同一${levelText.value}里，能否跑赢 ${idx} 该行业那一篮子？`,
      hint: `${scope}：策略${levelText.value}${w}收益 − ${benchText.value}。`,
    },
  };
  if (isAllPeriod.value) {
    base.oos = {
      key: "median_oos_excess_return",
      label: "样本外加权",
      compare: "策略 vs 该股满仓",
      scope: `后 30% · ${w}`,
      ask: "仓位规则在没见过的后段，还能不能相对该股多赚钱？",
      hint: `仅后 30%：策略 − 该股满仓；仓位在前 70% 定好再检验；行业内${w}。防过拟合。`,
    };
  }
  return base;
});

const activeMeta = computed(() => modeMeta.value[excessMode.value] || modeMeta.value.median);

async function loadIndustry(next?: PeriodKey) {
  loading.value = true;
  try {
    const reqYear = next === undefined ? undefined : next === "all" ? "all" : next;
    const industry = await getIndustrySummary(reqYear);
    rows.value = industry.rows || [];
    years.value = industry.years || [];
    periodLabel.value = industry.period_label || (industry.period === "all" ? "全样本" : `${industry.period}年`);
    if (industry.period === "all" || industry.year == null) {
      period.value = "all";
    } else {
      period.value = Number(industry.year);
    }
    indexName.value = industry.index_name || "沪深300";
    weightLabel.value = industry.weight_label || "流通市值加权";
    benchmarkLabel.value =
      industry.index_benchmark_label || "沪深300同行业（成分市值加权满仓）";
    sectorLevelLabel.value = industry.sector_level_label || "中信一级";
    indexBenchmarks.value = industry.index_benchmarks || [];
    if (!isAllPeriod.value && excessMode.value === "oos") {
      excessMode.value = "median";
    }
  } catch (e) {
    message.error(`行业汇总加载失败: ${(e as Error).message}`);
  } finally {
    loading.value = false;
  }
}

function selectPeriod(next: PeriodKey) {
  if (next === period.value && rows.value.length) return;
  void loadIndustry(next);
}

function numVal(v: unknown, empty: number): number {
  if (v == null || Number.isNaN(Number(v))) return empty;
  return Number(v);
}

function cmpNum(a: IndustryRow, b: IndustryRow, key: NumericKey, asc: boolean): number {
  const empty = asc ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;
  const av = numVal(a[key], empty);
  const bv = numVal(b[key], empty);
  return asc ? av - bv : bv - av;
}

const chartRows = computed(() => {
  const key = activeMeta.value.key;
  return [...rows.value].sort((a, b) => cmpNum(a, b, key, true));
});

const excessOption = computed(() =>
  industryExcessOption(chartRows.value, {
    metric: activeMeta.value.key,
    seriesName: activeMeta.value.label,
  }),
);

const outcomeKeys = computed(() => {
  if (excessMode.value === "oos") {
    return {
      winKey: "oos_n_win" as const,
      loseKey: "oos_n_lose" as const,
      flatKey: "oos_n_flat" as const,
      rateKey: "oos_win_rate" as const,
      title: "样本外胜负只数",
    };
  }
  if (excessMode.value === "index") {
    const b = activeBench.value;
    return {
      winKey: (b?.n_win_key || "hs300_n_win") as keyof IndustryRow,
      loseKey: (b?.n_lose_key || "hs300_n_lose") as keyof IndustryRow,
      flatKey: (b?.n_flat_key || "hs300_n_flat") as keyof IndustryRow,
      rateKey: (b?.win_rate_key || "hs300_win_rate") as keyof IndustryRow,
      title: `相对${indexLabel.value}胜负只数`,
    };
  }
  return {
    winKey: "n_win" as const,
    loseKey: "n_lose" as const,
    flatKey: "n_flat" as const,
    rateKey: "count_win_rate" as const,
    title: "相对个股满仓胜负只数",
  };
});

const winOption = computed(() =>
  industryWinOption(chartRows.value, {
    winKey: outcomeKeys.value.winKey,
    loseKey: outcomeKeys.value.loseKey,
    flatKey: outcomeKeys.value.flatKey,
    rateKey: outcomeKeys.value.rateKey,
    title: outcomeKeys.value.title,
  }),
);

const chartHeight = computed(() => {
  const n = chartRows.value.length;
  if (n <= 8) return 340;
  if (n <= 16) return 400;
  return 460;
});

function makeNumSorter(key: NumericKey) {
  return (a: IndustryRow, b: IndustryRow) => cmpNum(a, b, key, true);
}

const columns = computed<DataTableColumns<IndustryRow>>(() => {
  const sortKey = sortState.value?.columnKey;
  const sortOrder = sortState.value?.order ?? false;
  const idx = indexLabel.value;
  const retKey = (activeBench.value?.return_key || "hs300_industry_return") as NumericKey;
  const nKey = (activeBench.value?.n_key || "hs300_industry_n") as NumericKey;
  const excessKey = (activeBench.value?.excess_key ||
    "median_index_excess_return") as NumericKey;

  function excessCol(title: string, key: NumericKey) {
    return {
      title,
      key,
      sorter: makeNumSorter(key),
      sortOrder: (sortKey === key ? sortOrder : false) as false | "ascend" | "descend",
      render: (r: IndustryRow) => fmtPct(r[key] as number | null | undefined),
    };
  }

  const cols: DataTableColumns<IndustryRow> = [
    {
      title: "行业",
      key: "sector",
      sorter: (a, b) => String(a.sector || "").localeCompare(String(b.sector || ""), "zh"),
      sortOrder: sortKey === "sector" ? sortOrder : false,
    },
    {
      title: "样本",
      key: "n_symbols",
      sorter: makeNumSorter("n_symbols"),
      sortOrder: sortKey === "n_symbols" ? sortOrder : false,
    },
    excessCol("加权超额", "median_excess_return"),
  ];
  if (isAllPeriod.value) {
    cols.push(excessCol("样本外加权", "median_oos_excess_return"));
  }
  cols.push(
    excessCol(`${idx}行业`, retKey),
    {
      title: `${idx}成分`,
      key: nKey,
      sorter: makeNumSorter(nKey),
      sortOrder: sortKey === nKey ? sortOrder : false,
      render: (r) => {
        const n = r[nKey];
        return n == null ? "—" : String(n);
      },
    },
    excessCol(`相对${idx}行业`, excessKey),
    {
      title: "跑赢",
      key: outcomeKeys.value.winKey,
      sorter: makeNumSorter(outcomeKeys.value.winKey),
      sortOrder: sortKey === outcomeKeys.value.winKey ? sortOrder : false,
      render: (r) => {
        const n = r[outcomeKeys.value.winKey];
        return n == null ? "—" : String(n);
      },
    },
    {
      title: "跑输",
      key: outcomeKeys.value.loseKey,
      sorter: makeNumSorter(outcomeKeys.value.loseKey),
      sortOrder: sortKey === outcomeKeys.value.loseKey ? sortOrder : false,
      render: (r) => {
        const n = r[outcomeKeys.value.loseKey];
        return n == null ? "—" : String(n);
      },
    },
    {
      title: "持平",
      key: outcomeKeys.value.flatKey,
      sorter: makeNumSorter(outcomeKeys.value.flatKey),
      sortOrder: sortKey === outcomeKeys.value.flatKey ? sortOrder : false,
      render: (r) => {
        const n = r[outcomeKeys.value.flatKey];
        return n == null ? "—" : String(n);
      },
    },
    {
      title: "胜率",
      key: outcomeKeys.value.rateKey,
      sorter: makeNumSorter(outcomeKeys.value.rateKey),
      sortOrder: sortKey === outcomeKeys.value.rateKey ? sortOrder : false,
      render: (r) => fmtPct(r[outcomeKeys.value.rateKey] as number | null | undefined),
    },
    {
      title: "策略收益",
      key: "mean_strategy_return",
      sorter: makeNumSorter("mean_strategy_return"),
      sortOrder: sortKey === "mean_strategy_return" ? sortOrder : false,
      render: (r) => fmtPct(r.mean_strategy_return),
    },
    {
      title: "个股基准",
      key: "mean_benchmark_return",
      sorter: makeNumSorter("mean_benchmark_return"),
      sortOrder: sortKey === "mean_benchmark_return" ? sortOrder : false,
      render: (r) => fmtPct(r.mean_benchmark_return),
    },
  );
  return cols;
});

function onUpdateSorter(sorter: DataTableSortState | DataTableSortState[] | null) {
  const next = Array.isArray(sorter) ? sorter[0] ?? null : sorter;
  if (!next || !next.order) {
    sortState.value = { columnKey: String(activeMeta.value.key), order: "ascend" };
  } else {
    sortState.value = { columnKey: String(next.columnKey), order: next.order };
  }
  pagination.page = 1;
}

watch(
  () => rows.value.length,
  () => {
    pagination.page = 1;
  },
);

watch(excessMode, () => {
  sortState.value = { columnKey: String(activeMeta.value.key), order: "ascend" };
  pagination.page = 1;
});

watch(indexBenchId, () => {
  if (excessMode.value === "index") {
    sortState.value = { columnKey: String(activeMeta.value.key), order: "ascend" };
    pagination.page = 1;
  }
});

watch(
  benches,
  (list) => {
    if (!list.some((b) => b.id === indexBenchId.value) && list[0]) {
      indexBenchId.value = list[0].id;
    }
  },
  { immediate: true },
);

watch(
  () => store.dataEpoch,
  (epoch) => {
    if (epoch <= 0) return;
    void loadIndustry(period.value);
  },
);

onMounted(() => {
  void loadIndustry();
});
</script>

<template>
  <div class="page active">
    <section class="panel">
      <div class="panel-head">
        <h2>行业汇总</h2>
        <div class="industry-period-bar">
          <label class="industry-period-label" for="industry-year">年份</label>
          <n-select
            id="industry-year"
            v-model:value="periodSelect"
            class="industry-year-select"
            :options="yearOptions"
            :disabled="loading"
            :loading="loading"
            size="small"
            placeholder="选择年份"
          />
          <span v-if="rows.length" class="hint"
            >共 {{ rows.length }} 个{{ levelText }} · {{ weightText }}</span
          >
        </div>
      </div>

      <div class="excess-mode-tabs">
        <button
          v-for="(meta, key) in modeMeta"
          :key="key"
          type="button"
          class="btn btn-sm"
          :class="excessMode === key ? 'btn-primary' : 'btn-secondary'"
          @click="excessMode = key as ExcessMode"
        >
          {{ meta.label }}
        </button>
      </div>
      <div v-if="excessMode === 'index'" class="excess-mode-tabs index-bench-tabs">
        <button
          v-for="b in benches"
          :key="b.id"
          type="button"
          class="btn btn-sm"
          :class="indexBenchId === b.id ? 'btn-primary' : 'btn-secondary'"
          @click="indexBenchId = b.id"
        >
          {{ b.name }}
        </button>
      </div>

      <div v-if="rows.length" class="excess-compare-box">
        <p class="hint excess-compare-title">口径说明（行业内均为{{ weightText }}；区间={{ periodLabel }}）</p>
        <ul class="excess-compare-list">
          <li
            v-for="(meta, key) in modeMeta"
            :key="key"
            :class="{ active: excessMode === key }"
          >
            <strong>{{ meta.label }}</strong>
            <span class="excess-compare-vs">{{ meta.compare }}</span>
            <span class="excess-compare-scope">（{{ meta.scope }}）</span>
            <span class="excess-compare-ask">{{ meta.ask }}</span>
          </li>
        </ul>
        <p class="hint metrics-scope-hint">
          当前：{{ activeMeta.hint }} · 年度收益按权益曲线首末净值计算 · 点击表头排序
        </p>
      </div>

      <template v-if="rows.length">
        <div class="chart-stack">
          <ChartBox :option="excessOption" class="chart" :style="{ height: `${chartHeight}px` }" />
          <ChartBox :option="winOption" class="chart" :style="{ height: `${chartHeight}px` }" />
        </div>
        <n-data-table
          :columns="columns"
          :data="rows"
          :pagination="pagination"
          :bordered="false"
          @update:sorter="onUpdateSorter"
        />
      </template>
      <p v-else-if="loading" class="hint">行业汇总加载中…</p>
      <p v-else class="hint">暂无行业汇总，请先运行行业验证 batch</p>
    </section>
  </div>
</template>
