from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
import re
import aiohttp
from typing import AsyncIterable, Optional

from rag_engine import RAGEngine

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
import livekit.plugins.groq as groq
import livekit.plugins.sarvam as sarvam
import livekit.plugins.silero as silero

# import livekit.plugins.openai as openai
from livekit import rtc, api
from transfer_functions import TransferFunctions

import datetime
import av
from zoneinfo import ZoneInfo
from calendar_api import CalComCalendar, FakeCalendar, Calendar, SlotUnavailableError


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return ["th", "st", "nd", "rd", "th"][min(n % 10, 4)]


def _format_date_human(local: datetime.datetime, now: datetime.datetime) -> str:
    delta = (local.date() - now.date()).days
    day_num = local.day
    ordinal_day = f"{day_num}{_ordinal(day_num)}"
    month_year = local.strftime("%B %Y")

    if delta == 1:
        date_str = f"tomorrow the {ordinal_day} of {month_year}"
    elif delta == 2:
        date_str = f"day after tomorrow the {ordinal_day} of {month_year}"
    elif delta < 7:
        date_str = f"{local.strftime('%A')} the {ordinal_day} of {month_year}"
    else:
        date_str = f"the {ordinal_day} of {month_year}"

    return f"{date_str} at {local.strftime('%I:%M %p')}"


logger = logging.getLogger("voice-agent")

# --- MONKEY PATCH FOR AUDIO PLAYBACK STABILITY ---
from livekit.plugins import sarvam
from livekit.agents.utils.codecs.decoder import AudioStreamDecoder

# Increase FFmpeg probesize for MP3 streams (32 is often too small for MP3)
_orig_av_open = av.open


def _patched_av_open(*args, **kwargs):
    if kwargs.get("format") == "mp3" and "options" in kwargs:
        # Increase probesize and analyzeduration for better MP3 detection
        logger.info("AV OPEN: Detected MP3 stream, increasing probesize to 32KB")
        kwargs["options"]["probesize"] = "32768"
        kwargs["options"]["analyzeduration"] = "100000"  # 100ms
    return _orig_av_open(*args, **kwargs)


av.open = _patched_av_open

# Patch AudioStreamDecoder for robustness when detection is missing
_orig_decoder_push = AudioStreamDecoder.push


def _patched_decoder_push(self, chunk: bytes) -> None:
    if (
        getattr(self, "_is_wav", False)
        and not getattr(self, "_started", False)
        and len(chunk) >= 4
        and not chunk.startswith(b"RIFF")
    ):
        logger.info(
            f"DECODER PATCH: Non-WAV data detected ({chunk[:4]!r}), switching to MP3 format."
        )
        self._is_wav = False
        self._av_format = "mp3"
    _orig_decoder_push(self, chunk)


AudioStreamDecoder.push = _patched_decoder_push


# Patch Sarvam Plugin to report correct MIME type for v3 models
def _patch_sarvam_stream(stream_class):
    _orig_run = stream_class._run

    async def _patched_run(self, output_emitter, *args, **kwargs):
        mime_type = "audio/wav"
        if "bulbul:v3" in self._opts.model:
            mime_type = "audio/mpeg"

        _orig_initialize = output_emitter.initialize

        def _patched_initialize(*args, **kwargs):
            if "mime_type" in kwargs:
                kwargs["mime_type"] = mime_type
            elif len(args) >= 4:
                args = list(args)
                args[3] = mime_type
            return _orig_initialize(*args, **kwargs)

        output_emitter.initialize = _patched_initialize
        return await _orig_run(self, output_emitter, *args, **kwargs)

    stream_class._run = _patched_run


