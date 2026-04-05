# Implementation Plan for agent.py Fixes

## Summary

| # | Issue | Fix |
|---|-------|-----|
| 1 | TTS hardcoded to Hindi only | Add `ExpertInstituteAgent` class with language detection via `stt_node` override |
| 2 | No English speaker option | Update TTS with both `target_language_code` AND `speaker` on language switch |
| 3 | `asyncio.create_task()` may not complete | Use `ctx.add_shutdown_callback()` |
| 4 | Relative path for knowledge.txt | Use `os.path.join(os.path.dirname(__file__), "knowledge.txt")` |

---

## Implementation Details

### New imports needed (lines 1-18)

```python
from typing import AsyncIterable
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
    stt,
    ModelSettings,
    SpeechEventType,
    NOT_GIVEN,
)
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import groq
from livekit.plugins import sarvam
from livekit.plugins import silero
from livekit import rtc
```

---

### New Agent class (after imports, before `summarize_and_send_to_telegram`)

```python
class ExpertInstituteAgent(Agent):
    LANGUAGE_CONFIG = {
        "hi": {"lang": "hi-IN", "speaker": "shubh"},
        "en": {"lang": "en-IN", "speaker": "shubh"},
    }

    def __init__(self, instructions: str):
        super().__init__(instructions=instructions)
        self._current_lang = "hi-IN"

    async def stt_node(self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings) -> AsyncIterable[stt.SpeechEvent]:
        default_stt = super().stt_node(audio, model_settings)
        async for event in default_stt:
            if event.type in [SpeechEventType.INTERIM_TRANSCRIPT, SpeechEventType.FINAL_TRANSCRIPT]:
                if event.alternatives and event.alternatives[0].language:
                    lang = event.alternatives[0].language.split("-")[0]
                    config = self.LANGUAGE_CONFIG.get(lang, self.LANGUAGE_CONFIG["hi"])
                    if config["lang"] != self._current_lang:
                        self._current_lang = config["lang"]
                        self.session.tts.update_options(
                            target_language_code=config["lang"],
                            speaker=config["speaker"]
                        )
                        logger.info(f"Switched TTS to {config['lang']} with speaker {config['speaker']}")
            yield event
```

---

### Line 114 - Fix knowledge base path

```python
kb_path = os.path.join(os.path.dirname(__file__), "knowledge.txt")
```

---

### Line 166-172 - Update agent creation

```python
agent = ExpertInstituteAgent(
    instructions=initial_ctx.messages()[0].text_content,
)
# Remove stt/tts from Agent constructor (they're in session)
```

---

### Lines 206-209 - Fix async task (participant_disconnected handler)

Replace:
```python
@ctx.room.on("participant_disconnected")
def on_participant_disconnected(participant):
    logger.info(f"Participant disconnected: {participant.identity}. Generating and sending summary...")
    asyncio.create_task(summarize_and_send_to_telegram(session.history.messages()))
```

With:
```python
async def send_summary():
    await summarize_and_send_to_telegram(session.history.messages())

ctx.add_shutdown_callback(send_summary)
```

---

## Notes

- The initial greeting remains bilingual (English + Hindi) to ask the user for their language preference
- After the user responds, language is auto-detected from STT events and TTS is updated accordingly
- Both Hindi and English use the `shubh` speaker (works for both languages in bulbul:v3)
- Version 1.4.5 of livekit-plugins-sarvam supports `update_options(target_language_code=...)`
