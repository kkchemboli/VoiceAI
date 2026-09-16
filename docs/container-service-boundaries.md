# Container Service Boundaries

This document defines the target runtime boundaries for the container refactor. It is a design contract; it does not change the current all-in-one deployment.

## Service map

| Service | Primary process | Responsibility | Exposed to | Scaling model |
| --- | --- | --- | --- | --- |
| `api` | Uvicorn/FastAPI | HTTP API, booking/configuration endpoints, request validation, durable record creation | Frontend and approved external clients | Scale horizontally |
| `voice-agent` | LiveKit agent worker | LiveKit sessions, conversation orchestration, OpenAI `gpt-4.1-mini`, call lifecycle | LiveKit Cloud and private application services | Scale by concurrent calls |
| `celery-worker` | Celery worker | Asynchronous jobs, including queued outbound-call processing | Redis and private application services | Scale by queue depth |
| `celery-beat` | Celery beat | Periodic task scheduling | Redis | Exactly one active scheduler |
| `redis` | Redis server | Celery broker and short-lived coordination/cache state | Private application network only | Managed/single service initially |
| `frontend` | Nginx/static server | Serve compiled frontend assets and route browser requests to the API | Public users | Scale independently or use static hosting |

## Runtime contracts

### `api`

- Owns HTTP request/response handling and API validation.
- Owns creation and updates of durable booking and outbound-call records through Supabase.
- Publishes asynchronous work to Celery instead of running long-lived outbound calls in API background tasks.
- Does not start LiveKit workers, Celery workers, Celery beat, or Redis.
- Listens on port `8000` inside the private service network.
- Provides liveness and readiness endpoints.

### `voice-agent`

- Owns the LiveKit worker lifecycle and active voice sessions.
- Uses `OPENAI_API_KEY` and `OPENAI_LLM_MODEL`, with the target model set to `gpt-4.1-mini`.
- Owns LiveKit, speech, call-transfer, and conversation-timeout behavior.
- Does not serve browser HTTP traffic.
- Does not run Celery or Redis locally.
- Reads application configuration from the approved configuration source and emits call-correlated telemetry.

### `celery-worker`

- Owns asynchronous task execution.
- Consumes tasks from Redis using `REDIS_URL`.
- Processes queued outbound-call work and updates its durable status.
- Must make task execution idempotent so retries do not create duplicate calls.
- Does not accept public HTTP traffic.

### `celery-beat`

- Owns only periodic schedule dispatch.
- Uses the same Celery application configuration as the worker.
- Must run as a singleton unless a distributed scheduler is introduced.
- Does not execute long-running application work itself.

### `redis`

- Provides the Celery broker and explicitly short-lived cache/coordination state.
- Is reachable only from the private application network.
- Is not the source of truth for bookings, call history, configuration, or uploaded files.
- Uses a managed Redis service in production where available.

### `frontend`

- Serves the compiled Vite application independently of FastAPI.
- Receives the API base URL through `VITE_API_URL` or same-origin routing.
- Does not receive server-side credentials.
- Proxies or routes `/api` traffic to the `api` service through the deployment edge.

## Network rules

```text
Public users -> frontend/edge -> api
api -> Supabase, Cal.com, Redis
voice-agent -> LiveKit, OpenAI, Supabase, Cal.com
celery-worker -> Redis, Supabase, LiveKit-related services
celery-beat -> Redis
```

Only the frontend/edge and explicitly approved API routes should be public. Redis, Celery, and the voice-agent process remain private.

## Configuration ownership

| Configuration | Owning service(s) |
| --- | --- |
| `SUPABASE_URL`, `SUPABASE_KEY` | API, voice-agent, celery-worker as required |
| `REDIS_URL` | API publisher, celery-worker, celery-beat |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | voice-agent and call-control integrations |
| `OPENAI_API_KEY`, `OPENAI_LLM_MODEL` | voice-agent |
| `CAL_API_KEY`, `CAL_EVENT_ID` | API and voice-agent where calendar actions are performed |
| `CORS_ALLOWED_ORIGINS` | API |
| `VITE_API_URL` | frontend build/deployment |

Secrets are injected at runtime. They are not copied into images or committed to the repository.

## Explicitly out of scope for this boundary step

- RAG/index generation and storage redesign
- Database schema changes
- Logging implementation
- Dockerfile or Compose changes
- API behavior changes
- Deployment migration

Those changes should follow after this service contract is accepted.

## Shared image foundation

The initial image split is represented by [`Dockerfile.backend`](../Dockerfile.backend). It provides one dependency layer and three runtime targets:

```text
backend-base
  +--> api
  +--> voice-agent
  +--> celery
```

The targets intentionally do not install or run Supervisor, Redis, or the frontend. `celery-beat` uses the `celery` target with a scheduler command override in the orchestrator. The existing [`Dockerfile`](../Dockerfile) remains the rollback image until the later orchestration steps are complete.
