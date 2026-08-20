/* 猎迹 dashboard —— 玄：黑不是关掉光，是让光更显眼。
   数据形状 = 主agent的只读桥（spoor_view.py）的契约，前端只管 fetch。
   只读。单一事实源。记忆不出前端。 */
const $ = (s) => document.querySelector(s);
const api = (p) => fetch(p).then((r) => {
  if (!r.ok) throw new Error(r.status);
  return r.json();
});
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const hideSection = (id) => { const el = $(id); if (el) el.closest("section").style.display = "none"; };

/* 源文件路径图标：只有提供了 src/path 字段的数据层才出现（本地开发桥有，生产桥没有） */
const FOLDER_SVG = `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" ` +
  `stroke="currentColor" stroke-width="1.3" stroke-linejoin="round">` +
  `<path d="M1.8 4.2 A1 1 0 0 1 2.8 3.2 h3.4 l1.4 1.8 h5.6 a1 1 0 0 1 1 1 v6 a1 1 0 0 1 -1 1 H2.8 a1 1 0 0 1 -1 -1 z"/></svg>`;
const srcLink = (p) => p
  ? `<a class="src" title="${esc(p)}" onclick="openSrc('${encodeURIComponent(p)}');return false" href="#">${FOLDER_SVG}</a>`
  : "";
window.openSrc = (enc) =>
  fetch("/api/open?path=" + enc).then((r) => r.json()).then((d) => {
    if (!d.ok) console.warn("open failed:", d.error);
  }).catch(() => {});

/* 契约 v0.2：lifecycle/ecosystem/relates_to/milestones/docs 全部来自 API 的
   frontmatter 字段，前端只渲染不猜测。lifecycle.json 已退役（不 fetch）。 */

/* ---------- 路由 ---------- */
const VIEWS = ["home", "project", "docview"];
function show(view) {
  VIEWS.forEach((v) => $("#" + v).classList.toggle("hidden", v !== view));
}
function route() {
  const h = location.hash;
  if (h.startsWith("#/project/")) showProject(decodeURIComponent(h.slice(10)));
  else if (h.startsWith("#/doc/")) showDoc(decodeURIComponent(h.slice(6)));
  else show("home");
}
window.addEventListener("hashchange", route);
$("#brand").addEventListener("click", () => (location.hash = ""));
document.querySelectorAll(".back-btn").forEach((b) =>
  b.addEventListener("click", () => (location.hash = "")));

const count = (v) => Array.isArray(v) ? v.length : (v | 0);

/* ---------- 侧栏：生命周期分层 + 生态色徽章 / 档案房平铺 ---------- */
/* 契约 v0.2（用户侧 2026-08-18 定轴）：分层主轴 = 生命周期（生长/里程碑/毕业/胚胎），
   生态降为组内徽章，颜色由名字哈希派生——机制在代码里，数据在 API 里。 */
