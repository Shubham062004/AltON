from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from deep_translator import GoogleTranslator
from PIL import Image
import pytesseract
import io
import asyncio

router = APIRouter()

@router.post("/ocr")
async def ocr_translate(
    image: UploadFile = File(...),
    target: str = Form("en")
):
    try:
        # Read image
        img_bytes = await image.read()
        img = Image.open(io.BytesIO(img_bytes))
        
        # Extract text via Tesseract
        def run_ocr():
            # Basic OCR extraction (assumes tesseract is in PATH)
            text = pytesseract.image_to_string(img)
            return text.strip()
            
        extracted_text = await asyncio.to_thread(run_ocr)
        
        if not extracted_text:
            return {"extracted": "", "translated": "No text detected in image."}
            
        # Translate the text
        translator = GoogleTranslator(source="auto", target=target)
        translated = translator.translate(extracted_text)
        
        return {
            "extracted": extracted_text,
            "translated": translated
        }
    except pytesseract.TesseractNotFoundError:
        raise HTTPException(status_code=500, detail="Tesseract OCR engine is not installed on the system.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
