#!/usr/bin/env node
/* Generate a real-data snapshot for the MemALL dashboard from the local SQLite DB.
   Read-only: this process never writes to the memory database.
   Usage:  node tools/sync.mjs [--db <path>] [--include-private] [--out <path>] [--max <n>]
   Default profile (shared-only) writes website/dist/data/snapshot.json — safe to package.
   --include-private writes website/data/snapshot.full.json (outside dist) so private
   memory text is never uploaded by accident. */
import { DatabaseSync } from "node:sqlite";
import { mkdirSync, writeFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, "..");
const argv = process.argv.slice(2);
const flag = (n) => argv.includes(n);
const opt = (n, d) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : d; };

const includePrivate = flag("--include-private");
const HOME = process.env.USERPROFILE || process.env.HOME || "";
const dbPath = opt("--db", path.join(HOME, ".memall", "data.db"));
const outPath = path.resolve(opt("--out", path.join(SITE, includePrivate ? "data/snapshot.full.json" : "dist/data/snapshot.json")));
const MAX_MEMORIES = Number(opt("--max", includePrivate ? 600 : 400));

if (!statSync(dbPath).isFile()) { console.error(`数据库不存在：${dbPath}`); process.exit(1); }
const db = new DatabaseSync(dbPath, { readOnly: true });
const all = (sql, ...p) => db.prepare(sql).all(...p);
const one = (sql, ...p) => all(sql, ...p)[0];
const maybe = (sql, ...p) => { try { return one(sql, ...p); } catch { return undefined; } };
const has = (t) => maybe(`SELECT 1 FROM sqlite_master WHERE type='table' AND name=?`, t) ? t : null;

