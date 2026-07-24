const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const python = 'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';
const logs = [];

logs.push('Python: ' + (fs.existsSync(python) ? 'EXISTS' : 'NOT FOUND'));

// Check DB locations
const dbCandidates = [
    path.join(os.homedir(), '.memall', 'memories.db'),
    'E:\\memall\\.memall\\memories.db',
    'C:\\Users\\Administrator\\.memall\\memories.db',
];
for (const db of dbCandidates) {
    logs.push(`DB ${path.basename(path.dirname(db))}\\${path.basename(db)}: ${fs.existsSync(db) ? 'EXISTS (' + fs.statSync(db).size + ' bytes)' : 'NOT FOUND'}`);
}

// Run Python script
const scriptPath = 'E:\\memall\\__deep_analysis2.py';
if (fs.existsSync(scriptPath)) {
    logs.push('Script exists: ' + fs.statSync(scriptPath).size + ' bytes');
    try {
        const result = execSync(`"${python}" "${scriptPath}"`, {
            encoding: 'utf8',
            timeout: 120000,
            cwd: 'E:\\memall',
            maxBuffer: 50 * 1024 * 1024
        });
        logs.push('Python exit: 0');
        logs.push('stdout: ' + result);
    } catch (e) {
        logs.push('Python error: ' + e.message);
        logs.push('stdout: ' + (e.stdout || ''));
        logs.push('stderr: ' + (e.stderr || ''));
    }
} else {
    logs.push('Script NOT FOUND!');
}

// Check for results
const resultsPath = 'E:\\memall\\__deep_results.json';
if (fs.existsSync(resultsPath)) {
    logs.push('Results file: ' + fs.statSync(resultsPath).size + ' bytes');
    try {
        const data = JSON.parse(fs.readFileSync(resultsPath, 'utf8'));
        logs.push('Keys: ' + Object.keys(data).join(', '));
        if (data.level_dist) logs.push('Levels: ' + JSON.stringify(data.level_dist));
        if (data.edge_types) logs.push('Edges: ' + JSON.stringify(data.edge_types));
        if (data.missing_data) logs.push('Missing: ' + JSON.stringify(data.missing_data));
        if (data.time_range) logs.push('Time: ' + JSON.stringify(data.time_range));
        if (data.dedup) logs.push('Dedup: ' + JSON.stringify(data.dedup));
        if (data.agent_temporal) logs.push('Agent count: ' + data.agent_temporal.length);
        if (data.tables) logs.push('Table count: ' + Object.keys(data.tables).length);
    } catch(e) {
        logs.push('Parse error: ' + e.message);
    }
} else {
    logs.push('No results file created');
}

fs.writeFileSync('E:\\memall\\__node_analysis.txt', logs.join('\n'));
console.log('Done, wrote ' + logs.length + ' lines');
