# LiveKit AI Voice Agent

A human-like AI voice agent designed to converse with prospects over the phone. This agent uses:
- **LiveKit** as the real-time agent framework and hosting platform.
- **Sarvam AI** for highly natural text-to-speech (TTS) voices.
- **Groq** for Speech-to-Text (STT) and conversational LLM capabilities.
- **Telegram** to receive a summarized report of the call once it is complete.

## Requirements
- Python 3.9, 3.10, or 3.11
- LiveKit Cloud account (for hosting the room/SIP integration)
- Sarvam AI API Key
- Groq API Key
- Telegram Bot Token & Chat ID

---

## 🚀 Setup & Installation

### 1. Clone & Environment setup
First, create a virtual environment to isolate the dependencies to avoid conflicts:

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies
Install all required LiveKit plugins and AI libraries via pip:

```bash
pip install -r requirements.txt
```

### 3. Configure Credentials (Do NOT expose these secrets)
The repository includes a `.env` file that handles sensitive credentials. 
Keep this file local and do **not** commit it to public repositories. 

Fill out the `.env` file with the following variables:
*   `LIVEKIT_URL`: Your LiveKit WebSocket URL (from LiveKit Cloud dashboard).
*   `LIVEKIT_API_KEY`: Your LiveKit API Key.
*   `LIVEKIT_API_SECRET`: Your LiveKit API Secret.
*   `SARVAM_API_KEY`: API Key for Sarvam TTS.
*   `GROQ_API_KEY`: API Key for Llama-3 and Groq Speech-to-text.
*   `TELEGRAM_BOT_TOKEN`: The token given by BotFather on Telegram.
*   `TELEGRAM_CHAT_ID`: The numeric Chat ID where the bot will send call summaries.

---

## 🏃‍♂ Running the Agent

You can run the agent in development mode (which connects locally to your LiveKit cloud instance).

```bash
python agent.py dev
```

For production deployments (Docker/Coolify), run:

```bash
python agent.py start
```

> Required env vars in production: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
> If any of these are missing, the worker will fail fast at startup with a clear error.

Once the agent shows `Connected to LiveKit`, you can test it directly via the **LiveKit Playground**:
1. Go to your LiveKit Cloud Dashboard.
2. Open the **Playground**.
3. Connect into the room. 
4. The AI Agent will greet you, and you can converse with it using your microphone.
5. When you disconnect, you'll instantly receive a summary of the conversation in your Telegram chat!

---

## 📞 Telephony Configuration (Vobiz integration)

This voice agent is structured perfectly to integrate with actual SIP phone numbers provided by telecom carriers like **Vobiz**, Twilio, or Telnyx. Since the AI agent relies purely on LiveKit's unified audio rooms, you only need to configure SIP routing on LiveKit Cloud.

### How to map a Vobiz Phone Number to this Agent:

1. **Obtain SIP Trunk from Vobiz:**
   From your Vobiz dashboard, configure a SIP trunk and get your SIP Domain, Username, and Password (or IP Whitelist based authentication).

2. **Configure Inbound SIP in LiveKit Cloud:**
   - Go to your LiveKit Cloud Dashboard.
   - Navigate to the **SIP** section on the left sidebar.
   - Create a new **Inbound Trunk**.
   - Input the telephony numbers provided by Vobiz.
   - Set the external IP or credentials that Vobiz will use to send calls to LiveKit.

3. **Set a Dispatch Rule:**
   - In the LiveKit Cloud SIP dashboard, create a **Dispatch Rule**.
   - Tell LiveKit to route incoming calls from the newly created trunk to a specific room pattern (for example: `call-room-random`).

4. **Agent Dispatch Configuration:**
   - Keep your `python agent.py start` process running on your server.
   - As soon as a real user dials your Vobiz number, Vobiz routes it to LiveKit -> LiveKit drops them anonymously as an audio participant into the room -> The Python agent detects the participant and starts having a human-like conversation.

Enjoy your fully automated human-like prospect agent! 🤖✨
