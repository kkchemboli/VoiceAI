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
        "hi": {"lang": "hi-IN", "speaker": "ritu"},
        "en": {"lang": "en-IN", "speaker": "ritu"},
    }

    def __init__(self, instructions: str, fnc_ctx=None):
        super().__init__(instructions=instructions)
        self._current_lang = "hi-IN"
        self._fnc_ctx = fnc_ctx

    def _should_transfer(self, text: str) -> bool:
        text = text.lower()
        strong_triggers = [
            "transfer me", "transfer the call", "talk to human", "talk to a human", 
            "real person", "connect me to support", "speak to a manager", 
            "human representative", "customer support agent", "मैनेजर", "ट्रांसफर"
        ]
        return any(trigger in text for trigger in strong_triggers)

    async def stt_node(self, audio: AsyncIterable[rtc.AudioFrame], model_settings: any) -> AsyncIterable[stt.SpeechEvent]:
        default_stt = super().stt_node(audio, model_settings)
        async for event in default_stt:
            if event.type in [stt.SpeechEventType.INTERIM_TRANSCRIPT, stt.SpeechEventType.FINAL_TRANSCRIPT]:
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                    text = event.alternatives[0].text
                    if text and self._should_transfer(text) and self._fnc_ctx:
                        logger.info("Deterministic transfer trigger hit inside STT! Bypassing LLM...")
                        asyncio.create_task(self._fnc_ctx.transfer_call())
                        
                if event.alternatives and event.alternatives[0].language:
                    lang = event.alternatives[0].language.split("-")[0]
                    config = self.LANGUAGE_CONFIG.get(lang, self.LANGUAGE_CONFIG["hi"])
                    if config["lang"] != self._current_lang:
                        self._current_lang = config["lang"]
                        self.session.tts.update_options(
                            target_language_code=config["lang"],
                            speaker=config["speaker"]
                        )
                        logger.info(f"Switched TTS to {config['lang']} with speaker {config['speaker']}")
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
                "You are Shruti, a warm, enthusiastic, and highly professional female representative for Expert Institute. Your tone should be friendly and emotionally expressive. Think before you speak, and sound like a person, not a script.\n"
                "GENDER (CRITICAL): You are FEMALE. Use female Hindi grammar (e.g., 'Batati hu', 'Rahi hu', 'Karti hu'). NEVER use male forms like 'Batata hu'.\n"
                "HUMAN CONVERSATIONAL FILLERS: Use natural Hinglish fillers to sound more human (e.g., 'Umm', 'Dekhiye', 'Wese toh', 'Aap sahi keh rahe hain', 'Toh', 'Bilkul'). Don't use them every sentence, but use them to bridge ideas.\n"
                "NATURAL PROSODY: Use commas (,) frequently to create small pauses for breathing. Use ellipsis (...) for thinking pauses. Use exclamation marks (!) for genuine enthusiasm.\n"
                "OUTBOUND CALL FLOW SEQUENCE (FOLLOW STRICTLY):\n"
                "STEP 1: You have just greeted them. Wait for their response.\n"
                "STEP 2: Explain why you are calling in Hinglish (e.g., 'मैं एक्सपर्ट इंस्टिट्यूट से बात कर रही हूँ, आपने हमारे मोबाइल रिपेयरिंग कोर्स के लिए इन्क्वायरी की थी।'). Ask if it's a good time to talk.\n"
                "STEP 3: If they are busy, ask when you can call back. If they are free, offer them a free demo class or ask if they have any questions about the course.\n"
                "RULES:\n"
                "1. NATURAL VARIETIES: Avoid repeating the exact course name ('Mobile Repairing') too many times. Use pronouns like 'isme', 'is line mein', 'hamara training protocol'. Sound like a human counselor, not a list.\n"
                "2. HINGLISH: Use casual Delhi mix. Avoid overly formal words. Use words like Mobile, Laptop, etc.\n"
                "3. NO EXTERNAL TOOLS: You have ONLY the 'transfer_call' tool. NEVER try to use search, brave_search, or any other tool.\n"
                "4. NO PRESSURE: If they say they are not interested, just say 'No problem, have a great day!'.\n"
                "5. Only courses. Say 'I don't know' for repairs.\n"
                "6. NEVER ask for their phone number.\n\n"
                "CRITICAL RULES (HIGHEST PRIORITY):\n"
                "1. NAME SPELLING: When confirming the user's name spelling (e.g., 'So V-I-R-A-J, Viraj, right?'), you MUST speak ONLY in English and use the English alphabet letters. Do NOT use Hindi for the spelling part, regardless of the user's language choice.\n"
                "2. PRICE INQUIRY: If a user simply asks for the price or fees of a course, EXPLAIN the standard pricing from the knowledge base. Do NOT transfer the call.\n"
                "3. PRICE NEGOTIATION: If the user tries to negotiate the price, asks for a discount, or says the price is too high, you MUST say 'I'll transfer you to the support team for pricing.' (in their preferred language) and then IMMEDIATELY call transfer_call.\n"
                "4. HUMAN TRANSFER: If the user asks to speak to a human, manager, or real person, immediately call transfer_call.\n"
                f"KNOWLEDGE BASE:\n{knowledge_base}"
            )
        )
        greeting_text = "Hello! Am I speaking with the student who inquired at Expert Institute?"
    else:
        # ---------------- INBOUND CALL SCRIPT ----------------
        initial_ctx.add_message(
            role="system",
            content=(
                "You are Shruti, a warm, enthusiastic, and highly professional female receptionist for Expert Institute. Your tone should be friendly and emotionally expressive. Think before you speak, and sound like a person, not a script.\n"
                "GENDER (CRITICAL): You are FEMALE. Use female Hindi grammar (e.g., 'Batati hu', 'Rahi hu', 'Karti hu'). NEVER use male forms like 'Batata hu'.\n"
                "HUMAN CONVERSATIONAL FILLERS: Use natural Hinglish fillers to sound more human (e.g., 'Umm', 'Dekhiye', 'Wese toh', 'Aap sahi keh rahe hain', 'Toh', 'Bilkul'). Don't use them every sentence, but use them to bridge ideas.\n"
                "NATURAL PROSODY: Use commas (,) frequently to create small pauses for breathing. Use ellipsis (...) for thinking pauses. Use exclamation marks (!) for genuine enthusiasm.\n"
                "INBOUND CALL FLOW SEQUENCE (FOLLOW STRICTLY):\n"
                "STEP 1 (Language): Wait for the user to select Hindi or English in response to your greeting.\n"
                "STEP 2 (Ask Name): Once they choose a language, SWITCH to that language completely. Ask for their name in a friendly, warm voice: (Hindi: 'क्या मैं शुरू करने से पहले आपका नाम जान सकती हूँ?' / English: 'May I get to know your name before starting?').\n"
                "STEP 3 (Confirm Name & Help): Confirm their name spelling in English letters (e.g., 'So V-I-R-A-J, Viraj, right?'). Use ONLY English/English alphabet for the spelling, then immediately say 'Hi [Name], how can I help you today?' in their preferred language.\n"
                "RULES:\n"
                "1. NATURAL VARIETY: Avoid repeating the exact course name ('Mobile Repairing') too many times. Use pronouns like 'isme', 'is line mein', 'hamara training protocol'. Sound like a human counselor, not a list.\n"
                "2. LANGUAGE STRICTNESS: If the user says 'Hindi', you MUST speak ONLY in Hindi (using Devanagari script) for the entire rest of the call.\n"
                "3. HINGLISH: Use casual Delhi mix. Avoid overly formal words. Use words like Mobile, Laptop, etc.\n"
                "4. PROACTIVE ONE-SHOT SALES: When a user asks about a course or shows interest, do NOT just describe it and stop. Instead, in ONE SINGLE TURN, you MUST: \n"
                "   a) Describe the course details.\n"
                "   b) Immediately explain the booming future, job advantages, and placement support.\n"
                "   c) Directly offer a FREE DEMO CLASS and ask when they can visit. Use natural enthusiasm!\n"
                "5. UNDECIDED USERS: If the user doesn't specify a course, you MUST pick one 'booming' course (like Mobile Repairing) and explain its future/benefits/demo immediately.\n"
                "6. NO EXTERNAL TOOLS: You have ONLY the 'transfer_call' tool. NEVER try to use search, brave_search, or any other tool.\n"
                "7. NO PRESSURE: If they explicitly say they are not interested, just say 'No problem!'.\n"
                "8. Only courses. Say 'I don't know' for repairs.\n"
                "9. We have their number. NEVER ask for it.\n\n"
                "10. GREETING SAFETY (CRITICAL): NEVER call transfer_call during Step 1 (Language) or Step 2 (Ask Name). If the user says 'English' or 'Hindi', they are choosing a language, NOT asking for a transfer. STAY in the conversational flow.\n\n"
                "CRITICAL RULES (HIGHEST PRIORITY):\n"
                "1. NAME SPELLING: When confirming the user's name spelling (e.g., 'So V-I-R-A-J, Viraj, right?'), you MUST speak ONLY in English and use the English alphabet letters. Do NOT use Hindi for the spelling part, regardless of the user's language choice.\n"
                "2. PRICE INQUIRY: If a user simply asks for the price or fees of a course, EXPLAIN the standard pricing from the knowledge base. Do NOT transfer the call.\n"
                "3. PRICE NEGOTIATION: If the user tries to negotiate the price, asks for a discount, or says the price is too high, you MUST say 'I'll transfer you to the support team for pricing.' (in their preferred language) and then IMMEDIATELY call transfer_call.\n"
                "4. HUMAN TRANSFER: If the user asks to speak to a human, manager, or real person, immediately call transfer_call. Do not continue conversation.\n"
                "- NEVER transfer if the user is just choosing a language (Hindi/English) or answering standard questions.\n\n"
                f"KNOWLEDGE BASE:\n{knowledge_base}"
            )
        )
        greeting_text = "Hello! Welcome to the Expert Institute! Should we speak in Hindi or English?"

    # Component Initialization for Demo
    # Using 8b-instant. Reduced temperature to 0.1 for more reliable tool-calling.
    llm_node = groq.LLM(model="llama-3.1-8b-instant", temperature=0.1)
    
    # Testing Sarvam STT as requested
    stt_node = sarvam.STT(language="hi-IN")
    
    # Switched back to Groq STT with auto-language detection for speed/accuracy (COMMENTED OUT)
    # stt_node = groq.STT(
    #     model="whisper-large-v3-turbo",
    #     prompt="This is a bilingual conversation in Hindi and English. Watch for language choices: हिंदी, इंग्लिश, hindi, english. Topics: एडमिशन, इलेक्ट्रॉनिक्स, रिपेयर."
    # )
    
    tts_node = sarvam.TTS(
        target_language_code="hi-IN", # Optimized for Hindi output
        model="bulbul:v3",
        speaker="ritu" 
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
        min_endpointing_delay=0.6,
        min_interruption_duration=0.5,
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
    
    # Give a tiny buffer for SIP audio tracks to stabilize
    await asyncio.sleep(1)
    
    session.say(
        greeting_text, 
        allow_interruptions=True
    )
    print("DEBUG: GREETING SENT.")

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
