<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useAppStore } from "@/状态/应用";

const store = useAppStore();

const STORAGE_KEY = "ma.sidebar";

const tabs = [
  { key: "overview" as const, label: "总览", short: "总" },
  { key: "industry" as const, label: "行业", short: "行" },
  { key: "symbol" as const, label: "个股", short: "股" },
  { key: "portfolio" as const, label: "策略", short: "策" },
  { key: "paper" as const, label: "模拟", short: "模" },
];

/** 是否完全隐藏侧栏 */
const hidden = ref(false);
/** 是否钉住展开（不自动收起） */
const pinned = ref(false);
/** 悬停展开 */
const hovered = ref(false);

const expanded = computed(() => !hidden.value && (pinned.value || hovered.value));

function persist() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ hidden: hidden.value, pinned: pinned.value }),
    );
  } catch {
    /* ignore */
  }
}

function loadPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw) as { hidden?: boolean; pinned?: boolean };
    if (typeof data.hidden === "boolean") hidden.value = data.hidden;
    if (typeof data.pinned === "boolean") pinned.value = data.pinned;
  } catch {
    /* ignore */
  }
}

function selectTab(key: (typeof tabs)[number]["key"]) {
  store.activeTab = key;
}

function togglePin() {
  pinned.value = !pinned.value;
  if (pinned.value) hidden.value = false;
  persist();
}

function toggleHidden() {
  hidden.value = !hidden.value;
  if (!hidden.value && !pinned.value) hovered.value = true;
  persist();
}

function onEnter() {
  if (!hidden.value) hovered.value = true;
}

function onLeave() {
  hovered.value = false;
}

onMounted(loadPrefs);
watch([hidden, pinned], persist);

defineExpose({ toggleHidden, hidden, expanded, pinned });
</script>

<template>
  <aside
    class="side-nav"
    :class="{ expanded, hidden, pinned }"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
  >
    <div class="side-nav-inner">
      <nav class="side-nav-tabs" aria-label="页面导航">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="side-nav-tab"
          :class="{ active: store.activeTab === tab.key }"
          :title="tab.label"
          @click="selectTab(tab.key)"
        >
          <span class="side-nav-short">{{ tab.short }}</span>
          <span class="side-nav-label">{{ tab.label }}</span>
        </button>
      </nav>
      <div class="side-nav-footer">
        <button
          type="button"
          class="side-nav-ctrl"
          :title="pinned ? '取消固定（悬停展开）' : '固定展开'"
          @click="togglePin"
        >
          <span class="side-nav-short">{{ pinned ? "钉" : "隐" }}</span>
          <span class="side-nav-label">{{ pinned ? "已固定" : "自动隐藏" }}</span>
        </button>
        <button type="button" class="side-nav-ctrl" title="完全隐藏侧栏" @click="toggleHidden">
          <span class="side-nav-short">收</span>
          <span class="side-nav-label">收起侧栏</span>
        </button>
      </div>
    </div>
  </aside>
</template>
