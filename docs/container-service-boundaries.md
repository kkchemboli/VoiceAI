# Container Service Boundaries

This document defines the runtime boundaries between the containers. It matches
[`docker-compose.yml`](../docker-compose.yml), which is the deployed
arrangement: `api`, `voice-agent`, `celery-worker`, `celery-beat`, `redis`, and
`frontend` each run as their own service, alongside the observability stack.

## Service map

| Service | Primary process | Responsibility | Exposed to | Scaling model |
| --- | --- | --- | --- | --- |
| `api` | Uvicorn/FastAPI | HTTP API, booking/configuration endpoints, request validation, durable record creation | Frontend and approved external clients | Scale horizontally |
| `voice-agent` | LiveKit agent worker | LiveKit sessions, conversation orchestration, OpenAI `gpt-4.1-mini`, call lifecycle | LiveKit Cloud and private application services | Scale by replicas; per-replica capacity is CPU-admission controlled, see below |
| `celery-worker` | Celery worker | Asynchronous jobs, including queued outbound-call processing | Redis and private application services | Scale by queue depth |
| `celery-beat` | Celery beat | Periodic task scheduling | Redis | Exactly one active scheduler |
| `redis` | Redis server | Celery broker, RAG index cache and build lock, runtime-editable config | Private application network only | Managed/single service initially |
| `frontend` | Nginx/static server | Serve compiled frontend assets and route browser requests to the API | Public users | Scale independently or use static hosting |

## Runtime contracts

### `api`

- Owns HTTP request/response handling and API validation.
- Owns creation and updates of durable booking and outbound-call records through Supabase.
- Publishes asynchronous work to Celery instead of running long-lived outbound calls in API background tasks.
- Does not start LiveKit workers, Celery workers, Celery beat, or Redis.
- Listens on port `8000` inside the private service network.
- Provides liveness and readiness endpoints.
- Is the only writer of knowledge content. The local-file upload and delete
  routes were removed, so every knowledge edit goes through
  `POST /api/knowledge/{lang}` and lands in Supabase.

### `voice-agent`

- Owns the LiveKit worker lifecycle and active voice sessions.
- Uses `OPENAI_API_KEY` and `OPENAI_LLM_MODEL`, with the target model set to `gpt-4.1-mini`.
- Owns LiveKit, speech, call-transfer, and conversation-timeout behavior.
- Does not serve browser HTTP traffic.
- Does not run Celery or a Redis server locally, but it **depends on** the `redis`
  service for the RAG index cache and build lock, and must not start without it.
- Capacity is set by `VOICE_NUM_IDLE_PROCESSES` and `VOICE_LOAD_THRESHOLD`. There
  is no maximum-concurrent-calls setting in `livekit-agents` 1.5.x: the idle
  process count is a warm pool that the server will grow past on demand, and
  `load_threshold` is the CPU load at which it stops admitting calls. Raising
  replicas is how capacity is added; raising the idle count pre-warms.
- Reads application configuration from the approved configuration source and emits call-correlated telemetry.

#### Knowledge retrieval

- `supabase.knowledge_base` is the only knowledge store. There is no local file
  mirror, and no upload or delete endpoint: a local copy was readable by the
  agent, so an admin's save could be silently ignored in favour of a stale file.
- Each call re-reads the rows and compares a fingerprint of their contents. The
  index is rebuilt only when that fingerprint changes, so there is no polling and
  no TTL expiry, and a save takes effect on the next call in every process.
- The fingerprint and lock are coordination, not correctness: if Redis is empty
  or unreachable, processes simply rebuild instead of sharing, which costs OpenAI
  embedding calls but never serves the wrong index.

#### How documents are split, and why it used to matter

Chunking is one recursive separator hierarchy — `"\n\n"`, `"\n"`, `". "`, `"? "`,
`"! "`, `"; "`, `", "`, `" "`, then characters as the last resort — with a soft
100-character minimum, a hard 500-character ceiling, and 100 characters of
overlap snapped to a line start (falling back to a word start) so a chunk never
opens mid-word.

