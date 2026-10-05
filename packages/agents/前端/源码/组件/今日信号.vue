<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { DailySignalRow, DailySignalsData } from "@/类型/接口";
import { useAppStore } from "@/状态/应用";
import { fmtPct, fmtPctSigned, fmtStockName, signedClass } from "@/工具/格式化";

const props = defineProps<{
  data: DailySignalsData | null;
  loading?: boolean;
}>();

const store = useAppStore();
const PAGE_SIZE = 5;
const offensePage = ref(1);
const defensePage = ref(1);
const switchPage = ref(1);

const summaryChips = computed(() => {
  const s = props.data?.summary;
  if (!s) return [];
  return [
    { label: "已分析", value: String(s.total_analyzed) },
    { label: "有变动", value: String(s.changed_count), accent: s.changed_count > 0 },
    { label: "加仓", value: String(s.add_count) },
    { label: "减仓", value: String(s.reduce_count) },
    { label: "状态切换", value: String(s.state_change_count) },
  ];
});

const offenseAll = computed(() => props.data?.high_conviction || []);
const defenseAll = computed(() => props.data?.defensive || []);
const switchAll = computed(() => props.data?.state_changes || []);

const offensePages = computed(() => Math.max(1, Math.ceil(offenseAll.value.length / PAGE_SIZE)));
const defensePages = computed(() => Math.max(1, Math.ceil(defenseAll.value.length / PAGE_SIZE)));
const switchPages = computed(() => Math.max(1, Math.ceil(switchAll.value.length / PAGE_SIZE)));

const offenseRows = computed(() => {
  const start = (offensePage.value - 1) * PAGE_SIZE;
  return offenseAll.value.slice(start, start + PAGE_SIZE);
});

const defenseRows = computed(() => {
  const start = (defensePage.value - 1) * PAGE_SIZE;
  return defenseAll.value.slice(start, start + PAGE_SIZE);
});

const switchRows = computed(() => {
  const start = (switchPage.value - 1) * PAGE_SIZE;
  return switchAll.value.slice(start, start + PAGE_SIZE);
});

watch(offensePages, (n) => {
  if (offensePage.value > n) offensePage.value = n;
});
watch(defensePages, (n) => {
  if (defensePage.value > n) defensePage.value = n;
});
watch(switchPages, (n) => {
  if (switchPage.value > n) switchPage.value = n;
});
watch(
  () => props.data?.signal_date,
  () => {
    offensePage.value = 1;
    defensePage.value = 1;
    switchPage.value = 1;
  },
);

function onPick(row: DailySignalRow) {
  if (row.symbol) store.goToSymbol(row.symbol);
}
</script>

<template>
  <section class="panel daily-signals-panel">
    <div class="panel-head">
      <h2>今日信号</h2>
      <span v-if="loading" class="hint">加载中…</span>
      <button type="button" class="btn btn-secondary btn-sm daily-signals-holdings-btn" @click="store.goToPaper()">
        模拟
      </button>
    </div>

    <div v-if="summaryChips.length" class="stats-strip signal-summary-strip" aria-label="信号摘要">
      <span
        v-for="(chip, i) in summaryChips"
        :key="chip.label"
        class="stats-strip-item"
        :class="{ accent: chip.accent }"
      >
        <template v-if="i > 0"><span class="stats-strip-sep" aria-hidden="true">·</span></template>
        <span class="stats-strip-label">{{ chip.label }}</span>
        <strong class="stats-strip-value">{{ chip.value }}</strong>
      </span>
    </div>

    <div class="signal-grid-2">
      <div class="signal-card">
        <div class="signal-card-head">
          <h3>仓位进攻</h3>
          <div v-if="offenseAll.length" class="signal-pager">
            <button type="button" class="btn btn-secondary btn-sm" :disabled="offensePage <= 1" @click="offensePage--">上一页</button>
            <span class="hint">{{ offensePage }}/{{ offensePages }}</span>
            <button type="button" class="btn btn-secondary btn-sm" :disabled="offensePage >= offensePages" @click="offensePage++">下一页</button>
          </div>
        </div>
        <div v-if="offenseRows.length" class="signal-list">
          <button
            v-for="row in offenseRows"
            :key="`hi-${row.symbol}`"
            type="button"
            class="signal-row"
            @click="onPick(row)"
          >
            <span class="signal-name">{{ fmtStockName(row, store.symbolIndex) }}</span>
            <span class="signal-meta">
              {{ row.market_state }} · {{ fmtPct(row.prev_position) }} → {{ fmtPct(row.suggested_position) }}
              <span :class="signedClass(row.oos_excess_return)">
                · 样本外 {{ fmtPctSigned(row.oos_excess_return) }}
              </span>
            </span>
          </button>
        </div>
        <p v-else class="hint">今日暂无加仓信号</p>
      </div>

      <div class="signal-card">
        <div class="signal-card-head">
          <h3>防御观望</h3>
          <div v-if="defenseAll.length" class="signal-pager">
            <button type="button" class="btn btn-secondary btn-sm" :disabled="defensePage <= 1" @click="defensePage--">上一页</button>
            <span class="hint">{{ defensePage }}/{{ defensePages }}</span>
            <button type="button" class="btn btn-secondary btn-sm" :disabled="defensePage >= defensePages" @click="defensePage++">下一页</button>
          </div>
        </div>
        <div v-if="defenseRows.length" class="signal-list">
          <button
            v-for="row in defenseRows"
            :key="`lo-${row.symbol}`"
            type="button"
            class="signal-row"
            @click="onPick(row)"
          >
            <span class="signal-name">{{ fmtStockName(row, store.symbolIndex) }}</span>
            <span class="signal-meta">
              {{ row.market_state }} · {{ fmtPct(row.prev_position) }} → {{ fmtPct(row.suggested_position) }}
            </span>
          </button>
        </div>
        <p v-else class="hint">今日暂无减仓信号</p>
      </div>
    </div>

    <div class="signal-card signal-card-below">
      <div class="signal-card-head">
        <h3>状态切换</h3>
        <div v-if="switchAll.length" class="signal-pager">
          <button type="button" class="btn btn-secondary btn-sm" :disabled="switchPage <= 1" @click="switchPage--">上一页</button>
          <span class="hint">{{ switchPage }}/{{ switchPages }}</span>
          <button type="button" class="btn btn-secondary btn-sm" :disabled="switchPage >= switchPages" @click="switchPage++">下一页</button>
        </div>
      </div>
      <div v-if="switchRows.length" class="signal-list">
        <button
          v-for="row in switchRows"
          :key="`sw-${row.symbol}`"
          type="button"
          class="signal-row"
          @click="onPick(row)"
        >
          <span class="signal-name">{{ fmtStockName(row, store.symbolIndex) }}</span>
          <span class="signal-meta">
            {{ row.prev_state || "--" }} → {{ row.market_state || "--" }}
            · 仓位 {{ fmtPct(row.suggested_position) }}
          </span>
        </button>
      </div>
      <p v-else class="hint">今日暂无状态切换</p>
    </div>
  </section>
</template>
