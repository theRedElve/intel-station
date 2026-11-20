/* 个人情报站 · 前端逻辑 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  view: "overview",
  status: "all",
  feedId: "",
  topic: "",
  sort: "time",
  q: "",
  tag: "",
  offset: 0,
  limit: 12,
  total: 0,
  items: [],
  selected: new Set(),
  excluded: new Set(),
  allSelected: false,
  running: false,
  overview: null,
  feeds: [],
  topics: [],
  settings: null,
};

const VIEW_TITLES = {
  overview: ["总览", "你的私人情报工作流"],
  feed: ["情报流", "采集 · 加工 · 分级后的信息流"],
  intake: ["投递", "把 RSS 抓不到的信息补进来"],
  digest: ["每日简报", "把一天的信息压缩成一页结论"],
  knowledge: ["知识库", "已读情报沉淀成的个人知识库"],
  sources: ["信息源", "管理 RSS 订阅与采集状态"],
  resources: ["资源库", "权威信息源导航与信源分级"],
  topics: ["关注主题", "用关键词定义你的情报雷达"],
  settings: ["设置", "模型与调度"],
};

const TIER_LABELS = { official: "官方", industry: "行业", media: "媒体" };
const RATING_LABELS = { low: "低", mid: "中", high: "高" };

function tierBadge(tier) {
  const t = tier && TIER_LABELS[tier] ? tier : "media";
  return `<span class="tier-badge ${t}">${TIER_LABELS[t]}</span>`;
}

function ratingGroup(it) {
  return `<span class="rating-group" title="人工评判，会影响重要度">
    ${["low", "mid", "high"].map(r =>
      `<button class="rating-btn ${r} ${it.rating === r ? "on" : ""}" data-rate="${r}" data-id="${it.id}">${RATING_LABELS[r]}</button>`
    ).join("")}
  </span>`;
}

/* ---------------- 基础工具 ---------------- */