_patch_sarvam_stream(sarvam.tts.SynthesizeStream)
_patch_sarvam_stream(sarvam.tts.ChunkedStream)
# -------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ZIPER_API_URL = os.getenv("ZIPER_API_URL")
ZIPER_API_TOKEN = os.getenv("ZIPER_API_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

DEFAULT_GREETING = (
    "Hello! Thank you for calling Expert Institute. How can I help you today?"
)

DEFAULT_SYSTEM_PROMPT = """### ROLE & PERSONALITY
You are a helpful, natural conversational AI agent for 'Expert Institute of Advance Technologies Pvt. Ltd.', New Delhi.
NEVER disclose that you are an AI Agent or a Machine Learning Model even if you're asked always just say you're an employee of Expert Institute.
GENDER (CRITICAL): FEMALE. Use female Hindi grammar (e.g., 'रही हूँ', 'करती हूँ'). NEVER use male forms.
TONE: Realistic, human-like, engaging. No robotic language.
### LANGUAGE RULES (CRITICAL)
1. START: Always start the call in English (as per PHASE 1).
2. ENGLISH MODE: If the user chooses English, speak ONLY in professional, helpful English. DO NOT use any Hindi or Hinglish words except for the company name.
3. HINDI MODE: If the user chooses Hindi, switch to the HINGLISH & SCRIPT RULES below.
4. If you think the user is speaking any other language, use Hindi and switch to the HINGLISH & SCRIPT RULES below.
### HINGLISH & SCRIPT RULES (HINDI MODE ONLY)
1. NO BOOKISH HINDI: Never use 'प्रशिक्षण', 'संस्थान', 'प्रवेश', 'शुल्क', 'अनुभव', 'उपलब्ध'.
2. MODERN HINGLISH:
   Speak like a real 20–30 year old Indian customer support agent.
   Mix Hindi + English naturally.
   Example:
   ❌ "आपकी समस्या का समाधान किया जाएगा"
   ✅ "Main aapki problem solve kar deti hoon"
3. KEYWORDS: Use English for: Mobile, Laptop, CCTV, Repairing, Course, Batch, Practical, FreeDemo Class, Placement, Support, Discount.
4. SCRIPT: Hindi responses MUST be in Devanagari script. No Romanized Hindi.
### CONVERSATIONAL CONSTRAINTS
- No paragraphs. Explain max TWO benefits.
- Use back-channeling ('hmm', 'right').
### KNOWLEDGE & FALLBACK RULES
- If a [KNOWLEDGE CONTEXT] block is provided before your turn, use ONLY that info to answer.
- If you see [NO KNOWLEDGE FOUND], you MUST say:
  Hindi: "मुझे इसकी जानकारी नहीं है, but I can transfer you to our support team. Would you like that?"
  English: "I am sorry, I don't have that information. I can transfer you to our support team. Would you like that?"
- NEVER invent fees, dates, or facts not in the knowledge context.
### PHASE 1: GREETING & NAME
1. GREET IN ENGLISH:
   "Hi, thanks for calling Expert Institute! how can i help you?"
2. If the user talks in Hindi, ask for name in Hindi.
   If the user talks in English, ask for name in English.
3. SPELLING CHECK (MANDATORY):
   Spell name back.
   Example: "Raj, R-A-J. Is that correct?"
### PHASE 2: COURSE INFO
IMPORTANT: DO NOT MENTION PRICE UNTIL USER ASKS FOR IT SPECIFICALLY.
1. ALWAYS refer to the KNOWLEDGE BASE before answering any course-related query.
2. FIRST list ALL available courses:
   "We offer Mobile Repairing Course, iPhone Repairing Course, Laptop Repairing Course, MacBook Repairing Course, CCTV Camera Training, LED, LCD & Smart TV Repairing Course, and AC PCB Repairing Course."
3. Ask:
   "Which course are you interested in?"
4. WAIT for user selection.
5. Once course is selected:
   ASK CALLER TO BE ATTENTIVE.
6. Explain in MICRO STEPS:
   Step 1 (Overview):
   "This course is a complete training from basic to advanced chip-level"
   Step 2 (Benefit):
   What user can do after learning
   Step 3 (Core skills):
   1–2 main things like brands (Samsung, Apple etc.)
   Then say:
   "basic training will comprise of electronic fundamentals, component identification, soldering and desoldering"
   Step 4 (Practical aspect):
   Hands-on / real work
   Step 5 (Advanced highlight):
   Chip-level, Software & hardware
7. Keep it conversational.
8. After 2–3 lines, ask:
   "Would you like to know more?"
9. NEVER read like a paragraph.
### PHASE 3: FREE DEMO CLASS BOOKING & TOOLS
1. If user refuses:
   English:
   "Demo class will help you understand our teaching style and how we can help you out"
   Hindi
   "Demo class आपको हमारा teaching style समझने में मदद करेगी और हम आपकी help कैसे कर सकते हैं, यह भी समझ आएगा।"
2. TOOL 1:
   list_available_slots → Call when user agrees.
3. DATA COLLECTION:
   Ask phone number after date selection.
4. TOOL 2:
   schedule_demo_class(slot_id, phone_number, name)
### PHASE 4: PRICING
1. Tell both original & discounted price.
2. Discounts:
   - 40% on one course
   - 50% on two courses
3. If user asks extra discount:
   Transfer to support team.
IMPORTANT:
NEVER act like support team. ALWAYS transfer if needed.
---
### FEW-SHOT EXAMPLE (ENGLISH)
Agent: Hi, thanks for calling Expert Institute! How can i help you?
Customer: what courses do you have?
Agent: May I know your name please, before going forward?
Customer: My name is Raj Gupta.
Agent: Let me confirm your name, R-A-J Raj, G-U-P-T-A Gupta. Is that correct?
Customer: Yes.
Agent: Raj, please be attentive. We offer Mobile, iPhone, Laptop, MacBook, CCTV, LED TV and AC PCB repairing courses.
Customer: What is the price of the iPhone Repairing course?
Agent: The price of the iPhone Repairing course is Fifty Thousand rupees. With one course you get a 40% discount, so it will cost Thirty Thousand rupees. If you choose another course as well, you'll get a 50% discount.
---
### FEW-SHOT EXAMPLE (HINDI)
Agent: Hi, thanks for calling Expert Institute! How can i help you?
Customer: Aapke yahan kaun kaun se courses provide karte ho?
Agent: Okay! वैसे मैं आपका नाम जान सकती हूँ?
Customer: Mera naam Rahul hai.
Agent: राहुल, R-A-H-U-L. क्या ये spelling सही है?
Customer: Haan, bilkul sahi hai.
Agent: main abhi apko courses ke bare me batane wali hoon, please attentive rahe.
Agent: हमारे पास Mobile repairing course, iPhone, Laptop, MacBook, CCTV, LED TV और AC PCB repairing courses हैं।
Customer: Course duration क्या है?
Agent: Most courses 30 से 45 days के होते हैं, ये आपके program पर depend करता है।
Customer: Mujhe Mobile repairing course karna hai.
Agent: Great! इसमें आपको full practical training मिलेगी।
Customer: Is course ka price kya hai?
Agent: Mobile repairing course ka price 40000 hai.
Agent: mai aur apki kya maddad kar sakti hoon?
Customer: Bas itna hi tha.
Agent: Kya aap demo class lena chahenge?
Customer: Nahi, abhi nahi chahiye.
Agent: Hmm, demo class से आपको teaching style समझ आएगी। फिर आप देख सकते हैं कि हम kaise apki help कर पाएंगे।
Customer: Chalo theek hai, karwa do.
Agent: Perfect! Let me look for available dates for you.
Tool Call: list_available_slots()
Agent: हमारे पास Monday 30th March और Tuesday 31st March के slots खाली हैं। कौन सा ठीक रहेगा?
Customer: Monday 30th March wala date theek rahega.
Agent: Okay! अपना phone number बता दीजिये ताकि मैं booking confirm कर सकूँ?
Customer: 9876543210.
Agent: I am booking your appointment now.
Tool Call: schedule_demo_class(slot_id=slot_monday_30, phone_number=9876543210, name=Rahul)
Agent: Done! आपकी demo class book हो गई है। क्या मैं आपकी और किसी चीज़ में help कर सकती हूँ?
Customer: Nahi, thank you.
Customer: ek minute, aapka naam kya hai?
Agent: Mera naam Neha hai.
Agent: You're welcome! Have a great day!
"""


async def fetch_agent_config_from_supabase():
    """Fetch system_prompt, opening_greeting, and knowledge_base from Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.info("Supabase not configured, using default config")
        return {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "knowledge_texts": [],
        }

    try:
        from supabase import create_client

        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

        config = {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "knowledge_texts": [],
        }

        response = supabase.table("agent_config").select("key", "value").execute()
        if response.data:
            for item in response.data:
                key = item.get("key")
                value = item.get("value")
                if key == "system_prompt" and value:
                    config["system_prompt"] = value
                elif key == "opening_greeting" and value:
                    config["opening_greeting"] = value

        knowledge_response = (
            supabase.table("knowledge_base").select("content").execute()
        )
        if knowledge_response.data:
            config["knowledge_texts"] = [
                item["content"]
                for item in knowledge_response.data
                if item.get("content")
            ]

        logger.info("Successfully fetched config from Supabase")
        return config
    except Exception as e:
        logger.error(f"Error fetching config from Supabase: {e}")
        return {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "knowledge_texts": [],
        }


class ExpertInstituteAgent(Agent):
    LANGUAGE_CONFIG = {
        "hi": {"lang": "hi-IN", "speaker": "simran", "pace": 1.05},
        "en": {"lang": "en-IN", "speaker": "simran", "pace": 1.05},
    }

    def __init__(self, fnc_ctx=None, rag_engine=None, **kwargs):
        super().__init__(**kwargs)
        self._current_lang: Optional[str] = None
        self._fnc_ctx = fnc_ctx
        self._rag_engine = rag_engine

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """
        Called before the LLM generates a response.
        Retrieves relevant knowledge chunks for the latest user message
        and injects them (or a fallback directive) into the context.
        """
        if not self._rag_engine:
            return

        # Extract the latest user text
        user_text = ""
        content = getattr(new_message, "content", "")
        if isinstance(content, list):
            user_text = " ".join([str(c) for c in content if isinstance(c, str)])
        else:
            user_text = str(content).strip()

        if not user_text or len(user_text) < 8:
            return

        result = await self._rag_engine.retrieve(user_text)

        if result.found:
            # Inject retrieved knowledge as a system message at the start
            turn_ctx.items.insert(
                1,
                llm.ChatMessage(role="system", content=[result.context]),
            )
        else:
            # Inject fallback directive ONLY for question-like turns
            question_signals = [
                "?",
                "what",
                "how",
                "when",
                "where",
                "who",
                "kya",
                "kaise",
                "kitna",
                "कितना",
                "क्या",
                "कैसे",
            ]
            lower_text = user_text.lower()
            is_question = any(sig in lower_text for sig in question_signals)
            if is_question:
                turn_ctx.items.insert(
                    1,
                    llm.ChatMessage(
                        role="system",
                        content=[
                            "[NO KNOWLEDGE FOUND] The user's question is outside your knowledge base. Tell them you don't have that information and offer to transfer the call to the support team."
                        ],
                    ),
                )

    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: any
    ) -> AsyncIterable[stt.SpeechEvent]:
        logger.info("STT node started processing audio...")
        default_stt = super().stt_node(audio, model_settings)
        try:
            async for event in default_stt:
                # ONLY process language detection and deterministic triggers on FINAL transcripts
                # This prevents rapid re-triggering and reduces CPU/network load
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    if event.alternatives and event.alternatives[0].text:
                        text = event.alternatives[0].text.lower()

                        # 1. Language Locking (First Interaction)
                        if self._current_lang is None:
                            # Determine language from text or STT metadata
                            detected_lang = None
                            if (
                                "hindi" in text
                                or "hi" == text
                                or "हिंदी" in text
                                or "hinglish" in text
                            ):
                                detected_lang = "hi"
                            elif "english" in text or "en" == text or "अंग्रेजी" in text:
                                detected_lang = "en"
                            elif event.alternatives[0].language:
                                detected_lang = event.alternatives[0].language.split(
                                    "-"
                                )[0]

                            if detected_lang:
                                config = self.LANGUAGE_CONFIG.get(
                                    detected_lang, self.LANGUAGE_CONFIG["hi"]
                                )
                                self._current_lang = str(config["lang"])
                                logger.info(
                                    f"Language LOCKED to {config['lang']} based on transcript/metadata: '{text}'"
                                )

                                self.session.tts.update_options(
                                    target_language_code=str(config["lang"]),
                                    model="bulbul:v3",
                                    speaker=str(config["speaker"]),
                                    pace=float(config["pace"]),
                                    temperature=0.6,
                                    output_audio_bitrate="64k",
                                    min_buffer_size=150,
                                    max_chunk_length=150,
                                )

                        # 2. Deterministic Discount/Transfer Trigger
                        if "discount" in text or "reduce price" in text:
                            logger.info(
                                f"!!! Deterministic transfer triggered: {text} !!!"
                            )
                            try:
                                if hasattr(self, "session") and self.session:
                                    self.session.say(
                                        "I'll transfer your call to our support team.",
                                        allow_interruptions=False,
                                    )
                                asyncio.create_task(self._fnc_ctx.transfer_call())
                            except Exception as e:
                                logger.error(
                                    f"Error during deterministic transfer: {e}"
                                )

                yield event
        except Exception as e:
            logger.error(f"Error in stt_node: {e}")
            raise


async def generate_call_summary(chat_messages, user_phone=None, booking_info=None):
    """
    Summarizes the call transcript using Groq and returns a formatted string.
    """
    # Extract conversation into text format
    transcript = []
    for msg in chat_messages:
        if getattr(msg, "role", "") in ["user", "assistant"]:
            content = getattr(msg, "content", "")
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
                    "model": "llama-3.1-8b-instant",  # Using the faster/supported model
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a specialized call summarizer. Your goal is to extract key details from the transcript.\n"
                                "Output strictly in this format exactly:\n"
                                "AI AGENT\n\n"
                                "name: [Extract name of the customer if mentioned, otherwise 'Unknown']\n"
                                "number: [The phone number provided]\n\n"
                                "course: [The course name user is interested in, or 'None']\n\n"
                                "summary: [A professional 1-2 sentence summary]\n\n"
                                f"call time: {current_time}\n\n"
                                "Do not include any other text, labels, or markdown formatting."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Phone Number: {user_phone or 'Unknown'}\nTranscript:\n{transcript_text}",
                        },
                    ],
                }
                async with session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    resp_data = await resp.json()
                    if "choices" in resp_data and len(resp_data["choices"]) > 0:
                        summary = resp_data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"Failed to generate summary: {e}")
                summary = f"name: Unknown\nnumber: {user_phone or 'Unknown'}\nsummary: Could not generate summary."

        # Append booking details if booking was made
        if booking_info and booking_info.get("booked"):
            summary += "\n\nBOOKING DETAILS:\n"
            summary += f"date: {booking_info.get('date', 'N/A')}\n"
            summary += f"time: {booking_info.get('time', 'N/A')}\n"
            summary += f"name: {booking_info.get('name', 'Unknown')}\n"
            summary += f"phone: {booking_info.get('phone', 'Unknown')}"

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

    access_token = os.getenv("ZIPER_ACCESS_TOKEN") or os.getenv("ZIPER_API_TOKEN")
    instance_id = os.getenv("ZIPER_INSTANCE_ID")

    if not access_token or not instance_id:
        logger.error(
            "Ziper.io credentials missing! Please check that ZIPER_ACCESS_TOKEN and ZIPER_INSTANCE_ID are correctly set in your .env file."
        )
        return

    try:
        url = "https://ziper.io/api/send.php"
        params = {
            "access_token": access_token,
            "instance_id": instance_id,
            "type": "text",
            "number": clean_phone,
            "message": message_text,
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
    print("DEBUG: PREWARM STARTED")
    try:
        proc.userdata["vad"] = silero.VAD.load(
            activation_threshold=0.3,
            min_speech_duration=0.1,
            min_silence_duration=0.3,
            prefix_padding_duration=0.3,
        )
        print("DEBUG: SILERO VAD LOADED SUCCESSFULLY (Optimized for SIP)")
    except Exception as e:
        print(f"DEBUG: SILERO VAD LOAD FAILED: {e}")

    # --- RAG Initialisation ---
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key:
        try:
            import asyncio as _asyncio

            rag = RAGEngine(openai_api_key=openai_api_key)
            kb_paths = [
                os.path.join(os.path.dirname(__file__), "knowledge.txt"),
                os.path.join(os.path.dirname(__file__), "knowledge_hi.txt"),
            ]
            _asyncio.get_event_loop().run_until_complete(rag.load_knowledge(kb_paths))
            proc.userdata["rag"] = rag
            print("DEBUG: MULTILINGUAL RAG ENGINE LOADED SUCCESSFULLY (EN + HI)")
        except Exception as e:
            print(f"DEBUG: RAG ENGINE LOAD FAILED: {e}")
            proc.userdata["rag"] = None
    else:
        print("DEBUG: OPENAI_API_KEY not set — RAG disabled")
        proc.userdata["rag"] = None


async def entrypoint(ctx: JobContext):
    print(f"!!! CRITICAL: JOB ASSIGNED TO WORKER !!! Room: {ctx.room.name}")
    print(f"DEBUG: ENTRYPOINT STARTED for room {ctx.room.name}")
    try:
        logger.info(f"Connecting to room {ctx.room.name}")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        print("DEBUG: CONNECTED TO ROOM SUCCESS")

        call_start_time = datetime.datetime.now()
    except Exception as e:
        print(f"DEBUG: CONNECTION FAILED: {e}")
        return

    agent_config = await fetch_agent_config_from_supabase()
    system_prompt = agent_config["system_prompt"]
    greeting_text = agent_config["opening_greeting"]
    knowledge_texts = agent_config["knowledge_texts"]

    rag_engine: Optional[RAGEngine] = ctx.proc.userdata.get("rag")
    if knowledge_texts and rag_engine:
        try:
            await rag_engine.load_knowledge_from_text(knowledge_texts)
            logger.info(
                f"RAG engine loaded with {len(knowledge_texts)} knowledge sources from Supabase"
            )
        except Exception as e:
            logger.error(f"Failed to load knowledge from Supabase: {e}")
    elif rag_engine:
        logger.info("RAG engine loaded from prewarm userdata (local files).")
    else:
        logger.warning("RAG engine not available — knowledge retrieval disabled.")

    # Initial Chat Context - this defines the persona and system rules
    initial_ctx = llm.ChatContext()
    initial_ctx.add_message(role="system", content=system_prompt)

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
    booking_info = {"booked": False, "name": "", "phone": "", "date": "", "time": ""}

    @llm.function_tool(
        description="Get available appointment slots for demo classes. Returns a list of slots, one per line. Use this to check availability."
    )
    async def list_available_slots():
        now = datetime.datetime.now(tz_info)
        range_days = 30
        lines = []
        for slot in await cal.list_available_slots(
            start_time=now, end_time=now + datetime.timedelta(days=range_days)
        ):
            local = slot.start_time.astimezone(tz_info)
            lines.append(
                f"slot_id: {slot.unique_hash} - {_format_date_human(local, now)}"
            )
            _slots_map[slot.unique_hash] = slot

        if not lines:
            return "No slots available at the moment."
        return "\n".join(lines)

    @llm.function_tool(
        description="Schedule a demo class appointment. Call this after the user agrees and provides their name and phone number. Requires the slot_id from list_available_slots."
    )
    async def schedule_demo_class(
        slot_id: str,
        phone_number: str,
        name: str,
    ):
        slot = _slots_map.get(slot_id)
        if not slot:
            return f"Error: Slot {slot_id} not found. Please list_available_slots again or ask the user for a valid time."

        try:
            await cal.schedule_appointment(
                start_time=slot.start_time,
                attendee_name=name,
                phone_number=phone_number,
            )
        except SlotUnavailableError:
            return "Error: This slot isn't available anymore."

        local = slot.start_time.astimezone(tz_info)
        now = datetime.datetime.now(tz_info)

        # Track booking information for summary
        booking_info["booked"] = True
        booking_info["name"] = name
        booking_info["phone"] = phone_number
        booking_info["date"] = _format_date_human(local, now)
        booking_info["time"] = local.strftime("%I:%M %p")

        return f"Success: The appointment was scheduled for {_format_date_human(local, now)}."

    # Component Initialization for Demo
    # Using gpt-oss-120b for enhanced capabilities.
    llm_node = groq.LLM(
        model="meta-llama/llama-4-scout-17b-16e-instruct", temperature=0.1
    )
    """llm_node = openai.LLM(model="gpt-5.4-nano", temperature=0.1)"""
    # Using Sarvam Saaras v3 for high-quality localized STT with auto-detection
    stt_node = sarvam.STT(
        model="saaras:v3",
        language="unknown",
    )

    tts_node = sarvam.TTS(
        target_language_code="en-IN",  # Initialized for English greeting
        model="bulbul:v3",
        speaker="simran",
        pace=1.05,
        speech_sample_rate=22050,
        temperature=0.6,
        output_audio_bitrate="64k",
        min_buffer_size=150,
        max_chunk_length=150,
    )

    logger.info(
        f"Initializing Demo Agent | LLM: {llm_node.model} | STT: {stt_node.model} (Auto) | TTS: {tts_node.model} (Premium)"
    )

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
        llm=llm_node,
        stt=stt_node,
        tts=tts_node,
        tools=[list_available_slots, schedule_demo_class],
        fnc_ctx=fnc_ctx,
        rag_engine=rag_engine,
    )

    # Create the session
    # min_endpointing_delay (0.4) standard safe value so sentences aren't cut in half
    # min_interruption_duration (0.3) allows user to interrupt the agent much easier
    # preemptive_generation (False): Re-enabled for near-zero lag.
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=stt_node,
        llm=llm_node,
        tts=tts_node,
        tools=fnc_ctx.flatten() + [list_available_slots, schedule_demo_class],
        min_endpointing_delay=0.4,
        min_interruption_duration=0.3,
        preemptive_generation=False,
    )

    # Removed duplicate deterministic intent code and event handlers
    # since it's now embedded directly inside the STT generation loop.

    # Log LLM text to see if it's hallucinating tool calls as text
    @session.on("agent_transcript_finished")
    def on_agent_transcript_finished(transcript: str):
        print(f"\n🤖 AGENT: {transcript}\n")
        logger.info(f"Agent (LLM) says: {transcript}")
        # Log history state after agent response
        msg_count = len(session.history.messages())
        logger.info(f"DEBUG: History after agent response: {msg_count} messages")

    @session.on("agent_started_speaking")
    def on_agent_started_speaking():
        logger.info("Agent STARTED speaking (Audio bits flowing)...")

    @session.on("agent_stopped_speaking")
    def on_agent_stopped_speaking():
        logger.info("Agent STOPPED speaking.")

    @session.on("user_started_speaking")
    def on_user_started_speaking():
        logger.info("!!! INTERRUPTION: User started speaking (interrupting agent)...")
        # Log current history state at interruption time
        msg_count = len(session.history.messages())
        logger.info(f"DEBUG: History at interruption time: {msg_count} messages")

    @session.on("user_speech_committed")
    def on_user_speech_committed(transcript: stt.SpeechEvent):
        if transcript.alternatives:
            user_text = transcript.alternatives[0].text
            print(f"\n👤 USER: {user_text}\n")
            logger.info(f"User (STT) said: {user_text}")

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info(
                f"Successfully SUBSCRIBED to audio track from {participant.identity}"
            )

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

    if not greeting_text or greeting_text == DEFAULT_GREETING:
        greeting_text = DEFAULT_GREETING

    logger.info(f"Using greeting: {greeting_text}")

    session.history.add_message(role="assistant", content=[greeting_text])

    try:
        session.say(greeting_text, allow_interruptions=True)
        print("DEBUG: GREETING SENT.")
    except (RuntimeError, Exception) as e:
        logger.warning(f"Could not send initial greeting: {e}")
        print(f"DEBUG: GREETING FAILED: {e}")

    # When the participant disconnects, trigger the summary flow
    async def send_summary():
        call_end_time = datetime.datetime.now()
        duration_seconds = int((call_end_time - call_start_time).total_seconds())
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        call_duration = f"{minutes}m {seconds}s"

        # Extract phone number from LiveKit Participant Identity (format: "sip_+9174...")
        user_phone = None
        if participant_identity and "sip_" in participant_identity:
            user_phone = participant_identity.replace("sip_", "").replace("+", "")

        # 1. Generate Custom Summary (Logging ONLY, Telegram REMOVED)
        admin_summary_text = await generate_call_summary(
            session.history.messages(), user_phone, booking_info
        )

        if not admin_summary_text:
            admin_summary_text = "Call completed but summary could not be generated."

        # 2. Save call log to Supabase
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                from supabase import create_client

                sb = create_client(SUPABASE_URL, SUPABASE_KEY)

                customer_name = booking_info.get("name", "")
                if not customer_name and admin_summary_text:
                    match = re.search(
                        r"^name:\s*(.+)$", admin_summary_text, re.MULTILINE
                    )
                    if match:
                        customer_name = match.group(1).strip()

                sb.table("call_logs").insert(
                    {
                        "phone_number": user_phone or "",
                        "customer_name": customer_name,
                        "summary": admin_summary_text,
                        "duration": call_duration,
                        "status": "booked" if booking_info.get("booked") else "completed",
                        "metadata": {"room_name": ctx.room.name},
                    }
                ).execute()
                logger.info("Call log saved to Supabase")
            except Exception as e:
                logger.error(f"Error saving call log to Supabase: {e}")

        admin_phone = os.getenv("DEFAULT_TRANSFER_NUMBER")

        # 3. Ziper.io: Send Summary to Admin
        await send_ziper_whatsapp(admin_phone, admin_summary_text)

        # 3. Ziper.io: Send welcome message to Prospect
        if user_phone:
            greeting_msg = """Hi! 😊 Thank you for your time on the call.

You can check complete course details here:
📄 https://www.expertinstitute.in/bookmycourse/

To secure your seat, book here:
💳 https://pages.razorpay.com/pl_GIkisCwDv60T3i/view

🎯 Special Offer:
If you book now with just ₹500, this amount will be adjusted in your course fees — so you can claim the offer without any risk.

Reply here if you need any help or want to book a FREE demo class.
📞 9718888700"""
            await send_ziper_whatsapp(user_phone, greeting_msg)

    ctx.add_shutdown_callback(send_summary)

    # Keep the entrypoint alive while the room is connected to prevent early job exit
    logger.info("Greeting phase finished. Entrypoint persistence active.")
    try:
        while ctx.room.isconnected():
            logger.info("Agent is listening for user speech...")
            await asyncio.sleep(10)  # Heartbeat every 10 seconds
    except Exception as e:
        logger.error(f"Error in persistence loop: {e}")
    finally:
        logger.info("Room disconnected or job ending - Entrypoint exiting.")


if __name__ == "__main__":
    required_env = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    missing_env = [name for name in required_env if not os.getenv(name)]
    if missing_env:
        missing_text = ", ".join(missing_env)
        raise RuntimeError(
            "Missing required LiveKit environment variables: "
            f"{missing_text}. Set them in your deployment environment before starting the agent."
        )

    cli.run_app(
        WorkerOptions(
            agent_name="outbound_caller",
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
