---
type: Source Map
title: Source Map
description: Mapping of key source files and their purposes in the LiveKit AI Voice Agent repository.
tags: [source-map, reference, files]
---
# Source Map

This document maps key source files and directories to their purposes within the LiveKit AI Voice Agent repository.

## Backend (`/backend`)

| File/Directory | Purpose |
|----------------|---------|
| `agent.py` | Main agent worker: handles LiveKit connections, conversation flow, TTS/STT/LLM integrations, function calls (calendar, knowledge, call control), and Telegram summaries. |
| `main.py` | Entrypoint for running the agent in different modes (dev, production). Initializes the worker and handles process management. |
| `calendar_api.py` | Interface for calendar services (Google Calendar, Calendly, etc.) to check availability and book appointments. |
| `calendar_api.py` | Interface for calendar services (Google Calendar, Calendly, etc.) to check availability and book appointments. |
| `rag_engine.py` | Retrieval-Augmented Generation engine that loads knowledge base files and retrieves relevant context for the LLM. |
| `knowledge.txt` | English knowledge base used by the RAG engine. |
| `knowledge_hi.txt` | Hindi knowledge base used by the RAG engine. |
| `transfer_functions.py` | Functions for call transfers, holding, and other call control operations via LiveKit APIs. |
| `vobiz_outbound.py` | Module for handling outbound calls via Vobiz SIP trunk integration. |
| `bulk_dialer.py` | Manages bulk outbound calling campaigns. |
| `celery_worker.py` | Background task worker for asynchronous operations (e.g., sending summaries, logging). |
| `capture_logs.py` | Utility for capturing call audio and transcripts for debugging and quality assurance. |
| `inspect_llm.py` | Utility for inspecting and debugging LLM interactions. |
| `transfer_functions.py` | Contains functions for call transfers, holding, and other call control operations via LiveKit APIs. |
| `vobiz_outbound.py` | Handles outbound calling via Vobiz SIP trunk integration. |
| `bulk_dialer.py` | Manages bulk outbound calling campaigns. |
| `celery_worker.py` | Background task worker for asynchronous operations like sending summaries and logging. |
| `capture_logs.py` | Captures and logs call audio and transcripts for debugging and quality assurance. |
| `inspect_llm.py` | Utility for inspecting and debugging LLM interactions. |
| `requirements.txt` | Python dependencies for the backend. |
| `.env.example` | Example environment variables file for configuration. |
| `README.md` | Backend-specific README with setup, installation, and running instructions. |
| `run_dev.ps1` | PowerShell script for running the backend in development mode (Windows). |
| `run_full_backend.ps1` | PowerShell script for running the full backend with all workers (Windows). |
| `supervisord.conf` | Example Supervisor configuration for process management. |
| `Dockerfile` | Instructions for building a Docker container for the agent. |

## Frontend (`/frontend`)

| File/Directory | Purpose |
|----------------|---------|
| `src/` | Source code for the frontend (React/Vite-based dashboard). |
| `dist/` | Built production assets for the frontend. |
| `index.html` | Entry HTML file for the frontend. |
| `package.json` | npm dependencies and scripts for the frontend. |
| `package-lock.json` | Locked versions of npm dependencies. |
| `tsconfig.json` | TypeScript configuration for the frontend. |
| `tsconfig.node.json` | TypeScript configuration for Node.js scripts in the frontend. |
| `vite.config.ts` | Vite configuration for the frontend development server and build. |

## Configuration

| File | Purpose |
|------|---------|
| `.env.example` | Example environment variables (copy to `.env` and fill in actual values). |
| `supervisord.conf` | Example configuration for managing the agent and worker processes with Supervisor. |
| `Dockerfile` | Instructions for containerizing the application. |

## Documentation

