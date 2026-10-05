import { api } from "@/接口/客户端.js";
import { $, escapeHtml, fmtNum, renderDataTable } from "@/公共/工具.js";
import { state } from "@/公共/状态.js";

let queryOffset = 0;
let sqlHistory = [];
let querySearch = "";
let selectedSchema = null;
let selectedTable = null;
let tableColumns = [];
let collapsedSchemas = new Set();
let sortCol = null;
let sortDir = "DESC";
/** SQL 结果客户端排序 */
let sqlResultCols = [];
let sqlResultRows = [];
let sqlSortCol = null;
let sqlSortDir = "ASC";

const SQL_TEMPLATES = [
  {
    title: "预览日历",
    sql: `SELECT *\nFROM "公共"."日历文件"\nORDER BY "日历日期" DESC\nLIMIT 50`,
  },
  {
    title: "个股近况",
    sql: `SELECT "证券代码", "交易日期", "收盘价", "涨跌幅"\nFROM "公共"."个股日行情文件"\nWHERE "证券代码" = '000001'\nORDER BY "交易日期" DESC\nLIMIT 100`,
  },
  {
    title: "Schema 表清单",
    sql: `SELECT schemaname, tablename\nFROM pg_tables\nWHERE schemaname NOT IN ('pg_catalog', 'information_schema')\nORDER BY 1, 2`,
  },
  {
    title: "大表排行",
    sql: `SELECT n.nspname AS schema,\n       c.relname AS table,\n       pg_size_pretty(pg_total_relation_size(c.oid)) AS size\nFROM pg_class c\nJOIN pg_namespace n ON n.oid = c.relnamespace\nWHERE c.relkind = 'r'\n  AND n.nspname NOT IN ('pg_catalog', 'information_schema')\nORDER BY pg_total_relation_size(c.oid) DESC\nLIMIT 20`,
  },
  {
    title: "宏观 CPI",
    sql: `SELECT *\nFROM "公共"."居民消费价格涨幅表(月)"\nORDER BY 1 DESC\nLIMIT 50`,
  },
  {
    title: "水位总览",
    sql: `SELECT "数据源", "数据集", "责任方", "最早日", "最后日", "行数", "状态"\nFROM "行情增量"."水位总览"\nORDER BY "状态", "最后日" NULLS FIRST\nLIMIT 50`,
  },
];

export async function loadQueryTables() {
  if (!state.tables.length) {
    state.tables = await api("/api/tables");
  }
  renderQueryTree();
  renderSqlTemplates();
}

function filteredTables() {
  const kw = querySearch.trim().toLowerCase();
  if (!kw) return state.tables;
  return state.tables.filter(
    (t) =>
      String(t.tablename).toLowerCase().includes(kw) ||
      String(t.schema_name).toLowerCase().includes(kw)
  );
}

function renderQueryTree() {
  const tree = $("query-table-tree");
  if (!tree) return;
  const tables = filteredTables();
  if (!tables.length) {
    tree.innerHTML = `<p class="empty-hint">${state.tables.length ? "无匹配表" : "暂无表"}</p>`;
    return;
  }

  const groups = {};
  tables.forEach((t) => {
    const s = t.schema_name || "public";
    if (!groups[s]) groups[s] = [];
    groups[s].push(t);
  });

  tree.innerHTML = Object.entries(groups)
    .map(([schema, list]) => {
      const open = !collapsedSchemas.has(schema);
      const items = list
        .map((t) => {
          const active =
            selectedSchema === schema && selectedTable === t.tablename ? " active" : "";
          return `<button type="button" class="tree-item${active}"
              data-schema="${escapeHtml(schema)}"
              data-table="${escapeHtml(t.tablename)}">
            <span class="tree-item-name" title="${escapeHtml(t.tablename)}">${escapeHtml(t.tablename)}</span>
            <span class="tree-item-meta"><span>${escapeHtml(t.size || "")}</span></span>
          </button>`;
        })
        .join("");
      return `<div class="tree-group${open ? " open" : ""}">
        <button type="button" class="tree-group-head" data-toggle="${escapeHtml(schema)}">
          <span class="tree-caret">${open ? "▾" : "▸"}</span>
          <span class="tree-schema">${escapeHtml(schema)}</span>
          <span class="tree-count">${list.length}</span>
        </button>
        <div class="tree-group-body">${items}</div>
      </div>`;
    })
    .join("");

  tree.querySelectorAll(".tree-group-head").forEach((btn) => {
    btn.addEventListener("click", () => {
      const schema = btn.dataset.toggle;
      if (collapsedSchemas.has(schema)) collapsedSchemas.delete(schema);
      else collapsedSchemas.add(schema);
      renderQueryTree();
    });
  });

  tree.querySelectorAll(".tree-item").forEach((btn) => {
    btn.addEventListener("click", () => selectQueryTable(btn.dataset.schema, btn.dataset.table));
  });
}

