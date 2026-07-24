// Process the already-saved deep_data.json and extract insights
const fs = require('fs');

const raw = fs.readFileSync('E:\\memall\\__deep_data.json', 'utf8');
const data = JSON.parse(raw);

const lines = [];

lines.push('=== MEMORIES TABLE SCHEMA ===');
lines.push(data.memories_schema || 'N/A');
lines.push('');

lines.push('=== COLUMNS ===');
lines.push(data.memories_columns.join(', '));
lines.push('');

lines.push('=== BASICS ===');
lines.push('Total memories: ' + data.total_memories);
lines.push('Total edges: ' + data.total_edges);
lines.push('Time range: ' + data.time_range?.first + ' -> ' + data.time_range?.last + ' (' + data.time_span_days + ' days)');
lines.push('');

lines.push('=== LEVEL DISTRIBUTION ===');
for (const r of (data.level_dist || [])) {
    lines.push(`  ${r.level}: ${r.cnt}`);
}
lines.push('');

lines.push('=== AGENT DISTRIBUTION ===');
for (const r of (data.agent_dist || [])) {
    lines.push(`  ${r.agent_name}: ${r.cnt}`);
}
lines.push('');

lines.push('=== CATEGORY DISTRIBUTION ===');
for (const r of (data.category_dist || [])) {
    lines.push(`  ${r.category}: ${r.cnt}`);
}
lines.push('');

lines.push('=== SUBJECT DISTRIBUTION (top 15) ===');
for (const r of (data.subject_dist || [])) {
    lines.push(`  ${r.subject}: ${r.cnt}`);
}
lines.push('');

lines.push('=== PROJECT DISTRIBUTION (top 15) ===');
for (const r of (data.project_dist || [])) {
    lines.push(`  ${r.project}: ${r.cnt}`);
}
lines.push('');

lines.push('=== VISIBILITY ===');
for (const r of (data.visibility_dist || [])) {
    lines.push(`  ${r.visibility}: ${r.cnt}`);
}
lines.push('');

lines.push('=== EDGE TYPES ===');
for (const r of (data.edge_types || [])) {
    lines.push(`  ${r.relation_type}: ${r.cnt}`);
}
lines.push('');

if (data.memory_edge_types) {
    lines.push('=== MEMORY EDGE TYPES ===');
    for (const r of data.memory_edge_types) {
        lines.push(`  ${r.relation_type}: ${r.cnt}`);
    }
    lines.push('');
}

lines.push('=== AGENT TEMPORAL ACTIVITY ===');
for (const r of (data.agent_temporal || [])) {
    lines.push(`  ${r.agent_name}: ${r.first?.slice(0,10)} -> ${r.last?.slice(0,10)} (${r.cnt})`);
}
lines.push('');

lines.push('=== TOP MEMORY DAYS ===');
for (const r of (data.top_days || [])) {
    lines.push(`  ${r.day}: ${r.cnt}`);
}
lines.push('');

lines.push('=== TOP SOURCE NODES ===');
for (const r of (data.top_source || [])) {
    const m = r.mem;
    if (m) {
        lines.push(`  #${r.source_id}: out=${r.degree} L${m.level} [${m.category}] ${m.agent_name}: ${(m.content||'').slice(0,100)}`);
    }
}
lines.push('');

lines.push('=== TOP TARGET NODES ===');
for (const r of (data.top_target || [])) {
    const m = r.mem;
    if (m) {
        lines.push(`  #${r.target_id}: in=${r.degree} L${m.level} [${m.category}] ${m.agent_name}: ${(m.content||'').slice(0,100)}`);
    }
}
lines.push('');

lines.push('=== CONTENT PATTERNS ===');
for (const [k, v] of Object.entries(data.content_patterns || {})) {
    lines.push(`  ${k}: ${v}`);
}
lines.push('');

lines.push('=== OTHER STATS ===');
for (const key of ['pipeline','discussions_count','clusters_count','facts_count','sessions_count','memory_edges_count']) {
    if (data[key] !== undefined) lines.push(`  ${key}: ${JSON.stringify(data[key])}`);
}
if (data.dedup) {
    lines.push(`  dedup: ${JSON.stringify(data.dedup)}`);
}
lines.push('');

if (data.identities && data.identities.length > 0) {
    lines.push('=== IDENTITIES ===');
    for (const i of data.identities) {
        lines.push(`  ${JSON.stringify(i)}`);
    }
    lines.push('');
}

if (data.epochs && data.epochs.length > 0) {
    lines.push('=== EPOCHS ===');
    for (const e of data.epochs) {
        lines.push(`  ${JSON.stringify(e)}`);
    }
    lines.push('');
}

if (data.schema_versions && data.schema_versions.length > 0) {
    lines.push('=== SCHEMA VERSIONS ===');
    for (const s of data.schema_versions) {
        lines.push(`  ${JSON.stringify(s)}`);
    }
    lines.push('');
}

fs.writeFileSync('E:\\memall\\__deep_summary.txt', lines.join('\n'));
console.log('DONE: ' + lines.length + ' lines');

// Also print a few data points to help analysis
console.log('Levels: ' + JSON.stringify(data.level_dist));
console.log('Edges: ' + JSON.stringify(data.edge_types));
console.log('Patterns: ' + JSON.stringify(data.content_patterns));