This replaced two code paths. The old version had a regex for numbered sections
plus a `Q:` special case, and a blank-line fallback for everything else. **Only
the first path respected the size limit.** An ordinary bullet list fell through to
the fallback and came back as fragments — for the live 16413-character knowledge
base, 350 chunks, the shortest 2 characters, with single headings and lone
bullets embedded and retrieved as though they were answers. Nothing raised an
error; only the chunk count was wrong, which is why it survived.

Measured on the live content after the change:

| | before | after |
|---|---|---|
| chunks | 350 | 110 |
| cold build | 6.74 s | 1.89 s |
| chunks under the 100-char minimum | many | 0 |
| chunk length range | — | 104–492 |

A side effect worth naming: `what are the working hours?` now returns *no*
result, scoring 0.288 against the 0.30 threshold. That is correct rather than a
regression — the knowledge base contains no occurrence of "hours" at all, and the
old fragmentary index "answered" it with the bare heading `Working
Professionals`. Real questions do retrieve sensibly: address 0.541, fees 0.439,
appointment 0.402.

Short pieces are merged into a neighbour rather than dropped. The previous
`len(c) > 20` filter deleted them from the index outright, which lost content
silently. A document shorter than the minimum is returned whole, since there is
nothing to merge it with and shredding it would only produce smaller fragments.

### `celery-worker`

- Owns asynchronous task execution.
- Consumes tasks from Redis using `REDIS_URL`.
- Processes queued outbound-call work and updates its durable status.
- Must make task execution idempotent so retries do not create duplicate calls.
- Does not accept public HTTP traffic.

#### Two limitations found by reading the live data

Both were confirmed against the real project on 2026-09-27 and are recorded
rather than fixed, because both change production behaviour and each needs a
decision rather than a patch. Neither is a regression: the stuck-call handling
is doing what it was designed to do.

**A stuck call pins the alert forever, and the alert's value goes blind.**
`voice_calls_inflight_oldest_seconds` is a `max` over the age of every row in
`calling`, with no per-row labels. One row from 2026-04-25 was still `calling`
after roughly 155 days, which held the gauge at around 1.34e7 seconds and
`VoiceCallsStuckInFlight` continuously firing. A *new* stuck call is not
concealed — the alert tests a threshold, so it still fires — but the value
does not move, so the alert text cannot tell "one row from April" apart from "a
call died a minute ago". It is a boolean with no cardinality behind it, and an
alert that never clears is an alert people stop reading. The fix is a `call_id`
label plus a count of rows over threshold. Left unfixed deliberately: it changes
metric cardinality and the alert expression.

#### The gauge was also silently dead, and that is now fixed

Worth separating from the limitation above, because the two are unrelated and
the second was hiding the first. The observable-gauge callback imported
`opentelemetry.metrics.Measurement`, which no longer exists in the SDK this repo
pins (`1.39.1`). The callback is invoked lazily by the SDK, so the resulting
`ImportError` was raised inside the collection loop and swallowed. The effect
was that **every gauge disappeared while the counter kept working** —
`voice_calls_inflight_total` and `voice_calls_inflight_oldest_seconds` exported
nothing, so no alert could evaluate, and the logs showed no error.

The callback now returns `[Observation(value)]`, which is the supported
observable-gauge contract. All four `voice_calls_*` series were confirmed present
in Prometheus from the running worker, and `VoiceCallsStuckInFlight` was
observed moving to `firing` with `health=ok`. `VoiceCallsInFlightNotObserved`
stays `pending` for the first ~15 minutes after a fix like this, by design: its
expression is `voice_calls_inflight_total > 0 unless on() (voice_calls_inflight_total
offset 15m)`, so with no 15-minute-old sample yet there genuinely is nothing to
compare against. It clears once that history exists. That is the alert warming
up, not a second fault.

Note that age is measured from `created_at` because there is no `started_at` or
`updated_at` column on `outbound_calls` to measure from. That makes the reported
age an upper bound, which errs toward alerting early rather than late — the right
direction — but it also means the number overstates how long the call has
actually been dialling.

