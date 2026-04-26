from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
import re
import aiohttp
import json
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

# WABridge Credentials
WABRIDGE_AUTH_KEY = os.getenv("WABRIDGE_AUTH_KEY")
WABRIDGE_APP_KEY = os.getenv("WABRIDGE_APP_KEY")
WABRIDGE_DEVICE_ID = os.getenv("WABRIDGE_DEVICE_ID")
WABRIDGE_MEDIA_URL = os.getenv("WABRIDGE_MEDIA_URL")
WABRIDGE_TEMPLATE_ID = os.getenv("WABRIDGE_TEMPLATE_ID")

DEFAULT_GREETING = (
    "Hello! Thank you for calling Expert Institute. How can I help you today?"
)

OUTBOUND_SYSTEM_PROMPT = """### ROLE & PERSONALITY
You are Neha, a helpful and natural conversational AI agent for 'Expert Institute of Advance Technologies Pvt. Ltd.', New Delhi.
You are making an OUTBOUND call to a potential student who has inquired about your courses.
NEVER disclose that you are an AI Agent or a Machine Learning Model even if you're asked always just say you're an employee of Expert Institute.
GENDER (CRITICAL): FEMALE. Use female Hindi grammar (e.g., 'रही हूँ', 'करती हूँ'). NEVER use male forms.
TONE: Realistic, human-like, engaging, and professional.

### LANGUAGE RULES (CRITICAL)
1. START: Always start the call in English.
2. ENGLISH MODE: If the user chooses English, speak ONLY in professional, helpful English.
3. HINDI MODE: If the user chooses Hindi, switch to the HINGLISH & SCRIPT RULES below.
4. If you think the user is speaking any other language, use Hindi and switch to the HINGLISH & SCRIPT RULES below.

### HINGLISH & SCRIPT RULES (HINDI MODE ONLY)
- Mix Hindi + English naturally. 
- Use Roman script (English letters) for all responses.
- Speak like a friendly 20–30 year old Indian customer support agent.

### PHASE 1: GREETING & PURPOSE
1. GREET IN ENGLISH: 
   "Hi, am I speaking with [Name]?" 
   (Wait for confirmation)
2. PROCEED AFTER CONFIRMATION:
   "Hi! I'm Neha calling from Expert Institute, New Delhi. I'm calling because you recently showed interest in [Course]. Is this a good time to speak?"
3. If they are busy: "No problem! When would be a better time to call you back?"
4. If they are free: Proceed to list the courses.

### PHASE 2: COURSE CONFIRMATION & DETAILS
1. ACKNOWLEDGE INTEREST:
   "Great! Since you are interested in [Course], I can tell you all about it. We also offer specialized courses in Mobile Repairing, iPhone, Laptop, MacBook, CCTV, LED TV, and AC PCB repairing if you are curious about those too."
2. CONFIRM COURSE:
   "Is [Course] the main thing you are looking for?"
3. Refer to [KNOWLEDGE CONTEXT] for any specific course details or benefits.

### PHASE 3: THE HOOK (FREE DEMO CLASS)
1. After providing initial info, PUSH for the demo:
   "Since you've already inquired, I'd highly recommend booking a FREE demo class. It's the best way to see our practical labs and teaching style. Should I check the available slots for you?"

### CONVERSATIONAL CONSTRAINTS
- No paragraphs. Keep it light and interactive.
- Use back-channeling ('hmm', 'right').
"""

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
4. SCRIPT: Write ALL Hindi/Hinglish responses in Roman script only. 
Speak in natural, conversational Hinglish like a friendly 20–30 year old Indian customer support agent.
Keep it casual but clear and professional. 
Avoid pure Hindi and avoid overly slangy or broken sentences.
Example: "main aapko explain karti hoon", "aap kaunsa course dekh rahe ho?"
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
            "outbound_system_prompt": "",  # Empty by default to trigger fallback
            "outbound_opening_greeting": "",
            "knowledge_texts": [],
        }

        try:
            response = supabase.table("agent_config").select("key", "value").execute()
            if response.data:
                for item in response.data:
                    key = item.get("key")
                    value = item.get("value")
                    if key in config and value:
                        config[key] = value
        except Exception as e:
            logger.error(f"Error fetching config from agent_config table: {e}")

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
        "hi": {"lang": "hi-IN", "speaker": "roopa", "pace": 1.05},
        "en": {"lang": "en-IN", "speaker": "roopa", "pace": 1.05},
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


