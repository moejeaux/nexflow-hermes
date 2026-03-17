/**
 * NexFlow BullMQ Scheduler
 *
 * Replaces the node-cron scheduler with BullMQ repeatable jobs.
 * Each job is an HTTP call to the Fastify API (localhost:3002).
 *
 * Key improvements over node-cron:
 * - Overlap prevention (concurrency: 1 per queue)
 * - Retry with exponential backoff
 * - Per-job timeouts
 * - Deduplication
 * - Bull Board UI for visibility
 *
 * Usage:
 *   pm2 start npx --name nexflow-bullmq-scheduler -- tsx queue-scheduler.ts
 */

import { Queue, Worker, QueueEvents } from 'bullmq';
import { createBullBoard } from '@bull-board/api';
import { BullMQAdapter } from '@bull-board/api/bullMQAdapter';
import { FastifyAdapter } from '@bull-board/fastify';
import Fastify from 'fastify';

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const REDIS_HOST = process.env.REDIS_HOST || '127.0.0.1';
const REDIS_PORT = Number(process.env.REDIS_PORT) || 6379;
const API_BASE_URL = process.env.SCHEDULER_API_URL || 'http://localhost:3002';
const CRON_SECRET = process.env.CRON_SECRET?.trim();
const BOARD_PORT = Number(process.env.BOARD_PORT) || 3003;

if (!CRON_SECRET) {
  console.error('❌ CRON_SECRET is required');
  process.exit(1);
}

const connection = { host: REDIS_HOST, port: REDIS_PORT };

// ---------------------------------------------------------------------------
// Job definitions — migrated 1:1 from scheduler/index.ts
// ---------------------------------------------------------------------------

interface JobDef {
  name: string;
  endpoint: string;
  schedule: string;
  /** Per-job timeout in ms. Default: 60_000 */
  timeout?: number;
  /** Number of retry attempts on failure. Default: 2 */
  attempts?: number;
  description?: string;
}

const JOBS: JobDef[] = [
  // HIGH FREQUENCY — every minute
  {
    name: 'commerce-indexer',
    endpoint: '/api/cron/commerce-indexer',
    schedule: '*/1 * * * *',
    timeout: 120_000,   // 2 min (indexing can be slow)
    attempts: 2,
    description: 'Commerce ERC-8183 event indexer',
  },

  // HIGH FREQUENCY — every 5 min
  {
    name: 'dogfood',
    endpoint: '/api/cron/dogfood',
    schedule: '*/5 * * * *',
    description: 'Dogfood Agent — exercises routes end-to-end',
  },
  {
    name: 'facilitator-probes',
    endpoint: '/api/cron/facilitator-probes',
    schedule: '*/5 * * * *',
    description: 'Facilitator health probes',
  },
  {
    name: 'evaluate-anomalies',
    endpoint: '/api/cron/evaluate-anomalies',
    schedule: '*/5 * * * *',
    description: 'Anomaly evaluation + circuit breakers',
  },
  {
    name: 'fleet-survival',
    endpoint: '/api/cron/fleet-survival',
    schedule: '*/5 * * * *',
    description: 'Fleet hunger decay + metrics flush',
  },

  // MEDIUM FREQUENCY — every 10 min
  {
    name: 'house-x402-job',
    endpoint: '/api/cron/house-x402-job',
    schedule: '*/10 * * * *',
    description: 'Internal x402 traffic generation',
  },
  {
    name: 'recommendation-applier',
    endpoint: '/api/cron/recommendation-applier',
    schedule: '*/10 * * * *',
    description: 'Apply agent recommendations',
  },

  // MEDIUM FREQUENCY — every 15 min
  {
    name: 'scout',
    endpoint: '/api/cron/scout',
    schedule: '*/15 * * * *',
    description: 'Test under-used facilitator routes',
  },
  {
    name: 'facilitator-probes-deep',
    endpoint: '/api/cron/facilitator-probes-deep',
    schedule: '*/15 * * * *',
    description: 'Comprehensive facilitator health checks',
  },
  {
    name: 'social-reply',
    endpoint: '/api/cron/social-reply',
    schedule: '*/15 * * * *',
    description: 'Farcaster reply scanner',
  },

  // LOW FREQUENCY — every 2 hours
  {
    name: 'coordinator',
    endpoint: '/api/cron/coord?bandit=true&banditHours=1',
    schedule: '0 */2 * * *',
    timeout: 180_000,   // 3 min
    attempts: 2,
    description: 'AI optimization loop + bandit simulation',
  },

  // LOW FREQUENCY — every 6 hours
  {
    name: 'pull-metrics',
    endpoint: '/api/cron/pull-metrics',
    schedule: '0 */6 * * *',
    description: 'Metrics from x402scan + Scattering',
  },
  {
    name: 'crawl-actions',
    endpoint: '/api/cron/crawl-actions',
    schedule: '0 */6 * * *',
    description: 'Discovery action crawler',
  },

  // DAILY
  {
    name: 'social-daily',
    endpoint: '/api/cron/social-daily',
    schedule: '0 9 * * *',
    timeout: 120_000,
    description: 'Daily Farcaster content pipeline',
  },
  {
    name: 'daily-health-report',
    endpoint: '/api/cron/daily-health-report',
    schedule: '0 7 * * *',
    description: 'Daily health report',
  },
  {
    name: 'cphy-sync',
    endpoint: '/api/cron/cphy-sync',
    schedule: '0 8 * * *',
    description: 'CPHY data sync',
  },

  // x402 discovery
  {
    name: 'x402-discovery-high',
    endpoint: '/api/cron/x402-discovery?maxPriority=5',
    schedule: '0 0 * * *',
    timeout: 120_000,
    description: 'x402 discovery — high priority',
  },
  {
    name: 'x402-discovery-low',
    endpoint: '/api/cron/x402-discovery?maxPriority=3',
    schedule: '0 0 */3 * *',
    timeout: 120_000,
    description: 'x402 discovery — low priority',
  },

  // Job hunter (two rotations)
  {
    name: 'job-hunter',
    endpoint: '/api/cron/job-hunter',
    schedule: '0 0,3,6,9,12,15,18,21 * * *',
    timeout: 180_000,
    attempts: 2,
    description: 'Job hunter — main rotation',
  },
  {
    name: 'job-hunter-half',
    endpoint: '/api/cron/job-hunter',
    schedule: '30 1,4,7,10,13,16,19,22 * * *',
    timeout: 180_000,
    attempts: 2,
    description: 'Job hunter — half rotation',
  },
];

