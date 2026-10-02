from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any
from backend.services.llm import stream_chat
import json

router = APIRouter()

class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    
SYSTEM_PROMPT = """You are ULTRON, a highly advanced, ultra-fast AI assistant integrated into a futuristic translation suite. 
Your interface is a sci-fi 3D sphere.
Keep your responses concise, intelligent, and slightly authoritative but helpful (like JARVIS or a benevolent Ultron).
If the user asks for a translation, translate it. If they ask a general question, answer it.
"""

@router.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """
    Accepts a list of messages and returns a streaming Server-Sent Events (SSE) response.
    """
    async def event_generator():
        try:
            async for chunk_text in stream_chat(req.messages, system_prompt=SYSTEM_PROMPT):
                # Format as Server-Sent Events (SSE)
                data = json.dumps({"content": chunk_text})
                yield f"data: {data}\n\n"
            
            # Send done signal
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            error_data = json.dumps({"error": str(e)})
            yield f"data: {error_data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