**`call_id` is not persisted, so a call cannot be reconstructed from the
database.** `outbound_calls` holds `id, phone_number, status, error_message,
created_at`; `call_logs` holds `id, created_at, phone_number, customer_name,
transcript, summary, duration, status, metadata`. Neither has a `call_id` —
`call_logs.call_id` returns `42703 column does not exist` — and the two share no
join key at all. The only common column is `phone_number`, which is not unique
over time. `call_logs` also records conversation outcomes only (`completed`,
`booked`) and never dial outcomes, so a call that was dialled but never connected
leaves no `call_logs` row; the stuck row above is precisely that case. This means
the complete-trace guarantee cannot be audited from the data of record. Whether
`call_id` actually survives through LiveKit job metadata into Langfuse was **not**
traced — the honest claim is only that the correlation is not verifiable from
persisted rows.

Separately, one `failed` row from 2026-07-25 stores a raw, unparsed Postgres
error in `error_message` (`code 555, "JSON could not be generated"`, a PostgREST
serialisation failure). Worth noting because the field is read as a human-readable
reason and this is machine noise, but it is a single historical row and nothing
currently writes errors in that shape.

### `celery-beat`

- Owns only periodic schedule dispatch.
- Uses the same Celery application configuration as the worker.
- Must run as a singleton unless a distributed scheduler is introduced. Extra
  replicas are wasteful rather than corrupting: claiming an outbound call is a
  single atomic `update ... where id = ? and status = 'pending'` set, so a second
  scheduler finds the row already `calling` and skips it. That compare-and-swap
  is what makes the singleton rule safe to relax, and it is why no separate
  "dispatched" marker exists.
- Does not execute long-running application work itself.

### `redis`

- Provides the Celery broker, plus cache, lock, and configuration state that is
  all rebuildable from a source of truth.
- None of that state is time-expiring. The RAG cache has no TTL on purpose: it is
  keyed by content fingerprint, so it is invalidated by the knowledge changing
  rather than by the clock, and it self-prunes to the current fingerprint. Adding
  an expiry would reintroduce the periodic surprise rebuild this design removed.
- Is reachable only from the private application network.
- Is not the source of truth for bookings, call history, knowledge content, or
  configuration. Its keys are safe to flush: `rag:index:*` and `rag:build:*` are
  caches rebuilt on demand, and `config:google_sheet_url` is a copy of a value an
  operator can re-set. Flushing all of them costs embedding calls, not data.
- Flush is **not** safe for broker state: the Celery queue lives here too.
- Runs with `cap_drop: ALL` plus `cap_add: [SETUID, SETGID]`, and the pair is
  load-bearing. The `redis:7-alpine` entrypoint starts as root and drops to the
  `redis` user with `setpriv` before exec'ing the server; without those two
  capabilities the drop fails and the container exits 127, crash-looping on
  restart. Do not remove `cap_add` as redundant against `cap_drop: ALL`. The
  server holds none of them once running: it is PID 1 as user `redis` with
  `CapEff` of 0, so its effective capability set is empty, and
  `no-new-privileges` keeps it from regaining what it dropped.
- Uses a managed Redis service in production where available.

### `frontend`

- Serves the compiled Vite application independently of FastAPI.
- Receives the API base URL through `VITE_API_URL` or same-origin routing.
- Does not receive server-side credentials.
- Proxies or routes `/api` traffic to the `api` service through the deployment edge.
- Runs nginx as the unprivileged `nginx` user, not as root. This is load-bearing
  rather than cosmetic: the service sets `cap_drop: ALL` and
  `no-new-privileges`, and an nginx master running as root creates and `chown`s
  its temp directories down to the worker user at startup. With no capabilities
  that `chown` returns `EPERM` and the master aborts before it ever binds, so the
  container crash-looped on every start. nginx only attempts that `chown` when
  the effective uid is 0, so starting unprivileged avoids it — at the cost of
  having to pre-create and `chown` the temp paths at build time, which is what
  `Dockerfile.frontend` now does. Verified: 0 restarts, `8080` published, `/`
  serving the SPA, `/dashboard` falling through to `index.html` for client-side
  routes, and `/api/knowledge/status` returning live Supabase rows through the
  proxy.

Note that only `/api/` is proxied. The API's health routes are `/health/live`
and `/health/ready`, which sit outside that prefix and are answered by Compose
health checks rather than through the browser, so `/api/health/live` correctly
returns FastAPI's own 404 — which is a useful signal that the proxy really is
forwarding to the API rather than falling through to the SPA.

## Network rules

