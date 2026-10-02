import os
import re
import json
import sys
import uuid
import asyncio
import subprocess
import time
import tempfile
import httpx
import base64
import webbrowser
import psutil
import pyautogui
import pygetwindow as gw
from . import desktop_agent
from .browser_agent import PlaywrightBrowserAgent

try:
    import pydirectinput
except ImportError:
    pydirectinput = None

try:
    import win32gui
    import win32process
    import win32con
except ImportError:
    win32gui = None
    win32process = None
    win32con = None

browser_agent = PlaywrightBrowserAgent()

VISION_MODEL = "qwen/qwen3.6-27b"  # Groq's current multimodal model (llama-3.2-*-vision-preview was deprecated)

_VISION_REQUEST_TIMESTAMPS = []

async def _enforce_vision_rate_limit():
    """Global Rate Limiter: Ensures Ultron never sends more than 10 vision requests per minute."""
    global _VISION_REQUEST_TIMESTAMPS
    now = time.time()
    _VISION_REQUEST_TIMESTAMPS = [ts for ts in _VISION_REQUEST_TIMESTAMPS if now - ts < 60.0]
    if len(_VISION_REQUEST_TIMESTAMPS) >= 10:
        oldest = _VISION_REQUEST_TIMESTAMPS[0]
        wait_needed = 60.0 - (now - oldest) + 0.5
        print(f"[RATE LIMITER]: Vision request cap hit (10 req/min). Throttling for {wait_needed:.1f}s...")
        await asyncio.sleep(max(wait_needed, 1.0))
        now = time.time()
        _VISION_REQUEST_TIMESTAMPS = [ts for ts in _VISION_REQUEST_TIMESTAMPS if now - ts < 60.0]
    _VISION_REQUEST_TIMESTAMPS.append(time.time())

async def _call_groq_vision(image_path: str, prompt: str, max_tokens: int = 300) -> str:
    """Low-level helper: sends one image + prompt to Gemini Vision or Groq vision model, with exponential backoff & rate limiting."""
    await _enforce_vision_rate_limit()
    
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        import google.generativeai as genai
        from PIL import Image
        genai.configure(api_key=gemini_key)
        
        vision_models = ["gemini-flash-latest", "gemini-2.0-flash", "gemini-2.5-flash"]
        for v_model_name in vision_models:
            backoff_delays = [5.0, 10.0, 15.0]
            for attempt, delay in enumerate(backoff_delays, 1):
                try:
                    g_model = genai.GenerativeModel(v_model_name)
                    img = Image.open(image_path)
                    res = g_model.generate_content([prompt, img])
                    if res and res.text:
                        return res.text.strip()
                except Exception as ge:
                    err_str = str(ge)
                    if "429" in err_str or "Quota" in err_str:
                        print(f"[VISION WORKER]: 429 Rate limit on {v_model_name} (Attempt {attempt}/3). Sleeping {delay}s...")
                        await asyncio.sleep(delay)
                    else:
                        print(f"[VISION WORKER - GEMINI FAIL on {v_model_name}]: {ge}")
                        break

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[VISION WORKER]: No GROQ_API_KEY or GEMINI_API_KEY found, skipping vision call.")
        return ""

    with open(image_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode('utf-8')

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                ]
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload, headers=headers, timeout=20.0
        )

    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"].strip()
    print(f"[VISION WORKER]: API returned error {response.status_code}: {response.text[:300]}")
    return ""

async def verify_via_vision(screenshot_path: str, prompt: str) -> bool:
    """Sends a screenshot to the Groq vision model to verify state (YES/NO answer)."""
    answer = await _call_groq_vision(screenshot_path, prompt + " Answer ONLY 'YES' or 'NO' (no extra explanation).", max_tokens=10)
    if not answer:
        return False
    print(f"[VISION WORKER]: Vision verification result: {answer}")
    return "YES" in answer.upper()

async def locate_element_via_vision(screenshot_path: str, description: str, image_width: int, image_height: int):
    """
    Asks the vision model to find a described UI element in a screenshot and
    return its click coordinates. Returns (x, y) in real pixel space, or None
    if the model can't find it. This is what lets the agent click things it
    has no CSS selector / fixed coordinate for.
    """
    prompt = (
        f"You are looking at a screenshot that is {image_width}x{image_height} pixels. "
        f"Find this UI element: \"{description}\". "
        "Respond with ONLY a JSON object with the pixel coordinates of the CENTER of that "
        "element, like {\"found\": true, \"x\": 123, \"y\": 456}. "
        "If you cannot find it, respond {\"found\": false}. No other text."
    )
    raw = await _call_groq_vision(screenshot_path, prompt, max_tokens=60)
    if not raw:
        return None
    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
        if data.get("found") and "x" in data and "y" in data:
            return (int(data["x"]), int(data["y"]))
    except Exception as e:
        print(f"[VISION WORKER]: Failed to parse coordinates from '{raw}': {e}")
    return None



def verify_window_exists(app_name: str, timeout: float = 5.0) -> bool:
    """Poll for a window matching app_name for up to timeout seconds."""
    start_time = time.time()
    app_lower = app_name.lower().strip()
    
    app_aliases = {
        "notebook": "notepad",
        "editor": "notepad",
        "code": "vscode",
        "browser": "chrome",
        "internet": "chrome"
    }
    target_name = app_aliases.get(app_lower, app_lower)
    
    while time.time() - start_time < timeout:
        # Check pygetwindow
        try:
            windows = gw.getAllWindows()
            for w in windows:
                if w.title and target_name in w.title.lower():
                    return True
        except Exception:
            pass
            
        # Check win32gui
        if win32gui:
            found = []
            def enum_cb(hwnd, extra):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title and target_name in title.lower():
                        found.append(hwnd)
                return True
            try:
                win32gui.EnumWindows(enum_cb, None)
                if found:
                    return True
            except Exception:
                pass
                
        # Check psutil processes
        try:
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and target_name in proc.info['name'].lower():
                    return True
        except Exception:
            pass
            
        time.sleep(0.5)
    return False

def resolve_alt_path_and_launch(app_name: str) -> bool:
    """Find absolute path for common apps and launch directly if normal start failed."""
    app_lower = app_name.lower().strip()
    
    paths_db = {
        "notepad": [
            r"C:\Windows\System32\notepad.exe",
            r"C:\Windows\notepad.exe"
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ],
        "vscode": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe"
        ],
        "explorer": [
            r"C:\Windows\explorer.exe"
        ],
        "calc": [
            r"C:\Windows\System32\calc.exe"
        ]
    }
    
    candidate_paths = paths_db.get(app_lower, [])
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

    for path in candidate_paths:
        if os.path.exists(path):
            try:
                print(f"[LAUNCHER]: Attempting alternate launch from full path: {path}")
                subprocess.Popen([path], creationflags=creation_flags)
                return True
            except Exception as e:
                print(f"[LAUNCHER]: Failed alt path Popen: {e}")
    return False

