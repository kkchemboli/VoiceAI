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

import datetime
import av
from zoneinfo import ZoneInfo
from calendar_api import CalComCalendar, FakeCalendar, Calendar, SlotUnavailableError

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


class ExpertInstituteAgent(Agent):
    LANGUAGE_CONFIG = {
        "hi": {"lang": "hi-IN", "speaker": "simran", "pace": 1.05},
        "en": {"lang": "en-IN", "speaker": "simran", "pace": 1.05},
    }

    def __init__(self, fnc_ctx=None, **kwargs):
        super().__init__(**kwargs)
        self._current_lang: Optional[str] = None
        self._fnc_ctx = fnc_ctx

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
                                    detected_lang, self.LANGUAGE_CONFIG["en"]
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


async def generate_call_summary(chat_messages, user_phone=None):
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
                                "name: [Extract name if mentioned, otherwise 'Unknown']\n"
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


async def entrypoint(ctx: JobContext):
    print(f"!!! CRITICAL: JOB ASSIGNED TO WORKER !!! Room: {ctx.room.name}")
    print(f"DEBUG: ENTRYPOINT STARTED for room {ctx.room.name}")
    try:
        logger.info(f"Connecting to room {ctx.room.name}")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        print("DEBUG: CONNECTED TO ROOM SUCCESS")
    except Exception as e:
        print(f"DEBUG: CONNECTION FAILED: {e}")
        return

    # Load knowledge base content dynamically
    kb_path = os.path.join(os.path.dirname(__file__), "knowledge.txt")
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            knowledge_base = f.read()
        logger.info(
            f"Successfully loaded knowledge base from {kb_path} ({len(knowledge_base)} characters)"
        )
    except FileNotFoundError:
        logger.warning(
            f"Knowledge base file {kb_path} not found. Using default instructions."
        )
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

            "2. Be EXTREMELY concise: ALWAYS keep your turns under 100 characters. This is CRITICAL for the phone system stability. "

            "Never provide a full paragraph. Explain at max TWO benefits.\n\n"

            "PHASE 1: GREETING, LANGUAGE & NAME COLLECTION (CRITICAL)\n"

            "1. You MUST start the call BY GREETING ONLY IN ENGLISH. Ask them clearly if they prefer to continue in English or Hindi.\n"

            "2. ALWAYS wait for their response. Do not provide course info until they have chosen a language.\n"

            "3. Once a language is chosen, ask for the caller's name (e.g., 'May I know your name?' or 'आपका नाम क्या है?').\n"

            "4. When the caller provides their name, confirm it by spelling it out (e.g., if they say 'Raj', you respond 'Raj, R-A-J. Is that correct?'). This spelling confirmation is MANDATORY for all names.\n\n"

            "PHASE 2: INFORMATION GATHERING AND COURSE EXPLANATION\n"

            "1. Once the name is confirmed, explain our courses ONE sentence at a time to keep it engaging and conversational.\n"

            "2. Ask 'Are you interested in any of these courses?' before explaining further.\n\n"

            "PHASE 3: DEMO CLASS BOOKING\n"

            "1. Explain their chosen course in brief, explicitly mentioning how this course will benefit the caller.\n"

            "2. After the course explanation, ask the caller to attend a free demo class.\n"

            "3. If the caller refuses, suggest they attend the demo class ONE MORE TIME.\n"

            "4. If they refuse AGAIN, DO NOT force them any further. Simply ask how else you can help them.\n"

            "5. If the caller AGREES to the demo class, ask them for their phone number, preferred date, and time slot.\n"

            "6. To find time slots, call 'list_available_slots' and offer them a few options. Once they agree to a slot and provide their phone number, use 'schedule_demo_class' with the slot_id to book it.\n\n"

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
                f"slot_id: {slot.unique_hash} - {local.strftime('%A, %B %d, %Y')} at {local:%H:%M} {local.tzname()}"
            )
            _slots_map[slot.unique_hash] = slot

        if not lines:
            return "No slots available at the moment."
        return "\n".join(lines)

    @llm.function_tool(
        description="Schedule a demo class appointment. Call this after the user agrees and provides phone number. Requires the slot_id from list_available_slots."
    )
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
                phone_number=phone_number,
            )
        except SlotUnavailableError:
            return "Error: This slot isn't available anymore."

        local = slot.start_time.astimezone(tz_info)
        return f"Success: The appointment was scheduled for {local.strftime('%A, %B %d, %Y at %H:%M %Z')}."

    # Component Initialization for Demo
    # Using 8b-instant. Reduced temperature to 0.1 for more reliable tool-calling.
    llm_node = groq.LLM(model="meta-llama/llama-4-scout-17b-16e-instruct", temperature=0.1)
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
        min_endpointing_delay=0.4,
        min_interruption_duration=0.3,
        preemptive_generation=False,
    )

    # Removed duplicate deterministic intent code and event handlers
    # since it's now embedded directly inside the STT generation loop.

    # Log LLM text to see if it's hallucinating tool calls as text
    @session.on("agent_transcript_finished")
    def on_agent_transcript_finished(transcript: str):
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

    # Initial greeting in English only (Sarvam fails on Devnagari in English mode)
    greeting_text = "Namaste! Welcome to Expert Institute. I am Simran. Before we begin, would you prefer to speak in English or Hindi?"

    # Sync greeting to history so LLM knows it spoke Step 1
    session.history.add_message(role="assistant", content=[greeting_text])

    try:
        session.say(greeting_text, allow_interruptions=True)
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
        admin_summary_text = await generate_call_summary(
            session.history.messages(), user_phone
        )

        if not admin_summary_text:
            admin_summary_text = "Call completed but summary could not be generated."

        admin_phone = "917498952789"  # the number you provided

        # 2. Ziper.io: Send Summary to Admin
        await send_ziper_whatsapp(admin_phone, admin_summary_text)

        # 3. Ziper.io: Send welcome message to Prospect
        if user_phone:
            greeting_msg = "Hello! Welcome to Expert Institute. We're glad we could speak with you. Let us know if you need anything else!"
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
    cli.run_app(
        WorkerOptions(
            agent_name="outbound_caller",
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
