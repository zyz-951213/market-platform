/** 渠道 id → 完整 model 名（由 overview / catalog 填充） */
export let channelLabels = {};

export let overviewCache = null;

export function setOverviewCache(data) {
  overviewCache = data;
}

export function applyChannelCatalog(data) {
  const next = {};
  const channels = data?.llm_channels || data?.providers || [];
  if (Array.isArray(channels) && channels.length && typeof channels[0] === "object") {
    for (const ch of channels) {
      if (ch?.id) next[ch.id] = ch.label || ch.model || ch.id;
    }
  } else if (Array.isArray(data?.llm_providers)) {
    for (const id of data.llm_providers) next[id] = id;
  }
  if (data?.active_api && data?.active_model) {
    next[data.active_api] = data.active_model;
  }
  if (Object.keys(next).length) channelLabels = { ...channelLabels, ...next };
}
