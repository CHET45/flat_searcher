"use strict";

let bridge = null;
let syncPollTimer = null;
let aiQueueRefreshTimer = null;
let lastQueuePanelSig = null;
let aiLiveTimer = null;
let lastAiSeq = -1;

const state = {
  view: "ranking",
  tab: "all",
  profileKey: "",
  profileName: "",
  language: "en",
  search: "",
  sort: "score_desc",
  filters: defaultFilters(),
  rows: [],
  markers: [],
  referencePoints: [],
  mapCoverage: { visible: 0, geocoded: 0 },
  summary: {},
  openRowId: null,
  detailId: null,
  detailReturnView: "ranking",
  detailCache: {},
  comparisonIds: [],
  strings: {},
  profiles: [],
  sessions: [],
  districts: [],
  filterBounds: defaultFilterBounds(),
  languages: [],
  syncBusy: false,
  pendingReload: false,
  hasRendered: false,
  pipelineStatus: null,
  aiQueueOpen: false,
  aiQueueLoaded: false,
  aiQueue: [],
  aiAnalyzedOptions: [],
  aiCurrentListingId: null,
  aiAnalyzing: [],
  aiAnalyzingIds: new Set(),
  aiBusy: false,
  aiPaused: false,
};

let map = null;
let mapLayer = null;
let mapMarkerIndex = {};

const NAV = [
  { id: "ranking", tab: "all", icon: "analytics", label: "nav.ranking" },
  { id: "map", icon: "map", label: "nav.map" },
  { id: "new", tab: "new", icon: "add_circle", label: "nav.new" },
  { id: "favorites", tab: "favorites", icon: "favorite", label: "nav.favorites" },
  { id: "rejected", tab: "rejected", icon: "block", label: "nav.rejected" },
  { id: "inactive", tab: "inactive", icon: "archive", label: "nav.inactive" },
  { id: "comparison", icon: "compare_arrows", label: "nav.comparison" },
];

const SORTS = [
  { key: "score_desc", label: "sort.score_desc" },
  { key: "price_asc", label: "sort.price_asc" },
  { key: "price_desc", label: "sort.price_desc" },
  { key: "area_desc", label: "sort.area_desc" },
];

const IMPORTANCE = [
  { value: "Ignore", short: "importance.ignore", cls: "ignore" },
  { value: "Weak factor", short: "importance.weak", cls: "weak" },
  { value: "Medium factor", short: "importance.medium", cls: "medium" },
  { value: "Strong factor", short: "importance.strong", cls: "strong" },
  { value: "Critical factor", short: "importance.critical", cls: "critical" },
];

function defaultFilters() {
  return {
    price_min: null, price_max: null, area_min: null, area_max: null,
    district: null,
    declared_rooms: null, effective_private_rooms: null,
    declared_rooms_min: null, declared_rooms_max: null,
    effective_private_rooms_min: null, effective_private_rooms_max: null,
    only_confirmed_layout: false, only_without_room_conflict: false,
    only_with_floor_plan: false, only_good_transport: false,
    only_near_rtu: false, only_near_central_station: false,
    hide_high_mortgage_risk: false, hide_stove_heating: false,
    hide_wooden_buildings: false, hide_viewed: false,
  };
}

function defaultFilterBounds() {
  return {
    price: { min: 0, max: 300000, step: 5000 },
    area: { min: 0, max: 160, step: 1 },
    ssRooms: { min: 1, max: 6, step: 1 },
    aiRooms: { min: 1, max: 6, step: 1 },
  };
}

/* ===================== Bridge plumbing ===================== */
function call(method, ...args) {
  return new Promise((resolve) => {
    bridge[method](...args, (result) => resolve(result));
  });
}
async function callJson(method, ...args) {
  const raw = await call(method, ...args);
  return raw ? JSON.parse(raw) : null;
}
function t(key) { return state.strings[key] || key; }
function tf(key, values) {
  let text = t(key);
  Object.entries(values || {}).forEach(([name, value]) => {
    text = text.replaceAll(`{${name}}`, value == null ? "" : String(value));
  });
  return text;
}
function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function el(id) { return document.getElementById(id); }

/* ===================== Boot ===================== */
new QWebChannel(qt.webChannelTransport, (channel) => {
  bridge = channel.objects.bridge;
  bridge.syncFinished.connect(onSyncFinished);
  bridge.syncFailed.connect(onSyncFailed);
  if (bridge.aiFinished) bridge.aiFinished.connect(onAIFinished);
  if (bridge.aiFailed) bridge.aiFailed.connect(onAIFailed);
  if (bridge.scoresReady) bridge.scoresReady.connect(onScoresReady);
  if (bridge.aiRefresh) bridge.aiRefresh.connect(onAIRefresh);
  bridge.pipelineProgress.connect(onPipelineProgress);
  boot();
});

async function boot() {
  setAppLoading(true, "Loading application…", "Preparing filters and rankings.");
  try {
    const data = await callJson("bootstrap");
    state.strings = data.strings || {};
    state.profiles = data.profiles || [];
    state.sessions = data.sessions || [];
    state.districts = data.districts || [];
    state.filterBounds = normalizeFilterBounds(data.filterBounds);
    state.languages = data.languages || [];
    state.language = data.language;
    state.profileKey = data.activeProfile;
    state.profileName = nameForProfile(state.profileKey);
    setAppLoading(true, t("common.loading"), t("status.loading_data"));
    applyStaticI18n();
    renderNav();
    renderSort();
    renderLang();
    bindTopbar();
    await loadAIQueue();
    updateQueueButton();
    if (state.aiQueueOpen) {
      renderAIQueuePanel();
    }
    await reload();
    // reload() has rendered and dropped the blocking overlay by now; the app is
    // interactive even if a background recalc/analysis is still running.
    console.info("flat-searcher UI ready");
    await ensureAIQueueRunning();
    startAILivePolling();
  } catch (error) {
    setAppLoading(true, "Failed to load application", String(error));
    throw error;
  }
}

function setAppLoading(active, title, text) {
  const overlay = el("appLoading");
  const app = el("app");
  // Only the very first load (before anything has rendered) is allowed to show
  // the full blocking overlay -- there is genuinely nothing to interact with
  // yet. Once the app has rendered once, every later load (profile recalc,
  // background pipeline refresh, sync) keeps the UI fully interactive and shows
  // a subtle non-blocking progress bar instead, so the user is never locked out.
  const blocking = active && !state.hasRendered;
  if (overlay) {
    if (title && el("appLoadingTitle")) el("appLoadingTitle").textContent = title;
    if (text && el("appLoadingText")) el("appLoadingText").textContent = text;
    overlay.hidden = !blocking;
  }
  setBusyBar(active && state.hasRendered);
  if (app) app.setAttribute("aria-busy", active ? "true" : "false");
}

function setBusyBar(active) {
  const bar = el("appBusyBar");
  if (bar) bar.hidden = !active;
}

function normalizeFilterBounds(raw) {
  const fallback = defaultFilterBounds();
  const result = {};
  Object.entries(fallback).forEach(([key, value]) => {
    const incoming = raw && raw[key] ? raw[key] : {};
    const min = Number.isFinite(Number(incoming.min)) ? Number(incoming.min) : value.min;
    let max = Number.isFinite(Number(incoming.max)) ? Number(incoming.max) : value.max;
    if (max <= min) max = min + value.step;
    result[key] = {
      min,
      max,
      step: Number.isFinite(Number(incoming.step)) ? Number(incoming.step) : value.step,
    };
  });
  return result;
}

function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.getAttribute("data-i18n"));
  });
  el("searchInput").placeholder = t("search.placeholder");
  el("filtersBtn").title = t("action.filters");
  el("syncBtn").title = t("action.sync");
}

function nameForProfile(key) {
  const found = state.profiles.find((p) => p.key === key);
  return found ? found.name : key;
}

/* ===================== Chrome render ===================== */
function renderNav() {
  const nav = el("nav");
  nav.innerHTML = "";
  NAV.forEach((item, index) => {
    if (index === 1 || index === 6) {
      const sep = document.createElement("div");
      sep.className = "nav-sep";
      nav.appendChild(sep);
    }
    const btn = document.createElement("button");
    btn.className = "nav-item" + (isNavActive(item) ? " active" : "");
    const badge = item.id === "comparison" && state.comparisonIds.length
      ? `<span class="nav-count">${state.comparisonIds.length}</span>` : "";
    btn.innerHTML = `<span class="material-symbols-outlined ${isNavActive(item) ? "fill" : ""}">${item.icon}</span><span>${esc(t(item.label))}</span>${badge}`;
    btn.onclick = () => onNav(item);
    nav.appendChild(btn);
  });
}

function isNavActive(item) {
  if (state.view === "ranking" && item.id !== "map" && item.id !== "comparison") {
    return state.tab === (item.tab || "all");
  }
  if (state.view === "map") return item.id === "map";
  if (state.view === "comparison") return item.id === "comparison";
  if (state.view === "detail") return state.tab === (item.tab || "all") && item.id !== "map" && item.id !== "comparison" && item.id === "ranking";
  return false;
}

function onNav(item) {
  if (item.id === "map") { state.view = "map"; }
  else if (item.id === "comparison") { state.view = "comparison"; }
  else { state.view = "ranking"; state.tab = item.tab || "all"; state.openRowId = null; }
  renderNav();
  reload();
}

function renderSort() {
  const sel = el("sortSelect");
  sel.innerHTML = "";
  SORTS.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.key;
    opt.textContent = t(s.label);
    sel.appendChild(opt);
  });
  sel.value = state.sort;
  sel.onchange = () => { state.sort = sel.value; renderCurrent(); };
}

