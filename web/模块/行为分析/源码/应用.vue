<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useMessage } from "naive-ui";
import { getOverview, listPortfolios } from "@/接口/客户端";
import Navbar from "@/组件/导航栏.vue";
import SideNav from "@/组件/侧边导航.vue";
import OverviewView from "@/页面/总览.vue";
import IndustryView from "@/页面/行业.vue";
import SymbolView from "@/页面/标的.vue";
import PortfolioView from "@/页面/组合.vue";
import PaperView from "@/页面/模拟盘.vue";
import { useAppStore } from "@/状态/应用";

const store = useAppStore();
const message = useMessage();
const portfolioHistory = ref<import("@/类型/接口").PortfolioHistoryItem[]>([]);
const booting = ref(true);
const sideNavRef = ref<InstanceType<typeof SideNav> | null>(null);

async function boot() {
  booting.value = true;
  try {
    const [overview, history] = await Promise.all([
      getOverview(),
      listPortfolios().catch(() => ({ portfolios: [] })),
      store.loadSectorTree(),
    ]);
    store.setOverview(overview);
    portfolioHistory.value = history.portfolios || [];
    store.bumpDataEpoch();
  } catch (e) {
    message.error(`加载失败: ${(e as Error).message}`);
  } finally {
    booting.value = false;
  }
}

function onToggleSidebar() {
  sideNavRef.value?.toggleHidden();
}

onMounted(() => boot());
</script>

<template>
  <div class="app-shell">
    <Navbar @refresh="boot" @toggle-sidebar="onToggleSidebar" />
    <div class="app-body">
      <SideNav ref="sideNavRef" />
      <main class="main-content">
        <div v-if="booting" class="page active">
          <p class="hint">加载中…</p>
        </div>
        <OverviewView v-show="!booting && store.activeTab === 'overview'" />
        <IndustryView v-show="!booting && store.activeTab === 'industry'" />
        <SymbolView v-show="!booting && store.activeTab === 'symbol'" />
        <PortfolioView
          v-show="!booting && store.activeTab === 'portfolio'"
          :history="portfolioHistory"
          @history-change="portfolioHistory = $event"
        />
        <PaperView v-show="!booting && store.activeTab === 'paper'" />
      </main>
    </div>
  </div>
</template>
