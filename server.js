const http     = require('http');
const { spawn } = require('child_process');
const fs       = require('fs');
const path     = require('path');

const PORT = 3000;
const ROOT = __dirname;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.js':   'text/javascript; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.ico':  'image/x-icon',
};

function serveStatic(req, res) {
  const urlPath  = req.url.split('?')[0];
  const filePath = path.join(ROOT, urlPath === '/' ? 'index.html' : urlPath);

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      res.writeHead(404);
      res.end('Not found');
      return;
    }
    const ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    fs.createReadStream(filePath).pipe(res);
  });
}

function runGenerate(res) {
  console.log('[refresh] running python3 scripts/codex-dashboard.py …');

  const proc   = spawn('python3', ['scripts/codex-dashboard.py'], { cwd: ROOT });
  const output = [];

  proc.stdout.on('data', d => { process.stdout.write(d); output.push(d); });
  proc.stderr.on('data', d => { process.stderr.write(d); output.push(d); });

  proc.on('close', code => {
    if (code === 0) {
      console.log('[refresh] done');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } else {
      console.error(`[refresh] exited with code ${code}`);
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: Buffer.concat(output).toString() }));
    }
  });
}

const server = http.createServer((req, res) => {
  // CORS headers for local dev
  res.setHeader('Access-Control-Allow-Origin', '*');

  if (req.method === 'POST' && req.url === '/api/refresh') {
    runGenerate(res);
    return;
  }

  serveStatic(req, res);
});

server.listen(PORT, () => {
  console.log(`Codex Dashboard → http://localhost:${PORT}`);
});