const LIFE_ORDER = ["生长", "里程碑", "毕业", "胚胎"];
const lifeGroups = (projects) => {
  const byLife = {};
  for (const p of projects) {
    const l = p.lifecycle || "生长";
    (byLife[l] = byLife[l] || []).push(p);
  }
  // 契约四值按叙事序；未知 lifecycle 值透传垫底，字典序稳定
  const rank = (l) => { const i = LIFE_ORDER.indexOf(l); return i >= 0 ? i : LIFE_ORDER.length; };
  return Object.entries(byLife).sort((a, b) =>
    rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
};
const docProjectEdges = (docs, projects) => {
  // 契约 v0.2：显式 docs 声明优先（w:2，权威）；未声明的项目退回名字前缀
  // 启发式（w:1）——零配置部署仍有连线，声明过的以声明为准。
  const edges = [];
  for (const p of projects) {
    const claimed = p.docs || [];
    const has = new Set(claimed);
    for (const d of docs) {
      if (has.has(d.doc)) edges.push([p.project, d.doc, 2]);
      else if (!claimed.length && (d.doc.startsWith(p.project) || p.project.startsWith(d.doc)))
        edges.push([p.project, d.doc, 1]);
    }
  }
  return edges;
};
const ecoColor = (eco) => {
  if (!eco) return "#8b8794"; // 未归属统一灰
  let h = 0;
  for (let i = 0; i < eco.length; i++) h = (h * 31 + eco.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 55% 70%)`;
};

async function renderSidebar() {
  // 项目：ecosystem 树形分组（顶层）+ lifecycle 徽章（主状态），全读 API
  try {
    const projects = await api("/api/projects");
    const nav = $("#lifecycle-nav");
    nav.innerHTML = "";
    for (const [life, group] of lifeGroups(projects)) {
      const det = document.createElement("details");
      det.open = true;
      det.innerHTML =
        `<summary><span class="lc-name">${esc(life)}</span>` +
        `<span class="lc-count">${group.length}</span></summary>`;
      const ul = document.createElement("ul");
      const ordered = group.slice().sort((a, b) =>
        (a.ecosystem || "zz").localeCompare(b.ecosystem || "zz") ||
        a.project.localeCompare(b.project));
      for (const p of ordered) {
        const li = document.createElement("li");
        li.innerHTML =
          `<a href="#/project/${encodeURIComponent(p.project)}">${esc(p.project)}` +
          (p.ecosystem ? `<span class="tag lc" style="color:${ecoColor(p.ecosystem)}">${esc(p.ecosystem)}</span>` : "") +
          (count(p.pits) ? `<span class="tag pit">${count(p.pits)}坑</span>` : "") +
          (count(p.pending_review) ? `<span class="tag human">${count(p.pending_review)}待审</span>` : "") +
          `</a>`;
        ul.appendChild(li);
      }
      det.appendChild(ul);
      nav.appendChild(det);
    }
    if (!nav.innerHTML) nav.innerHTML = `<div class="empty">还没有项目。</div>`;
  } catch { }

  // 档案房：平铺（不按标签套娃——侧边栏已表达状态，信息不重复）
  const anav = $("#archive-nav");
  try {
    const raw = await api("/api/archive");
    const docs = Array.isArray(raw) ? raw : (raw.docs || []);
    let html = `<div class="anav-title">档案房</div>`;
    html += docs.length
      ? `<ul class="flat">` + docs.map((d) =>
          `<li><a href="#/doc/${encodeURIComponent(d.doc)}" title="${d.versions} 个版本 · ${esc(d.ts)}">` +
          `${d.pinned ? "📌 " : ""}${esc(d.doc)}</a></li>`).join("") + `</ul>`
      : `<div class="empty">（还空着——还没长出来也是一种真实状态）</div>`;
    anav.innerHTML = html;
  } catch { anav.innerHTML = ""; }
}

/* ---------- 首页三段：待办 / 待审 / 自上次以来 ---------- */
const EV_ACTION = {
  put: "写了新一版归档",
  link: "连了一条关系",
  get: "取出读了",
  query: "查了",
  // 本地桥的事件类型（同源契约的扩展位）
  "journal.write": "落了条记录",
  "journal.read": "翻了工作记录",
  "journal.search": "搜了",
  "workbench.snippet_get": "取了复用件",
  "workbench.snippet_save": "存了复用件",
  "workbench.status": "更新了状态桌面",
  create: "开了间涂鸦房",
  cleanup: "清了间涂鸦房",
  export: "从涂鸦房导出了",
};
async function renderOverview() {
  const d = await api("/api/overview");

  $("#todo-list").innerHTML = (d.todos || []).length
    ? d.todos.map((t) =>
        `<div class="card"><div class="meta"><span class="who">${esc(t.project)}</span>${srcLink(t.src)}</div>` +
        `<div class="text">${esc(t.todo)}</div></div>`).join("")
    : `<div class="empty">没有挂着的待办 —— 干净的桌面也是痕迹。</div>`;

  $("#review-list").innerHTML = (d.pending_review || []).length
    ? d.pending_review.map((e) =>
        `<div class="card"><div class="meta"><span class="who">${esc(e.project)}</span>` +
        `<span class="tag human">${esc(e.mark)}</span><span>${esc(e.ts)}</span>${srcLink(e.src)}</div>` +
        `<div class="text">${esc(e.entry)}</div></div>`).join("")
    : `<div class="empty">没有等你拍板的事。</div>`;

  // 最近一天的归档流：主体加粗、人话动作、时间戳最后压透明
  const feed = d.recent_events || [];
  const cutoff = Date.now() - 24 * 3600 * 1000;
  const fresh = feed.filter((e) => new Date((e.ts || "").replace(" ", "T")) >= cutoff);
  const shown = fresh.length ? fresh : feed.slice(0, 8);
  $("#feed-hint").textContent = fresh.length
    ? `最近一天 · ${fresh.length} 条`
    : "最近一天没有新动静 —— 这是最后的几条";
  $("#feed").innerHTML = shown.map((e) => {
    const act = EV_ACTION[e.event] || e.event || "留下了痕迹";
    const linkTo = e.to_doc ? ` → ${esc(e.to_doc)}` : "";
    return `<div class="fitem"><div class="fline">` +
      `<strong class="who">${esc(e.doc || "?")}</strong> ${esc(act)}${linkTo}` +
      (e.source_ref ? `<span class="fsep">——</span><span class="ftext">${esc(e.source_ref).slice(0, 100)}</span>` : "") +
      `</div><span class="ftime">${esc((e.ts || "").slice(5, 16))}</span></div>`;
  }).join("");
}

/* ---------- 链接图：星座星图（同心环聚合，防膨胀） ----------
   契约 v0.2 映射：节点 = 档案 doc + 项目（生态=分区色），
   边 = archive_link + relates_to；doc↔project 显式 docs 声明优先，前缀启发式兜底。 */
async function renderGraph() {
  let data, projects = [];
  try {
    [data, projects] = await Promise.all([
      api("/api/graph"),
      api("/api/projects").catch(() => []),
    ]);
  } catch { return; }
  const svg = $("#graph");
  const docs = data.nodes || [];
  if (!docs.length && !projects.length) { svg.innerHTML = ""; return; }
  const W = 880, H = 380, cx = W / 2, cy = H / 2;

  const nodes = docs.map((d) => ({ id: d.doc, type: "doc", pinned: d.pinned, w: d.versions || 1 }));
  for (const p of projects) {
    nodes.push({ id: p.project, type: "project", eco: p.ecosystem || "",
                 w: 2 + count(p.pits) + count(p.pending_review) });
  }
  const ids = new Set(nodes.map((n) => n.id));
  const edges = [];
  for (const e of data.edges || []) {
    const a = e.from_doc, b = e.to_doc;
    if (!a) continue;
    if (b && !ids.has(b)) {
      if (e.external) {
        const ext = "ext:" + b;
        if (!ids.has(ext)) { nodes.push({ id: ext, type: "ext", w: 1 }); ids.add(ext); }
        edges.push({ a, b: ext, w: 1 });
      }
      continue;
    }
    if (b) edges.push({ a, b, w: 1 + (e.relation === "parent" ? 1 : 0) });
  }
  // relates_to：项目间的横向边；对方不在本地墙上 → 画成虚影星（关系是真的，
  // 只是它住别处——比如 tideline 住在 VPS）
  for (const p of projects) {
    for (const r of p.relates_to || []) {
      if (!ids.has(p.project)) continue;
      let target = r.project;
      if (!ids.has(target)) {
        target = "ghost:" + r.project;
        if (!ids.has(target)) {
          nodes.push({ id: target, type: "ghost", w: 1 });
          ids.add(target);
        }
      }
      edges.push({ a: p.project, b: target, w: 2 });
    }
  }
  for (const [a, b, w] of docProjectEdges(docs, projects)) edges.push({ a, b, w });
  const deg = {};
  edges.forEach((e) => { deg[e.a] = (deg[e.a] || 0) + 1; deg[e.b] = (deg[e.b] || 0) + 1; });
  nodes.forEach((n) => (n.w += deg[n.id] || 0));

  // 同心环：重的靠中心，节点多了向外长新环，密度恒定
  const pos = {};
  let ringBase = 0;
  const placeGroup = (list, off) => {
    list = list.slice().sort((x, y) => y.w - x.w);
    let i = 0, ring = 0;
    while (i < list.length) {
      const r = ringBase + ring * 62;
      if (r === 0 && list.length === 1) { pos[list[0].id] = { x: cx, y: cy }; break; }
      const rr = Math.max(r, 62);
      const cap = Math.max(3, Math.floor((2 * Math.PI * rr) / 78));
      const n = Math.min(cap, list.length - i);
      for (let k = 0; k < n; k++) {
        const a = off + ring * 0.7 + (k / n) * Math.PI * 2;
        pos[list[i + k].id] = { x: cx + rr * Math.cos(a), y: cy + rr * 0.62 * Math.sin(a) };
      }
      i += n; ring++;
    }
    ringBase = Math.max(ringBase, 62) + (ring - 1) * 62 + 64;
  };
  placeGroup(nodes.filter((n) => n.type === "project"), -Math.PI / 2);
  placeGroup(nodes.filter((n) => n.type === "doc"), 0.3);
  placeGroup(nodes.filter((n) => n.type === "ext" || n.type === "ghost"), 0.9);
  const maxW = Math.max(...edges.map((e) => e.w), 1);

  let maxRy = 0;
  for (const id in pos) maxRy = Math.max(maxRy, Math.abs(pos[id].y - cy));
  svg.setAttribute("viewBox", `0 0 ${W} ${Math.max(H, Math.ceil(maxRy) * 2 + 70)}`);

  // 高斯模糊滤镜：光晕大柔光，亮核小柔边——星点是半透明发光的，不是贴纸
  let html =
    `<defs>` +
    `<filter id="soft" x="-300%" y="-300%" width="700%" height="700%"><feGaussianBlur stdDeviation="4"/></filter>` +
    `<filter id="softcore" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="1.1"/></filter>` +
    `</defs>`;
  for (const e of edges) {
    const a = pos[e.a], b = pos[e.b];
    if (!a || !b) continue;
    const o = 0.10 + 0.35 * (e.w / maxW);
    html += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"` +
      ` stroke="rgba(184,169,232,${o})" stroke-width="${0.5 + 1.2 * (e.w / maxW)}"/>`;
  }
  nodes.forEach((n, i) => {
    const p = pos[n.id];
    if (!p) return;
    const isDim = n.type === "ext" || n.type === "ghost";
    const col = isDim ? "#6b6774"
      : n.type === "project" ? ecoColor(n.eco)
      : n.pinned ? "#e8c169" : "#dcd2f7";
    const core = isDim ? 2.2 : n.type === "project" ? 4.2 : n.pinned ? 4.2 : 3.4;
    const label = n.type === "ext" ? n.id.slice(4, 22)
      : n.type === "ghost" ? n.id.slice(6) + " ↗"
      : n.id;
    const dash = n.type === "ghost" ? ` stroke="#8b8794" stroke-dasharray="2 3" fill-opacity="0.35"` : "";
    const delay = ((i * 0.7) % 3.5).toFixed(2);
    const dur = (2.8 + (i % 5) * 0.6).toFixed(2);
    html +=
      `<circle class="halo" filter="url(#soft)" style="animation-delay:${delay}s;animation-duration:${dur}s"` +
      ` cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${core * 3.2}" fill="${col}"/>` +
      `<circle class="core" filter="url(#softcore)" style="animation-delay:${delay}s;animation-duration:${dur}s"` +
      ` cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${core}" fill="${col}"${dash}>` +
      `<title>${esc(n.id)} · ${n.w} 痕迹</title></circle>` +
      `<text x="${p.x.toFixed(1)}" y="${(p.y - core - 9).toFixed(1)}" text-anchor="middle" class="glabel">${esc(label)}</text>`;
  });
  svg.innerHTML = html;
}

