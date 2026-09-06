from dotenv import load_dotenv

load_dotenv()

import asyncio
import datetime
import json
import logging
import os
import time
from typing import Optional

from rag import RAGEngine
from services.calendar_api import CalComCalendar, FakeCalendar
from tools.transfer_functions import TransferFunctions

from livekit.agents import AutoSubscribe, JobContext, JobProcess, WorkerOptions, cli, llm
from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import (
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
)
import livekit.plugins.groq as groq
import livekit.plugins.sarvam as sarvam
import livekit.plugins.silero as silero
from livekit import rtc

# Core, Prompts, Services, Tools, Utils Imports
from core.patches import apply_audio_patches
from core.agent import ExpertInstituteAgent
from core.autocut import is_closing_assistant_message, hang_up_call, autocut_monitor
from prompts import DEFAULT_GREETING, DEFAULT_SYSTEM_PROMPT, OUTBOUND_SYSTEM_PROMPT, format_outbound_prompt
from services import (
    fetch_agent_config_from_supabase,
    save_call_log_to_supabase,
    send_ziper_whatsapp,
    send_wabridge_whatsapp,
    generate_call_summary,
)
from tools import create_calendar_tools
from utils import _normalize_phone_e164

# Apply stability audio monkey patches immediately on load
apply_audio_patches()

logger = logging.getLogger("voice-agent")

GROQ_LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "openai/gpt-oss-120b")


def is_legacy_prompt(prompt_text: Optional[str]) -> bool:
    """Detect if a prompt string is a stale dump of the old ~900-line legacy system prompt."""
    if not prompt_text or not prompt_text.strip():
        return False
    lower = prompt_text.lower()
    legacy_signatures = [
        "updated 2026",
        "step 1 – overview",
        "step 2 – outcome",
        "phase 1: greeting (updated 2026)",
        "avoid bookish hindi",
        "प्रशिक्षण",
    ]
    return any(sig in lower for sig in legacy_signatures)


def prewarm(proc: JobProcess):
    print("DEBUG: PREWARM STARTED")
    try:
        proc.userdata["vad"] = silero.VAD.load(
            activation_threshold=0.5,
            min_speech_duration=0.25,
            min_silence_duration=0.3,
            prefix_padding_duration=0.3,
        )
        print("DEBUG: SILERO VAD LOADED SUCCESSFULLY (Optimized for SIP)")
    except Exception as e:
        print(f"DEBUG: SILERO VAD LOAD FAILED: {e}")
    pass


