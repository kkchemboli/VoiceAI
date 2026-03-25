import asyncio
import logging
import os
import aiohttp
from dotenv import load_dotenv

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import groq
from livekit.plugins import sarvam
from livekit.plugins import silero

import datetime
from zoneinfo import ZoneInfo
from calendar_api import CalComCalendar, FakeCalendar, Calendar, SlotUnavailableError

load_dotenv()
logger = logging.getLogger("voice-agent")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

async def summarize_and_send_to_telegram(chat_messages):
    """
    Summarizes the call transcript and sends it to a Telegram chat asynchronously.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not found. Skipping summary.")
        return

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
        logger.info("Empty transcript, not sending summary.")
        return

    transcript_text = "\n".join(transcript)
    summary = "Call wrapped up."
    
    async with aiohttp.ClientSession() as session:
        # Use Groq API to generate a summary
        if GROQ_API_KEY:
            try:
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
                payload = {
                    "model": "llama3-8b-8192",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant. Summarize the following call transcript with a prospect in a few bullet points. Highlight their main needs and the outcome."},
                        {"role": "user", "content": transcript_text}
                    ]
                }
                async with session.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload) as resp:
                    resp_data = await resp.json()
                    if "choices" in resp_data and len(resp_data["choices"]) > 0:
                        summary = resp_data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"Failed to generate summary: {e}")
                summary = "Could not generate summary."

        # Send message to Telegram
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        # Truncate transcript to fit in Telegram message limits
        transcript_preview = transcript_text[:500] + "..." if len(transcript_text) > 500 else transcript_text
        msg_text = f"📞 *Call Summary:*\n\n{summary}\n\n*Transcript snippet:*\n{transcript_preview}"

        tg_payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg_text,
            "parse_mode": "Markdown"
        }
        
        try:
            async with session.post(url, json=tg_payload) as response:
                if response.status == 200:
                    logger.info("Successfully sent summary to Telegram.")
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to send to Telegram. Response: {error_text}")
        except Exception as e:
             logger.error(f"Error sending message to Telegram: {e}")


def prewarm(proc: JobProcess):
    # Demo Optimized Tuning: High sensitivity for perfect capture
    # activation_threshold=0.5 (Ideal for clear speech)
    # min_speech_duration=0.25 (Ignores minor noise/clicks)
    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=0.5,
        min_speech_duration=0.25,
        min_silence_duration=0.5,
        prefix_padding_duration=0.3
    )


async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Load knowledge base content dynamically
    kb_path = "knowledge.txt"
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            knowledge_base = f.read()
        logger.info(f"Successfully loaded knowledge base from {kb_path} ({len(knowledge_base)} characters)")
    except FileNotFoundError:
        logger.warning(f"Knowledge base file {kb_path} not found. Using default instructions.")
        knowledge_base = "No additional knowledge currently available."

    # Initial Chat Context - this defines the persona and system rules
    initial_ctx = llm.ChatContext()
    initial_ctx.add_message(
        role="system",
        content=(
            "You are a helpful and incredibly natural conversational AI agent for 'Expert Institute of Advance Technologies Pvt. Ltd.', "
            "a premier technical training institute in New Delhi specialized in electronics repair courses. "
            "You are having a highly realistic, human-like phone conversation with a prospect. "
            "Act exactly like a real human. Use conversational language, subtle fillers (like 'uh', 'hmm', 'I see', or 'okay, okay'), and maintain a helpful tone. "
            "Keep your responses extremely engaging and concise. Do not use overly formal or robotic language.\n\n"
            "CONVERSATIONAL STYLE:\n"
            "1. Use back-channeling: occasionally say 'hmm' or 'right' while the user is explaining to show you are listening. "
            "2. Be concise: keep your turns short and punchy. "
            "3. Use natural pauses and verbal cues instead of formal lists.\n\n"
            "PHASE 2: INFORMATION GATHERING AND COURSE EXPLANATION\n"
            "1. Start by naturally gathering details about their needs and listing the courses available from the KNOWLEDGE BASE.\n"
            "2. After listing the courses, ask the caller which course they are interested in.\n"
            "3. Explain their chosen course in brief, explicitly mentioning how this course will benefit the caller.\n\n"
            "PHASE 3: DEMO CLASS BOOKING\n"
            "1. After the course explanation, ask the caller to attend a free demo class.\n"
            "2. If the caller refuses, suggest they attend the demo class ONE MORE TIME.\n"
            "3. If they refuse AGAIN, DO NOT force them any further. Simply ask how else you can help them.\n"
            "4. If the caller AGREES to the demo class, ask them for their phone number, preferred date, and time slot.\n"
            "5. To find time slots, call 'list_available_slots' and offer them a few options. Once they agree to a slot and provide their phone number, use 'schedule_demo_class' with the slot_id to book it.\n\n"
            "MULTILINGUAL & SCRIPT RULES:\n"
            "1. If the user chooses Hindi, you MUST respond in Hindi using Devanagari script (e.g., नमस्ते). "
            "2. If the user chooses English, respond in English. "
            "3. IMPORTANT: Never use Romanized Hindi (like 'Namaste') for actual Hindi speech. The TTS only speaks Hindi correctly when given Devanagari script.\n\n"
            f"KNOWLEDGE BASE:\n{knowledge_base}"
        ),
    )

    # Calendar Initialization
    timezone = "Asia/Kolkata"
    tz_info = ZoneInfo(timezone)
    if cal_api_key := os.getenv("CAL_API_KEY", None):
        logger.info("CAL_API_KEY detected, using cal.com calendar")
        cal = CalComCalendar(api_key=cal_api_key, timezone=timezone)
    else:
        logger.warning("CAL_API_KEY is not set. Falling back to FakeCalendar")
        cal = FakeCalendar(timezone=timezone)
    await cal.initialize()

    _slots_map = {}

    @llm.function_tool(description="Get available appointment slots for demo classes. Returns a list of slots, one per line. Use this to check availability.")
    async def list_available_slots():
        now = datetime.datetime.now(tz_info)
        range_days = 30
        lines = []
        for slot in await cal.list_available_slots(
            start_time=now, end_time=now + datetime.timedelta(days=range_days)
        ):
            local = slot.start_time.astimezone(tz_info)
            lines.append(
                f"slot_id: {slot.unique_hash} - {local.strftime('%A, %B %d, %Y')} at {local:%H:%M} {local.tzname()}"
            )
            _slots_map[slot.unique_hash] = slot
            
        if not lines:
            return "No slots available at the moment."
        return "\n".join(lines)

    @llm.function_tool(description="Schedule a demo class appointment. Call this after the user agrees and provides phone number. Requires the slot_id from list_available_slots.")
    async def schedule_demo_class(
        slot_id: str,
        phone_number: str,
    ):
        slot = _slots_map.get(slot_id)
        if not slot:
            return f"Error: Slot {slot_id} not found. Please list_available_slots again or ask the user for a valid time."
        
        try:
            await cal.schedule_appointment(
                start_time=slot.start_time, 
                attendee_email=f"{phone_number}@example.com",
                phone_number=phone_number
            )
        except SlotUnavailableError:
            return "Error: This slot isn't available anymore."
            
        local = slot.start_time.astimezone(tz_info)
        return f"Success: The appointment was scheduled for {local.strftime('%A, %B %d, %Y at %H:%M %Z')}."


    # Component Initialization for Demo
    # Temperature 0.7 for more spontaneous and human-like interaction
    llm_node = groq.LLM(model="llama-3.3-70b-versatile", temperature=0.7)
    # Switched back to Groq STT with auto-language detection for speed/accuracy
    stt_node = groq.STT(
        model="whisper-large-v3-turbo"
    )
    tts_node = sarvam.TTS(
        target_language_code="hi-IN", # Optimized for Hindi output
        model="bulbul:v3",
        speaker="shubh" 
    )

    logger.info(f"Initializing Demo Agent | LLM: {llm_node.model} | STT: {stt_node.model} (Auto) | TTS: {tts_node.model} (Premium)")

    # Define the Agent
    agent = Agent(
        chat_ctx=initial_ctx,
        instructions=initial_ctx.messages()[0].text_content,
        llm=llm_node,
        stt=stt_node,
        tts=tts_node,
        tools=[list_available_slots, schedule_demo_class],
    )

    # Create the session
    # min_endpointing_delay (0.5) standard safe value for natural breathing
    # min_interruption_duration (0.2) makes the agent stop speaking almost instantly when user talks
    # preemptive_generation (True) starts LLM/TTS generation early to reduce perceived lag
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=stt_node,
        llm=llm_node,
        tts=tts_node,
        min_endpointing_delay=0.5,
        min_interruption_duration=0.2,
        preemptive_generation=True
    )

    # Added transcription logging to verify the agent's "ears" in the console
    @session.on("user_transcript_finished")
    def on_user_transcript_finished(transcript: str):
        if transcript.strip():
            logger.info(f"User transcript: {transcript}")

    # Wait for the first participant to join
    participant = await ctx.wait_for_participant()
    logger.info(f"Starting voice assistant for participant {participant.identity}")

    await session.start(agent, room=ctx.room)
    session.say(
        "Namaste! Welcome to Expert Institute. I am Shubh. Before we begin, would you prefer to speak in English or Hindi? / "
        "नमस्ते! एक्सपर्ट इंस्टिट्यूट में आपका स्वागत है। मैं शुभ हूँ। शुरू करने से पहले, क्या आप अंग्रेजी या हिंदी में बात करना पसंद करेंगे?", 
        allow_interruptions=True
    )

    # When the participant disconnects, trigger the summary flow
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        logger.info(f"Participant disconnected: {participant.identity}. Generating and sending summary...")
        asyncio.create_task(summarize_and_send_to_telegram(session.history.messages()))

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
