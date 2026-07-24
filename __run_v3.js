const fs = require('fs');
const { execSync } = require('child_process');

const python = 'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';
const script = 'E:\\memall\\__deep_v3.py';

try {
    const result = execSync(`"${python}" "${script}"`, {
        encoding: 'utf8', timeout: 30000, cwd: 'E:\\memall', maxBuffer: 10*1024*1024
    });
    const outfile = 'E:\\memall\\__deep_v3_out.txt';
    fs.writeFileSync(outfile, result);
    console.log('OUTPUT:', result);
} catch(e) {
    console.log('ERROR:', e.message);
    if (e.stdout) console.log('STDOUT:', e.stdout);
    if (e.stderr) console.log('STDERR:', e.stderr.slice(0,2000));
}