function renderLang() {
  const sel = el("langSelect");
  sel.innerHTML = "";
  state.languages.forEach((lang) => {
    const opt = document.createElement("option");
    opt.value = lang.code;
    opt.textContent = lang.label;
    sel.appendChild(opt);
  });
  sel.value = state.language;
  sel.onchange = async () => {
    await call("setLanguage", sel.value);
    await boot();
  };
}

function bindTopbar() {
  el("filtersBtn").onclick = openDrawer;
  el("syncBtn").onclick = () => startSync();
  el("profilePill").onclick = () => openProfileEditor();
  const settingsBtn = document.querySelector('[data-nav="settings"]');
  if (settingsBtn) settingsBtn.onclick = openSettings;
  const queueBtn = el("aiQueueBtn");
  if (queueBtn) queueBtn.onclick = toggleAIQueue;
  const search = el("searchInput");
  search.oninput = () => {
    state.search = search.value.trim();
    clearTimeout(search._timer);
    search._timer = setTimeout(renderCurrent, 180);
  };
}

function updateTopbar() {
  el("viewTitle").textContent = titleForView();
  el("profilePillName").textContent = state.profileName;
  el("syncBtn").classList.toggle("on", state.syncBusy);
  updateQueueButton();
}

function progressText(status) {
  if (!status || !status.stage) return "";
  if (status.stage === "sync_prepare") return t("progress.sync_prepare");
  if (status.stage === "ai_prepare") return tf("progress.ai_prepare", { total: status.total || 0 });
  if (status.stage === "sync_discover") {
    if (status.totalPages) {
      return tf("progress.sync_discover_known", {
        pages: status.pages || 0,
        totalPages: status.totalPages || 0,
        listings: status.listings || 0,
      });
    }
    return tf("progress.sync_discover", {
      pages: status.pages || 0,
      listings: status.listings || 0,
    });
  }
  if (status.stage === "sync_list_page") {
    return tf("progress.sync_list_page", {
      page: status.page || 1,
    });
  }
  if (status.stage === "sync") {
    if (status.total) {
      return tf("progress.sync_with_count", {
        current: status.current || 0,
        total: status.total || 0,
      });
    }
    return t("progress.sync");
  }
  if (status.stage === "geocode") return t("progress.geocode");
  if (status.stage === "infrastructure") return t("progress.infrastructure");
  if (status.stage === "location") return t("progress.location");
  if (status.stage === "ai") {
    const analyzing = Array.isArray(status.analyzing) ? status.analyzing : [];
    const label = analyzing.length
      ? analyzing.map((a) => a.listing).filter(Boolean).join(", ")
      : (status.listing || "");
    return tf("progress.ai", {
      current: status.analyzed != null ? status.analyzed : (status.current || 0),
      total: status.total || 0,
      listing: label,
    });
  }
  if (status.stage === "ai_stopping") return t("progress.ai_stopping");
  if (status.stage === "ai_stopped") return tf("ai_queue.stopped_summary", {
    analyzed: status.analyzed || 0,
    failed: status.failed || 0,
  });
  if (status.stage === "scoring") return t("progress.scoring");
  if (status.stage === "ai_finished") return tf("ai_queue.finished_summary", {
    analyzed: status.analyzed || 0,
    failed: status.failed || 0,
  });
  return "";
}

function titleForView() {
  if (state.view === "map") return t("view.map");
  if (state.view === "comparison") return t("nav.comparison");
  if (state.view === "settings") return t("view.settings");
  if (state.view === "detail") return t("view.detail");
  const labels = {
    all: "view.ranking",
    new: "view.new",
    favorites: "nav.favorites",
    rejected: "nav.rejected",
    inactive: "nav.inactive",
  };
  return t(labels[state.tab] || "view.ranking");
}

/* ===================== AI queue flyout ===================== */
async function toggleAIQueue() {
  if (state.aiQueueOpen) {
    closeAIQueue();
    return;
  }
  await openAIQueue();
}

async function openAIQueue() {
  state.aiQueueOpen = true;
  el("queuePanel").classList.add("open");
  await loadAIQueue();
  renderAIQueuePanel();
  updateQueueButton();
}

function closeAIQueue() {
  state.aiQueueOpen = false;
  const panel = el("queuePanel");
  if (panel) panel.classList.remove("open");
  updateQueueButton();
}

async function loadAIQueue() {
  const data = await callJson("loadAIQueue");
  applyAIQueuePayload(data || {});
}

function applyAIQueuePayload(data) {
  let queue = data.queue || [];
  if (isAIActive() && state.aiAnalyzingIds && state.aiAnalyzingIds.size) {
    queue = queue.filter((item) => !state.aiAnalyzingIds.has(item.listingId));
  }
  state.aiQueue = queue;
  state.aiAnalyzedOptions = data.analyzedOptions || [];
  state.aiQueueLoaded = true;
}

function scheduleAIQueueRefresh(delayMs = 3000) {
  if (!bridge || !bridge.loadAIQueue || aiQueueRefreshTimer) return;
  aiQueueRefreshTimer = setTimeout(async () => {
    aiQueueRefreshTimer = null;
    try {
      await loadAIQueue();
      updateQueueButton();
      if (state.aiQueueOpen) renderAIQueuePanel();
    } catch (error) {
      // Queue refresh is opportunistic; the running worker owns the authoritative state.
    }
  }, delayMs);
}

async function persistAIQueue() {
  const order = state.aiQueue.map((item) => item.listingId);
  const reanalysis = state.aiQueue
    .filter((item) => item.status === "reanalyze")
    .map((item) => item.listingId);
  const data = await callJson("saveAIQueue", JSON.stringify(order), JSON.stringify(reanalysis));
  applyAIQueuePayload(data || {});
  renderAIQueuePanel();
  updateQueueButton();
  await ensureAIQueueRunning();
}

function isAIActive() {
  return Boolean(state.pipelineStatus && (state.pipelineStatus.stage === "ai" || state.pipelineStatus.stage === "ai_prepare"));
}

async function startAIQueue() {
  if (!bridge || !bridge.startAIQueue) return;
  state.aiPaused = false;
  state.pipelineStatus = { stage: "ai_prepare", total: state.aiQueue.length || 0 };
  state.aiBusy = true;
  updateTopbar();
  if (state.aiQueueOpen) renderAIQueuePanel();
  startSyncPolling();
  const result = await callJson("startAIQueue");
  if (result) {
    state.aiBusy = Boolean(result.busy);
    state.aiPaused = Boolean(result.paused);
  }
  if (!result || (!result.started && !result.busy)) {
    await loadAIQueue();
  }
  updateTopbar();
  if (state.aiQueueOpen) renderAIQueuePanel();
}

async function stopAIQueue() {
  if (!bridge || !bridge.stopAIQueue) return;
  state.aiPaused = true;
  state.pipelineStatus = { stage: "ai_stopping" };
  updateTopbar();
  if (state.aiQueueOpen) renderAIQueuePanel();
  const result = await callJson("stopAIQueue");
  if (result) {
    state.aiBusy = Boolean(result.busy);
    state.aiPaused = Boolean(result.paused);
  }
  startSyncPolling();
}

function updateQueueButton() {
  const btn = el("aiQueueBtn");
  if (!btn) return;
  btn.classList.toggle("active", state.aiQueueOpen);
  const dot = el("queueStatusDot");
  const inline = el("queueInlineStatus");
  const active = isAIActive();
  const stopping = state.pipelineStatus && state.pipelineStatus.stage === "ai_stopping";
  const queueCount = state.aiQueue.length || 0;
  if (dot) {
    dot.hidden = !active && !stopping && !queueCount;
    dot.classList.toggle("busy", Boolean(active || stopping));
    dot.textContent = (active || stopping) ? "" : String(Math.min(queueCount, 99));
  }
  if (inline) {
    if (active || stopping) inline.textContent = progressText(state.pipelineStatus);
    else if (state.aiPaused && queueCount) inline.textContent = t("ai_queue.paused");
    else if (queueCount) inline.textContent = tf("ai_queue.waiting_count", { count: queueCount });
    else inline.textContent = "";
  }
}

