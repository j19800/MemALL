const fs = require('fs');
const { execSync } = require('child_process');

const python = 'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';
const script = 'E:\\memall\\__deep_analysis3.py';
const outputFile = 'E:\\memall\\__deep_analysis3_out.txt';

try {
    const result = execSync(`"${python}" "${script}"`, {
        encoding: 'utf8',
        timeout: 300000,
        cwd: 'E:\\memall',
        maxBuffer: 100 * 1024 * 1024
    });
    fs.writeFileSync(outputFile, result);
    console.log('SUCCESS: ' + result.slice(0, 200) + '...');
} catch(e) {
    const errMsg = 'ERROR: ' + e.message + '\nstdout: ' + (e.stdout || '') + '\nstderr: ' + (e.stderr || '');
    fs.writeFileSync(outputFile, errMsg);
    console.log('FAILED: ' + errMsg.slice(0, 300));
}