/* ---------- 涂鸦房（活的；桥没开这个口就整区隐藏） ---------- */
let scratchSeen = new Map();
let previewRunning = false;
async function renderScratch() {
  if (previewRunning) return;  // 预览播放期间，轮询让路
  let data;
  try { data = await api("/api/scratch"); } catch { hideSection("#scratch"); return; }
  const box = $("#scratch");
  const now = new Set();

  let html = "";
  for (const sp of data.spaces || []) {
    html += `<div class="card space"><div class="meta"><span class="who">${esc(sp.space)}</span>` +
      `<span>${sp.files.length} 个文件</span></div><div class="sfiles">`;
    for (const f of sp.files) {
      const key = sp.space + "/" + f.name;
      now.add(key);
      const fresh = !scratchSeen.has(key) ? " born" : "";
      html += `<div class="sfile${fresh}"><span class="fname">${esc(f.name)}</span>` +
        `<span class="fmeta">${f.bytes}B · ${esc(f.mtime)}</span></div>`;
    }
    html += `</div></div>`;
  }
  const gone = [...scratchSeen.keys()].filter((k) => !now.has(k));
  scratchSeen = new Map([...now].map((k) => [k, 1]));

  if (!(data.spaces || []).length)
    html = `<div class="empty">涂鸦房此刻空着 —— 没有 subagent 在干活。` +
      `<a class="preview-btn" id="scratch-preview" href="#">▶ 看一段预览</a></div>`;

  const ev = (data.events || []).slice().reverse();
  if (ev.length)
    html += `<div class="sevents">` + ev.slice(0, 8).map((e) =>
      `<div class="sevent"><span class="fmeta">${esc((e.ts || "").slice(5))}</span> ` +
      `<span class="tag">${esc(e.kind)}</span> ${esc(e.agent)} · ${esc(e.space)} ${esc(e.detail)}</div>`
    ).join("") + `</div>`;
  if (gone.length)
    html += `<div class="sevent gone-note">刚被擦除：${gone.map(esc).join("、")}</div>`;
  box.innerHTML = html;
  const pv = $("#scratch-preview");
  if (pv) pv.addEventListener("click", (e) => { e.preventDefault(); scratchPreview(); });
}

