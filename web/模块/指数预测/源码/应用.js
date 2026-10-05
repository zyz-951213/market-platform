import { escapeHtml } from "@/公共/工具.js";
import {
  loadOverview,
  loadPriceSeries,
  loadPromptPreview,
  switchPreviewTab,
  resizeOverviewCharts,
} from "@/页面/概览.js";
import {
  loadBacktestCharts,
  loadChannelSummaries,
  loadAccuracyPartitions,
  runBacktestCompare,
  renderCompareMetrics,
  renderAccuracyHeat,
  setAllocationViewMode,
  resizeBacktestCharts,
  clearCompareChart,
} from "@/页面/回测.js";
import { loadPaperTrading, resizePaperCharts } from "@/页面/模拟盘.js";
import { loadAttribution, resizeAttributionCharts } from "@/页面/归因.js";

function resizeAllCharts() {
  resizeOverviewCharts();
  resizeBacktestCharts();
  resizeAttributionCharts();
  resizePaperCharts();
}

function bindEvents() {
  document.querySelectorAll(".navbar-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".navbar-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      const page = document.getElementById(tab.dataset.page);
      if (page) page.classList.add("active");
      if (tab.dataset.page === "page-attribution") {
        setTimeout(() => loadAttribution(), 80);
      }
      if (tab.dataset.page === "page-paper") {
        setTimeout(() => loadPaperTrading(), 80);
      }
      setTimeout(() => resizeAllCharts(), 120);
    });
  });

  document.getElementById("backtest-provider")?.addEventListener("change", () => {
    loadBacktestCharts().catch((e) => console.warn("切换回测渠道失败:", e.message));
  });

  document.getElementById("channel-summary-body")?.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-provider]");
    if (!tr) return;
    const provider = tr.dataset.provider;
    const sel = document.getElementById("backtest-provider");
    if (sel) sel.value = provider;
    loadBacktestCharts(provider).catch((err) => console.warn("切换回测渠道失败:", err.message));
  });

  document.getElementById("acc-part-dim")?.addEventListener("change", (e) => {
    renderAccuracyHeat(e.target.value);
  });

  document.getElementById("paper-provider")?.addEventListener("change", () => {
    loadPaperTrading().catch((e) => console.warn("切换模拟实盘渠道失败:", e.message));
  });

  document.getElementById("attribution-provider")?.addEventListener("change", () => {
    loadAttribution().catch((e) => console.warn("切换归因渠道失败:", e.message));
  });

  document.getElementById("analyze-date")?.addEventListener("change", loadPromptPreview);

  document.getElementById("preview-tabs")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".preview-tab");
    if (!btn) return;
    switchPreviewTab(btn.dataset.tab);
  });

  document.getElementById("refresh-btn").addEventListener("click", async () => {
    await loadOverview();
    await loadChannelSummaries();
    await loadAccuracyPartitions();
    await loadBacktestCharts();
    await loadPriceSeries();
    await loadPromptPreview();
    await loadAttribution();
  });

  document.getElementById("paper-refresh-btn")?.addEventListener("click", loadPaperTrading);

  document.getElementById("compare-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("compare-btn");
    btn.disabled = true;
    try {
      await runBacktestCompare();
    } catch (e) {
      renderCompareMetrics(null);
      clearCompareChart();
      alert(e.message);
    } finally {
      btn.disabled = false;
    }
  });

  document.querySelectorAll(".alloc-mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => setAllocationViewMode(btn.dataset.mode));
  });

  window.addEventListener("resize", resizeAllCharts);
}

async function boot() {
  bindEvents();
  try {
    await loadOverview();
    await Promise.all([
      loadChannelSummaries(),
      loadBacktestCharts(),
      loadPriceSeries(),
      loadPromptPreview(),
    ]);
    loadAccuracyPartitions().catch((e) => console.warn("分区准确率加载失败:", e.message));
    if (document.querySelectorAll('#compare-provider-picks input:checked').length >= 2) {
      try {
        await runBacktestCompare();
      } catch (_) {
        /* 可选对比 */
      }
    }
  } catch (e) {
    document.getElementById("preview-body").innerHTML =
      `<div class="preview-pane active" data-pane="overview" id="preview-pane-overview"><p class="empty-hint">初始化失败：${escapeHtml(e.message)}<br>请先运行 market-predictor pipeline -- --step data</p></div>`;
  }
}

boot();
