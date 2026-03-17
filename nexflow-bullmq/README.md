# NexFlow BullMQ Scheduler

Replaces the `node-cron` scheduler with BullMQ repeatable jobs backed by Redis.

## What it does

- **Overlap prevention**: Each job queue has `concurrency: 1` — a new tick won't fire if the previous is still running.
- **Retry with backoff**: Failed jobs retry with exponential backoff (5s, 10s, 20s...).
- **Per-job timeouts**: commerce-indexer gets 120s, coordinator gets 180s, etc.
- **Deduplication**: BullMQ repeatable jobs won't stack duplicate triggers.
- **Bull Board UI**: Visual dashboard at `http://localhost:3003` showing all queues, job states, and failure logs.

## Prerequisites

- Redis running on localhost:6379 (already running on Mac Mini)
- Node.js >= 20

## Setup

```bash
cd nexflow-bullmq
npm install
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CRON_SECRET` | Yes | — | Auth secret for cron endpoints |
| `SCHEDULER_API_URL` | No | `http://localhost:3002` | Fastify API base URL |
| `REDIS_HOST` | No | `127.0.0.1` | Redis host |
| `REDIS_PORT` | No | `6379` | Redis port |
| `BOARD_PORT` | No | `3003` | Bull Board UI port |

## Run

```bash
# Development (watch mode)
npm run dev

# Production via PM2
pm2 start npx --name nexflow-bullmq-scheduler -- tsx queue-scheduler.ts
```

## Migration from node-cron

1. Start the BullMQ scheduler alongside the old one
2. Verify jobs are executing via Bull Board UI
3. Stop the old `nexflow-scheduler` PM2 process
4. Update `SCHEDULER_BASE_URL` is no longer needed

## Jobs

All 19 jobs migrated 1:1 from `scheduler/index.ts`. Target is now Fastify (port 3002)
instead of Next.js (port 3000).
