const P = window.PLATFORMS;
let DAYS = 30;
let CURRENT = "overview";
let PLATFORM_FILTER = "all";
const charts = {};

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt = (n) => {
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
};
const platColor = (k) => P[k] ? P[k].color : css("--series-1");
const platLabel = (k) => (P[k] ? P[k].label : k);
const timeAgo = (iso) => {
  const d = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (d <= 0) return "today";
  if (d === 1) return "yesterday";
  return d + "d ago";
};

const VIEW_META = {
  overview: ["Overview", "Everything your brand posted, at a glance."],
  content: ["My Content", "Your latest shorts, reels and videos — and how they performed."],
  hooks: ["Hook Lab", "Compare hooks and platforms to find what actually lands."],
  competitors: ["Competitors", "Track concurrent channels and reverse-engineer what blew up."],
};

/* ---------- Navigation ---------- */
document.querySelectorAll(".nav-item").forEach((b) =>
  b.addEventListener("click", () => switchView(b.dataset.view)));

function switchView(view) {
  CURRENT = view;
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  document.getElementById("view-" + view).classList.remove("hidden");
  document.getElementById("viewTitle").textContent = VIEW_META[view][0];
  document.getElementById("viewSub").textContent = VIEW_META[view][1];
  load();
}

/* ---------- Date range ---------- */
document.querySelectorAll(".range-btn").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".range-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    DAYS = +b.dataset.days;
    load();
  }));

/* ---------- Theme ---------- */
document.getElementById("themeBtn").addEventListener("click", () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  load(); // re-render charts with new token colors
});

/* ---------- Loader ---------- */
function load() {
  if (CURRENT === "overview") loadOverview();
  else if (CURRENT === "content") loadContent();
  else if (CURRENT === "hooks") loadHooks();
  else if (CURRENT === "competitors") loadCompetitors();
}

const baseAxis = () => ({
  grid: { color: css("--grid"), drawBorder: false },
  border: { display: false },
  ticks: { color: css("--muted"), font: { family: css("--font"), size: 11 } },
});

/* ---------- OVERVIEW ---------- */
async function loadOverview() {
  const d = await (await fetch(`/api/overview?days=${DAYS}`)).json();
  const k = d.kpis;
  document.getElementById("kpis").innerHTML = `
    ${kpi("Views", fmt(k.views || 0), `across ${k.posts || 0} posts`)}
    ${kpi("Followers", fmt(k.followers || 0), "all platforms")}
    ${kpi("Avg engagement", (k.avgEngagement || 0) + "%", "likes+comments+shares+saves")}
    ${kpi("Top post", k.bestPost ? fmt(k.bestPost.views) : "—",
          k.bestPost ? platLabel(k.bestPost.platform) + " · " + k.bestPost.title : "")}`;

  // Trend (area line)
  const grad = (ctx) => {
    const g = ctx.createLinearGradient(0, 0, 0, 260);
    g.addColorStop(0, css("--series-1") + "55"); g.addColorStop(1, css("--series-1") + "00");
    return g;
  };
  mkChart("trendChart", {
    type: "line",
    data: {
      labels: d.trend.map((t) => t.date.slice(5)),
      datasets: [{
        label: "Views", data: d.trend.map((t) => t.views),
        borderColor: css("--series-1"), borderWidth: 2, tension: .35,
        fill: true, backgroundColor: (c) => grad(c.chart.ctx),
        pointRadius: 0, pointHoverRadius: 5, pointHoverBackgroundColor: css("--series-1"),
      }],
    },
    options: lineOpts(),
  });

  // Platform doughnut
  mkChart("platformChart", {
    type: "doughnut",
    data: {
      labels: d.platforms.map((p) => p.label),
      datasets: [{
        data: d.platforms.map((p) => p.views),
        backgroundColor: d.platforms.map((p) => p.color),
        borderColor: css("--surface"), borderWidth: 3, hoverOffset: 6,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: "62%",
      plugins: { legend: { display: false }, tooltip: tooltip((c) => `${c.label}: ${fmt(c.parsed)} views`) },
    },
  });
  document.getElementById("platformLegend").innerHTML = d.platforms.map((p) =>
    `<span class="legend-item"><span class="dot" style="background:${p.color}"></span>${p.label} · ${fmt(p.views)}</span>`).join("");

  document.getElementById("topPosts").innerHTML = d.topPosts.map(postCard).join("");
}

const kpi = (label, value, sub) =>
  `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-sub">${sub || ""}</div></div>`;

function postCard(p) {
  return `<div class="post">
    <div class="post-top">
      <span class="tag" style="background:${platColor(p.platform)}">${platLabel(p.platform)}</span>
      <span class="muted small">${timeAgo(p.published_at)}</span>
    </div>
    <div class="post-title">${p.title}</div>
    <div class="post-hook">“${p.hook}”</div>
    <div class="post-stats">
      <div><b>${fmt(p.views)}</b>views</div>
      <div><b>${p.engagement}%</b>eng</div>
      <div><b>${p.retention}%</b>retention</div>
    </div>
  </div>`;
}

/* ---------- CONTENT ---------- */
function buildPlatformFilter() {
  const el = document.getElementById("platformFilter");
  if (el.dataset.built) return;
  Object.keys(P).forEach((k) => {
    const b = document.createElement("button");
    b.className = "chip"; b.dataset.platform = k; b.textContent = P[k].label;
    el.appendChild(b);
  });
  el.querySelectorAll(".chip").forEach((c) =>
    c.addEventListener("click", () => {
      el.querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));
      c.classList.add("active");
      PLATFORM_FILTER = c.dataset.platform;
      loadContent();
    }));
  el.dataset.built = "1";
}

