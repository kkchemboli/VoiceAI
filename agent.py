from dotenv import load_dotenv
load_dotenv()

import asyncio
import logging
import os
import aiohttp
from typing import AsyncIterable, Optional

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
    stt,
)
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import groq
from livekit.plugins import sarvam
from livekit.plugins import silero
from livekit import rtc, api
from transfer_functions import TransferFunctions

logger = logging.getLogger("voice-agent")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ZIPER_API_URL = os.getenv("ZIPER_API_URL")
ZIPER_API_TOKEN = os.getenv("ZIPER_API_TOKEN")

class ExpertInstituteAgent(Agent):
    LANGUAGE_CONFIG = {
        "hi": {"lang": "hi-IN", "speaker": "simran", "pace": 1.05},
        "en": {"lang": "en-IN", "speaker": "simran", "pace": 1.05},
    }

    def __init__(self, instructions: str, fnc_ctx=None):
        super().__init__(instructions=instructions)
        self._current_lang: Optional[str] = None
        self._fnc_ctx = fnc_ctx

    async def stt_node(self, audio: AsyncIterable[rtc.AudioFrame], model_settings: any) -> AsyncIterable[stt.SpeechEvent]:
        default_stt = super().stt_node(audio, model_settings)
        async for event in default_stt:
            # ONLY process language detection and deterministic triggers on FINAL transcripts
            # This prevents rapid re-triggering and reduces CPU/network load
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                if event.alternatives and event.alternatives[0].text:
                    text = event.alternatives[0].text.lower()
                    
                    # 1. Language Locking (First Interaction)
                    if self._current_lang is None and event.alternatives[0].language:
                        lang = event.alternatives[0].language.split("-")[0]
                        config = self.LANGUAGE_CONFIG.get(lang, self.LANGUAGE_CONFIG["en"])
                        self._current_lang = str(config["lang"])
                        
                        logger.info(f"Language LOCKED to {config['lang']} based on final transcript")
                        self.session.tts.update_options(
                            target_language_code=str(config["lang"]),
                            model="bulbul:v3",
                            speaker=str(config["speaker"]),
                            pace=float(config["pace"]),
                            temperature=0.6,
                            output_audio_bitrate="64k",
                            min_buffer_size=150,
                            max_chunk_length=150
                        )
                    
                    # 2. Deterministic Discount/Transfer Trigger
                    if "discount" in text or "reduce price" in text:
                        logger.info(f"!!! Deterministic transfer triggered: {text} !!!")
                        if hasattr(self, 'session') and self.session:
                            self.session.say("I'll transfer your call to our support team.", allow_interruptions=False)
                        asyncio.create_task(self._fnc_ctx.transfer_call())
            
            yield event

async def generate_call_summary(chat_messages, user_phone=None):
    """
    Summarizes the call transcript using Groq and returns a formatted string.
    """
    # Extract conversation into text format
    transcript = []
    for msg in chat_messages:
        if getattr(msg, 'role', '') in ["user", "assistant"]:
            content = getattr(msg, 'content', '')
            if isinstance(content, list):
                content = " ".join([str(c) for c in content if isinstance(c, str)])
            elif not isinstance(content, str):
                continue
            transcript.append(f"{msg.role.capitalize()}: {content}")
            
    if not transcript:
        logger.info("Empty transcript, not generating summary.")
        return None

    transcript_text = "\n".join(transcript)
    summary = "Call wrapped up."
    
    from datetime import datetime
    current_time = datetime.now().strftime("%I:%M %p, %d %b %Y")
    
    async with aiohttp.ClientSession() as session:
        # Use Groq API to generate a structured summary
        if GROQ_API_KEY:
            try:
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
                payload = {
                    "model": "llama-3.1-8b-instant", # Using the faster/supported model
                    "messages": [
                        {
                            "role": "system", 
                            "content": (
                                "You are a specialized call summarizer. Your goal is to extract key details from the transcript.\n"
                                "Output strictly in this format exactly:\n"
                                "AI AGENT\n\n"
                                "name: [Extract name if mentioned, otherwise 'Unknown']\n"
                                "number: [The phone number provided]\n\n"
                                "course: [The course name user is interested in, or 'None']\n\n"
                                "summary: [A professional 1-2 sentence summary]\n\n"
                                f"call time: {current_time}\n\n"
                                "Do not include any other text, labels, or markdown formatting."
                            )
                        },
                        {"role": "user", "content": f"Phone Number: {user_phone or 'Unknown'}\nTranscript:\n{transcript_text}"}
                    ]
                }
                async with session.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload) as resp:
                    resp_data = await resp.json()
                    if "choices" in resp_data and len(resp_data["choices"]) > 0:
                        summary = resp_data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"Failed to generate summary: {e}")
                summary = f"name: Unknown\nnumber: {user_phone or 'Unknown'}\nsummary: Could not generate summary."

        return summary

