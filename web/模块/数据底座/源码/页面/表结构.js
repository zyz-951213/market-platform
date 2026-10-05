import { api } from "@/接口/客户端.js";
import { $, escapeHtml, fmtNum, renderDataTable } from "@/公共/工具.js";
import { state } from "@/公共/状态.js";

let searchKeyword = "";
let collapsedSchemas = new Set();

export async function loadTables(force = false) {
  if (!force && state.tables.length) {
    renderTableTree();
    return state.tables;
  }
  const data = await api("/api/tables");
  state.tables = data || [];
  renderTableTree();
  return state.tables;
}

function filteredTables() {
  const kw = searchKeyword.trim().toLowerCase();
  if (!kw) return state.tables;
  return state.tables.filter(
    (t) =>
      String(t.tablename).toLowerCase().includes(kw) ||
      String(t.schema_name).toLowerCase().includes(kw)
  );
}

function renderTableTree() {
  const tree = $("table-tree");
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
            state.selectedSchema === schema && state.selectedTable === t.tablename
              ? " active"
              : "";
          const rows =
            t.row_estimate != null ? fmtNum(t.row_estimate) : "";
          return `<button type="button" class="tree-item${active}"
              data-schema="${escapeHtml(schema)}"
              data-table="${escapeHtml(t.tablename)}">
            <span class="tree-item-name" title="${escapeHtml(t.tablename)}">${escapeHtml(t.tablename)}</span>
            <span class="tree-item-meta">
              <span>${escapeHtml(t.size || "")}</span>
              ${rows ? `<span class="tree-rows">${rows}</span>` : ""}
            </span>
          </button>`;
        })
        .join("");
      return `<div class="tree-group${open ? " open" : ""}" data-schema-group="${escapeHtml(schema)}">
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
      renderTableTree();
    });
  });

  tree.querySelectorAll(".tree-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      openTable(btn.dataset.schema, btn.dataset.table);
    });
  });
}

export async function openSchema(schema) {
  state.selectedSchema = schema;
  state.selectedTable = null;
  collapsedSchemas.delete(schema);
  await loadTables();
  resetDetail(`Schema · ${schema}`, `共 ${state.tables.filter((t) => t.schema_name === schema).length} 张表，请选择`);
}

export async function openTable(schema, table) {
  state.selectedSchema = schema;
  state.selectedTable = table;
  collapsedSchemas.delete(schema);
  renderTableTree();
  await loadTableDetails(schema, table);
}

function resetDetail(title, subtitle) {
  if ($("detail-title")) $("detail-title").textContent = title;
  if ($("detail-subtitle")) $("detail-subtitle").textContent = subtitle;
  $("table-info-bar")?.classList.add("hidden");
  if ($("schema-content")) $("schema-content").innerHTML = `<p class="empty-hint">${escapeHtml(subtitle)}</p>`;
  if ($("index-content")) $("index-content").innerHTML = `<p class="empty-hint">—</p>`;
  if ($("preview-content")) $("preview-content").innerHTML = `<p class="empty-hint">—</p>`;
  switchDetailTab("fields");
}

export async function loadTableDetails(schema, table) {
  if (!schema || !table) return;
  state.selectedSchema = schema;
  state.selectedTable = table;
  if ($("detail-title")) $("detail-title").textContent = table;
  if ($("detail-subtitle")) $("detail-subtitle").textContent = `${schema} · 加载中…`;
  switchDetailTab("fields");

  try {
    const [info, stats, preview] = await Promise.all([
      api(`/api/schema/${encodeURIComponent(schema)}/tables/${encodeURIComponent(table)}`),
      api(`/api/schema/${encodeURIComponent(schema)}/tables/${encodeURIComponent(table)}/stats`),
      api(`/api/schema/${encodeURIComponent(schema)}/tables/${encodeURIComponent(table)}/preview?limit=20`),
    ]);

    $("table-info-bar")?.classList.remove("hidden");
    const colCount = (info.columns || []).length;
    if ($("detail-subtitle")) {
      $("detail-subtitle").textContent = `${schema} · ${colCount} 字段 · ${(info.indexes || []).length} 索引`;
    }
    if ($("ti-rows")) {
      $("ti-rows").textContent = fmtNum(stats.row_count) + (stats.row_estimate ? " ≈" : "");
    }
    if ($("ti-size")) $("ti-size").textContent = stats.table_size || "--";
    if ($("ti-cols")) $("ti-cols").textContent = fmtNum(colCount);
    if ($("ti-analyze")) {
      $("ti-analyze").textContent = stats.last_analyze
        ? String(stats.last_analyze).slice(0, 16)
        : "--";
    }
    renderSchema(info);
    renderIndexes(info);
    renderPreview(preview);
  } catch (e) {
    if ($("schema-content")) {
      $("schema-content").innerHTML = `<p class="error-hint">加载失败：${escapeHtml(e.message)}</p>`;
    }
  }
}

function typeKind(type) {
  const t = String(type || "").toLowerCase();
  if (/(int|numeric|double|real|decimal|money|serial)/.test(t)) return "num";
  if (/(timestamp|date|time)/.test(t)) return "time";
  if (/(bool)/.test(t)) return "bool";
  if (/(json|uuid|bytea)/.test(t)) return "special";
  return "text";
}

function shortenType(type) {
  return String(type || "")
    .replace("character varying", "varchar")
    .replace("timestamp without time zone", "timestamp")
    .replace("timestamp with time zone", "timestamptz")
    .replace("double precision", "float8");
}

function renderSchema(info) {
  const cols = info.columns || [];
  if (!$("schema-content")) return;
  if (!cols.length) {
    $("schema-content").innerHTML = `<p class="empty-hint">无字段</p>`;
    return;
  }

  const rows = cols
    .map((c, i) => {
      const tags = [];
      if (c.is_pk) tags.push(`<span class="tag tag-pk">PK</span>`);
      if (c.is_identity) tags.push(`<span class="tag tag-id">自增</span>`);
      if (c.not_null) tags.push(`<span class="tag tag-nn">NOT NULL</span>`);
      else tags.push(`<span class="tag tag-null">NULL</span>`);
      const kind = typeKind(c.data_type);
      return `<tr class="${c.is_pk ? "is-pk" : ""}">
        <td class="col-idx">${i + 1}</td>
        <td class="col-name">
          <div class="col-name-row">
            <strong>${escapeHtml(c.column_name)}</strong>
            <span class="col-tags">${tags.join("")}</span>
          </div>
          ${c.description ? `<div class="col-desc">${escapeHtml(c.description)}</div>` : ""}
        </td>
        <td><span class="type-badge kind-${kind}" title="${escapeHtml(c.data_type)}">${escapeHtml(shortenType(c.data_type))}</span></td>
      </tr>`;
    })
    .join("");

  $("schema-content").innerHTML = `
    <div class="fields-toolbar">
      <span class="hint">${cols.length} 个字段</span>
    </div>
    <div class="table-wrap fields-wrap">
      <table class="schema-table">
        <thead>
          <tr><th class="col-idx">#</th><th>字段</th><th>类型</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderIndexes(info) {
  const idxs = info.indexes || [];
  const fks = info.foreign_keys || [];
  if (!$("index-content")) return;

  if (!idxs.length && !fks.length) {
    $("index-content").innerHTML = `<p class="empty-hint">无索引 / 外键</p>`;
    return;
  }

  let html = "";
  if (idxs.length) {
    html += `<div class="index-section">
      <div class="section-sub">索引 · ${idxs.length}</div>
      <div class="index-list">
        ${idxs
          .map((i) => {
            const def = i.indexdef || "";
            const unique = /\bUNIQUE\b/i.test(def);
            const primary = /\bPRIMARY KEY\b/i.test(def);
            const badge = primary
              ? `<span class="tag tag-pk">PRIMARY</span>`
              : unique
                ? `<span class="tag tag-id">UNIQUE</span>`
                : `<span class="tag tag-nn">INDEX</span>`;
            return `<div class="index-card">
              <div class="index-card-head">
                <strong>${escapeHtml(i.indexname)}</strong>
                ${badge}
              </div>
              <code class="mono index-def">${escapeHtml(def)}</code>
            </div>`;
          })
          .join("")}
      </div>
    </div>`;
  }

  if (fks.length) {
    html += `<div class="index-section">
      <div class="section-sub">外键 · ${fks.length}</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>约束</th><th>字段</th><th>引用</th></tr></thead>
          <tbody>
            ${fks
              .map(
                (f) => `<tr>
              <td>${escapeHtml(f.constraint_name)}</td>
              <td><strong>${escapeHtml(f.column_name)}</strong></td>
              <td class="mono">→ ${escapeHtml(f.foreign_table_name)}.${escapeHtml(f.foreign_column_name)}</td>
            </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>
    </div>`;
  }

  $("index-content").innerHTML = html;
}

function renderPreview(data) {
  const wrap = $("preview-content");
  if (!wrap) return;
  renderDataTable(wrap, data.columns || [], data.rows || [], "无数据");
  const meta = document.createElement("div");
  meta.className = "table-footer-hint";
  const est = data.total_estimate ? "（估算）" : "";
  meta.textContent = `共 ${fmtNum(data.total)} 行${est}，显示前 ${Math.min(20, (data.rows || []).length)} 行`;
  wrap.appendChild(meta);
}

function switchDetailTab(tab) {
  document.querySelectorAll(".detail-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === tab);
  });
  document.querySelectorAll(".detail-pane").forEach((p) => {
    p.classList.toggle("active", p.dataset.pane === tab);
  });
}

export function bindTablesEvents() {
  $("table-search")?.addEventListener("input", (e) => {
    searchKeyword = e.target.value || "";
    renderTableTree();
  });

  document.querySelectorAll(".detail-tab").forEach((tab) => {
    tab.addEventListener("click", () => switchDetailTab(tab.dataset.tab));
  });

  window.addEventListener("db:open-schema", (e) => openSchema(e.detail.schema));
  window.addEventListener("db:open-table", (e) => openTable(e.detail.schema, e.detail.table));
}
