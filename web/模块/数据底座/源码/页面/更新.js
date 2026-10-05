import { api } from "@/接口/客户端.js";
import { $, escapeHtml, fmtNum } from "@/公共/工具.js";

let rows = [];
let pollTimer = null;
let currentJobId = null;

const FRESH_CLASS = {
  可更新: "fresh-stale",
  已对齐: "fresh-ok",
  冻结: "fresh-frozen",
  不可调度: "fresh-muted",
  无数据: "fresh-stale",
  未知: "fresh-muted",
  已停用: "fresh-muted",
};

export async function loadUpdatePage() {
  await refreshConsole();
  await refreshJobs();
  bindUpdateEventsOnce();
}

async function refreshConsole() {
  const owner = $("upd-filter-owner")?.value || "";
  const tag = $("upd-filter-tag")?.value || "";
  const freshness = $("upd-filter-fresh")?.value || "";
  const sched = $("upd-filter-sched")?.checked ? "1" : "";
  const q = new URLSearchParams();
  if (owner) q.set("owner", owner);
  if (tag) q.set("tag", tag);
  if (freshness) q.set("freshness", freshness);
  if (sched) q.set("schedulable_only", "true");
  rows = await api(`/api/update/console?${q}`);
  renderConsole();
  renderTags();
}

function renderTags() {
  const sel = $("upd-filter-tag");
  if (!sel || sel.dataset.filled === "1") return;
  const tags = [...new Set(rows.map((r) => r.批量标签).filter(Boolean))].sort();
  const cur = sel.value;
  sel.innerHTML = `<option value="">全部标签</option>` + tags.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
  sel.value = cur;
  sel.dataset.filled = "1";
}

function renderConsole() {
  const body = $("upd-console-body");
  const meta = $("upd-console-meta");
  if (!body) return;
  const schedulable = rows.filter((r) => r.可调度).length;
  const stale = rows.filter((r) => r.新鲜度 === "可更新").length;
  if (meta) meta.textContent = `${rows.length} 条 · 可调度 ${schedulable} · 可更新 ${stale}`;

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="11" class="empty-hint">无匹配数据</td></tr>`;
    return;
  }

  body.innerHTML = rows
    .map((r) => {
      const key = `${r.数据源}|${r.数据集}`;
      const disabled = r.可调度 ? "" : "disabled";
      const fc = FRESH_CLASS[r.新鲜度] || "";
      return `<tr data-key="${escapeHtml(key)}">
        <td><input type="checkbox" class="upd-check" data-src="${escapeHtml(r.数据源)}" data-ds="${escapeHtml(r.数据集)}" ${disabled} ${r.可调度 ? "" : 'title="不可调度"'} /></td>
        <td>${escapeHtml(r.数据源)}</td>
        <td>${escapeHtml(r.数据集)}</td>
        <td>${escapeHtml(r.拉取源 || "—")}</td>
        <td>${escapeHtml(r.批量标签 || "—")}</td>
        <td>${escapeHtml(r.数据最后日 || "—")}</td>
        <td>${escapeHtml(r.期望最后日 || "—")}</td>
        <td class="num">${r.落后天数 == null ? "—" : fmtNum(r.落后天数)}</td>
        <td><span class="fresh-pill ${fc}">${escapeHtml(r.新鲜度 || "—")}</span></td>
        <td>${escapeHtml(r.最近成功时间 || "—")}</td>
        <td><button class="btn small primary upd-one" data-src="${escapeHtml(r.数据源)}" data-ds="${escapeHtml(r.数据集)}" ${disabled}>更新</button></td>
      </tr>`;
    })
    .join("");

  body.querySelectorAll(".upd-one").forEach((btn) => {
    btn.addEventListener("click", () =>
      startJob({
        targets: [{ 数据源: btn.dataset.src, 数据集: btn.dataset.ds }],
      })
    );
  });
}

async function refreshJobs() {
  const body = $("upd-jobs-body");
  if (!body) return;
  const jobs = await api("/api/update/jobs?limit=15");
  if (!jobs.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty-hint">暂无作业</td></tr>`;
    return;
  }
  body.innerHTML = jobs
    .map(
      (j) => `<tr data-job="${escapeHtml(j.运行编号)}">
      <td class="mono">${escapeHtml(String(j.运行编号).slice(0, 8))}…</td>
      <td>${escapeHtml(j.触发方式 || "")}</td>
      <td>${escapeHtml(j.范围说明 || "")}</td>
      <td>${escapeHtml(j.状态 || "")}</td>
      <td class="num">${fmtNum(j.成功数)}/${fmtNum(j.失败数)}</td>
      <td>${escapeHtml(j.开始时间 || "")}</td>
      <td><button class="btn small secondary upd-job-view" data-id="${escapeHtml(j.运行编号)}">明细</button></td>
    </tr>`
    )
    .join("");

  body.querySelectorAll(".upd-job-view").forEach((btn) => {
    btn.addEventListener("click", () => showJobDetail(btn.dataset.id));
  });
}