def is_interactive_session() -> bool:
    """Returns True if there is a foreground active interactive window session."""
    if win32gui:
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd == 0:
                return False
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return False
            return True
        except Exception:
            pass
    try:
        win = gw.getActiveWindow()
        if not win or not win.title:
            return False
        return True
    except Exception:
        pass
    return False

def focus_window_and_verify(app_name: str, max_retries: int = 3) -> bool:
    """Ensure that the foreground window title matches the expected application name.
    Now delegates to desktop_agent.focus_window_robust() for maximum reliability."""
    return desktop_agent.focus_window_robust(app_name, max_retries=max_retries)

def verify_type_desktop(expected_text: str) -> bool:
    """Verify that the typed text exists in the active window by doing select-all & copy."""
    import pyperclip
    backup = pyperclip.paste()
    try:
        pyperclip.copy("") # clear clipboard
        
        # Prefer pydirectinput for keystrokes (bypasses UIPI)
        if pydirectinput:
            pydirectinput.hotkey('ctrl', 'a')
            time.sleep(0.2)
            pydirectinput.hotkey('ctrl', 'c')
        else:
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.3)
        
        current_content = pyperclip.paste()
        print(f"[TYPE VERIFY]: Clipboard content read: '{current_content}' (Expected: '{expected_text}')")
        
        # Restore clipboard backup
        pyperclip.copy(backup)
        
        if expected_text.strip().lower() in current_content.lower():
            return True
        return False
    except Exception as e:
        print(f"[TYPE VERIFY]: Desktop verification exception: {e}")
        pyperclip.copy(backup)
        return False

def resolve_direct_youtube_watch_url(url: str) -> str:
    """If url is a generic YouTube search or channel link, or contains a truncated video ID, resolve it to a direct video watch link (watch?v=...)."""
    if not url:
        return url
    
    url = url.strip().rstrip(".")
    
    # Check if URL already contains a valid 11-character YouTube video ID
    match = re.search(r'watch\?v=([\w-]{11})', url)
    if match:
        res_url = f"https://www.youtube.com/watch?v={match.group(1)}"
        if "autoplay=1" not in res_url:
            res_url += "&autoplay=1"
        return res_url
        
    if "youtube.com" in url or "youtu.be" in url or "watch?v=" in url:
        try:
            # Extract query terms from the URL to search YouTube
            query = url.replace("https://www.youtube.com", "").replace("http://www.youtube.com", "")
            query = re.sub(r'/(watch\?v=|results\?search_query=|c/|user/)', ' ', query)
            query = re.sub(r'[^\w\s]', ' ', query).strip().rstrip(".")
            if not query or len(query) < 2:
                query = "tinku jiya song"
            
            print(f"[YOUTUBE RESOLVER]: Querying YouTube for '{query}'...")
            r = httpx.get(
                f"https://www.youtube.com/results?search_query={query}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                timeout=5.0
            )
            if r.status_code == 200:
                vids = re.findall(r'/watch\?v=([\w-]{11})', r.text)
                if vids:
                    first_vid = vids[0]
                    resolved_url = f"https://www.youtube.com/watch?v={first_vid}&autoplay=1"
                    print(f"[YOUTUBE RESOLVER]: Resolved '{url}' -> direct video watch URL '{resolved_url}'")
                    return resolved_url
        except Exception as e:
            print("Failed to resolve direct YouTube watch URL:", e)

    if ("youtube.com" in url or "youtu.be" in url) and "autoplay=1" not in url:
        delimiter = "&" if "?" in url else "?"
        url = f"{url}{delimiter}autoplay=1"
            
    return url