async function selectQueryTable(schema, table) {
  selectedSchema = schema;
  selectedTable = table;
  queryOffset = 0;
  sortCol = null;
  sortDir = "DESC";
  if ($("query-order")) $("query-order").value = "";
  if ($("query-where")) $("query-where").value = "";
  collapsedSchemas.delete(schema);
  renderQueryTree();

  if ($("query-detail-title")) $("query-detail-title").textContent = table;
  if ($("query-detail-subtitle")) $("query-detail-subtitle").textContent = `${schema} · 条件查询`;

  // 拉字段供点击插入
  try {
    const info = await api(
      `/api/schema/${encodeURIComponent(schema)}/tables/${encodeURIComponent(table)}`
    );
    tableColumns = (info.columns || []).map((c) => c.column_name);
    renderColumnChips();
    // 自动默认排序: 有日期列则按日期列 DESC(显示数据库最新), 无日期列不设排序
    const dateCol = (info.columns || []).find((c) =>
      /date|time|日期|时间/i.test(String(c.data_type || ""))
    );
    if (dateCol && $("query-order")) {
      $("query-order").value = `"${dateCol.column_name}" DESC`;
      sortCol = dateCol.column_name;
      sortDir = "DESC";
    }
  } catch {
    tableColumns = [];
    renderColumnChips();
  }

  // 切到条件查询并自动查一页
  switchMode("builder");
  await runQuery();
}

function renderColumnChips() {
  const box = $("query-columns");
  if (!box) return;
  if (!tableColumns.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML =
    `<span class="hint">字段（点击插入 WHERE；右键设为排序）</span>` +
    tableColumns
      .map(
        (c) =>
          `<button type="button" class="col-chip" data-col="${escapeHtml(c)}">${escapeHtml(c)}</button>`
      )
      .join("");
  box.querySelectorAll(".col-chip").forEach((btn) => {
    btn.addEventListener("click", () => insertColumn(btn.dataset.col));
    btn.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      applySort(btn.dataset.col, true);
    });
  });
}

function insertColumn(col) {
  const where = $("query-where");
  const order = $("query-order");
  const active = document.activeElement;
  const target =
    active === order || active === where ? active : where || order;
  if (!target) return;
  const quoted = `"${col}"`;
  const start = target.selectionStart ?? target.value.length;
  const end = target.selectionEnd ?? target.value.length;
  const before = target.value.slice(0, start);
  const after = target.value.slice(end);
  const needSpace = before && !/\s$/.test(before) && !/[,(]$/.test(before);
  const insert = (needSpace ? " " : "") + quoted;
  target.value = before + insert + after;
  const pos = start + insert.length;
  target.focus();
  target.setSelectionRange(pos, pos);
}

function syncOrderInput() {
  if (!$("query-order")) return;
  if (!sortCol) {
    // 保留用户手写多列排序
    return;
  }
  $("query-order").value = `"${sortCol}" ${sortDir}`;
}

function parseOrderInput() {
  const raw = $("query-order")?.value.trim() || "";
  if (!raw) {
    sortCol = null;
    return;
  }
  // 仅识别单列排序以高亮表头
  const m = raw.match(/^"?([\w\u4e00-\u9fff（）()\-./·]+)"?(?:\s+(ASC|DESC))?$/i);
  if (m && !raw.includes(",")) {
    sortCol = m[1];
    sortDir = (m[2] || "ASC").toUpperCase();
  } else {
    sortCol = null;
  }
}

function applySort(col, fromChip = false) {
  if (sortCol === col) {
    sortDir = sortDir === "ASC" ? "DESC" : "ASC";
  } else {
    sortCol = col;
    sortDir = fromChip ? "DESC" : "ASC";
  }
  syncOrderInput();
  queryOffset = 0;
  runQuery();
}

function buildSqlFromForm() {
  if (!selectedSchema || !selectedTable) return "";
  const where = $("query-where")?.value.trim();
  const order = $("query-order")?.value.trim();
  const limit = parseInt($("query-limit")?.value, 10) || 100;
  let sql = `SELECT *\nFROM "${selectedSchema}"."${selectedTable}"`;
  if (where) sql += `\nWHERE ${where}`;
  if (order) sql += `\nORDER BY ${order}`;
  sql += `\nLIMIT ${limit}`;
  return sql;
}

export async function runQuery() {
  if (!selectedSchema || !selectedTable) {
    if ($("query-meta")) $("query-meta").textContent = "请先选择左侧的表";
    return;
  }
  parseOrderInput();
  const limit = parseInt($("query-limit")?.value, 10) || 100;
  const orderBy = $("query-order")?.value.trim() || null;
  const where = $("query-where")?.value.trim() || null;
  const btn = $("query-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "查询中…";
  }
  if ($("query-meta")) $("query-meta").textContent = "查询中…";

  try {
    const data = await api("/api/query", {
      method: "POST",
      body: JSON.stringify({
        table: selectedTable,
        schema: selectedSchema,
        limit,
        offset: queryOffset,
        order_by: orderBy,
        where,
      }),
    });
    const cols = data.columns || [];
    const rows = data.rows || [];
    const est = data.total_estimate ? " ≈" : "";
    const from = data.total ? data.offset + 1 : 0;
    const to = Math.min(data.offset + data.limit, data.total);
    const sortHint = orderBy ? ` · 排序 ${escapeHtml(orderBy)}` : "";
    if ($("query-meta")) {
      $("query-meta").innerHTML =
        `<strong>${escapeHtml(selectedTable)}</strong>` +
        ` · 共 ${fmtNum(data.total)}${est} 行 · 显示 ${from}-${to} · ${cols.length} 列${sortHint}` +
        `<span class="hint" style="margin-left:8px">点击表头排序</span>`;
    }
    renderDataTable($("query-table-wrap"), cols, rows, "无结果", {
      sortable: true,
      sortCol,
      sortDir,
      onSort: (col) => applySort(col),
    });
    renderPagination(data);
  } catch (e) {
    if ($("query-table-wrap")) {
      $("query-table-wrap").innerHTML = `<p class="error-hint">${escapeHtml(e.message)}</p>`;
    }
    if ($("query-meta")) $("query-meta").textContent = "查询失败";
    if ($("query-pagination")) $("query-pagination").innerHTML = "";
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "查询";
    }
  }
}