```text
Public users -> frontend/edge -> api
api -> Supabase, Cal.com, Redis
voice-agent -> LiveKit, OpenAI, Supabase, Cal.com, Redis
celery-worker -> Redis, Supabase, LiveKit-related services
celery-beat -> Redis
```

Only the frontend/edge and explicitly approved API routes should be public. Redis, Celery, and the voice-agent process remain private.

## Configuration ownership

| Configuration | Owning service(s) |
| --- | --- |
| `SUPABASE_URL`, `SUPABASE_KEY` | API, voice-agent, celery-worker as required |
| `REDIS_URL` | API publisher, voice-agent, celery-worker, celery-beat |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | voice-agent and call-control integrations |
| `OPENAI_API_KEY`, `OPENAI_LLM_MODEL` | voice-agent |
| `VOICE_NUM_IDLE_PROCESSES`, `VOICE_LOAD_THRESHOLD` | voice-agent |
| `CAL_API_KEY`, `CAL_EVENT_ID` | API and voice-agent where calendar actions are performed |
| `CORS_ALLOWED_ORIGINS` | API |
| `VITE_API_URL` | frontend build/deployment |

`voice-agent` needs `REDIS_URL`: it reads and writes the RAG cache and build
lock. A deployment that omits it degrades to rebuilding the index per process,
which is correct but pays for OpenAI embedding calls on every cold start.

The campaign Sheet URL is deliberately **not** environment-only. `GOOGLE_SHEET_URL`
seeds the initial value, but operators change it at runtime through
`POST /api/config/sheet-url`, which writes `config:google_sheet_url` in Redis so
the change reaches every replica without a restart. If both are set, the
environment variable wins, which is the intended way to pin a value.

Secrets are injected at runtime and are not committed. They are also kept out of
image layers: [`.dockerignore`](../.dockerignore) excludes `.env` files and
`backend/data/` from the build context. [`Dockerfile.backend`](../Dockerfile.backend)
copies the whole backend directory with `COPY backend/ ./`, so without that ignore
file `backend/.env` is baked into a layer where it survives after the file is
deleted from the working tree. Removing that ignore file silently reintroduces
the leak, so it is part of the contract rather than a build optimization.

## Still out of scope

- Database schema changes. Nothing in the repo creates or migrates a table, so
  every column the code reads is treated as pre-existing. The live
  `knowledge_base` table was introspected on 2026-09-27 and is recorded under
  `services.voice-agent.knowledge` in [`SERVICE_CONTRACTS.yaml`](../SERVICE_CONTRACTS.yaml):
  `id`, `language` (unique), `content`, and `updated_at`, all readable, with
  `en` and `hi` rows present.
- `knowledge_base.updated_at` **does exist** and is populated, which corrects an
  earlier claim in this file that it was absent. It is still not selected, for a
  different reason: PostgREST fails an entire query on one unknown column, and
  nothing in the repo creates or migrates the table, so selecting a column the
  repo does not own would turn a future table recreation into a broken
  `/api/knowledge/status` and a broken RAG load. Cache invalidation stays
  derived from a content fingerprint, which cannot disagree with the content it
  describes. Re-run the probe before trusting the snapshot against a new project:
  the schema is recorded, not enforced.
- The knowledge *lifecycle*. Removing the file endpoints made Supabase the only
  writer, and Supabase has no UI for it, so an editor has to use the API. A real
  editing surface is a product decision, not a boundary change.
- Authentication on the API. There is none, and the frontend proxies `/api/`
  unauthenticated on a published port. That is worth fixing, but it is a
  deliberate separate piece of work: adding auth to these routes changes how the
  frontend calls every endpoint, not just the knowledge ones.

## Shared image foundation

The initial image split is represented by [`Dockerfile.backend`](../Dockerfile.backend). It provides one dependency layer and three runtime targets:

```text
backend-base
  +--> api
  +--> voice-agent
  +--> celery
```

The targets intentionally do not install or run Supervisor, Redis, or the frontend. `celery-beat` uses the `celery` target with a scheduler command override in the orchestrator.

The older single-stage [`Dockerfile`](../Dockerfile) is still in the tree but no
service in `docker-compose.yml` references it -- every backend service builds
from `Dockerfile.backend`. It is retained only as a rollback path, so it is not
covered by these boundaries and is not kept in sync.
