export const $ = (id) => document.getElementById(id);

export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function fmtNum(v) {
  if (v == null || Number.isNaN(Number(v))) return "--";
  return Number(v).toLocaleString("zh-CN");
}

export function fmtBytes(n) {
  if (vNull(n)) return "--";
  const v = Number(n);
  if (v < 1024) return `${v} B`;
  if (v < 1024 ** 2) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1024 ** 3) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  return `${(v / 1024 ** 3).toFixed(2)} GB`;
}

function vNull(v) {
  return v == null || Number.isNaN(Number(v));
}

export function switchPage(pageId) {
  document.querySelectorAll(".navbar-tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  const tab = document.querySelector(`.navbar-tab[data-page="${pageId}"]`);
  const page = $(pageId);
  if (tab) tab.classList.add("active");
  if (page) page.classList.add("active");
}

/** 渲染通用结果表
 * options: { sortable, sortCol, sortDir, onSort }
 */
export function renderDataTable(container, columns, rows, emptyText = "无数据", options = {}) {
  if (!container) return;
  const { sortable = false, sortCol = null, sortDir = null, onSort = null } = options;
  if (!columns?.length) {
    container.innerHTML = `<p class="empty-hint">${escapeHtml(emptyText)}</p>`;
    return;
  }
  const head = columns
    .map((c) => {
      if (!sortable) return `<th>${escapeHtml(c)}</th>`;
      const active = sortCol === c;
      const arrow = active ? (String(sortDir).toUpperCase() === "ASC" ? "↑" : "↓") : "";
      const cls = active ? "sortable sorted" : "sortable";
      return `<th class="${cls}" data-col="${escapeHtml(c)}" title="点击排序：${escapeHtml(c)}">
        <span class="th-label">${escapeHtml(c)}</span>
        <span class="th-sort">${arrow || "⇅"}</span>
      </th>`;
    })
    .join("");
  const body = rows.length
    ? rows
        .map(
          (r) =>
            `<tr>${columns
              .map((c) => {
                const v = r[c] ?? "";
                const s = String(v);
                return `<td title="${escapeHtml(s)}">${escapeHtml(s)}</td>`;
              })
              .join("")}</tr>`
        )
        .join("")
    : `<tr><td colspan="${columns.length}" class="empty-hint">${escapeHtml(emptyText)}</td></tr>`;
  container.innerHTML = `<table class="${sortable ? "sortable-table" : ""}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;

  if (sortable && typeof onSort === "function") {
    container.querySelectorAll("th.sortable").forEach((th) => {
      th.addEventListener("click", () => onSort(th.dataset.col));
    });
  }
}
