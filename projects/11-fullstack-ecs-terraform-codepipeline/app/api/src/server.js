import http from 'node:http';
import process from 'node:process';

import { createApp } from './app.js';

const port = Number(process.env.PORT ?? 3000);
const app = createApp();
const server = http.createServer(app);

// Fargate sends SIGTERM, then waits. Stop accepting connections, let in-flight
// requests finish, and only then exit — otherwise the ALB sees resets during
// every deployment.
let shuttingDown = false;

function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(JSON.stringify({ level: 'info', msg: 'shutting down', signal }));

  server.close(() => {
    console.log(JSON.stringify({ level: 'info', msg: 'closed cleanly' }));
    process.exit(0);
  });

  // Hard stop if connections refuse to drain.
  setTimeout(() => {
    console.warn(JSON.stringify({ level: 'warn', msg: 'forced exit after drain timeout' }));
    process.exit(1);
  }, 15_000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));

process.on('unhandledRejection', (reason) => {
  console.error(JSON.stringify({ level: 'error', msg: 'unhandled rejection', reason: String(reason) }));
});

server.listen(port, '0.0.0.0', () => {
  console.log(JSON.stringify({ level: 'info', msg: 'listening', port, env: process.env.NODE_ENV }));
});

export { server };
