import asyncio
import logging
import os
import re
import time
from livekit import api

logger = logging.getLogger("voice-agent")


def is_closing_assistant_message(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    return any(
        closing_line in normalized
        for closing_line in (
            "thank you for contacting expert institute. have a wonderful day",
            "expert institute से contact करने के लिए thank you",
            "we look forward to meeting you in the free demo class",
            "thank you for calling expert institute. goodbye",
            "expert institute call करने के लिए धन्यवाद. goodbye",
        )
    )


async def hang_up_call(ctx, session, autocut_triggered: list, reason: str):
    if autocut_triggered[0]:
        return

    autocut_triggered[0] = True
    logger.info(f"AUTOCUT: {reason}. Ending call.")
    try:
        await session.aclose()
    except Exception as e:
        logger.warning(f"Could not close agent session during hangup: {e}")

    livekit_url = os.getenv("LIVEKIT_URL")
    livekit_key = os.getenv("LIVEKIT_API_KEY")
    livekit_secret = os.getenv("LIVEKIT_API_SECRET")
    if not all([livekit_url, livekit_key, livekit_secret]):
        return

    lkapi = api.LiveKitAPI(livekit_url, livekit_key, livekit_secret)
    try:
        await lkapi.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
    except Exception as e:
        logger.warning(f"Could not delete LiveKit room during hangup: {e}")
    finally:
        await lkapi.aclose()


async def autocut_monitor(
    ctx,
    session,
    last_user_speech_time: list,
    autocut_triggered: list,
    agent_is_speaking: list,
    user_is_speaking: list,
    timeout_seconds: int = 60,
):
    """End the call after specified seconds of user inactivity."""
    while ctx.room.isconnected():
        await asyncio.sleep(5)

        if autocut_triggered[0]:
            continue

        if agent_is_speaking[0] or user_is_speaking[0]:
            continue

        idle_seconds = time.monotonic() - last_user_speech_time[0]
        if idle_seconds >= timeout_seconds:
            logger.info(
                f"AUTOCUT: {int(idle_seconds)}s user inactivity. Ending call."
            )
            try:
                handle = session.say(
                    "It seems the line has gone quiet. Goodbye!",
                    allow_interruptions=False,
                )
                await handle.wait_for_playout()
            except Exception:
                pass
            await hang_up_call(ctx, session, autocut_triggered, "inactivity timeout")