async def send_wabridge_whatsapp(phone_number, template_id=None, media_url=None):
    """
    WABridge WhatsApp API integration to send template messages with media.
    """
    if not phone_number:
        return

    # Formatting: 91 prefix, no +
    clean_phone = phone_number.replace("+", "")
    if not clean_phone.startswith("91"):
        clean_phone = "91" + clean_phone

    logger.info(f"Preparing to send WABridge template message to {clean_phone}...")

    auth_key = WABRIDGE_AUTH_KEY
    app_key = WABRIDGE_APP_KEY
    device_id = WABRIDGE_DEVICE_ID
    
    # Use provided template/media or fallback to environment variables
    template_id = template_id or WABRIDGE_TEMPLATE_ID
    media_url = media_url or WABRIDGE_MEDIA_URL

    if not all([auth_key, app_key, device_id, template_id]):
        logger.error("WABridge credentials or Template ID missing! Please check your .env file.")
        return

    try:
        # Switching to GET with params, which is more reliable for these types of WhatsApp bridges
        url = "https://web.wabridge.com/api/createmessage"
        params = {
            "authkey": auth_key,
            "apikey": auth_key,    # Some versions use apikey instead of authkey
            "auth_key": auth_key,
            "appkey": app_key,
            "app_key": app_key,
            "device_id": device_id,
            "destination_number": clean_phone,
            "phone": clean_phone,   # Adding phone back as fallback
            "template_id": template_id,
            "media_url": media_url,
        }

        async with aiohttp.ClientSession() as session:
            # Using Form Data (data=params) which is typically required for 'createmessage' endpoints
            async with session.post(url, data=params, ssl=False) as response:
                resp_text = await response.text()
                if response.status in [200, 201]:
                    logger.info(f"Successfully sent WABridge WhatsApp template to {clean_phone}. Response: {resp_text}")
                else:
                    logger.error(f"WABridge API failed (Status {response.status}): {resp_text}")
    except Exception as e:
        logger.error(f"Error calling WABridge: {e}")


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

    # RAG is now loaded in entrypoint for loop stability.
    pass


