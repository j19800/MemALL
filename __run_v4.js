const fs = require('fs');
const { execSync } = require('child_process');

const python = 'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';
const script = 'E:\\memall\\__deep_v4.py';

try {
    const result = execSync(`"${python}" "${script}"`, {
        encoding: 'utf8',
        timeout: 300000,
        cwd: 'E:\\memall',
        maxBuffer: 10 * 1024 * 1024
    });
    fs.writeFileSync('E:\\memall\\__v4_result.txt', result);
    console.log('SUCCESS:', result.slice(0,500));
} catch(e) {
    const errMsg = 'ERROR: ' + e.message + '\nSTDOUT: ' + (e.stdout || '').slice(0,3000) + '\nSTDERR: ' + (e.stderr || '').slice(0,3000);
    fs.writeFileSync('E:\\memall\\__v4_result.txt', errMsg);
    console.log('FAILED:', errMsg.slice(0,500));
}