async function showJobDetail(jobId) {
  const log = $("upd-job-log");
  if (!log) return;
  const job = await api(`/api/update/jobs/${jobId}`);
  const lines = [
    `# ${job.运行编号}  ${job.状态}  成功${job.成功数} 失败${job.失败数}`,
    `范围: ${job.范围说明 || ""}`,
    "",
  ];
  for (const d of job.明细 || []) {
    const adv =
      d.水位是否推进 == null ? "" : d.水位是否推进 ? "推进" : "未推进";
    lines.push(
      `[${d.状态}] ${d.数据源}.${d.数据集}  ${d.水位前最后日 || "—"} → ${d.水位后最后日 || "—"} ${adv}`
    );
    if (d.消息) lines.push(`  ${d.消息}`);
    if (d.错误) lines.push(`  ERR ${d.错误}`);
  }
  log.textContent = lines.join("\n");
}

async function startJob(payload) {
  const log = $("upd-job-log");
  try {
    const res = await api("/api/update/jobs", {
      method: "POST",
      body: JSON.stringify({ ...payload, trigger: "前端" }),
    });
    currentJobId = res.运行编号;
    if (log) log.textContent += `\n已创建作业 ${currentJobId}（${res.范围说明}）\n`;
    startPoll();
    await refreshJobs();
  } catch (e) {
    alert(e.message);
  }
}

function startPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!currentJobId) return;
    try {
      const job = await api(`/api/update/jobs/${currentJobId}`);
      await showJobDetail(currentJobId);
      await refreshJobs();
      if (!["排队", "运行中"].includes(job.状态)) {
        clearInterval(pollTimer);
        pollTimer = null;
        await refreshConsole();
      }
    } catch {
      /* ignore transient */
    }
  }, 2000);
}

let bound = false;
function bindUpdateEventsOnce() {
  if (bound) return;
  bound = true;

  ["upd-filter-owner", "upd-filter-tag", "upd-filter-fresh"].forEach((id) => {
    $(id)?.addEventListener("change", () => refreshConsole());
  });
  $("upd-filter-sched")?.addEventListener("change", () => refreshConsole());

  $("upd-refresh-console")?.addEventListener("click", () => refreshConsole());
  $("upd-refresh-wm")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/update/watermarks/refresh", { method: "POST", body: "{}" });
      alert(r.ok ? "水位已刷新" : `刷新失败: ${r.stderr || r.stdout || r.exit}`);
      await refreshConsole();
    } catch (e) {
      alert(e.message);
    }
  });

  $("upd-select-stale")?.addEventListener("click", () => {
    document.querySelectorAll(".upd-check").forEach((c) => {
      const tr = c.closest("tr");
      const pill = tr?.querySelector(".fresh-pill");
      c.checked = !c.disabled && pill?.textContent === "可更新";
    });
  });

  $("upd-run-selected")?.addEventListener("click", () => {
    const targets = [...document.querySelectorAll(".upd-check:checked")].map((c) => ({
      数据源: c.dataset.src,
      数据集: c.dataset.ds,
    }));
    if (!targets.length) {
      alert("请先勾选可调度表");
      return;
    }
    startJob({ targets });
  });

  $("upd-run-stale")?.addEventListener("click", () => {
    startJob({ all_schedulable: true, only_stale: true });
  });

  $("upd-run-tag")?.addEventListener("click", () => {
    const tag = $("upd-filter-tag")?.value;
    if (!tag) {
      alert("请先在筛选里选择批量标签");
      return;
    }
    startJob({ tags: [tag], only_stale: $("upd-only-stale-tag")?.checked });
  });
}
