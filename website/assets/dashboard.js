/* MemALL dashboard — data source priority: live gateway → real snapshot → built-in samples.
   The snapshot is produced by tools/sync.mjs from the local SQLite DB (shared-only profile). */
import { get, post, ping, apiBase, setApiBase } from "./api.js";
import { SAMPLE_MEMORIES, LEVEL_META, LEVEL_BOOST, LEVEL_BOOST_DEFAULT, PIPELINE_STEPS, OPTIONAL_STEPS } from "./sample-data.js";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const lvCls = (lv) => (lv || "").startsWith("P") ? "lvl-p" : ["L1", "L2", "L3", "L4"].includes(lv) ? "lvl-a" : ["L5", "L6", "L7", "L8"].includes(lv) ? "lvl-b" : "lvl-c";
const lvColor = (lv) => (lv || "").startsWith("P") ? "var(--level-p)" : ["L1", "L2", "L3", "L4"].includes(lv) ? "var(--level-l-low)" : ["L5", "L6", "L7", "L8"].includes(lv) ? "var(--level-l-mid)" : "var(--level-l-high)";
const chip = (lv) => `<span class="lvchip ${lvCls(lv)}">${esc(lv || "L1")}</span>`;
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("zh-CN"));
const day = (iso) => String(iso || "").slice(0, 10);
const fmtTs = (iso) => String(iso || "").replace("T", " ").slice(0, 16);

const S = { mode: "connecting", snap: null, memories: [], agents: [], edges: [], nodes: [], arcs: [], epochs: [], runs: [], health: null };
const Demo = {
  memories: SAMPLE_MEMORIES.map((m) => ({ ...m })),
  agents: (() => { const c = {}; SAMPLE_MEMORIES.forEach((m) => (c[m.agent] = (c[m.agent] || 0) + 1)); return Object.entries(c).map(([name, count]) => ({ name, count })); })(),
  edges: SAMPLE_MEMORIES.flatMap((m) => (m.edges || []).map(([rel, t]) => ({ s: m.id, t, r: rel }))),
  nodes: SAMPLE_MEMORIES.map((m) => ({ id: m.id, level: m.level, agent: m.agent, label: m.subject, degree: (m.edges || []).length })),
  arcs: [{ decision_id: 1041, subject: "网关技术选型 aiohttp", status: "closed" }, { decision_id: 1103, subject: "v0.2.0 发布计划", status: "in_progress" }, { decision_id: 1110, subject: "MemPort 迁移工具（提案）", status: "open" }],
  epochs: [{ id: 7, label: "检索重构期", agent: "claude", started_at: "2026-09-28", ended_at: null }, { id: 6, label: "联邦联调期", agent: "workbuddy", started_at: "2026-08-11", ended_at: "2026-09-26" }],
  runs: [], graphNote: "内置样例数据",
};

const snapshotUrl = () => {
  const u = new URLSearchParams(location.search).get("snapshot_url");
  return u ? new URL(u, location.href).href : new URL("data/snapshot.json", location.href).href;
};

/* ── data loading ─────────────────────────────────────────── */
const unwrap = (j) => (j && typeof j === "object" && "data" in j ? j.data : j);

function useSnapshot(snap) {
  S.snap = snap;
  S.memories = snap.memories || [];
  S.agents = (snap.by_agent || []).map((a) => ({ name: a.name, count: a.count }));
  S.edges = snap.graph?.edges || [];
  S.nodes = snap.graph?.nodes || [];
  S.arcs = (snap.arcs?.open || []).map((a) => ({ decision_id: a.id, subject: a.subject, status: a.status || "unset", agent: a.agent, created_at: a.created_at, level: a.level }));
  S.epochs = snap.epochs || [];
  S.runs = snap.runs || [];
}
function useDemo() {
  S.snap = null;
  Object.assign(S, { memories: Demo.memories, agents: Demo.agents, edges: Demo.edges, nodes: Demo.nodes, arcs: Demo.arcs, epochs: Demo.epochs, runs: Demo.runs, health: null });
}

