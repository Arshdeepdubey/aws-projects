import { metrics } from './metrics.js';

// Read at request time rather than at import: the value is identical in
// production (ECS sets these before the process starts) and it keeps the
// rendering testable without module-cache tricks.
export function versionInfo() {
  return {
    imageTag: process.env.IMAGE_TAG ?? 'local',
    gitSha: process.env.GIT_SHA ?? 'unknown',
    builtAt: process.env.BUILT_AT ?? 'unknown',
    environment: process.env.NODE_ENV ?? 'development',
  };
}

// Readiness is separate from liveness: the task is alive immediately but should
// not take traffic until warm-up finishes.
const STARTED_AT = Date.now();
const WARMUP_MS = Number(process.env.WARMUP_MS ?? 2000);

let draining = false;

export function beginDraining() {
  draining = true;
}

export function handleRequest(req, res) {
  const startedAt = process.hrtime.bigint();
  const url = new URL(req.url, `http://${req.headers.host ?? 'localhost'}`);

  res.on('finish', () => {
    const durationMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
    metrics.observe(url.pathname, res.statusCode, durationMs);

    console.log(
      JSON.stringify({
        level: res.statusCode >= 500 ? 'error' : 'info',
        method: req.method,
        path: url.pathname,
        status: res.statusCode,
        durationMs: Math.round(durationMs * 10) / 10,
        taskArn: process.env.ECS_TASK_ARN?.split('/').pop(),
      }),
    );
  });

  switch (url.pathname) {
    // Liveness: no dependencies. If this fails the process is broken, and ECS
    // should replace the task.
    case '/health':
      return json(res, 200, { status: 'ok', uptimeSeconds: Math.round(process.uptime()) });

    // Readiness: reports not-ready while warming and while draining, so the ALB
    // stops sending new requests before the process exits.
    case '/ready': {
      if (draining) return json(res, 503, { status: 'draining' });
      const warm = Date.now() - STARTED_AT >= WARMUP_MS;
      return json(res, warm ? 200 : 503, { status: warm ? 'ready' : 'warming' });
    }

    case '/version':
      return json(res, 200, versionInfo());

    case '/metrics':
      res.writeHead(200, { 'content-type': 'text/plain; version=0.0.4' });
      return res.end(metrics.prometheus());

    case '/':
      return html(res);

    case '/api/echo': {
      if (req.method !== 'POST') return json(res, 405, { error: 'method not allowed' });
      return readBody(req, 64 * 1024)
        .then((body) => json(res, 200, { received: body.slice(0, 2000), length: body.length }))
        .catch((error) => json(res, 413, { error: String(error.message ?? error) }));
    }

    // Deliberate failure, for testing that an alarm rolls a deployment back.
    case '/boom':
      return json(res, 500, { error: 'deliberate failure' });

    default:
      return json(res, 404, { error: 'not found' });
  }
}

function json(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(payload) });
  res.end(payload);
}

function html(res) {
  const version = versionInfo();
  const page = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Running on ECS Fargate</title>
<style>
:root{color-scheme:light dark}
body{margin:0;font:16px/1.6 system-ui,-apple-system,sans-serif;display:grid;place-items:center;min-height:100vh}
main{max-width:32rem;padding:2rem}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem}
dt{font-weight:600}dd{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
a{color:inherit}
</style></head>
<body><main>
<h1>Running on ECS Fargate</h1>
<dl>
<dt>Image tag</dt><dd>${escapeHtml(version.imageTag)}</dd>
<dt>Commit</dt><dd>${escapeHtml(version.gitSha)}</dd>
<dt>Built</dt><dd>${escapeHtml(version.builtAt)}</dd>
<dt>Environment</dt><dd>${escapeHtml(version.environment)}</dd>
<dt>Uptime</dt><dd>${Math.round(process.uptime())}s</dd>
</dl>
<p><a href="/health">/health</a> · <a href="/ready">/ready</a> · <a href="/version">/version</a> · <a href="/metrics">/metrics</a></p>
</main></body></html>`;

  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(page);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char],
  );
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];

    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new Error('payload too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

export { escapeHtml };
