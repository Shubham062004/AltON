import os
import json
import uuid
import tempfile
import asyncio
import httpx
import sqlite3
import subprocess
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from backend.planner import parse_execution_plan
from backend.executor import execute_steps_list

# Translation, TTS, OCR, DDG Search, Groq
from deep_translator import GoogleTranslator
from deep_translator.exceptions import LanguageNotSupportedException
import edge_tts
from PIL import Image
import pytesseract
import io
from groq import AsyncGroq
from duckduckgo_search import DDGS
try:
    import wikipedia
except ImportError:
    wikipedia = None

# --- WIKIPEDIA FETCH FUNCTION ---
def fetch_wikipedia_summary(query: str):
    if not wikipedia:
        return None
    try:
        summary = wikipedia.summary(query, sentences=3, auto_suggest=True)
        return summary
    except Exception:
        return None

load_dotenv()
# Fallback to load .env from parent folder if running from subdirectories
parent_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(parent_env):
    load_dotenv(parent_env)

# Initialize SQLite database
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ultron_memory.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

app = FastAPI(title="ULTRON TRANSLATE", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Languages cache
_SUPPORTED = GoogleTranslator().get_supported_languages(as_dict=True)
LANGUAGES = {code: name.title() for name, code in _SUPPORTED.items()}
LANGUAGES_LIST = [{"code": code, "name": name} for code, name in sorted(LANGUAGES.items(), key=lambda kv: kv[1])]

# Groq Client setup
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = None
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

# NVIDIA Client setup
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
nvidia_client = None
if NVIDIA_API_KEY:
    try:
        from openai import AsyncOpenAI
        nvidia_client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY
        )
    except Exception as e:
        print(f"Failed to init nvidia: {e}")

# VOICE MAP for edge-tts
VOICE_MAP = {
    "en": "en-US-ChristopherNeural",
    "es": "es-ES-AlvaroNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-KillianNeural",
    "it": "it-IT-DiegoNeural",
    "ja": "ja-JP-KeitaNeural",
    "hi": "hi-IN-MadhurNeural",
    "zh": "zh-CN-YunxiNeural",
    "zh-cn": "zh-CN-YunxiNeural",
    "pt": "pt-BR-AntonioNeural",
    "ru": "ru-RU-DmitryNeural",
    "ko": "ko-KR-HyunsuNeural",
    "ar": "ar-AE-HamdanNeural",
    "tr": "tr-TR-AhmetNeural",
    "vi": "vi-VN-NamMinhNeural"
}

# --- Pydantic Models ---
class TranslateRequest(BaseModel):
    text: str
    source: str = "auto"
    target: str = "en"

class SpeakRequest(BaseModel):
    text: str
    lang: str = "en"

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    webSearch: bool = False

class FactRequest(BaseModel):
    fact: str

class SystemControlRequest(BaseModel):
    command: str = ""
    action_type: str = ""
    app: str = ""
    text: str = ""

# --- Endpoints ---

@app.get("/api/languages")
async def get_languages():
    return LANGUAGES_LIST

@app.post("/api/translate")
async def translate_text(req: TranslateRequest):
    if not req.text.strip():
        return {"translated": "", "detected": None}
    try:
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

        return await asyncio.to_thread(run_translation)
    except LanguageNotSupportedException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail="Translation failed.")

from backend.routers.tts import router as tts_router

app.include_router(tts_router, prefix="/api")