async function api(path, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && typeof opts.body !== "string") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const msg = (data && data.error) || `请求失败 (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function esc(str) {
  return String(str == null ? "" : str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function toast(msg, type = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show " + type;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = "toast " + type; }, 2600);
}

function showAlert(title, message, detail = "") {
  $("#modalBody").innerHTML = `
    <div class="alert-head"><div class="alert-icon">!</div><h2 class="alert-title">${esc(title)}</h2></div>
    <p class="alert-desc">${esc(message)}</p>
    ${detail ? `<div class="alert-detail">${esc(detail)}</div>` : ""}
    <div class="alert-actions"><button class="btn" id="alertOk">知道了</button></div>`;
  $("#modalMask").classList.add("alert-mode");
  $("#modalMask").classList.remove("hidden");
  $("#alertOk").addEventListener("click", closeModal);
}

function relTime(ts) {
  if (!ts) return "";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  if (diff < 7 * 86400) return Math.floor(diff / 86400) + " 天前";
  return new Date(ts * 1000).toLocaleDateString("zh-CN");
}

function scoreClass(score) {
  if (score >= 85) return "critical";
  if (score >= 70) return "high";
  if (score >= 50) return "mid";
  return "low";
}

function scoreLabel(score) {
  if (score >= 85) return "关键";
  if (score >= 70) return "重要";
  if (score >= 50) return "关注";
  return "参考";
}

/* ---------------- 导航 ---------------- */

function switchView(view) {
  if (!VIEW_TITLES[view]) view = "overview";
  state.view = view;
  if (location.hash !== "#" + view) history.replaceState(null, "", "#" + view);
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + view));
  const [title, sub] = VIEW_TITLES[view] || ["", ""];
  $("#crumb").textContent = title;
  $("#crumbSub").textContent = sub;
  window.scrollTo({ top: 0 });

  if (view === "overview") loadOverview();
  if (view === "feed") loadFeed(true);
  if (view === "intake") loadIntake();
  if (view === "digest") loadDigest();
  if (view === "knowledge") loadKnowledge();
  if (view === "sources") loadSources();
  if (view === "resources") loadResources();
  if (view === "topics") loadTopics();
  if (view === "settings") loadSettings();
}

/* ---------------- 总览 ---------------- */

async function loadOverview() {
  let data;
  try { data = await api("/api/overview"); } catch (e) { return toast(e.message, "err"); }
  state.overview = data;
  $("#navUnread").textContent = data.unread;
  $("#pendingCount").textContent = data.pending;

  const cards = [
    { label: "今日新增", value: data.today, foot: `累计收录 ${data.total} 条`, cls: "" },
    { label: "待读情报", value: data.unread, foot: `收藏 ${data.starred} 条`, cls: "blue" },
    { label: "高价值未读", value: data.high, foot: "重要度 ≥ 70", cls: "accent" },
    { label: "待加工 / 信息源", value: data.pending, foot: `启用订阅 ${data.feeds} 个`, cls: "warn" },
  ];
  $("#statsGrid").innerHTML = cards.map(c => `
    <div class="stat ${c.cls}">
      <div class="stat-label">${c.label}</div>
      <div class="stat-value">${c.value}<small>条</small></div>
      <div class="stat-foot">${c.foot}</div>
    </div>`).join("");

  const max = Math.max(1, ...data.trend.map(t => t.count));
  const total7 = data.trend.reduce((s, t) => s + t.count, 0);
  $("#trendTotal").textContent = `7 日共 ${total7} 条`;
  $("#trendChart").innerHTML = data.trend.map((t, i) => `
    <div class="bar-wrap" title="${t.day} · ${t.count} 条">
      <div class="bar-value">${t.count || ""}</div>
      <div class="bar ${i === data.trend.length - 1 ? "today" : ""}" style="height:${Math.max(3, t.count / max * 100)}%"></div>
      <div class="bar-label">${t.day}</div>
    </div>`).join("");

  $("#tagCloud").innerHTML = data.top_tags.length
    ? data.top_tags.map(t => `<button class="tag" data-tag="${esc(t.tag)}">${esc(t.tag)}<b>${t.count}</b></button>`).join("")
    : `<div class="empty">还没有标签，先运行一次采集加工</div>`;

  const sourcesMax = Math.max(1, ...data.sources.map(s => s.c));
  $("#sourceBars").innerHTML = data.sources.length
    ? data.sources.map(s => `
      <div class="source-row">
        <span class="source-name" title="${esc(s.title)}">${esc(s.title)}</span>
        <span class="source-track"><span class="source-fill" style="width:${s.c / sourcesMax * 100}%"></span></span>
        <span class="source-count">${s.c} 条</span>
      </div>`).join("")
    : `<div class="empty">暂无数据</div>`;

  try {
    const high = await api("/api/items?status=high&sort=importance&limit=5");
    $("#highList").innerHTML = high.items.length
      ? high.items.map(it => `
        <div class="mini-item" data-open="${it.id}">
          <div class="mini-score"><span class="score ${scoreClass(it.importance)}" style="padding:0;border:0;background:none">${it.importance}</span></div>
          <div class="mini-body">
            <div class="mini-title">${esc(it.title)}</div>
            <div class="mini-meta">${esc(it.source || "")} · ${relTime(it.published_at)}${it.read ? " · 已读" : ""}</div>
          </div>
        </div>`).join("")
      : `<div class="empty">暂无高价值情报</div>`;
  } catch (e) { /* ignore */ }

  loadEvents();
}

async function loadKnowledge() {
  let data;
  try { data = await api("/api/knowledge"); } catch (e) { return toast(e.message, "err"); }

  const rated = data.ratings.low + data.ratings.mid + data.ratings.high;
  const cards = [
    { label: "已读沉淀", value: data.read_total, unit: "条", foot: "标记已读即进入知识库", cls: "" },
    { label: "其中收藏", value: data.starred, unit: "条", foot: "重点回看", cls: "blue" },
    { label: "平均重要度", value: data.avg_importance, unit: "", foot: "已读情报均分", cls: "accent" },
    {
      label: "人工已评判", value: rated, unit: "条", cls: "warn",
      foot: `低 ${data.ratings.low} · 中 ${data.ratings.mid} · 高 ${data.ratings.high}`,
    },
  ];
  $("#kbStats").innerHTML = cards.map(c => `
    <div class="stat ${c.cls}">
      <div class="stat-label">${c.label}</div>
      <div class="stat-value">${c.value}${c.unit ? `<small>${c.unit}</small>` : ""}</div>
      <div class="stat-foot">${c.foot}</div>
    </div>`).join("");

  loadKbSummary(3);
  loadKbSummary(1);

  $("#kbRecent").innerHTML = data.recent.length
    ? data.recent.map(it => `
      <div class="mini-item" data-open="${it.id}">
        <div class="mini-score"><span class="score ${scoreClass(it.importance)}" style="padding:0;border:0;background:none">${it.importance}</span></div>
        <div class="mini-body">
          <div class="mini-title">${esc(it.title)}</div>
          <div class="mini-meta">${esc(it.source || "")} · ${relTime(it.published_at)}${it.rating ? ` · 评判 ${RATING_LABELS[it.rating]}` : ""}${it.starred ? " · ★" : ""}</div>
        </div>
      </div>`).join("")
    : `<div class="empty">还没有已读情报</div>`;
}

async function loadKbSummary(months) {
  const body = $("#kbSummary" + months);
  const chips = $("#kbSum" + months + "Chips");
  const hint = $("#kbSum" + months + "Hint");
  try {
    const d = await api(`/api/knowledge/summary?months=${months}`);
    body.textContent = d.text;
    hint.textContent = `${months === 1 ? "近 30 天" : "近 90 天"}已读 · ${d.source === "ai" ? "模型总结" : "规则摘要"}`;
    chips.innerHTML = d.total
      ? [
        `<span class="mini-tag">共 ${d.total} 条</span>`,
        ...d.topics.slice(0, 3).map(t => `<span class="mini-tag">${esc(t.topic)} ${t.c}</span>`),
        d.ratings.high ? `<span class="mini-tag">高 ${d.ratings.high}</span>` : "",
      ].filter(Boolean).join("")
      : "";
  } catch (e) {
    body.textContent = "知识总结生成失败：" + e.message;
  }
}

/* ---------------- 人工投递 ---------------- */

async function loadIntake() {
  let list;
  try { list = await api("/api/intake?limit=20"); } catch (e) { return toast(e.message, "err"); }
  $("#intakeSummary").textContent = list.length ? `最近 ${list.length} 条` : "还没有人工投递";
  $("#intakeRecent").innerHTML = list.length
    ? list.map(it => `
      <div class="mini-item" data-open="${it.id}">
        <div class="mini-body">
          <div class="mini-title">${esc(it.title)}</div>
          <div class="mini-meta">${esc(it.channel || "人工投递")} · ${relTime(it.fetched_at)}${it.rating ? " · 评判 " + RATING_LABELS[it.rating] : ""}${it.read ? " · 已读" : " · 未读"}</div>
        </div>
      </div>`).join("")
    : `<div class="empty">粘贴原文链接后，从左侧提交。</div>`;
}

async function loadEvents() {
  try {
    const events = await api("/api/events?limit=30");
    $("#eventList").innerHTML = events.length
      ? events.map(e => `
        <div class="event ${e.level}">
          <time>${new Date(e.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })}</time>
          <span class="msg">${esc(e.message)}</span>
        </div>`).join("")
      : `<div class="empty">暂无日志</div>`;
  } catch (e) { /* ignore */ }
}

/* ---------------- 情报流 ---------------- */

function itemCard(it) {
  const cls = scoreClass(it.importance);
  const manual = it.origin === "manual";
  const picked = state.allSelected ? !state.excluded.has(it.id) : state.selected.has(it.id);
  const tags = (it.tags || []).filter(t => t !== it.topic).slice(0, 4)
    .map(t => `<span class="mini-tag">${esc(t)}</span>`).join("");
  return `
    <article class="card ${cls} ${it.read ? "read" : ""}" data-open="${it.id}">
      <div class="card-top">
        <input type="checkbox" class="pick" data-pick="${it.id}" title="选择这条情报"${picked ? " checked" : ""}>
        <span class="score ${cls}" title="${esc(it.reason || "")}">${it.importance}</span>
        <h3 class="card-title">${esc(it.title)}</h3>
      </div>
      ${it.summary ? `<p class="card-summary">${esc(it.summary)}</p>` : ""}
      <div class="card-foot">
        <span class="src">${esc(it.source || "未知来源")}</span>
        ${manual ? `<span class="mini-tag manual">人工</span>` : tierBadge(it.source_tier)}
        <i class="dot-sep"></i>
        <span>${relTime(it.published_at)}</span>
        ${it.topic ? `<span class="mini-tag">${esc(it.topic)}</span>` : ""}
        ${it.verify_hint ? `<span class="verify-badge" title="命中「${esc(it.verify_hint)}」，建议回源核对">建议回源核对</span>` : ""}
        <div class="card-tags">${tags}</div>
        <div class="card-actions">
          ${ratingGroup(it)}
          <button class="icon-btn ${it.starred ? "on" : ""}" data-act="star" data-id="${it.id}" title="收藏">★</button>
          <button class="icon-btn ${it.read ? "on" : ""}" data-act="read" data-id="${it.id}" title="标记已读">✓</button>
          <button class="icon-btn danger" data-discard="${it.id}" title="丢弃（直接删除）">✕</button>
        </div>
      </div>
    </article>`;
}

async function loadFeed(reset = false) {
  if (reset) {
    state.offset = 0;
    state.items = [];
    clearSelection(false);
    $("#feedList").innerHTML = "";
  }
  const params = new URLSearchParams({
    status: state.status,
    sort: state.sort,
    limit: state.limit,
    offset: state.offset,
  });
  if (state.feedId) params.set("feed_id", state.feedId);
  if (state.topic) params.set("topic", state.topic);
  if (state.q) params.set("q", state.q);
  if (state.tag) params.set("tag", state.tag);

  let data;
  try { data = await api("/api/items?" + params.toString()); } catch (e) { return toast(e.message, "err"); }
  state.items = state.items.concat(data.items);
  state.offset += data.items.length;
  state.total = data.total;

  updateFeedMeta();
  $("#feedList").innerHTML = state.items.length
    ? state.items.map(itemCard).join("")
    : `<div class="panel"><div class="empty">这里还没有情报。点击右上角「立即采集」抓取一次，或在信息源里添加订阅。</div></div>`;
  $("#btnMore").style.display = state.items.length < data.total ? "" : "none";
  syncSelectionUI();
}

function selectionCount() {
  return state.allSelected
    ? Math.max(0, state.total - state.excluded.size)
    : state.selected.size;
}

function syncSelectionUI() {
  const count = selectionCount();
  const all = $("#selectAll");
  if (all) all.checked = state.allSelected ? !state.excluded.size : (count > 0 && count >= state.total);
  const hint = $("#selectHint");
  if (hint) hint.textContent = `已选 ${count} 条 / 共 ${state.total} 条`;
  const btn = $("#discardSelected");
  if (btn) btn.disabled = count === 0;
}

function clearSelection(refresh = true) {
  state.selected.clear();
  state.excluded.clear();
  state.allSelected = false;
  if (refresh) syncSelectionUI();
}

function togglePick(id, checked) {
  if (state.allSelected) {
    if (checked) state.excluded.delete(id);
    else state.excluded.add(id);
  } else if (checked) {
    state.selected.add(id);
  } else {
    state.selected.delete(id);
  }
  syncSelectionUI();
}

async function discardSelected() {
  const count = selectionCount();
  if (!count) return;
  if (!confirm(`确定丢弃 ${count} 条情报？删除后不可恢复。`)) return;
  const body = state.allSelected
    ? {
      all: true,
      exclude: Array.from(state.excluded),
      status: state.status,
      feed_id: state.feedId || undefined,
      topic: state.topic || undefined,
      tag: state.tag || undefined,
      q: state.q || undefined,
    }
    : { ids: Array.from(state.selected) };
  const btn = $("#discardSelected");
  btn.disabled = true;
  try {
    const res = await api("/api/items/discard", { method: "POST", body });
    toast(`已丢弃 ${res.deleted} 条情报`, "ok");
    clearSelection(false);
    loadFeed(true);
    loadOverview();
  } catch (e) {
    toast(e.message, "err");
    syncSelectionUI();
  }
}

async function toggleItem(id, field, force) {
  const item = state.items.find(i => i.id === id);
  const next = force !== undefined ? force : !(item ? item[field] : false);
  try {
    await api("/api/items/" + id, { method: "POST", body: { [field]: next } });
    if (item) item[field] = next ? 1 : 0;
    if (field === "read" && ["unread", "read", "high"].includes(state.status)) {
      removeCard(id);
    } else {
      $$(`.card[data-open="${id}"]`).forEach(el => {
        if (field === "read") el.classList.toggle("read", !!next);
        const btn = $(`.icon-btn[data-act="read"]`, el);
        if (btn) btn.classList.toggle("on", !!next);
        if (field === "starred") {
          const sb = $(`.icon-btn[data-act="star"]`, el);
          if (sb) sb.classList.toggle("on", !!next);
        }
      });
    }
    toast(next ? (field === "starred" ? "已收藏" : "已标记为已读") : (field === "starred" ? "已取消收藏" : "已标记为未读"), "ok");
    if (state.view === "overview") loadOverview();
  } catch (e) { toast(e.message, "err"); }
}

function updateFeedMeta() {
  $("#feedMeta").textContent = `共 ${state.total} 条${state.topic ? ` · 主题「${state.topic}」` : ""}${state.tag ? ` · 标签「${state.tag}」` : ""}${state.q ? ` · 搜索「${state.q}」` : ""}`;
}

function removeCard(id) {
  state.items = state.items.filter(i => i.id !== id);
  state.selected.delete(id);
  state.excluded.delete(id);
  state.offset = Math.max(0, state.offset - 1);
  state.total = Math.max(0, state.total - 1);
  const cards = $$(`.card[data-open="${id}"]`);
  cards.forEach(el => {
    el.style.maxHeight = el.offsetHeight + "px";
    el.classList.add("leaving");
  });
  updateFeedMeta();
  syncSelectionUI();
  $("#btnMore").style.display = state.items.length < state.total ? "" : "none";
  setTimeout(() => {
    cards.forEach(el => el.remove());
    if (!state.items.length && state.total > 0) loadFeed(true);
  }, 300);
}

async function discardItem(id) {
  try {
    await api("/api/items/" + id, { method: "DELETE" });
    removeCard(id);
    closeModal();
    toast("已丢弃并删除", "ok");
    loadSettingsCounters();
    if (state.view === "overview") loadOverview();
  } catch (e) { toast(e.message, "err"); }
}

async function setRating(id, rating) {
  try {
    const res = await api("/api/items/" + id, { method: "POST", body: { rating } });
    const item = state.items.find(i => i.id === id);
    if (item) { item.rating = res.rating; item.importance = res.importance; }
    updateCardRating(id, res.rating, res.importance);
    updateModalRating(id, res.rating, res.importance);
    toast(`人工评判：${RATING_LABELS[res.rating] || "未评"}，重要度 ${res.importance}`, "ok");
    if (state.view === "overview") loadOverview();
  } catch (e) { toast(e.message, "err"); }
}

function updateCardRating(id, rating, importance) {
  const cls = scoreClass(importance);
  $$(`.card[data-open="${id}"]`).forEach(card => {
    card.classList.remove("critical", "high", "mid", "low");
    card.classList.add(cls);
    const score = $(".score", card);
    if (score) {
      score.textContent = importance;
      score.classList.remove("critical", "high", "mid", "low");
      score.classList.add(cls);
    }
    $$(".rating-btn", card).forEach(b => b.classList.toggle("on", b.dataset.rate === rating));
  });
}

function updateModalRating(id, rating, importance) {
  const body = $("#modalBody");
  if (!body || body.dataset.itemId !== String(id)) return;
  const cls = scoreClass(importance);
  const score = $(".modal-meta .score", body);
  if (score) {
    score.textContent = `${importance} · ${scoreLabel(importance)}`;
    score.classList.remove("critical", "high", "mid", "low");
    score.classList.add(cls);
  }
  $$("[data-rate]", body).forEach(b => b.classList.toggle("on", b.dataset.rate === rating));
}

/* ---------------- 详情弹窗 ---------------- */

function safeUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : "";
}

async function openItem(id) {
  let it;
  try { it = await api("/api/items/" + id); } catch (e) { return toast(e.message, "err"); }
  const cls = scoreClass(it.importance);
  const points = (it.key_points || []).length
    ? `<h4>关键信息</h4><ul>${it.key_points.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : "";
  const entities = (it.entities || []).length
    ? `<h4>涉及实体</h4><div class="modal-tags">${it.entities.map(e => `<span class="mini-tag">${esc(e)}</span>`).join("")}</div>` : "";
  const tags = (it.tags || []).length
    ? `<div class="modal-tags">${it.tags.map(t => `<span class="mini-tag">${esc(t)}</span>`).join("")}</div>` : "";

  $("#modalBody").dataset.itemId = it.id;
  $("#modalBody").innerHTML = `
    <div class="modal-meta">
      <span class="score ${cls}">${it.importance} · ${scoreLabel(it.importance)}</span>
      <span>${esc(it.source || "未知来源")}</span>
      ${tierBadge(it.source_tier)}
      <i class="dot-sep"></i>
      <span>${it.published_at ? new Date(it.published_at * 1000).toLocaleString("zh-CN", { hour12: false }) : ""}</span>
      ${it.topic ? `<span class="mini-tag">${esc(it.topic)}</span>` : ""}
    </div>
    <h2>${esc(it.title)}</h2>
    ${tags}
    ${it.summary ? `<div class="modal-summary">${esc(it.summary)}</div>` : ""}
    ${it.attachment ? `<h4>投递截图</h4><img class="modal-shot" src="${esc(it.attachment)}" alt="投递截图">` : ""}
    ${it.verify_hint ? `<div class="verify-note">文中出现「${esc(it.verify_hint)}」类引用，建议回源核对后再采信或转发。</div>` : ""}
    ${points}
    ${entities}
    ${it.content ? `<h4>正文摘录</h4><div class="content">${esc(it.content)}</div>` : ""}
    <div class="modal-rating"><span>人工评判</span>${ratingGroup(it)}</div>
    <div class="modal-actions">
      ${safeUrl(it.url) ? `<a class="btn primary" href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener">打开原文 ↗</a>` : ""}
      <button class="btn ghost" data-modal-act="read" data-id="${it.id}" data-next="${it.read ? 0 : 1}">${it.read ? "标记未读" : "标记已读"}</button>
      <button class="btn ghost" data-modal-act="star" data-id="${it.id}" data-next="${it.starred ? 0 : 1}">${it.starred ? "取消收藏" : "收藏"}</button>
      <button class="btn danger" data-modal-discard="${it.id}">丢弃</button>
    </div>`;
  $("#modalMask").classList.remove("hidden");
}

function closeModal() {
  $("#modalMask").classList.add("hidden");
  $("#modalMask").classList.remove("alert-mode");
}

/* ---------------- 简报 ---------------- */

function inlineMd(text) {
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}

function mdToHtml(md) {
  const out = [];
  let inList = false;
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (line.startsWith("- ")) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push("<li>" + inlineMd(line.slice(2)) + "</li>");
      continue;
    }
    if (inList) { out.push("</ul>"); inList = false; }
    if (!line) { out.push(""); continue; }
    if (line.startsWith("### ")) out.push("<h3>" + inlineMd(line.slice(4)) + "</h3>");
    else if (line.startsWith("## ")) out.push("<h2>" + inlineMd(line.slice(3)) + "</h2>");
    else if (line.startsWith("# ")) out.push("<h1>" + inlineMd(line.slice(2)) + "</h1>");
    else if (line.startsWith("> ")) out.push("<blockquote>" + inlineMd(line.slice(2)) + "</blockquote>");
    else if (line === "---") out.push("<hr>");
    else out.push("<p>" + inlineMd(line) + "</p>");
  }
  if (inList) out.push("</ul>");
  return out.join("\n");
}

async function loadDigest(day) {
  if (!day) {
    const input = $("#digestDate");
    if (!input.value) input.value = new Date().toISOString().slice(0, 10);
    day = input.value;
  }
  $("#digestDate").value = day;
  try {
    const data = await api("/api/digest?day=" + day);
    if (data.content) {
      $("#digestBody").innerHTML = mdToHtml(data.content);
      $("#digestHint").textContent = `${day} · ${data.item_count} 条`;
    } else {
      $("#digestBody").innerHTML = `<div class="empty" style="padding:40px 0">${day} 还没有生成简报，点击「生成简报」。</div>`;
      $("#digestHint").textContent = "";
    }
  } catch (e) { toast(e.message, "err"); }
}

async function generateDigest() {
  const btn = $("#btnGenDigest");
  btn.disabled = true;
  btn.textContent = "生成中…";
  try {
    const day = $("#digestDate").value || new Date().toISOString().slice(0, 10);
    const data = await api("/api/digest/generate", { method: "POST", body: { day } });
    $("#digestBody").innerHTML = mdToHtml(data.content);
    $("#digestHint").textContent = `${data.day} · ${data.item_count} 条`;
    toast("简报已生成", "ok");
    loadEvents();
  } catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; btn.textContent = "生成简报"; }
}

/* ---------------- 信息源 ---------------- */

async function loadSources() {
  let feeds;
  try { feeds = await api("/api/feeds"); } catch (e) { return toast(e.message, "err"); }
  state.feeds = feeds;
  const totalItems = feeds.reduce((s, f) => s + (f.item_count || 0), 0);
  $("#feedSummary").textContent = `${feeds.length} 个订阅 · ${totalItems} 条情报`;

  $("#feedTable").innerHTML = `
    <thead><tr>
      <th>名称 / 地址</th><th>分类</th><th>等级</th><th>最近采集</th><th>状态</th><th>情报数</th><th></th>
    </tr></thead>
    <tbody>${feeds.map(f => `
      <tr>
        <td>
          <div class="feed-title">${esc(f.title)}</div>
          <div class="feed-url">${esc(f.url)}</div>
        </td>
        <td>${esc(f.category || "未分类")}</td>
        <td>
          <select class="select tier-select" data-feed-tier="${f.id}" title="信源等级">
            ${["official", "industry", "media"].map(t =>
              `<option value="${t}" ${(f.tier || "media") === t ? "selected" : ""}>${TIER_LABELS[t]}</option>`).join("")}
          </select>
        </td>
        <td>${f.last_fetch_at ? relTime(f.last_fetch_at) : "—"}</td>
        <td><span class="status-pill ${statusCls(f)}">${esc(f.last_status || (f.enabled ? "等待采集" : "已停用"))}</span></td>
        <td>${f.item_count || 0}</td>
        <td>
          <div class="cell-actions">
            <button class="mini-btn" data-feed-fetch="${f.id}">采集</button>
            <button class="mini-btn" data-feed-toggle="${f.id}" data-enabled="${f.enabled}">${f.enabled ? "停用" : "启用"}</button>
            <button class="mini-btn danger" data-feed-del="${f.id}" data-title="${esc(f.title)}">删除</button>
          </div>
        </td>
      </tr>`).join("")}
    </tbody>`;
  $("#feedSelect").innerHTML = `<option value="">全部来源</option>` +
    `<option value="manual" ${state.feedId === "manual" ? "selected" : ""}>人工投递</option>` +
    feeds.map(f => `<option value="${f.id}" ${String(f.id) === String(state.feedId) ? "selected" : ""}>${esc(f.title)}</option>`).join("");
}

function statusCls(f) {
  if (!f.enabled) return "idle";
  if ((f.last_status || "").startsWith("失败")) return "err";
  if (f.last_status) return "ok";
  return "idle";
}

/* ---------------- 资源库 ---------------- */

async function loadResources() {
  let list;
  try { list = await api("/api/resources"); } catch (e) { return toast(e.message, "err"); }
  const order = [];
  const groups = {};
  list.forEach(r => {
    const cat = r.category || "未分类";
    if (!groups[cat]) { groups[cat] = []; order.push(cat); }
    groups[cat].push(r);
  });
  $("#resourceGroups").innerHTML = order.length ? order.map(cat => `
    <div class="panel">
      <div class="panel-head"><h3>${esc(cat)}</h3><span class="panel-hint">${groups[cat].length} 个站点</span></div>
      <div class="resource-grid">
        ${groups[cat].map(r => `
          <div class="resource-card">
            <div class="resource-top">
              <span class="resource-name">${esc(r.name)}</span>
              ${tierBadge(r.tier)}
            </div>
            ${r.note ? `<div class="resource-note">${esc(r.note)}</div>` : ""}
            <div class="resource-url">${esc(r.url || "")}</div>
            <div class="resource-actions">
              ${safeUrl(r.url) ? `<a class="mini-btn" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">打开站点</a>` : ""}
              <button class="mini-btn" data-res-sub="${r.id}">尝试订阅</button>
              <button class="mini-btn danger" data-res-del="${r.id}" data-name="${esc(r.name)}">删除</button>
            </div>
          </div>`).join("")}
      </div>
    </div>`).join("") : `<div class="panel"><div class="empty">资源库为空，先添加一个权威站点。</div></div>`;
}

/* ---------------- 主题 ---------------- */

async function loadTopics() {
  let topics;
  try { topics = await api("/api/topics"); } catch (e) { return toast(e.message, "err"); }
  state.topics = topics;
  const counts = {};
  if (state.overview && state.overview.topics) state.overview.topics.forEach(t => counts[t.name] = t.count);

  $("#topicGrid").innerHTML = topics.length ? topics.map(t => `
    <div class="topic-card" data-topic="${t.id}">
      <div class="topic-head">
        <span class="topic-name">${esc(t.name)}</span>
        <span class="topic-weight">权重 ${t.weight}</span>
      </div>
      <div class="topic-kws">${(t.keywords || []).map(k => `<span>${esc(k)}</span>`).join("") || '<span>未设置关键词</span>'}</div>
      <div class="topic-meta">
        <span>命中 ${counts[t.name] || 0} 条</span>
        <span>
          <button class="mini-btn" data-topic-edit="${t.id}">编辑</button>
          <button class="mini-btn danger" data-topic-del="${t.id}">删除</button>
        </span>
      </div>
    </div>`).join("") : `<div class="panel"><div class="empty">还没有关注主题，先添加一个吧。</div></div>`;
}

function editTopic(id) {
  const t = state.topics.find(x => x.id === id);
  if (!t) return;
  $("#modalBody").innerHTML = `
    <h2>编辑主题</h2>
    <div class="form">
      <label>主题名称
        <input class="input" id="topicEditName" value="${esc(t.name)}">
      </label>
      <label>关键词（用逗号分隔）
        <input class="input" id="topicEditKws" value="${esc((t.keywords || []).join("，"))}">
      </label>
      <label>权重 <b class="weight-val" id="topicEditWeightVal">${t.weight}</b>
        <input type="range" id="topicEditWeight" class="range" min="1" max="30" step="1" value="${t.weight}">
      </label>
      <p class="note">权重越高，命中该主题关键词时对重要度的加成越大（1-30）。修改只影响之后加工的情报，已入库的分数不变。</p>
    </div>
    <div class="modal-actions">
      <button class="btn primary" id="topicEditSave">保存</button>
      <button class="btn" id="topicEditCancel">取消</button>
    </div>`;
  $("#modalMask").classList.remove("hidden");

  const range = $("#topicEditWeight");
  range.addEventListener("input", () => { $("#topicEditWeightVal").textContent = range.value; });
  $("#topicEditCancel").addEventListener("click", closeModal);
  $("#topicEditSave").addEventListener("click", async () => {
    const name = $("#topicEditName").value.trim();
    if (!name) return toast("主题名称不能为空", "err");
    try {
      await api(`/api/topics/${id}`, {
        method: "PATCH",
        body: { name, keywords: $("#topicEditKws").value, weight: Number(range.value) }
      });
      closeModal();
      toast("主题已更新", "ok");
      loadTopics();
    } catch (e) { toast(e.message, "err"); }
  });
}

/* ---------------- 设置 ---------------- */

async function loadSettings() {
  let cfg;
  try { cfg = await api("/api/settings"); } catch (e) { return toast(e.message, "err"); }
  state.settings = cfg;
  $("#aiEnabled").checked = !!cfg.ai.enabled;
  $("#aiBaseUrl").value = cfg.ai.base_url || "";
  $("#aiModel").value = cfg.ai.model || "";
  $("#aiBatch").value = cfg.ai.batch_size || 6;
  $("#aiApiKey").value = "";
  $("#aiApiKey").placeholder = cfg.ai.api_key_set ? "已保存，留空则不修改" : "请输入 API Key";
  $("#aiStatus").textContent = cfg.ai.api_key_set ? "已配置密钥" : "未配置密钥";
  $("#aiStatus").className = "hint " + (cfg.ai.api_key_set ? "ok" : "");

  $("#schedMinutes").value = cfg.schedule.fetch_minutes;
  $("#schedLimit").value = cfg.schedule.process_limit;
  $("#schedDigest").value = cfg.schedule.digest_time;
  $("#collectMax").value = cfg.collect.max_items_per_feed;

  const ov = state.overview || await api("/api/overview").catch(() => null);
  if (ov) $("#dataTotal").textContent = ov.total + " 条";
}

async function saveSettings() {
  const btn = $("#btnSaveSettings");
  btn.disabled = true;
  const payload = {
    ai: {
      enabled: $("#aiEnabled").checked,
      base_url: $("#aiBaseUrl").value.trim(),
      model: $("#aiModel").value.trim(),
      batch_size: Number($("#aiBatch").value) || 6,
      api_key: $("#aiApiKey").value.trim(),
    },
    schedule: {
      fetch_minutes: Number($("#schedMinutes").value) || 30,
      process_limit: Number($("#schedLimit").value) || 40,
      digest_time: $("#schedDigest").value || "09:00",
    },
    collect: { max_items_per_feed: Number($("#collectMax").value) || 30 },
  };
  try {
    const cfg = await api("/api/settings", { method: "POST", body: payload });
    state.settings = cfg;
    $("#saveHint").textContent = "已保存 " + new Date().toLocaleTimeString("zh-CN", { hour12: false });
    $("#saveHint").className = "hint ok";
    $("#aiApiKey").value = "";
    toast("设置已保存", "ok");
  } catch (e) {
    $("#saveHint").textContent = e.message;
    $("#saveHint").className = "hint err";
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- 任务 ---------------- */

async function runFetch() {
  try {
    const res = await api("/api/tasks/run", { method: "POST", body: {} });
    if (res.busy) return toast(res.message || "任务正在执行", "err");
    toast("已开始采集，稍候查看结果", "ok");
    pollStatus(true);
  } catch (e) { toast(e.message, "err"); }
}

async function runProcess() {
  try {
    const res = await api("/api/tasks/process", { method: "POST", body: {} });
    if (res.busy) return toast(res.message || "任务正在执行", "err");
    toast("已开始加工队列", "ok");
    pollStatus(true);
  } catch (e) { toast(e.message, "err"); }
}

let pollTimer = null;
async function pollStatus(force = false) {
  try {
    const st = await api("/api/tasks/status");
    const wasRunning = state.running;
    state.running = !!st.running;
    $("#runDot").classList.toggle("busy", state.running);
    $("#runText").textContent = state.running ? (st.stage || "执行中") : "空闲";
    $("#btnFetch").classList.toggle("loading", state.running);
    $("#btnProcess").disabled = state.running;

    if (wasRunning && !state.running) {
      const perr = st.last_result && st.last_result.processed && st.last_result.processed.error;
      if (perr) {
        const remain = st.last_result.processed.remaining;
        const kept = remain > 0 ? `剩余 ${remain} 条情报仍在加工队列，稍后点击「加工队列」重试即可。` : "情报仍在加工队列，稍后重试即可。";
        showAlert("模型加工失败", kept, `错误原因：${perr}`);
      } else {
        toast("任务已完成", "ok");
      }
      if (state.view === "overview") loadOverview();
      if (state.view === "feed") loadFeed(true);
      if (state.view === "sources") loadSources();
      if (state.view === "topics") loadTopics();
      loadSettingsCounters();
    }
    if (!state.running && st.last_result && state.overview) {
      const f = st.last_result.fetched || {};
      if (f.new != null) $("#footMeta").textContent = `上次采集新增 ${f.new} 条 · 本地运行`;
    }
  } catch (e) { /* ignore */ }
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollStatus, state.running ? 2500 : 7000);
}

async function loadSettingsCounters() {
  try {
    const ov = await api("/api/overview");
    state.overview = ov;
    $("#navUnread").textContent = ov.unread;
    $("#pendingCount").textContent = ov.pending;
    if ($("#dataTotal")) $("#dataTotal").textContent = ov.total + " 条";
  } catch (e) { /* ignore */ }
}

/* ---------------- 事件绑定 ---------------- */

function bind() {
  $$(".nav-item").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));

  document.addEventListener("click", (e) => {
    const goto = e.target.closest("[data-goto]");
    if (goto) {
      e.preventDefault();
      if (goto.dataset.status) {
        state.status = goto.dataset.status;
        $$("#statusChips .chip").forEach(c => c.classList.toggle("active", c.dataset.status === state.status));
      }
      switchView(goto.dataset.goto);
      return;
    }

    const rate = e.target.closest("[data-rate]");
    if (rate) {
      e.stopPropagation();
      setRating(Number(rate.dataset.id), rate.dataset.rate);
      return;
    }

    const discard = e.target.closest("[data-discard]");
    if (discard) {
      e.stopPropagation();
      discardItem(Number(discard.dataset.discard));
      return;
    }

    const pick = e.target.closest("[data-pick]");
    if (pick) {
      e.stopPropagation();
      togglePick(Number(pick.dataset.pick), pick.checked);
      return;
    }

    const open = e.target.closest("[data-open]");
    if (open) {
      const act = e.target.closest("[data-act]");
      if (act) {
        e.stopPropagation();
        toggleItem(Number(act.dataset.id), act.dataset.act === "star" ? "starred" : "read");
        return;
      }
      openItem(Number(open.dataset.open));
      return;
    }

    const tag = e.target.closest("[data-tag]");
    if (tag) {
      state.tag = tag.dataset.tag;
      state.q = "";
      switchView("feed");
      return;
    }

    const modalAct = e.target.closest("[data-modal-act]");
    if (modalAct) {
      const field = modalAct.dataset.modalAct === "star" ? "starred" : "read";
      toggleItem(Number(modalAct.dataset.id), field, modalAct.dataset.next === "1");
      closeModal();
      return;
    }

    const modalDiscard = e.target.closest("[data-modal-discard]");
    if (modalDiscard) {
      discardItem(Number(modalDiscard.dataset.modalDiscard));
      return;
    }

    const feedFetch = e.target.closest("[data-feed-fetch]");
    if (feedFetch) {
      api("/api/tasks/run", { method: "POST", body: { feed_id: Number(feedFetch.dataset.feedFetch) } })
        .then(r => { toast(r.busy ? "任务正在执行" : "已开始采集该订阅", r.busy ? "err" : "ok"); pollStatus(true); })
        .catch(err => toast(err.message, "err"));
      return;
    }

    const feedToggle = e.target.closest("[data-feed-toggle]");
    if (feedToggle) {
      const enabled = feedToggle.dataset.enabled === "1" || feedToggle.dataset.enabled === "true";
      api("/api/feeds/" + feedToggle.dataset.feedToggle, { method: "PATCH", body: { enabled: !enabled } })
        .then(() => { toast(enabled ? "已停用" : "已启用", "ok"); loadSources(); })
        .catch(err => toast(err.message, "err"));
      return;
    }

    const feedDel = e.target.closest("[data-feed-del]");
    if (feedDel) {
      if (!confirm(`删除信息源「${feedDel.dataset.title}」及其情报？`)) return;
      api("/api/feeds/" + feedDel.dataset.feedDel, { method: "DELETE" })
        .then(() => { toast("已删除", "ok"); loadSources(); loadSettingsCounters(); })
        .catch(err => toast(err.message, "err"));
      return;
    }

    const topicEdit = e.target.closest("[data-topic-edit]");
    if (topicEdit) return editTopic(Number(topicEdit.dataset.topicEdit));

    const resSub = e.target.closest("[data-res-sub]");
    if (resSub) {
      api("/api/resources/" + resSub.dataset.resSub + "/subscribe", { method: "POST" })
        .then(r => { toast(`已接入「${r.title}」，发现 ${r.sample} 条内容`, "ok"); pollStatus(true); })
        .catch(err => toast(err.message, "err"));
      return;
    }

    const resDel = e.target.closest("[data-res-del]");
    if (resDel) {
      if (!confirm(`从资源库删除「${resDel.dataset.name}」？`)) return;
      api("/api/resources/" + resDel.dataset.resDel, { method: "DELETE" })
        .then(() => { toast("已删除", "ok"); loadResources(); })
        .catch(err => toast(err.message, "err"));
      return;
    }

    const topicDel = e.target.closest("[data-topic-del]");
    if (topicDel) {
      if (!confirm("删除该关注主题？")) return;
      api("/api/topics/" + topicDel.dataset.topicDel, { method: "DELETE" })
        .then(() => { toast("已删除", "ok"); loadTopics(); })
        .catch(err => toast(err.message, "err"));
    }
  });

  document.addEventListener("change", (e) => {
    const sel = e.target.closest("[data-feed-tier]");
    if (!sel) return;
    const id = sel.dataset.feedTier;
    const tier = sel.value;
    api("/api/feeds/" + id, { method: "PATCH", body: { tier } })
      .then(() => toast(`等级已改为「${TIER_LABELS[tier]}」，仅影响之后加工的情报`, "ok"))
      .catch(err => toast(err.message, "err"));
  });

  $("#btnFetch").addEventListener("click", runFetch);
  $("#btnProcess").addEventListener("click", runProcess);
  $("#btnMore").addEventListener("click", () => loadFeed(false));

  $("#statusChips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    state.status = chip.dataset.status;
    $$("#statusChips .chip").forEach(c => c.classList.toggle("active", c === chip));
    state.tag = "";
    loadFeed(true);
  });
  $("#feedSelect").addEventListener("change", (e) => { state.feedId = e.target.value; loadFeed(true); });
  $("#topicSelect").addEventListener("change", (e) => { state.topic = e.target.value; loadFeed(true); });

  $("#selectAll").addEventListener("change", (e) => {
    if (e.target.checked) {
      state.allSelected = true;
      state.selected.clear();
      state.excluded.clear();
    } else {
      clearSelection(false);
    }
    $$("#feedList [data-pick]").forEach(cb => { cb.checked = e.target.checked; });
    syncSelectionUI();
  });
  $("#discardSelected").addEventListener("click", discardSelected);

  let searchTimer = null;
  $("#feedSearch").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value.trim(); loadFeed(true); }, 300);
  });
  $("#globalSearch").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    state.q = e.target.value.trim();
    state.status = "all";
    state.tag = "";
    $$("#statusChips .chip").forEach(c => c.classList.toggle("active", c.dataset.status === "all"));
    switchView("feed");
  });

  $("#feedForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const fields = form.elements;
    const btn = $("button", form);
    btn.disabled = true;
    btn.textContent = "校验中…";
    try {
      const res = await api("/api/feeds", {
        method: "POST",
        body: {
          url: fields.url.value.trim(),
          title: fields.title.value.trim(),
          category: fields.category.value.trim(),
          tier: fields.tier.value,
        },
      });
      toast(`已添加「${res.title}」，发现 ${res.sample} 条内容`, "ok");
      form.reset();
      loadSources();
      api("/api/tasks/run", { method: "POST", body: { feed_id: res.id } }).catch(() => {});
      pollStatus(true);
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; btn.textContent = "添加"; }
  });

  $$("#intakeRating .rating-btn").forEach(btn => btn.addEventListener("click", () => {
    $$("#intakeRating .rating-btn").forEach(b => b.classList.toggle("on", b === btn));
    $("#intakeForm").elements.rating.value = btn.dataset.pick;
  }));

  $("#intakeForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const f = form.elements;
    const btn = $('button[type="submit"]', form);
    btn.disabled = true;
    try {
      const res = await api("/api/intake", {
        method: "POST",
        body: {
          url: f.url.value.trim(),
          rating: f.rating.value,
        },
      });
      toast(res.duplicate ? "已存在相同投递" : "已投递，正在提取原文并加入加工队列", "ok");
      form.reset();
      $$("#intakeRating .rating-btn").forEach(b => b.classList.toggle("on", b.dataset.pick === "mid"));
      f.rating.value = "mid";
      loadIntake();
      if (!res.duplicate) setTimeout(loadIntake, 4000);
      loadSettingsCounters();
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; }
  });

  $("#resourceForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const fields = form.elements;
    const btn = $("button", form);
    btn.disabled = true;
    try {
      await api("/api/resources", {
        method: "POST",
        body: {
          name: fields.name.value.trim(),
          url: fields.url.value.trim(),
          category: fields.category.value,
          tier: fields.tier.value,
          note: fields.note.value.trim(),
        },
      });
      toast("已加入资源库", "ok");
      form.reset();
      loadResources();
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; }
  });

  $("#topicForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const fields = form.elements;
    try {
      await api("/api/topics", {
        method: "POST",
        body: {
          name: fields.name.value.trim(),
          keywords: fields.keywords.value.trim(),
          weight: Number(fields.weight.value) || 10,
        },
      });
      toast("主题已添加", "ok");
      form.reset();
      fields.weight.value = 10;
      loadTopics();
    } catch (err) { toast(err.message, "err"); }
  });

  $("#modalClose").addEventListener("click", closeModal);
  $("#modalMask").addEventListener("click", (e) => { if (e.target === $("#modalMask")) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  $("#digestDate").addEventListener("change", (e) => loadDigest(e.target.value));
  $("#btnGenDigest").addEventListener("click", generateDigest);
  $("#btnCopyDigest").addEventListener("click", async () => {
    const text = $("#digestBody").innerText;
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制到剪贴板", "ok");
    } catch (e) { toast("复制失败，请手动选择", "err"); }
  });

  $("#btnSaveSettings").addEventListener("click", saveSettings);
  $("#btnTestAI").addEventListener("click", async () => {
    const el = $("#aiStatus");
    el.textContent = "测试中…";
    el.className = "hint";
    try {
      const res = await api("/api/settings/test-ai", {
        method: "POST",
        body: {
          base_url: $("#aiBaseUrl").value.trim(),
          model: $("#aiModel").value.trim(),
          api_key: $("#aiApiKey").value.trim(),
        },
      });
      el.textContent = "连接正常：" + res.reply;
      el.className = "hint ok";
    } catch (err) {
      el.textContent = err.message;
      el.className = "hint err";
    }
  });
}

/* ---------------- 启动 ---------------- */

document.addEventListener("DOMContentLoaded", () => {
  bind();
  $("#digestDate").value = new Date().toISOString().slice(0, 10);
  switchView(location.hash.replace("#", "") || "overview");
  loadFeedsForFilter();
  loadTopicsForFilter();
  loadSettingsCounters();
  pollStatus(true);
});

async function loadFeedsForFilter() {
  try {
    const feeds = await api("/api/feeds");
    state.feeds = feeds;
    $("#feedSelect").innerHTML = `<option value="">全部来源</option>` +
      `<option value="manual">人工投递</option>` +
      feeds.map(f => `<option value="${f.id}">${esc(f.title)}</option>`).join("");
  } catch (e) { /* ignore */ }
}

async function loadTopicsForFilter() {
  try {
    const topics = await api("/api/topics");
    $("#topicSelect").innerHTML = `<option value="">全部主题</option>` +
      topics.map(t => `<option value="${esc(t.name)}" ${t.name === state.topic ? "selected" : ""}>${esc(t.name)}</option>`).join("");
  } catch (e) { /* ignore */ }
}