async def entrypoint(ctx: JobContext):
    print(f"!!! CRITICAL: JOB ASSIGNED TO WORKER !!! Room: {ctx.room.name}")
    print(f"DEBUG: ENTRYPOINT STARTED for room {ctx.room.name}")
    try:
        logger.info(f"Connecting to room {ctx.room.name}")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        print("DEBUG: CONNECTED TO ROOM SUCCESS")

        call_start_time = datetime.datetime.now()

        # RAG Initialization
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key and "rag" not in ctx.proc.userdata:
            try:
                rag = RAGEngine(openai_api_key=openai_api_key)

                base_dir = os.path.dirname(os.path.abspath(__file__))
                data_dir = os.path.join(base_dir, "data")
                search_dirs = [data_dir, base_dir]
                kb_files = []
                for d in search_dirs:
                    if os.path.exists(d):
                        for f in os.listdir(d):
                            if f.endswith((".txt", ".pdf")) and f != "requirements.txt":
                                kb_files.append(os.path.join(d, f))
                logger.debug(f"RAG: Detected {len(kb_files)} knowledge files: {kb_files}")

                if kb_files:
                    try:
                        await rag.load_knowledge(kb_files)
                        logger.info(f"RAG: Indexed {len(kb_files)} local files (TXT/PDF).")
                    except Exception as e:
                        logger.error(f"RAG: Failed to load knowledge files: {type(e).__name__}: {e}")

                if sheet_url := os.getenv("GOOGLE_SHEET_URL"):
                    try:
                        await rag.load_knowledge_from_sheet(sheet_url)
                    except Exception as e:
                        logger.error(f"RAG: Failed to load Google Sheet ({e}), continuing without it.")

                ctx.proc.userdata["rag"] = rag
                print("DEBUG: UNIVERSAL RAG ENGINE LOADED SUCCESSFULLY (PDF + TXT + SHEETS)")
            except Exception as e:
                logger.error(f"RAG: Initialization failed: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"DEBUG: CONNECTION FAILED: {e}")
        return

    agent_config = await fetch_agent_config_from_supabase()

    recipient_name = "Student"
    target_course = "our technical programs"

    if ctx.job.metadata and ctx.job.metadata.strip():
        try:
            meta = json.loads(ctx.job.metadata)
            recipient_name = meta.get("recipientName", recipient_name)
            target_course = meta.get("targetCourse", target_course)

            generic_vals = ["our technical programs", "our training programs", "technical programs", "training programs", "none", "unknown"]
            if not target_course or target_course.lower().strip() in generic_vals:
                target_course = "our mobile repairing course"
            else:
                if "course" not in target_course.lower() and "program" not in target_course.lower():
                    target_course = f"our {target_course} course"
                else:
                    target_course = f"our {target_course}"

            logger.info(f"Metadata detected: Calling {recipient_name} for {target_course}")
        except Exception as e:
            logger.warning(f"Metadata provided but failed to parse: {e}")

    is_generic_name = recipient_name.lower().strip() in ["student", "unknown", "none", "prospect", ""]
    room_name = ctx.room.name.lower()
    is_outbound = "outbound" in room_name

    if is_outbound:
        logger.info("OUTBOUND call detected. Using refined outbound persona.")
        db_outbound_prompt = agent_config.get("outbound_system_prompt")
        if is_legacy_prompt(db_outbound_prompt):
            logger.info("Stale legacy outbound prompt detected in Supabase; using modular OUTBOUND_SYSTEM_PROMPT.")
            base_prompt = OUTBOUND_SYSTEM_PROMPT
        else:
            base_prompt = db_outbound_prompt if (db_outbound_prompt and db_outbound_prompt.strip()) else OUTBOUND_SYSTEM_PROMPT
        system_prompt = format_outbound_prompt(base_prompt, recipient_name, target_course, is_generic_name)

        db_outbound_greeting = agent_config.get("outbound_opening_greeting")
        if db_outbound_greeting and db_outbound_greeting.strip():
            greeting_text = db_outbound_greeting.replace("[Name]", recipient_name).replace("[Course]", target_course)
        else:
            if is_generic_name:
                greeting_text = f"Hello! I'm Neha calling from Expert Institute, New Delhi. I'm calling because we received an inquiry regarding {target_course}. Is this a good time to speak?"
            else:
                greeting_text = f"Hi, am I speaking with {recipient_name}?"
    else:
        logger.info("INBOUND call detected. Using standard configuration.")
        db_system_prompt = agent_config.get("system_prompt")
        if is_legacy_prompt(db_system_prompt):
            logger.info("Stale legacy inbound prompt detected in Supabase; using modular DEFAULT_SYSTEM_PROMPT.")
            system_prompt = DEFAULT_SYSTEM_PROMPT
        else:
            system_prompt = db_system_prompt if (db_system_prompt and db_system_prompt.strip()) else DEFAULT_SYSTEM_PROMPT
        greeting_text = agent_config.get("opening_greeting", DEFAULT_GREETING)


    knowledge_texts = agent_config["knowledge_texts"]
    rag_engine: Optional[RAGEngine] = ctx.proc.userdata.get("rag")
    if knowledge_texts and rag_engine:
        try:
            await rag_engine.load_knowledge_from_text(knowledge_texts)
            logger.info(f"RAG engine loaded with {len(knowledge_texts)} knowledge sources from Supabase")
        except Exception as e:
            logger.error(f"Failed to load knowledge from Supabase: {e}")

    initial_ctx = llm.ChatContext()
    initial_ctx.add_message(role="system", content=system_prompt)

    timezone = "Asia/Kolkata"
    try:
        from zoneinfo import ZoneInfo
        tz_info = ZoneInfo(timezone)
    except Exception as e:
        tz_info = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

    cal_event_id = os.getenv("CAL_EVENT_ID")
    try:
        cal_event_id = int(cal_event_id) if cal_event_id else None
    except ValueError:
        cal_event_id = None

    if cal_api_key := os.getenv("CAL_API_KEY", None):
        cal = CalComCalendar(api_key=cal_api_key, timezone=timezone, event_id=cal_event_id)
    else:
        cal = FakeCalendar(timezone=timezone)
    await cal.initialize()

    _slots_map = {}
    _slots_normalized = {}
    booking_info = {"booked": False, "name": "", "phone": "", "date": "", "time": ""}

    llm_node = groq.LLM(model=GROQ_LLM_MODEL, temperature=0.1)
    stt_node = sarvam.STT(model="saaras:v3", language="unknown", mode="codemix")
    tts_node = sarvam.TTS(
        target_language_code="hi-IN",
        model="bulbul:v3",
        speaker="roopa",
        pace=1.05,
        speech_sample_rate=22050,
        temperature=0.6,
        output_audio_bitrate="64k",
        min_buffer_size=150,
        max_chunk_length=150,
    )

    user_phone = "Unknown"
    room_name = ctx.room.name
    if room_name.startswith("+"):
        user_phone = room_name.split("_")[0].replace("+", "")
    elif "_" in room_name:
        user_phone = room_name.split("_")[0]

    fnc_ctx = TransferFunctions(ctx, user_phone)

    AUTOCUT_TIMEOUT = 60
    last_user_speech_time = [time.monotonic()]
    autocut_triggered = [False]
    agent_is_speaking = [False]
    user_is_speaking = [False]
    summary_sent = [False]

    def reset_autocut_timer(reason: str = "activity"):
        last_user_speech_time[0] = time.monotonic()
        logger.info(f"AUTOCUT: timer reset due to {reason}.")

    async def close_after_assistant_closing():
        await asyncio.sleep(0.5)
        if ctx.room.isconnected():
            await hang_up_call(ctx, session, autocut_triggered, "assistant closing detected")

    agent = ExpertInstituteAgent(
        instructions=initial_ctx.messages()[0].text_content,
        llm=llm_node,
        stt=stt_node,
        tts=tts_node,
        fnc_ctx=fnc_ctx,
        rag_engine=rag_engine,
        on_user_activity=lambda text: reset_autocut_timer("final STT transcript"),
    )

    calendar_tools = create_calendar_tools(
        cal, tz_info, _slots_map, _slots_normalized, booking_info, agent_state=agent.state
    )

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=stt_node,
        llm=llm_node,
        tts=tts_node,
        tools=fnc_ctx.flatten() + calendar_tools,
        turn_handling={
            "endpointing": {"min_delay": 0.6},
            "interruption": {"min_duration": 0.5},
            "preemptive_generation": {"enabled": False},
        },
    )

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        from livekit.agents.llm.chat_context import ChatMessage
        if isinstance(event.item, ChatMessage) and event.item.role == "assistant":
            transcript = " ".join(c for c in event.item.content if isinstance(c, str))
            print(f"\n🤖 AGENT: {transcript}\n")
            logger.info(f"Agent (LLM) says: {transcript}")
            agent_is_speaking[0] = False
            reset_autocut_timer("assistant response finished")

            if is_closing_assistant_message(transcript):
                logger.info("AUTOCUT: Exact assistant closing phrase detected.")
                asyncio.create_task(close_after_assistant_closing())

    @session.on("agent_state_changed")
    def on_agent_state_changed(event: AgentStateChangedEvent):
        if event.new_state == "speaking":
            agent_is_speaking[0] = True
            logger.info("Agent STARTED speaking...")
        elif event.new_state == "idle":
            agent_is_speaking[0] = False
            logger.info("Agent STOPPED speaking.")
            reset_autocut_timer("agent became idle")

    @session.on("user_state_changed")
    def on_user_state_changed(event: UserStateChangedEvent):
        if event.new_state == "speaking":
            user_is_speaking[0] = True
            reset_autocut_timer("user started speaking")
            logger.info("!!! INTERRUPTION: User started speaking...")
        elif event.new_state in ("listening", "idle"):
            user_is_speaking[0] = False

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: UserInputTranscribedEvent):
        if event.is_final:
            print(f"\n👤 USER: {event.transcript}\n")
            logger.info(f"User (STT) said: {event.transcript}")
            user_is_speaking[0] = False
            reset_autocut_timer("user_input_transcribed event")

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info(f"Successfully SUBSCRIBED to audio track from {participant.identity}")

    print("DEBUG: WAITING FOR USER TO ANSWER...")
    participant_identity = None
    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=30)
        participant_identity = participant.identity
        logger.info(f"User answered! Identity: {participant_identity}")
    except asyncio.TimeoutError:
        logger.warning("No answer detected within 30s.")

    await session.start(agent, room=ctx.room)
    print("DEBUG: SESSION STARTED. PREPARING GREETING...")

    if not greeting_text or greeting_text == DEFAULT_GREETING:
        greeting_text = DEFAULT_GREETING

    try:
        if is_outbound:
            await asyncio.sleep(2.0)
        session.say(greeting_text, allow_interruptions=True)
    except Exception as e:
        logger.warning(f"Could not send initial greeting: {e}")

    reset_autocut_timer("greeting sent")

    async def send_summary():
        if summary_sent[0]:
            return

        call_end_time = datetime.datetime.now()
        duration_seconds = int((call_end_time - call_start_time).total_seconds())
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        call_duration = f"{minutes}m {seconds}s"

        user_phone_normalized = None
        if participant_identity and "sip_" in participant_identity:
            user_phone_normalized = _normalize_phone_e164(participant_identity.replace("sip_", ""))

        admin_summary_text = await generate_call_summary(session.history.messages(), user_phone_normalized, booking_info)
        if not admin_summary_text:
            admin_summary_text = "Call completed but summary could not be generated."

        customer_name = booking_info.get("name", "")
        save_call_log_to_supabase(
            user_phone=user_phone_normalized or "",
            customer_name=customer_name,
            summary=admin_summary_text,
            duration=call_duration,
            is_booked=bool(booking_info.get("booked")),
            room_name=ctx.room.name,
        )

        admin_phone = os.getenv("DEFAULT_TRANSFER_NUMBER")
        await send_ziper_whatsapp(admin_phone, admin_summary_text)

        if user_phone_normalized:
            await send_wabridge_whatsapp(user_phone_normalized)

        summary_sent[0] = True

    async def safe_shutdown():
        try:
            await send_summary()
        except Exception as e:
            logger.error(f"Error during shutdown summary: {e}")

    ctx.add_shutdown_callback(safe_shutdown)

    logger.info("Greeting phase finished. Entrypoint persistence active.")

    try:
        await autocut_monitor(
            ctx,
            session,
            last_user_speech_time,
            autocut_triggered,
            agent_is_speaking,
            user_is_speaking,
            timeout_seconds=AUTOCUT_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"Error in autocut monitor: {e}")
    finally:
        logger.info("Room disconnected or autocut triggered - Entrypoint exiting.")


if __name__ == "__main__":
    required_env = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    missing_env = [name for name in required_env if not os.getenv(name)]
    if missing_env:
        missing_text = ", ".join(missing_env)
        raise RuntimeError(
            f"Missing required LiveKit environment variables: {missing_text}. Set them in your deployment environment before starting the agent."
        )

    cli.run_app(
        WorkerOptions(
            agent_name="outbound_caller",
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
