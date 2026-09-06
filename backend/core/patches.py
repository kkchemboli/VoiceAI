import logging
import av
import livekit.plugins.sarvam as sarvam
from livekit.agents.utils.codecs.decoder import AudioStreamDecoder

logger = logging.getLogger("voice-agent")

_patches_applied = False


def apply_audio_patches():
    """Apply FFmpeg, AudioDecoder, and Sarvam TTS monkey patches for audio playback stability."""
    global _patches_applied
    if _patches_applied:
        return

    logger.info("Applying audio playback stability monkey patches...")

    # 1. Patch FFmpeg probesize for MP3 streams
    _orig_av_open = av.open

    def _patched_av_open(*args, **kwargs):
        if kwargs.get("format") == "mp3" and "options" in kwargs:
            logger.info("AV OPEN: Detected MP3 stream, increasing probesize to 32KB")
            kwargs["options"]["probesize"] = "32768"
            kwargs["options"]["analyzeduration"] = "100000"  # 100ms
        return _orig_av_open(*args, **kwargs)

    av.open = _patched_av_open

    # 2. Patch AudioStreamDecoder for robustness when detection is missing
    _orig_decoder_push = AudioStreamDecoder.push

    def _patched_decoder_push(self, chunk: bytes) -> None:
        if (
            getattr(self, "_is_wav", False)
            and not getattr(self, "_started", False)
            and len(chunk) >= 4
            and not chunk.startswith(b"RIFF")
        ):
            logger.info(
                f"DECODER PATCH: Non-WAV data detected ({chunk[:4]!r}), switching to MP3 format."
            )
            self._is_wav = False
            self._av_format = "mp3"
        _orig_decoder_push(self, chunk)

    AudioStreamDecoder.push = _patched_decoder_push

    # 3. Patch Sarvam Plugin to report correct MIME type for v3 models
    def _patch_sarvam_stream(stream_class):
        _orig_run = stream_class._run

        async def _patched_run(self, output_emitter, *args, **kwargs):
            mime_type = "audio/wav"
            if "bulbul:v3" in self._opts.model:
                mime_type = "audio/mpeg"

            _orig_initialize = output_emitter.initialize

            def _patched_initialize(*args, **kwargs):
                if "mime_type" in kwargs:
                    kwargs["mime_type"] = mime_type
                elif len(args) >= 4:
                    args = list(args)
                    args[3] = mime_type
                return _orig_initialize(*args, **kwargs)

            output_emitter.initialize = _patched_initialize
            return await _orig_run(self, output_emitter, *args, **kwargs)

        stream_class._run = _patched_run

    _patch_sarvam_stream(sarvam.tts.SynthesizeStream)
    _patch_sarvam_stream(sarvam.tts.ChunkedStream)

    _patches_applied = True
    logger.info("Audio stability monkey patches successfully applied.")
