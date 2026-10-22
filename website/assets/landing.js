/* Landing page: hero graph canvas, pipeline chips, interactive simulator. */
import { SAMPLE_MEMORIES, LEVEL_META, PIPELINE_STEPS, OPTIONAL_STEPS } from "./sample-data.js";

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ── hero knowledge-graph backdrop ─────────────────────────── */
(function heroGraph() {
  const cv = document.getElementById("hero-graph");
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const colors = ["#58a6ff", "#bc8cff", "#3fb950", "#f85149", "#f778ba"];
  let nodes = [], W = 0, H = 0, raf = 0, visible = true;

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    W = cv.clientWidth; H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function seed() {
    const n = W < 700 ? 26 : 48;
    nodes = Array.from({ length: n }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - .5) * .22, vy: (Math.random() - .5) * .22,
      r: 2 + Math.random() * 3.2, c: colors[(Math.random() * colors.length) | 0],
    }));
  }
  function frame() {
    ctx.clearRect(0, 0, W, H);
    for (const a of nodes) {
      for (const b of nodes) {
        const dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < 15000 && d2 > 1) {
          ctx.strokeStyle = a.c === b.c ? a.c : "#30363d";
          ctx.globalAlpha = (1 - d2 / 15000) * .35;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }
    ctx.globalAlpha = .9;
    for (const p of nodes) {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > W) p.vx *= -1;
      if (p.y < 0 || p.y > H) p.vy *= -1;
      ctx.fillStyle = p.c; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (visible && !reduced) raf = requestAnimationFrame(frame);
  }
  resize(); seed(); frame();
  addEventListener("resize", () => { resize(); seed(); });
  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible && !reduced) frame(); else cancelAnimationFrame(raf);
  }).observe(cv);
})();

/* ── pipeline chips ────────────────────────────────────────── */
(function pipeline() {
  const box = document.getElementById("pipe-grid");
  if (!box) return;
  const tip = document.getElementById("pipe-tip");
  const mk = (name, desc, opt) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pipe-step" + (opt ? " opt" : "");
    b.textContent = name;
    b.addEventListener("mouseenter", () => { tip.innerHTML = `<b>${name}</b> — ${desc}`; });
    return b;
  };
  PIPELINE_STEPS.forEach(([n, d]) => box.appendChild(mk(n, d, false)));
  OPTIONAL_STEPS.forEach(([n, d]) => box.appendChild(mk(n, d + "（可选步骤）", true)));
})();

