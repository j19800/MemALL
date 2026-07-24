const fs = require('fs');
const { execSync } = require('child_process');

const python = 'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';
const script = 'E:\\memall\\__deep_v2.py';
const outputFile = 'E:\\memall\\__deep_v2_out.txt';

try {
    const result = execSync(`"${python}" "${script}"`, {
        encoding: 'utf8',
        timeout: 300000,
        cwd: 'E:\\memall',
        maxBuffer: 100 * 1024 * 1024
    });
    fs.writeFileSync(outputFile, result);
    console.log('SUCCESS');
} catch(e) {
    const errMsg = 'ERROR: ' + e.message + '\nstdout: ' + (e.stdout || '').slice(0,5000) + '\nstderr: ' + (e.stderr || '').slice(0,5000);
    fs.writeFileSync(outputFile, errMsg);
    console.log('FAILED');
}
