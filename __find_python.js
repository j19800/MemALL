const fs = require('fs');
const path = require('path');
const os = require('os');

// Find python
const candidates = [
    'python.exe', 'python3.exe', 'py.exe',
    'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python311\\python.exe',
    'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe',
    'C:\\Program Files\\Python311\\python.exe',
    'C:\\Program Files\\Python312\\python.exe',
    'C:\\Python311\\python.exe',
    'C:\\Python312\\python.exe',
];

// Try to use where
const { execSync } = require('child_process');

let pythonPath = null;
for (const cmd of ['where python', 'where python3', 'where py', 'where.exe python']) {
    try {
        const out = execSync(cmd, { encoding: 'utf8', timeout: 5000 });
        const lines = out.trim().split('\n').filter(l => l.trim().endsWith('.exe'));
        if (lines.length > 0) {
            pythonPath = lines[0].trim();
            break;
        }
    } catch(e) {}
}

if (!pythonPath) {
    for (const c of candidates) {
        if (fs.existsSync(c)) {
            pythonPath = c;
            break;
        }
    }
}

fs.writeFileSync('E:\\memall\\__python_path.txt', pythonPath || 'NOT_FOUND');
console.log('Python:', pythonPath);

if (pythonPath && fs.existsSync(pythonPath)) {
    try {
        const result = execSync(`"${pythonPath}" E:\\memall\\__deep_analysis2.py`, {
            encoding: 'utf8',
            timeout: 60000,
            cwd: 'E:\\memall'
        });
        fs.writeFileSync('E:\\memall\\__deep_analysis2_out.txt', result);
        console.log('Script output:', result);
        
        // Check if results file was created
        if (fs.existsSync('E:\\memall\\__deep_results.json')) {
            const data = JSON.parse(fs.readFileSync('E:\\memall\\__deep_results.json', 'utf8'));
            console.log('Results file size:', JSON.stringify(data).length, 'bytes');
            console.log('Tables found:', Object.keys(data.tables || {}).length);
            console.log('Level distribution:', JSON.stringify(data.level_dist || []));
            console.log('Edge types:', JSON.stringify(data.edge_types || []));
            console.log('Missing data:', JSON.stringify(data.missing_data || {}));
            console.log('Time range:', JSON.stringify(data.time_range || {}));
            console.log('Total memories:', data.dedup);
        } else {
            console.log('ERROR: results file not created!');
        }
    } catch (e) {
        console.error('Execution error:', e.message);
        const stderr = e.stderr || '';
        console.error('Stderr:', stderr);
    }
}

// Also check if deep_analysis_out.txt exists
for (const f of ['__deep_analysis_out.txt', '__deep_results.json', '__deep_analysis2_out.txt']) {
    const fp = `E:\\memall\\${f}`;
    if (fs.existsSync(fp)) {
        console.log(`${f} exists, size:`, fs.statSync(fp).size);
    } else {
        console.log(`${f} NOT FOUND`);
    }
}
