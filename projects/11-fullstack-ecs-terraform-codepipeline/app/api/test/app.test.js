import assert from 'node:assert/strict';
import test from 'node:test';

import { createApp } from '../src/app.js';
import { createMemoryStore } from '../src/store.js';

async function withServer(run) {
  const app = createApp({ store: createMemoryStore() });
  const server = app.listen(0);
  const { port } = server.address();
  try {
    await run(`http://127.0.0.1:${port}`);
  } finally {
    server.close();
  }
}

test('health check does not depend on the database', async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/api/health`);
    assert.equal(response.status, 200);
    assert.equal((await response.json()).status, 'ok');
  });
});

test('creates and lists todos', async () => {
  await withServer(async (base) => {
    const created = await fetch(`${base}/api/todos`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ title: 'Ship the pipeline' }),
    });
    assert.equal(created.status, 201);
    const todo = await created.json();
    assert.equal(todo.done, false);

    const list = await (await fetch(`${base}/api/todos`)).json();
    assert.equal(list.items.length, 1);
    assert.equal(list.items[0].title, 'Ship the pipeline');
  });
});

test('rejects an empty title', async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/api/todos`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ title: '   ' }),
    });
    assert.equal(response.status, 400);
  });
});

test('patching a missing todo is a 404, not a 500', async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/api/todos/does-not-exist`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ done: true }),
    });
    assert.equal(response.status, 404);
  });
});

test('unknown routes return JSON 404', async () => {
  await withServer(async (base) => {
    const response = await fetch(`${base}/api/nope`);
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: 'not found' });
  });
});
