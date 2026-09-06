import logging
from typing import AsyncIterable, Callable, Optional
from livekit import rtc
from livekit.agents import llm, stt
from livekit.agents.voice import Agent

from core.state import AgentState
from core.router import handle_user_turn_completed

logger = logging.getLogger("voice-agent")


class ExpertInstituteAgent(Agent):
    def __init__(
        self,
        fnc_ctx=None,
        rag_engine=None,
        on_user_activity: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.state = AgentState()
        self._fnc_ctx = fnc_ctx
        self._rag_engine = rag_engine
        self._on_user_activity = on_user_activity

    @property
    def _current_lang(self) -> Optional[str]:
        return self.state.current_lang

    @_current_lang.setter
    def _current_lang(self, value: Optional[str]):
        self.state.current_lang = value

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Called before the LLM generates a response."""
        await handle_user_turn_completed(turn_ctx, new_message, self._rag_engine, agent=self)

    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: any
    ) -> AsyncIterable[stt.SpeechEvent]:
        logger.info("STT node started processing audio...")
        default_stt = super().stt_node(audio, model_settings)
        try:
            async for event in default_stt:
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    if event.alternatives and event.alternatives[0].text:
                        text = event.alternatives[0].text.lower()
                        if self._on_user_activity:
                            self._on_user_activity(text)

                        stt_lang = event.alternatives[0].language
                        config = self.state.lock_language(text, stt_lang)

                        if config:
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

                yield event
        except Exception as e:
            logger.error(f"Error in stt_node: {e}")
            raise