async function loadContent() {
  buildPlatformFilter();
  const posts = await (await fetch(`/api/content?days=${DAYS}&platform=${PLATFORM_FILTER}`)).json();
  const max = Math.max(1, ...posts.map((p) => p.views));
  document.querySelector("#contentTable tbody").innerHTML = posts.map((p) => `
    <tr>
      <td class="ptitle">${p.title}<div class="muted small">“${p.hook}”</div></td>
      <td><span class="tag" style="background:${platColor(p.platform)}">${platLabel(p.platform)}</span></td>
      <td>${p.type}</td>
      <td class="num"><div class="bar-cell"><span class="mini-bar" style="width:${Math.max(6, p.views / max * 70)}px;background:${platColor(p.platform)}"></span>${fmt(p.views)}</div></td>
      <td class="num">${p.engagement}%</td>
      <td class="num">${p.retention}%</td>
      <td>${timeAgo(p.published_at)}</td>
    </tr>`).join("") || `<tr><td colspan="7" class="muted" style="padding:24px;text-align:center">No posts in this window.</td></tr>`;
}

/* ---------- HOOK LAB ---------- */
async function loadHooks() {
  const d = await (await fetch(`/api/hooks?days=${Math.max(DAYS, 90)}`)).json();

  mkChart("hookPlatformChart", {
    type: "bar",
    data: {
      labels: d.byPlatform.map((x) => x.label),
      datasets: [{
        data: d.byPlatform.map((x) => x.avgViews),
        backgroundColor: d.byPlatform.map((x) => x.color),
        borderRadius: 4, borderSkipped: false, maxBarThickness: 64,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false },
        tooltip: tooltip((c) => `${c.label}: ${fmt(c.parsed.y)} avg views · ${d.byPlatform[c.dataIndex].avgEngagement}% eng`) },
      scales: { x: baseAxis(), y: { ...baseAxis(), ticks: { ...baseAxis().ticks, callback: fmt } } },
    },
  });
  document.getElementById("hookPlatformLegend").innerHTML = d.byPlatform.map((p) =>
    `<span class="legend-item"><span class="dot" style="background:${p.color}"></span>${p.label} · ${p.count} posts</span>`).join("");

  const top = d.hooks.slice(0, 8);
  mkChart("hookBarChart", {
    type: "bar",
    data: {
      labels: top.map((h) => h.hook),
      datasets: [{
        data: top.map((h) => h.avgViews),
        backgroundColor: css("--series-1"), borderRadius: 4, borderSkipped: false, maxBarThickness: 22,
      }],
    },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: tooltip((c) => `${fmt(c.parsed.x)} avg views`) },
      scales: {
        x: { ...baseAxis(), ticks: { ...baseAxis().ticks, callback: fmt } },
        y: { ...baseAxis(), grid: { display: false }, ticks: { ...baseAxis().ticks, autoSkip: false,
          callback: function (v) { const l = this.getLabelForValue(v); return l.length > 34 ? l.slice(0, 33) + "…" : l; } } },
      },
    },
  });

  document.querySelector("#hookTable tbody").innerHTML = d.hooks.map((h) => `
    <tr>
      <td class="ptitle">“${h.hook}”</td>
      <td class="num">${h.count}</td>
      <td class="num">${fmt(h.avgViews)}</td>
      <td class="num">${h.avgEngagement}%</td>
      <td class="num">${h.avgRetention}%</td>
    </tr>`).join("");
}