/* 涂鸦房预览：纯前端罐头动画，不碰真实数据 */
function scratchPreview() {
  if (previewRunning) return;
  previewRunning = true;
  const box = $("#scratch");
  box.innerHTML =
    `<div class="card space"><div class="meta"><span class="who">preview-space（假数据）</span>` +
    `<span class="fmeta">想象一个 subagent 正在干活</span></div>` +
    `<div class="sfiles" id="pv-files"></div></div><div class="sevents" id="pv-events"></div>`;
  const files = $("#pv-files"), events = $("#pv-events");
  const addFile = (name, bytes) => {
    const d = document.createElement("div");
    d.className = "sfile born";
    d.id = "pv-" + name.replace(/\W/g, "");
    d.innerHTML = `<span class="fname">${esc(name)}</span><span class="fmeta">${bytes}B · 刚刚</span>`;
    files.appendChild(d);
  };
  const killFile = (name) => {
    const d = $("#pv-" + name.replace(/\W/g, ""));
    if (!d) return;
    d.classList.add("dying");
    setTimeout(() => d.remove(), 900);
    const ev = document.createElement("div");
    ev.className = "sevent gone-note";
    ev.textContent = `被擦除：${name}`;
    events.appendChild(ev);
  };
  const seq = [
    [400, () => addFile("notes.md", 812)],
    [1100, () => addFile("draft-plan.md", 2304)],
    [1800, () => addFile("tmp.txt", 148)],
    [2500, () => addFile("finding.md", 1120)],
    [3600, () => killFile("tmp.txt")],
    [4500, () => killFile("draft-plan.md")],
    [5600, () => {
      const d = $("#pv-findingmd");
      if (d) { d.classList.add("settled"); d.querySelector(".fmeta").textContent = "沉淀 → export 毕业 ✓"; }
      const ev = document.createElement("div");
      ev.className = "sevent";
      ev.innerHTML = `<span class="tag human">export</span> finding.md 毕业了，沉到项目里`;
      events.appendChild(ev);
    }],
    [6800, () => {
      const ev = document.createElement("div");
      ev.className = "sevent";
      ev.innerHTML = `—— 预览结束。真实数据进来时，就是这样动的。 <a class="preview-btn" id="pv-again" href="#">↻ 再看一遍</a>`;
      events.appendChild(ev);
      $("#pv-again").addEventListener("click", (e) => { e.preventDefault(); previewRunning = false; scratchPreview(); });
    }],
  ];
  seq.forEach(([t, fn]) => setTimeout(fn, t));
  setTimeout(() => { previewRunning = false; }, 7000);
}

