# AI Voice Agent

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LiveKit](https://img.shields.io/badge/LiveKit-Agents-00B4D8?logo=livekit&logoColor=white)](https://livekit.io/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

A human-like, bilingual (English / Hinglish) **AI voice agent** for a technical training institute. The agent answers inbound phone calls, runs outbound calling campaigns, explains courses, handles objections, and books **Free Demo Classes** end-to-end over the phone.

The agent's persona is a warm, professional female representative who speaks natural Hinglish, follows a strict scripted conversation flow, and can transfer callers to the human support team when needed.

---

## Features

- **📞 Real phone calls** over SIP via **Vobiz** and **LiveKit Cloud** — both inbound and outbound.
- **🎙 Natural bilingual voice** — **Sarvam AI** for speech-to-text (`saaras:v3`) and text-to-speech (`bulbul:v3`) with automatic language locking between English and Hinglish.
- **🧠 Conversational LLM** — **Groq** (`gpt-oss-120b`) drives the conversation with low latency.
- **📚 RAG knowledge base** — OpenAI embeddings + FAISS index over `knowledge.txt`, `knowledge_hi.txt`, PDFs, and live Google Sheets. The agent only answers from the knowledge context — it never guesses.
- **🗓 Demo-class booking** — live **Cal.com** integration with real-time slot availability, slot confirmation, name/phone collection, and duplicate-booking protection (with a `FakeCalendar` fallback for local testing).
- **🤝 Human call transfer** — LLM-triggered SIP transfer to a support manager (`transfer_functions.py`).
- **🚀 Outbound campaign dialer** — bulk dial from a Google Sheet via **Supabase** queue + **Celery/Redis** cron (respects an 8 AM–8 PM IST calling window).
- **💬 WhatsApp follow-ups** — call summaries to the admin via **Ziper.io** and template messages to the caller via **WABridge**.
- **⏹ Smart autocut** — auto-ends calls after 60s of user inactivity or on the closing phrase.
- **📊 Admin dashboard** — a React + Vite frontend to monitor call logs, bookings, edit agent prompts, manage the knowledge base, and trigger outbound calls.

---

## Architecture

```
                         ┌──────────────────────────────────────────────┐
  Inbound caller         │           LiveKit Cloud / SIP                │
 ───────────▶  Vobiz SIP ──▶  Inbound trunk  ──▶  Room (audio only)     │
                            └──────────────────────┬───────────────────┘
                                                   │
          ┌────────────────────────────────────────▼───────────────────────────┐
          │                          agent.py (worker)                         │
          │   LiveKit Agents session ─────────────────────────────────────────  │
          │      ├─ STT: Sarvam saaras:v3 (Hinglish codemix)                    │
          │      ├─ LLM: Groq gpt-oss-120b  ◀── RAGEngine (FAISS + OpenAI)      │
          │      ├─ TTS: Sarvam bulbul:v3 (speaker "roopa")                     │
          │      └─ Tools: list_available_slots · schedule_demo_class           │
          │              transfer_call (SIP transfer to human)                  │
          └──────┬───────────────┬────────────────┬─────────────────────────────┘
                 │               │                │
        ┌────────▼──────┐ ┌──────▼───────┐ ┌──────▼─────────┐
        │  Cal.com      │ │  Supabase    │ │  WhatsApp      │
        │  bookings     │ │  config · KB  │ │  Ziper / WABridge
        └───────────────┘ │  logs · queue │ └────────────────┘
                          └──────┬────────┘
                                 │
        ┌────────────────────────▼─────────────────────────┐
        │  main.py (FastAPI) ─ serves /api/* + dashboard     │
        │  frontend/dist (React dashboard)                   │
        └────────────────────────────────────────────────────┘

        Outbound campaigns:  Google Sheet ─▶ Celery beat ─▶ queue ─▶ vobiz_outbound.py ─▶ LiveKit dispatch
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Real-time voice | [LiveKit Agents](https://livekit.io/) + LiveKit Cloud (SIP trunks, dispatch) |
| Speech-to-Text | [Sarvam AI](https://sarvam.ai/) `saaras:v3` (codemix Hinglish) |
| Text-to-Speech | [Sarvam AI](https://sarvam.ai/) `bulbul:v3` (speaker `roopa`) |
| LLM | [Groq](https://groq.com/) `openai/gpt-oss-120b` |
| RAG | OpenAI `text-embedding-3-small` + FAISS (`faiss-cpu`) |
| Backend API | FastAPI + Uvicorn |
| Database / Queue store | [Supabase](https://supabase.com/) (Postgres + REST) |
| Calendar | [Cal.com](https://cal.com/) API v2 (fallback: `FakeCalendar`) |
| Job queue / cron | Celery + Redis (outbound campaign window) |
| Telephony | Vobiz SIP trunk → LiveKit SIP participant |
| Frontend | React 18 + Vite + TypeScript |
| Deployment | Docker (multi-stage build) + supervisord |

---

## Project Structure

```
Expert_Ai/
├── Dockerfile                 # Multi-stage build: frontend → Python runtime
├── supervisord.conf           # Runs API, agent, Redis, Celery worker & beat
├── backend/
│   ├── agent.py               # LiveKit agent worker — persona, RAG injection, tools, autocut
│   ├── main.py                # FastAPI app — dashboard/API endpoints, static frontend
│   ├── rag_engine.py          # Chunking, embedding, FAISS index, retrieval
│   ├── transfer_functions.py  # LiveKit SIP call transfer tool
│   ├── calendar_api.py        # Cal.com / FakeCalendar slot & booking abstraction
│   ├── vobiz_outbound.py      # Initiate outbound SIP calls via LiveKit dispatch
│   ├── bulk_dialer.py         # Bulk dialer — loads a Google Sheet into the queue
│   ├── celery_worker.py       # Celery beat — processes outbound queue (IST window)
│   ├── capture_logs.py        # Windows helper to capture agent stdout logs
│   ├── knowledge.txt          # English knowledge base (RAG source of truth)
│   ├── knowledge_hi.txt       # Hindi knowledge base
│   ├── requirements.txt       # Python dependencies
│   ├── .env.example           # Environment variable template
│   └── run_*.ps1              # Windows dev/run helpers
└── frontend/
    ├── src/                   # React dashboard (logs, calendar, agents, KB, outbound)
    ├── index.html
    ├── package.json
    └── vite.config.ts
```

---

## Getting Started

### Prerequisites

- Python **3.12**+
- Node.js **20+** (for the frontend)
- Accounts/keys for: **LiveKit Cloud**, **Sarvam AI**, **Groq**, **OpenAI**, **Supabase**, and optionally **Cal.com**, **Vobiz**, **Ziper.io** / **WABridge**.

### 1. Clone & install the backend

```bash
git clone <repository-url>
cd Expert_Ai

# Python virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r backend/requirements.txt
```

### 2. Configure environment

```bash
cp backend/.env.example backend/.env
```

Then fill in `backend/.env`:

| Variable | Required | Description |
|---|---|---|
| `LIVEKIT_URL` | ✅ | LiveKit Cloud WebSocket URL |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | ✅ | LiveKit Cloud API credentials |
| `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` | for outbound | Vobiz/LiveKit outbound SIP trunk |
| `GROQ_API_KEY` | ✅ | Groq API key (`GROQ_LLM_MODEL` defaults to `openai/gpt-oss-120b`) |
| `SARVAM_API_KEY` | ✅ | Sarvam STT/TTS key |
| `OPENAI_API_KEY` | for RAG | Embeddings for the knowledge base |
| `SUPABASE_URL` / `SUPABASE_KEY` | ✅ | Config, knowledge base, call logs, outbound queue |
| `CAL_API_KEY` / `CAL_EVENT_ID` | for live booking | Cal.com API v2 (falls back to `FakeCalendar`) |
| `DEFAULT_TRANSFER_NUMBER` | for transfers | Number to transfer calls to |
| `GOOGLE_SHEET_URL` | for bulk dialing | Published-as-CSV Google Sheet of leads |
| `ZIPER_ACCESS_TOKEN` / `ZIPER_INSTANCE_ID` | optional | WhatsApp summaries to admin |
| `WABRIDGE_*` | optional | WhatsApp template follow-ups |
| `CORS_ALLOWED_ORIGINS` | optional | Comma-separated frontend origins |

> **⚠️ Never commit `.env`.** It is git-ignored and holds secrets.

### 3. Run the frontend dashboard

```bash
cd frontend
npm install
npm run dev          # Vite dev server on http://localhost:5173
```

### 4. Run the backend API

```bash
cd backend
uvicorn main:app --reload --port 8000
```

### 5. Run the voice agent

```bash
cd backend
python agent.py dev          # connect to your LiveKit Cloud room
```

Then test it in the **LiveKit Playground** (dashboard → Playground → join a room) and talk to the agent. Windows users can also use the provided `run_full_backend.ps1` / `run_dev.ps1` helpers.

---

## Docker Deployment

The repo ships with a multi-stage `Dockerfile` and a `supervisord.conf` that boots the full stack — API, agent, Redis, and Celery worker + beat — in one container.

```bash
docker build -t expert-voice-agent .

# provide your backend/.env
docker run -d --name voice-agent \
  -p 8000:8000 \
  --env-file backend/.env \
  expert-voice-agent
```

On startup, supervisord manages:

| Process | Command |
|---|---|
| `api` | `uvicorn main:app --host 0.0.0.0 --port 8000` |
| `agent` | `python agent.py start` |
| `redis` | `redis-server` |
| `celery_worker` | `celery -A celery_worker.app worker` |
| `celery_beat` | `celery -A celery_worker.app beat` |

---

## How It Works

### Inbound call flow

1. A caller dials the institute's number (Vobiz SIP → LiveKit inbound trunk).
2. LiveKit drops the caller into a room; a dispatch rule wakes up the agent worker.
3. The agent greets, detects English vs. Hindi/Hinglish, and locks the TTS language.
4. The RAG engine retrieves knowledge chunks for each user turn and injects them (or a `[NO KNOWLEDGE FOUND]` fallback that offers a support transfer).
5. If the caller wants a **Free Demo Class**, the agent lists real slots from Cal.com, confirms name + phone (digit by digit), and books via `schedule_demo_class`.
6. On hang-up, a summary is generated, saved to Supabase `call_logs`, and pushed to WhatsApp (admin) + a template message to the caller.

### Outbound campaign flow

1. Publish a lead sheet (columns: `Name`, `Phone`, `Course`) to the web as CSV and set `GOOGLE_SHEET_URL`.
2. `POST /api/outbound/bulk-dial` → `bulk_dialer.py` reads the sheet and inserts pending rows into Supabase `outbound_calls`.
3. Celery beat runs every minute; within the **8 AM – 8 PM IST** window it picks the oldest pending call and dials via `vobiz_outbound.py` (unique room per call, agent dispatched first).
4. The agent uses the **outbound persona**, confirms the prospect's course, and pushes for a Free Demo Class booking.

### Demo-class booking safeguards

- Slots are always read fresh from `list_available_slots`; never invented.
- The chosen slot must exactly match a returned option before booking.
- Phone numbers are confirmed digit-by-digit; name spelling is confirmed.
- Cal.com duplicate-booking (HTTP 409) is surfaced honestly to the caller.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/config` | Fetch inbound/outbound system prompts & greetings |
| `POST` | `/api/config` | Update agent prompts/greetings (Supabase) |
| `GET` | `/api/knowledge?lang=en\|hi` | Fetch knowledge base content |
| `POST` | `/api/knowledge/{lang}` | Update knowledge base (Supabase + local sync) |
| `GET` | `/api/knowledge/status` | List detected knowledge files & sheet URL |
| `POST` | `/api/knowledge/upload` | Upload a PDF/TXT knowledge file |
| `DELETE` | `/api/knowledge/file/{filename}` | Delete a knowledge file |
| `GET` | `/api/logs` | Paginated call logs (`page`, `page_size`) |
| `GET` | `/api/appointments?year=&month=` | Cal.com bookings for a month |
| `POST` | `/api/outbound/call` | Trigger a single outbound call |
| `POST` | `/api/outbound/batch` | Trigger multiple outbound calls |
| `GET` | `/api/outbound/queue` | Recent outbound call statuses |
| `POST` | `/api/outbound/bulk-dial` | Load the Google Sheet campaign queue |
| `POST` | `/api/config/sheet-url` | Persist `GOOGLE_SHEET_URL` to `.env` |

The FastAPI app also serves the built frontend (`frontend/dist`) as a single-page app when present.

---

## Knowledge Base

The agent answers **only** from retrieved knowledge — it is explicitly instructed to never guess fees, courses, batches, or addresses. Update the source of truth in three ways:

1. Edit `backend/knowledge.txt` (English) / `knowledge_hi.txt` (Hindi).
2. Upload PDF/TXT files via the dashboard (`Knowledge Base` view).
3. Set `GOOGLE_SHEET_URL` for a live-updating sheet.

Each update re-hashes the merged corpus and re-embeds chunks, cached to disk so restarts are fast.

