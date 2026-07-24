const http = require('http');

const MCP_URL = 'http://127.0.0.1:9876/mcp';

function mcpCall(method, params) {
    return new Promise((resolve, reject) => {
        const payload = JSON.stringify({ jsonrpc: "2.0", method, params, id: Date.now() });
        const u = new URL(MCP_URL);
        const req = http.request({
            hostname: u.hostname, port: u.port, path: '/mcp',
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        }, res => {
            let data = '';
            res.on('data', c => data += c);
            res.on('end', () => {
                try { resolve(JSON.parse(data)); }
                catch(e) { resolve({ raw: data }); }
            });
        });
        req.on('error', reject);
        req.write(payload);
        req.end();
    });
}

async function queryAll(action, params, label) {
    console.log(`[${label}] ${action}...`);
    const r = await mcpCall('tools/call', {
        name: action,
        arguments: params
    });
    const content = r?.result?.content?.[0]?.text || JSON.stringify(r);
    return { label, result: content };
}

(async () => {
    const results = [];

    // 1. System stats
    results.push(await queryAll('memall_system', { action: 'db', sub_action: 'stats' }, 'System Stats'));

    // 2. Browse each major category
    const categories = [
        'reflection', 'architecture', 'implementation', 'session',
        'problem', 'meeting', 'testing', 'general', 'fix',
        'message', 'planning', 'deployment', 'config', 'decision',
        'task', 'domain', 'milestone', 'discussion'
    ];

    for (const cat of categories) {
        results.push(await queryAll('memall_read', {
            action: 'retrieve',
            category: cat,
            limit: 8
        }, `Category: ${cat}`));
    }

    // 3. Get recent timeline (last 24h)
    results.push(await queryAll('memall_read', {
        action: 'timeline',
        hours: 48,
        limit: 20
    }, 'Recent Timeline (48h)'));

    // 4. Hot topics
    results.push(await queryAll('memall_system', {
        action: 'hot',
        limit: 20
    }, 'Hot Topics'));

    // 5. Daily digest
    results.push(await queryAll('memall_system', {
        action: 'digest'
    }, 'Daily Digest'));

    // 6. Search for top-level themes
    results.push(await queryAll('memall_read', {
        action: 'search',
        query: '',
        limit: 30,
        mode: 'auto'
    }, 'Top Memories'));

    // 7. Agent personas
    results.push(await queryAll('memall_persona', {
        action: 'persona',
        agent_name: ''
    }, 'All Personas'));

    // 8. Federation overview
    results.push(await queryAll('memall_federation', {
        action: 'query',
        query: ''
    }, 'Federation Overview'));

    // 9. Reflection analysis
    results.push(await queryAll('memall_read', {
        action: 'search',
        query: '总结 分析 评估',
        category: 'reflection',
        limit: 15
    }, 'Reflection Analysis'));

    // 10. Graph explore
    results.push(await queryAll('memall_read', {
        action: 'traverse',
        node_id: 1,
        depth: 2,
        limit: 50
    }, 'Graph Traverse'));

    require('fs').writeFileSync('E:\\memall\\__analysis.json', JSON.stringify(results, null, 2), 'utf8');
    console.log(`Done: ${results.length} queries`);
})();