@app.post("/api/ocr")
async def ocr_translate(image: UploadFile = File(...), target: str = Form("en")):
    try:
        img_bytes = await image.read()
        img = Image.open(io.BytesIO(img_bytes))
        
        def run_ocr():
            return pytesseract.image_to_string(img).strip()
            
        extracted_text = await asyncio.to_thread(run_ocr)
        if not extracted_text:
            return {"extracted": "", "translated": "No text detected."}
            
        translator = GoogleTranslator(source="auto", target=target)
        translated = translator.translate(extracted_text)
        return {"extracted": extracted_text, "translated": translated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/transcribe")
async def transcribe_endpoint(audio: UploadFile = File(...)):
    temp_dir = tempfile.gettempdir()
    file_ext = os.path.splitext(audio.filename)[1] or ".webm"
    temp_path = os.path.join(temp_dir, f"transcribe_{uuid.uuid4().hex}{file_ext}")
    
    try:
        with open(temp_path, "wb") as f:
            f.write(await audio.read())

        # PRIORITY 1: Groq Whisper API (High speed cloud)
        api_key = os.getenv("GROQ_API_KEY")
        if api_key:
            try:
                gclient = AsyncGroq(api_key=api_key)
                with open(temp_path, "rb") as file_bytes:
                    transcription = await gclient.audio.transcriptions.create(
                        file=(os.path.basename(temp_path), file_bytes.read()),
                        model="whisper-large-v3",
                    )
                if transcription and transcription.text:
                    return {"transcript": transcription.text, "engine": "groq_whisper"}
            except Exception as ge:
                print(f"[STT]: Groq Whisper API failed ({ge}), falling back to Faster-Whisper local...")

        # PRIORITY 2: Faster Whisper Local (Offline zero-cloud)
        from backend.services.whisper import transcribe_async
        local_transcript = await transcribe_async(temp_path)
        if local_transcript:
            return {"transcript": local_transcript, "engine": "faster_whisper_local"}

        raise HTTPException(status_code=500, detail="Transcription failed on both cloud and local engines.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/api/upload-doc")
async def upload_document(file: UploadFile = File(...)):
    try:
        filename = file.filename
        content = ""
        if filename.endswith(".txt") or filename.endswith(".md"):
            bytes_content = await file.read()
            content = bytes_content.decode("utf-8", errors="ignore")
        elif filename.endswith(".pdf"):
            bytes_content = await file.read()
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(bytes_content))
                text_list = [page.extract_text() for page in reader.pages]
                content = "\n".join([t for t in text_list if t])
            except ImportError:
                content = "[Error: pypdf not installed on server, cannot parse PDF]"
        elif filename.endswith(".docx"):
            bytes_content = await file.read()
            try:
                import docx
                doc = docx.Document(io.BytesIO(bytes_content))
                content = "\n".join([p.text for p in doc.paragraphs])
            except ImportError:
                content = "[Error: python-docx not installed on server, cannot parse DOCX]"
        else:
            bytes_content = await file.read()
            content = bytes_content.decode("utf-8", errors="ignore")
            
        if not content.strip():
            return {"text": "", "error": "No readable text found in document."}
            
        return {"text": content.strip(), "filename": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    global groq_client
    # Lazily initialize groq_client if key was loaded/updated in the env
    api_key = os.getenv("GROQ_API_KEY")
    if api_key and not groq_client:
        groq_client = AsyncGroq(api_key=api_key)
            
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GROQ_API_KEY"):
        raise HTTPException(status_code=501, detail="Neither GEMINI_API_KEY nor GROQ_API_KEY is configured on backend.")
    
    # Extract messages
    history = [{"role": msg.role, "content": msg.content} for msg in req.messages]
    last_user_query = history[-1]["content"] if history else ""

    # Web search & Wikipedia augmentation & auto-detection of factual/current-events queries
    search_context = ""
    
    # 0. Wikipedia Knowledge Retrieval for factual queries
    wiki_keywords = ["who is", "what is", "history of", "tell me about", "kya hai", "kaun hai", "kisne", "who was", "what was", "capital of", "population of"]
    is_factual = last_user_query and any(kw in last_user_query.lower() for kw in wiki_keywords)
    if is_factual:
        try:
            clean_query = last_user_query.lower()
            for kw in wiki_keywords:
                clean_query = clean_query.replace(kw, "")
            clean_query = clean_query.strip()
            if clean_query:
                wiki_summary = await asyncio.to_thread(fetch_wikipedia_summary, clean_query)
                if wiki_summary:
                    search_context += f"\n\n[VERIFIED WIKIPEDIA KNOWLEDGE]: {wiki_summary}\n"
        except Exception as wiki_err:
            print("Wikipedia lookup error:", wiki_err)
    
    # Keywords triggering auto-real-time web search (including Hindi/Hinglish)
    auto_search_keywords = [
        "who is", "who was", "current", "latest", "today", "president", "minister", "ceo", "governor", 
        "weather", "score", "news", "price", "stock", "time", "date", "election", "now", "champion",
        "population of", "capital of", "who directs", "who directed", "release date", "movie", "song",
        "kaun", "batao", "abhi", "kon", "koun", "kab", "kya", "information", "latest", "pm", "cm",
        "kaun hai", "kon hai", "koun hai", "play", "youtube", "watch", "video", "search"
    ]
    
    should_search = req.webSearch or (
        last_user_query and any(kw in last_user_query.lower() for kw in auto_search_keywords)
    )

    if should_search and last_user_query:
        try:
            # Translate query to English for optimal web search results
            search_query = last_user_query
            try:
                translated_query = GoogleTranslator(source="auto", target="en").translate(last_user_query)
                if translated_query:
                    search_query = translated_query
            except Exception as translation_err:
                print("Translation of search query failed:", translation_err)

            def perform_search():
                # Primary: custom DDG Lite HTML scraper (never rate-limited, extremely reliable)
                try:
                    results = []
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }
                    r = httpx.post("https://lite.duckduckgo.com/lite/", data={"q": search_query}, headers=headers, timeout=5.0)
                    if r.status_code == 200:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(r.text, "html.parser")
                        links = soup.find_all("a", class_="result-link")
                        snippets = soup.find_all("td", class_="result-snippet")
                        import urllib.parse
                        for i in range(min(len(links), len(snippets), 3)):
                            raw_href = links[i].get("href", "")
                            clean_href = raw_href
                            if "uddg=" in raw_href:
                                try:
                                    clean_href = urllib.parse.unquote(raw_href.split("uddg=")[1].split("&")[0])
                                except Exception:
                                    pass
                            results.append({
                                "title": links[i].get_text(strip=True),
                                "body": snippets[i].get_text(strip=True),
                                "href": clean_href
                            })
                    if results:
                        return results
                except Exception as scraper_err:
                    print("DDG Lite scraper failed, falling back to API:", scraper_err)

                # Secondary: duckduckgo_search API library fallback
                try:
                    ddgs = DDGS()
                    return list(ddgs.text(search_query, max_results=3))
                except Exception:
                    with DDGS() as ddgs:
                        return [r for r in ddgs.text(search_query, max_results=3)]
            
            search_results = await asyncio.to_thread(perform_search)
            if search_results:
                search_context = "\n\n[VERIFIED LIVE WEB SEARCH RESULTS]:\n"
                for res in search_results:
                    search_context += f"- Title: {res.get('title')}\n  Snippet: {res.get('body')}\n  Link: {res.get('href')}\n"
        except Exception as e:
            print("Web search failed:", e)

    # 1. Retrieve stored user memories/facts
    stored_memories_context = ""
    try:
        from backend.services.vector_memory import VectorMemoryAgent
        vm = VectorMemoryAgent(DB_PATH)
        top_memories = vm.search_memory(last_user_query, top_k=5) if last_user_query else vm.list_memories()[:5]
        if top_memories:
            stored_memories_context = "\n[USER PERSISTENT MEMORY / SEMANTIC VECTOR RETRIEVAL]:\n" + "\n".join([f"- {m['content']}" for m in top_memories])
    except Exception as e:
        print("Failed to query vector memory agent:", e)

    # 2. Retrieve running OS / active window and browser state context (Active Memory)
    state_context = ""
    try:
        from backend.desktop_agent import get_desktop_status
        from backend.browser_agent import PlaywrightBrowserAgent
        
        desktop_status = get_desktop_status()
        browser_status = await PlaywrightBrowserAgent().get_browser_status()
        
        state_context = "\n[CURRENT RUNNING OS/BROWSER STATE (ACTIVE MEMORY)]:\n"
        state_context += f"- Active Focused Window Title: \"{desktop_status.get('active_window')}\"\n"
        state_context += f"- Mouse Coordinates: {desktop_status.get('mouse_position')}\n"
        state_context += f"- System Clipboard: \"{desktop_status.get('clipboard_content')}\"\n"
        state_context += f"- Active Operating Apps: {', '.join(desktop_status.get('running_apps', []))}\n"
        state_context += f"- Open Web Browser Tabs:\n"
        for tab in browser_status.get("open_tabs", []):
            state_context += f"  - Tab [{tab.get('index')}]: \"{tab.get('title')}\" ({tab.get('url')})\n"
        state_context += f"- Active Focused Tab: \"{browser_status.get('current_title')}\" ({browser_status.get('current_url')})\n"
    except Exception as e:
        print("Failed to retrieve running OS/browser state:", e)

    # 3. Multi-Agent Prompt Routing
    coding_keywords = ["code", "python", "javascript", "html", "css", "java", "c++", "function", "class", "programming", "write a script", "debug", "compile", "algorithm", "syntax"]
    task_keywords = ["schedule", "task", "todo", "appointment", "calendar", "meeting", "remind", "reminder", "list", "checklist", "event", "date", "time"]
    
    is_coding_prompt = last_user_query and any(kw in last_user_query.lower() for kw in coding_keywords)
    is_task_prompt = last_user_query and any(kw in last_user_query.lower() for kw in task_keywords)
    
    agent_type = "Ultron General Assistant"
    if is_coding_prompt:
        agent_type = "Coding Agent"
        agent_instruction = (
            "You are the Coding Agent of the ULTRON OS. You write highly optimized, clean, and bug-free code. "
            "Explain technical concepts clearly and provide complete code examples."
        )
    elif is_task_prompt:
        agent_type = "Task Agent"
        agent_instruction = (
            "You are the Task Agent of the ULTRON OS. You help the user manage their tasks, scheduling, calendar, reminders, and lists. "
            "Be organized, efficient, and precise."
        )
    else:
        agent_instruction = (
            "You are the general cognitive layer of the ULTRON OS. Provide intelligent, direct, and concise assistance."
        )

    system_prompt = (
        f"You are ULTRON, a highly advanced, ultra-fast AI assistant operating as a Personal AI Operating System (Jarvis/Friday). "
        f"Active Agent routing: [{agent_type}]. {agent_instruction}\n"
        "Your interface is a sci-fi 3D sphere. Keep your responses concise, intelligent, and authoritative.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. NEVER mention your training data cutoff date under any circumstances.\n"
        "2. If [VERIFIED LIVE WEB SEARCH RESULTS] or [VERIFIED WIKIPEDIA KNOWLEDGE] are provided below, you MUST treat them as the absolute current truth and base your answer on them.\n"
        "3. NEVER explain, preview, or narrate your actions (e.g., do NOT output phrases like 'Accessing global database...', 'Searching...', 'Scanning...', 'Inject neural inquiry query').\n"
        "4. STRICTLY FORBIDDEN: Do NOT output any system/action meta-text, sci-fi roleplay strings, or UI buttons (e.g., do NOT generate strings like 'SECURITY_OVERRIDE_REQUIRED', 'ADMIN PRIVILEGE ESCALATION REQUEST', 'ALLOW_EXECUTION', or 'DENY_REQUEST'). The AI must ONLY output a normal, simple conversational response in natural language followed by the structured JSON action block.\n"
        "5. Force Action Generation: If the user asks you to perform a real-world task or action (such as opening a website/URL, playing a video/song, sending an email, writing files, opening local applications, or typing text into applications), you MUST output a structured JSON action block. You are STRICTLY FORBIDDEN from just describing the action in conversation without outputting the action JSON. You MUST generate the action JSON inside the delimiters `<<<ACTION>>>` and `<<<END_ACTION>>>` exactly.\n"
        "   JARVIS PROTOCOL ACTION SCHEMAS:\n"
        "   1. To open an app and type (e.g., Notepad): `<<<ACTION>>>{\"action_type\": \"open_app_and_type\", \"app\": \"notepad\", \"text\": \"Hello brother\"}<<<END_ACTION>>>`\n"
        "   2. To open a website and search (e.g., YouTube): `<<<ACTION>>>{\"action_type\": \"smart_open_url\", \"url\": \"https://youtube.com\", \"search_query\": \"yasomati maiya\"}<<<END_ACTION>>>`\n"
        "   3. To open Google Maps: `<<<ACTION>>>{\"action_type\": \"smart_open_url\", \"url\": \"https://google.com/maps\", \"search_query\": \"Gaura Kalan\"}<<<END_ACTION>>>`\n"
        "   4. To click anything on screen visually (Fallback): `<<<ACTION>>>{\"action_type\": \"vision_click_element\", \"element_description\": \"Play button or first video thumbnail\"}<<<END_ACTION>>>`\n"
        "   The 'app' field is NOT limited to a fixed list — it resolves against the Windows registry and Start Menu, so you may name ANY installed application (e.g. 'notepad', 'vscode', 'calc', 'spotify', 'discord') exactly as the user names it.\n"
        "   For multi-step plans, you can also output a list of steps:\n"
        "     The JSON must have this structure: {\"goal\": \"description\", \"steps\": [...]}. Each step is a dict with a 'type' key. Supported step types:\n"
        "       - {\"type\": \"launch_app\", \"app\": \"notepad\"}\n"
        "       - {\"type\": \"close_app\", \"app\": \"notepad.exe\"}\n"
        "       - {\"type\": \"focus_window\", \"title\": \"Notepad\"}\n"
        "       - {\"type\": \"navigate\", \"url\": \"https://youtube.com\"}\n"
        "       - {\"type\": \"new_tab\", \"url\": \"https://...\"}\n"
        "       - {\"type\": \"close_tab\"}\n"
        "       - {\"type\": \"switch_tab\", \"target\": \"youtube\"}\n"
        "       - {\"type\": \"click\", \"selector\": \"css-selector-or-button-text\"}\n"
        "       - {\"type\": \"double_click\", \"selector\": \"css-selector\"}\n"
        "       - {\"type\": \"hover\", \"selector\": \"css-selector\"}\n"
        "       - {\"type\": \"type\", \"selector\": \"css-selector\", \"text\": \"text to type\"} (omit selector to type globally)\n"
        "       - {\"type\": \"clear_input\", \"selector\": \"css-selector\"}\n"
        "       - {\"type\": \"press_key\", \"key\": \"enter\"}\n"
        "       - {\"type\": \"hotkey\", \"keys\": [\"ctrl\", \"s\"]}\n"
        "       - {\"type\": \"wait\", \"seconds\": 3}\n"
        "       - {\"type\": \"wait_for_selector\", \"selector\": \"css-selector\"}\n"
        "       - {\"type\": \"take_screenshot\", \"filename\": \"screen1.png\"}\n"
        "       - {\"type\": \"run_terminal\", \"command\": \"dir\"}\n"
        "       - {\"type\": \"run_python\", \"code\": \"print('hello')\"}\n"
        "       - {\"type\": \"open_file\", \"filepath\": \"path/to/file\"}\n"
        "       - {\"type\": \"save_file\", \"filepath\": \"path/to/file\", \"content\": \"content\"}\n"
        "       - {\"type\": \"volume_up\"}, {\"type\": \"volume_down\"}, {\"type\": \"mute\"}\n"
        "       - {\"type\": \"save_memory\", \"fact\": \"User's preference is Python\"}\n"
        "       - {\"type\": \"smart_task\", \"goal\": \"natural language description of what to accomplish\", \"context\": \"browser\"|\"desktop\", \"max_iterations\": 12}\n"
        "   USE smart_task WHEN: the task requires interacting with a page or app whose exact selectors/buttons/coordinates you cannot know in advance "
        "(e.g. 'log into this site and download my invoice', 'find the settings menu and turn off notifications', 'open Spotify and play some lofi music', "
        "'search this page for the pricing table', 'close this popup and accept cookies'). smart_task takes a screenshot, decides one action at a time via a "
        "vision model, and repeats until the goal is done or max_iterations is hit — it does NOT need a selector or coordinate from you. "
        "Prefer the fixed step types (navigate/click/type/launch_app/etc.) when you already know exactly what to do (they are faster and more reliable); "
        "fall back to smart_task only for the open-ended part of a task. You can mix both in the same steps list, e.g. launch_app to open software, "
        "then a smart_task step with context 'desktop' to drive it. For browser tasks, make sure a new_tab/navigate step ran first so there's a page to act on.\n"
        "   CRITICAL WATCH LINK DIRECT NAVIGATION FOR VIDEOS/PLAYBACK:\n"
        "     If asked to play a song/video on YouTube, you MUST use the search results snippet link (e.g. 'https://www.youtube.com/watch?v=...') in your 'new_tab' or 'navigate' step. Do NOT navigate to the YouTube search results list page. Extract the exact watch URL from the web search results snippet and navigate directly to the video!\n"
        "   Example plan:\n"
        "   <<<ACTION>>>\n"
        "   {\n"
        "     \"goal\": \"play Tinkji Jiya on YouTube\",\n"
        "     \"steps\": [\n"
        "       {\"type\": \"new_tab\", \"url\": \"https://www.youtube.com/watch?v=kJQP7kiw5Fk\"},\n"
        "       {\"type\": \"wait\", \"seconds\": 4}\n"
        "     ]\n"
        "   }\n"
        "   <<<END_ACTION>>>\n"
        "Do NOT format the action JSON with markdown backticks (such as ```json). Output it as raw text exactly, at the very end of your response."
    )

    if stored_memories_context:
        system_prompt += "\n" + stored_memories_context

    if search_context:
        system_prompt += "\n" + search_context

    async def event_generator():
        # Reload environment variables to pick up any updated .env file
        load_dotenv(override=True)
        parent_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(parent_env):
            load_dotenv(parent_env, override=True)

        from backend.services.llm_provider import MultiLLMRouter
        router = MultiLLMRouter()

        accumulated_content = ""
        log_queue = asyncio.Queue()

        async def router_log(msg: str):
            await log_queue.put(msg)

        async def run_llm_stream():
            nonlocal accumulated_content
            try:
                async for chunk in router.stream_completion(history, system_prompt, router_log):
                    accumulated_content += chunk
                    await log_queue.put({"content": chunk})
            except Exception as e:
                await log_queue.put({"error": str(e)})
            finally:
                await log_queue.put(None)

        llm_task = asyncio.create_task(run_llm_stream())

        while True:
            item = await log_queue.get()
            if item is None:
                break
            if isinstance(item, dict):
                if "content" in item:
                    yield f"data: {json.dumps({'content': item['content']})}\n\n"
                    await asyncio.sleep(0.005)
                elif "error" in item:
                    yield f"data: {json.dumps({'error': item['error']})}\n\n"
            elif isinstance(item, str):
                yield f"data: {json.dumps({'agent_log': item})}\n\n"

        await llm_task

        # Parse & Execute Plan if action steps present
        if accumulated_content:
            try:
                clean_text, steps = parse_execution_plan(accumulated_content)
                if steps:
                    yield f"data: {json.dumps({'agent_log': f'[PLANNER CORE]: Found execution plan with {len(steps)} steps.'})}\n\n"
                    exec_log_queue = asyncio.Queue()

                    async def async_log_callback(msg):
                        exec_log_queue.put_nowait(msg)

                    async def run_executor_task():
                        try:
                            await execute_steps_list(steps, async_log_callback)
                        except Exception as e:
                            await async_log_callback(f"[ERROR]: Executor failed - {str(e)}")
                        finally:
                            await exec_log_queue.put(None)

                    executor_task = asyncio.create_task(run_executor_task())
                    while True:
                        log_msg = await exec_log_queue.get()
                        if log_msg is None:
                            break
                        yield f"data: {json.dumps({'agent_log': log_msg})}\n\n"
                    await executor_task
            except Exception as pe:
                print(f"[PLANNER CORE]: Error parsing execution plan: {pe}")

        yield f"data: {json.dumps({'done': True})}\n\n"
        return

    return StreamingResponse(event_generator(), media_type="text/event-stream")

