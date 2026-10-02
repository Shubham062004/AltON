# ALton

ALton is a local AI assistant and translation desktop app for Windows. It combines a FastAPI backend, a sci-fi web interface, PyWebView desktop mode, speech tools, OCR, memory, web search, and plugin-based desktop automation.

This project was created with AI-assisted development and iterative prompting. The architecture, UI, features, and fixes were shaped by asking AI tools to generate, improve, debug, and refine the code, then testing it locally.

## Features

- AI chat assistant with streaming responses
- Multi-provider LLM routing through Ollama, Groq, Gemini, Anthropic, OpenAI, and NVIDIA when configured
- Real-time translation with automatic language support
- Text-to-speech and speech-to-text support
- OCR image scanning and translation
- Document upload support for TXT, Markdown, PDF, and DOCX
- Persistent memory using a local SQLite/vector-memory workflow
- Web search and Wikipedia context for current or factual questions
- Desktop automation helpers for launching apps, typing, browser actions, and system status
- Modular plugin system under `plugins/`
- Native desktop window mode using PyWebView

## Project Structure

```text
ALton/
  backend/              FastAPI app, routers, agents, services, memory, and execution logic
  frontend/             Static HTML, CSS, JavaScript, and visual assets
  plugins/              Local plugin modules for tools and integrations
  desktop_app.py        Native desktop wrapper using PyWebView
  requirements.txt      Python dependencies
  run.bat               Windows backend/web launcher
  run.ps1               PowerShell backend/web launcher
  run_desktop.bat       Windows desktop app launcher
  vercel.json           Optional deployment config
```

## Requirements

- Windows 10 or newer
- Python 3.10 or newer
- Tesseract OCR installed and available to `pytesseract` if you want OCR
- Optional: Ollama for local LLM inference
- Optional: API keys for Groq, Gemini, Anthropic, OpenAI, or NVIDIA

## Setup

Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

If you use Playwright-powered browser automation, install the browser runtime:

```powershell
python -m playwright install
```

Create a `.env` file in the project root if you want cloud LLM providers:

```env
GROQ_API_KEY=your_groq_key
GEMINI_API_KEY=your_gemini_key
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key
NVIDIA_API_KEY=your_nvidia_key
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

You do not need every key. The app tries available providers in order and can use Ollama locally if it is running.

## Run Locally

Start the web/backend version:

```powershell
.\run.bat
```

or:

```powershell
.\run.ps1
```

Then open:

```text
http://127.0.0.1:8080
```

Start the native desktop app:

```powershell
.\run_desktop.bat
```

## Cleanup

Before sharing or committing the project, remove generated and local-only files:

```powershell
Remove-Item -Recurse -Force __pycache__ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .pytest_cache -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force backend\__pycache__ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force frontend\__pycache__ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force plugins\__pycache__ -ErrorAction SilentlyContinue
```

Do not commit these files or folders:

- `venv/`
- `.env`
- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`
- local database files such as `*.db`
- generated audio files such as `*.mp3`, `*.wav`, and `*.m4a`
- browser/session folders such as `playwright_user_data/`

Optional files to review manually before sharing:

- `test_*.py` files if they are only local experiments
- `setup_modi_voice.py` if it is not part of the final app
- `backend/session_memory.json` if it contains private local memory
- `vercel.json` if you are not deploying this app to Vercel

## Optimization Ideas

- Move long prompts and provider settings out of `backend/main.py` into config files or dedicated service modules.
- Restrict CORS in production instead of using `allow_origins=["*"]`.
- Split the large `frontend/index.html` into smaller HTML, CSS, and JavaScript modules.
- Add a `.env.example` file with placeholder keys so setup is easier without exposing secrets.
- Add endpoint tests for translation, chat, memory, OCR, and plugin listing.
- Add startup checks for Tesseract, Ollama, API keys, and Playwright so missing dependencies are reported clearly.
- Cache supported languages and provider health checks to reduce repeated startup or request work.
- Add structured logging instead of scattered `print()` calls.
- Keep desktop automation behind explicit user confirmation when running actions that control the computer.
- Package the desktop version with PyInstaller or Briefcase when the app is ready to distribute.

## Notes

This app is intended for local development and personal use first. Review security, CORS, secrets handling, and desktop automation permissions carefully before deploying or sharing it publicly.
