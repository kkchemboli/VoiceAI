import os
import logging
from typing import Annotated, Optional

from livekit import agents, api
from livekit.agents import llm

logger = logging.getLogger("call-transfer")


class TransferFunctions(llm.ToolContext):
    def __init__(self, ctx: agents.JobContext, phone_number: str = None):
        super().__init__(tools=[])
        self.ctx = ctx
        self.phone_number = phone_number

    @llm.function_tool(description="Transfer the call to a human manager or support agent. Call this when the user requests a 'manager', 'senior', 'higher-up', or shows frustration that you cannot resolve. DO NOT call this for language choices.")
    async def transfer_call(self, destination: Annotated[Optional[str], "The phone number or SIP URI to transfer to."] = None):
        """
        Transfer the call to a different number.
        """
        logger.info(f"AI requested transfer to: {destination}")

        # 1. Handle placeholders or default
        placeholders = ["human", "support", "manager", "human_support_agent", "DEFAULT_TRANSFER_NUMBER"]
        if destination is None or str(destination).lower() in [p.lower() for p in placeholders]:
            destination = os.getenv("DEFAULT_TRANSFER_NUMBER")
            if not destination:
                return "Error: No default transfer number configured in .env."

        # 2. Format Destination
        if destination:
            destination = str(destination).strip()
            if destination.isdigit():
                destination = f"tel:+{destination}"
            elif destination.startswith("+"):
                destination = f"tel:{destination}"
            elif destination.startswith("sip:"):
                # Leave full SIP URIs alone if provided manually
                pass
            elif destination.startswith("tel:"):
                pass

        # 3. Identify the Participant to transfer dynamically from the room
        participant_identity = None
        
        # Look for the exact participant identity assigned by LiveKit
        for p in self.ctx.room.remote_participants.values():
            if p.identity.startswith("sip_"):
                participant_identity = p.identity
                break

        # Fallback 1: Any remote participant (since it's a 1-on-1 call)
        if not participant_identity and self.ctx.room.remote_participants:
            participant_identity = next(iter(self.ctx.room.remote_participants.values())).identity
            
        # Fallback 2: Reconstruct if room somehow doesn't have participants yet
        if not participant_identity and self.phone_number and self.phone_number.lower() != "unknown":
            participant_identity = f"sip_+{self.phone_number}"

        if not participant_identity:
            return "Error: Could not identify the caller for transfer."

        try:
            logger.info(f"Transferring {participant_identity} to {destination}")
            lkapi = api.LiveKitAPI(
                os.getenv("LIVEKIT_URL"),
                os.getenv("LIVEKIT_API_KEY"),
                os.getenv("LIVEKIT_API_SECRET")
            )
            try:
                await lkapi.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.ctx.room.name,
                        participant_identity=participant_identity,
                        transfer_to=destination,
                        play_dialtone=False
                    )
                )
            finally:
                await lkapi.aclose()
            return "Transfer initiated successfully."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return f"Error executing transfer: {e}"
