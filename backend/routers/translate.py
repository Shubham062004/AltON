from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from deep_translator import GoogleTranslator
from deep_translator.exceptions import LanguageNotSupportedException
import asyncio

router = APIRouter()

# Get supported languages from deep-translator
_SUPPORTED = GoogleTranslator().get_supported_languages(as_dict=True)
LANGUAGES = {code: name.title() for name, code in _SUPPORTED.items()}
# Sort languages alphabetically by name
LANGUAGES_LIST = [{"code": code, "name": name} for code, name in sorted(LANGUAGES.items(), key=lambda kv: kv[1])]

class TranslateRequest(BaseModel):
    text: str
    source: str = "auto"
    target: str = "en"

@router.get("/languages")
async def get_languages():
    return LANGUAGES_LIST

@router.post("/translate")
async def translate_text(req: TranslateRequest):
    if not req.text.strip():
        return {"translated": "", "detected": None}

    try:
        # Run deep-translator in a separate thread since it's synchronous
        def run_translation():
            translator = GoogleTranslator(source=req.source, target=req.target)
            result = translator.translate(req.text)
            detected = None
            if req.source == "auto":
                try:
                    detected_code = GoogleTranslator(source="auto", target=req.target)._source
                    detected = LANGUAGES.get(detected_code, detected_code)
                except Exception:
                    pass
            return {"translated": result, "detected": detected}

        response = await asyncio.to_thread(run_translation)
        return response
    except LanguageNotSupportedException as e:
        raise HTTPException(status_code=400, detail=f"Language not supported: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail="Translation failed. Check internet connection.")