function renderAIQueuePanel() {
  const panel = el("queuePanel");
  if (!panel) return;
  const currentText = isAIActive() || (state.pipelineStatus && (state.pipelineStatus.stage === "ai_stopping" || state.pipelineStatus.stage === "ai_stopped"))
    ? progressText(state.pipelineStatus)
    : (state.aiPaused && state.aiQueue.length ? t("ai_queue.paused") : t("ai_queue.idle"));
  const aiRunning = isAIActive() || Boolean(state.aiBusy);
  const canStart = !aiRunning && state.aiQueue.length > 0;
  const canStop = aiRunning;
  const sig = JSON.stringify({
    currentText, aiRunning, canStart, canStop,
    cur: Array.from(state.aiAnalyzingIds || []),
    q: state.aiQueue.map((i) => [i.listingId, i.status]),
    opts: state.aiAnalyzedOptions.map((i) => i.listingId),
  });
  if (sig === lastQueuePanelSig) return;
  lastQueuePanelSig = sig;
  const items = state.aiQueue.length
    ? state.aiQueue.map((item, index) => queueItemHtml(item, index)).join("")
    : `<div class="queue-empty">${t("ai_queue.empty")}</div>`;
  const queuedIds = new Set(state.aiQueue.map((item) => item.listingId));
  const options = state.aiAnalyzedOptions
    .filter((item) => !queuedIds.has(item.listingId))
    .map((item) => `<option value="${item.listingId}">${esc(item.title)} · ${esc(item.meta)}</option>`)
    .join("");
  panel.innerHTML = `
    <div class="queue-head">
      <div>
        <h2><span class="material-symbols-outlined">auto_awesome_motion</span>${t("ai_queue.title")}</h2>
        <p>${t("ai_queue.subtitle")}</p>
      </div>
      <button class="icon-btn" id="closeQueue"><span class="material-symbols-outlined">close</span></button>
    </div>
    <div class="queue-current-box">
      <span class="queue-current-dot ${isAIActive() || (state.pipelineStatus && state.pipelineStatus.stage === "ai_stopping") ? "busy" : ""}"></span>
      <div>
        <strong>${t("ai_queue.now")}</strong>
        <small>${esc(currentText)}</small>
      </div>
    </div>
    <div class="queue-controls">
      <button class="btn sm primary" id="queueStartAnalysis" ${canStart ? "" : "disabled"}>
        <span class="material-symbols-outlined">play_arrow</span>${t("ai_queue.start_analysis")}
      </button>
      <button class="btn sm danger" id="queueStopAnalysis" ${canStop ? "" : "disabled"}>
        <span class="material-symbols-outlined">stop_circle</span>${t("ai_queue.stop_analysis")}
      </button>
    </div>
    <div class="queue-list">${items}</div>
    <div class="queue-add">
      <label>${t("ai_queue.add_analyzed")}</label>
      <div class="queue-add-row">
        <select id="queueAddSelect" ${options ? "" : "disabled"}>${options || `<option>${t("ai_queue.no_analyzed")}</option>`}</select>
        <button class="btn sm" id="queueAddBtn" ${options ? "" : "disabled"}><span class="material-symbols-outlined">add</span>${t("action.add")}</button>
      </div>
    </div>`;
  el("closeQueue").onclick = closeAIQueue;
  const startAnalysisBtn = el("queueStartAnalysis");
  if (startAnalysisBtn) startAnalysisBtn.onclick = startAIQueue;
  const stopAnalysisBtn = el("queueStopAnalysis");
  if (stopAnalysisBtn) stopAnalysisBtn.onclick = stopAIQueue;
  panel.querySelectorAll("[data-move]").forEach((btn) => {
    btn.onclick = () => moveQueueItem(parseInt(btn.dataset.id, 10), parseInt(btn.dataset.move, 10));
  });
  panel.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.onclick = () => removeReanalysisItem(parseInt(btn.dataset.remove, 10));
  });
  const addBtn = el("queueAddBtn");
  if (addBtn) addBtn.onclick = addAnalyzedToQueue;
}

function queueItemHtml(item, index) {
  const isCurrent = (state.aiAnalyzingIds && state.aiAnalyzingIds.has(item.listingId))
    || item.listingId === state.aiCurrentListingId;
  const canUp = index > 0;
  const canDown = index < state.aiQueue.length - 1;
  const status = isCurrent ? "current" : item.status;
  const remove = item.status === "reanalyze"
    ? `<button class="icon-btn" data-remove="${item.listingId}" title="${esc(t("ai_queue.remove_reanalysis"))}"><span class="material-symbols-outlined">remove_circle</span></button>`
    : "";
  return `
    <div class="queue-item ${isCurrent ? "current" : ""}">
      <span class="queue-position">${index + 1}</span>
      <span class="queue-current-dot ${isCurrent ? "busy" : ""}"></span>
      <div class="queue-item-main">
        <strong>${esc(item.title)}</strong>
        <small>${esc(item.meta)}</small>
        <span class="queue-badge ${status}">${queueStatusText(status)}</span>
      </div>
      <div class="queue-actions">
        <button class="icon-btn" data-id="${item.listingId}" data-move="-1" ${canUp ? "" : "disabled"} title="${esc(t("ai_queue.move_up"))}"><span class="material-symbols-outlined">keyboard_arrow_up</span></button>
        <button class="icon-btn" data-id="${item.listingId}" data-move="1" ${canDown ? "" : "disabled"} title="${esc(t("ai_queue.move_down"))}"><span class="material-symbols-outlined">keyboard_arrow_down</span></button>
        ${remove}
      </div>
    </div>`;
}

function queueStatusText(status) {
  if (status === "current") return t("ai_queue.current");
  if (status === "reanalyze") return t("ai_queue.reanalyze");
  if (status === "analyzed") return t("ai_queue.analyzed");
  return t("ai_queue.pending");
}

async function moveQueueItem(listingId, delta) {
  const from = state.aiQueue.findIndex((item) => item.listingId === listingId);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= state.aiQueue.length) return;
  const items = state.aiQueue.slice();
  [items[from], items[to]] = [items[to], items[from]];
  state.aiQueue = items;
  renderAIQueuePanel();
  await persistAIQueue();
}

async function removeReanalysisItem(listingId) {
  state.aiQueue = state.aiQueue.filter(
    (item) => item.listingId !== listingId || item.status !== "reanalyze"
  );
  renderAIQueuePanel();
  await persistAIQueue();
}

async function addAnalyzedToQueue() {
  const select = el("queueAddSelect");
  if (!select || !select.value) return;
  const listingId = parseInt(select.value, 10);
  const item = state.aiAnalyzedOptions.find((candidate) => candidate.listingId === listingId);
  if (!item) return;
  state.aiQueue.push(Object.assign({}, item, { status: "reanalyze" }));
  renderAIQueuePanel();
  await persistAIQueue();
}

/* ===================== Data load ===================== */
// Fetch the current view's data into state. A background score recalculation
// may still be running (it is kicked off on launch and after data changes), but
// we NEVER block the UI on it: we render whatever scores are already persisted
// and set pendingReload so scoresReady can refresh in place when it completes.
async function fetchViewData() {
  const prep = await callJson("prepareProfile", state.profileKey);
  state.pendingReload = Boolean(prep && prep.ready === false);
  const payload = JSON.stringify({
    tab: state.tab,
    profileKey: state.profileKey,
    filters: state.filters,
    search: state.search,
  });
  const data = await callJson("loadView", payload);
  state.rows = data.rows || [];
  state.markers = data.markers || [];
  state.referencePoints = data.referencePoints || [];
  state.mapCoverage = data.mapCoverage || { visible: state.rows.length, geocoded: state.markers.length };
  state.summary = data.summary || {};
  return true;
}

// User-initiated reload: re-fetch and fully re-render the current view. Always
// renders immediately; if a recalc is still in flight the subtle busy bar stays
// up (never the blocking overlay) so the app is usable right away.
async function reload() {
  if (state.view === "settings") { updateTopbar(); renderSettings(); return; }
  await fetchViewData();
  setAppLoading(false);
  setBusyBar(state.pendingReload);
  updateTopbar();
  renderCurrent();
}

// Background refresh driven by the analysis pipeline (between AI chunks and the
// native fallback). Updates the data without blocking and without tearing down
// interactive surfaces: the ranking list is re-rendered in place with its
// scroll position preserved, while the map / comparison / detail views keep
// their live widget and just refresh their counts. They pick up fresh data the
// next time the user navigates to them.
async function onAIRefresh() {
  state.aiQueueLoaded = false;
  await softReload();
}

let softReloadRunning = false;
let softReloadAgain = false;
async function softReload() {
  if (!bridge || state.view === "settings" || state.view === "detail") return;
  // Per-listing refreshes can arrive close together (two analyses finish at
  // once). Coalesce so we never run overlapping fetch+render passes.
  if (softReloadRunning) { softReloadAgain = true; return; }
  softReloadRunning = true;
  try {
    const onRanking = state.view === "ranking";
    const canvas = el("canvas");
    const scrollTop = (onRanking && canvas) ? canvas.scrollTop : 0;
    await fetchViewData();
    setAppLoading(false);
    setBusyBar(state.pendingReload);
    updateTopbar();
    if (onRanking) {
      renderCurrent();
      const after = el("canvas");
      if (after && scrollTop) after.scrollTop = scrollTop;
    } else if (state.view === "map") {
      // Drop in newly analyzed apartments while keeping the current zoom/pan.
      syncMapMarkers();
    }
  } finally {
    softReloadRunning = false;
    if (softReloadAgain) { softReloadAgain = false; setTimeout(softReload, 0); }
  }
}

// Reliable refresh hook the native shell calls via runJavaScript.
window.__flatSearcherSoftRefresh = softReload;

function onScoresReady(profileKey) {
  if (profileKey !== state.profileKey) return;
  state.pendingReload = false;
  setBusyBar(false);
  softReload();
}

function renderCurrent() {
  state.hasRendered = true;
  updateTopbar();
  if (state.view === "ranking") renderRanking();
  else if (state.view === "map") renderMap();
  else if (state.view === "comparison") renderComparison();
  else if (state.view === "settings") renderSettings();
  else if (state.view === "detail") renderDetailView();
}

function visibleRows() {
  let rows = state.rows.slice();
  const q = state.search.toLowerCase();
  if (q) {
    rows = rows.filter((r) =>
      (r.address || "").toLowerCase().includes(q) ||
      (r.district || "").toLowerCase().includes(q) ||
      String(r.listingId) === q
    );
  }
  const dir = { score_desc: ["score", -1], price_asc: ["priceValue", 1], price_desc: ["priceValue", -1], area_desc: ["areaValue", -1] }[state.sort];
  if (dir) {
    const [field, sign] = dir;
    rows.sort((a, b) => {
      const av = a[field], bv = b[field];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (av - bv) * sign;
    });
  }
  return rows;
}

