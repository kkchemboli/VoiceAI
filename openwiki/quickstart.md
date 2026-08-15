---
type: Guide
title: Quickstart
description: Get started with the LiveKit AI Voice Agent repository. Learn how to set up, configure, run, and test the agent locally or in production.
tags: [quickstart, setup, installation, deployment]
---

# LiveKit AI Voice Agent Quickstart

This guide helps you get the LiveKit AI Voice Agent up and running quickly. The agent is a human-like AI voice agent designed to converse with prospects over the phone, using LiveKit for real-time communication, Sarvam AI for text-to-speech, Groq for speech-to-text and LLM, and Telegram for call summaries.

## 📋 Table of Contents
- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Setup & Installation](#setup--installation)
- [Configuration](#configuration)
- [Running the Agent](#running-the-agent)
- [Testing Locally](#testing-locally)
- [Production Deployment](#production-deployment)
- [Telephony Integration (Vobiz/SIP)](#telephony-integration-vobizsip)
- [Next Steps](#next-steps)

## Overview

This repository contains the code for an AI voice agent that:
- Answers inbound phone calls via SIP trunk integration (e.g., Vobiz, Twilio, Telnyx)
- Engages in natural conversations using state-of-the-art LLMs and TTS
- Books appointments or demos through calendar integration
- Sends call summaries to a Telegram chat upon call completion
- Provides a frontend dashboard for monitoring call logs and agent performance

The agent uses:
- **LiveKit** for real-time audio streaming and SIP integration
- **Sarvam AI** for highly natural Indian-language text-to-speech
- **Groq** for fast speech-to-text and LLM inference (Llama-3)
- **Telegram Bot API** for sending call summaries
- **LiveKit Cloud** for SIP trunking and dispatch rules

## Prerequisites

Before you begin, ensure you have:
- **Python 3.9, 3.10, or 3.11**
- A [LiveKit Cloud](https://cloud.livekit.io/) account (for SIP trunking and room management)
- A [Sarvam AI](https://sarvam.ai/) API key (for text-to-speech)
- A [Groq](https://groq.com/) API key (for Llama-3 and speech-to-text)
- A [Telegram Bot Token](https://core.telegram.org/bots#6-botfather) and Chat ID (for call summaries)
- (Optional) A SIP trunk provider like [Vobiz](https://vobiz.com/) for PSTN connectivity

## Setup & Installation

Follow these steps to set up the project locally:

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Create a Python virtual environment**:
   ```bash
   # Windows
   python -m venv venv
   venv\Scripts\activate

   # Mac/Linux
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

## Configuration

The agent requires several environment variables for API keys and configuration. The repository includes a `.env.example` file in the `backend/` directory.

1. Copy the example file and rename it to `.env`:
   ```bash
   cp backend/.env.example backend/.env
   ```

2. Edit `backend/.env` and fill in your credentials:
   ```env
   LIVEKIT_URL=your_livekit_url
   LIVEKIT_API_KEY=your_livekit_api_key
   LIVEKIT_API_SECRET=your_livekit_api_secret
   SARVAM_API_KEY=your_sarvam_api_key
   GROQ_API_KEY=your_groq_api_key
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   ```

   > **⚠️ Security Note**: Never commit your `.env` file to a public repository. Keep it local and secure.

## Running the Agent

You can run the agent in development mode or start it for production use.

### Development Mode

Run the agent in development mode (connects to your LiveKit Cloud instance):
```bash
# Ensure you're in the repository root and your virtual environment is activated
python backend/agent.py dev
```

You should see output indicating the agent is connecting to LiveKit. Once connected, you can test it via the LiveKit Playground.

### Production Deployment

For production (Docker, Coolify, or direct server deployment):
```bash
python backend/agent.py start
```

> **Note**: In production, ensure the following environment variables are set: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. The agent will fail fast if these are missing.

## Testing Locally

Once the agent is running and connected to LiveKit:

1. Open your [LiveKit Cloud Dashboard](https://cloud.livekit.io/).
2. Navigate to the **Playground**.
3. Connect to a room (the agent will automatically join via dispatch rules or you can manually join the agent's room).
4. Speak with the agent using your microphone.
5. When you end the call, you should receive a summary of the conversation in your configured Telegram chat.

## Telephony Integration (Vobiz/SIP)

To connect real phone numbers to the agent via SIP trunking (e.g., using Vobiz):

1. **Obtain SIP Trunk Credentials** from your provider (e.g., Vobiz):
   - SIP Domain
   - Username
   - Password (or IP whitelisting details)

2. **Configure Inbound Trunk in LiveKit Cloud**:
   - Go to LiveKit Cloud Dashboard → **SIP** → **Inbound Trunks**
   - Create a new trunk and input your provider's details
   - Set the external IP or credentials that your provider will use to send calls

3. **Create a Dispatch Rule**:
   - In the LiveKit Cloud SIP dashboard, create a **Dispatch Rule**
   - Map incoming calls from your trunk to a specific room pattern (e.g., `call-room-random`)
   - The agent will automatically join rooms matching this pattern

4. **Test the Flow**:
   - Call your phone number connected to the SIP trunk
   - The call should route to LiveKit, invoke the agent, and begin the conversation

For detailed steps, refer to the [Telephony Configuration section](#telephony-integration-vobizsip) in the backend README.

## Next Steps

After getting the agent running locally, explore the following areas to deepen your understanding:

- [Architecture Overview](/openwiki/architecture/overview.md) - Deep dive into system architecture
- [Source Map](/openwiki/architecture/source-map.md) - Guide to the codebase structure
- [Key Workflows](/openwiki/workflows/) - Detailed walkthroughs of call flows and processes
- [Domain Concepts](/openwiki/domain/) - Core concepts like appointment booking, call handling, etc.
- [Operations & Runbook](/openwiki/operations/) - Deployment, monitoring, and troubleshooting
- [Testing Guidance](/openwiki/testing/) - How to test the agent effectively
- [Integration Points](/openwiki/integrations/) - Integrations with LiveKit, Sarvam, Groq, Telegram, and SIP providers

---
*Ready to dive deeper? Check out the [Architecture Overview](/openwiki/architecture/overview.md) next.*