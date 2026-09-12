/**
 * Minimal metrics: Prometheus text on /metrics, plus CloudWatch EMF log lines so
 * the same counters become CloudWatch metrics with no agent and no extra cost
 * beyond the log ingestion you already pay for.
 */

const counters = new Map();
const durations = [];
const MAX_DURATION_SAMPLES = 1000;
const NAMESPACE = process.env.METRICS_NAMESPACE ?? 'fargate-web';
const EMF_INTERVAL_MS = Number(process.env.EMF_INTERVAL_MS ?? 60_000);

export const metrics = {
  observe(path, status, durationMs) {
    const key = `${normalise(path)}|${status}`;
    counters.set(key, (counters.get(key) ?? 0) + 1);

    durations.push(durationMs);
    if (durations.length > MAX_DURATION_SAMPLES) durations.shift();
  },

  snapshot() {
    const sorted = [...durations].sort((a, b) => a - b);
    const total = [...counters.values()].reduce((sum, count) => sum + count, 0);
    const errors = [...counters.entries()]
      .filter(([key]) => Number(key.split('|')[1]) >= 500)
      .reduce((sum, [, count]) => sum + count, 0);

    return {
      requests: total,
      errors,
      p50: percentile(sorted, 0.5),
      p99: percentile(sorted, 0.99),
    };
  },

  prometheus() {
    const lines = [
      '# HELP http_requests_total Total HTTP requests',
      '# TYPE http_requests_total counter',
    ];

    for (const [key, count] of counters) {
      const [path, status] = key.split('|');
      lines.push(`http_requests_total{path="${path}",status="${status}"} ${count}`);
    }

    const { p50, p99 } = metrics.snapshot();
    lines.push(
      '# HELP http_request_duration_ms Request duration quantiles',
      '# TYPE http_request_duration_ms summary',
      `http_request_duration_ms{quantile="0.5"} ${p50}`,
      `http_request_duration_ms{quantile="0.99"} ${p99}`,
    );

    return `${lines.join('\n')}\n`;
  },

  /** Emit one EMF line; CloudWatch turns it into metrics automatically. */
  emit() {
    const { requests, errors, p50, p99 } = metrics.snapshot();
    if (requests === 0) return;

    console.log(
      JSON.stringify({
        _aws: {
          Timestamp: Date.now(),
          CloudWatchMetrics: [
            {
              Namespace: NAMESPACE,
              Dimensions: [['Environment']],
              Metrics: [
                { Name: 'Requests', Unit: 'Count' },
                { Name: 'Errors', Unit: 'Count' },
                { Name: 'LatencyP50', Unit: 'Milliseconds' },
                { Name: 'LatencyP99', Unit: 'Milliseconds' },
              ],
            },
          ],
        },
        Environment: process.env.NODE_ENV ?? 'development',
        Requests: requests,
        Errors: errors,
        LatencyP50: p50,
        LatencyP99: p99,
      }),
    );
  },
};

export function startMetricsTimer() {
  const timer = setInterval(() => metrics.emit(), EMF_INTERVAL_MS);
  timer.unref();
  return timer;
}

function normalise(path) {
  // Collapse ids so cardinality stays bounded: /orders/abc123 -> /orders/:id
  return path.replace(/\/[0-9a-f]{8,}(?=\/|$)/gi, '/:id').replace(/\/\d+(?=\/|$)/g, '/:id');
}

function percentile(sorted, fraction) {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.floor(sorted.length * fraction));
  return Math.round(sorted[index] * 10) / 10;
}
