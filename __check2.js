const { spawnSync } = require('child_process');
const fs = require('fs');

function run(cmd, args) {
    const result = spawnSync(cmd, args, { timeout: 10000, encoding: 'utf8', shell: true });
    if (result.error) return `ERROR: ${result.error.message}\n${result.stderr || ''}`;
    return result.stdout + '\n' + result.stderr;
}

const results = [];
results.push("=== MEMALL SERVE --HTTP --HELP ===");
results.push(run('C:\\Users\\Administrator\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe', ['-m', 'memall', 'serve', '--http', '--help']));

results.push("=== MEMALL MCP CONNECT --HELP ===");
results.push(run('C:\\Users\\Administrator\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe', ['-m', 'memall', 'mcp', 'connect', '--help']));

results.push("=== MCP SERVERS JSON ===");
try {
    results.push(fs.readFileSync('E:\\memall\\mcp_servers.json', 'utf8'));
} catch(e) { results.push('File not found'); }

results.push("=== .env.example ===");
try {
    results.push(fs.readFileSync('E:\\memall\\.env.example', 'utf8'));
} catch(e) { results.push('File not found'); }

try {
    const files = fs.readdirSync('E:\\memall').filter(f => f.includes('mcp'));
    results.push("=== MCP FILES ===");
    results.push(files.join('\n'));
} catch(e) {}

fs.writeFileSync('E:\\memall\\__check_result.txt', results.join('\n'), 'utf8');