function renderPagination(data) {
  const el = $("query-pagination");
  if (!el) return;
  const total = data.total;
  const lim = data.limit;
  const off = data.offset;
  const tp = Math.max(1, Math.ceil(total / lim) || 1);
  const cp = Math.floor(off / lim) + 1;
  el.innerHTML = `
    <button type="button" ${off <= 0 ? "disabled" : ""} id="page-prev">上一页</button>
    <span>${cp} / ${tp}</span>
    <button type="button" ${off + lim >= total ? "disabled" : ""} id="page-next">下一页</button>
    <span class="page-jump">
      <input type="number" id="page-input" value="${cp}" min="1" max="${tp}" />
      <button type="button" id="page-go" class="btn small">跳转</button>
    </span>`;
  $("page-prev")?.addEventListener("click", () => {
    queryOffset = Math.max(0, off - lim);
    runQuery();
  });
  $("page-next")?.addEventListener("click", () => {
    queryOffset = off + lim;
    runQuery();
  });
  $("page-go")?.addEventListener("click", () => {
    const p = Math.max(1, Math.min(tp, parseInt($("page-input").value, 10) || 1));
    queryOffset = (p - 1) * lim;
    runQuery();
  });
}

export async function runSql() {
  const sql = $("sql-editor")?.value?.trim();
  if (!sql) {
    if ($("sql-meta")) $("sql-meta").textContent = "请输入 SQL";
    return;
  }
  const btn = $("sql-run-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "执行中…";
  }
  if ($("sql-result-meta")) $("sql-result-meta").textContent = "执行中…";
  try {
    const data = await api("/api/sql", {
      method: "POST",
      body: JSON.stringify({ sql, max_rows: 500 }),
    });
    pushHistory(sql);
    if (data.type === "execute") {
      if ($("sql-meta")) $("sql-meta").textContent = `影响 ${fmtNum(data.affected_rows)} 行`;
      if ($("sql-result-meta")) {
        $("sql-result-meta").textContent = `执行成功 · affected ${fmtNum(data.affected_rows)}`;
      }
      if ($("sql-result-wrap")) {
        $("sql-result-wrap").innerHTML = `<p class="empty-hint">无结果集</p>`;
      }
    } else {
      const tip = data.truncated ? " · 已截断至 500 行" : "";
      const msg = `${fmtNum(data.row_count)} 行 · ${(data.columns || []).length} 列${tip}`;
      if ($("sql-meta")) $("sql-meta").textContent = msg;
      if ($("sql-result-meta")) {
        $("sql-result-meta").innerHTML =
          `${msg}<span class="hint" style="margin-left:8px">点击表头排序（当前结果）</span>`;
      }
      sqlResultCols = data.columns || [];
      sqlResultRows = data.rows || [];
      sqlSortCol = null;
      sqlSortDir = "ASC";
      renderSqlResultTable();
    }
  } catch (e) {
    if ($("sql-meta")) $("sql-meta").textContent = "失败";
    if ($("sql-result-meta")) $("sql-result-meta").textContent = "执行失败";
    if ($("sql-result-wrap")) {
      $("sql-result-wrap").innerHTML = `<p class="error-hint">${escapeHtml(e.message)}</p>`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "执行";
    }
  }
}

