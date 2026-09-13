# Expert Institute AI Voice Agent

An AI-powered outbound calling and inbound telephony platform that lets a human-like voice agent
converse with prospects over the phone for **Expert Institute, New Delhi**. The agent answers or
dials calls in Hinglish, uses RAG-backed knowledge, books appointments via Cal.com, and delivers
call summaries to WhatsApp and Telegram.

The project is split into independently deployable services: a FastAPI control plane, a LiveKit
voice-agent worker, Celery for asynchronous call work, a React admin panel, and a full
OpenTelemetry observability stack.

## Highlights

- **Inbound & outbound calling** over LiveKit rooms, integrated with SIP trunks (Vobiz / Twilio / Telnyx).
- **Durable outbound dialing** — calls are persisted in Supabase, claimed idempotently by Celery workers,
  and dispatched only inside the calling window (08:00–20:00 IST).
- **Campaign dialing from Google Sheets** — one-click sequential dialing of every lead in a sheet.
- **Human-like conversation** — Sarvam AI (STT/TTS), OpenAI LLM, Silero VAD, dynamic language locking, audio stability patches.
- **RAG knowledge engine** — answers grounded in local TXT/PDF files, Google Sheets, and a Supabase knowledge base.
- **Automatic call summaries** — delivered to Telegram, WhatsApp (admin + customer), and stored as call logs.
- **Admin panel** — dashboard, call logs, knowledge base editor, calendar, agent prompt configuration, and outbound campaign controls.
- **Observable by design** — OpenTelemetry traces/logs/metrics exported to Prometheus, Loki, and Jaeger, visualized in Grafana.

## Architecture

| Service | Process | Responsibility |
| --- | --- | --- |
| `api` | Uvicorn/FastAPI | HTTP control plane: call queueing, configuration, knowledge base, call logs, calendar, health endpoints |
| `voice-agent` | LiveKit worker (`agent.py`) | LiveKit voice sessions, conversation orchestration, STT/TTS, booking & transfer tools, RAG |
| `celery-worker` | Celery worker | Asynchronous work: campaign lead import, outbound call execution (via LiveKit SIP) |
| `celery-beat` | Celery beat | Periodic dispatcher that enqueues pending calls inside the calling window |
| `redis` | Redis 7 | Celery broker, short-lived coordination state, distributed bulk-import lock |
| `frontend` | Nginx + static build | React/Vite admin panel; proxies `/api` to the `api` service |
| `otel-collector` | OpenTelemetry collector | Receives OTLP, fans out to Prometheus, Loki, and Jaeger |
| `prometheus` / `loki` / `jaeger` | Metrics, logs, traces stores | Backends for the dashboards |
| `grafana` | Dashboards | Visualizes metrics, logs, and traces |

Runtime boundaries, contracts, and configuration ownership are documented in
[`docs/container-service-boundaries.md`](docs/container-service-boundaries.md) and
[`SERVICE_CONTRACTS.yaml`](SERVICE_CONTRACTS.yaml).

## Tech Stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, Pydantic, Celery, Redis
- **Voice:** LiveKit Agents + LiveKit API, Sarvam AI (STT/TTS), OpenAI (LLM + embeddings), Silero VAD, Groq
- **Data:** Supabase (durable records: calls, config, knowledge), Google Sheets (leads)
- **Calendar:** Cal.com (booking availability + appointments)
- **Search:** faiss-cpu + a custom RAG engine
- **Frontend:** React 18, TypeScript, Vite, lucide-react, pdfjs-dist
- **Observability:** OpenTelemetry SDK/OTLP, Prometheus, Loki, Jaeger, Grafana
- **Deployment:** Docker Compose, multi-stage Dockerfiles, git

## Repository Layout

```
.
├── backend/
│   ├── main.py                 # FastAPI control-plane application
│   ├── agent.py                # LiveKit voice-agent worker (entrypoint)
│   ├── core/                   # Config, observability, agent, router, autocut, state, patches
│   ├── prompts/                # System prompts & greetings
│   ├── rag/                    # RAG engine + intents
│   ├── services/               # Celery worker, bulk dialer, Vobiz outbound, calendar, Supabase, WhatsApp, runtime config
│   ├── tools/                  # Calendar tools, call transfer functions
│   ├── utils/                  # Phone normalizers, distributed lock, safe paths
│   └── README.md               # Standalone voice-agent docs
├── frontend/                   # React + Vite admin panel
├── docs/                       # Design notes (not part of implementation)
├── grafana/                    # Dashboards + provisioning
├── Dockerfile                  # Legacy all-in-one image (rollback path)
├── Dockerfile.backend          # Split multi-target backend image (api / voice-agent / celery-worker / celery-beat)
├── Dockerfile.frontend         # Independent frontend image
├── docker-compose.yml          # Full service stack
├── docker-compose.redis.yml    # Redis-only stack
├── supervisord.conf            # Legacy all-in-one process manager
├── SERVICE_CONTRACTS.yaml      # Service-level contracts
└── otel-collector-config.yaml  # OTLP -> Prometheus / Loki / Jaeger
```

## Prerequisites

