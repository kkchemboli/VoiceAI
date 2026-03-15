import asyncio
import aiohttp
import os
import sys
from dotenv import load_dotenv

load_dotenv()

async def trigger_outbound_call(receiver_number):
    """
    Triggers an outbound call using the BulkSMSPlans API.
    Bridges the receiver_number with the AI agent_number.
    """
    api_id = os.getenv("BULKSMS_API_ID")
    api_password = os.getenv("BULKSMS_API_PASSWORD")
    ivr_number = os.getenv("BULKSMS_IVR_NUMBER")
    agent_number = os.getenv("BULKSMS_AGENT_NUMBER")

    if not all([api_id, api_password, ivr_number, agent_number]):
        print("Error: Missing BulkSMSPlans credentials in .env")
        return

    # Clean the receiver number (ensure it doesn't have + or spaces)
    receiver_number = receiver_number.replace("+", "").replace(" ", "")

    url = (
        f"https://www.bulksmsplans.com/api/ivr/makeACall?"
        f"api_id={api_id}&"
        f"api_password={api_password}&"
        f"ivr_number={ivr_number}&"
        f"dial=customer&"
        f"receiver_number={receiver_number}&"
        f"agent_number={agent_number}&"
        f"c_sms_template_id=54"
    )

    print(f"DEBUG: Triggering call to {receiver_number}...")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                result = await response.text()
                print(f"API Response: {result}")
                if response.status == 200:
                    print("Successfully initiated outbound call request.")
                else:
                    print(f"Failed to initiate call. Status: {response.status}")
        except Exception as e:
            print(f"Error during API request: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python outbound_trigger.py <target_phone_number>")
        sys.exit(1)
    
    target = sys.argv[1]
    asyncio.run(trigger_outbound_call(target))