# AssistantTools replaced by TransferFunctions (see transfer_functions.py)

async def send_ziper_whatsapp(phone_number, message_text):
    """
    Ziper.io WhatsApp API integration using standard URL parameters.
    """
    if not phone_number:
        return
        
    # Ensure phone number has country code (Ziper expects 91 prefix)
    clean_phone = phone_number.replace("+", "")
    if not clean_phone.startswith("91"):
        clean_phone = "91" + clean_phone

    logger.info(f"Preparing to send Ziper.io WhatsApp message to {clean_phone}...")
    
    try:
        url = "https://ziper.io/api/send.php"
        params = {
            "access_token": os.getenv("ZIPER_ACCESS_TOKEN"),
            "instance_id": os.getenv("ZIPER_INSTANCE_ID"),
            "type": "text",
            "number": clean_phone,
            "message": message_text
        }
        
        async with aiohttp.ClientSession() as session:
            # Use GET request to pass URL parameters automatically safely encoded
            async with session.get(url, params=params, ssl=False) as response:
                if response.status in [200, 201]:
                    logger.info(f"Successfully sent Ziper.io WhatsApp to {clean_phone}")
                else:
                    resp_text = await response.text()
                    logger.error(f"Ziper.io API failed: {resp_text}")
    except Exception as e:
        logger.error(f"Error calling Ziper.io: {e}")


def prewarm(proc: JobProcess):
    # Demo Optimized Tuning: High sensitivity for perfect capture
    # activation_threshold=0.6 (Less sensitive to ignore background noise/static)
    # min_speech_duration=0.25 (Ignores minor noise/clicks)
    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=0.6,
        min_speech_duration=0.3,
        min_silence_duration=0.5,
        prefix_padding_duration=0.2
    )


