<script setup lang="ts">
/**
 * 统一站点壳:顶栏导航 + 三域容器。
 *
 * 三域页面以子应用形式内嵌(同一站点、同一端口、同一服务),
 * 各域保留自己的页面与交互,站点壳只负责入口、切换与状态。
 */
import { computed, onMounted, ref, watch } from "vue";
import { 域接口基址, 域列表, 域页面地址, type 域信息 } from "@/域登记";

const 开发模式 = import.meta.env.DEV;
const 当前 = ref<域信息>(域列表[0]);
const 容器 = ref<HTMLIFrameElement | null>(null);
const 键 = ref(0);
const 健康 = ref<Record<string, string>>({});

const 页面地址 = computed(() => 域页面地址(当前.value, 开发模式));

async function 探测(域: 域信息) {
  const 基址 = 域接口基址(域, 开发模式);
  try {
    const r = await fetch(`${基址}/api/health`, { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const 体 = await r.json();
    健康.value = { ...健康.value, [域.名]: 体?.mode === "update" ? "可写" : "在线" };
  } catch {
    健康.value = { ...健康.value, [域.名]: "离线" };
  }
}

function 切换(域: 域信息) {
  当前.value = 域;
  键.value += 1;
}

function 刷新() {
  键.value += 1;
}

watch(当前, (域) => void 探测(域));

onMounted(() => {
  void Promise.all(域列表.map(探测));
});
</script>

<template>
  <div class="壳">
    <header class="顶栏">
      <div class="品牌">
        <span class="标">市场量化平台</span>
        <span class="副">数据底座 · 行为分析 · 指数预测</span>
      </div>

      <nav class="导航">
        <button
          v-for="域 in 域列表"
          :key="域.名"
          class="导航项"
          :class="{ 选中: 当前.名 === 域.名 }"
          :style="{ '--域色': 域.色 }"
          @click="切换(域)"
        >
          <span class="点" />
          {{ 域.名 }}
          <em class="态" :data-态="健康[域.名] || '探测中'">{{ 健康[域.名] || "…" }}</em>
        </button>
      </nav>

      <div class="操作">
        <span class="说明">{{ 当前.说明 }}</span>
        <button class="钮" @click="刷新">刷新</button>
        <a class="钮" :href="页面地址" target="_blank" rel="noreferrer">新窗口</a>
      </div>
    </header>

    <main class="容器">
      <iframe
        ref="容器"
        :key="键"
        :src="页面地址"
        :title="当前.名"
        class="子应用"
      />
    </main>
  </div>
</template>

<style scoped>
.壳 {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0b1120;
  color: #e2e8f0;
}

.顶栏 {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 10px 18px;
  background: linear-gradient(90deg, #0f172a, #131c31);
  border-bottom: 1px solid rgba(148, 163, 184, 0.18);
  flex: none;
}

.品牌 { display: flex; flex-direction: column; line-height: 1.25; }
.标 { font-size: 17px; font-weight: 600; letter-spacing: 0.5px; }
.副 { font-size: 11px; color: #7f8ea8; }

.导航 { display: flex; gap: 6px; flex: 1; }
.导航项 {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border-radius: 9px;
  border: 1px solid transparent;
  background: rgba(148, 163, 184, 0.07);
  color: #cbd5e1;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.导航项:hover { background: rgba(148, 163, 184, 0.14); }
.导航项.选中 {
  background: color-mix(in srgb, var(--域色) 18%, transparent);
  border-color: color-mix(in srgb, var(--域色) 55%, transparent);
  color: #f8fafc;
}
.点 {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--域色); opacity: 0.75;
}
.态 {
  font-size: 10px; font-style: normal; color: #94a3b8;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: 999px; padding: 1px 6px;
}
.态[data-态="在线"], .态[data-态="可写"] { color: #4ade80; border-color: rgba(74, 222, 128, 0.4); }
.态[data-态="离线"] { color: #f87171; border-color: rgba(248, 113, 113, 0.4); }

.操作 { display: flex; align-items: center; gap: 10px; }
.说明 { font-size: 12px; color: #8b9cb8; max-width: 380px; }
.钮 {
  font-size: 12px; padding: 6px 12px; border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  background: rgba(148, 163, 184, 0.08);
  color: #cbd5e1; cursor: pointer; text-decoration: none;
}
.钮:hover { background: rgba(148, 163, 184, 0.18); }

.容器 { flex: 1; min-height: 0; background: #0b1120; }
.子应用 { width: 100%; height: 100%; border: 0; display: block; }
</style>
