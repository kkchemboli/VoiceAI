import asyncio
import os
import sys
import uuid
import json
import logging
from dotenv import load_dotenv
from livekit import api

logger = logging.getLogger("outbound-call")

load_dotenv()


async def make_outbound_call(destination_number, recipient_name="Student", target_course="our technical programs", wait_for_completion=False):
    """
    Initiates an outbound SIP call via LiveKit and Vobiz.
    """
    url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    trunk_id = os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
    room_name = os.getenv("LIVEKIT_SIP_ROOM", "outbound-call-room")

    if not all([url, api_key, api_secret, trunk_id]):
        raise RuntimeError(
            "Missing LiveKit SIP configuration; set LIVEKIT_URL, LIVEKIT_API_KEY, "
            "LIVEKIT_API_SECRET, and LIVEKIT_SIP_OUTBOUND_TRUNK_ID."
        )

    destination_number = destination_number.strip().replace(" ", "").replace("-", "")
    
    if len(destination_number) == 10 and destination_number.isdigit():
        logger.info("Smart formatting applied for 10-digit destination")
        destination_number = '+91' + destination_number
    elif not destination_number.startswith('+'):
        destination_number = '+' + destination_number

    base_room = os.getenv("LIVEKIT_SIP_ROOM", "outbound-call")
    room_name = f"{base_room}-{uuid.uuid4().hex[:6]}"

    lkapi = api.LiveKitAPI(url, api_key, api_secret)

    logger.info("Dialing outbound destination", extra={"room_name": room_name})

    metadata = json.dumps({
        "recipientName": recipient_name,
        "targetCourse": target_course
    })

    try:
        logger.info("Dispatching outbound agent")
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="outbound_caller",
                room=room_name,
                metadata=metadata
            )
        )
        logger.info("Agent dispatched successfully", extra={"dispatch_id": dispatch.id})

        logger.info("Initiating SIP call", extra={"room_name": room_name})
        participant = await lkapi.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=room_name,
                sip_trunk_id=trunk_id,
                sip_call_to=destination_number,
                participant_identity=f"sip_{destination_number.replace('+', '')}_{uuid.uuid4().hex[:4]}",
                participant_name=recipient_name
            )
        )
        logger.info("Call initiated", extra={"sip_call_id": participant.sip_call_id})

        if wait_for_completion:
            logger.info("Monitoring call until hang-up", extra={"room_name": room_name})
            await asyncio.sleep(5)
            
            while True:
                participants = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
                if not participants.participants:
                    logger.info("Call ended because room is empty", extra={"room_name": room_name})
                    break
                
                active_humans = [p for p in participants.participants if p.identity.startswith("sip_")]
                if not active_humans:
                    logger.info("Participant hung up", extra={"room_name": room_name})
                    break
                    
                await asyncio.sleep(5)

    except Exception as e:
        import traceback
        logger.exception("Error initiating outbound call")
        raise
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python vobiz_outbound.py <phone_number> [name] [course]")
        logger.error("Example: python vobiz_outbound.py 8591454670 \"Aman\" \"Mobile Repairing\"")
        sys.exit(1)
        
    target_num = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "Student"
    course = sys.argv[3] if len(sys.argv) > 3 else "our technical programs"
    
    asyncio.run(make_outbound_call(target_num, name, course))
