import asyncio
import os
import sys
from dotenv import load_dotenv
from livekit import api

load_dotenv()

async def make_outbound_call(destination_number):
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

    # Ensure number is in E.164 format (starts with +)
    if not destination_number.startswith('+'):
        destination_number = '+' + destination_number

    lkapi = api.LiveKitAPI(url, api_key, api_secret)

    print(f"Dialing {destination_number} via Trunk {trunk_id}...")
    print(f"Room: {room_name}")

    try:
        # Step 1: Dispatch the agent FIRST so it's ready in the room
        print(f"Dispatching agent 'agent' to room '{room_name}'...")
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="agent",
                room=room_name,
            )
        )
        print("Agent dispatched successfully!")

        # Step 2: Create SIP Participant (dial the phone number)
        print(f"Initiating SIP call to {destination_number}...")
        await lkapi.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=room_name,
                sip_trunk_id=trunk_id,
                sip_call_to=destination_number,
                participant_identity=f"sip_{destination_number.replace('+', '')}",
                participant_name="Outbound Caller"
            )
        )
        print(f"Call initiated! Answer the phone to hear the agent.")

    except Exception as e:
        print(f"Error initiating call: {e}")
    finally:
        await lkapi.aclose()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python vobiz_outbound.py <phone_number_with_country_code>")
        print("Example: python vobiz_outbound.py +917498952789")
        sys.exit(1)
        
    target_number = sys.argv[1]
    asyncio.run(make_outbound_call(target_number))