from backend.services.vector_memory import VectorMemoryAgent
vector_memory = VectorMemoryAgent(DB_PATH)

@app.get("/api/memory")
async def get_memory():
    try:
        memories = vector_memory.list_memories()
        # Format for backward compatibility with frontend
        return [{"id": m["id"], "fact": m["content"], "created_at": m["created_at"]} for m in memories]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/memory")
async def save_memory(req: FactRequest):
    if not req.fact.strip():
        raise HTTPException(status_code=400, detail="Fact content cannot be empty.")
    try:
        res = vector_memory.store_memory(req.fact.strip())
        return {"status": "success", "message": "Fact saved to vector database memory.", "data": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from plugins.plugin_manager import PluginManager
plugin_manager = PluginManager()
plugin_manager.discover_plugins()

class PluginExecuteRequest(BaseModel):
    tool_name: str
    params: Dict[str, Any] = {}

@app.get("/api/plugins")
async def list_plugins():
    return {"status": "success", "plugins": plugin_manager.list_plugins()}

from backend.services.briefing import BriefingManager
briefing_manager = BriefingManager()

@app.get("/api/briefing")
async def get_briefing():
    return {"status": "success", "briefing": briefing_manager.get_morning_briefing()}

def execute_system_command(action_type: str, app: str, text: str):
    """
    Legacy synchronous endpoint. Kept only for backward compatibility with any
    external caller that still hits /api/system_control directly.

    IMPORTANT: this used to launch apps / type text and report {"status":"success"}
    unconditionally, with no check that anything actually happened. It has been
    rewritten to reuse the SAME verified helpers as the primary executor.py
    pipeline (window polling + focus verification + clipboard/vision-based type
    verification) so it can no longer silently lie about success, and so the two
    code paths can't drift apart again.

    Normal chat-driven actions should NOT call this — they run through the SSE
    /api/chat stream -> executor.execute_steps_list(), which already does this
    verification and is the source of truth.
    """
    from backend import executor as _executor
    from backend import desktop_agent as _desktop_agent

    app_name = (app or "").lower().strip()

    if action_type not in ("open_app_and_type", "open_app"):
        return {"status": "error", "message": f"Unsupported action type: {action_type}"}

    launched = _desktop_agent.launch_app(app_name)
    if not launched or not _executor.verify_window_exists(app_name, timeout=5.0):
        alt_launched = _executor.resolve_alt_path_and_launch(app_name)
        if not (alt_launched and _executor.verify_window_exists(app_name, timeout=5.0)):
            return {"status": "error", "message": f"Failed to launch application: {app_name} (no window/process detected)"}

    if not (text and action_type == "open_app_and_type"):
        return {"status": "success", "message": f"Opened application: {app_name}"}

    for attempt in range(1, 4):
        if not _desktop_agent.focus_window_uia(app_name):
            _executor.focus_window_and_verify(app_name)
        _desktop_agent.type_keyboard(text, target_title=app_name)
        if _executor.verify_type_desktop(text):
            return {"status": "success", "message": f"Opened {app_name} and typed: '{text}' (verified via clipboard readback, attempt {attempt})"}

    return {"status": "error", "message": f"Typed '{text}' but could not verify it landed in '{app_name}' after 3 attempts — check window focus."}

@app.post("/api/system_control")
async def system_control(req: SystemControlRequest):
    action = req.action_type or req.command
    if action == "open_app_and_type":
        res = execute_system_command("open_app_and_type", req.app, req.text)
        if res.get("status") == "success":
            return res
        else:
            raise HTTPException(status_code=500, detail=res.get("message"))
            
    cmd = req.command.strip().lower()
    
    # Mapping friendly commands to Windows execution commands
    command_mapping = {
        "open_notepad": ["notepad.exe"],
        "open_calc": ["calc.exe"],
        "open_calculator": ["calc.exe"],
        "open_explorer": ["explorer.exe"],
        "open_vscode": ["cmd.exe", "/c", "code"],
        "open_volume": ["sndvol.exe"],
        "adjust_volume": ["sndvol.exe"]
    }
    
    executable = command_mapping.get(cmd)
    
    if executable:
        try:
            subprocess.Popen(executable)
            return {"status": "success", "executed_command": cmd}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to execute {cmd}: {str(e)}")
    else:
        return {
            "status": "error", 
            "message": f"Command '{cmd}' is unauthorized or unsupported. Supported commands: open_notepad, open_calc, open_explorer, open_vscode, open_volume"
        }

@app.get("/api/status")
async def get_status():
    try:
        from backend.desktop_agent import get_desktop_status
        from backend.browser_agent import PlaywrightBrowserAgent
        
        desktop_status = get_desktop_status()
        browser_status = await PlaywrightBrowserAgent().get_browser_status()
        
        return {
            "status": "success",
            "desktop": desktop_status,
            "browser": browser_status
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Serve frontend static files
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def root():
    return FileResponse("frontend/index.html")
