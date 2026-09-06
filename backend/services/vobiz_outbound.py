import asyncio
import os
import sys
import uuid
import json
from dotenv import load_dotenv
from livekit import api

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
        print("Error: Missing LiveKit SIP configuration in .env")
        print("Please ensure LIVEKIT_SIP_OUTBOUND_TRUNK_ID is set.")
        return

    destination_number = destination_number.strip().replace(" ", "").replace("-", "")
    
    if len(destination_number) == 10 and destination_number.isdigit():
        print("Smart Formatting: Detected 10-digit number, assuming India (+91)")
        destination_number = '+91' + destination_number
    elif not destination_number.startswith('+'):
        destination_number = '+' + destination_number

    base_room = os.getenv("LIVEKIT_SIP_ROOM", "outbound-call")
    room_name = f"{base_room}-{uuid.uuid4().hex[:6]}"

    lkapi = api.LiveKitAPI(url, api_key, api_secret)

    print(f"Dialing {destination_number} for {recipient_name} regarding {target_course}...")
    print(f"Unique Room: {room_name}")

    metadata = json.dumps({
        "recipientName": recipient_name,
        "targetCourse": target_course
    })

    try:
        print("Dispatching agent 'outbound_caller' with metadata...")
        dispatch = await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="outbound_caller",
                room=room_name,
                metadata=metadata
            )
        )
        print(f"Agent dispatched successfully! Dispatch ID: {dispatch.id}")

        print(f"Initiating SIP call to {destination_number}...")
        participant = await lkapi.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=room_name,
                sip_trunk_id=trunk_id,
                sip_call_to=destination_number,
                participant_identity=f"sip_{destination_number.replace('+', '')}_{uuid.uuid4().hex[:4]}",
                participant_name=recipient_name
            )
        )
        print(f"Call initiated! SIP Call ID: {participant.sip_call_id}")

        if wait_for_completion:
            print(f"Monitoring call to {recipient_name}... Waiting for hang-up.")
            await asyncio.sleep(5)
            
            while True:
                participants = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
                if not participants.participants:
                    print(f"Call with {recipient_name} has ended (Room empty).")
                    break
                
                active_humans = [p for p in participants.participants if p.identity.startswith("sip_")]
                if not active_humans:
                    print(f"Student {recipient_name} has hung up. Cleaning up...")
                    break
                    
                await asyncio.sleep(5)

    except Exception as e:
        import traceback
        print(f"Error initiating call: {e}")
        traceback.print_exc()
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python vobiz_outbound.py <phone_number> [name] [course]")
        print("Example: python vobiz_outbound.py 8591454670 \"Aman\" \"Mobile Repairing\"")
        sys.exit(1)
        
    target_num = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else "Student"
    course = sys.argv[3] if len(sys.argv) > 3 else "our technical programs"
    
    asyncio.run(make_outbound_call(target_num, name, course))