async function loadSnapshot() {
  try {
    const r = await fetch(snapshotUrl(), { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(String(r.status));
    const j = await r.json();
    if (j?.kind !== "memall-snapshot") throw new Error("非快照文件");
    useSnapshot(j); S.mode = "snapshot"; return true;
  } catch { return false; }
}

async function connect() {
  S.mode = "connecting"; renderConn();
  try {
    S.health = await ping();
    S.mode = "live";
    const [mem, ag] = await Promise.allSettled([get(`/memories?limit=200&hours=${24 * 3650}`), get("/agents")]);
    S.memories = (mem.status === "fulfilled" && unwrap(mem.value)?.items) || [];
    S.agents = (ag.status === "fulfilled" && unwrap(ag.value)) || [];
    S.arcs = []; S.epochs = []; S.runs = []; S.edges = []; S.nodes = [];
    for (const [p, key] of [["/api/arcs", "arcs"], ["/api/epochs", "epochs"]]) {
      try { const v = unwrap(await get(p)); S[key] = Array.isArray(v) ? v : v?.items || []; } catch { S[key] = []; }
    }
    await loadSnapshot();          /* real snapshot still enriches graph/pipeline */
    if (S.mode === "live") S.snap = S.snap; /* keep live as primary source */
  } catch {
    if (!(await loadSnapshot())) { useDemo(); S.mode = "demo"; }
  }
  renderConn(); route();
}

function sourceLabel() {
  if (S.mode === "live") return { txt: `网关实时 ${apiBase().replace(/^https?:\/\//, "")}`, cls: "live" };
  if (S.mode === "snapshot") return { txt: `真实快照 · ${day(S.snap.generated_at)} ${fmtTs(S.snap.generated_at).slice(11, 16)} · ${S.snap.profile}`, cls: "live" };
  if (S.mode === "demo") return { txt: "演示模式（内置样例）", cls: "demo" };
  return { txt: "连接中…", cls: "" };
}
function renderConn() {
  const el = $("#conn"); const s = sourceLabel();
  el.className = "status-pill " + s.cls + (S.mode === "connecting" ? "" : "");
  el.innerHTML = `<span class="dot"></span>${esc(s.txt)}` + (S.mode === "live" && S.health ? ` · 库内 ${num(S.health.memory_count)} 条` : "");
}

/* ── views ────────────────────────────────────────────────── */
const V = {};

V.overview = () => {
  const t = S.snap?.totals;
  const levels = S.snap?.by_level?.length ? S.snap.by_level : Object.entries(S.memories.reduce((a, m) => ((a[m.level || "L1"] = (a[m.level || "L1"] || 0) + 1), a), {})).map(([level, count]) => ({ level, count }));
  const maxN = Math.max(1, ...levels.map((l) => l.count));
  const arcStat = S.snap?.arcs?.status || [];
  const tl = S.snap?.timeline || [];
  const rel = (S.snap?.relations || []).slice(0, 8);
  const kpi = [
    ["记忆总数", t ? num(t.memories) : num(S.health?.memory_count ?? S.memories.length), t ? `shared ${num(t.shared)} · private ${num(t.private)}` : "可见条目"],
    ["活跃 Agent", t ? num(S.snap.by_agent.length) + " / " + num(t.agents) : num(S.agents.length), "库内出现过的写入方"],
    ["图谱边", t ? num(t.edges) : num(S.edges.length), "类型化关系"],
    ["未闭合决策弧", arcStat.length ? num(arcStat.filter((a) => a.status !== "closed").reduce((s, a) => s + a.count, 0)) : num((S.arcs || []).filter((a) => (a.status || a.arc_status) !== "closed").length), "open + in_progress"],
  ];
  return `
  <div class="kpis">${kpi.map(([k, v, sub]) => `<div class="kpi"><b>${v}</b><span>${k}</span><div class="muted" style="font-size:11.5px;margin-top:2px">${esc(sub)}</div></div>`).join("")}</div>

  <div class="grid grid-2">
    <div class="panel"><h2>层级分布${S.snap ? "（全库真实计数）" : ""}</h2>
      ${levels.map((l) => `<div class="lvbar" style="margin:6px 0">
        <span class="name">${chip(l.level)} <span class="muted" style="font-size:11.5px">${LEVEL_META[l.level]?.label || ""}</span></span>
        <span class="track"><i style="width:${(l.count / maxN) * 100}%;background:${lvColor(l.level)}"></i></span>
        <span class="num">${num(l.count)}</span></div>`).join("") || '<p class="muted">暂无记忆。</p>'}
    </div>
    <div class="panel"><h2>记忆写入密度（按天）</h2>
      ${tl.length ? `<div style="display:flex;align-items:flex-end;gap:2px;height:130px">${tl.slice(-60).map((d) => { const max = Math.max(...tl.slice(-60).map((x) => x.count)); return `<div title="${d.day} · ${d.count} 条" style="flex:1;background:linear-gradient(180deg,var(--accent),rgba(88,166,255,.25));height:${Math.max(3, (d.count / max) * 128)}px;border-radius:2px 2px 0 0"></div>`; }).join("")}</div>
      <p class="hint">最近 ${Math.min(60, tl.length)} 天，合计 ${num(tl.reduce((s, d) => s + d.count, 0))} 条 · 峰值 ${num(Math.max(...tl.map((d) => d.count)))} /天</p>`
      : '<p class="muted">无时间线数据（连接网关或生成快照后可见）。</p>'}
    </div>
  </div>

  <div class="grid grid-2">
    <div class="panel"><h2>关系类型分布</h2>
      ${rel.length ? `<table class="d"><thead><tr><th>关系</th><th>条数</th><th>平均权重</th></tr></thead><tbody>
        ${rel.map((r) => `<tr><td class="mono">${esc(r.relation)}</td><td class="mono">${num(r.count)}</td><td class="mono muted">${r.avg_weight ?? "—"}</td></tr>`).join("")}</tbody></table>`
      : '<p class="muted">快照未包含关系统计。</p>'}
    </div>
    <div class="panel"><h2>决策弧状态 · 未闭合样例</h2>
      ${arcStat.length ? `<div class="bar" style="margin-bottom:12px">${arcStat.map((a) => `<i style="width:${(a.count / arcStat.reduce((s, x) => s + x.count, 0)) * 100}%;background:${a.status === "closed" ? "var(--success)" : a.status === "in_progress" ? "var(--warning)" : "var(--danger)"}" title="${a.status} ${a.count}"></i>`).join("")}</div>
      <div class="muted" style="font-size:12px;margin-bottom:10px">${arcStat.map((a) => `${a.status} ${num(a.count)}`).join(" · ")}</div>` : ""}
      ${(S.arcs || []).slice(0, 6).map((a) => `<div class="lvbar" style="margin:6px 0"><span class="name" style="flex-basis:96px;color:${a.status === "closed" ? "var(--success)" : a.status === "in_progress" ? "var(--warning)" : "var(--danger)"}">● ${esc(a.status || "open")}</span><span class="num" style="flex:1 1 auto;min-width:0;text-align:left;white-space:normal;color:var(--fg)">#${a.decision_id ?? a.id ?? ""} ${esc((a.subject || "").slice(0, 54))}</span></div>`).join("") || '<p class="muted">暂无未闭合决策。</p>'}
    </div>
  </div>

  ${S.epochs?.length ? `<div class="panel"><h2>时期分段 Epochs（最近 ${Math.min(8, S.epochs.length)} 条）</h2>
    <div class="tbl-x"><table class="d"><thead><tr><th>Agent</th><th>标签</th><th>起始</th><th>结束</th><th>边界原因</th><th>记忆数</th></tr></thead><tbody>
    ${S.epochs.slice(0, 8).map((e) => `<tr><td class="mono">@${esc(e.agent_name || e.agent)}</td><td>${esc(e.label)}</td>
      <td class="mono muted">${fmtTs(e.started_at)}</td><td class="mono muted">${e.ended_at ? fmtTs(e.ended_at) : "进行中"}</td>
      <td class="mono">${esc(e.reason || e.boundary_reason || "")}</td><td class="mono">${num(e.memory_count)}</td></tr>`).join("")}
    </tbody></table></div></div>` : ""}

  ${S.mode === "snapshot" ? `<div class="panel"><h2>数据来源</h2><p class="muted" style="margin:0;font-size:13.5px">
    本页读取 <span class="mono">${esc(snapshotUrl().replace(/^https?:\/\//, ""))}</span>：由 <span class="mono">tools/sync.mjs</span> 以只读方式从
    <span class="mono">${esc(S.snap.source.db_file)}</span>（${S.snap.source.size_mb} MB · ${S.snap.source.migrations} 个迁移 · 最新 ${esc(S.snap.source.latest_migration || "")}）生成，
    口径 <b>${esc(S.snap.profile)}</b>：聚合统计覆盖全库 ${num(S.snap.totals.memories)} 条，逐条文本只含 <b>visibility=shared</b> 的 ${num(S.snap.totals.shared)} 条。
    图谱视图 ${esc(S.snap.graph.note)}。</p>
    <p class="hint">更新数据：在项目里运行 <span class="mono">node tools/build.mjs</span>；要看 private 内容用 <span class="mono">--include-private</span>（输出写在 dist/ 之外，不会被打包上传）。</p></div>` : ""}
  ${S.mode === "demo" ? `<div class="panel"><h2>关于演示模式</h2><p class="muted" style="margin:0;font-size:13.5px">
    既没连上网关，也没找到 <span class="mono">data/snapshot.json</span>，正在展示内置样例。启动网关 <span class="mono">memall start</span> 或生成快照 <span class="mono">node tools/build.mjs</span> 后刷新。</p></div>` : ""}`;
};

let memFilter = { level: "", agent: "", q: "" };
V.memories = () => {
  const match = (m) => (!memFilter.level || (m.level || "L1") === memFilter.level) &&
    (!memFilter.agent || (m.agent_name || m.agent) === memFilter.agent) &&
    (!memFilter.q || `${m.subject}${m.content}${m.category}${m.tags}`.toLowerCase().includes(memFilter.q.toLowerCase()));
  const rows = S.memories.filter(match);
  const agents = [...new Set(S.memories.map((m) => m.agent_name || m.agent).filter(Boolean))];
  const scopeNote = S.mode === "snapshot" ? `逐条浏览范围：shared 记忆 ${S.memories.length} 条（全库 ${num(S.snap.totals.memories)} 条，private 内容不在快照中）` : S.mode === "live" && S.memories.length >= 200 ? "最近 200 条内" : "";
  return `<div class="panel"><h2>记忆浏览 <span class="muted" style="font-weight:400">（${rows.length} 条${scopeNote ? " · " + esc(scopeNote) : ""}）</span></h2>
    <div class="tools-row">
      <input type="text" id="f-q" placeholder="页内过滤：主题 / 内容 / 类目…" value="${esc(memFilter.q)}" style="flex:1">
      <select id="f-lv"><option value="">全部层级</option>${Object.keys(LEVEL_META).map((l) => `<option ${memFilter.level === l ? "selected" : ""}>${l}</option>`).join("")}</select>
      <select id="f-ag"><option value="">全部 Agent</option>${agents.map((a) => `<option ${memFilter.agent === a ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
    </div>
    <table class="d"><thead><tr><th style="width:56px">级别</th><th>主题</th><th style="width:120px">Agent</th><th class="th-time" style="width:140px">时间</th><th style="width:70px">回声</th><th style="width:56px">访问</th></tr></thead>
    <tbody id="mem-rows">
    ${rows.slice(0, 150).map((m) => `<tr class="row" data-mid="${m.id}">
      <td>${chip(m.level)}</td><td>${esc(m.subject || (m.content || "").slice(0, 72))}</td>
      <td class="mono">@${esc(m.agent_name || m.agent || "-")}</td>
      <td class="mono muted td-time">${esc(fmtTs(m.created_at))}</td>
      <td class="mono muted">${m.echo_score != null ? Math.round(m.echo_score) : "—"}</td>
      <td class="mono muted">${m.access_count ?? "—"}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">无匹配记忆</td></tr>`}
    </tbody></table></div>`;
};
V.memories.bind = () => {
  const repaint = () => { $("#view").innerHTML = V.memories(); V.memories.bind(); };
  const set = (k, v) => { memFilter[k] = v; repaint(); };
  let dbt;
  $("#f-q").addEventListener("input", (e) => { memFilter.q = e.target.value; clearTimeout(dbt); dbt = setTimeout(repaint, 220); });
  $("#f-lv").addEventListener("change", (e) => set("level", e.target.value));
  $("#f-ag").addEventListener("change", (e) => set("agent", e.target.value));
  $("#mem-rows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-mid]"); if (!tr) return;
    const m = S.memories.find((x) => String(x.id) === tr.dataset.mid); if (!m) return;
    const nx = tr.nextElementSibling;
    if (nx?.classList.contains("expanded")) { nx.remove(); tr.classList.remove("expanded"); return; }
    document.querySelectorAll("tr.expanded").forEach((x) => { x.previousElementSibling?.classList.remove("expanded"); x.remove(); });
    tr.classList.add("expanded");
    tr.insertAdjacentHTML("afterend", `<tr class="expanded"><td colspan="6"><div class="full">${esc(m.content || "（该条内容不在快照导出范围内）")}</div>
      <div class="muted mono" style="padding:6px 0">#${m.id} · ${esc(m.category || "")} · ${esc(m.tags || "")} · echo ${m.echo_score ?? "—"} · trust ${m.trust_level ?? "—"}</div></td></tr>`);
  });
};

let lastSearch = [];
V.search = () => `<div class="panel"><h2>检索台 ${S.mode === "live" ? '<span class="badge" style="float:right">网关 retrieve / vector-search</span>' : S.mode === "snapshot" ? '<span class="badge" style="float:right">真实数据 · 浏览器内 RRF（k=60 + _LEVEL_BOOST，不含重排）</span>' : '<span class="badge" style="float:right">样例数据 · 模拟 RRF</span>'}</h2>
  <div class="tools-row">
    <input type="text" id="s-q" placeholder="自然语言查询，回车检索…" ${lastSearch[0]?.q ? `value="${esc(lastSearch[0].q)}"` : ""}>
    <select id="s-mode">${S.mode === "live" ? '<option value="search">retrieve（语义+过滤）</option><option value="vector">vector-search</option>' : '<option value="search">hybrid_search（本地 RRF）</option>'}</select>
    <button class="btn btn-primary" id="s-go">检索</button>
  </div>
  <div id="s-out">${lastSearch.length ? resultsHtml(lastSearch) : `<p class="hint">检索范围：${S.memories.length} 条可见记忆${S.mode === "snapshot" ? `（全库 ${num(S.snap.totals.memories)} 条，private 文本未导出）` : ""}。${suggestTips()}</p>`}</div></div>`;

/* terms that actually occur in the loaded memory text, so the tips never miss */
function suggestTips() {
  if (!S.memories.length) return "";
  const df = new Map();
  for (const m of S.memories) for (const t of new Set(tok(`${m.subject} ${m.content}`))) {
    if (t.length < 2 || /^\d+$/.test(t)) continue;
    df.set(t, (df.get(t) || 0) + 1);
  }
  const cjk = (t) => /[\u4e00-\u9fff]/.test(t);
  const noisy = (t) => /^[的了条个次在和与是有一中把被对为以而就都很]|[的了条个次在和与是有一中把被对为以而就都很]$/.test(t);
  const ranked = [...df.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const pick = [ranked.filter(([t]) => cjk(t) && !noisy(t)).slice(0, 3), ranked.filter(([t]) => !cjk(t) && t.length > 3).slice(0, 2)].flat().filter(([, n]) => n > 1);
  return pick.length ? `点击试试：${pick.map(([t]) => `<button class="s-tip mono" data-tip="${esc(t)}">${esc(t)}</button>`).join(" ")}` : "";
}

function resultsHtml(items) {
  return items.map(({ m, s, score }) => `<div class="result"><div class="meta">${chip(m.level)}
    <span>${esc(m.subject || (m.content || "").slice(0, 50))}</span><span class="mono">@${esc(m.agent_name || m.agent)}</span><span class="mono muted">#${m.id}</span>
    <span class="score">${s != null ? `rrf ${(+s).toFixed(4)}` : `score ${(+score || 0).toFixed(2) || "—"}`}</span></div>
    <div class="body">${esc((m.content || "（内容不在快照范围）").slice(0, 260))}</div></div>`).join("") || '<p class="hint">无命中。</p>';
}
V.search.bind = () => {
  const run = async () => {
    const q = $("#s-q").value.trim(); if (!q) return;
    $("#s-out").innerHTML = '<p class="hint"><span class="spin"></span>检索中…</p>';
    try {
      if (S.mode === "live") {
        const path = $("#s-mode").value === "vector" ? `/memories/vector-search?query=${encodeURIComponent(q)}&top_k=8` : `/memories/search?query=${encodeURIComponent(q)}&limit=8`;
        const j = unwrap(await get(path));
        const list = j?.items || j?.results || (Array.isArray(j) ? j : []);
        lastSearch = list.map((m) => ({ m, q }));
      } else {
        lastSearch = localHybrid(q).map((x) => ({ ...x, q }));
      }
      $("#s-out").innerHTML = resultsHtml(lastSearch) + `<p class="hint">${lastSearch.length} 条命中${S.mode === "live" ? "（网关返回）" : "（FTS 词面 + 向量近邻双路 RRF）"}</p>`;
    } catch (e) { $("#s-out").innerHTML = `<p class="hint" style="color:var(--danger)">${esc(e.message)}</p>`; }
  };
  $("#s-go").addEventListener("click", run);
  $("#s-q").addEventListener("keydown", (e) => e.key === "Enter" && run());
  $("#s-out").addEventListener("click", (e) => { const t = e.target.closest(".s-tip"); if (t) { $("#s-q").value = t.dataset.tip; run(); } });
};

/* browser-side RRF mirroring core/thin_waist_search.py.
   Level weighting = graph/retrieve._LEVEL_BOOST, applied to the keyword leg
   only (production reranks the vector leg instead). */
const tok = (s) => { const o = (s.toLowerCase().match(/[a-z0-9_\-\.\/]+/g) || []);
  for (const run of s.match(/[\u4e00-\u9fff]+/g) || []) for (let i = 0; i < run.length - 1; i++) o.push(run.slice(i, i + 2)); return o; };
function localHybrid(query, k = 10) {
  const terms = [...new Set(tok(query))]; if (!terms.length) return [];
  const f = (m) => { const hay = `${m.subject} ${m.content} ${m.category}`.toLowerCase(); let h = 0; terms.forEach((t) => hay.includes(t) && h++); return h / terms.length * (1 + Math.log1p(m.access_count || 0) / 6); };
  const v = (m) => { const d = new Set(tok(`${m.subject} ${m.content}`)); let i = 0; terms.forEach((t) => d.has(t) && i++); return i / Math.sqrt(Math.max(d.size, 1)) / Math.sqrt(terms.length); };
  const rrf = (fn, boosted) => S.memories.map((m) => ({ m, s: fn(m) * (boosted ? (LEVEL_BOOST[m.level] ?? LEVEL_BOOST_DEFAULT) : 1) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s).map((x, i) => [x.m.id, 1 / (60 + i + 1)]);
  const fused = new Map();
  for (const [id, s] of [...rrf(f, true), ...rrf(v)]) fused.set(id, (fused.get(id) || 0) + s);
  return [...fused.entries()].map(([id, s]) => ({ m: S.memories.find((x) => x.id === id), s })).sort((a, b) => b.s - a.s).slice(0, k);
}

V.graph = () => `<div class="panel"><h2>知识图谱 ${S.snap ? `<span class="muted" style="font-weight:400">· ${S.snap.graph.nodes.length} 个高连接度节点 / ${S.snap.graph.edges.length} 条边（库内共 ${num(S.snap.totals.edges)} 条边）· ${esc(S.snap.graph.note)}</span>` : S.mode === "live" ? '<span class="muted" style="font-weight:400">· 中心节点 BFS 展开</span>' : '<span class="muted" style="font-weight:400">· 样例数据</span>'}</h2>
  <canvas id="graph-canvas"></canvas>
  <div class="graph-legend">${["var(--level-p)", "var(--level-l-low)", "var(--level-l-mid)", "var(--level-l-high)"].map((c, i) => `<span><i style="background:${c}"></i>${["原始 P0–P2", "身份·时间·流程·会话 L1–L4", "计划·反思·教训·关系 L5–L8", "蒸馏·整合·商业 L9–L11"][i]}</span>`).join("")}
    <span style="margin-left:auto">拖拽平移 · 滚轮缩放 · 点击节点看详情</span></div>
  <div id="g-detail"></div></div>`;
V.graph.bind = () => drawGraph();

V.agents = () => `<div class="panel"><h2>Agent 联邦视图 ${S.snap ? `<span class="muted" style="font-weight:400">· 库内共 ${num(S.snap.totals.agents)} 个写入方，列出前 ${S.agents.length} 个</span>` : ""}</h2>
  ${S.agents.length ? `<table class="d"><thead><tr><th>Agent</th><th style="width:110px">记忆数</th><th>主用层级</th></tr></thead><tbody>
  ${S.agents.map((a) => { const ms = S.memories.filter((m) => (m.agent_name || m.agent) === a.name);
    const lv = {}; ms.forEach((m) => (lv[m.level] = (lv[m.level] || 0) + 1));
    const top = Object.entries(lv).sort((x, y) => y[1] - x[1]).slice(0, 3).map(([l]) => chip(l)).join(" ") || '<span class="muted" style="font-size:12px">文本未导出</span>';
    return `<tr><td class="mono">@${esc(a.name)}</td><td class="mono">${num(a.count ?? ms.length)}</td><td>${top}</td></tr>`; }).join("")}
  </tbody></table>` : '<div class="empty"><div class="big">🛰️</div>暂无 Agent 记录。</div>'}
  ${S.snap ? `<p class="hint">生命周期状态：archived ${num(S.snap.totals.archived)} · dormant ${num(S.snap.totals.dormant)} · 冲突待处理 ${num(S.snap.totals.conflicts)}；身份档案 ${num(S.snap.totals.identities)} 条，实体 ${num(S.snap.totals.entities)}，三元组 ${num(S.snap.totals.triples)}，会话 ${num(S.snap.totals.sessions)}，叙事 ${num(S.snap.totals.narratives)}。</p>` : ""}
  <p class="hint">跨设备：网关每 5 秒 UDP 广播发现同伴，凭一次性配对码 <span class="mono">POST /pair</span> 握手后共享；矛盾记忆由冲突检测标记。</p></div>`;

V.pipeline = () => `<div class="panel"><h2>自进化管线 ${S.runs?.length ? `<span class="muted" style="font-weight:400">· 最近 ${S.runs.length} 次真实运行（库内累计 ${num(S.snap?.totals?.pipeline_runs ?? "?")} 次）</span>` : S.mode === "demo" ? '<span class="muted" style="font-weight:400">· 演示轮次</span>' : ""}</h2>
  <div class="tools-row">
    <button class="btn btn-primary" id="p-run">${S.mode === "live" ? "触发 run_pipeline" : S.runs?.length ? "查看步骤清单" : "模拟运行一轮"}</button>
    <select id="p-pick">${(S.runs || []).map((r, i) => `<option value="${i}">#${r.id} · ${fmtTs(r.started_at)} · ${r.status}</option>`).join("") || '<option>无运行记录</option>'}</select>
    <span class="muted" id="p-status" style="align-self:center;font-size:13px"></span>
  </div>
  <div id="p-detail"></div>
  <h2 style="margin-top:26px">步骤清单（源码 31 核心 + 5 可选）</h2>
  <div class="pipe-grid" id="p-grid"></div>
  <p class="hint" id="p-tip" style="min-height:20px;color:var(--fg-muted)">悬停步骤查看职责（虚线边框 = 可选步骤）。</p></div>`;
V.pipeline.bind = () => {
  const g = $("#p-grid");
  PIPELINE_STEPS.forEach(([n, d], i) => {
    const b = document.createElement("button"); b.className = "pipe-step"; b.textContent = n;
    b.addEventListener("mouseenter", () => ($("#p-tip").textContent = `${n} — ${d}`));
    g.appendChild(b);
  });
  OPTIONAL_STEPS.forEach(([n, d]) => {
    const b = document.createElement("button"); b.className = "pipe-step opt"; b.textContent = n;
    b.addEventListener("mouseenter", () => ($("#p-tip").textContent = `${n} — ${d}（可选步骤）`));
    g.appendChild(b);
  });
  const detail = $("#p-detail");
  const paint = () => {
    const r = S.runs?.[Number($("#p-pick")?.value || 0)];
    if (!r) { detail.innerHTML = ""; return; }
    const byName = new Map((r.steps || []).map((s) => [s.step, s]));
    detail.innerHTML = `<p class="hint">开始 ${fmtTs(r.started_at)} → 结束 ${fmtTs(r.ended_at)} · ${r.status} · 总耗时 ${(r.total_elapsed_ms / 1000).toFixed(1)}s · 本轮 ${r.step_count} 步${r.error ? ` · <span style="color:var(--danger)">错误 ${esc(r.error)}</span>` : ""}</p>
      <div class="tbl-x"><table class="d"><thead><tr><th>步骤</th><th style="width:64px">状态</th><th style="width:74px">耗时</th><th style="width:70px">输入</th><th style="width:70px">输出</th><th style="width:78px">结果</th><th style="width:150px">契约</th><th style="width:58px">质量</th></tr></thead>
      <tbody>${(r.steps || []).map((s) => `<tr><td class="mono">${esc(s.step)}</td>
        <td style="color:${s.status === "ok" ? "var(--success)" : "var(--danger)"}">${esc(s.status)}</td>
        <td class="mono muted nw">${s.elapsed_ms} ms</td><td class="mono muted nw">${num(s.records_in)}</td><td class="mono muted nw">${num(s.records_out)}</td>
        <td class="mono nw">${num(s.result)}</td><td class="mono muted">${esc(s.contract || "")}</td>
        <td>${s.quality === "pass" ? '<span class="ck">✓</span>' : s.quality === "fail" ? '<span style="color:var(--warning)">未达标</span>' : '<span class="muted">—</span>'}</td></tr>`).join("")}</tbody></table></div>
      <p class="hint">未在本轮出现的步骤：${PIPELINE_STEPS.map(([n]) => n).filter((n) => !byName.has(n)).join(" · ") || "无"}</p>`;
  };
  $("#p-pick")?.addEventListener("change", paint); paint();
  $("#p-run").addEventListener("click", async () => {
    const st = $("#p-status");
    if (S.mode === "live") { try { st.innerHTML = '<span class="spin"></span>已入队（后台 worker 异步执行）'; await post("/pipeline/run", {}); } catch (e) { st.textContent = e.message; } return; }
    if (S.runs?.length) { st.textContent = `已展示最近一次真实运行 #${S.runs[0].id}`; return; }
    const chips = [...g.children];
    for (const c of chips) { c.style.borderColor = "var(--accent)"; await new Promise((r) => setTimeout(r, 16)); c.style.borderColor = ""; c.insertAdjacentHTML("beforeend", `<small style="color:var(--success)"> +${(Math.random() * 12) | 0}</small>`); }
    st.textContent = "演示轮次完成 ✓（真实管线 commit-per-step、每步幂等）";
  });
};

/* ── graph renderer (force layout on canvas) ──────────────── */
async function drawGraph() {
  const cv = $("#graph-canvas"); const ctx = cv.getContext("2d");
  const color = (lv) => (lv || "").startsWith("P") ? "#f85149" : ["L1", "L2", "L3", "L4"].includes(lv) ? "#58a6ff" : ["L5", "L6", "L7", "L8"].includes(lv) ? "#bc8cff" : "#3fb950";
  let nodes, links, view = { x: 0, y: 0, k: 1 };

  if (S.nodes?.length) {                       /* snapshot: real topology */
    const byMem = new Map(S.memories.map((m) => [String(m.id), m]));
    nodes = S.nodes.map((n) => ({ id: n.id, label: n.label, lv: n.level, agent: n.agent, text: byMem.get(String(n.id))?.content || n.label, degree: n.degree, color: color(n.level) }));
    links = S.edges.map((e) => ({ s: e.s, t: e.t, r: e.r }));
  } else if (S.mode === "live") {               /* gateway: center-node BFS */
    nodes = S.memories.slice(0, 60).map((m, i) => ({ id: m.id, label: m.subject || (m.content || "").slice(0, 18), lv: m.level, agent: m.agent_name || m.agent, text: m.content, color: color(m.level) }));
    links = [];
    try {
      const c = unwrap(await get("/api/graph/center"));
      if (c?.node_id) {
        const t = unwrap(await get(`/graph/${c.node_id}`));
        for (const m of t?.nodes || t?.items || []) if (!nodes.some((n) => n.id === m.id)) nodes.push({ id: m.id, label: m.subject, lv: m.level, agent: m.agent_name, text: m.content, color: color(m.level) });
        for (const e of t?.edges || []) links.push({ s: e.source_id ?? e.source, t: e.target_id ?? e.target, r: e.relation || "related" });
      }
    } catch { /* token-gated */ }
    if (!links.length) $("#g-detail").innerHTML = '<p class="hint">网关模式下边数据需要 token；也可先生成 <span class="mono">data/snapshot.json</span> 看真实拓扑。</p>';
  } else {
    nodes = Demo.nodes.map((n) => ({ ...n, text: n.label, color: color(n.level) }));
    links = Demo.edges.map((e) => ({ s: e.s, t: e.t, r: e.r }));
  }
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const L = links.filter((l) => byId[l.s] && byId[l.t] && l.s !== l.t);

  nodes.forEach((n, i) => { const a = (i / nodes.length) * 6.283 * 7.1; const rad = 90 + (i % 9) * 34 + Math.min(240, (n.degree || 0) * 6); n.x = Math.cos(a) * rad; n.y = Math.sin(a) * rad; n.vx = n.vy = 0; });
  function tick() {
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j], dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy + 1, F = 26000 / d2, d = Math.sqrt(d2);
      a.vx -= (dx / d) * F; a.vy -= (dy / d) * F; b.vx += (dx / d) * F; b.vy += (dy / d) * F;
    }
    for (const l of L) { const a = byId[l.s], b = byId[l.t], dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) + 1, F = (d - 190) * .02;
      a.vx += (dx / d) * F; a.vy += (dy / d) * F; b.vx -= (dx / d) * F; b.vy -= (dy / d) * F; }
    for (const n of nodes) { n.vx -= n.x * .003; n.vy -= n.y * .003; n.x += (n.vx = Math.max(-9, Math.min(9, n.vx * .82))); n.y += (n.vy = Math.max(-9, Math.min(9, n.vy * .82))); }
  }
  function paint() {
    const dpr = Math.min(devicePixelRatio || 1, 2), W = cv.clientWidth, H = cv.clientHeight;
    if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
    if (!fitted || alpha > 0) {
      const span = Math.max(...nodes.flatMap((n) => [Math.abs(n.x), Math.abs(n.y)]), 1);
      view.k = Math.min(1.6, (0.44 * Math.min(W, H)) / span); fitted = true;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    ctx.translate(W / 2 + view.x, H / 2 + view.y); ctx.scale(view.k, view.k);
    for (const l of L) { const a = byId[l.s], b = byId[l.t];
      ctx.strokeStyle = l.r === "contradicts" ? "rgba(248,81,73,.6)" : l.r === "supersedes" ? "rgba(210,153,34,.55)" : l.r === "refines" ? "rgba(88,166,255,.35)" : "rgba(139,148,158,.3)";
      ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
    if (view.k > 1.1) for (const l of L) { const a = byId[l.s], b = byId[l.t];
      ctx.fillStyle = "rgba(139,148,158,.55)"; ctx.font = `${8 / view.k}px ui-monospace,monospace`; ctx.textAlign = "center"; ctx.fillText(l.r, (a.x + b.x) / 2, (a.y + b.y) / 2 - 2); }
    for (const n of nodes) {
      ctx.fillStyle = n.color; ctx.beginPath(); ctx.arc(n.x, n.y, (n.id === sel ? 9 : 4) + Math.min(9, (n.degree || 0) / 2), 0, 7); ctx.fill();
      if (n.id === sel) { ctx.strokeStyle = "#e6edf3"; ctx.lineWidth = 1.5 / view.k; ctx.stroke(); }
      if (view.k > 1.1 || (n.degree || 0) >= 10 || n.id === sel) {
        ctx.fillStyle = "rgba(230,237,243,.85)"; ctx.font = `${Math.min(11, 9.5 / view.k)}px sans-serif`; ctx.fillText(String(n.label || "").slice(0, 18), n.x, n.y - 12 / view.k); }
    }
  }
  let sel = null, alpha = 340, fitted = false;
  (function loop() { if (alpha-- > 0) tick(); paint(); requestAnimationFrame(loop); })();

  let drag = null;
  cv.onpointerdown = (e) => { alpha = 0; drag = { x: e.offsetX, y: e.offsetY, vx: view.x, vy: view.y, moved: 0 }; };
  cv.onpointermove = (e) => { if (!drag) { cv.style.cursor = pick(e) ? "pointer" : "grab"; return; } view.x = drag.vx + (e.offsetX - drag.x); view.y = drag.vy + (e.offsetY - drag.y); drag.moved += Math.abs(e.movementX) + Math.abs(e.movementY); };
  cv.onpointerup = (e) => { if (drag?.moved < 4) clickPick(e); drag = null; };
  cv.onwheel = (e) => { e.preventDefault(); alpha = 0; fitted = true; view.k = Math.min(2.6, Math.max(.2, view.k * (e.deltaY < 0 ? 1.12 : .89))); };
  const toWorld = (e) => [(e.offsetX - cv.clientWidth / 2 - view.x) / view.k, (e.offsetY - cv.clientHeight / 2 - view.y) / view.k];
  const pick = (e) => { const [x, y] = toWorld(e), r = 12 / view.k; return nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 < r * r); };
  function clickPick(e) {
    const n = pick(e); sel = n?.id ?? null; const box = $("#g-detail");
    if (!n) { box.innerHTML = ""; return; }
    const rel = L.filter((l) => l.s === n.id || l.t === n.id).slice(0, 8)
      .map((l) => `<span class="mono">${l.r === "contradicts" ? "⚡" : "→"} ${esc(l.r)} → #${l.s === n.id ? l.t : l.s} ${esc(byId[l.s === n.id ? l.t : l.s].label)}</span>`).join("<br>");
    box.innerHTML = `<div class="result" style="margin-top:14px"><div class="meta">${chip(n.lv)}<span>#${n.id}</span><span class="mono">@${esc(n.agent || "-")}</span>${n.degree ? `<span class="score">度数 ${n.degree}</span>` : ""}</div>
      <div class="body"><b>${esc(n.label)}</b>${n.text && n.text !== n.label ? `<br>${esc(String(n.text).slice(0, 300))}` : ""}</div>${rel ? `<div class="edges" style="line-height:1.9">${rel}</div>` : ""}</div>`;
  }
}

/* ── router ───────────────────────────────────────────────── */
const ROUTES = { overview: "总览", memories: "记忆", search: "检索", graph: "图谱", agents: "Agents", pipeline: "管线" };
function route() {
  const key = location.hash.slice(1) in ROUTES ? location.hash.slice(1) : "overview";
  document.querySelectorAll(".side nav a").forEach((a) => a.classList.toggle("on", a.dataset.r === key));
  $("#view").innerHTML = V[key]();
  V[key].bind?.();
}
$("main").addEventListener("click", (e) => {
  const g = e.target.closest("[data-go]");
  if (g) { location.hash = g.dataset.go; setTimeout(() => { const row = document.querySelector(`tr[data-mid="${g.dataset.open}"]`); row?.click(); row?.scrollIntoView({ block: "center" }); }, 60); }
});
addEventListener("hashchange", () => { if (S.mode !== "connecting") route(); });
$("#api-url").value = apiBase();
$("#reconnect").addEventListener("click", () => { setApiBase($("#api-url").value.trim() || "http://127.0.0.1:9920"); connect(); });
$("#demo-btn").addEventListener("click", () => { useDemo(); S.mode = "demo"; renderConn(); route(); });
connect();