- Python 3.9–3.12 (backend), Node 20 (frontend)
- A [LiveKit Cloud](https://livekit.io) account (rooms, SIP, agent dispatch)
- [Supabase](https://supabase.com) project (call logs, config, knowledge base)
- API keys: OpenAI, Sarvam AI, and optionally Groq
- Cal.com account (booking) and a Google Sheet (campaign leads)
- Docker + Docker Compose for containerized deployment

## Environment Configuration

Copy `backend/.env.example` to `backend/.env` and fill in the values:

| Variable | Purpose |
| --- | --- |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit Cloud connection + SIP |
| `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` / `LIVEKIT_SIP_ROOM` | Outbound SIP trunk and room prefix |
| `OPENAI_API_KEY` / `OPENAI_LLM_MODEL` | Conversation LLM + RAG embeddings (default `gpt-4.1-mini`) |
| `SARVAM_API_KEY` | Sarvam AI STT/TTS |
| `GROQ_API_KEY` / `GROQ_LLM_MODEL` | Groq STT/LLM (optional) |
| `SUPABASE_URL` / `SUPABASE_KEY` | Durable storage and agent configuration |
| `CAL_API_KEY` / `CAL_EVENT_ID` | Cal.com booking |
| `GOOGLE_SHEET_URL` | Campaign lead sheet (runtime-configurable from the admin panel) |
| `DEFAULT_TRANSFER_NUMBER` | Number used for warm transfers / admin WhatsApp summary |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Call summary reports |
| `REDIS_URL` | Celery broker (default `redis://localhost:6379/0`) |
| `CORS_ALLOWED_ORIGINS` | Comma-separated browser origins allowed to call the API |

Secrets are injected at runtime only; they are never baked into images or committed.

## Local Development

### Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# API control plane
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Voice agent (connects to your LiveKit Cloud room)
python agent.py dev

# Celery worker + beat (for outbound campaign queue)
celery -A services.celery_worker.app worker --loglevel=info
celery -A services.celery_worker.app beat --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to localhost:8000
```

## Docker Deployment

Run the full stack (API, voice agent, Celery, Redis, frontend, and the observability stack):

```bash
cp backend/.env.example .env   # then fill in real values
docker compose up -d --build
```

If you only need Redis (Celery broker) locally:

```bash
docker compose -f docker-compose.redis.yml up -d
```

### Service targets

The backend image (`Dockerfile.backend`) exposes multiple runtime targets:

```bash
docker build -f Dockerfile.backend --target api -t expert-inst-api .
docker build -f Dockerfile.backend --target voice-agent -t expert-inst-voice-agent .
docker build -f Dockerfile.backend --target celery-worker -t expert-inst-celery-worker .
docker build -f Dockerfile.backend --target celery-beat -t expert-inst-celery-beat .
```

The legacy all-in-one image (`Dockerfile` + `supervisord.conf`) is kept available for rollback
until the service migration is complete.

## Outbound Campaign Flow

1. In the admin panel, save a public **Google Sheets** URL (or set `GOOGLE_SHEET_URL`).
2. Click **Start Massive Sequential Dialing** → the API enqueues `import_campaign_leads`.
3. A distributed Redis lock prevents concurrent imports; each valid row is inserted into
   Supabase as a `pending` call and a dial task is enqueued per lead.
4. `celery-beat` runs every minute and dispatches pending calls **only between 08:00–20:00 IST**.
5. `process_outbound_call` claims the record (pending → calling), dials the number via LiveKit SIP
   + Vobiz, and the `voice-agent` worker runs the conversation in the room.
6. On completion the record is set to `success`/`failed` and surfaced in the admin queue view.

Calls can also be triggered manually (`/api/outbound/call`) or in batches (`/api/outbound/batch`).

## Telephony Integration

The agent relies purely on LiveKit audio rooms, so it works with any SIP carrier (Vobiz, Twilio,
Telnyx). Configure an inbound trunk + dispatch rule on LiveKit Cloud to route incoming PSTN calls
into the agent's room; outbound calls are dialed through `LIVEKIT_SIP_OUTBOUND_TRUNK_ID`. See
`backend/README.md` for a step-by-step Vobiz setup guide.

## Observability

All services emit OTLP to the `otel-collector`, which fans out logs to **Loki**, metrics to
**Prometheus**, and traces to **Jaeger**, all visualized in **Grafana** (auto-provisioned):

- FastAPI: metrics (`/metrics`), request spans, request/trace ID headers
- Voice agent: call-correlated spans and metrics
- Celery: task spans and metrics (started count, sample counters)

## API Overview

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/health/live`, `/health/ready` | Liveness / readiness probes |
| `POST` | `/api/outbound/call` | Queue a single outbound call |
| `POST` | `/api/outbound/batch` | Queue multiple outbound calls |
| `POST` | `/api/outbound/bulk-dial` | Start a Google Sheets campaign import |
| `GET`  | `/api/outbound/queue` | Outbound call queue status |
| `GET/POST` | `/api/config` | Read/update agent prompts and greetings |
| `POST` | `/api/config/sheet-url` | Save the campaign Google Sheet URL at runtime |
| `GET/POST` | `/api/knowledge`, `/api/knowledge/{lang}` | Read/update knowledge base |
| `GET`  | `/api/knowledge/status` | List knowledge sources |
| `POST` | `/api/knowledge/upload` | Upload a TXT/PDF knowledge file |
| `DELETE` | `/api/knowledge/file/{filename}` | Remove a knowledge file |
| `GET`  | `/api/logs` | Paginated call logs |
| `GET`  | `/api/appointments` | Confirmed appointments from Cal.com |

## Related Documentation

- [`backend/README.md`](backend/README.md) — voice agent setup, LiveKit playground, Vobiz SIP walkthrough
- [`docs/container-service-boundaries.md`](docs/container-service-boundaries.md) — service boundaries and runtime contracts
- [`SERVICE_CONTRACTS.yaml`](SERVICE_CONTRACTS.yaml) — machine-readable API/Celery contracts