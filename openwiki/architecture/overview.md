---
type: Architecture Overview
title: Architecture Overview
description: High-level architecture of the LiveKit AI Voice Agent, including components, data flow, and integration points.
tags: [architecture, overview, architecture-diagram]
---
# Architecture Overview

The LiveKit AI Voice Agent is a real-time voice agent built on the LiveKit platform, integrating Sarvam AI for text-to-speech, Groq for speech-to-text and LLM, and Telegram for call summaries. The system is designed to handle inbound and outbound calls, manage appointments, and provide a conversational experience.

## High-Level Architecture

```mermaid
graph TD
    %% External Systems
    subgraph External Systems
        SIP[SIP Trunk (Vobiz/Twilio/Telnyx)]
        Telegram[Telegram Bot]
        LiveKitCloud[LiveKit Cloud]
        Sarvam[Sarvam AI TTS]
        Groq[Groq API (STT + LLM)]
        Calendar[Calendar API (Google/Calendly)]
    end

    %% Core Agent Components
    subgraph Agent System[Agent System (Backend)]
        Agent[Agent Worker (agent.py)]
        Main[Main Entrypoint (main.py)]
        CalendarAPI[Calendar API (calendar_api.py)]
        RAG[RAG Engine (rag_engine.py)]
        Knowledge[Knowledge Base (knowledge.txt, knowledge_hi.txt)]
        Transfer[Transfer Functions (transfer_functions.py)]
        VobizOutbound[Vobiz Outbound (vobiz_outbound.py)]
        BulkDialer[Bulk Dialer (bulk_dialer.py)]
        Celery[Celery Worker (celery_worker.py)]
        CaptureLogs[Capture Logs (capture_logs.py)]
    end

    %% Frontend
    subgraph Frontend[Frontend (Optional Dashboard)]
        FrontendApp[Frontend App (frontend/src)]
    end

    %% Connections
    SIP -->|SIP -> LiveKit Room| LiveKitCloud
    LiveKitCloud -->|Media Stream| Agent
    Agent -->|TTS Request| Sarvam
    Sarvam -->|Audio Stream| Agent
    Agent -->|Transcription Request| Groq
    Groq -->|Text Response| Agent
    Agent -->|Function Call| CalendarAPI
    CalendarAPI -->|API Calls| Calendar
    Agent -->|Message Send| Telegram
    Telegram -->|Bot API| Telegram
    Agent -->|Knowledge Lookup| Knowledge
    Agent -->|RAG Query| RAG
    Agent -->|Outbound Dial| VobizOutbound
    Agent -->|Bulk Dial| BulkDialer
    Agent -->|Background Tasks| Celery
    Agent -->|Debug Logging| CaptureLogs
    Main --> Agent
    Main --> Celery
    FrontendApp -->|API Calls| Agent
```

## Core Components

### 1. Agent Worker (`backend/agent.py`)
The main agent worker that connects to LiveKit, handles voice interactions, orchestrates the conversation flow, and integrates with external services (TTS, STT/LLM, calendar, Telegram).

**Key Responsibilities:**
- Connect to LiveKit room via worker
- Handle incoming/outgoing call events
- Manage conversation state and session handling
- Interface with Sarvam AI for text-to-speech
- Interface with Groq for speech-to-text and LLM reasoning
- Execute function calls for calendar operations, knowledge lookup, and call control
- Send call summaries via Telegram
- Manage outbound dialing and bulk dialing workflows

### 2. Main Entrypoint (`backend/main.py`)
Entry point for running the agent in different modes (development, production). Handles worker initialization, environment configuration, and process management.

### 3. Calendar API (`backend/calendar_api.py`)
Handles integration with calendar services (Google Calendar, Calendly, etc.) for checking availability, booking appointments, and managing schedules.

### 4. RAG Engine (`backend/rag_engine.py`)
Retrieval-Augmented Generation engine that loads knowledge base files (`knowledge.txt`, `knowledge_hi.txt`) and retrieves relevant information to augment the LLM's responses.

### 5. Knowledge Base
- `backend/knowledge.txt`: English knowledge base
- `backend/knowledge_hi.txt`: Hindi knowledge base
Used by the RAG engine to provide contextual information during conversations.

### 6. Transfer Functions (`backend/transfer_functions.py`)
Contains functions for call transfers, holding, and other call control operations via LiveKit APIs.

### 7. Vobiz Outbound (`backend/vobiz_outbound.py`)
Handles outbound calling via Vobiz SIP trunk integration.

### 8. Bulk Dialer (`backend/bulk_dialer.py`)
Manages bulk outbound calling campaigns.

### 9. Celery Worker (`backend/celery_worker.py`)
Background task worker for asynchronous operations like sending summaries, logging, and other non-blocking tasks.

### 10. Capture Logs (`backend/capture_logs.py`)
Utility for capturing and logging call audio and transcripts for debugging and quality assurance.

### 11. Frontend (`frontend/`)
Optional React/Vite-based frontend dashboard for monitoring and managing the agent (optional component).

## Data Flow