/* ===================== Ranking view ===================== */
function renderRanking() {
  const rows = visibleRows();
  const s = state.summary || {};
  const canvas = el("canvas");
  canvas.innerHTML = `
    <div class="canvas-pad">
      <div class="bento">
        ${bentoCard("trending_up", t("summary.top_score"), s.topScore != null ? s.topScore : "—", "/100")}
        ${bentoCard("payments", t("summary.avg_price"), s.avgPrice != null ? s.avgPrice : "—", "€")}
        ${bentoCard("square_foot", t("summary.avg_area"), s.avgArea != null ? s.avgArea : "—", "m²")}
        ${bentoCard("warning", t("summary.high_risk"), s.highRisk || 0, t("common.listings"), s.highRisk > 0)}
      </div>
      <div class="grid-card">
        <div class="grid-cols grid-head">
          <div>${t("table.rank")}</div>
          <div>${t("table.district")}</div>
          <div>${t("table.address")}</div>
          <div>${t("table.rooms")}</div>
          <div class="right">${t("table.area")}</div>
          <div class="right">${t("table.price")}</div>
          <div class="center"><span class="sortable">${t("table.score")} <span class="material-symbols-outlined" style="font-size:14px">arrow_downward</span></span></div>
          <div>${t("table.flags")}</div>
        </div>
        <div id="rowsHost"></div>
        ${rows.length ? "" : `<div class="empty"><span class="material-symbols-outlined">search_off</span>${t("empty.no_matches")}</div>`}
      </div>
      ${rows.length ? `<div class="end-note"><span class="material-symbols-outlined" style="font-size:16px">info</span>${t("empty.end_results")}</div>` : ""}
    </div>`;
  const host = el("rowsHost");
  rows.forEach((row) => host.appendChild(rankingRow(row)));
}

function bentoCard(icon, label, value, unit, danger) {
  return `<div class="bento-card">
    <div class="label caps"><span class="material-symbols-outlined">${icon}</span>${esc(label)}</div>
    <div class="bento-value ${danger ? "danger" : ""}">${esc(value)} <small>${esc(unit)}</small></div>
  </div>`;
}

function rankingRow(row) {
  const wrap = document.createElement("div");
  wrap.className = "grid-row" + (row.isRejected ? " rejected" : "") + (state.openRowId === row.listingId ? " open" : "");
  const scoreCls = row.scoreBucket === "high" ? "high" : "";
  wrap.innerHTML = `
    <div class="grid-cols">
      <div class="rank ${row.position === 1 ? "top" : ""}">#${row.position}</div>
      <div>${esc(row.district)}</div>
      <div class="addr">${esc(row.address)}</div>
      <div class="rooms">
        <span class="room-badge ${row.aiRoomsType}">AI: ${esc(row.aiRooms)}</span>
        <span class="room-sep">/</span>
        <span class="room-ss">SS: ${esc(row.ssRooms)}</span>
      </div>
      <div class="mono col-right">${esc(row.area)}</div>
      <div class="mono col-right" style="font-weight:700">${esc(row.price)}</div>
      <div class="col-center"><span class="score-pill ${scoreCls}">${row.score != null ? row.score : "—"}</span></div>
      <div class="flags">${row.flags.map((f) => `<span class="chip ${f.type}">${esc(f.label)}</span>`).join("")}</div>
    </div>
    <div class="row-detail" style="display:none"></div>`;
  wrap.querySelector(".grid-cols").onclick = () => toggleRow(row, wrap);
  if (state.openRowId === row.listingId) {
    const host = wrap.querySelector(".row-detail");
    host.style.display = "block";
    fillRowDetail(host, row.listingId);
  }
  return wrap;
}

async function toggleRow(row, wrap) {
  const host = wrap.querySelector(".row-detail");
  if (state.openRowId === row.listingId) {
    state.openRowId = null;
    host.style.display = "none";
    wrap.classList.remove("open");
    return;
  }
  state.openRowId = row.listingId;
  document.querySelectorAll(".grid-row.open").forEach((node) => {
    node.classList.remove("open");
    const h = node.querySelector(".row-detail");
    if (h) h.style.display = "none";
  });
  wrap.classList.add("open");
  host.style.display = "block";
  fillRowDetail(host, row.listingId);
}

async function fillRowDetail(host, listingId) {
  host.innerHTML = `<div class="muted" style="padding:8px">${t("common.loading")}</div>`;
  const detail = await loadDetail(listingId);
  const bars = detail.breakdown.map((b) => `
    <div class="bar-row">
      <span>${esc(b.label)}</span>
      <div class="bar-track">
        <div class="bar"><span class="${b.bucket}" style="width:${b.value}%"></span></div>
        <span class="bar-value ${b.bucket}">${b.value}</span>
      </div>
    </div>`).join("");
  const floor = detail.floorPlan
    ? `<div class="floor"><img src="${detail.floorPlan}" alt="floor plan"><span class="caps">${t("detail.floorplan_verified")}</span></div>` : "";
  host.innerHTML = `
    <div class="row-detail-grid">
      <div>
        <div class="section-label"><span class="material-symbols-outlined">bar_chart</span>${t("score.breakdown")}</div>
        ${bars || `<div class="muted">${t("score.no_breakdown")}</div>`}
        <div class="row-actions">
          <button class="btn sm" data-act="full">${t("action.full_details")}</button>
          <button class="btn sm" data-act="favorite">${detail.isFavorite ? t("action.unfavorite") : t("action.favorite")}</button>
          <button class="btn sm" data-act="reject">${detail.isRejected ? t("action.unreject") : t("action.reject")}</button>
          <button class="btn sm" data-act="compare">${t("action.add_to_comparison")}</button>
          <button class="btn sm" data-act="open">${t("action.open_ss")}</button>
        </div>
      </div>
      <div>
        <div class="section-label"><span class="material-symbols-outlined">psychology</span>${t("detail.ai_insight")}</div>
        <div class="ai-card">
          <p style="margin:0">${esc(detail.aiInsight || t("detail.no_ai_summary"))}</p>
          ${floor}
        </div>
      </div>
    </div>`;
  host.querySelector('[data-act="full"]').onclick = () => openDetail(listingId);
  host.querySelector('[data-act="favorite"]').onclick = () => toggleFavorite(listingId);
  host.querySelector('[data-act="reject"]').onclick = () => toggleRejected(listingId);
  host.querySelector('[data-act="compare"]').onclick = () => addToComparison(listingId);
  host.querySelector('[data-act="open"]').onclick = () => call("openExternal", detail.ssUrl);
}

/* ===================== Detail view ===================== */
async function loadDetail(listingId, force) {
  if (!force && state.detailCache[listingId]) return state.detailCache[listingId];
  const detail = await callJson("loadDetail", listingId, state.profileKey);
  state.detailCache[listingId] = detail;
  return detail;
}

async function openDetail(listingId) {
  if (state.view !== "detail") state.detailReturnView = state.view;
  state.view = "detail";
  state.detailId = listingId;
  renderNav();
  updateTopbar();
  await loadDetail(listingId, true);
  renderDetailView();
}

