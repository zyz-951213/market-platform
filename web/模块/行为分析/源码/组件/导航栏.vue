<script setup lang="ts">
import { computed, onMounted, onUnmounted } from "vue";
import { getUpdateStatus } from "@/接口/客户端";
import { useAppStore } from "@/状态/应用";

const store = useAppStore();
const emit = defineEmits<{ refresh: []; toggleSidebar: [] }>();

const tabTitle = computed(() => {
  const map: Record<string, string> = {
    overview: "总览",
    industry: "行业",
    symbol: "个股",
    portfolio: "策略",
    paper: "模拟",
  };
  return map[store.activeTab] || "";
});

const freshnessHint = computed(() => {
  const u = store.updateStatus;
  if (u.is_running) return "后台自动更新中…";
  const parts: string[] = [];
  if (u.scheduler_enabled) parts.push("自动更新已开启");
  parts.push(`行情 ${u.latest_bar_date || "--"}`, `目标 ${u.expected_trading_day || "--"}`);
  if (u.stale != null && Number(u.stale) > 0) parts.push(`待更新 ${u.stale}`);
  if (u.suspended != null && Number(u.suspended) > 0) parts.push(`停牌 ${u.suspended}`);
  if (Number(u.analyze_pending) > 0) parts.push(`待分析 ${u.analyze_pending}`);
  if (u.paper_last_trade_date) parts.push(`模拟换仓 ${u.paper_last_trade_date}`);
  if (u.last_run_at) parts.push(`上次 ${String(u.last_run_at).replace("T", " ").slice(0, 16)}`);
  return parts.join(" · ");
});

let pollTimer: ReturnType<typeof setInterval> | null = null;
let wasRunning = false;
let lastPaperUpdatedAt = "";

async function pollStatus() {
  try {
    const status = await getUpdateStatus();
    const running = !!status.is_running;
    store.setUpdateStatus(status);
    if (wasRunning && !running) emit("refresh");
    wasRunning = running;

    const paperAt = status.paper_updated_at || "";
    if (paperAt && lastPaperUpdatedAt && paperAt !== lastPaperUpdatedAt) {
      // 后台模拟盘换仓/刷新后，通知各页重拉（含持仓与成交）
      store.bumpDataEpoch();
    }
    if (paperAt) lastPaperUpdatedAt = paperAt;
  } catch {
    /* ignore */
  }
}

onMounted(() => {
  wasRunning = !!store.updateStatus.is_running;
  lastPaperUpdatedAt = store.updateStatus.paper_updated_at || "";
  pollTimer = setInterval(pollStatus, 15000);
  void pollStatus();
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <header class="navbar">
    <div class="navbar-brand">
      <button type="button" class="navbar-menu-btn" title="显示/隐藏侧栏" @click="emit('toggleSidebar')">
        ☰
      </button>
      <div class="navbar-brand-text">
        <h1>Market Agents <span v-if="tabTitle" class="navbar-page-title">/ {{ tabTitle }}</span></h1>
        <p class="data-freshness-hint">{{ freshnessHint }}</p>
      </div>
    </div>
    <div class="navbar-actions">
      <button class="btn btn-secondary" @click="emit('refresh')">刷新</button>
    </div>
  </header>
</template>
