import express from 'express';

import { createTodoStore } from './store.js';

export function createApp({ store = createTodoStore() } = {}) {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '64kb' }));

  // One structured log line per request. Fargate ships stdout to CloudWatch;
  // JSON means Logs Insights can query it without a parser.
  app.use((req, res, next) => {
    const startedAt = process.hrtime.bigint();
    res.on('finish', () => {
      const ms = Number(process.hrtime.bigint() - startedAt) / 1e6;
      console.log(
        JSON.stringify({
          level: res.statusCode >= 500 ? 'error' : 'info',
          method: req.method,
          path: req.path,
          status: res.statusCode,
          durationMs: Math.round(ms * 10) / 10,
          requestId: req.headers['x-amzn-trace-id'] ?? undefined,
        }),
      );
    });
    next();
  });

  // Health check must not touch the database: a slow dependency should show up
  // as 5xx on real routes, not as the ALB killing every healthy task.
  app.get('/api/health', (_req, res) => {
    res.json({ status: 'ok', env: process.env.NODE_ENV ?? 'development', uptime: process.uptime() });
  });

  app.get('/api/ready', async (_req, res) => {
    try {
      await store.ping();
      res.json({ status: 'ready' });
    } catch (error) {
      res.status(503).json({ status: 'not-ready', reason: String(error.message ?? error) });
    }
  });

  app.get('/api/todos', async (req, res, next) => {
    try {
      const ownerId = String(req.query.ownerId ?? 'demo');
      const limit = Math.min(Number(req.query.limit ?? 50), 200);
      res.json({ items: await store.list(ownerId, limit) });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/todos', async (req, res, next) => {
    try {
      const title = String(req.body?.title ?? '').trim();
      if (!title) return res.status(400).json({ error: 'title is required' });
      if (title.length > 200) return res.status(400).json({ error: 'title is too long' });

      const todo = await store.create({
        ownerId: String(req.body?.ownerId ?? 'demo'),
        title,
      });
      res.status(201).json(todo);
    } catch (error) {
      next(error);
    }
  });

  app.patch('/api/todos/:todoId', async (req, res, next) => {
    try {
      const done = Boolean(req.body?.done);
      const updated = await store.setDone(req.params.todoId, done);
      if (!updated) return res.status(404).json({ error: 'not found' });
      res.json(updated);
    } catch (error) {
      next(error);
    }
  });

  app.delete('/api/todos/:todoId', async (req, res, next) => {
    try {
      await store.remove(req.params.todoId);
      res.status(204).end();
    } catch (error) {
      next(error);
    }
  });

  app.use((_req, res) => res.status(404).json({ error: 'not found' }));

  // eslint-disable-next-line no-unused-vars
  app.use((error, _req, res, _next) => {
    console.error(JSON.stringify({ level: 'error', msg: 'request failed', error: String(error?.stack ?? error) }));
    res.status(500).json({ error: 'internal error' });
  });

  return app;
}