function renderSqlResultTable() {
  let rows = sqlResultRows;
  if (sqlSortCol) {
    const col = sqlSortCol;
    const dir = sqlSortDir === "DESC" ? -1 : 1;
    rows = [...sqlResultRows].sort((a, b) => {
      const va = a[col];
      const vb = b[col];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const na = Number(va);
      const nb = Number(vb);
      if (!Number.isNaN(na) && !Number.isNaN(nb) && String(va).trim() !== "" && String(vb).trim() !== "") {
        return (na - nb) * dir;
      }
      return String(va).localeCompare(String(vb), "zh-CN", { numeric: true }) * dir;
    });
  }
  renderDataTable($("sql-result-wrap"), sqlResultCols, rows, "无结果", {
    sortable: true,
    sortCol: sqlSortCol,
    sortDir: sqlSortDir,
    onSort: (col) => {
      if (sqlSortCol === col) sqlSortDir = sqlSortDir === "ASC" ? "DESC" : "ASC";
      else {
        sqlSortCol = col;
        sqlSortDir = "ASC";
      }
      renderSqlResultTable();
    },
  });
}

function pushHistory(sql) {
  sqlHistory = [sql, ...sqlHistory.filter((s) => s !== sql)].slice(0, 10);
  const box = $("sql-history");
  if (!box) return;
  box.innerHTML =
    `<span class="hint">历史</span>` +
    sqlHistory
      .map(
        (s, i) =>
          `<button type="button" class="sql-hist-item" data-i="${i}" title="${escapeHtml(s)}">${escapeHtml(
            s.replace(/\s+/g, " ").slice(0, 48)
          )}${s.length > 48 ? "…" : ""}</button>`
      )
      .join("");
  box.querySelectorAll(".sql-hist-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      if ($("sql-editor")) $("sql-editor").value = sqlHistory[Number(btn.dataset.i)];
    });
  });
}

function renderSqlTemplates() {
  const box = $("sql-templates");
  if (!box) return;
  box.innerHTML = SQL_TEMPLATES.map(
    (t, i) =>
      `<button type="button" class="sql-tpl" data-i="${i}">
        <strong>${escapeHtml(t.title)}</strong>
        <span>${escapeHtml(t.sql.replace(/\s+/g, " ").slice(0, 56))}…</span>
      </button>`
  ).join("");
  box.querySelectorAll(".sql-tpl").forEach((btn) => {
    btn.addEventListener("click", () => {
      if ($("sql-editor")) $("sql-editor").value = SQL_TEMPLATES[Number(btn.dataset.i)].sql;
    });
  });
}

function switchMode(mode) {
  document.querySelectorAll(".query-mode-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.mode === mode);
  });
  $("query-builder")?.classList.toggle("hidden", mode !== "builder");
  $("sql-console")?.classList.toggle("hidden", mode !== "sql");
  if (mode === "sql" && $("query-detail-subtitle")) {
    $("query-detail-subtitle").textContent = "只读 · 仅 SELECT / WITH / SHOW / EXPLAIN";
  } else if (mode === "builder" && selectedTable && $("query-detail-subtitle")) {
    $("query-detail-subtitle").textContent = `${selectedSchema} · 条件查询（只读）`;
  }
}

function convertToSql() {
  const sql = buildSqlFromForm();
  if (!sql) {
    if ($("query-meta")) $("query-meta").textContent = "请先选择表";
    return;
  }
  if ($("sql-editor")) $("sql-editor").value = sql;
  switchMode("sql");
}

function tidySql() {
  const el = $("sql-editor");
  if (!el) return;
  el.value = el.value
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function bindQueryEvents() {
  $("query-table-search")?.addEventListener("input", (e) => {
    querySearch = e.target.value || "";
    renderQueryTree();
  });

  $("query-btn")?.addEventListener("click", () => {
    queryOffset = 0;
    runQuery();
  });
  $("query-to-sql")?.addEventListener("click", convertToSql);
  $("sql-run-btn")?.addEventListener("click", runSql);
  $("sql-format-btn")?.addEventListener("click", tidySql);

  ["query-where", "query-order", "query-limit"].forEach((id) => {
    $(id)?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        queryOffset = 0;
        runQuery();
      }
    });
  });

  $("sql-editor")?.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      runSql();
    }
  });

  document.querySelectorAll(".query-mode-tab").forEach((tab) => {
    tab.addEventListener("click", () => switchMode(tab.dataset.mode));
  });
}