// ---------------------------------------------------------------------------
// Build queues + workers
// ---------------------------------------------------------------------------

const queues: Queue[] = [];
const workers: Worker[] = [];

async function executeJob(name: string, endpoint: string, timeoutMs: number): Promise<void> {
  const url = `${API_BASE_URL}${endpoint}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${CRON_SECRET}`,
        'X-Cron-Secret': CRON_SECRET!,
        'User-Agent': 'NexFlow-BullMQ-Scheduler/1.0',
        'X-Trigger-Source': 'bullmq-scheduler',
      },
      signal: controller.signal,
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}: ${body.slice(0, 300)}`);
    }

    const data = await res.json().catch(() => ({}));
    console.log(`✅ [${name}] ${res.status} — ${JSON.stringify(data).slice(0, 200)}`);
  } finally {
    clearTimeout(timer);
  }
}

for (const job of JOBS) {
  const queueName = `cron-${job.name}`;
  const timeout = job.timeout ?? 60_000;
  const attempts = job.attempts ?? 2;

  const queue = new Queue(queueName, { connection });
  queues.push(queue);

  // Remove any old repeatable jobs, then add the current one
  const existing = await queue.getRepeatableJobs();
  for (const r of existing) {
    await queue.removeRepeatableByKey(r.key);
  }

  await queue.add(
    job.name,
    { endpoint: job.endpoint },
    {
      repeat: { pattern: job.schedule },
      attempts,
      backoff: { type: 'exponential', delay: 5_000 },
      removeOnComplete: { count: 200 },
      removeOnFail: { count: 500 },
    },
  );

  const worker = new Worker(
    queueName,
    async (bullJob) => {
      await executeJob(job.name, bullJob.data.endpoint, timeout);
    },
    {
      connection,
      concurrency: 1,  // overlap prevention
    },
  );

  worker.on('failed', (bullJob, err) => {
    console.error(`❌ [${job.name}] attempt ${bullJob?.attemptsMade}/${attempts} — ${err.message}`);
  });

  worker.on('error', (err) => {
    console.error(`⚠️  [${job.name}] worker error — ${err.message}`);
  });

  workers.push(worker);
}

console.log(`\n🚀 NexFlow BullMQ Scheduler`);
console.log(`📍 API target: ${API_BASE_URL}`);
console.log(`📦 Redis: ${REDIS_HOST}:${REDIS_PORT}`);
console.log(`📋 ${JOBS.length} jobs registered\n`);

for (const job of JOBS) {
  console.log(`  • ${job.name.padEnd(25)} ${job.schedule.padEnd(25)} timeout=${(job.timeout ?? 60000) / 1000}s`);
}

// ---------------------------------------------------------------------------
// Bull Board UI
// ---------------------------------------------------------------------------

const boardApp = Fastify({ logger: false });
const serverAdapter = new FastifyAdapter();

createBullBoard({
  queues: queues.map((q) => new BullMQAdapter(q)),
  serverAdapter,
});

serverAdapter.setBasePath('/');
await boardApp.register(serverAdapter.registerPlugin(), { prefix: '/' });

await boardApp.listen({ port: BOARD_PORT, host: '0.0.0.0' });
console.log(`\n📊 Bull Board UI: http://localhost:${BOARD_PORT}`);

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------

async function shutdown(signal: string) {
  console.log(`\n🛑 ${signal} received — shutting down`);
  await Promise.all(workers.map((w) => w.close()));
  await Promise.all(queues.map((q) => q.close()));
  await boardApp.close();
  process.exit(0);
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
