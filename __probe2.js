const http = require('http');
const fs = require('fs');

function request(url, method, body) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const opts = {
            hostname: u.hostname,
            port: u.port,
            path: u.pathname || '/',
            method: method,
            headers: body ? {'Content-Type': 'application/json'} : {}
        };
        const req = http.request(opts, res => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve({status: res.statusCode, body: data}));
        });
        req.on('error', reject);
        if (body) req.write(body);
        req.end();
    });
}

(async () => {
    const out = [];
    out.push("=== MCP tools/list ===");
    try {
        const mcpReq = JSON.stringify({jsonrpc: "2.0", method: "tools/list", params: {}, id: 1});
        const res = await request('http://127.0.0.1:9876/mcp', 'POST', mcpReq);
        out.push(`Status: ${res.status}`);
        out.push(res.body.substring(0, 2000));
    } catch(e) { out.push("Error: " + e.message); }

    out.push("\n=== Root endpoint ===");
    try {
        const res = await request('http://127.0.0.1:9876/', 'GET');
        out.push(`Status: ${res.status}`);
        out.push(res.body.substring(0, 500));
    } catch(e) { out.push("Error: " + e.message); }

    out.push("\n=== API Routes ===");
    try {
        const res = await request('http://127.0.0.1:9876/api/routes', 'GET');
        out.push(`Status: ${res.status}`);
        out.push(res.body.substring(0, 2000));
    } catch(e) { out.push("Error: " + e.message); }

    fs.writeFileSync('E:\\memall\\__probe_result.txt', out.join('\n'), 'utf8');
    console.log("Written to __probe_result.txt");
})();
