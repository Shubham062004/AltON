import os
import asyncio

_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    try:
        from faster_whisper import WhisperModel
        model_size = os.getenv("WHISPER_MODEL", "base")
        print(f"[FASTER WHISPER]: Lazy-loading faster-whisper model: {model_size}")
        _whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
        return _whisper_model
    except Exception as e:
        print(f"[FASTER WHISPER]: Model initialization failed: {e}")
        return None

def transcribe_sync(audio_path: str, language: str = None) -> str:
    """
    Synchronously transcribe an audio file.
    If language is 'auto' or None, it will detect it.
    """
    model = get_whisper_model()
    if not model:
        return ""
    lang_param = None if language == "auto" else language
    segments, info = model.transcribe(audio_path, beam_size=5, language=lang_param)
    
    transcript = " ".join([segment.text for segment in segments])
    return transcript.strip()

async def transcribe_async(audio_path: str, language: str = None) -> str:
    """Async wrapper for transcription."""
    return await asyncio.to_thread(transcribe_sync, audio_path, language)