function renderDetailView() {
  const d = state.detailCache[state.detailId];
  if (!d) return;
  const canvas = el("canvas");
  const badges = d.badges.map((b) => `<span class="badge ${b.type}">${esc(b.label)}</span>`).join("");
  const evalBars = d.evaluation.map((e) => `
    <div class="eval-bar">
      <div class="top"><span>${esc(e.label)}</span><span>${e.value != null ? e.value + "/100" : "—"}</span></div>
      <div class="track"><span class="${e.bucket}" style="width:${e.value || 0}%"></span></div>
    </div>`).join("");
  const proximity = d.proximity.map((p) => `
    <tr><td><span class="material-symbols-outlined">${p.icon}</span>${esc(p.label)}</td><td>${esc(p.value)}</td></tr>`).join("");
  const history = d.history.map((h) => `
    <div class="hist-row"><span class="l"><span class="material-symbols-outlined">${h.icon}</span>${esc(h.label)}</span><span class="v ${h.type}">${esc(h.value)}</span></div>`).join("");
  const floor = d.floorPlan
    ? `<img src="${d.floorPlan}" alt="floor plan">`
    : `<span class="material-symbols-outlined">image</span>`;
  canvas.innerHTML = `
    <div class="detail">
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn sm" id="backBtn"><span class="material-symbols-outlined">arrow_back</span>${t("action.back")}</button>
      </div>
      <section class="panel detail-head">
        <div>
          <h2 class="detail-title"><span class="material-symbols-outlined" style="color:var(--secondary)">location_on</span>${esc(d.district)} · ${esc(d.address)}${d.houseNumber ? " " + esc(d.houseNumber) : ""}</h2>
          <div class="detail-badges">${badges || ""}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:10px">
          <div class="detail-metrics">
            <div class="metric"><div class="caps">${t("table.price")}</div><div class="big">${esc(d.price)}</div></div>
            <div class="metric"><div class="caps">${t("table.area")}</div><div class="mid">${esc(d.area)}</div></div>
            <div class="metric"><div class="caps">${t("metric.eur_m2")}</div><div class="mid">${esc(d.pricePerM2)}</div></div>
          </div>
          <button class="btn primary" id="ssBtn"><span class="material-symbols-outlined">open_in_new</span>${t("action.open_ss")}</button>
        </div>
      </section>
      <div class="detail-cols">
        <div class="detail-col">
          <section class="panel">
            <h3 class="panel-h"><span class="material-symbols-outlined">equalizer</span>${t("detail.evaluation")}
              <span style="margin-left:auto;font-size:12px;font-weight:500" class="muted mono">${esc(d.profileName)}</span></h3>
            <div class="eval-row">
              <div class="score-ring ${d.scoreBucket}"><span class="n">${d.overallScore != null ? d.overallScore : "—"}</span><span class="l">${t("detail.overall")}</span></div>
              <div class="eval-bars">${evalBars}</div>
            </div>
          </section>
          <section class="panel">
            <h3 class="panel-h"><span class="material-symbols-outlined" style="color:var(--ai)">auto_awesome</span>${t("detail.ai_layout_analysis")}</h3>
            <div class="layout-grid">
              <div class="layout-fig">${floor}</div>
              <div class="layout-info">
                <div class="ai-note"><strong>${t("detail.ai_effective_private_rooms")}: ${esc(d.layout.aiRooms)}</strong> · SS: ${esc(d.layout.ssRooms)} · ${esc(d.layout.confidence)}</div>
                <div class="mini-grid">
                  <div class="mini-box"><div class="caps">${t("detail.walkthrough_rooms")}</div><div class="v">${esc(d.layout.walkthrough)}</div></div>
                  <div class="mini-box"><div class="caps">${t("detail.kitchen_living_detected")}</div><div class="v">${d.layout.kitchenLiving ? t("common.yes") : t("common.no")}</div></div>
                </div>
                <p class="muted" style="margin:4px 0 0">${esc(d.layout.explanation)}</p>
              </div>
            </div>
          </section>
          <section class="panel mortgage ${d.mortgage.type}">
            <h3 class="panel-h"><span class="material-symbols-outlined">account_balance</span>${t("detail.mortgage_risk_profile")}</h3>
            <div class="mortgage-row">
              <span class="material-symbols-outlined" style="color:var(--${d.mortgage.type === "neutral" ? "secondary" : d.mortgage.type})">verified_user</span>
              <p style="margin:0"><strong style="color:var(--${d.mortgage.type === "neutral" ? "on-surface" : d.mortgage.type})">${esc(d.mortgage.level)}.</strong> ${esc(d.mortgage.text)}</p>
            </div>
          </section>
          <section class="panel">
            <h3 class="panel-h"><span class="material-symbols-outlined">edit_note</span>${t("detail.notes")}</h3>
            <textarea class="notes-area" id="notesArea" placeholder="${esc(t("detail.notes_placeholder"))}">${esc(d.notes)}</textarea>
            <div style="margin-top:8px;display:flex;gap:8px">
              <button class="btn sm primary" id="saveNotes">${t("action.save_notes")}</button>
              <button class="btn sm" id="favBtn">${d.isFavorite ? t("action.unfavorite") : t("action.favorite")}</button>
              <button class="btn sm" id="rejBtn">${d.isRejected ? t("action.unreject") : t("action.reject")}</button>
              <button class="btn sm" id="cmpBtn">${t("action.add_to_comparison")}</button>
            </div>
          </section>
        </div>
        <div class="detail-col">
          <section class="panel">
            <h3 class="panel-h"><span class="material-symbols-outlined">distance</span>${t("detail.proximity_matrix")}</h3>
            <table class="kv-table"><tbody>${proximity}</tbody></table>
          </section>
          <section class="panel">
            <h3 class="panel-h"><span class="material-symbols-outlined">history</span>${t("detail.listing_history")}</h3>
            ${history}
          </section>
          <section class="panel">
            <h3 class="panel-h"><span class="material-symbols-outlined">description</span>${t("detail.source_description")}</h3>
            <div class="source-text">${esc(d.sourceText) || `<span class="muted">${t("detail.no_description")}</span>`}</div>
          </section>
        </div>
      </div>
    </div>`;
  el("backBtn").onclick = () => { state.view = state.detailReturnView || "ranking"; renderNav(); renderCurrent(); };
  el("ssBtn").onclick = () => call("openExternal", d.ssUrl);
  el("saveNotes").onclick = async () => {
    await call("saveNotes", d.listingId, el("notesArea").value);
    toast(t("status.notes_saved"));
  };
  el("favBtn").onclick = () => toggleFavorite(d.listingId, true);
  el("rejBtn").onclick = () => toggleRejected(d.listingId, true);
  el("cmpBtn").onclick = () => addToComparison(d.listingId);
}

/* ===================== Map view ===================== */
function visibleMarkers() {
  const q = state.search.toLowerCase();
  if (!q) return state.markers;
  return state.markers.filter((m) =>
    (m.address || "").toLowerCase().includes(q) ||
    (m.district || "").toLowerCase().includes(q) ||
    String(m.listingId) === q
  );
}

function renderMap() {
  if (map) { map.remove(); map = null; mapLayer = null; }
  const canvas = el("canvas");
  const markers = visibleMarkers();
  const coverage = state.mapCoverage || { visible: state.rows.length, geocoded: state.markers.length };
  const mapNotice = markers.length ? (
    coverage.visible > coverage.geocoded ? `
      <div class="map-empty">
        <span class="material-symbols-outlined">travel_explore</span>
        <strong>${tf("map.partial_coordinates", { geocoded: coverage.geocoded, visible: coverage.visible })}</strong>
        <small>${t("map.sync_to_geocode_more")}</small>
      </div>` : ""
  ) : `
    <div class="map-empty">
      <span class="material-symbols-outlined">location_off</span>
      <strong>${t("map.no_coordinates")}</strong>
      <small>${t("map.run_sync_to_populate")}</small>
    </div>`;
  canvas.innerHTML = `<div class="map-wrap"><div id="map"></div>${mapNotice}</div>`;
  if (!window.L) {
    canvas.innerHTML = `<div class="canvas-pad"><div class="empty"><span class="material-symbols-outlined">map</span>${t("map.library_failed")}</div></div>`;
    return;
  }
  map = L.map("map", { zoomControl: true }).setView([56.9496, 24.1052], 11);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  mapLayer = L.markerClusterGroup({ showCoverageOnHover: false });
  mapMarkerIndex = {};
  const bounds = [];
  markers.forEach((m) => {
    const marker = makeAptMarker(m);
    mapMarkerIndex[m.listingId] = marker;
    mapLayer.addLayer(marker);
    bounds.push([m.latitude, m.longitude]);
  });
  map.addLayer(mapLayer);
  state.referencePoints.forEach((p) => {
    L.marker([p.latitude, p.longitude], {
      icon: refIcon(p),
      zIndexOffset: -300,
    }).addTo(map)
      .bindPopup(`<strong>${esc(p.title)}</strong>`);
    if (markers.length) bounds.push([p.latitude, p.longitude]);
  });
  if (bounds.length === 1) map.setView(bounds[0], 14);
  else if (bounds.length > 1) map.fitBounds(bounds, { padding: [40, 40] });
}

function makeAptMarker(m) {
  const marker = L.marker([m.latitude, m.longitude], { icon: aptIcon(m), zIndexOffset: 800 });
  marker.bindPopup(mapPopup(m), { minWidth: 300, className: "apt-popup" });
  marker.on("popupopen", () => bindPopup(m));
  return marker;
}

// Add markers for newly analyzed apartments (and refresh changed ones) onto the
// live map without rebuilding it, so the user's current zoom/pan is preserved.
function syncMapMarkers() {
  if (!map || !mapLayer || !window.L) return 0;
  let added = 0;
  visibleMarkers().forEach((m) => {
    const existing = mapMarkerIndex[m.listingId];
    if (existing) {
      existing.setIcon(aptIcon(m));
      if (existing.getPopup()) existing.setPopupContent(mapPopup(m));
    } else {
      const marker = makeAptMarker(m);
      mapMarkerIndex[m.listingId] = marker;
      mapLayer.addLayer(marker);
      added += 1;
    }
  });
  return added;
}

const SCORE_COLORS = { high: "#16a34a", medium: "#c58a00", low: "#d55b2d", very_low: "#b42318", unknown: "#515f74" };

function aptIcon(m) {
  const color = SCORE_COLORS[m.scoreBucket] || SCORE_COLORS.unknown;
  const fav = m.isFavorite ? "box-shadow:0 0 0 2px #c89200, 0 1px 6px rgba(0,0,0,.3);" : "";
  const text = m.score != null ? m.score : "≈";
  return L.divIcon({
    className: "",
    html: `<div class="apt-marker" style="background:${color};${fav}">${text}</div>`,
    iconSize: [30, 30], iconAnchor: [15, 15],
  });
}
function refIcon(p) {
  return L.divIcon({
    className: "",
    html: `<div class="ref-marker ${p.kind}"><span class="material-symbols-outlined">${refIconName(p.kind)}</span></div>`,
    iconSize: [22, 22], iconAnchor: [11, 11],
  });
}
function refIconName(kind) {
  if (kind === "grocery") return "shopping_cart";
  if (kind === "station") return "train";
  return "school";
}
function mapPopup(m) {
  return `<div class="map-popup">
    <div style="display:flex;justify-content:space-between;align-items:flex-start">
      <div><h3>${esc(m.district || "")}</h3><div class="sub">${esc(m.address || "")}</div></div>
    </div>
    <div class="popup-grid">
      <div class="popup-box"><div class="caps">${t("table.score")}</div><div style="font-size:22px;font-weight:700">${m.score != null ? m.score : "—"} <small class="muted">/100</small></div></div>
      <div class="popup-box"><div class="caps">${t("table.price")}</div><div class="mono" style="font-size:15px;font-weight:700">${esc(m.price)}</div><div class="mono muted" style="font-size:11px">AI ${esc(m.aiRooms)} / SS ${esc(m.ssRooms)}</div></div>
    </div>
    <div class="popup-actions">
      <button class="btn primary" data-pop="details">${t("detail.details")}</button>
      <button class="btn" data-pop="ss">SS.com <span class="material-symbols-outlined" style="font-size:14px">open_in_new</span></button>
    </div>
  </div>`;
}
function bindPopup(m) {
  const root = document.querySelector(".apt-popup");
  if (!root) return;
  const det = root.querySelector('[data-pop="details"]');
  const ss = root.querySelector('[data-pop="ss"]');
  if (det) det.onclick = () => openDetail(m.listingId);
  if (ss) ss.onclick = async () => {
    const d = await loadDetail(m.listingId);
    call("openExternal", d.ssUrl);
  };
}