/* 命中即视为敏感，内容整体脱敏 */
const SECRET_PATTERNS = [
  [/"(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key|authorization)\s*[:=]/i, "credential"],
  [/\b(sk|ghp|github_pat|xox[abprs]|ya29|AKIA|ASIA)[-_][A-Za-z0-9]{8,}/, "token"],
  [/\bbearer\s+[A-Za-z0-9._\-]{16,}/i, "token"],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/, "email"],
  [/\b\d{17}[\dXx]\b/, "id-card"],
  [/\b1[3-9]\d{9}\b/, "phone"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "private-key"],
];
function redact(text) {
  const s = String(text ?? "");
  for (const [re, name] of SECRET_PATTERNS) if (re.test(s)) return { text: `[内容已脱敏 · 命中 ${name}]`, redacted: true };
  return { text: s, redacted: false };
}
const clip = (s, n) => { const t = String(s ?? "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n) + "…" : t; };
const safeText = (s, n) => { const r = redact(s); return r.redacted ? r.text : clip(r.text, n); };

const dayOf = (iso) => String(iso ?? "").slice(0, 10);
const where = includePrivate ? "" : "WHERE visibility = 'shared'";
const and = (extra) => (where ? `${where} AND ${extra}` : `WHERE ${extra}`);

const counts = (table, col = "1") => (has(table) ? one(`SELECT COUNT(*) c FROM ${table}`).c : 0);
const memoryTotal = one("SELECT COUNT(*) c FROM memories").c;

const snapshot = {
  kind: "memall-snapshot",
  profile: includePrivate ? "full (includes private memories)" : "shared-only",
  generated_at: new Date().toISOString(),
  source: { db_file: path.basename(dbPath), db_path: includePrivate ? dbPath : path.basename(dbPath), size_mb: +(statSync(dbPath).size / 1048576).toFixed(1), migrations: maybe("SELECT COUNT(*) c FROM schema_version")?.c ?? null, latest_migration: maybe("SELECT migration_id m FROM schema_version ORDER BY applied_at DESC LIMIT 1")?.m ?? null, host: includePrivate ? maybe("SELECT host h FROM pipeline_runs ORDER BY id DESC LIMIT 1")?.h ?? null : null },

  totals: {
    memories: memoryTotal,
    shared: one("SELECT COUNT(*) c FROM memories WHERE visibility='shared'").c,
    private: one("SELECT COUNT(*) c FROM memories WHERE visibility='private'").c,
    edges: counts("edges"),
    identities: counts("identities"),
    agents: one("SELECT COUNT(DISTINCT agent_name) c FROM memories WHERE agent_name<>''").c,
    entities: counts("entities"),
    triples: counts("knowledge_triples"),
    sessions: counts("sessions"),
    narratives: counts("narratives"),
    epochs: counts("epochs"),
    time_slices: counts("time_slices"),
    pipeline_runs: counts("pipeline_runs"),
    archived: one("SELECT COUNT(*) c FROM memories WHERE memory_status='archived'").c,
    dormant: one("SELECT COUNT(*) c FROM memories WHERE memory_status='dormant'").c,
    conflicts: one("SELECT COUNT(*) c FROM memories WHERE memory_status='conflict'").c,
  },

  by_level: all("SELECT level, COUNT(*) count FROM memories GROUP BY level ORDER BY count DESC"),
  by_agent: all("SELECT agent_name name, COUNT(*) count, SUM(COALESCE(echo_score,0)) echo FROM memories WHERE agent_name<>'' GROUP BY agent_name ORDER BY count DESC LIMIT 24"),
  relations: all("SELECT relation_type relation, COUNT(*) count, ROUND(AVG(weight),2) avg_weight FROM edges GROUP BY relation_type ORDER BY count DESC"),

  arcs: {
    status: all("SELECT COALESCE(arc_status,'unset') status, COUNT(*) count FROM memories WHERE level='L4' GROUP BY arc_status ORDER BY count DESC"),
    open: all(`SELECT id, level, agent_name agent, category, created_at, arc_status status, subject, ${includePrivate ? "substr(content,1,240) content" : "'' content"} FROM memories ${and("level='L4' AND (arc_status IS NULL OR arc_status<>'closed')")} ORDER BY id DESC LIMIT 25`)
      .map((r) => ({ ...r, subject: safeText(r.subject, 90), content: r.content ? safeText(r.content, 200) : "" })),
  },

  epochs: all(`SELECT id, agent_name agent, label, started_at, ended_at, boundary_reason reason, memory_count FROM epochs ORDER BY started_at DESC LIMIT 14`)
    .map((e) => ({ ...e, label: safeText(e.label, 70) })),

  timeline: all("SELECT substr(created_at,1,10) day, COUNT(*) count FROM memories GROUP BY day ORDER BY day DESC LIMIT 120").reverse(),

  echo_top: all(`SELECT id, level, agent_name agent, echo_score, access_count, subject, created_at FROM memories ${and("echo_score>0")} ORDER BY echo_score DESC, access_count DESC LIMIT 20`)
    .map((r) => ({ ...r, subject: safeText(r.subject, 90) })),

  runs: all("SELECT id, started_at, ended_at, status, total_elapsed_ms, error, steps FROM pipeline_runs ORDER BY id DESC LIMIT 6").map((r) => {
    let steps = [];
    try { steps = JSON.parse(r.steps || "[]").map((s) => ({ step: s.step, status: s.status, elapsed_ms: s.elapsed_ms, records_in: s.records_in, records_out: s.records_out, result: s.result, contract: s.contract, quality: s.quality ? (s.quality.passed ? "pass" : "fail") : null })); } catch { steps = []; }
    return { id: r.id, started_at: r.started_at, ended_at: r.ended_at, status: r.status, total_elapsed_ms: r.total_elapsed_ms, error: safeText(r.error, 160), step_count: steps.length, steps };
  }),

  memories: all(`SELECT id, level, agent_name agent, category, subject, tags, created_at, access_count, echo_score, trust_level, ${includePrivate ? "substr(content,1,520) content" : "substr(content,1,520) content"} FROM memories ${where} ORDER BY id DESC LIMIT ?`, MAX_MEMORIES)
    .map((m) => ({ ...m, subject: safeText(m.subject, 120), content: safeText(m.content, 460), tags: clip(m.tags, 80) })),

  stats_by_visibility: all("SELECT COALESCE(visibility,'unset') visibility, COUNT(*) count FROM memories GROUP BY visibility"),
};

/* graph subset: induced subgraph around the highest-degree nodes.
   Node topology (ids, levels, agents, relations) is always exported because it is
   structural; subject text is only exported for memories we are allowed to show. */
{
  const canShow = new Set(snapshot.memories.map((m) => m.id));
  const rows = all("SELECT source_id s, target_id t, relation_type r FROM edges LIMIT 40000");
  const deg = new Map();
  for (const e of rows) { deg.set(e.s, (deg.get(e.s) || 0) + 1); deg.set(e.t, (deg.get(e.t) || 0) + 1); }
  const ids = [...deg.entries()].sort((a, b) => b[1] - a[1]).slice(0, 150).map(([id]) => id);
  const inSet = new Set(ids);
  const meta = new Map();
  for (const r of all(`SELECT id, level, agent_name agent, subject, content, visibility FROM memories WHERE id IN (${ids.map(() => "?").join(",") || "-1"})`, ...ids)) {
    const show = includePrivate || canShow.has(r.id) || r.visibility === "shared";
    meta.set(r.id, { id: r.id, level: r.level, agent: r.agent, degree: deg.get(r.id) || 0,
      label: show ? safeText(r.subject || r.content, 40) : `${r.level} #${r.id}` });
  }
  snapshot.graph = {
    nodes: ids.filter((id) => meta.has(id)).map((id) => meta.get(id)),
    edges: rows.filter((e) => inSet.has(e.s) && inSet.has(e.t)).slice(0, 420).map((e) => ({ s: e.s, t: e.t, r: e.r })),
    note: includePrivate ? "真实图谱：全部节点带主题" : "真实拓扑：private 记忆只显示 id/层级，不显示文本",
  };
}

mkdirSync(path.dirname(outPath), { recursive: true });
writeFileSync(outPath, JSON.stringify(snapshot), "utf8");
const kb = (statSync(outPath).size / 1024).toFixed(0);
console.log(`profile=${snapshot.profile}`);
console.log(`memories=${snapshot.memories.length} / 全库 ${snapshot.totals.memories} · graph ${snapshot.graph.nodes.length}节点/${snapshot.graph.edges.length}边`);
console.log(`写入 ${outPath} (${kb} KB)`);
if (includePrivate && outPath.includes(`${path.sep}dist${path.sep}`)) {
  console.warn("⚠ 你正在把含 private 记忆的快照写进 dist/ —— 该目录会被打包上传。");
}
