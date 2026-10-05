import { defineStore } from "pinia";
import { ref } from "vue";
import { getSectorTree } from "@/接口/客户端";
import type { OverviewData, SectorTreeNode, UpdateStatus } from "@/类型/接口";

export interface SymbolMeta {
  sector_l1: string;
  sector_l2: string;
  sector_l3: string;
  name: string;
}

export const useAppStore = defineStore("app", () => {
  const activeTab = ref<"overview" | "industry" | "symbol" | "portfolio" | "paper">("overview");
  const sectorTree = ref<SectorTreeNode[]>([]);
  const symbolIndex = ref<Map<string, SymbolMeta>>(new Map());
  const overview = ref<OverviewData | null>(null);
  const updateStatus = ref<UpdateStatus>({});
  const selectedSymbol = ref<string | null>(null);
  /** 概览/更新完成后递增，各页据此重拉图表数据 */
  const dataEpoch = ref(0);

  function buildSymbolIndex(tree: SectorTreeNode[]) {
    const map = new Map<string, SymbolMeta>();
    for (const l1 of tree) {
      for (const l2 of l1.children || []) {
        for (const l3 of l2.children || []) {
          for (const item of l3.children || []) {
            if (!item.symbol) continue;
            map.set(item.symbol, {
              sector_l1: l1.name,
              sector_l2: l2.name,
              sector_l3: l3.name,
              name: item.name || "",
            });
          }
        }
      }
    }
    symbolIndex.value = map;
  }

  async function loadSectorTree() {
    const data = await getSectorTree();
    sectorTree.value = data.tree || [];
    buildSymbolIndex(sectorTree.value);
  }

  function findSymbolPath(symbol: string) {
    for (const l1 of sectorTree.value) {
      for (const l2 of l1.children || []) {
        for (const l3 of l2.children || []) {
          for (const item of l3.children || []) {
            if (item.symbol === symbol) {
              return { l1: l1.name, l2: l2.name, l3: l3.name, symbol };
            }
          }
        }
      }
    }
    return null;
  }

  function setOverview(data: OverviewData) {
    overview.value = data;
    updateStatus.value = data.update || {};
  }

  function setUpdateStatus(data: UpdateStatus) {
    updateStatus.value = data;
    if (overview.value) overview.value.update = data;
  }

  function bumpDataEpoch() {
    dataEpoch.value += 1;
  }

  function goToSymbol(symbol: string) {
    selectedSymbol.value = symbol;
    activeTab.value = "symbol";
  }

  function goToPaper() {
    activeTab.value = "paper";
  }

  return {
    activeTab,
    sectorTree,
    symbolIndex,
    overview,
    updateStatus,
    selectedSymbol,
    dataEpoch,
    loadSectorTree,
    findSymbolPath,
    setOverview,
    setUpdateStatus,
    bumpDataEpoch,
    goToSymbol,
    goToPaper,
  };
});
