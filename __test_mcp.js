const http = require('http');

function request(url, method, body) {
    return new Promise((resolve, reject) => {
        const u = new URL(url.startsWith('http') ? url : 'http://127.0.0.1:9876/mcp');
        const opts = {
            hostname: u.hostname,
            port: u.port,
            path: '/mcp',
            method: method || 'POST',
            headers: { 'Content-Type': 'application/json' }
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
    // 1. Initialize session
    console.log("1. Initialize...");
    const initRes = await request('http://127.0.0.1:9876/mcp', 'POST', JSON.stringify({
        jsonrpc: "2.0", method: "initialize", params: {
            protocolVersion: "2024-11-05",
            capabilities: {},
            clientInfo: { name: "nomi-desktop", version: "1.0.0" }
        }, id: 1
    }));
    console.log("Init:", initRes.status);
    console.log(initRes.body.substring(0, 500));

    // 2. List tools
    console.log("\n2. List tools...");
    const toolsRes = await request('http://127.0.0.1:9876/mcp', 'POST', JSON.stringify({
        jsonrpc: "2.0", method: "tools/list", params: {}, id: 2
    }));
    console.log("Tools:", toolsRes.status);
    console.log(toolsRes.body.substring(0, 2000));

    // 3. List resources
    console.log("\n3. List resources...");
    const resRes = await request('http://127.0.0.1:9876/mcp', 'POST', JSON.stringify({
        jsonrpc: "2.0", method: "resources/list", params: {}, id: 3
    }));
    console.log("Resources:", resRes.status);
    console.log(resRes.body.substring(0, 2000));

    require('fs').writeFileSync('E:\\memall\\__mcp_test_result.txt', 
        `INIT: ${initRes.status}\n${initRes.body}\n\nTOOLS: ${toolsRes.status}\n${toolsRes.body}\n\nRESOURCES: ${resRes.status}\n${resRes.body}`, 'utf8');
    console.log("\nDone - written to __mcp_test_result.txt");
})();
