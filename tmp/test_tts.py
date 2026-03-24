import os
from dotenv import load_dotenv
from livekit.plugins import sarvam
import asyncio

load_dotenv()

async def test_tts():
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        print("SARVAM_API_KEY not found")
        return

    try:
        tts = sarvam.TTS(
            api_key=api_key,
            target_language_code="hi-IN",
            model="bulbul:v3",
            speaker="shubh"
        )
        print("TTS initialized successfully")
        
        # Test synthesis if possible (requires more setup, but initialization check is a start)
        # async for event in tts.synthesize("नमस्ते"):
        #    print(f"Received event: {event.type}")
            
    except Exception as e:
        print(f"Error initializing TTS: {e}")

if __name__ == "__main__":
    asyncio.run(test_tts())
