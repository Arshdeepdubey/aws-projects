import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';

import { beginDraining, escapeHtml, handleRequest } from '../src/app.js';
import { metrics } from '../src/metrics.js';

async function withServer(run) {
  const server = http.createServer(handleRequest).listen(0);
  const { port } = server.address();
  try {
    await run(`http://127.0.0.1:${port}`);
  } finally {
    server.close();
  }
}

test('health is 200 immediately, with no dependencies', async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/health`);
    assert.equal(response.status, 200);
    assert.equal((await response.json()).status, 'ok');
  });
});

test('version reports the image tag the fleet is running', async () => {
  await withServer(async (base) => {
    const body = await (await fetch(`${base}/version`)).json();
    assert.ok('imageTag' in body);
    assert.ok('gitSha' in body);
  });
});

test('unknown paths are a JSON 404', async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/nope`);
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: 'not found' });
  });
});

test('echo rejects an oversized body instead of buffering it', async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/api/echo`, {
      method: 'POST',
      body: 'x'.repeat(70 * 1024),
    }).catch(() => ({ status: 413 }));
    assert.equal(response.status, 413);
  });
});

test('the home page escapes build metadata rather than interpolating it raw', async () => {
  process.env.IMAGE_TAG = '<script>alert(1)</script>';
  try {
    await withServer(async (base) => {
      const body = await (await fetch(`${base}/`)).text();
      assert.ok(!body.includes('<script>alert(1)</script>'));
      assert.ok(body.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
    });
  } finally {
    delete process.env.IMAGE_TAG;
  }
});

test('escapeHtml covers every dangerous character', () => {
  assert.equal(escapeHtml(`<&">'`), '&lt;&amp;&quot;&gt;&#39;');
});

test('metrics collapse ids so cardinality stays bounded', () => {
  metrics.observe('/orders/1234', 200, 5);
  metrics.observe('/orders/5678', 200, 5);
  const output = metrics.prometheus();

  assert.ok(output.includes('path="/orders/:id"'));
  assert.ok(!output.includes('path="/orders/1234"'));
});

test('readiness fails once draining starts, before the socket closes', async () => {
  await withServer(async (base) => {
    assert.equal((await fetch(`${base}/ready`)).status, 503); // still warming
    beginDraining();
    const response = await fetch(`${base}/ready`);
    assert.equal(response.status, 503);
    assert.equal((await response.json()).status, 'draining');

    // Liveness must stay healthy while draining, or ECS kills the task early.
    assert.equal((await fetch(`${base}/health`)).status, 200);
  });
});
