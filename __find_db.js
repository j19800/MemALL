const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const logs = [];
const home = os.homedir();

// Search for actual database files
logs.push('=== Searching for SQLite DB files ===');
const searchDirs = [
    home,
    'E:\\memall',
    'C:\\Users\\Administrator',
];

for (const dir of searchDirs) {
    logs.push(`\nSearching ${dir}...`);
    try {
        const result = execSync(`cmd /c "dir /s /b "${dir}\\*.db" 2>nul"`, {
            encoding: 'utf8',
            timeout: 30000,
            maxBuffer: 10*1024*1024
        });
        const files = result.trim().split('\n').filter(f => f.trim());
        if (files.length > 0) {
            for (const f of files) {
                try {
                    const stat = fs.statSync(f.trim());
                    logs.push(`  ${f.trim()} -> ${stat.size} bytes, mod ${stat.mtime}`);
                } catch(e) {
                    logs.push(`  ${f.trim()} -> error: ${e.message}`);
                }
            }
        } else {
            logs.push('  No .db files found');
        }
    } catch(e) {
        logs.push(`  Search error: ${e.message}`);
    }
}

// Also search for .sqlite files
logs.push('\n=== Searching for .sqlite files ===');
for (const dir of searchDirs) {
    try {
        const result = execSync(`cmd /c "dir /s /b "${dir}\\*.sqlite" 2>nul"`, {
            encoding: 'utf8', timeout: 30000, maxBuffer: 10*1024*1024
        });
        const files = result.trim().split('\n').filter(f => f.trim());
        if (files.length > 0) {
            for (const f of files) {
                const stat = fs.statSync(f.trim());
                logs.push(`  ${f.trim()} -> ${stat.size} bytes, mod ${stat.mtime}`);
            }
        }
    } catch(e) {}
}

// Check for the MemALL data directory
logs.push('\n=== Checking MemALL data directories ===');
const dataDirs = [
    path.join(home, '.memall'),
    'E:\\memall\\.memall',
    'E:\\memall\\data',
    'E:\\memall\\db',
];
for (const d of dataDirs) {
    if (fs.existsSync(d)) {
        logs.push(`\n${d} EXISTS:`);
        try {
            const items = fs.readdirSync(d);
            for (const item of items) {
                const fullPath = path.join(d, item);
                const stat = fs.statSync(fullPath);
                logs.push(`  ${item} (${stat.isDirectory() ? 'DIR' : stat.size + ' bytes'})`);
            }
        } catch(e) {
            logs.push(`  read error: ${e.message}`);
        }
    } else {
        logs.push(`${d} NOT FOUND`);
    }
}

// Also check the MemALL package for default config
logs.push('\n=== Checking MemALL config ===');
const configFiles = [
    'E:\\memall\\.env',
    'E:\\memall\\.env.example',
    'E:\\memall\\memall\\config.py',
    'E:\\memall\\memall\\settings.py',
];
for (const f of configFiles) {
    logs.push(`${f}: ${fs.existsSync(f) ? 'EXISTS' : 'NOT FOUND'}`);
}

fs.writeFileSync('E:\\memall\\__db_search.txt', logs.join('\n'));
console.log('DONE');