async def entrypoint(ctx: JobContext):
    print(f"!!! CRITICAL: JOB ASSIGNED TO WORKER !!! Room: {ctx.room.name}")
    print(f"DEBUG: ENTRYPOINT STARTED for room {ctx.room.name}")
    try:
        logger.info(f"Connecting to room {ctx.room.name}")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        print("DEBUG: CONNECTED TO ROOM SUCCESS")

        call_start_time = datetime.datetime.now()

        # --- Safe RAG Initialization (Inside Entrypoint) ---
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key and "rag" not in ctx.proc.userdata:
            try:
                rag = RAGEngine(openai_api_key=openai_api_key)

                # Automatically find all local TXT and PDF knowledge files
                base_dir = os.path.dirname(__file__)
                kb_files = [
                    os.path.join(base_dir, f)
                    for f in os.listdir(base_dir)
                    if f.endswith((".txt", ".pdf"))
                ]

                if kb_files:
                    await rag.load_knowledge(kb_files)
                    logger.info(f"RAG: Indexed {len(kb_files)} local files (TXT/PDF).")

                # Check for Google Sheet URL (Safely)
                if sheet_url := os.getenv("GOOGLE_SHEET_URL"):
                    try:
                        await rag.load_knowledge_from_sheet(sheet_url)
                    except Exception as e:
                        logger.error(
                            f"RAG: Failed to load Google Sheet ({e}), continuing without it."
                        )

                ctx.proc.userdata["rag"] = rag
                print(
                    "DEBUG: UNIVERSAL RAG ENGINE LOADED SUCCESSFULLY (PDF + TXT + SHEETS)"
                )
            except Exception as e:
                print(f"DEBUG: RAG ENGINE INIT FAILED: {e}")
    except Exception as e:
        print(f"DEBUG: CONNECTION FAILED: {e}")
        return

    agent_config = await fetch_agent_config_from_supabase()

    # --- Metadata & Persona Selection ---
    recipient_name = "Student"
    target_course = "our technical programs"

    # Safely handle metadata (especially for Inbound calls where it might be empty)
    if ctx.job.metadata and ctx.job.metadata.strip():
        try:
            meta = json.loads(ctx.job.metadata)
            recipient_name = meta.get("recipientName", recipient_name)
            target_course = meta.get("targetCourse", target_course)
            
            # Normalize and handle fallback for generic/missing courses
            generic_vals = ["our technical programs", "our training programs", "technical programs", "training programs", "none", "unknown"]
            if not target_course or target_course.lower().strip() in generic_vals:
                target_course = "our mobile repairing course"
            else:
                # Add "course" suffix for specific courses if it doesn't already have it
                if "course" not in target_course.lower() and "program" not in target_course.lower():
                    target_course = f"our {target_course} course"
                else:
                    target_course = f"our {target_course}"
            
            logger.info(
                f"Metadata detected: Calling {recipient_name} for {target_course}"
            )
        except Exception as e:
            logger.warning(f"Metadata provided but failed to parse: {e}")
            logger.debug(f"Raw metadata was: '{ctx.job.metadata}'")

    # Detect if we have a generic name (to avoid saying "Hi Student")
    is_generic_name = recipient_name.lower().strip() in ["student", "unknown", "none", "prospect", ""]

    # Detect call direction (Inbound vs Outbound)
    # Outbound calls are explicitly dispatched by vobiz_outbound.py with "outbound" in room name
    # Inbound calls (SIP users calling the system) have room names like "+91865..." or contain "sip"
    room_name = ctx.room.name.lower()
    is_outbound = "outbound" in room_name

    if is_outbound:
        logger.info("OUTBOUND call detected. Using refined outbound persona.")

        # 1. System Prompt Fallback Logic
        db_outbound_prompt = agent_config.get("outbound_system_prompt")
        if db_outbound_prompt and db_outbound_prompt.strip():
            logger.info("Using custom OUTBOUND system prompt from Supabase.")
            system_prompt = db_outbound_prompt
        else:
            logger.info(
                "No custom outbound prompt found (or empty). Falling back to hardcoded OUTBOUND_SYSTEM_PROMPT."
            )
            system_prompt = OUTBOUND_SYSTEM_PROMPT

        # Inject dynamic details into prompt
        system_prompt = system_prompt.replace("[Name]", recipient_name)
        system_prompt = system_prompt.replace("[Course]", target_course)
        
        if is_generic_name:
            system_prompt += "\n\nCRITICAL: You have already introduced yourself and mentioned the course interest in the initial greeting. DO NOT repeat your introduction. Respond naturally to the user's answer and proceed to course details (Phase 2)."
        
        system_prompt += f"\n\nCURRENT CONTEXT:\nYou are calling {recipient_name} specifically about the {target_course} course they inquired about."

        # 2. Greeting Fallback Logic
        db_outbound_greeting = agent_config.get("outbound_opening_greeting")
        if db_outbound_greeting and db_outbound_greeting.strip():
            logger.info("Using custom OUTBOUND greeting from Supabase.")
            greeting_text = db_outbound_greeting.replace("[Name]", recipient_name).replace("[Course]", target_course)
        else:
            logger.info("No custom outbound greeting found. Using Smart Greeting logic.")
            if is_generic_name:
                greeting_text = f"Hello! I'm Neha calling from Expert Institute, New Delhi. I'm calling because we received an inquiry regarding {target_course}. Is this a good time to speak?"
            else:
                greeting_text = f"Hi, am I speaking with {recipient_name}?"

        print(f"DEBUG: OUTBOUND GREETING SELECTED: '{greeting_text}'")
    else:
        logger.info("INBOUND call detected. Using standard configuration.")

        system_prompt = agent_config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        greeting_text = agent_config.get("opening_greeting", DEFAULT_GREETING)
        print(f"DEBUG: INBOUND GREETING SELECTED: '{greeting_text}'")

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
    tz_info = None
    try:
        from zoneinfo import ZoneInfo

        tz_info = ZoneInfo(timezone)
    except Exception as e:
        logger.warning(
            f"ZoneInfo database missing or error ({e}). Using hardcoded Indian Offset (+5:30)."
        )
        tz_info = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

    cal_event_id = os.getenv("CAL_EVENT_ID")
    if cal_event_id:
        try:
            cal_event_id = int(cal_event_id)
        except ValueError:
            cal_event_id = None

    if cal_api_key := os.getenv("CAL_API_KEY", None):
        logger.info(
            f"CAL_API_KEY detected, using cal.com calendar (event_id: {cal_event_id})"
        )
        cal = CalComCalendar(
            api_key=cal_api_key, timezone=timezone, event_id=cal_event_id
        )
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
            result = await cal.schedule_appointment(
                start_time=slot.start_time,
                attendee_name=name,
                phone_number=phone_number,
            )
            if result.startswith("Error"):
                return result
        except Exception as e:
            logger.error(f"Unexpected error in schedule_demo_class: {e}")
            return f"Error: An unexpected error occurred while booking. ({str(e)})"

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
        speaker="roopa",
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
    print("DEBUG: WAITING FOR USER TO ANSWER...")
    participant_identity = None
    try:
        # For outbound calls, the participant joins the room only when the phone is answered
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=30)
        participant_identity = participant.identity
        logger.info(f"User answered! Identity: {participant_identity}")
        print(f"DEBUG: USER ANSWERED: {participant_identity}")
    except asyncio.TimeoutError:
        print("DEBUG: TIMEOUT WAITING FOR ANSWER - Starting session anyway")
        logger.warning("No answer detected within 30s.")

    await session.start(agent, room=ctx.room)
    print("DEBUG: SESSION STARTED. PREPARING GREETING...")

    if not greeting_text or greeting_text == DEFAULT_GREETING:
        greeting_text = DEFAULT_GREETING

    logger.info(f"Using greeting: {greeting_text}")

    session.history.add_message(role="assistant", content=[greeting_text])

    try:
        if is_outbound:
            # SIP calls need a moment for the audio bridge to clear after answering
            print("DEBUG: OUTBOUND - Waiting 2.0s for audio bridge to stabilize...")
            await asyncio.sleep(2.0)

        session.say(greeting_text, allow_interruptions=True)
        print(f"DEBUG: GREETING SENT: '{greeting_text}'")
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
                        "status": "booked"
                        if booking_info.get("booked")
                        else "completed",
                        "metadata": {"room_name": ctx.room.name},
                    }
                ).execute()
                logger.info("Call log saved to Supabase")
            except Exception as e:
                logger.error(f"Error saving call log to Supabase: {e}")

        admin_phone = os.getenv("DEFAULT_TRANSFER_NUMBER")

        # 3. Ziper.io: Send Summary to Admin
        await send_ziper_whatsapp(admin_phone, admin_summary_text)

        # 3. WhatsApp Follow-ups (Ziper + WABridge)
        if user_phone:
            # Send Ziper Text Message
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
            
            # Send WABridge Video Template
            await send_wabridge_whatsapp(user_phone)

    # We wrap the shutdown callback to ensure it doesn't block forever
    # and handles its own errors gracefully.
    async def safe_shutdown():
        try:
            # Setting a reasonable timeout for the summary/whatsapp tasks
            await asyncio.wait_for(send_summary(), timeout=10)
        except asyncio.TimeoutError:
            logger.warning("Summary task timed out during shutdown.")
        except Exception as e:
            logger.error(f"Error during shutdown summary: {e}")

    ctx.add_shutdown_callback(safe_shutdown)

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
