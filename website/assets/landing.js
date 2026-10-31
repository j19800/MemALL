/* Landing page: hero graph canvas, pipeline chips, interactive simulator. */
import { SAMPLE_MEMORIES, LEVEL_META, LEVEL_BOOST, LEVEL_BOOST_DEFAULT, PIPELINE_STEPS, OPTIONAL_STEPS } from "./sample-data.js";

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

  /* RRF fusion, mirroring core/thin_waist_search.py: score = Σ 1/(k+rank).
     Level boost follows graph/retrieve._LEVEL_BOOST and, like production,
     weights the keyword leg only (the vector leg is reranked instead). */
  function hybridSearch(query, k = 5) {
    const terms = [...new Set(tokenize(query))];
    if (!terms.length) return [];
    const boostOf = (m) => LEVEL_BOOST[m.level] ?? LEVEL_BOOST_DEFAULT;
    const rrf = (rankFn, boosted) => {
      const ranked = store.map((m) => ({ m, s: rankFn(m) * (boosted ? boostOf(m) : 1) }))
        .filter((x) => x.s > 0).sort((a, b) => b.s - a.s);
      return ranked.map((x, i) => [x.m.id, 1 / (60 + i + 1)]);
    };
    const fused = new Map();
    for (const [id, s] of [...rrf((m) => ftsScore(m, terms) * (1 + Math.log1p(m.access_count) / 6), true), ...rrf((m) => vecScore(m, terms))])
      fused.set(id, (fused.get(id) || 0) + s);
    return [...fused.entries()]
      .map(([id, s]) => ({ m: store.find((x) => x.id === id), s }))
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
  /* Mini-port of pipeline/classify.py: exclusion chain (first match wins,
     counted by distinct marker patterns + minimum content length) and the
     15 category rules. Anything unrecognized stays at the P2 raw band. */
  const LAYER_RULES = [
    ["L6", [/(?:教训|根因|学到|改进点|lesson)/i, /(?:修正|更正|纠正|做错了|踩坑|遗漏|偏了)/, /(?:反思|回顾|retrospective|反省)/i, /(?:做对了|做得好的|好的做法|有效|Why)/, /(?:下一次|以后.*应该|需要.*注意|避免.*再)/, /(?:不应该|不对|本应|本不该)/], 2, 25],
    ["L11", [/(?:商业模式|变现|盈利|market|商业|创业|竞品|定价|收入|增长|growth|领域|domain)/i, /(?:一人公司|OPC|数字游民|产品定位|价值主张|壁垒|moat|网络效应)/i, /(?:蓝海|红海|差异化|护城河|平台效应|战略)/i, /(?:use.?case|用户画像|场景|投资回报|ROI|GTM|go.?to.?market|okr)/i], 1, 20],
    ["L7", [/(?:我喜欢|我偏好|prefer|偏好|倾向于|常用|习惯用|主要用)/i, /(?:我觉得更好|更高效|更[^过]|使用场景|适用场景)/, /(?:我不喜欢|我排斥|避免|我不用|我讨厌|不建议)/, /(?:推荐|建议用|更推荐|优先选择)/], 1, 15],
    ["L3", [/(?:步骤|流程|方法|workflow|如何|方式|procedure|checklist|指南)/i, /(?:第一步|第二步|接着|然后|最后|以下步骤|pipelines?|cmd)/i, /(?:安装|配置|部署|设置|configure|install|setup)/i, /(?:规范|规则|guideline|标准|原则|sop|操作)/i], 2, 15],
    ["L5", [/(?:计划|规划|路线图|roadmap|目标|goal|milestone)/i, /(?:待办|todo|task|任务|next.?step|下一步|Phase\s+\d)/i, /(?:迭代|sprint|ETA|截止日期|deadline|预计|安排)/i], 1, 15],
    ["L1", [/(?:我叫|我是|本人|name|email|phone|contact)/i, /(?:我从事|我担任|我的角色|我的职位|我的职业)/i, /(?:我擅长|我精通|我的能力|我的技能|我会|我能|熟悉)/i, /(?:我的习惯|我习惯|我每天|我认为|我相信|我的理念|我看重|我的价值观)/i, /(?:我住在|我的家乡|毕业于|我的背景|我的经历)/i], 1, 15],
    ["L4", [/(?:会话|session|本次|此次|刚才|前面讨论|conversation|对话中|会议|讨论)/i, /session_id\d*/i], 1, 10],
    ["L2", [/(今天|昨天|上周|这周|下周|\d{4}-\d{2}-\d{2})/, /(来了|开始|结束|上线|merged|deployed|completed)/i], 1, 10],
  ];
  const CATEGORY_RULES = [
    [/(决定|选择|采用|替代|改用)\s/, "decision"], [/(问题|瓶颈|不足|错误|bug|故障|缺陷)/i, "problem"],
    [/(架构|设计|方案|模式|原则|权衡|系统)/, "architecture"], [/(代码|实现|函数|类|接口|API|SDK|模块)/, "implementation"],
    [/(测试|验证|确认|benchmark|压测)/, "testing"], [/(部署|发布|上线|CI|CD|运维)/, "deployment"],
    [/(讨论|会议|对齐|同步|评审)/, "meeting"], [/(文档|README|手册|指南|规范)/, "documentation"],
    [/(规划|路线图|计划|目标|OKR|Milestone|阶段)/i, "planning"], [/(学习|笔记|教程|概念)/, "learning"],
    [/(想法|灵感|构思|脑洞|创意)/, "idea"], [/(复盘|反思|总结|教训|回顾)/, "reflection"],
    [/(修复|修正|解决|纠正)/, "fix"], [/(配置|参数|设置|优化|性能)/, "config"], [/(规则|RULE|原则|标准)/i, "rule"],
  ];
  const classify = (v) => {
    const level = (LAYER_RULES.find(([lv, pats, min, len]) => v.length >= len && pats.filter((p) => p.test(v)).length >= min) || ["P2"])[0];
    const cat = (CATEGORY_RULES.find(([p]) => p.test(v)) || [null, "general"])[1];
    return { level, cat };
  };

  const CAP_STEPS = ["classify", "echo", "link", "embed_index", "enrich"];
  document.getElementById("cap-run").addEventListener("click", () => {
    const v = document.getElementById("cap-in").value.trim();
    const out = document.getElementById("cap-out");
    if (v.length < 4) { out.innerHTML = `<p class="hint">内容太短（质量门：≥4 字符）。</p>`; return; }
    const m = { id: ++seq, level: "P2", agent: "you", category: "general", subject: v.slice(0, 28), content: v, created_at: new Date().toISOString().slice(0, 16).replace("T", " "), access_count: 0, edges: [] };
    store.push(m);
    out.innerHTML = `<div class="toast">✓ memall_write capture → 已入库 #${m.id}（暂记 P2 原始），管线异步排队…</div><p class="hint" id="cap-prog"></p>`;
    let i = 0;
    const prog = document.getElementById("cap-prog");
    const tick = () => {
      if (i >= CAP_STEPS.length) {
        const { level: lvl, cat } = classify(v);
        m.level = lvl; m.category = cat;
        const linked = hybridSearch(v, 2).filter((x) => x.m.id !== m.id);
        m.edges = linked.map((x) => ["related", x.m.id]);
        prog.innerHTML = `完成：<b class="${LEVEL_META[lvl].cls}">${lvl} ${LEVEL_META[lvl].label}</b>（排除链 first-match）· 分类 <code>${cat}</code> · echo 42/100 · 自动建边 ${m.edges.length} 条 · vec0 已索引。再检索一次就能看到它。`;
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