/* ── interactive simulator ─────────────────────────────────── */
(function simulator() {
  const store = [...SAMPLE_MEMORIES];
  let seq = 2000;

  const tokenize = (s) => {
    const out = [];
    const ascii = s.toLowerCase().match(/[a-z0-9_\-\.\/]+/g) || [];
    out.push(...ascii);
    const cjk = s.match(/[\u4e00-\u9fff]+/g) || [];
    for (const run of cjk) for (let i = 0; i < run.length - 1; i++) out.push(run.slice(i, i + 2));
    return out;
  };

  function ftsScore(m, terms) {
    const hay = `${m.subject} ${m.content} ${m.category} ${m.agent}`;
    let hit = 0;
    for (const t of terms) if (hay.toLowerCase().includes(t)) hit++;
    return hit / Math.max(terms.length, 1);
  }
  function vecScore(m, terms) {
    const doc = new Set(tokenize(`${m.subject} ${m.content}`));
    if (!doc.size || !terms.length) return 0;
    let inter = 0;
    for (const t of terms) if (doc.has(t)) inter++;
    return inter / Math.sqrt(doc.size) / Math.sqrt(Math.max(terms.length, 1));
  }

  /* RRF fusion, mirroring core/thin_waist_search.py: score = Σ 1/(k+rank) */
  function hybridSearch(query, k = 5) {
    const terms = [...new Set(tokenize(query))];
    if (!terms.length) return [];
    const rrf = (rankFn) => {
      const ranked = store.map((m) => ({ m, s: rankFn(m) }))
        .filter((x) => x.s > 0).sort((a, b) => b.s - a.s);
      return ranked.map((x, i) => [x.m.id, 1 / (60 + i + 1)]);
    };
    const fused = new Map();
    for (const [id, s] of [...rrf((m) => ftsScore(m, terms) * (1 + Math.log1p(m.access_count) / 6)), ...rrf((m) => vecScore(m, terms))])
      fused.set(id, (fused.get(id) || 0) + s);
    const boost = { L11: 1.3, L10: 1.25, L9: 1.2, L8: 1.15, L7: 1.12, L6: 1.1, L4: 1.1, P0: 1.08 };
    return [...fused.entries()]
      .map(([id, s]) => ({ m: store.find((x) => x.id === id), s: s * (boost[store.find((x) => x.id === id).level] || 1) }))
      .sort((a, b) => b.s - a.s).slice(0, k);
  }

  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function runSearch() {
    const q = document.getElementById("sim-q").value.trim();
    const out = document.getElementById("sim-out");
    if (!q) { out.innerHTML = `<p class="hint">试试："OOM 修复"、"端口"、"用户偏好"、"蒸馏"</p>`; return; }
    const hits = hybridSearch(q);
    const dt = performance.now();
    if (!hits.length) { out.innerHTML = `<p class="hint">FTS5 与向量两路都无命中——RRF 融合后为空。</p>`; return; }
    out.innerHTML = `<p class="hint">hybrid_search · ${hits.length} 条 · 模拟 RRF k=60 · ${(dt % 20 + 8).toFixed(1)} ms</p>` +
      hits.map(({ m, s }) => `
        <div class="result">
          <div class="meta"><span class="lv ${LEVEL_META[m.level].cls}">${m.level}</span><span>${esc(m.subject)}</span>
            <span>@${esc(m.agent)}</span><span>#${m.id}</span><span class="score">rrf ${s.toFixed(4)}</span></div>
          <div class="body">${esc(m.content)}</div>
          ${m.edges.length ? `<div class="edges">⇢ ${m.edges.map(([r, t]) => `${r} → #${t}`).join(" · ")}</div>` : ""}
        </div>`).join("");
  }

  document.getElementById("sim-run").addEventListener("click", runSearch);
  document.getElementById("sim-q").addEventListener("keydown", (e) => e.key === "Enter" && runSearch());
  document.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => {
    document.getElementById("sim-q").value = b.dataset.q; runSearch();
  }));
  runSearch();

  /* capture → async pipeline animation */
  const CAP_STEPS = ["classify", "echo", "link", "embed_index", "enrich"];
  document.getElementById("cap-run").addEventListener("click", () => {
    const v = document.getElementById("cap-in").value.trim();
    const out = document.getElementById("cap-out");
    if (v.length < 4) { out.innerHTML = `<p class="hint">内容太短（质量门：≥4 字符）。</p>`; return; }
    const m = { id: ++seq, level: "L1", agent: "you", category: "事实", subject: v.slice(0, 28), content: v, created_at: new Date().toISOString().slice(0, 16).replace("T", " "), access_count: 0, edges: [] };
    store.push(m);
    out.innerHTML = `<div class="toast">✓ memall_write capture → 已入库 #${m.id}（暂记 L1），管线异步排队…</div><p class="hint" id="cap-prog"></p>`;
    let i = 0;
    const prog = document.getElementById("cap-prog");
    const tick = () => {
      if (i >= CAP_STEPS.length) {
        const lvl = /决定|选择|采用/.test(v) ? "L4" : /偏好|喜欢|习惯/.test(v) ? "L7" : /反思|复盘|教训/.test(v) ? "L6" : /约定|规范|必须/.test(v) ? "L2" : "L1";
        m.level = lvl;
        const linked = hybridSearch(v, 2).filter((x) => x.m.id !== m.id);
        m.edges = linked.map((x) => ["related", x.m.id]);
        prog.innerHTML = `完成：<b class="${LEVEL_META[lvl].cls}">${lvl}</b>（排除链分类）· echo 42/100 · 自动建边 ${m.edges.length} 条 · vec0 已索引。再检索一次就能看到它。`;
        return;
      }
      prog.textContent = `管线执行中… ${CAP_STEPS[i]} ✓`;
      i++; setTimeout(tick, reduced ? 0 : 260);
    };
    tick();
  });
})();

/* ── copy buttons + scroll reveal ──────────────────────────── */
document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(b.dataset.copy); }
  catch { const ta = document.createElement("textarea"); ta.value = b.dataset.copy; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  const t = b.textContent; b.textContent = "已复制";
  setTimeout(() => (b.textContent = t), 1200);
}));
if (!reduced) {
  const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && e.target.classList.add("in")), { threshold: .12 });
  document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
} else document.querySelectorAll(".reveal").forEach((el) => el.classList.add("in"));
