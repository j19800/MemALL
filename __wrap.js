const { execSync } = require('child_process');
try {
  const out = execSync('node E:\\memall\\__summarize_deep.js', {
    encoding: 'utf8', timeout: 30000, cwd: 'E:\\memall',
    maxBuffer: 50 * 1024 * 1024
  });
  require('fs').writeFileSync('E:\\memall\\__wrap_out.txt', 'SUCCESS\n' + out);
} catch (e) {
  require('fs').writeFileSync('E:\\memall\\__wrap_out.txt', 
    'EXIT: ' + e.status + '\n' +
    'STDOUT: ' + (e.stdout || '').slice(0,5000) + '\n' +
    'STDERR: ' + (e.stderr || '').slice(0,5000));
}