/* ===================== Comparison view ===================== */
async function renderComparison() {
  const canvas = el("canvas");
  if (state.comparisonIds.length < 2) {
    canvas.innerHTML = `<div class="canvas-pad"><div class="empty"><span class="material-symbols-outlined">compare_arrows</span>${t("comparison.empty")}</div></div>`;
    return;
  }
  const data = await callJson("loadComparison", JSON.stringify(state.comparisonIds), state.profileKey);
  const head = data.columns.map((c) => `<th>
      <div class="cmp-col-head">
        <div class="cmp-col-name">${esc(c.district)}<div class="sub">${esc(c.address)}</div></div>
        <button class="icon-btn" data-rm="${c.listingId}" title="${esc(t("action.remove"))}"><span class="material-symbols-outlined">close</span></button>
      </div></th>`).join("");
  const rows = data.rows.map((row) => {
    const cells = row.values.map((v) => {
      if (row.kind === "score") {
        return `<td>${esc(v.text)}<div class="cmp-bar"><span class="${v.bucket}" style="width:${v.value || 0}%"></span></div></td>`;
      }
      if (row.kind === "badge") {
        return `<td><span class="badge ${v.type}">${esc(v.text)}</span></td>`;
      }
      return `<td>${esc(v.text)}</td>`;
    }).join("");
    return `<tr><td>${esc(row.label)}</td>${cells}</tr>`;
  }).join("");
  canvas.innerHTML = `
    <div class="canvas-pad">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div class="cmp-title">${t("comparison.matrix")}</div>
        <button class="btn sm" id="clearCmp">${t("action.clear_comparison")}</button>
      </div>
      <table class="cmp-table">
        <thead><tr><th>${t("comparison.metric")}</th>${head}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  el("clearCmp").onclick = () => { state.comparisonIds = []; renderComparison(); renderNav(); updateTopbar(); };
  canvas.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => removeFromComparison(parseInt(b.dataset.rm, 10)));
}

async function addToComparison(listingId) {
  if (state.comparisonIds.includes(listingId)) { toast(t("comparison.already")); return; }
  if (state.comparisonIds.length >= 5) { toast(t("comparison.limit")); return; }
  state.comparisonIds.push(listingId);
  renderNav();
  toast(t("comparison.added"));
}

function removeFromComparison(listingId) {
  state.comparisonIds = state.comparisonIds.filter((id) => id !== listingId);
  renderComparison();
  renderNav();
  updateTopbar();
}

/* ===================== Settings view ===================== */
function renderSettings() {
  state.view = "settings";
  const canvas = el("canvas");
  const profiles = state.profiles.map((p) => `
    <div class="list-item ${p.key === state.profileKey ? "active" : ""}">
      <div><strong>${esc(p.name)}</strong> ${p.builtin ? `<span class="caps">${t("common.built_in")}</span>` : ""}</div>
      <div class="actions">
        <button class="btn sm" data-use="${p.key}">${t("action.use")}</button>
        <button class="btn sm" data-edit="${p.key}">${t("action.edit")}</button>
        ${p.builtin ? "" : `<button class="btn sm" data-rename="${p.key}">${t("action.rename")}</button><button class="btn sm danger" data-del="${p.key}">${t("action.delete")}</button>`}
      </div>
    </div>`).join("");
  const sessions = state.sessions.length ? state.sessions.map((s) => `
    <div class="list-item">
      <strong>${esc(s.name)}</strong>
      <div class="actions">
        <button class="btn sm" data-loads="${s.id}">${t("action.load")}</button>
        <button class="btn sm danger" data-dels="${s.id}">${t("action.delete")}</button>
      </div>
    </div>`).join("") : `<div class="muted">${t("settings.no_saved_sessions")}</div>`;
  canvas.innerHTML = `
    <div class="settings">
      <div class="set-card">
        <h3>${t("settings.data")}</h3>
        <div class="set-row">
          <div>${t("settings.fetch_listings")}</div>
          <div style="display:flex;gap:8px;align-items:center">
            <input id="syncLimit" type="number" min="1" max="1000" placeholder="${t("common.all")}" style="width:80px;height:32px;border:1px solid var(--outline-variant);border-radius:4px;padding:0 8px">
            <button class="btn primary sm" id="syncNow">${t("action.load_ss")}</button>
          </div>
        </div>
      </div>
      <div class="set-card">
        <h3>${t("settings.scoring_profiles")}</h3>
        <div class="profile-list">${profiles}</div>
        <div style="margin-top:12px"><button class="btn sm" id="newProfile"><span class="material-symbols-outlined">add</span>${t("settings.new_profile_from_current")}</button></div>
      </div>
      <div class="set-card">
        <h3>${t("settings.saved_sessions")}</h3>
        <div class="session-list">${sessions}</div>
        <div style="margin-top:12px"><button class="btn sm" id="saveSession"><span class="material-symbols-outlined">save</span>${t("settings.save_current_filters")}</button></div>
      </div>
    </div>`;
  el("syncNow").onclick = () => startSync(parseInt(el("syncLimit").value, 10) || 0);
  el("newProfile").onclick = () => openProfileEditor();
  el("saveSession").onclick = saveSession;
  canvas.querySelectorAll("[data-use]").forEach((b) => b.onclick = () => changeProfile(b.dataset.use));
  canvas.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => openProfileEditor(b.dataset.edit));
  canvas.querySelectorAll("[data-rename]").forEach((b) => b.onclick = () => renameProfile(b.dataset.rename));
  canvas.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => deleteProfile(b.dataset.del));
  canvas.querySelectorAll("[data-loads]").forEach((b) => b.onclick = () => loadSession(parseInt(b.dataset.loads, 10)));
  canvas.querySelectorAll("[data-dels]").forEach((b) => b.onclick = () => deleteSession(parseInt(b.dataset.dels, 10)));
}

function openSettings() { state.view = "settings"; renderNav(); updateTopbar(); renderSettings(); }

/* ===================== Filters drawer ===================== */
function openDrawer() {
  const f = state.filters;
  const drawer = el("drawer");
  const districts = ["", ...state.districts].map((d) =>
    `<option value="${esc(d)}" ${f.district === (d || null) ? "selected" : ""}>${d ? esc(d) : t("filter.all_districts")}</option>`).join("");
  const bounds = state.filterBounds || defaultFilterBounds();
  drawer.innerHTML = `
    <div class="drawer-head">
      <h2>${t("filter.title")}</h2>
      <button class="icon-btn" id="closeDrawer"><span class="material-symbols-outlined">close</span></button>
    </div>
    <div class="drawer-body">
      ${dualRange("priceRange", t("filter.price_range") + " (€)", "price_min", "price_max", bounds.price, formatRangeMoney)}
      ${dualRange("areaRange", t("table.area") + " (m²)", "area_min", "area_max", bounds.area, formatRangeArea)}
      <hr class="hr">
      <div class="field">
        <label>${t("table.district")}</label>
        <select id="districtSel">${districts}</select>
      </div>
      ${dualRange("ssRoomsRange", t("filter.ss_rooms"), "declared_rooms_min", "declared_rooms_max", bounds.ssRooms, formatRangeRooms)}
      ${dualRange("aiRoomsRange", t("filter.ai_effective_rooms"), "effective_private_rooms_min", "effective_private_rooms_max", bounds.aiRooms, formatRangeRooms)}
      <hr class="hr">
      <div class="checks">
        ${checkRow("only_confirmed_layout", t("filter.only_confirmed_layout"))}
        ${checkRow("only_without_room_conflict", t("filter.only_without_room_conflict"))}
        ${checkRow("only_with_floor_plan", t("filter.only_with_floor_plan"))}
        ${checkRow("only_good_transport", t("filter.only_good_transport"))}
        ${checkRow("only_near_rtu", t("filter.only_near_rtu"))}
        ${checkRow("only_near_central_station", t("filter.only_near_central_station"))}
        ${checkRow("hide_high_mortgage_risk", t("filter.hide_high_mortgage_risk"))}
        ${checkRow("hide_stove_heating", t("filter.hide_stove_heating"))}
        ${checkRow("hide_wooden_buildings", t("filter.hide_wooden_buildings"))}
        ${checkRow("hide_viewed", t("filter.hide_viewed"))}
      </div>
    </div>
    <div class="drawer-foot">
      <button class="btn" id="clearFilters" style="flex:1">${t("action.clear_filters")}</button>
      <button class="btn primary" id="applyFilters" style="flex:1">${t("action.apply_filters")}</button>
    </div>`;
  drawer.classList.add("open");
  el("drawerBackdrop").classList.add("open");
  el("closeDrawer").onclick = closeDrawer;
  el("drawerBackdrop").onclick = closeDrawer;
  bindDualRange("priceRange", bounds.price, formatRangeMoney);
  bindDualRange("areaRange", bounds.area, formatRangeArea);
  bindDualRange("ssRoomsRange", bounds.ssRooms, formatRangeRooms);
  bindDualRange("aiRoomsRange", bounds.aiRooms, formatRangeRooms);
  el("applyFilters").onclick = () => {
    f.price_min = rangeValue("priceRange", "min", bounds.price);
    f.price_max = rangeValue("priceRange", "max", bounds.price);
    f.area_min = rangeValue("areaRange", "min", bounds.area);
    f.area_max = rangeValue("areaRange", "max", bounds.area);
    f.declared_rooms_min = rangeValue("ssRoomsRange", "min", bounds.ssRooms);
    f.declared_rooms_max = rangeValue("ssRoomsRange", "max", bounds.ssRooms);
    f.effective_private_rooms_min = rangeValue("aiRoomsRange", "min", bounds.aiRooms);
    f.effective_private_rooms_max = rangeValue("aiRoomsRange", "max", bounds.aiRooms);
    f.declared_rooms = null;
    f.effective_private_rooms = null;
    f.district = el("districtSel").value || null;
    drawer.querySelectorAll(".check input").forEach((input) => { f[input.dataset.key] = input.checked; });
    closeDrawer();
    if (state.view !== "ranking" && state.view !== "map") { state.view = "ranking"; renderNav(); }
    reload();
  };
  el("clearFilters").onclick = () => { state.filters = defaultFilters(); closeDrawer(); reload(); };
}
function checkRow(key, label) {
  return `<label class="check"><input type="checkbox" data-key="${key}" ${state.filters[key] ? "checked" : ""}><span>${esc(label)}</span></label>`;
}
function closeDrawer() { el("drawer").classList.remove("open"); el("drawerBackdrop").classList.remove("open"); }

function dualRange(id, label, minKey, maxKey, bounds, formatter) {
  const min = state.filters[minKey] ?? bounds.min;
  const max = state.filters[maxKey] ?? bounds.max;
  return `
    <div class="field range-field">
      <label>${label}</label>
      <div class="range-readout">
        <span id="${id}MinText">${formatter(min)}</span>
        <span id="${id}MaxText">${formatter(max)}</span>
      </div>
      <div class="dual-range">
        <div class="dual-range-track"><span id="${id}Fill"></span></div>
        <input id="${id}Min" type="range" min="${bounds.min}" max="${bounds.max}" step="${bounds.step}" value="${min}">
        <input id="${id}Max" type="range" min="${bounds.min}" max="${bounds.max}" step="${bounds.step}" value="${max}">
      </div>
    </div>`;
}

function bindDualRange(id, bounds, formatter) {
  const minInput = el(`${id}Min`);
  const maxInput = el(`${id}Max`);
  const minText = el(`${id}MinText`);
  const maxText = el(`${id}MaxText`);
  const fill = el(`${id}Fill`);
  const sync = (active) => {
    let min = Number(minInput.value);
    let max = Number(maxInput.value);
    if (min > max) {
      if (active === "min") max = min;
      else min = max;
      minInput.value = min;
      maxInput.value = max;
    }
    minText.textContent = formatter(min);
    maxText.textContent = formatter(max);
    const span = Math.max(1, bounds.max - bounds.min);
    fill.style.left = `${((min - bounds.min) / span) * 100}%`;
    fill.style.right = `${100 - ((max - bounds.min) / span) * 100}%`;
  };
  minInput.oninput = () => sync("min");
  maxInput.oninput = () => sync("max");
  sync();
}

function rangeValue(id, edge, bounds) {
  const input = el(`${id}${edge === "min" ? "Min" : "Max"}`);
  const value = Number(input.value);
  if (edge === "min" && value <= bounds.min) return null;
  if (edge === "max" && value >= bounds.max) return null;
  return value;
}

function formatRangeMoney(value) { return `${Math.round(value / 1000)}k €`; }
function formatRangeArea(value) { return `${value} m²`; }
function formatRangeRooms(value) { return String(value); }

/* ===================== Profile editor modal ===================== */
async function openProfileEditor(key) {
  const editorKey = key || state.profileKey;
  const data = await callJson("loadProfileEditor", editorKey);
  const editingProfile = state.profiles.find((p) => p.key === editorKey);
  const isBuiltin = editingProfile ? editingProfile.builtin : true;
  const defaultName = isBuiltin ? `${data.name} (custom)` : data.name;
  const saveLabel = isBuiltin ? t("profile.save_as_new") : t("profile.save");
  const working = {};
  data.blocks.forEach((b) => { working[b.key] = b.importance; });
  const backdrop = el("modalBackdrop");
  const rows = () => data.blocks.map((b) => {
    const current = working[b.key];
    const ignored = current === "Ignore";
    const seg = IMPORTANCE.map((lvl) =>
      `<button class="${current === lvl.value ? "on " + lvl.cls : ""}" data-block="${b.key}" data-val="${esc(lvl.value)}">${esc(t(lvl.short) || lvl.short)}</button>`).join("");
    return `<div class="editor-row ${ignored ? "ignored" : ""}" data-row="${b.key}">
      <div><div class="blk-name">${esc(b.label)}</div></div>
      <div class="seg">${seg}</div>
    </div>`;
  }).join("");
  backdrop.innerHTML = `
    <div class="modal">
      <div class="modal-head">
        <div>
          <h2>${t("profile.edit")}</h2>
          <p>${t("profile.adjust_importance")}</p>
        </div>
        <button class="icon-btn" id="closeModal"><span class="material-symbols-outlined">close</span></button>
      </div>
      <div class="modal-body">
        <div class="editor-head">
          <div class="caps">${t("profile.scoring_block")}</div>
          <div class="caps" style="text-align:center">${t("profile.importance_weighting")}</div>
        </div>
        <div id="editorRows">${rows()}</div>
      </div>
      <div class="modal-foot">
        <input type="text" id="profileName" placeholder="${esc(t("profile.new_name"))}" value="${esc(defaultName)}"
          style="height:36px;border:1px solid var(--outline-variant);border-radius:4px;padding:0 10px;width:260px">
        <div style="display:flex;gap:8px">
          <button class="btn" id="cancelModal">${t("action.cancel")}</button>
          <button class="btn primary" id="applyModal"><span class="material-symbols-outlined">done</span>${esc(saveLabel)}</button>
        </div>
      </div>
    </div>`;
  backdrop.classList.add("open");
  const closeModal = () => { backdrop.classList.remove("open"); backdrop.innerHTML = ""; };
  el("closeModal").onclick = closeModal;
  el("cancelModal").onclick = closeModal;
  backdrop.querySelectorAll(".seg button").forEach((btn) => btn.onclick = () => {
    const block = btn.dataset.block;
    working[block] = btn.dataset.val;
    el("editorRows").innerHTML = rows();
    rebindSeg();
  });
  function rebindSeg() {
    backdrop.querySelectorAll(".seg button").forEach((btn) => btn.onclick = () => {
      working[btn.dataset.block] = btn.dataset.val;
      el("editorRows").innerHTML = rows();
      rebindSeg();
    });
  }
  el("applyModal").onclick = async () => {
    const name = el("profileName").value.trim();
    if (!name) { toast(t("profile.name_required")); return; }
    const result = await callJson("saveProfileImportance", data.key, name, JSON.stringify(working));
    state.profiles = result.profiles;
    state.profileKey = result.activeProfile;
    state.profileName = nameForProfile(state.profileKey);
    closeModal();
    toast(t("profile.saved"));
    if (state.view === "settings") renderSettings();
    reload();
  };
}

/* ===================== Profile / session actions ===================== */
async function changeProfile(key) {
  state.profileKey = key;
  state.profileName = nameForProfile(key);
  state.detailCache = {};
  toast(t("status.recalculating_scores"));
  await call("setProfile", key);
  if (state.view === "settings") renderSettings();
  reload();
}
async function renameProfile(key) {
  const current = state.profiles.find((p) => p.key === key);
  const name = await promptModal(t("profile.rename"), current ? current.name : "");
  if (!name) return;
  const result = await callJson("renameProfile", key, name);
  state.profiles = result.profiles;
  renderSettings();
}
async function deleteProfile(key) {
  const profile = state.profiles.find((p) => p.key === key);
  const ok = await confirmModal(t("action.delete"), tf("confirm.delete_profile", { name: profile ? profile.name : key }));
  if (!ok) return;
  const result = await callJson("deleteProfile", key);
  state.profiles = result.profiles;
  state.profileKey = result.activeProfile;
  state.profileName = nameForProfile(state.profileKey);
  renderSettings();
  reload();
}
async function saveSession() {
  const name = await promptModal(t("session.save"), "");
  if (!name) return;
  const payload = JSON.stringify({ name, profileKey: state.profileKey, filters: state.filters });
  const result = await callJson("saveSession", payload);
  state.sessions = result.sessions;
  renderSettings();
  toast(t("session.saved"));
}
async function loadSession(id) {
  const data = await callJson("loadSession", id);
  if (!data) return;
  state.filters = Object.assign(defaultFilters(), data.filters);
  if (data.profileKey) { state.profileKey = data.profileKey; state.profileName = nameForProfile(data.profileKey); await call("setProfile", data.profileKey); }
  state.view = "ranking"; state.tab = "all";
  renderNav(); reload();
  toast(t("session.loaded"));
}
async function deleteSession(id) {
  const session = state.sessions.find((s) => s.id === id);
  const ok = await confirmModal(t("action.delete"), tf("confirm.delete_session", { name: session ? session.name : "" }));
  if (!ok) return;
  const result = await callJson("deleteSession", id);
  state.sessions = result.sessions;
  renderSettings();
}

/* ===================== State change actions ===================== */
async function toggleFavorite(listingId, isDetail) {
  await call("toggleFavorite", listingId);
  await afterStateChange(listingId, isDetail);
}
async function toggleRejected(listingId, isDetail) {
  await call("toggleRejected", listingId);
  await afterStateChange(listingId, isDetail);
}
async function afterStateChange(listingId, isDetail) {
  delete state.detailCache[listingId];
  if (isDetail) { await loadDetail(listingId, true); }
  await reload();
  if (isDetail && state.view === "detail") renderDetailView();
}

/* ===================== Pipeline ===================== */
async function ensureAIQueueRunning() {
  if (!bridge || !bridge.ensureAIQueueRunning) return;
  try {
    const result = await callJson("ensureAIQueueRunning");
    if (result) {
      state.aiBusy = Boolean(result.busy);
      state.aiPaused = Boolean(result.paused);
    }
    if (result && result.busy) {
      startSyncPolling();
    }
    updateTopbar();
    if (state.aiQueueOpen) renderAIQueuePanel();
  } catch (error) {
    // AI queue startup must not block rendering.
  }
}

async function startSync(limit) {
  if (state.syncBusy) return;
  state.syncBusy = true;
  state.pipelineStatus = { stage: "sync_prepare" };
  updateTopbar();
  startSyncPolling();
  toast(t("progress.sync"));
  const result = await call("startSync", typeof limit === "number" ? limit : 0);
  if (result === "busy") {
    state.syncBusy = false;
    state.pipelineStatus = null;
    stopSyncPolling();
    updateTopbar();
    toast(t("status.sync_busy"));
  }
}
function onPipelineProgress(raw) {
  applyPipelineStatusFromRaw(raw);
}
window.__flatSearcherApplyPipelineStatusRaw = applyPipelineStatusFromRaw;
function applyPipelineStatusFromRaw(raw) {
  try {
    applyPipelineStatus(JSON.parse(raw));
  } catch (error) {
    state.pipelineStatus = null;
    updateTopbar();
  }
}
function applyPipelineStatus(status) {
  if (!status || typeof status !== "object") return;
  if (status.stage === "finished" || status.stage === "failed") return;
  state.pipelineStatus = status;
  state.aiBusy = status.stage === "ai" || status.stage === "ai_prepare" || status.stage === "ai_stopping";
  if (state.pipelineStatus && state.pipelineStatus.stage === "ai") {
    const analyzing = Array.isArray(state.pipelineStatus.analyzing) ? state.pipelineStatus.analyzing : [];
    state.aiAnalyzing = analyzing;
    state.aiAnalyzingIds = new Set(analyzing.map((a) => a.listingId));
    state.aiCurrentListingId = analyzing.length ? analyzing[0].listingId : (state.pipelineStatus.listingId || null);
    if (state.aiAnalyzingIds.size) {
      state.aiQueue = state.aiQueue.filter((item) => !state.aiAnalyzingIds.has(item.listingId));
    }
    scheduleAIQueueRefresh();
  } else if (state.pipelineStatus && (state.pipelineStatus.stage === "ai_prepare" || state.pipelineStatus.stage === "ai_stopped")) {
    state.aiCurrentListingId = null;
    state.aiAnalyzing = [];
    state.aiAnalyzingIds = new Set();
  }
  if (state.pipelineStatus && state.pipelineStatus.stage === "ai_stopped") {
    state.aiBusy = false;
    state.aiPaused = true;
  }
  updateTopbar();
  if (state.aiQueueOpen) renderAIQueuePanel();
}
// Persistent, lightweight poll that drives live analysis updates entirely
// through QWebChannel pulls (no Python->JS push, which destabilises the channel
// under load). Shows which apartments are analyzing now and, as each completes,
// pulls it into the ranking and onto the map.
function startAILivePolling() {
  if (aiLiveTimer) return;
  aiLiveTimer = setInterval(pollAILive, 1500);
  pollAILive();
}
async function pollAILive() {
  if (!bridge) return;
  try {
    const data = await callJson("syncStatus");
    if (!data) return;
    state.aiBusy = Boolean(data.aiBusy);
    state.aiPaused = Boolean(data.aiPaused);
    if (data.status) applyPipelineStatus(data.status);
    updateTopbar();
    if (state.aiQueueOpen) renderAIQueuePanel();
    if (data.aiSeq != null && data.aiSeq !== lastAiSeq) {
      lastAiSeq = data.aiSeq;
      softReload();
    }
  } catch (error) {
    // Never let polling break the app.
  }
}
function startSyncPolling() {
  stopSyncPolling();
  syncPollTimer = setInterval(pollSyncStatus, 1000);
  pollSyncStatus();
}
function stopSyncPolling() {
  if (syncPollTimer) {
    clearInterval(syncPollTimer);
    syncPollTimer = null;
  }
}
async function pollSyncStatus() {
  if (!bridge) return;
  try {
    const data = await callJson("syncStatus");
    if (data) {
      state.syncBusy = Boolean(data.syncBusy);
      state.aiBusy = Boolean(data.aiBusy);
      state.aiPaused = Boolean(data.aiPaused);
    }
    if (data && data.status) applyPipelineStatus(data.status);
    if (data && data.busy === false) stopSyncPolling();
    updateTopbar();
    if (state.aiQueueOpen) renderAIQueuePanel();
  } catch (error) {
    // Keep the signal path as fallback; polling should never break sync itself.
  }
}
function onSyncFinished(message) {
  stopSyncPolling();
  state.syncBusy = false;
  state.pipelineStatus = null;
  state.aiBusy = false;
  state.aiCurrentListingId = null;
  state.aiQueueLoaded = false;
  state.detailCache = {};
  toast(message);
  reloadBootstrapAndView().then(() => ensureAIQueueRunning());
}
function onSyncFailed(message) {
  stopSyncPolling();
  state.syncBusy = false;
  state.pipelineStatus = null;
  state.aiCurrentListingId = null;
  updateTopbar();
  if (state.aiQueueOpen) renderAIQueuePanel();
  toast(t("status.load_failed") + ": " + message);
}
function onAIFinished(message) {
  state.pipelineStatus = null;
  state.aiBusy = false;
  state.aiCurrentListingId = null;
  state.aiQueueLoaded = false;
  state.detailCache = {};
  if (aiQueueRefreshTimer) {
    clearTimeout(aiQueueRefreshTimer);
    aiQueueRefreshTimer = null;
  }
  toast(message);
  reloadBootstrapAndView().then(() => ensureAIQueueRunning());
}
function onAIFailed(message) {
  state.pipelineStatus = null;
  state.aiBusy = false;
  state.aiPaused = true;
  state.aiCurrentListingId = null;
  updateTopbar();
  if (state.aiQueueOpen) renderAIQueuePanel();
  toast(t("status.load_failed") + ": " + message);
}
async function reloadBootstrapAndView() {
  const data = await callJson("bootstrap");
  state.districts = data.districts || [];
  state.filterBounds = normalizeFilterBounds(data.filterBounds);
  state.profiles = data.profiles || [];
  state.profileName = nameForProfile(state.profileKey);
  await loadAIQueue();
  if (state.aiQueueOpen) {
    renderAIQueuePanel();
  }
  reload();
}

/* ===================== Prompt + toast helpers ===================== */
function promptModal(title, defaultValue) {
  return new Promise((resolve) => {
    const backdrop = el("modalBackdrop");
    backdrop.innerHTML = `
      <div class="modal" style="width:420px">
        <div class="modal-head"><h2 style="font-size:18px">${esc(title)}</h2></div>
        <div class="modal-body" style="background:var(--white);padding:16px">
          <input type="text" id="promptInput" value="${esc(defaultValue)}"
            style="width:100%;height:36px;border:1px solid var(--outline-variant);border-radius:4px;padding:0 10px">
        </div>
        <div class="modal-foot" style="justify-content:flex-end">
          <button class="btn" id="promptCancel">${t("action.cancel")}</button>
          <button class="btn primary" id="promptOk">${t("action.ok")}</button>
        </div>
      </div>`;
    backdrop.classList.add("open");
    const input = el("promptInput");
    input.focus(); input.select();
    const done = (value) => { backdrop.classList.remove("open"); backdrop.innerHTML = ""; resolve(value); };
    el("promptCancel").onclick = () => done(null);
    el("promptOk").onclick = () => done(input.value.trim() || null);
    input.onkeydown = (e) => { if (e.key === "Enter") done(input.value.trim() || null); if (e.key === "Escape") done(null); };
  });
}

function confirmModal(title, message) {
  return new Promise((resolve) => {
    const backdrop = el("modalBackdrop");
    backdrop.innerHTML = `
      <div class="modal" style="width:420px">
        <div class="modal-head"><h2 style="font-size:18px">${esc(title)}</h2></div>
        <div class="modal-body" style="background:var(--white);padding:16px">
          <p style="margin:0">${esc(message)}</p>
        </div>
        <div class="modal-foot" style="justify-content:flex-end">
          <button class="btn" id="confirmCancel">${t("action.cancel")}</button>
          <button class="btn danger" id="confirmOk">${t("action.delete")}</button>
        </div>
      </div>`;
    backdrop.classList.add("open");
    const done = (value) => { backdrop.classList.remove("open"); backdrop.innerHTML = ""; resolve(value); };
    el("confirmCancel").onclick = () => done(false);
    el("confirmOk").onclick = () => done(true);
    el("confirmOk").focus();
  });
}

let toastTimer = null;
function toast(message) {
  const node = el("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2600);
}
