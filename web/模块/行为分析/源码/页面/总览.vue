<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { getDailySignals } from "@/接口/客户端";
import DailySignalsPanel from "@/组件/今日信号.vue";
import { useAppStore } from "@/状态/应用";
import type { DailySignalsData } from "@/类型/接口";
import { sectorOptions, singleOptionValue } from "@/工具/行业级联";

const store = useAppStore();
const dailySignals = ref<DailySignalsData | null>(null);
const signalsLoading = ref(false);
const sectorL1 = ref("");
const sectorL2 = ref("");
const sectorL3 = ref("");
const searchQ = ref("");
let filterTimer: ReturnType<typeof setTimeout> | null = null;

const l1Options = computed(() => sectorOptions(store.sectorTree, []));
const l2Options = computed(() => sectorOptions(store.sectorTree, [sectorL1.value]));
const l3Options = computed(() => sectorOptions(store.sectorTree, [sectorL1.value, sectorL2.value]));

const classificationText = computed(() => {
  const cls = store.overview?.classification;
  if (!cls?.standard?.name) return "--";
  return `一级${cls.l1_count ?? "--"} · 二级${cls.l2_count ?? "--"} · 三级${cls.l3_count ?? "--"}`;
});

const overviewStats = computed(() => [
  { label: "中信行业", value: classificationText.value },
  { label: "已分析", value: store.overview?.analyzed_count ?? "--" },
  { label: "覆盖", value: store.overview?.data_symbol_count ?? "--" },
  { label: "行情", value: store.updateStatus.latest_bar_date ?? "--" },
  { label: "待更新", value: store.updateStatus.stale ?? "--" },
  { label: "停牌", value: store.updateStatus.suspended ?? 0 },
]);

function buildFilterParams() {
  const params = new URLSearchParams({ limit: "30" });
  if (sectorL1.value) params.set("sector_l1", sectorL1.value);
  if (sectorL2.value) params.set("sector_l2", sectorL2.value);
  if (sectorL3.value) params.set("sector_l3", sectorL3.value);
  if (searchQ.value.trim()) params.set("q", searchQ.value.trim());
  return params;
}

async function loadSignals() {
  signalsLoading.value = true;
  try {
    dailySignals.value = await getDailySignals(buildFilterParams());
  } catch {
    dailySignals.value = null;
  } finally {
    signalsLoading.value = false;
  }
}

watch([sectorL1], () => {
  const l2 = sectorOptions(store.sectorTree, [sectorL1.value]);
  sectorL2.value = singleOptionValue(l2);
  const l3 = sectorOptions(store.sectorTree, [sectorL1.value, sectorL2.value]);
  sectorL3.value = singleOptionValue(l3);
  loadSignals();
});

watch([sectorL2], () => {
  const l3 = sectorOptions(store.sectorTree, [sectorL1.value, sectorL2.value]);
  sectorL3.value = singleOptionValue(l3);
  loadSignals();
});

watch([sectorL3], () => loadSignals());

watch(searchQ, () => {
  if (filterTimer) clearTimeout(filterTimer);
  filterTimer = setTimeout(() => loadSignals(), 300);
});

watch(
  () => store.dataEpoch,
  (epoch) => {
    if (epoch <= 0) return;
    loadSignals();
  },
);

onMounted(() => {
  if (store.dataEpoch > 0) loadSignals();
});
</script>

<template>
  <div class="page active">
    <section class="stats-strip" aria-label="数据概览">
      <span v-for="(item, i) in overviewStats" :key="item.label" class="stats-strip-item">
        <template v-if="i > 0"><span class="stats-strip-sep" aria-hidden="true">·</span></template>
        <span class="stats-strip-label">{{ item.label }}</span>
        <strong class="stats-strip-value">{{ item.value }}</strong>
      </span>
    </section>

    <div class="filter-row">
      <n-select v-model:value="sectorL1" :options="l1Options" placeholder="全部一级行业" clearable />
      <n-select v-model:value="sectorL2" :options="l2Options" placeholder="全部二级行业" clearable :disabled="!sectorL1" />
      <n-select v-model:value="sectorL3" :options="l3Options" placeholder="全部三级行业" clearable :disabled="!sectorL2" />
      <n-input v-model:value="searchQ" placeholder="搜索代码或名称" clearable />
    </div>

    <DailySignalsPanel :data="dailySignals" :loading="signalsLoading" />
  </div>
</template>
