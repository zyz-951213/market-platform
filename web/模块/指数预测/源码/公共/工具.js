import { overviewCache } from "@/公共/状态.js";

export const $ = (id) => document.getElementById(id);

export function fmtPct(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return `${(v * 100).toFixed(2)}%`;
}

export function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return Number(v).toFixed(digits);
}

export function directionClass(dir) {
  if (dir === "涨") return "dir-up";
  if (dir === "跌") return "dir-down";
  return "dir-flat";
}

export function fmtPctSigned(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  const pct = (v * 100).toFixed(2);
  return v > 0 ? `+${pct}%` : `${pct}%`;
}

export function fmtChartDate(d) {
  if (!d) return "";
  return String(d).slice(0, 10);
}

export function signedClass(v) {
  if (v == null || Number.isNaN(v)) return "";
  if (v > 0) return "positive";
  if (v < 0) return "negative";
  return "";
}

export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function retClass(v) {
  if (v == null || Number.isNaN(v)) return "flat";
  if (v > 0) return "up";
  if (v < 0) return "down";
  return "flat";
}

export function scrollToEl(id) {
  const el = $(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function freqLabel(freq, meta) {
  const m = meta || overviewCache?.bar_meta || {};
  if (m.weekly_decision || m.decision_freq === "weekly" || freq === "weekly") return "周频";
  if (freq === "daily" || m.decision_freq === "daily") return "日频仓位";
  return "未知";
}

export function fmtMoney(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function fmtNum2(v, d) {
  if (v == null || Number.isNaN(v)) return "--";
  return Number(v).toFixed(d ?? 2);
}
