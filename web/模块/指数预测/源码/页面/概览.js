import * as echarts from "echarts";
import { api } from "@/接口/客户端.js";
import {
  $, fmtPct, fmtPctSigned, escapeHtml, retClass, scrollToEl,
} from "@/公共/工具.js";
import { baseGrid, chartTheme } from "@/公共/主题.js";
import {
  overviewCache, channelLabels, applyChannelCatalog, setOverviewCache,
} from "@/公共/状态.js";
import { renderStrategySummary } from "@/页面/回测.js";

let priceChart;
let previewCache = null;
let previewActiveTab = "overview";

export function resizeOverviewCharts() {
  priceChart?.resize();
}

function renderPriceChart(series) {
  const dates = series.dates || [];
  const closes = series.close || [];
  const lineData = dates.map((d, i) => [d, closes[i]]);

  if (priceChart) {
    priceChart.dispose();
    priceChart = null;
  }
  priceChart = echarts.init($("price-chart"));

  const range = series.date_range;
  if ($("price-hint") && range?.start && range?.end) {
    const unit = overviewCache?.bar_meta?.bar_unit || "日";
    const total = series.total_bars ?? dates.length;
    const scopeLabel = series.scope === "range"
      ? "指定区间"
      : series.scope === "recent"
        ? `近 ${total} 根`
        : "全部行情";
    $("price-hint").textContent = `${scopeLabel} · ${range.start} → ${range.end}（${total} 根${unit === "日" ? "日K" : unit}）`;
  }

  priceChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { ...baseGrid(), bottom: 48 },
    dataZoom: [
      {
        type: "inside",
        start: 0,
        end: 100,
        minValueSpan: 5,
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
      },
      {
        type: "slider",
        height: 18,
        bottom: 4,
        start: 0,
        end: 100,
        minValueSpan: 5,
        showDetail: true,
      },
    ],
    xAxis: {
      type: "time",
      axisLabel: chartTheme.axis,
      splitLine: { show: false },
    },
    yAxis: {
      scale: true,
      axisLabel: chartTheme.axis,
      splitLine: { lineStyle: { color: chartTheme.split } },
    },
    series: [{
      name: "收盘价",
      type: "line",
      smooth: true,
      showSymbol: false,
      data: lineData,
      lineStyle: { color: "#4c8bf5", width: 2 },
      areaStyle: { color: "rgba(76,139,245,0.12)" },
    }],
  }, { notMerge: true });
}
export function switchPreviewTab(tab) {
  previewActiveTab = tab;
  document.querySelectorAll(".preview-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".preview-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.pane === tab);
  });
  const body = $("preview-body");
  if (body) {
    body.classList.toggle("news-pane-active", tab === "news");
    body.classList.toggle("raw-pane-active", tab === "raw");
  }
}

function buildPreviewTabDefs(data) {
  const tabs = [
    { id: "overview", label: "概览" },
    { id: "trend", label: "走势" },
  ];
  (data?.dimensions || []).forEach((d) => {
    tabs.push({ id: `dim-${d.key}`, label: d.title });
  });
  tabs.push(
    { id: "overseas", label: "外盘" },
    { id: "news", label: "宏观新闻" },
  );
  // 技术面留给本地 Transformer，默认不在 LLM 研判窗口展示
  const showTech = data?.meta?.include_technical === true
    && Array.isArray(data?.technical)
    && data.technical.length > 0;
  if (showTech) {
    tabs.push({ id: "technical", label: "技术面" });
  }
  tabs.push({ id: "raw", label: "完整 Prompt" });
  return tabs;
}

function ensurePreviewShell(data) {
  const tabs = buildPreviewTabDefs(data);
  const tabIds = new Set(tabs.map((t) => t.id));
  if (!tabIds.has(previewActiveTab)) previewActiveTab = "overview";

  $("preview-tabs").innerHTML = tabs.map((t) => `
    <button type="button" class="preview-tab${t.id === previewActiveTab ? " active" : ""}" data-tab="${t.id}">
      ${escapeHtml(t.label)}
    </button>
  `).join("");

  const panes = tabs.map((t) => `
    <div class="preview-pane${t.id === previewActiveTab ? " active" : ""}" data-pane="${t.id}" id="preview-pane-${t.id}"></div>
  `).join("");
  $("preview-body").innerHTML = panes;
  switchPreviewTab(previewActiveTab);
}