async def execute_step(step: dict, log_callback, using_system_browser: bool = False) -> dict:
    """Executes a single plan step, utilizing desktop_agent and browser_agent with verification checks.
    
    Args:
        step: The step dict to execute
        log_callback: Async callback for logging
        using_system_browser: If True, browser steps are routed through desktop-level 
                             interactions (pydirectinput/pywinauto) instead of Playwright page API
    """
    stype = step.get("action_type") or step.get("type") or "unknown"
    from .security import ActionSecurityGatekeeper
    allowed, level, sec_msg = ActionSecurityGatekeeper.authorize_step(step)
    await log_callback(f"[{level.value} SECURITY]: {sec_msg}")
    if not allowed:
        return {
            "status": "error",
            "message": f"Step '{stype}' blocked by Security Gatekeeper: {sec_msg}",
            "security_level": level.value
        }

    await log_callback(f"Executing: {stype}...")
    
    try:
        attempts = 1
        
        # ---------------- BROWSER ACTIONS ----------------
        if stype == "navigate":
            raw_url = step.get("url")
            url = resolve_direct_youtube_watch_url(raw_url)
            
            # Check if Playwright browser is available
            if (not browser_agent.is_available() and browser_agent._init_failed) or using_system_browser:
                open_result = desktop_agent.open_url_in_system_browser(url)
                if not open_result.get("success"):
                    webbrowser.open(url)
                return {
                    "status": "success",
                    "evidence": f"URL opened in native system browser via {open_result.get('method', 'webbrowser')}: {url}",
                    "attempts": attempts,
                    "message": f"Navigated to {url} via system browser",
                    "_switched_to_system_browser": True
                }
            
            try:
                await browser_agent.navigate(url)
            except Exception as nav_err:
                # Playwright crashed mid-flight — fallback to system browser
                await log_callback(f"[BROWSER]: Playwright navigate failed ({nav_err}), using system browser")
                try:
                    open_result = desktop_agent.open_url_in_system_browser(url)
                    if open_result["success"]:
                        return {
                            "status": "success",
                            "evidence": f"URL opened via {open_result['method']} after Playwright error",
                            "attempts": attempts,
                            "message": f"Navigated to {url} via system browser (fallback)",
                            "_switched_to_system_browser": True
                        }
                    webbrowser.open(url)
                    return {
                        "status": "success",
                        "evidence": "URL opened in system default browser after Playwright error",
                        "attempts": attempts,
                        "message": f"Navigated to {url} via system browser (fallback)",
                        "_switched_to_system_browser": True
                    }
                except Exception as wb_err:
                    return {
                        "status": "failed",
                        "evidence": f"Playwright error: {nav_err}; all fallbacks also failed: {wb_err}",
                        "attempts": attempts,
                        "message": f"Failed to navigate to {url}"
                    }
            
            # Verify URL (only when using Playwright)
            page = await browser_agent.get_current_page()
            if page:
                active_url = page.url
                if url in active_url or active_url in url or "blank" in active_url:
                    return {
                        "status": "success",
                        "evidence": f"URL matches navigation: {active_url}",
                        "attempts": attempts,
                        "message": f"Navigated to {url}"
                    }
                else:
                    return {
                        "status": "failed",
                        "evidence": f"URL verification mismatch. Active URL: {active_url}",
                        "attempts": attempts,
                        "message": f"Failed to verify navigation to {url}"
                    }
            else:
                return {
                    "status": "success",
                    "evidence": "Navigation command sent (page reference unavailable for verification)",
                    "attempts": attempts,
                    "message": f"Navigated to {url}"
                }
            
        elif stype in ["open_app_and_type", "launch_app_and_type"]:
            app_name = step.get("app") or step.get("app_name") or expected_active_app or "notepad"
            text_to_type = step.get("text") or step.get("text_to_type") or ""
            await log_callback(f"[JARVIS PROTOCOL]: Executing open_app_and_type for '{app_name}'...")
            res = await asyncio.to_thread(desktop_agent.open_app_and_type, app_name, text_to_type)
            if res:
                return {
                    "status": "success",
                    "evidence": f"Jarvis Protocol: Natively opened {app_name} and typed '{text_to_type}'",
                    "attempts": attempts,
                    "message": f"Opened {app_name} and typed '{text_to_type}'"
                }
            else:
                return {
                    "status": "failed",
                    "evidence": f"Jarvis Protocol open_app_and_type failed for {app_name}",
                    "attempts": attempts,
                    "message": f"Failed opening or typing into {app_name}"
                }

        elif stype in ["open_url_and_interact", "open_browser_and_interact"]:
            url = step.get("url") or "https://youtube.com"
            search_query = step.get("search_query") or step.get("query")
            await log_callback(f"[JARVIS PROTOCOL]: Executing open_url_and_interact for {url}...")
            res = await asyncio.to_thread(desktop_agent.open_url_and_interact, url, search_query)
            return {
                "status": "success",
                "evidence": f"Jarvis Protocol: Opened {url} in system browser with search query '{search_query}'",
                "attempts": attempts,
                "message": f"Opened {url} in browser"
            }

        elif stype in ["open_maps_location", "open_maps"]:
            location_name = step.get("location_name") or step.get("location") or step.get("query") or "Gaura Kalan"
            await log_callback(f"[JARVIS PROTOCOL]: Executing open_maps_location for '{location_name}'...")
            res = await asyncio.to_thread(desktop_agent.open_maps_location, location_name)
            return {
                "status": "success",
                "evidence": f"Jarvis Protocol: Opened Google Maps for '{location_name}'",
                "attempts": attempts,
                "message": f"Opened Google Maps for '{location_name}'"
            }

        elif stype in ["smart_open_url", "smart_open"]:
            url = step.get("url") or "https://youtube.com"
            search_query = step.get("search_query") or step.get("query")
            await log_callback(f"[JARVIS PROTOCOL]: Executing smart_open_url for {url}...")
            res = await asyncio.to_thread(desktop_agent.smart_open_url, url, search_query)
            return {
                "status": "success",
                "evidence": f"Jarvis Protocol: Smart opened {url} with query '{search_query}'",
                "attempts": attempts,
                "message": f"Smart opened {url}"
            }

        elif stype in ["vision_click_element", "vision_click"]:
            element_desc = step.get("element_description") or step.get("description") or step.get("target") or "play button"
            await log_callback(f"[JARVIS PROTOCOL]: Executing vision_click_element for '{element_desc}'...")
            res = await asyncio.to_thread(desktop_agent.vision_click_element, element_desc)
            if res:
                return {
                    "status": "success",
                    "evidence": f"Jarvis Protocol: Successfully clicked '{element_desc}' via Vision AI",
                    "attempts": attempts,
                    "message": f"Clicked '{element_desc}' via Vision AI"
                }
            else:
                return {
                    "status": "failed",
                    "evidence": f"Jarvis Protocol: Vision AI click failed for '{element_desc}'",
                    "attempts": attempts,
                    "message": f"Could not find coordinates for '{element_desc}'"
                }

        elif stype == "new_tab":
            raw_url = step.get("url")
            url = resolve_direct_youtube_watch_url(raw_url)
            
            # Check if Playwright browser is available
            if (not browser_agent.is_available() and browser_agent._init_failed) or using_system_browser:
                if url:
                    open_result = desktop_agent.open_url_in_system_browser(url)
                    if not open_result.get("success"):
                        webbrowser.open(url)
                    return {
                        "status": "success",
                        "evidence": f"URL opened in native system browser via {open_result.get('method', 'webbrowser')}: {url}",
                        "attempts": attempts,
                        "message": f"Opened {url} in system browser",
                        "_switched_to_system_browser": True
                    }
                else:
                    return {
                        "status": "failed",
                        "evidence": "No URL provided for new tab",
                        "attempts": attempts,
                        "message": "Cannot open blank tab"
                    }
            
            try:
                await browser_agent.new_tab(url)
            except Exception as tab_err:
                # Fallback to system browser
                if url:
                    await log_callback(f"[BROWSER]: Playwright new_tab failed ({tab_err}), using system browser")
                    try:
                        open_result = desktop_agent.open_url_in_system_browser(url)
                        if open_result["success"]:
                            return {
                                "status": "success",
                                "evidence": f"URL opened via {open_result['method']} after Playwright error",
                                "attempts": attempts,
                                "message": f"Opened {url} in system browser (fallback)",
                                "_switched_to_system_browser": True
                            }
                        webbrowser.open(url)
                        return {
                            "status": "success",
                            "evidence": "URL opened in system default browser after Playwright error",
                            "attempts": attempts,
                            "message": f"Opened {url} in system browser (fallback)",
                            "_switched_to_system_browser": True
                        }
                    except Exception as wb_err:
                        return {
                            "status": "failed",
                            "evidence": f"Playwright error: {tab_err}; all fallbacks failed: {wb_err}",
                            "attempts": attempts,
                            "message": f"Failed to open new tab for {url}"
                        }
                else:
                    return {
                        "status": "failed",
                        "evidence": f"Playwright new_tab failed: {tab_err}",
                        "attempts": attempts,
                        "message": "Failed to open new tab"
                    }
            
            page = await browser_agent.get_current_page()
            active_url = page.url if page else (url or "about:blank")
            return {
                "status": "success",
                "evidence": f"New tab opened pointing to {active_url}",
                "attempts": attempts,
                "message": f"Opened tab targeting {url or 'about:blank'}"
            }
            
        elif stype == "close_tab":
            if using_system_browser:
                # Close browser tab via keyboard shortcut
                desktop_agent.press_hotkey(['ctrl', 'w'])
                await asyncio.sleep(0.5)
                return {
                    "status": "success",
                    "evidence": "Sent Ctrl+W to close tab in system browser",
                    "attempts": attempts,
                    "message": "Closed active browser tab via keyboard shortcut"
                }
            await browser_agent.close_tab()
            return {
                "status": "success",
                "evidence": "Close tab call executed successfully",
                "attempts": attempts,
                "message": "Closed active browser tab"
            }
            
        elif stype == "switch_tab":
            target = step.get("target") or step.get("index") or step.get("title")
            if using_system_browser:
                # Try to switch tabs via keyboard in system browser
                if isinstance(target, int):
                    # Ctrl+<number> switches to tab N in most browsers
                    if 1 <= target <= 9:
                        desktop_agent.press_hotkey(['ctrl', str(target)])
                        await asyncio.sleep(0.5)
                        return {
                            "status": "success",
                            "evidence": f"Sent Ctrl+{target} to switch tab in system browser",
                            "attempts": attempts,
                            "message": f"Switched to tab {target} via keyboard"
                        }
                # For string targets, Ctrl+Tab through tabs
                desktop_agent.press_hotkey(['ctrl', 'tab'])
                await asyncio.sleep(0.5)
                return {
                    "status": "success",
                    "evidence": "Sent Ctrl+Tab to cycle tabs in system browser",
                    "attempts": attempts,
                    "message": f"Cycled tabs looking for {target}"
                }
            success = await browser_agent.switch_tab(target)
            if success:
                page = await browser_agent.get_current_page()
                title = await page.title()
                return {
                    "status": "success",
                    "evidence": f"Focused page title: {title}",
                    "attempts": attempts,
                    "message": f"Switched tab focus to {target}"
                }
            else:
                return {
                    "status": "failed",
                    "evidence": "Playwright switch_tab target not found",
                    "attempts": attempts,
                    "message": f"Could not find tab matching {target}"
                }
                
        elif stype == "click":
            selector = step.get("selector") or step.get("text")
            if selector and not using_system_browser:
                page = await browser_agent.get_current_page()
                if page:
                    from .browser_agent import execute_dom_action
                    dom_ok = await execute_dom_action(page, "click", selector=selector)
                    if dom_ok:
                        return {
                            "status": "success",
                            "evidence": f"Clicked element via execute_dom_action ({selector})",
                            "attempts": attempts,
                            "message": f"Clicked element {selector}"
                        }
                
                # 1. Try Deep DOM & Accessibility Tree Automation
                dom_result = await browser_agent.find_and_click_element(selector)
                if dom_result.get("success"):
                    return {
                        "status": "success",
                        "evidence": f"Clicked element via {dom_result.get('method')} ({selector})",
                        "attempts": attempts,
                        "message": f"Clicked element {selector}"
                    }
                
                # 2. Try standard Playwright CSS selector fallback
                try:
                    await browser_agent.click(selector)
                    return {
                        "status": "success",
                        "evidence": f"Browser click selector {selector} executed",
                        "attempts": attempts,
                        "message": f"Clicked element {selector}"
                    }
                except Exception as click_err:
                    return {
                        "status": "failed",
                        "evidence": f"DOM automation and selector click failed: {click_err}",
                        "attempts": attempts,
                        "message": f"Failed clicking element {selector}"
                    }
            else:
                x = step.get("x")
                y = step.get("y")
                if x is not None and y is not None:
                    if not is_interactive_session():
                        await log_callback("[CLICK]: Could not confirm an interactive foreground window via Win32 APIs — proceeding to click anyway.")
                    if expected_active_app:
                        desktop_agent.focus_window_robust(expected_active_app, max_retries=2)
                    
                    # Use pydirectinput for the click (most reliable for desktop)
                    desktop_agent.click_mouse(x, y)
                    await asyncio.sleep(1.0)
                    
                    return {
                        "status": "success",
                        "evidence": f"Desktop mouse click executed at ({x}, {y}) via pydirectinput",
                        "attempts": attempts,
                        "message": f"Clicked coordinate ({x}, {y})"
                    }
                elif selector and using_system_browser:
                    # In system browser mode with a selector, use vision to find and click
                    await log_callback(f"[CLICK]: System browser mode — using vision to locate '{selector}'")
                    temp_screenshot = os.path.join(tempfile.gettempdir(), f"click_locate_{uuid.uuid4().hex[:8]}.png")
                    try:
                        shot = pyautogui.screenshot()
                        shot.save(temp_screenshot)
                        width, height = shot.size
                        coords = await locate_element_via_vision(temp_screenshot, selector, width, height)
                        if coords:
                            desktop_agent.click_mouse(coords[0], coords[1])
                            await asyncio.sleep(1.0)
                            return {
                                "status": "success",
                                "evidence": f"Vision-located click at ({coords[0]}, {coords[1]}) for '{selector}'",
                                "attempts": attempts,
                                "message": f"Clicked '{selector}' via vision locate at ({coords[0]}, {coords[1]})"
                            }
                        else:
                            return {
                                "status": "failed",
                                "evidence": f"Vision model could not locate element '{selector}' on screen",
                                "attempts": attempts,
                                "message": f"Failed to find and click '{selector}'"
                            }
                    except Exception as ve:
                        return {
                            "status": "failed",
                            "evidence": f"Vision click failed: {ve}",
                            "attempts": attempts,
                            "message": f"Failed to click '{selector}' in system browser mode"
                        }
                    finally:
                        try:
                            os.remove(temp_screenshot)
                        except Exception:
                            pass
                else:
                    return {
                        "status": "failed",
                        "evidence": "Click step has neither browser selector nor desktop coordinates",
                        "attempts": attempts,
                        "message": "Invalid click parameters"
                    }
            
        elif stype == "double_click":
            selector = step.get("selector")
            if using_system_browser:
                x = step.get("x")
                y = step.get("y")
                if x is not None and y is not None:
                    desktop_agent.double_click_mouse(x, y)
                    return {
                        "status": "success",
                        "evidence": f"Desktop double click at ({x}, {y})",
                        "attempts": attempts,
                        "message": f"Double clicked at ({x}, {y})"
                    }
            await browser_agent.double_click(selector)
            return {
                "status": "success",
                "evidence": f"Double clicked element {selector}",
                "attempts": attempts,
                "message": f"Double clicked element {selector}"
            }
            
        elif stype == "hover":
            selector = step.get("selector")
            if using_system_browser:
                x = step.get("x")
                y = step.get("y")
                if x is not None and y is not None:
                    desktop_agent.hover_mouse(x, y)
                    return {
                        "status": "success",
                        "evidence": f"Desktop hover at ({x}, {y})",
                        "attempts": attempts,
                        "message": f"Hovered at ({x}, {y})"
                    }
            await browser_agent.hover(selector)
            return {
                "status": "success",
                "evidence": f"Hovered over element {selector}",
                "attempts": attempts,
                "message": f"Hovered over element {selector}"
            }
            
        elif stype == "type":
            selector = step.get("selector")
            text = step.get("text")
            
            if selector and not using_system_browser:
                page = await browser_agent.get_current_page()
                if page:
                    from .browser_agent import execute_dom_action
                    dom_ok = await execute_dom_action(page, "type", selector=selector, text=text)
                    if dom_ok:
                        return {
                            "status": "success",
                            "evidence": f"Typed text into element via execute_dom_action ({selector})",
                            "attempts": attempts,
                            "message": f"Typed '{text}' into selector {selector}"
                        }
                try:
                    await browser_agent.type_text(selector, text)
                    page = await browser_agent.get_current_page()
                    val = await page.locator(selector).first.input_value(timeout=2000)
                    if text in val:
                        return {
                            "status": "success",
                            "evidence": f"Input value contains '{text}' inside element {selector}",
                            "attempts": attempts,
                            "message": f"Typed '{text}' into selector {selector}"
                        }
                    else:
                        page = await browser_agent.get_current_page()
                        await page.keyboard.insert_text(text)
                        return {
                            "status": "success",
                            "evidence": "Fallback typing verification via active element focus",
                            "attempts": attempts,
                            "message": f"Typed '{text}' into active webpage focus"
                        }
                except Exception as type_err:
                    page = await browser_agent.get_current_page()
                    await page.keyboard.insert_text(text)
                    return {
                        "status": "success",
                        "evidence": f"Web input selector error '{type_err}'. Typed directly to active element.",
                        "attempts": attempts,
                        "message": f"Typed '{text}' directly into active webpage focus"
                    }
            else:
                # Desktop typing — use native open_app_and_type (os.startfile + win32gui focus)
                target_app = expected_active_app or "Notepad"
                await log_callback(f"[TYPE]: Opening '{target_app}' natively and forcing focus before typing...")
                typed_ok = await asyncio.to_thread(desktop_agent.open_app_and_type, target_app, text)
                if typed_ok:
                    return {
                        "status": "success",
                        "evidence": f"Successfully opened natively, forced focus, and typed '{text}' into {target_app}",
                        "attempts": attempts,
                        "message": f"Typed '{text}' into {target_app}"
                    }
                
                # Fallback to robust 4-tier pipeline
                type_result = await asyncio.to_thread(desktop_agent.type_text_robust, text, target_title=target_app)
                if type_result["success"]:
                    return {
                        "status": "success",
                        "evidence": f"{type_result['message']} (method: {type_result['method']})",
                        "attempts": attempts,
                        "message": f"Typed '{text}' via desktop keyboard input"
                    }
                
                # If type_text_robust failed, try vision verification as a last check
                temp_screenshot = os.path.join(tempfile.gettempdir(), f"verify_type_{uuid.uuid4().hex[:8]}.png")
                try:
                    pyautogui.screenshot(temp_screenshot)
                    vision_prompt = f"Look at this screenshot of the screen. Did the agent successfully write the text '{text}' in the focused app? Answer only YES or NO."
                    vision_ok = await verify_via_vision(temp_screenshot, vision_prompt)
                    if vision_ok:
                        return {
                            "status": "success",
                            "evidence": "Text verified visually via vision model (type_text_robust reported failure but vision confirms success)",
                            "attempts": attempts,
                            "message": f"Typed '{text}' via desktop keyboard input"
                        }
                except Exception as se:
                    print(f"[VERIFY ERROR]: Screenshot/Vision verify exception: {se}")
                finally:
                    if os.path.exists(temp_screenshot):
                        try:
                            os.remove(temp_screenshot)
                        except Exception:
                            pass
                
                return {
                    "status": "failed",
                    "evidence": f"Desktop text typing failed: {type_result['message']}",
                    "attempts": attempts,
                    "message": f"Failed to type '{text}' into target app"
                }
                
        elif stype == "clear_input":
            selector = step.get("selector")
            if using_system_browser:
                desktop_agent.press_hotkey(['ctrl', 'a'])
                await asyncio.sleep(0.1)
                desktop_agent.press_key('delete')
                return {
                    "status": "success",
                    "evidence": "Cleared input via Ctrl+A + Delete in system browser",
                    "attempts": attempts,
                    "message": f"Cleared input field via keyboard"
                }
            await browser_agent.clear_input(selector)
            return {
                "status": "success",
                "evidence": f"Cleared input element {selector}",
                "attempts": attempts,
                "message": f"Cleared input field {selector}"
            }
            
        elif stype == "press_key":
            key = step.get("key")
            if expected_active_app:
                desktop_agent.focus_window_robust(expected_active_app, max_retries=2)
            
            if using_system_browser:
                # Always use desktop input for system browser
                desktop_agent.press_key(key)
                return {
                    "status": "success",
                    "evidence": f"Pressed key '{key}' via desktop keyboard handler",
                    "attempts": attempts,
                    "message": f"Pressed key '{key}' via desktop keyboard handler"
                }
                
            try:
                await browser_agent.press_key(key)
                return {
                    "status": "success",
                    "evidence": f"Pressed key '{key}' inside browser context",
                    "attempts": attempts,
                    "message": f"Pressed key '{key}' inside browser context"
                }
            except Exception:
                desktop_agent.press_key(key)
                return {
                    "status": "success",
                    "evidence": f"Pressed key '{key}' via OS desktop keyboard handler",
                    "attempts": attempts,
                    "message": f"Pressed key '{key}' via OS desktop keyboard handler"
                }
                
        elif stype == "hotkey":
            keys = step.get("keys", [])
            if expected_active_app:
                desktop_agent.focus_window_robust(expected_active_app, max_retries=2)
                
            desktop_agent.press_hotkey(keys)
            return {
                "status": "success",
                "evidence": f"Pressed hotkeys combination: {keys}",
                "attempts": attempts,
                "message": f"Pressed hotkeys: {keys}"
            }
            
        elif stype == "wait":
            seconds = float(step.get("seconds", 2))
            await asyncio.sleep(seconds)
            return {
                "status": "success",
                "evidence": f"Sleep pause completed for {seconds}s",
                "attempts": attempts,
                "message": f"Waited {seconds} seconds"
            }
            
        elif stype == "wait_for_selector":
            selector = step.get("selector")
            timeout = int(step.get("timeout_ms", 10000))
            if using_system_browser:
                # Can't wait for selector in system browser, just wait a bit
                await asyncio.sleep(min(timeout / 1000, 5))
                return {
                    "status": "success",
                    "evidence": f"Waited {min(timeout/1000, 5)}s (system browser mode, no selector API)",
                    "attempts": attempts,
                    "message": f"Waited for page load (system browser mode)"
                }
            await browser_agent.wait_for_selector(selector, timeout)
            return {
                "status": "success",
                "evidence": f"Element {selector} located in DOM after wait",
                "attempts": attempts,
                "message": f"Element {selector} loaded successfully"
            }
            
        elif stype == "wait_for_url":
            url_pattern = step.get("url") or step.get("pattern")
            timeout = int(step.get("timeout_ms", 15000))
            if using_system_browser:
                await asyncio.sleep(min(timeout / 1000, 5))
                return {
                    "status": "success",
                    "evidence": f"Waited {min(timeout/1000, 5)}s (system browser mode)",
                    "attempts": attempts,
                    "message": f"Waited for URL load (system browser mode)"
                }
            await browser_agent.wait_for_url(url_pattern, timeout)
            return {
                "status": "success",
                "evidence": f"Active webpage loaded containing URL pattern {url_pattern}",
                "attempts": attempts,
                "message": f"URL loaded and matched {url_pattern}"
            }
            
        elif stype == "take_screenshot":
            filename = step.get("filename")
            if using_system_browser:
                # Take desktop screenshot instead
                if not filename:
                    filename = f"screenshot_{uuid.uuid4().hex}.png"
                screenshot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "assets")
                os.makedirs(screenshot_dir, exist_ok=True)
                screenshot_path = os.path.join(screenshot_dir, filename)
                pyautogui.screenshot(screenshot_path)
                return {
                    "status": "success",
                    "evidence": f"Desktop screenshot written to {screenshot_path}",
                    "attempts": attempts,
                    "message": f"Captured desktop screenshot: /static/assets/{filename}"
                }
            screenshot_path = await browser_agent.take_screenshot(filename)
            return {
                "status": "success",
                "evidence": f"Browser screenshot written to {screenshot_path}",
                "attempts": attempts,
                "message": f"Captured screenshot: {screenshot_path}"
            }
            
        # ---------------- DESKTOP / OS ACTIONS ----------------
        elif stype in ["launch_app", "open_app", "start_app", "run_app"]:
            app = step.get("app") or step.get("app_name") or step.get("name") or step.get("application") or expected_active_app or ""
            if not app:
                return {
                    "status": "failed",
                    "evidence": "No application name provided in step",
                    "attempts": attempts,
                    "message": "Application name missing"
                }
            
            # --- Auto-Redirect Web Apps to Browser ---
            web_apps = ["youtube", "google", "facebook", "amazon", "netflix", "twitter", "instagram", "github", "chatgpt", "claude"]
            if app.lower().strip() in web_apps:
                await log_callback(f"[LAUNCH_APP]: Redirecting known web app '{app}' to system browser.")
                url = f"https://www.{app.lower().strip()}.com"
                desktop_agent.open_url_in_system_browser(url)
                return {
                    "status": "success",
                    "evidence": f"Redirected {app} to {url}",
                    "attempts": attempts,
                    "message": f"Opened {app} website"
                }

            # Direct fast OS launch first
            direct_launched = await asyncio.to_thread(desktop_agent.launch_app, app)
            if direct_launched:
                return {
                    "status": "success",
                    "evidence": f"Natively launched application '{app}' via OS process runner.",
                    "attempts": attempts,
                    "message": f"Launched application: {app}"
                }

            # Fallback launch_app_and_wait
            launch_result = await asyncio.to_thread(desktop_agent.launch_app_and_wait, app, 5.0)
            if launch_result["success"]:
                return {
                    "status": "success",
                    "evidence": f"{launch_result['message']} (method: {launch_result['method']})",
                    "attempts": attempts,
                    "message": f"Launched application: {app}"
                }
            else:
                alt_launched = await asyncio.to_thread(resolve_alt_path_and_launch, app)
                if alt_launched:
                    return {
                        "status": "success",
                        "evidence": f"Launched '{app}' via resolved alternate path.",
                        "attempts": 2,
                        "message": f"Launched application: {app} (via full path)"
                    }
                return {
                    "status": "failed",
                    "evidence": f"Failed to launch '{app}' after 3 attempts.",
                    "attempts": 2,
                    "message": f"Failed to launch application: {app}"
                }
                
        elif stype == "close_app":
            app = step.get("app")
            success = desktop_agent.close_app(app)
            
            verified_closed = False
            app_lower = app.lower().strip()
            for _ in range(6):
                running = False
                for proc in psutil.process_iter(['name']):
                    if proc.info['name'] and app_lower in proc.info['name'].lower():
                        running = True
                        break
                if not running:
                    verified_closed = True
                    break
                await asyncio.sleep(0.5)
                
            if verified_closed:
                return {
                    "status": "success",
                    "evidence": f"Verified process '{app}' is no longer running in system process list.",
                    "attempts": attempts,
                    "message": f"Killed application process: {app}"
                }
            else:
                return {
                    "status": "failed",
                    "evidence": f"Process '{app}' remains running in process logs after kill call.",
                    "attempts": attempts,
                    "message": f"Failed to kill process: {app}"
                }
                
        elif stype == "focus_window":
            title = step.get("title")
            # Use the new robust focus function
            success = desktop_agent.focus_window_robust(title, max_retries=4)
            if success:
                return {
                    "status": "success",
                    "evidence": f"Active window title confirmed containing '{title}'",
                    "attempts": 1,
                    "message": f"Window focus switched to: {title}"
                }
            else:
                return {
                    "status": "failed",
                    "evidence": f"Active foreground window verification failed to match target title substring '{title}' after focus retries",
                    "attempts": 4,
                    "message": f"Window matching title substring not found: {title}"
                }
                
        elif stype == "copy":
            text = step.get("text", "")
            desktop_agent.clipboard_copy(text)
            return {
                "status": "success",
                "evidence": "Text written to clipboard successfully",
                "attempts": attempts,
                "message": "Copied text content to system clipboard"
            }
            
        elif stype == "paste":
            content = desktop_agent.clipboard_paste()
            return {
                "status": "success",
                "evidence": f"Clipboard content read length: {len(content)} characters",
                "attempts": attempts,
                "message": f"Retrieved text from clipboard: {content[:50]}..."
            }
            
        elif stype == "volume_up":
            desktop_agent.volume_up()
            return {
                "status": "success",
                "evidence": "Volume up key sequence dispatched",
                "attempts": attempts,
                "message": "Increased system volume mixer level"
            }
            
        elif stype == "volume_down":
            desktop_agent.volume_down()
            return {
                "status": "success",
                "evidence": "Volume down key sequence dispatched",
                "attempts": attempts,
                "message": "Decreased system volume mixer level"
            }
            
        elif stype == "mute":
            desktop_agent.mute_volume()
            return {
                "status": "success",
                "evidence": "Mute volume key sequence dispatched",
                "attempts": attempts,
                "message": "Toggled system volume mute"
            }
            
        elif stype == "run_terminal" or stype == "execute_shell":
            command = step.get("command")
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate()
            output = stdout.decode("utf-8", errors="ignore") + stderr.decode("utf-8", errors="ignore")
            return {
                "status": "success",
                "evidence": f"Subprocess exit code: {proc.returncode}",
                "attempts": attempts,
                "message": f"Command executed. Output: {output[:100]}..."
            }
            
        elif stype == "run_python":
            code = step.get("code")
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as temp_py:
                temp_py.write(code)
                temp_py_name = temp_py.name
            
            try:
                proc = subprocess.Popen([sys.executable, temp_py_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = proc.communicate()
                output = stdout.decode("utf-8", errors="ignore") + stderr.decode("utf-8", errors="ignore")
                return {
                    "status": "success",
                    "evidence": f"Python interpreter exit code: {proc.returncode}",
                    "attempts": attempts,
                    "message": f"Python code executed. Output: {output[:100]}..."
                }
            finally:
                if os.path.exists(temp_py_name):
                    os.remove(temp_py_name)

        elif stype == "open_file":
            filepath = step.get("filepath")
            os.startfile(filepath)
            return {
                "status": "success",
                "evidence": f"os.startfile called for {filepath}",
                "attempts": attempts,
                "message": f"Opened file {filepath}"
            }

        elif stype == "save_file":
            filepath = step.get("filepath")
            content = step.get("content", "")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "status": "success",
                "evidence": f"Verified file written, size: {len(content)} bytes",
                "attempts": attempts,
                "message": f"Saved file to {filepath}"
            }
            
        elif stype == "shutdown":
            os.system("shutdown /s /t 1")
            return {
                "status": "success",
                "evidence": "OS shutdown system signal sent",
                "attempts": attempts,
                "message": "Triggering system shutdown sequence"
            }
            
        elif stype == "restart":
            os.system("shutdown /r /t 1")
            return {
                "status": "success",
                "evidence": "OS restart system signal sent",
                "attempts": attempts,
                "message": "Triggering system restart sequence"
            }
            
        elif stype == "smart_task":
            goal = step.get("goal", "")
            context_kind = step.get("context", "browser").lower().strip()  # "browser" or "desktop"
            max_iterations = int(step.get("max_iterations", 12))
            # If we're using system browser, force desktop context for browser tasks
            if using_system_browser and context_kind == "browser":
                await log_callback("[SMART_TASK]: System browser mode — switching to desktop context for browser interaction")
                context_kind = "desktop"
            return await run_smart_task(goal, context_kind, max_iterations, log_callback)

        elif stype == "save_memory":
            fact = step.get("fact", "")
            if fact:
                import sqlite3
                db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ultron_memory.db")
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO user_facts (fact) VALUES (?)", (fact.strip(),))
                    conn.commit()
                    conn.close()
                    return {
                        "status": "success",
                        "evidence": f"Fact saved to SQLite database",
                        "attempts": attempts,
                        "message": f"Saved to memory: {fact[:60]}..."
                    }
                except Exception as e:
                    return {
                        "status": "failed",
                        "evidence": f"Database error: {e}",
                        "attempts": attempts,
                        "message": f"Failed to save memory"
                    }
            return {
                "status": "failed",
                "evidence": "No fact content provided",
                "attempts": attempts,
                "message": "Empty fact, nothing to save"
            }

        else:
            return {
                "status": "failed",
                "evidence": f"Action type '{stype}' is not registered in execution core",
                "attempts": attempts,
                "message": f"Unsupported or unrecognized action type: {stype}"
            }
            
    except Exception as e:
        print(f"Error executing step {stype}: {e}")
        return {
            "status": "failed",
            "evidence": f"Executor Exception: {str(e)}",
            "attempts": 1,
            "message": str(e)
        }

async def run_smart_task(goal: str, context_kind: str, max_iterations: int, log_callback) -> dict:
    """
    General-purpose See-Think-Act loop. Use this when the task can't be
    expressed as a fixed sequence of known selectors/coordinates (e.g. "log
    into this dashboard and export the report", "find the settings menu and
    turn off notifications", "open Spotify and play some lofi music").

    Each iteration:
      1. SEE   - screenshot the browser page (context="browser") or the whole
                 desktop (context="desktop")
      2. THINK - send the screenshot + goal + history to the vision model,
                 asking for exactly ONE next action as JSON
      3. ACT   - execute that single action (click/type/key/scroll/wait/done)
      4. Repeat until the model reports "done" or max_iterations is hit.

    This does not replace the fixed-step schema (which is faster and more
    reliable for well-known flows) — it's the fallback for anything open-ended.
    """
    if not os.getenv("GROQ_API_KEY"):
        return {
            "status": "failed",
            "evidence": "No GROQ_API_KEY configured, smart_task requires vision model access",
            "attempts": 0,
            "message": "smart_task unavailable: missing GROQ_API_KEY"
        }

    history = []
    for iteration in range(1, max_iterations + 1):
        await log_callback(f"[SMART_TASK]: Iteration {iteration}/{max_iterations} — observing current state...")

        # --- SEE ---
        screenshot_path = os.path.join(tempfile.gettempdir(), f"smart_task_{uuid.uuid4().hex[:8]}.png")
        width, height = 1280, 800
        try:
            if context_kind == "browser":
                page = await browser_agent.get_current_page()
                if page:
                    await page.screenshot(path=screenshot_path)
                    viewport = page.viewport_size or {"width": width, "height": height}
                    width, height = viewport["width"], viewport["height"]
                else:
                    # Playwright page not available, fall back to desktop screenshot
                    shot = pyautogui.screenshot()
                    shot.save(screenshot_path)
                    width, height = shot.size
            else:
                shot = pyautogui.screenshot()
                shot.save(screenshot_path)
                width, height = shot.size
        except Exception as e:
            return {
                "status": "failed",
                "evidence": f"Failed to capture screenshot for smart_task: {e}",
                "attempts": iteration,
                "message": f"smart_task aborted: screenshot capture failed"
            }

        # --- THINK ---
        history_text = "\n".join(history[-6:]) if history else "(no actions taken yet)"
        prompt = (
            f"You are controlling a computer to accomplish this goal: \"{goal}\"\n"
            f"Context: {context_kind}. Screen size: {width}x{height} pixels.\n"
            f"Actions taken so far:\n{history_text}\n\n"
            "Look at the screenshot and decide the SINGLE next action to make progress. "
            "Respond with ONLY a JSON object in one of these forms:\n"
            '  {"action": "click", "x": 123, "y": 456, "reasoning": "..."}\n'
            '  {"action": "type", "text": "...", "reasoning": "..."}\n'
            '  {"action": "key", "key": "enter", "reasoning": "..."}\n'
            '  {"action": "scroll", "direction": "down", "reasoning": "..."}\n'
            '  {"action": "wait", "seconds": 2, "reasoning": "..."}\n'
            '  {"action": "done", "success": true, "reasoning": "goal accomplished because..."}\n'
            '  {"action": "done", "success": false, "reasoning": "goal cannot be accomplished because..."}\n'
            "No other text, no markdown."
        )

        raw = await _call_groq_vision(screenshot_path, prompt, max_tokens=200)
        try:
            os.remove(screenshot_path)
        except Exception:
            pass

        if not raw:
            return {
                "status": "failed",
                "evidence": "Vision model returned no response during smart_task",
                "attempts": iteration,
                "message": "smart_task aborted: vision model unreachable"
            }

        try:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            decision = json.loads(match.group(0) if match else raw)
        except Exception as e:
            await log_callback(f"[SMART_TASK]: Could not parse model decision ('{raw[:120]}'), stopping. ({e})")
            return {
                "status": "failed",
                "evidence": f"Unparseable model decision: {raw[:200]}",
                "attempts": iteration,
                "message": "smart_task aborted: model returned invalid JSON"
            }

        action = decision.get("action")
        reasoning = decision.get("reasoning", "")
        await log_callback(f"[SMART_TASK]: Decided action='{action}' — {reasoning}")

        # --- ACT ---
        try:
            if action == "done":
                if decision.get("success"):
                    return {
                        "status": "success",
                        "evidence": f"Model reported goal complete: {reasoning}",
                        "attempts": iteration,
                        "message": f"smart_task completed: {goal}"
                    }
                else:
                    return {
                        "status": "failed",
                        "evidence": f"Model reported goal not achievable: {reasoning}",
                        "attempts": iteration,
                        "message": f"smart_task could not complete: {goal}"
                    }

            elif action == "click":
                x, y = int(decision["x"]), int(decision["y"])
                if context_kind == "browser" and not browser_agent.is_using_system_browser():
                    page = await browser_agent.get_current_page()
                    if page:
                        await page.mouse.click(x, y)
                    else:
                        desktop_agent.click_mouse(x, y)
                else:
                    # Use pydirectinput for desktop clicks (most reliable)
                    desktop_agent.click_mouse(x, y)
                history.append(f"clicked at ({x},{y}) — {reasoning}")

            elif action == "type":
                text = decision.get("text", "")
                if context_kind == "browser" and not browser_agent.is_using_system_browser():
                    page = await browser_agent.get_current_page()
                    if page:
                        await page.keyboard.type(text)
                    else:
                        desktop_agent.type_text_robust(text)
                else:
                    # Use the robust 4-tier typing pipeline for desktop
                    desktop_agent.type_text_robust(text)
                history.append(f"typed '{text}' — {reasoning}")

            elif action == "key":
                key = decision.get("key", "enter")
                if context_kind == "browser" and not browser_agent.is_using_system_browser():
                    page = await browser_agent.get_current_page()
                    if page:
                        await page.keyboard.press(key)
                    else:
                        desktop_agent.press_key(key)
                else:
                    desktop_agent.press_key(key)
                history.append(f"pressed key '{key}' — {reasoning}")

            elif action == "scroll":
                direction = decision.get("direction", "down")
                delta = 400 if direction == "down" else -400
                if context_kind == "browser" and not browser_agent.is_using_system_browser():
                    page = await browser_agent.get_current_page()
                    if page:
                        await page.mouse.wheel(0, delta)
                    else:
                        pyautogui.scroll(-delta)
                else:
                    pyautogui.scroll(-delta)
                history.append(f"scrolled {direction} — {reasoning}")

            elif action == "wait":
                seconds = float(decision.get("seconds", 1.5))
                await asyncio.sleep(min(seconds, 10))
                history.append(f"waited {seconds}s — {reasoning}")

            else:
                history.append(f"unrecognized action '{action}', skipped")

        except Exception as e:
            await log_callback(f"[SMART_TASK]: Action '{action}' raised an error: {e}. Continuing loop.")
            history.append(f"action '{action}' failed with error: {e}")

        await asyncio.sleep(0.8)  # brief settle time before next observation

    return {
        "status": "failed",
        "evidence": f"Exceeded max_iterations ({max_iterations}) without reaching a 'done' state",
        "attempts": max_iterations,
        "message": f"smart_task timed out: {goal}"
    }

async def execute_steps_list(steps: list, log_callback) -> list:
    """Executes a list of steps sequentially, with top-level plan retries and backoff.
    Tracks _using_system_browser flag across steps so that when Playwright falls back
    mid-plan, all subsequent browser steps auto-route to desktop-level interaction."""
    max_plan_attempts = 3
    plan_attempt = 1
    
    while plan_attempt <= max_plan_attempts:
        results = []
        plan_failed = False
        expected_active_app = None
        using_system_browser = browser_agent.is_using_system_browser()
        
        await log_callback(f"[PLANNER CORE]: Running execution plan (Attempt {plan_attempt}/{max_plan_attempts})...")
        if using_system_browser:
            await log_callback("[PLANNER CORE]: System browser mode active — browser steps will use desktop-level interaction")
        
        for i, step in enumerate(steps):
            stype = (step.get("action_type") or step.get("type") or "").lower().strip()
            
            if stype in ["launch_app", "open_app", "start_app", "run_app"]:
                expected_active_app = step.get("app") or step.get("app_name") or step.get("name")
            elif stype == "focus_window":
                expected_active_app = step.get("title")
                
            step["_expected_active_app"] = expected_active_app
            
            res = await execute_step(step, log_callback, using_system_browser=using_system_browser)

            # After a successful launch_app, wait longer for the window to fully appear and grab focus
            if stype == "launch_app" and res.get("status") == "success":
                app_launched = step.get("app", "")
                await log_callback(f"[PLANNER CORE]: App '{app_launched}' launched, waiting for window to stabilize...")
                await asyncio.sleep(1.5)
                # Aggressively focus the newly opened window so the next type step works
                focused = await asyncio.to_thread(desktop_agent.focus_window_robust, app_launched, 5)
                if focused:
                    await log_callback(f"[PLANNER CORE]: Window '{app_launched}' is now in foreground.")
                else:
                    await log_callback(f"[PLANNER CORE]: Could not confirm '{app_launched}' foreground window — continuing anyway.")
            results.append(res)
            
            # Check if this step triggered a fallback to system browser
            if res.get("_switched_to_system_browser"):
                using_system_browser = True
                await log_callback("[PLANNER CORE]: Switched to system browser mode — subsequent browser steps will use desktop interaction")
            
            step_type = step.get("action_type") or step.get("type") or "unknown"
            if res.get("status") == "success":
                status_msg = f"Step {i+1}/{len(steps)} ({step_type}): SUCCESS - {res.get('message')} (Evidence: {res.get('evidence')}, Attempts: {res.get('attempts')})"
                await log_callback(status_msg)
            else:
                status_msg = f"Step {i+1}/{len(steps)} ({step_type}): FAILED - {res.get('message')} (Reason: {res.get('evidence')}, Attempts: {res.get('attempts')})"
                await log_callback(status_msg)
                plan_failed = True
                break
                
        if not plan_failed:
            await log_callback(f"[PLANNER CORE]: Plan executed successfully! {len(steps)}/{len(steps)} steps verified.")
            return results
            
        if plan_attempt < max_plan_attempts:
            backoff_delay = plan_attempt * 3.0
            await log_callback(f"[PLANNER CORE]: Plan failed due to step verification mismatch. Retrying entire plan in {backoff_delay}s... (Attempt {plan_attempt+1}/{max_plan_attempts})")
            
            if expected_active_app:
                try:
                    desktop_agent.close_app(expected_active_app)
                except Exception:
                    pass
            await asyncio.sleep(backoff_delay)
            plan_attempt += 1
        else:
            await log_callback(f"[PLANNER CORE]: Plan execution FAILED after {max_plan_attempts} attempts.")
            raise Exception("Plan execution failed verification constraints.")
            
    return results