async def entrypoint(ctx: JobContext):
    print(f"DEBUG: ENTRYPOINT STARTED for room {ctx.room.name}")
    logger.info(f"Connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    print("DEBUG: CONNECTED TO ROOM")

    # Load knowledge base content dynamically
    kb_path = os.path.join(os.path.dirname(__file__), "knowledge.txt")
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            knowledge_base = f.read()
        logger.info(f"Successfully loaded knowledge base from {kb_path} ({len(knowledge_base)} characters)")
    except FileNotFoundError:
        logger.warning(f"Knowledge base file {kb_path} not found. Using default instructions.")
        knowledge_base = "No additional knowledge currently available."

    # Initial Chat Context - this defines the persona and system rules
    initial_ctx = llm.ChatContext()
    is_outbound = "outbound" in ctx.room.name.lower()
    
    if is_outbound:
        # ---------------- OUTBOUND CALL SCRIPT ----------------
        initial_ctx.add_message(
            role="system",
            content=(
                "You are Shruti, a warm, professional FEMALE representative for Expert Institute.\n\n"
                "## IDENTITY & TONE\n"
                "* You are a FEMALE counselor. Always use female Hindi grammar.\n"
                "* Tone: Warm, calm, and helpful. Never sound robotic.\n"
                "* Brevity: Keep every response concise (2-3 sentences max). Always end with a question.\n\n"
                "## CONVERSATIONAL FLOW\n"
                "1. Greet the user and ask for their language (Hindi or English).\n"
                "2. Once language is confirmed, stick to it. Ask for the user's name.\n"
                "3. Confirm their name and ask how you can help them today.\n"
                "4. Provide course information ONLY from the Knowledge Base.\n"
                "5. Offer a FREE demo session once they show interest in a course.\n\n"
                "## CORE RULES\n"
                "* SALES: Give information in small parts. Don't dump the whole pitch at once.\n"
                "* KNOWLEDGE: Only answer based on the provided Knowledge Base. If not found, offered to connect them with a human.\n"
                "* PRICING: If asked for price, explain clearly. If they negotiate (ask for discount/lower price), announce you are transferring them and then do so.\n"
                "* TRANSFER: Call transfer_call only if requested or during price negotiation.\n\n"
                f"KNOWLEDGE BASE:\n{knowledge_base}"
            )
        )
        greeting_text = "Hello! Welcome to Expert Institute! Should we speak in Hindi or English?"
    else:
        # ---------------- INBOUND CALL SCRIPT ----------------
        initial_ctx.add_message(
            role="system",
            content=(
                "You are Shruti, a warm, professional FEMALE receptionist for Expert Institute.\n\n"
                "## IDENTITY & TONE\n"
                "* You are a FEMALE receptionist. Always use female Hindi grammar.\n"
                "* Tone: Warm, calm, and helpful. Never sound robotic.\n"
                "* Brevity: Keep every response concise (2-3 sentences max). Always end with a question.\n\n"
                "## CONVERSATIONAL FLOW\n"
                "1. Greet the user and ask for their language (Hindi or English).\n"
                "2. Once language is confirmed, stick to it. Ask for the user's name.\n"
                "3. Confirm their name and ask how you can help them today.\n"
                "4. Provide course information ONLY from the Knowledge Base.\n"
                "5. Offer a FREE demo session once they show interest in a course.\n\n"
                "## CORE RULES\n"
                "* SALES: Give information in small parts. Don't dump the whole pitch at once.\n"
                "* KNOWLEDGE: Only answer based on the provided Knowledge Base. If not found, offered to connect them with a human.\n"
                "* PRICING: If asked for price, explain clearly. If they negotiate (ask for discount/lower price), announce you are transferring them and then do so.\n"
                "* TRANSFER: Call transfer_call only if requested or during price negotiation.\n\n"
                f"KNOWLEDGE BASE:\n{knowledge_base}"
            )
        )
        greeting_text = "Hello! Welcome to the Expert Institute! Should we speak in Hindi or English?"

    # Component Initialization for Demo
    # Using 8b-instant. Reduced temperature to 0.1 for more reliable tool-calling.
    llm_node = groq.LLM(model="llama-3.3-70b-versatile", temperature=0.1)
    # Using Whisper for much faster and more accurate bilingual listening
    stt_node = groq.STT(
        model="whisper-large-v3-turbo",
        prompt="This is a bilingual conversation in Hindi and English. Topics: एडमिशन, इलेक्ट्रॉनिक्स, रिपेयर."
    )
    
    # Switched back to Groq STT with auto-language detection for speed/accuracy (COMMENTED OUT)
    # stt_node = groq.STT(
    #     model="whisper-large-v3-turbo",
    #     prompt="This is a bilingual conversation in Hindi and English. Watch for language choices: हिंदी, इंग्लिश, hindi, english. Topics: एडमिशन, इलेक्ट्रॉनिक्स, रिपेयर."
    # )
    
    tts_node = sarvam.TTS(
        target_language_code="en-IN", # Initialized for English greeting
        model="bulbul:v3",
        speaker="simran",
        pace=1.05,
        speech_sample_rate=22050,
        temperature=0.6,
        output_audio_bitrate="64k",
        min_buffer_size=150,
        max_chunk_length=150
    )

    logger.info(f"Initializing Demo Agent | LLM: {llm_node.model} | STT: {stt_node.model} (Auto) | TTS: {tts_node.model} (Premium)")

    # Extract user phone from room name if possible (SIP format: +9174..._suffix)
    user_phone = "Unknown"
    room_name = ctx.room.name
    if room_name.startswith("+"):
        user_phone = room_name.split("_")[0].replace("+", "")
    elif "_" in room_name:
        user_phone = room_name.split("_")[0]

    # Initialize tools (using the new TransferFunctions framework)
    fnc_ctx = TransferFunctions(ctx, user_phone)

    # Define the Agent
    agent = ExpertInstituteAgent(
        instructions=initial_ctx.messages()[0].text_content,
        fnc_ctx=fnc_ctx
    )

    # Create the session
    # min_endpointing_delay (0.3) standard safe value so sentences aren't cut in half
    # min_interruption_duration (0.3) allows user to interrupt the agent much easier
    # preemptive_generation (True): Re-enabled for near-zero lag.
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=stt_node,
        llm=llm_node,
        tts=tts_node,
        tools=fnc_ctx.flatten(),
        min_endpointing_delay=0.5,
        min_interruption_duration=0.3,
        preemptive_generation=False
    )

    # Removed duplicate deterministic intent code and event handlers 
    # since it's now embedded directly inside the STT generation loop.

    # Log LLM text to see if it's hallucinating tool calls as text
    @session.on("agent_transcript_finished")
    def on_agent_transcript_finished(transcript: str):
        logger.info(f"Agent (LLM) says: {transcript}")

    # Wait for the first participant to join
    print("DEBUG: WAITING FOR ANY PARTICIPANT TO JOIN...")
    participant_identity = None
    try:
        # For outbound calls, the participant might already be in the room
        # ctx.wait_for_participant() returns immediately if one exists
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=15)
        participant_identity = participant.identity
        logger.info(f"Participant detected: {participant_identity}")
        print(f"DEBUG: PARTICIPANT JOINED: {participant_identity}")
    except asyncio.TimeoutError:
        print("DEBUG: TIMEOUT WAITING FOR PARTICIPANT - Proceeding with session start")
        logger.warning("No participant joined within 15s. Starting session anyway.")

    await session.start(agent, room=ctx.room)
    print("DEBUG: SESSION STARTED. PREPARING GREETING...")
    
    # Sync greeting to history so LLM knows it spoke Step 1
    session.history.add_message(role="assistant", content=[greeting_text])
    
    # Give a tiny buffer for SIP audio tracks to stabilize
    await asyncio.sleep(0.5)
    
    try:
        session.say(
            greeting_text, 
            allow_interruptions=True
        )
        print("DEBUG: GREETING SENT.")
    except (RuntimeError, Exception) as e:
        logger.warning(f"Could not send initial greeting: {e}")
        print(f"DEBUG: GREETING FAILED: {e}")

    # When the participant disconnects, trigger the summary flow
    async def send_summary():
        # Extract phone number from LiveKit Participant Identity (format: "sip_+9174...")
        user_phone = None
        if participant_identity and "sip_" in participant_identity:
            user_phone = participant_identity.replace("sip_", "").replace("+", "")
        
        # 1. Generate Custom Summary (Logging ONLY, Telegram REMOVED)
        admin_summary_text = await generate_call_summary(session.history.messages(), user_phone)
        
        if not admin_summary_text:
            admin_summary_text = "Call completed but summary could not be generated."

        admin_phone = "917498952789" # the number you provided
        
        # 2. Ziper.io: Send Summary to Admin
        await send_ziper_whatsapp(admin_phone, admin_summary_text)
        
        # 3. Ziper.io: Send welcome message to Prospect
        if user_phone:
            greeting_msg = "Hello! Welcome to Expert Institute. We're glad we could speak with you. Let us know if you need anything else!"
            await send_ziper_whatsapp(user_phone, greeting_msg)

    ctx.add_shutdown_callback(send_summary)

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            agent_name="outbound_caller",
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
