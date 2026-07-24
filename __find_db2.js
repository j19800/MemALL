const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const logs = [];
const home = os.homedir();

// Check .memall directory
const memallDir = path.join(home, '.memall');
logs.push(`=== ${memallDir} ===`);
if (fs.existsSync(memallDir)) {
    const items = fs.readdirSync(memallDir);
    for (const item of items) {
        const fp = path.join(memallDir, item);
        try {
            const stat = fs.statSync(fp);
            logs.push(`  ${item} (${stat.isDirectory() ? 'DIR' : stat.size + ' bytes'})`);
        } catch(e) { logs.push(`  ${item} - error: ${e.message}`); }
    }
} else {
    logs.push('  NOT FOUND');
}

// Check E:\memall\.memall similarly
const altDir = 'E:\\memall\\.memall';
logs.push(`\n=== ${altDir} ===`);
if (fs.existsSync(altDir)) {
    const items = fs.readdirSync(altDir);
    for (const item of items) {
        const fp = path.join(altDir, item);
        try {
            const stat = fs.statSync(fp);
            logs.push(`  ${item} (${stat.isDirectory() ? 'DIR' : stat.size + ' bytes'})`);
        } catch(e) { logs.push(`  ${item} - error: ${e.message}`); }
    }
} else {
    logs.push('  NOT FOUND');
}

// Check if the MCP HTTP server is running
logs.push('\n=== MCP Server Status ===');
try {
    const result = execSync(`netstat -ano | findstr ":9876"`, {
        encoding: 'utf8', timeout: 5000
    });
    const lines = result.trim().split('\n');
    if (lines.length > 0 && lines[0].trim()) {
        for (const line of lines) {
            if (line.trim()) logs.push('  ' + line.trim());
        }
    } else {
        logs.push('  No listening on :9876');
    }
} catch(e) {
    logs.push('  No listening on :9876');
}

// Check the env.example for DB config
logs.push('\n=== .env.example ===');
if (fs.existsSync('E:\\memall\\.env.example')) {
    const content = fs.readFileSync('E:\\memall\\.env.example', 'utf8');
    for (const line of content.split('\n').filter(l => l.trim() && !l.startsWith('#'))) {
        logs.push('  ' + line.trim());
    }
}
if (fs.existsSync('E:\\memall\\.env')) {
    logs.push('\n=== .env (actual) ===');
    const content = fs.readFileSync('E:\\memall\\.env', 'utf8');
    for (const line of content.split('\n').filter(l => l.trim() && !l.startsWith('#'))) {
        logs.push('  ' + line.trim());
    }
}

// Check MemALL package for database path config
logs.push('\n=== Check memall python package ===');
const pkgDirs = [
    'E:\\memall\\memall',
    path.join(home, 'AppData', 'Local', 'Programs', 'Python', 'Python312', 'Lib', 'site-packages', 'memall'),
];
for (const dir of pkgDirs) {
    if (fs.existsSync(dir)) {
        logs.push(`${dir} EXISTS`);
        try {
            const files = fs.readdirSync(dir).filter(f => f.endsWith('.py')).slice(0, 20);
            for (const f of files) {
                logs.push(`  ${f}`);
            }
        } catch(e) {}
    }
}

// Look for db-related config in memall module files
if (fs.existsSync('E:\\memall\\memall')) {
    logs.push('\n=== Looking for DB path in memall module ===');
    try {
        // Find files that might contain "memories.db" or "database"
        const result = execSync(`cmd /c "findstr /s /m "memories.db\\|database\\|db_path\\|sqlite" "E:\\memall\\memall\\*.py" 2>nul"`, {
            encoding: 'utf8', timeout: 10000
        });
        const files = result.trim().split('\n').filter(f => f.trim());
        if (files.length > 0) {
            for (const f of files) {
                logs.push(`  Match in: ${f.trim()}`);
            }
        } else {
            logs.push('  No matches');
        }
    } catch(e) {
        logs.push('  Search error: ' + e.message);
    }
}

fs.writeFileSync('E:\\memall\\__db_search2.txt', logs.join('\n'));
console.log('DONE');