function renderPreviewMeta(data) {
  const meta = data?.meta || {};
  const strat = data?.strategy || {};
  const chips = [
    ["标的", meta.index_name || "--"],
    ["决策日", meta.date || "--"],
    ["收盘", meta.close != null ? Number(meta.close).toFixed(2) : "--"],
    ["交易主线", strat.headline || "趋势定底仓 · 研判调叠加"],
    ["信号", `日频涨跌平 ±${meta.daily_threshold_pct ?? "--"}%`],
    ["视野", meta.predict_horizon || "下一交易日"],
    ["模型", meta.active_model || strat.active_model || "--"],
    ["模板", strat.prompt_label || meta.prompt_version || "--"],
  ];
  if (meta.regime_router) {
    chips.push(["状态提示", meta.regime_zh || meta.regime || "路由开"]);
  }
  if (meta.date_aligned && meta.requested_date) {
    chips.push(["日期对齐", `${meta.requested_date} → ${meta.date_aligned}`]);
  }
  if (meta.actual_text) {
    chips.push(["当日标签", meta.actual_text]);
  }
  $("preview-meta").innerHTML = chips.map(([label, value]) => {
    const cls = label === "当日标签" ? "preview-chip warn"
      : (label === "交易主线" || label === "模板") ? "preview-chip accent" : "preview-chip";
    return `<div class="${cls}">${label}<strong>${escapeHtml(String(value))}</strong></div>`;
  }).join("");
}