/* ---------- COMPETITORS ---------- */
async function loadCompetitors() {
  const comps = await (await fetch(`/api/competitors?days=${DAYS}`)).json();
  document.getElementById("competitorList").innerHTML = comps.map((c) => {
    const initials = c.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    const hue = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5"][c.id % 5];
    const viral = c.viral.length ? c.viral.map((p) => `
      <div class="viral-item">
        <div><div class="t">${p.title} <span class="viral-flag">🚀 blew up</span></div>
          <div class="h">“${p.hook}” · ${platLabel(p.platform)} · ${timeAgo(p.published_at)}</div></div>
        <div class="v">${fmt(p.views)}</div>
      </div>`).join("") : `<div class="muted small">No breakout posts in this window.</div>`;
    const hooks = c.topHooks.map((h) =>
      `<div class="hook-pill"><span>“${h.hook}”</span><b>${fmt(h.avgViews)}</b></div>`).join("");
    return `<div class="comp">
      <div class="comp-head">
        <div class="comp-id">
          <div class="avatar" style="background:${css(hue)}">${initials}</div>
          <div><div class="comp-name">${c.name}</div><div class="muted small">${c.handle}</div></div>
        </div>
        <div class="comp-metrics">
          <div class="comp-metric"><b>${fmt(c.followers)}</b><span>followers</span></div>
          <div class="comp-metric"><b>${fmt(c.totalViews)}</b><span>views · ${DAYS}d</span></div>
          <div class="comp-metric"><b>${c.posts.length}</b><span>posts · ${DAYS}d</span></div>
        </div>
      </div>
      <div class="comp-cols">
        <div><p class="subhead">What blew up</p>${viral}</div>
        <div><p class="subhead">Their best hooks</p>${hooks || '<div class="muted small">—</div>'}</div>
      </div>
    </div>`;
  }).join("");
}

/* ---------- Chart helpers ---------- */
function mkChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}
function tooltip(labelFn) {
  return {
    backgroundColor: css("--surface-2"), titleColor: css("--ink"), bodyColor: css("--ink-2"),
    borderColor: css("--border"), borderWidth: 1, padding: 10, cornerRadius: 8, displayColors: false,
    callbacks: { label: labelFn },
  };
}
function lineOpts() {
  return {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: false }, tooltip: tooltip((c) => `${fmt(c.parsed.y)} views`) },
    scales: {
      x: { ...baseAxis(), grid: { display: false }, ticks: { ...baseAxis().ticks, maxTicksLimit: 8 } },
      y: { ...baseAxis(), ticks: { ...baseAxis().ticks, callback: fmt } },
    },
  };
}

/* ---------- Modal ---------- */
const modal = document.getElementById("modal");
document.getElementById("addBtn").addEventListener("click", () => modal.classList.remove("hidden"));
document.getElementById("modalClose").addEventListener("click", () => modal.classList.add("hidden"));
modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.add("hidden"); });
document.getElementById("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = Object.fromEntries(new FormData(e.target).entries());
  fd.published_at = new Date().toISOString();
  await fetch("/api/posts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fd) });
  modal.classList.add("hidden");
  e.target.reset();
  load();
});

/* ---------- Live sync ---------- */
const syncBtn = document.getElementById("syncBtn");
const syncStatus = document.getElementById("syncStatus");
syncBtn.addEventListener("click", async () => {
  syncBtn.disabled = true;
  syncStatus.textContent = "Syncing…";
  try {
    const d = await (await fetch("/api/sync", { method: "POST" })).json();
    if (d.error) { syncStatus.textContent = "⚠ " + d.error; }
    else {
      const ok = d.results.filter((r) => r.status === "ok");
      const total = ok.reduce((n, r) => n + (r.synced || 0), 0);
      if (ok.length) {
        const yt = ok.find((r) => r.retention === "youtube analytics");
        syncStatus.textContent = `✓ ${total} posts from ${ok.map((r) => r.platform).join(", ")}`
          + (yt ? " · retention ✓" : "");
        load();
      } else {
        syncStatus.innerHTML = "No platforms connected. See <b>.env.example</b>.";
      }
    }
  } catch (e) {
    syncStatus.textContent = "⚠ sync failed";
  }
  syncBtn.disabled = false;
  setTimeout(() => { syncStatus.textContent = ""; }, 9000);
});

/* ---------- Boot ---------- */
load();
