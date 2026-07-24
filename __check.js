const { execSync } = require('child_process');
const fs = require('fs');

const results = [];

function run(cmd) {
    try {
        const out = execSync(cmd, { timeout: 10000, encoding: 'utf8' });
        return out;
    } catch(e) {
        return `ERROR: ${e.message}`;
    }
}

results.push("=== MEMALL SERVE --HTTP --HELP ===");
results.push(run('C:\\Users\\Administrator\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe -m memall serve --http --help'));

results.push("\n=== MEMALL MCP CONNECT --HELP ===");
results.push(run('C:\\Users\\Administrator\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe -m memall mcp connect --help'));

results.push("\n=== MCP SERVERS JSON ===");
const mcpJson = fs.readFileSync('E:\\memall\\mcp_servers.json', 'utf8');
results.push(mcpJson);

const mcpExample = fs.readFileSync('E:\\memall\\mcp_servers.json.example', 'utf8');
results.push("\n=== MCP SERVERS EXAMPLE ===");
results.push(mcpExample);

const envExample = fs.readFileSync('E:\\memall\\.env.example', 'utf8');
results.push("\n=== .ENV.EXAMPLE ===");
results.push(envExample);

results.push("\n=== MCP FILES ===");
const files = fs.readdirSync('E:\\memall').filter(f => f.includes('mcp'));
results.push(files.join('\n'));

results.push("\n=== CURRENT MCP_HTTP ERR LOG ===");
try {
    const log = fs.readFileSync('E:\\memall\\mcp_http_err.log', 'utf8').substring(0, 1000);
    results.push(log);
} catch(e) { results.push('No mcp_http_err.log'); }

results.push("\n=== CURRENT PID 20236 ===");
results.push(run('tasklist /FI "PID eq 20236" /NH'));

fs.writeFileSync('E:\\memall\\__check_all.txt', results.join('\n'), 'utf8');
console.log('Written');
