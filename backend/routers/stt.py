from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from deep_translator import GoogleTranslator
import tempfile
import os
import uuid
from backend.services.whisper import transcribe_async

router = APIRouter()

@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    spoken_lang: str = Form("en"),
    target: str = Form("en")
):
    if not audio:
        raise HTTPException(status_code=400, detail="No audio file received")

    # Save uploaded file temporarily
    temp_dir = tempfile.gettempdir()
    file_ext = os.path.splitext(audio.filename)[1] or ".webm"
    temp_path = os.path.join(temp_dir, f"upload_{uuid.uuid4().hex}{file_ext}")
    
    try:
        with open(temp_path, "wb") as f:
            f.write(await audio.read())
            
        # Transcribe with faster-whisper
        transcript = await transcribe_async(temp_path, spoken_lang)
        
        if not transcript:
            raise HTTPException(status_code=422, detail="Couldn't make out any speech.")
            
        # Translate the transcript
        translator = GoogleTranslator(source=spoken_lang if spoken_lang != "auto" else "auto", target=target)
        translated = translator.translate(transcript)
        
        return {"transcript": transcript, "translated": translated}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