/* ---------- 痕迹热力图（桥没开这个口就整区隐藏） ---------- */
async function renderHeatmap() {
  let days;
  try { ({ days } = await api("/api/heatmap")); } catch { hideSection("#heatmap"); return; }
  const dates = Object.keys(days).sort();
  if (!dates.length) { $("#heatmap").innerHTML = `<div class="empty">还没有痕迹。</div>`; return; }
  const max = Math.max(...Object.values(days));
  const cells = [];
  const d0 = new Date(dates[0] + "T00:00:00"), d1 = new Date();
  let i = 0;
  for (let d = new Date(d0); d <= d1; d.setDate(d.getDate() + 1)) {
    const key = d.toISOString().slice(0, 10);
    const n = days[key] || 0;
    const lv = n === 0 ? 0 : Math.min(4, 1 + Math.floor((n / max) * 3.999));
    // 相位按位置错开，整片格子不会齐刷刷一起闪
    const delay = ((i * 0.37) % 4).toFixed(2);
    cells.push(`<span class="cell l${lv}" style="animation-delay:${delay}s" title="${key} · ${n} 条痕迹"></span>`);
    i++;
  }
  $("#heatmap").innerHTML = cells.join("") +
    `<div class="hm-legend"><span>${dates[0]}</span><span>今天</span></div>`;
}