| File | Purpose |
|------|---------|
| `backend/README.md` | Detailed backend setup, installation, and running instructions. |
| `openwiki/INSTRUCTIONS.md` | Instructions for the OpenWiki agent (this file). |
| `openwiki/quickstart.md` | Quickstart guide for the repository's OpenWiki. |
| `openwiki/architecture/overview.md` | High-level architecture overview. |
| `openwiki/workflows/` | Detailed workflow diagrams and explanations. |
| `openwiki/domain/` | Core domain concepts and explanations. |
| `openwiki/operations/` | Operational guides, deployment, monitoring, troubleshooting. |
| `openwiki/testing/` | Testing strategies and guides. |
| `openwiki/integrations/` | Details on integrating with external services. |

## Key Workflows (see `/openwiki/workflows/`)

| Workflow | Description |
|----------|-------------|
| [Appointment Booking](/openwiki/workflows/appointment-booking.md) | Flow for checking availability and booking appointments via calendar integration. |
| [Call Handling](/openwiki/workflows/call-handling.md) | End-to-end flow of handling an inbound or outbound call, from connection to call summary. |
| [Outbound Calling](/openwiki/workflows/outbound-calling.md) | Process for initiating outbound calls via bulk dialer or manual triggers. |
| [Call Summary & Notification](/openwiki/workflows/call-summary.md) | How call summaries are generated and sent via Telegram after a call ends. |

## Key Domain Concepts (see `/openwiki/domain/`)

| Concept | Description |
|---------|-------------|
| [Appointment Booking](/openwiki/domain/appointment-booking.md) | How the agent checks availability, books, and manages appointments. |
| [Call Handling & Conversation Flow](/openwiki/domain/call-handling.md) | Detailed breakdown of the conversation pipeline: STT, LLM, TTS, and function calling. |
| [Knowledge Base & RAG](/openwiki/domain/knowledge-base.md) | How the agent uses retrieval-augmented generation to inform responses. |
| [Multilingual Support (Hindi/English)](/openwiki/domain/multilingual.md) | Support for Hindi and English languages via Sarvam AI and bilingual knowledge bases. |

## Operations (see `/openwiki/operations/`)

| Guide | Description |
|-------|-------------|
| [Deployment](/openwiki/operations/deployment.md) | Instructions for deploying the agent in various environments (local, Docker, production). |
| [Configuration](/openwiki/operations/configuration.md) | Detailed explanation of environment variables and configuration files. |
| [Monitoring & Logging](/openwiki/operations/monitoring.md) | How to monitor the agent's health, performance, and logs. |
| [Troubleshooting](/openwiki/operations/troubleshooting.md) | Common issues and their resolutions. |
| [Backup & Recovery](/openwiki/operations/backup-recovery.md) | Strategies for backing up configuration and data. |

## Testing (see `/openwiki/testing/`)

| Guide | Description |
|-------|-------------|
| [Unit Testing](/openwiki/testing/unit-testing.md) | Guidelines for writing and running unit tests. |
| [Integration Testing](/openwiki/testing/integration-testing.md) | How to test integrations with external services (LiveKit, Groq, Sarvam, etc.). |
| [End-to-End Testing](/openwiki/testing/e2e-testing.md) | Full call flow testing scenarios. |
| [Manual Testing Guide](/openwiki/testing/manual-testing.md) | Step-by-step manual testing procedures for developers and QA. |

## Integrations (see `/openwiki/integrations/`)

| Integration | Description |
|-------------|-------------|
| [LiveKit](/openwiki/integrations/livekit.md) | Real-time audio/video streaming and room management. |
| [Sarvam AI TTS](/openwiki/integrations/sarvam.md) | High-quality text-to-speech for Hindi and English. |
| [Groq STT/LLM](/openwiki/integrations/groq.md) | Fast speech-to-text and large language model inference. |
| [Telegram](/openwiki/integrations/telegram.md) | Sending call summaries via Telegram bot. |
| [Calendar](/openwiki/integrations/calendar.md) | Integrations with Google Calendar, Calendly, etc. for appointment booking. |
| [SIP Trunk Providers](/openwiki/integrations/sip.md) | Connecting to telephony providers like Vobiz, Twilio, or Telnyx via LiveKit SIP trunks. |

---
*This source map is generated as part of the OpenWiki initialization process. For more details, see the [OpenWiki Quickstart](/openwiki/quickstart.md).*