function renderPreviewOverview(data) {
  const meta = data?.meta || {};
  const strat = data?.strategy || {};
  const cls = strat.classification || {};
  const trend = data?.trend || {};
  const dims = data?.dimensions || [];
  const overseas = data?.overseas?.indices || [];
  const news = data?.news;
  const factorCount = dims.reduce((n, d) => n + (d.factors?.length || 0), 0);
  const dailyTh = cls.daily_threshold_pct ?? meta.daily_threshold_pct ?? "--";
  const tags = (strat.tags || []).slice(0, 8)
    .map((t) => `<span class="strategy-tag">${escapeHtml(t)}</span>`)
    .join("");
  const showTech = meta.include_technical === true;

  const pct = (v) => (v != null ? `${(Number(v) * 100).toFixed(0)}%` : "--");
  const classifyHtml = `<ul>
        <li><strong>交易侧：</strong>${escapeHtml(strat.philosophy || strat.headline || "趋势定底仓 · 研判调叠加")}</li>
        <li>底仓 ${pct(strat.base_position)}；牛/熊 ${pct(strat.bull_regime_base)}/${pct(strat.bear_regime_base)}（相对 SMA60）</li>
        <li>仓位：${strat.position_cycle ? "底仓 + 研判叠加 · 阶梯建仓；减仓需结构性确认" : "底仓 + 研判叠加"}</li>
        <li><strong>信号侧：</strong>LLM（基本面/外盘/新闻）× Transformer（量价）· 日频涨/跌/平 ±${dailyTh}%</li>
        <li>模型 ${escapeHtml(meta.active_model || strat.active_model || "--")} · ${escapeHtml(strat.prompt_label || meta.prompt_version || "--")}</li>
        <li>状态路由：${meta.regime_router ? `开（${escapeHtml(meta.regime_zh || meta.regime || "--")}）` : "关"} · 量价不进 LLM Prompt</li>
        <li>Transformer：${escapeHtml(strat.transformer_status || (strat.transformer_enabled ? "启用（量价通道）" : "未启用（量价通道闲置）"))}</li>
      </ul>`;

  const notesHtml = (strat.notes || []).length
    ? `<ul>${strat.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>`
    : `<p class="empty-hint">暂无策略说明</p>`;

  $("preview-pane-overview").innerHTML = `
    <div class="preview-grid">
      <div class="preview-card">
        <h3>基本信息</h3>
        <ul>
          <li>指数代码：${escapeHtml(meta.index_code || "--")}</li>
          <li>决策日：${escapeHtml(meta.date || "--")}</li>
          <li>最新收盘：${meta.close != null ? Number(meta.close).toFixed(2) : "--"}</li>
          <li>信号回看：近 ${trend.lookback || meta.lookback || 0} 根日线</li>
        </ul>
        ${tags ? `<div class="strategy-summary-tags" style="margin-top:8px">${tags}</div>` : ""}
      </div>
      <div class="preview-card">
        <h3>策略口径</h3>
        ${classifyHtml}
      </div>
      <div class="preview-card">
        <h3>数据覆盖（信号输入）</h3>
        <ul>
          <li>七维因子：${dims.length} 维 / ${factorCount} 项</li>
          <li>外盘指数：${overseas.length} 个</li>
          <li>宏观新闻：${news ? `${news.items?.length ?? news.count ?? 0} 条` : "暂无"}</li>
          <li>技术面：${showTech ? "进 LLM（非常规）" : "不进 LLM · 交给 Transformer"}</li>
        </ul>
      </div>
      <div class="preview-card">
        <h3>趋势摘要</h3>
        ${(trend.summary || []).length
    ? `<ul>${trend.summary.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>`
    : `<p class="empty-hint">暂无趋势摘要</p>`}
      </div>
      <div class="preview-card">
        <h3>策略说明</h3>
        ${notesHtml}
      </div>
    </div>
    ${data.task ? `<div class="preview-card" style="margin-top:10px"><h3>信号任务（给 LLM）</h3><p>${escapeHtml(data.task)}</p></div>` : ""}
  `;
}

function renderPreviewTrend(data) {
  const bars = data?.trend?.bars || [];
  if (!bars.length) {
    $("preview-pane-trend").innerHTML = `<p class="empty-hint">暂无走势数据</p>`;
    return;
  }
  const rows = bars.map((b) => {
    const ret = b.return_pct;
    const retText = ret == null ? "—" : `${(ret * 100).toFixed(2)}%`;
    return `
      <tr class="${b.is_current ? "current" : ""}">
        <td>${escapeHtml(b.date)}${b.is_current ? " · 当前" : ""}</td>
        <td>${b.close != null ? b.close.toFixed(2) : "--"}</td>
        <td class="${retClass(ret)}">${retText}</td>
      </tr>
    `;
  }).join("");
  const summary = (data.trend.summary || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  $("preview-pane-trend").innerHTML = `
    <table class="trend-table">
      <thead><tr><th>日期</th><th>收盘</th><th>涨跌</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${summary ? `<ul style="margin-top:12px;padding-left:18px;font-size:12px;color:var(--muted)">${summary}</ul>` : ""}
  `;
}

function renderFactorGrid(items) {
  if (!items?.length) return `<p class="empty-hint">暂无数据</p>`;
  return `<div class="factor-grid">${items.map((f) => `
    <div class="factor-item">
      <span>${escapeHtml(f.label)}</span>
      <strong>${escapeHtml(f.display)}${escapeHtml(f.unit || "")}</strong>
    </div>
  `).join("")}</div>`;
}

function renderPreviewDimension(dim) {
  const pane = $(`preview-pane-dim-${dim.key}`);
  if (!pane) return;
  pane.innerHTML = `
    <div class="factor-section">
      <h3>${escapeHtml(dim.title)}</h3>
      ${renderFactorGrid(dim.factors)}
    </div>
  `;
}

function renderOverseasIndex(idx) {
  const ret1d = idx.return_1d != null ? `${(idx.return_1d * 100).toFixed(2)}%` : "—";
  const winRet = idx.window_return != null ? `${(idx.window_return * 100).toFixed(2)}%` : "—";
  const bars = idx.bars || [];
  const rows = bars.map((b) => {
    const ret = b.return_pct;
    const retText = ret == null ? "—" : `${(ret * 100).toFixed(2)}%`;
    return `
      <tr>
        <td>${escapeHtml(b.date)}</td>
        <td>${b.close != null ? b.close.toFixed(2) : "--"}</td>
        <td class="${retClass(ret)}">${retText}</td>
      </tr>
    `;
  }).join("");

  return `
    <div class="overseas-index-block">
      <h3>${escapeHtml(idx.name)}</h3>
      <div class="overseas-summary">
        <div class="overseas-chip">收盘<strong>${idx.close != null ? idx.close.toFixed(2) : "--"}</strong></div>
        <div class="overseas-chip">日涨跌<strong class="${retClass(idx.return_1d)}">${ret1d}</strong></div>
        <div class="overseas-chip">窗口累计<strong class="${retClass(idx.window_return)}">${winRet}</strong></div>
        <div class="overseas-chip">序列<strong>${bars.length} 根</strong></div>
      </div>
      ${bars.length
    ? `<table class="trend-table">
          <thead><tr><th>日期</th><th>收盘</th><th>涨跌</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`
    : `<p class="empty-hint">暂无${escapeHtml(idx.name)}序列数据</p>`}
    </div>
  `;
}

function renderPreviewOverseas(data) {
  const indices = data?.overseas?.indices || [];
  const pane = $("preview-pane-overseas");
  if (!pane) return;
  if (!indices.length) {
    pane.innerHTML = `<p class="empty-hint">暂无外盘数据</p>`;
    return;
  }
  pane.innerHTML = indices.map(renderOverseasIndex).join("");
}

function renderPreviewNews(data) {
  const news = data?.news;
  const pane = $("preview-pane-news");
  if (!pane) return;
  if (!news) {
    pane.innerHTML = `<p class="empty-hint">暂无宏观新闻</p>`;
    return;
  }

  const items = news.items || [];
  const rows = items.map((n) => `
    <tr>
      <td>${escapeHtml(n.region || "—")}</td>
      <td class="event">${escapeHtml(n.event || "—")}</td>
      <td class="num">${escapeHtml(n.importance || "—")}</td>
      <td class="num">${escapeHtml(n.actual || "—")}</td>
      <td class="num">${escapeHtml(n.forecast || "—")}</td>
    </tr>
  `).join("");

  pane.innerHTML = `
    <div class="factor-section">
      <h3>宏观新闻与事件（${items.length} 条）</h3>
      ${news.summary
    ? `<div class="news-box" style="margin-bottom:12px">
          <div class="meta" style="margin-bottom:6px">Prompt 摘要（仅前几条，完整列表见下表）</div>
          ${escapeHtml(news.summary)}
        </div>`
    : ""}
      ${items.length
    ? `<table class="news-table">
          <thead>
            <tr><th>地区</th><th>事件</th><th>重要性</th><th>公布</th><th>预期</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>`
    : `<p class="empty-hint">该日缓存中无新闻明细</p>`}
    </div>
  `;
}

function renderPreviewTechnical(data) {
  $("preview-pane-technical").innerHTML = `
    <div class="factor-section">
      <h3>技术面（当期）</h3>
      ${renderFactorGrid(data?.technical || [])}
    </div>
  `;
}

function renderPreviewRaw(data) {
  $("preview-pane-raw").innerHTML = `<pre class="prompt-box">${escapeHtml(data?.prompt || "")}</pre>`;
}

function renderPreview(data) {
  if (!data) return;
  previewCache = data;
  ensurePreviewShell(data);
  renderPreviewMeta(data);
  renderPreviewOverview(data);
  renderPreviewTrend(data);
  (data.dimensions || []).forEach(renderPreviewDimension);
  renderPreviewOverseas(data);
  renderPreviewNews(data);
  if (data?.meta?.include_technical === true && $("preview-pane-technical")) {
    renderPreviewTechnical(data);
  }
  renderPreviewRaw(data);
}
function applyBarMeta(meta) {
  if (!meta) return;
  if ($("page-title")) $("page-title").textContent = `Market Predictor · ${meta.subtitle}`;
  if ($("page-subtitle")) $("page-subtitle").textContent = meta.subtitle;
  if ($("analyze-date-label")) {
    $("analyze-date-label").textContent = `决策日（${meta.analyze_date_hint || "展示用"}）`;
  }
  renderAnalysisReadonly(meta);
}

function renderAnalysisReadonly(meta, overview) {
  const box = $("analysis-result");
  if (!box) return;
  const ss = overview?.strategy_summary || overviewCache?.strategy_summary || {};
  const model = ss.active_model || overview?.active_model || overviewCache?.active_model || "--";
  const prompt = ss.prompt_label || ss.prompt_version || meta?.prompt_version || "--";
  const cls = ss.classification || {};
  const th = cls.daily_threshold_pct ?? meta?.daily_threshold_pct ?? "--";
  const philosophy = ss.philosophy
    || "长期价投：趋势定底仓，研判调叠加，结构性风险才减仓，过热兑现留弹药";
  box.classList.remove("empty", "highlight");
  box.innerHTML = `
    <div class="meta">交易主线（只读）</div>
    <div><strong>${escapeHtml(philosophy)}</strong></div>
    <div style="margin-top:8px">信号层：${escapeHtml(String(model))} · ${escapeHtml(String(prompt))}</div>
    <div>信号：LLM × Transformer 综合评价 · 平盘 ±${escapeHtml(String(th))}% · 量价不进 LLM</div>
    <div class="meta" style="margin-top:8px">${escapeHtml(meta?.analysis_intro || "仓位由底仓+叠加与阶梯规则决定，研判窗口仅展示信号输入。")}</div>
    <a class="jump-link" id="jump-prompt-link">查看研判信息窗口 ↓</a>
  `;
  $("jump-prompt-link")?.addEventListener("click", (e) => {
    e.preventDefault();
    scrollToEl("prompt-panel");
  });
}

function renderOverview(data) {
  setOverviewCache(data);
  applyBarMeta(data.bar_meta);
  renderAnalysisReadonly(data.bar_meta, data);
  $("stat-close").textContent = data.latest?.close?.toFixed(2) ?? "--";
  $("stat-range").textContent = `${data.date_range.start} → ${data.date_range.end}`;
  const dateInput = $("analyze-date");
  const asOf = data.data_as_of || data.latest?.date;
  if (dateInput && asOf) {
    dateInput.min = data.date_range?.start;
    dateInput.max = asOf;
    if (!dateInput.value) dateInput.value = asOf;
  }
  const unit = data.bar_meta?.sample_unit || "个样本";
  if ($("stat-samples")) $("stat-samples").textContent = `${data.labeled_bars} ${unit}`;
  if ($("stat-features")) $("stat-features").textContent = `${data.feature_columns || "--"}`;
  if (data.backtest_stale) {
    // 即使 stale 也显示已有回测数据，加 * 提示
    const bt = data.backtest;
    const staleMark = " *";
    $("stat-annual").textContent = bt ? `${fmtPct(bt.annualized_return)}${staleMark}` : "待更新";
    if (bt?.cash_mgmt_enabled) {
      const parts = [
        `综合总收益 ${fmtPct(bt.total_return)}`,
        `策略净收益 ${fmtPct(bt.strategy_only_return)}`,
        `闲置资金 ${fmtPctSigned(bt.cash_return)}`,
        bt.excess_return != null ? `超额 ${fmtPctSigned(bt.excess_return)}` : null,
        "回测缓存来自旧配置（*）",
      ].filter(Boolean);
      $("stat-annual").title = parts.join(" · ");
    } else {
      $("stat-annual").title = bt ? `总收益 ${fmtPct(bt.total_return)} · 回测缓存来自旧配置（*）` : "";
    }
    $("stat-annual").classList.add("stale");
  } else {
    const bt = data.backtest;
    $("stat-annual").textContent = bt ? fmtPct(bt.annualized_return) : "--";
    if (bt?.cash_mgmt_enabled) {
      const parts = [
        `综合总收益 ${fmtPct(bt.total_return)}`,
        `策略净收益 ${fmtPct(bt.strategy_only_return)}`,
        `闲置资金 ${fmtPctSigned(bt.cash_return)}`,
        bt.excess_return != null ? `超额 ${fmtPctSigned(bt.excess_return)}` : null,
      ].filter(Boolean);
      $("stat-annual").title = parts.join(" · ");
    } else {
      $("stat-annual").title = bt ? `总收益 ${fmtPct(bt.total_return)}` : "";
    }
    $("stat-annual").classList.remove("stale");
  }
  if (data.strategy_summary) {
    renderStrategySummary(data.strategy_summary);
  }
  const latest = data.latest || {};
  $("stat-overseas").textContent = latest.spx_ret_1d != null
    ? `标普 ${(latest.spx_ret_1d * 100).toFixed(2)}%`
    : "暂无";
  if ($("stat-news")) $("stat-news").textContent = latest.has_news ? "已接入" : "暂无";
  applyChannelCatalog(data);
  $("provider-badge").textContent = data.latest_backtest_model
    || data.active_model
    || (data.llm_provider === "api" ? "LLM API" : data.llm_provider);
  fillProviderSelect(data);

  // 策略配置条
  const ss = data.strategy_summary || {};
  const cls = ss.classification || {};
  $("os-api").textContent = ss.active_model || data.active_model || "--";
  $("os-prompt").textContent = ss.prompt_label || ss.prompt_version || "--";
  $("os-freq").textContent = "价投仓位制";
  if ($("os-class")) {
    $("os-class").textContent = `信号±${cls.daily_threshold_pct ?? data.bar_meta?.daily_threshold_pct ?? "--"}%`;
  }
  $("os-mode").textContent = ss.position_cycle ? "底仓+阶梯叠加" : "底仓+叠加";
  $("os-base").textContent = ss.base_position != null
    ? `${(ss.base_position * 100).toFixed(0)}%` : "--";
  $("os-calib").textContent = ss.return_calibration ? "开" : "关";
  $("os-cash").textContent = ss.allocation_mode === "unified_sharpe"
    ? "统一夏普六袖"
    : ss.cash_management?.enabled
    ? (ss.three_layer_allocation ? "三层闲置" : "开") : "关";
  if ($("os-mode") && ss.allocation_mode === "unified_sharpe") {
    $("os-mode").textContent = "多资产配置";
  }
  if ($("os-base") && ss.allocation_mode === "unified_sharpe") {
    $("os-base").textContent = "组间夏普";
  }
}
export async function loadOverview() {
  const data = await api("/api/overview");
  renderOverview(data);
  return data;
}
export async function loadPriceSeries() {
  const series = await api("/api/price-series");
  if (!series?.dates?.length) {
    throw new Error("价格数据为空，请先运行 data 步骤");
  }
  renderPriceChart(series);
}

export async function loadPromptPreview() {
  const date = $("analyze-date")?.value || undefined;
  const q = date ? `?date=${date}` : "";
  const data = await api(`/api/prompt-preview${q}`);
  renderPreview(data);
  if ($("preview-date-hint")) {
    $("preview-date-hint").textContent = data?.meta?.date
      ? `当前展示：${data.meta.date}`
      : "与上方决策日联动";
  }
}
export function fillProviderSelect(data) {
  applyChannelCatalog(data);
  // 仅 API 成员渠道；排除 ensemble / llm_factor 等元模型
  const metaIds = new Set(["ensemble", "llm_factor_hgb", "llm_factor"]);
  const raw = data?.llm_providers || (data?.llm_channels || []).map((c) => c.id) || [];
  const providers = raw.filter((id) => id && !metaIds.has(id) && !String(id).startsWith("llm_factor"));
  const active = data?.active_api || "";
  const fill = (sel, { preferActive = false } = {}) => {
    if (!sel) return;
    const prev = sel.value;
    const opts = [];
    // 展示已启用的 API 成员渠道（海外 tuzi_* 默认 enabled:false，不会出现）
    const filtered = providers.filter((id) => !String(id).startsWith("tuzi_"));
    if (!filtered.length) {
      opts.push(`<option value="">暂无渠道</option>`);
    } else {
      for (const id of filtered) {
        const label = channelLabels[id] || id;
        opts.push(`<option value="${id}">${label}</option>`);
      }
    }
    sel.innerHTML = opts.join("");
    if (prev && filtered.includes(prev)) sel.value = prev;
    else if (preferActive && active && filtered.includes(active)) sel.value = active;
    else if (active && filtered.includes(active)) sel.value = active;
    else if (filtered.length) sel.value = filtered[0];
  };
  // 回测/归因：列出全部国内渠道，不设「主渠道 latest」空选项
  fill($("backtest-provider"));
  fill($("paper-provider"), { preferActive: true });
  fill($("attribution-provider"));
}
