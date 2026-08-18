/* 猎迹 dashboard —— 玄：黑不是关掉光，是让光更显眼。
   数据形状 = 洄的只读桥（spoor_view.py）的契约，前端只管 fetch。
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

/* 项目生命周期归类：静态配置，跟着前端文件走（契约层的字段归洄裁决，
   等她长出正式字段就换数据源——提案已在 session-spoor-review 落账） */
let LIFECYCLE = {};
const LC_CLASSES = ["毕业", "里程碑", "生长", "胚胎"];
fetch("lifecycle.json").then((r) => (r.ok ? r.json() : {})).then((d) => { LIFECYCLE = d; renderSidebar(); }).catch(() => {});

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

/* ---------- 侧栏：生命周期四类 + 档案房 ---------- */
const ARCH_TAGS = ["毕业档案", "里程碑", "目录册", "胚胎"];
async function docTag(d) {
  for (const t of ARCH_TAGS) if (d.doc.includes(t)) return t;
  try {
    const full = await api("/api/archive/" + encodeURIComponent(d.doc));
    const head = (full.content || "").split("\n").slice(0, 5).join("\n");
    for (const t of ARCH_TAGS) if (head.includes(t)) return t;
  } catch { }
  return "";
}

async function renderSidebar() {
  // 项目：按生命周期四类
  try {
    const projects = await api("/api/projects");
    const nav = $("#lifecycle-nav");
    nav.innerHTML = "";
    for (const lc of LC_CLASSES) {
      const group = projects.filter((p) => (LIFECYCLE[p.project] || "生长") === lc);
      const det = document.createElement("details");
      det.open = group.length > 0;
      det.innerHTML =
        `<summary><span class="lc-name">${esc(lc)}</span>` +
        `<span class="lc-count">${group.length}</span></summary>`;
      const ul = document.createElement("ul");
      for (const p of group) {
        const li = document.createElement("li");
        li.innerHTML =
          `<a href="#/project/${encodeURIComponent(p.project)}">${esc(p.project)}` +
          (count(p.pits) ? `<span class="tag pit">${count(p.pits)}坑</span>` : "") +
          (count(p.pending_review) ? `<span class="tag human">${count(p.pending_review)}待审</span>` : "") +
          `</a>`;
        ul.appendChild(li);
      }
      if (!group.length) ul.innerHTML = `<li class="empty">——</li>`;
      det.appendChild(ul);
      nav.appendChild(det);
    }
  } catch { }

  // 档案房：按生命周期标签分组（名字里没有就读正文头部）
  const anav = $("#archive-nav");
  try {
    const docs = await api("/api/archive");
    const tags = await Promise.all(docs.map(docTag));
    docs.forEach((d, i) => (d._tag = tags[i]));
    let html = `<div class="anav-title">档案房</div>`;
    if (!docs.length) html += `<div class="empty">（还空着）</div>`;
    for (const tag of ARCH_TAGS) {
      const group = docs.filter((d) => d._tag === tag);
      if (!group.length) continue;
      html += `<details open><summary><span class="lc-name">${esc(tag)}</span>` +
        `<span class="lc-count">${group.length}</span></summary><ul>` +
        group.map((d) =>
          `<li><a href="#/doc/${encodeURIComponent(d.doc)}" title="${d.versions} 个版本 · ${esc(d.ts)}">` +
          `${d.pinned ? "📌 " : ""}${esc(d.doc)}</a></li>`).join("") +
        `</ul></details>`;
    }
    const untagged = docs.filter((d) => !d._tag);
    if (untagged.length)
      html += `<details open><summary><span class="lc-name">未标</span>` +
        `<span class="lc-count">${untagged.length}</span></summary><ul>` +
        untagged.map((d) =>
          `<li><a href="#/doc/${encodeURIComponent(d.doc)}">${d.pinned ? "📌 " : ""}${esc(d.doc)}</a></li>`).join("") +
        `</ul></details>`;
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

/* ---------- 链接图：星座星图（同心环聚合，防膨胀） ---------- */
async function renderGraph() {
  let data;
  try { data = await api("/api/graph"); } catch { return; }
  const svg = $("#graph");
  const docs = data.nodes || [];
  if (!docs.length) { svg.innerHTML = ""; return; }
  const W = 880, H = 380, cx = W / 2, cy = H / 2;

  // 节点=档案 doc，边=archive_link；外部 URI 落成暗淡的外围小星
  const nodes = docs.map((d) => ({ id: d.doc, pinned: d.pinned, w: d.versions || 1 }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges = [];
  for (const e of data.edges || []) {
    const a = e.from_doc, b = e.to_doc;
    if (!a) continue;
    if (b && !ids.has(b)) {
      if (e.external) {
        const ext = "ext:" + b;
        if (!ids.has(ext)) { nodes.push({ id: ext, ext: true, w: 1 }); ids.add(ext); }
        edges.push({ a, b: ext, w: 1 });
      }
      continue;
    }
    if (b) edges.push({ a, b, w: 1 + (e.relation === "parent" ? 1 : 0) });
  }
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
  placeGroup(nodes.filter((n) => !n.ext), -Math.PI / 2);
  placeGroup(nodes.filter((n) => n.ext), 0.9);
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
    const col = n.ext ? "#6b6774" : n.pinned ? "#e8c169" : "#dcd2f7";
    const core = n.ext ? 2.2 : n.pinned ? 4.2 : 3.4;
    const label = n.ext ? n.id.slice(4, 22) : n.id;
    const delay = ((i * 0.7) % 3.5).toFixed(2);
    const dur = (2.8 + (i % 5) * 0.6).toFixed(2);
    html +=
      `<circle class="halo" filter="url(#soft)" style="animation-delay:${delay}s;animation-duration:${dur}s"` +
      ` cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${core * 3.2}" fill="${col}"/>` +
      `<circle class="core" filter="url(#softcore)" style="animation-delay:${delay}s;animation-duration:${dur}s"` +
      ` cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${core}" fill="${col}">` +
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
  $("#proj-lifecycle").textContent = LIFECYCLE[projData.project] || "生长";
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
    box.innerHTML =
      (d.todo ? `<div class="card"><div class="meta">待办</div><div class="text">${esc(d.todo)}</div></div>` : "") +
      (d.blocked ? `<div class="card"><div class="meta">卡点</div><div class="text">${esc(d.blocked)}</div></div>` : "") +
      (!d.todo && !d.blocked ? `<div class="empty">没有待办和卡点。</div>` : "");
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
