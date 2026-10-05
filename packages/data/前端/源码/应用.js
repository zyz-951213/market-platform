import { api } from "@/接口/客户端.js";
import { $ } from "@/公共/工具.js";
import { loadOverview, resizeOverviewCharts } from "@/页面/概览.js";
import { loadTables, bindTablesEvents } from "@/页面/表结构.js";
import { loadQueryTables, bindQueryEvents } from "@/页面/查询.js";
import { loadUpdatePage } from "@/页面/更新.js";

function bindNav() {
  document.querySelectorAll(".navbar-tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      document.querySelectorAll(".navbar-tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      const page = $(tab.dataset.page);
      if (page) page.classList.add("active");
      if (tab.dataset.page === "page-overview") {
        setTimeout(() => resizeOverviewCharts(), 80);
      }
      if (tab.dataset.page === "page-update") {
        try {
          await loadUpdatePage();
        } catch (e) {
          alert(e.message);
        }
      }
    });
  });

  $("refresh-btn")?.addEventListener("click", async () => {
    const btn = $("refresh-btn");
    if (btn) btn.disabled = true;
    try {
      await loadOverview();
      await loadTables(true);
      await loadQueryTables();
      const active = document.querySelector(".navbar-tab.active")?.dataset.page;
      if (active === "page-update") await loadUpdatePage();
    } catch (e) {
      alert(e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  window.addEventListener("resize", resizeOverviewCharts);
}

async function syncModeBadge() {
  try {
    const h = await api("/api/health");
    const badge = $("view-only-badge");
    if (!badge) return;
    if (h.update_api) {
      badge.textContent = "可更新";
      badge.classList.add("mode-write");
    } else {
      badge.textContent = "只读";
      badge.classList.remove("mode-write");
    }
  } catch {
    /* ignore */
  }
}

async function boot() {
  bindNav();
  bindTablesEvents();
  bindQueryEvents();
  try {
    await syncModeBadge();
    await loadOverview();
    await loadTables();
    await loadQueryTables();
  } catch (e) {
    const grid = $("schema-grid");
    if (grid) {
      grid.innerHTML = `<p class="error-hint">初始化失败：${e.message}<br>请确认 PostgreSQL 已启动，且「金融市场数据」库可连接。</p>`;
    }
  }
}

boot();
