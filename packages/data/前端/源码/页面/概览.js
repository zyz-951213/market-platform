import * as echarts from "echarts";
import { api } from "@/接口/客户端.js";
import { $, escapeHtml, fmtNum, switchPage } from "@/公共/工具.js";
import { state } from "@/公共/状态.js";

let sizeChart = null;

function ensureChart() {
  const el = $("schema-size-chart");
  if (!el) return null;
  if (!sizeChart) sizeChart = echarts.init(el);
  return sizeChart;
}

export function resizeOverviewCharts() {
  sizeChart?.resize();
}

export async function loadOverview() {
  const db = await api("/api/db");
  state.db = db;

  const statusEl = $("stat-status");
  if (statusEl) {
    statusEl.textContent = db.status === "connected" ? "已连接" : "连接失败";
    statusEl.className = `stat-value ${db.status === "connected" ? "connected" : "error"}`;
  }
  if ($("stat-schemas")) $("stat-schemas").textContent = fmtNum(db.schema_count);
  if ($("stat-tables")) $("stat-tables").textContent = fmtNum(db.table_count);
  if ($("stat-size")) $("stat-size").textContent = db.size || "--";

  const badge = $("db-badge");
  if (badge) {
    badge.textContent = db.status === "connected" ? "已连接" : "异常";
    badge.className = `badge ${db.status === "connected" ? "ok" : "err"}`;
  }

  renderSchemaCards(db.schemas || []);
  renderTopTables(db.top_tables || []);
  renderWatermarks(db.watermarks || []);
  renderSizeChart(db.schemas || []);
}

function renderWatermarks(rows) {
  const wrap = $("watermarks-body");
  if (!wrap) return;
  if (!rows.length) {
    wrap.innerHTML = `<tr><td colspan="6" class="empty-hint">暂无异常水位（或水位表不可用）</td></tr>`;
    return;
  }
  wrap.innerHTML = rows
    .map(
      (r) => `
    <tr>
      <td>${escapeHtml(r.数据源 ?? "")}</td>
      <td>${escapeHtml(r.数据集 ?? "")}</td>
      <td>${escapeHtml(r.责任方 ?? "")}</td>
      <td>${escapeHtml(r.最后日 ?? "—")}</td>
      <td class="num">${fmtNum(r.行数)}</td>
      <td>${escapeHtml(r.状态 ?? "")}</td>
    </tr>`
    )
    .join("");
}

function renderSchemaCards(schemas) {
  const grid = $("schema-grid");
  if (!grid) return;
  if (!schemas.length) {
    grid.innerHTML = `<p class="empty-hint">${state.db?.error ? escapeHtml(state.db.error) : "暂无 Schema"}</p>`;
    return;
  }
  grid.innerHTML = schemas
    .map(
      (s) => `
    <div class="db-card" data-schema="${escapeHtml(s.schema_name)}">
      <div class="db-card-name">${escapeHtml(s.schema_name)}</div>
      <div class="db-card-label">Schema</div>
      <div class="db-card-stats">
        <span>表 <strong>${fmtNum(s.table_count)}</strong></span>
        <span>大小 <strong>${escapeHtml(s.size || "--")}</strong></span>
      </div>
    </div>`
    )
    .join("");

  grid.querySelectorAll(".db-card").forEach((card) => {
    card.addEventListener("click", () => {
      state.selectedSchema = card.dataset.schema;
      switchPage("page-tables");
      window.dispatchEvent(new CustomEvent("db:open-schema", { detail: { schema: state.selectedSchema } }));
    });
  });
}

function renderTopTables(tables) {
  const wrap = $("top-tables-body");
  if (!wrap) return;
  if (!tables.length) {
    wrap.innerHTML = `<tr><td colspan="4" class="empty-hint">暂无数据</td></tr>`;
    return;
  }
  wrap.innerHTML = tables
    .map(
      (t, i) => `
    <tr data-schema="${escapeHtml(t.schema_name)}" data-table="${escapeHtml(t.tablename)}">
      <td>${i + 1}</td>
      <td>${escapeHtml(t.schema_name)}</td>
      <td>${escapeHtml(t.tablename)}</td>
      <td class="num">${escapeHtml(t.size)}</td>
    </tr>`
    )
    .join("");

  wrap.querySelectorAll("tr[data-table]").forEach((tr) => {
    tr.addEventListener("click", () => {
      state.selectedSchema = tr.dataset.schema;
      state.selectedTable = tr.dataset.table;
      switchPage("page-tables");
      window.dispatchEvent(
        new CustomEvent("db:open-table", {
          detail: { schema: tr.dataset.schema, table: tr.dataset.table },
        })
      );
    });
  });
}

function renderSizeChart(schemas) {
  const chart = ensureChart();
  if (!chart) return;
  const names = schemas.map((s) => s.schema_name);
  const values = schemas.map((s) => Number(s.size_bytes || 0) / (1024 ** 3));
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const p = params[0];
        return `${p.name}<br/>${p.value.toFixed(2)} GB`;
      },
    },
    grid: { left: 48, right: 20, top: 24, bottom: 40 },
    xAxis: {
      type: "category",
      data: names,
      axisLabel: { color: "#9aa0a6", rotate: names.length > 4 ? 20 : 0 },
      axisLine: { lineStyle: { color: "#2a2f3a" } },
    },
    yAxis: {
      type: "value",
      name: "GB",
      nameTextStyle: { color: "#9aa0a6" },
      axisLabel: { color: "#9aa0a6" },
      splitLine: { lineStyle: { color: "#2a2f3a" } },
    },
    series: [
      {
        type: "bar",
        data: values,
        barMaxWidth: 48,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "#4c8bf5" },
            { offset: 1, color: "#2a4a80" },
          ]),
          borderRadius: [6, 6, 0, 0],
        },
      },
    ],
  });
}