/* ---------- 项目页 ---------- */
const PROJ_TABS = ["状态", "待审", "坑", "判断", "数据"];
let projData = null, projTab = "状态";

async function showProject(name) {
  show("project");
  projData = await api("/api/project/" + encodeURIComponent(name));
  $("#proj-name").textContent = projData.project || name;
  $("#proj-lifecycle").textContent = projData.lifecycle || "生长";
  $("#proj-desc").textContent = projData.description || "";
  renderProjTabs();
  renderProjBody();
}

function renderProjTabs() {
  $("#proj-tabs").innerHTML = PROJ_TABS.map((label) =>
    `<button class="${label === projTab ? "active" : ""}" data-tab="${label}">${label}</button>`
  ).join("");
  $("#proj-tabs").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { projTab = b.dataset.tab; renderProjTabs(); renderProjBody(); }));
}

const entryHtml = (e) =>
  `<div class="entry"><div class="meta"><span class="tag">${esc(e.mark)}</span>` +
  `<span>${esc(e.ts)}</span>${srcLink(e.src)}</div>` +
  `<div class="text">${esc(e.entry)}</div></div>`;

function renderProjBody() {
  const d = projData, box = $("#proj-body");
  // STATUS 区是固定头部，不随 tab 动
  const st = d.status || {};
  const secs = st.sections || {};
  $("#proj-status").innerHTML =
    (st.updated ? `<div class="meta">STATUS · 更新于 ${esc(st.updated)}${st.priority ? ` · ${esc(st.priority)}` : ""}</div>` : "") +
    Object.entries(secs).map(([k, v]) =>
      `<div class="stline"><span class="stkey">${esc(k)}</span><span class="stval">${esc(v)}</span></div>`).join("") +
    (d.git && d.git.repo ? `<div class="meta" style="margin-top:8px">git: ${esc(d.git.repo)} @ ${esc(d.git.branch || "")}${d.git.dirty ? " · 有未提交" : ""}</div>` : "");

  if (projTab === "状态") {
    const ms = d.milestones || [];
    const rel = d.relates_to || [];
    const dcs = d.docs || [];
    box.innerHTML =
      (d.todo ? `<div class="card"><div class="meta">待办</div><div class="text">${esc(d.todo)}</div></div>` : "") +
      (d.blocked ? `<div class="card"><div class="meta">卡点</div><div class="text">${esc(d.blocked)}</div></div>` : "") +
      (ms.length ? `<div class="card"><div class="meta">成就</div>` +
        ms.map((m) => `<div class="mstone">🏆 ${esc(m)}</div>`).join("") + `</div>` : "") +
      (rel.length ? `<div class="card"><div class="meta">关联项目</div>` +
        rel.map((r) => `<div class="mstone"><a class="doclink" href="#/project/${encodeURIComponent(r.project)}">${esc(r.project)}</a>` +
          ` <span class="fmeta">—— ${esc(r.relation)}</span></div>`).join("") + `</div>` : "") +
      (dcs.length ? `<div class="card"><div class="meta">档案</div>` +
        dcs.map((n) => `<div class="mstone"><a class="doclink" href="#/doc/${encodeURIComponent(n)}">${esc(n)}</a></div>`).join("") + `</div>` : "") +
      (!d.todo && !d.blocked && !ms.length && !rel.length && !dcs.length ? `<div class="empty">没有待办和卡点。</div>` : "");
  } else if (projTab === "待审") {
    const es = (d.pending_review || []);
    box.innerHTML = es.length ? es.map(entryHtml).join("")
      : `<div class="empty">没有待审。</div>`;
  } else {
    const es = (d.journal || []).filter((e) => e.mark === projTab);
    box.innerHTML = es.length ? es.map(entryHtml).join("")
      : `<div class="empty">这一类还没有痕迹。</div>`;
  }
}

