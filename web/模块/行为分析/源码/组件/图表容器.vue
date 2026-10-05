<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { use } from "echarts/core";
import { BarChart, CandlestickChart, LineChart, PieChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import VChart from "vue-echarts";
import type { EChartsOption } from "echarts";

use([
  CanvasRenderer,
  BarChart,
  LineChart,
  PieChart,
  CandlestickChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkAreaComponent,
  AxisPointerComponent,
]);

const props = defineProps<{
  option: EChartsOption;
  chartClass?: string;
  /** 数据身份变化时传入（如 symbol+末日），强制重建实例 */
  remountKey?: string | number;
}>();

const emit = defineEmits<{
  chartClick: [params: { dataIndex?: number; name?: string; seriesName?: string }];
}>();

const hostRef = ref<HTMLElement | null>(null);
const chartRef = ref<{ resize?: () => void; setOption?: (...args: unknown[]) => void } | null>(null);
let io: IntersectionObserver | null = null;

function onChartClick(params: {
  dataIndex?: number;
  name?: string;
  seriesName?: string;
  componentType?: string;
  value?: string | number;
}) {
  const type = params?.componentType;
  if (type && type !== "series" && type !== "yAxis") return;
  emit("chartClick", {
    dataIndex: params?.dataIndex,
    name: typeof params?.name === "string" ? params.name : String(params?.value ?? ""),
    seriesName: params?.seriesName,
  });
}

const instanceKey = computed(() => String(props.remountKey ?? "chart"));

async function safeResize() {
  await nextTick();
  const el = hostRef.value;
  if (!el || el.clientWidth <= 0 || el.clientHeight <= 0) return;
  chartRef.value?.resize?.();
}

onMounted(() => {
  if (typeof IntersectionObserver === "undefined") return;
  io = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting && e.intersectionRatio > 0)) {
        safeResize();
      }
    },
    { threshold: [0, 0.01, 0.1] },
  );
  if (hostRef.value) io.observe(hostRef.value);
});

onBeforeUnmount(() => {
  io?.disconnect();
  io = null;
});

watch(
  () => props.option,
  () => {
    safeResize();
  },
);

watch(
  () => props.remountKey,
  () => {
    safeResize();
  },
);
</script>

<template>
  <div ref="hostRef" class="chart-host" :class="chartClass">
    <VChart
      ref="chartRef"
      :key="instanceKey"
      :option="option"
      :update-options="{ notMerge: true }"
      autoresize
      class="chart-host-canvas"
      @click="onChartClick"
    />
  </div>
</template>

<style scoped>
.chart-host {
  width: 100%;
  min-height: 0;
  position: relative;
}
.chart-host-canvas {
  width: 100%;
  height: 100%;
  min-height: inherit;
}
</style>