### Inbound Call Flow
1. Incoming call arrives via SIP trunk -> LiveKit Cloud
2. LiveKit routes call to agent worker via dispatch rule
3. Agent worker connects to LiveKit room and begins media exchange
4. User speaks -> Audio stream sent to Groq for speech-to-text
5. Groq returns text transcription
6. Agent processes transcript:
   - Retrieves relevant knowledge from RAG engine
   - Determines intent and extracts entities (e.g., appointment request)
   - If booking intent: calls Calendar API to check availability/book slot
   - Generates response using Groq LLM
   - Sends response text to Sarvam AI for text-to-speech conversion
   - Sarvam returns audio stream played back to user
7. On call end: Agent generates call summary and sends via Telegram bot

### Outbound Call Flow
1. Triggered via bulk dialer or manual trigger
2. Agent initiates outbound call via Vobiz outbound module
3. Call connects via SIP -> LiveKit -> Agent worker
4. Follows same conversational flow as inbound call
5. Post-call summary sent via Telegram

## Key Integrations

| Service | Purpose | Integration Method |
|---------|---------|-------------------|
| **LiveKit** | Real-time audio/video streaming | Worker SDK, Room connection |
| **Sarvam AI** | High-quality text-to-speech (Hindi/English) | REST API |
| **Groq** | Fast speech-to-text and LLM inference | REST API (Whisper STT, Llama3 LLM) |
| **Telegram** | Call summary notifications | Bot API |
| **Calendar Services** | Appointment booking/availability | REST APIs (Google Calendar, Calendly, etc.) |
| **SIP Trunks (Vobiz/Twilio)** | Telephony connectivity | LiveKit SIP Ingestion/Outbound Trunks |

## Deployment Options

### Development Mode
```bash
python agent.py dev
```
- Connects to local development environment
- Uses local configuration for testing

### Production Mode
```bash
python agent.py start
```
- Designed for Docker/Kubernetes deployment
- Requires essential environment variables: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

### Docker Deployment
See `Dockerfile` for containerized deployment instructions.

### Process Management (Supervisor)
See `supervisord.conf` for example process management configuration.

## Key Configuration (Environment Variables)

| Variable | Description | Required |
|----------|-------------|----------|
| `LIVEKIT_URL` | LiveKit WebSocket URL | Yes (production) |
| `LIVEKIT_API_KEY` | LiveKit API Key | Yes (production) |
| `LIVEKIT_API_SECRET` | LiveKit API Secret | Yes (production) |
| `SARVAM_API_KEY` | Sarvam AI API Key | Yes |
| `GROQ_API_KEY` | Groq API Key | Yes |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | Yes (for summaries) |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID for summaries | Yes |
| `GROQ_LLM_MODEL` | Groq LLM model to use (default: llama3-70b-8192) | No |
| `GROQ_STT_MODEL` | Groq STT model to use (default: whisper-large-v3) | No |

See `.env.example` for complete reference.

## Key Workflows

See [Workflows](/openwiki/workflows/) for detailed flow diagrams:
- [Appointment Booking Workflow](/openwiki/workflows/appointment-booking.md)
- [Call Handling Workflow](/openwiki/workflows/call-handling.md)
- [Outbound Calling Workflow](/openwiki/workflows/outbound-calling.md)
- [Call Summary & Notification Workflow](/openwiki/workflows/call-summary.md)

## Key Domain Concepts

See [Domain Concepts](/openwiki/domain/) for:
- [Appointment Booking](/openwiki/domain/appointment-booking.md)
- [Call Handling & Conversation Flow](/openwiki/domain/call-handling.md)
- [Knowledge Base & RAG](/openwiki/domain/knowledge-base.md)
- [Multilingual Support (Hindi/English)](/openwiki/domain/multilingual.md)

## Operations & Runbook

See [Operations](/openwiki/operations/) for:
- [Deployment Guide](/openwiki/operations/deployment.md)
- [Environment Configuration](/openwiki/operations/configuration.md)
- [Monitoring & Logging](/openwiki/operations/monitoring.md)
- [Troubleshooting Guide](/openwiki/operations/troubleshooting.md)
- [Backup & Recovery](/openwiki/operations/backup-recovery.md)

## Testing Guidance

See [Testing](/openwiki/testing/) for:
- [Unit Testing](/openwiki/testing/unit-testing.md)
- [Integration Testing](/openwiki/testing/integration-testing.md)
- [End-to-End Testing](/openwiki/testing/e2e-testing.md)
- [Manual Testing Guide](/openwiki/testing/manual-testing.md)

## Integration Points

See [Integrations](/openwiki/integrations/) for:
- [LiveKit Integration](/openwiki/integrations/livekit.md)
- [Sarvam AI TTS Integration](/openwiki/integrations/sarvam.md)
- [Groq STT/LLM Integration](/openwiki/integrations/groq.md)
- [Telegram Integration](/openwiki/integrations/telegram.md)
- [Calendar Integration](/openwiki/integrations/calendar.md)
- [SIP Trunk Providers (Vobiz, Twilio, Telnyx)](/openwiki/integrations/sip.md)

---
*Next: Read the [Workflows Overview](/openwiki/workflows/) to understand key processes in detail.*