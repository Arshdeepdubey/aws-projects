import http from 'node:http';
import process from 'node:process';

import { beginDraining, handleRequest } from './app.js';
import { metrics, startMetricsTimer } from './metrics.js';

const port = Number(process.env.PORT ?? 8080);
const DRAIN_MS = Number(process.env.DRAIN_MS ?? 25_000);

const server = http.createServer(handleRequest);
server.keepAliveTimeout = 65_000; // longer than the ALB's 60s idle timeout
server.headersTimeout = 70_000;

startMetricsTimer();

let shuttingDown = false;

function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;

  console.log(JSON.stringify({ level: 'info', msg: 'draining', signal }));
  // Fail readiness first so the ALB stops routing here, then stop accepting.
  beginDraining();
  metrics.emit();

  setTimeout(() => {
    server.close(() => {
      console.log(JSON.stringify({ level: 'info', msg: 'closed cleanly' }));
      process.exit(0);
    });
  }, 3000).unref();

  setTimeout(() => {
    console.warn(JSON.stringify({ level: 'warn', msg: 'forced exit after drain timeout' }));
    process.exit(1);
  }, DRAIN_MS).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));

process.on('uncaughtException', (error) => {
  console.error(JSON.stringify({ level: 'error', msg: 'uncaught exception', error: String(error?.stack ?? error) }));
  process.exit(1);
});

server.listen(port, '0.0.0.0', () => {
  console.log(
    JSON.stringify({
      level: 'info',
      msg: 'listening',
      port,
      version: process.env.IMAGE_TAG ?? 'local',
      env: process.env.NODE_ENV ?? 'development',
    }),
  );
});

export { server };
