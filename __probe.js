const http = require('http');

function request(url, method, body) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const opts = {
            hostname: u.hostname,
            port: u.port,
            path: u.pathname,
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
    // MCP endpoint - tools/list
    console.log("=== MCP tools/list ===");
    try {
        const mcpReq = JSON.stringify({jsonrpc: "2.0", method: "tools/list", params: {}, id: 1});
        const res = await request('http://127.0.0.1:9876/mcp', 'POST', mcpReq);
        console.log(`Status: ${res.status}`);
        console.log(res.body.substring(0, 1000));
    } catch(e) { console.log("Error:", e.message); }

    // Root
    console.log("\n=== Root endpoint ===");
    try {
        const res = await request('http://127.0.0.1:9876/', 'GET');
        console.log(`Status: ${res.status}`);
        console.log(res.body.substring(0, 500));
    } catch(e) { console.log("Error:", e.message); }

    // API routes
    console.log("\n=== API Routes ===");
    try {
        const res = await request('http://127.0.0.1:9876/api/routes', 'GET');
        console.log(`Status: ${res.status}`);
        console.log(res.body.substring(0, 1000));
    } catch(e) { console.log("Error:", e.message); }

    // Health
    console.log("\n=== Health ===");
    try {
        const res = await request('http://127.0.0.1:9876/api/health', 'GET');
        console.log(`Status: ${res.status}`);
        console.log(res.body.substring(0, 500));
    } catch(e) { console.log("Error:", e.message); }

    console.log("\n=== Done ===");
})();