/* ---------- 档案 doc 页 ---------- */
async function showDoc(name) {
  show("docview");
  const d = await api("/api/archive/" + encodeURIComponent(name));
  $("#doc-name").textContent = d.doc || name;
  $("#doc-tag").textContent = d.pinned ? "📌 pinned" : "";
  $("#doc-meta").innerHTML =
    `(archive ${esc(d.doc)} @ ${esc(d.version_id)} · ${d.bytes}B · parent ${esc(d.parent || "-")} · ` +
    `${esc(d.ts)} · source_ref ${esc(d.source_ref || "")})` +
    `<br><span class="fmeta">${(d.versions_chain || []).length} 个版本：` +
    (d.versions_chain || []).map((v) => esc(v.version_id)).join(" → ") + `</span>` +
    ((d.out_links || []).length ? `<br><span class="fmeta">连出：` + d.out_links.map((l) =>
      l.to_doc ? `<a href="#/doc/${encodeURIComponent(l.to_doc)}" class="doclink">${esc(l.to_doc)}</a>` : esc(l.to)
    ).join("、") + `</span>` : "") +
    ((d.in_links || []).length ? `<br><span class="fmeta">连入：` + d.in_links.map((l) =>
      l.from_doc ? `<a href="#/doc/${encodeURIComponent(l.from_doc)}" class="doclink">${esc(l.from_doc)}</a>` : esc(l.from_version)
    ).join("、") + `</span>` : "");
  $("#doc-body").textContent = d.content || "";
}

/* ---------- 启动 ---------- */
renderSidebar();
api("/api/overview").then(() => renderOverview()).catch(() => {
  $("#todo-list").innerHTML = `<div class="empty">桥暂时不在。</div>`;
});
renderGraph();
renderHeatmap();
renderScratch();
setInterval(renderScratch, 3000);
route();
